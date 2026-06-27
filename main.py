"""Darwin SRE — autonomous capture run.

Episode design (wave-based, guarantees 8 clean Darwin triggers):
  1. 15 normal prod incidents → establish baseline
  2. CCF-1 wave → scores tank → Darwin gen 1
  3. 3 training washout → window recovers above threshold
  4. CCF-2 wave → Darwin gen 2
  ... × 8 families
  9. Remaining normal prod → final validation (all skills loaded)

This structure ensures exactly one Darwin trigger per failure family,
producing a clean 8-step staircase regardless of exact score values.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from observability import setup_tracing
from darwin.loop import DarwinLoop
from darwin.storage import seed_incidents, seed_logs, create_run, finish_run
from config import DARWIN_TRIGGER_THRESHOLD

DATA_DIR = Path("data")
WASHOUT_SIZE = 3   # normal incidents between families to reset rolling window


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def build_episode(
    normal_prod: list[dict],
    corner_cases: list[dict],
    washout_pool: list[dict],
) -> list[dict]:
    """Build the ordered episode list: waves of corner cases separated by washout incidents.

    Structure:
      normal_prod[:15]
      for each family: family_incidents + washout_pool[i*3:(i+1)*3]
      normal_prod[15:]
    """
    # Group corner cases by family, preserving order
    families: dict[str, list[dict]] = {}
    for inc in corner_cases:
        fam = inc.get("edge_case_family", "unknown")
        families.setdefault(fam, []).append(inc)

    family_order = ["CCF-1", "CCF-2", "CCF-3", "CCF-4", "CCF-5", "CCF-6", "CCF-7", "CCF-8"]

    episode = []
    episode.extend(normal_prod[:15])

    washout_idx = 0
    for fam_id in family_order:
        incidents = families.get(fam_id, [])
        if not incidents:
            continue
        episode.extend(incidents)
        # Washout: WASHOUT_SIZE normal incidents to push window back above threshold
        washout = washout_pool[washout_idx: washout_idx + WASHOUT_SIZE]
        episode.extend(washout)
        washout_idx += WASHOUT_SIZE

    episode.extend(normal_prod[15:])
    return episode


def on_event(event: dict) -> None:
    t = event["type"]
    if t == "incident_start":
        fam = f" [{event.get('edge_case_family')}]" if event.get("edge_case_family") else ""
        skills_n = event.get("num_skills", 0)
        kb_n = event.get("num_kb", 0)
        skills_str = f" skills={skills_n} kb={kb_n}" if (skills_n or kb_n) else ""
        print(f"  → [{event['incident_id']}]{fam} {event['title'][:50]}{skills_str}")

    elif t == "incident_resolved":
        s = event["scores"]
        avg = event["rolling_avg"]
        marker = "🔴" if s["composite"] < 0.5 else "🟡" if s["composite"] < DARWIN_TRIGGER_THRESHOLD else "🟢"
        print(f"    {marker} score={s['composite']:.2f}  avg={avg:.2f}  gen={event['generation']}")

    elif t == "alert_raised":
        print(f"\n  ⚠️  ALERT RAISED — rolling_avg={event.get('rolling_avg', 0):.2f}\n")

    elif t == "darwin_start":
        print(f"\n{'='*60}")
        print(f"  🧬 DARWIN gen {event['generation']} — {event.get('failure_families', [])}")
        print(f"     score_before={event['score_before']:.2f}  failures={event['num_failures']}")
        print(f"{'='*60}")

    elif t == "darwin_complete":
        skill = event.get("new_skill", {})
        retired = event.get("skills_retired", [])
        improved = event["score_after"] >= event["score_before"]
        status = "✅ IMPROVED" if improved else "⚠️  no improvement"
        print(f"  {status}  {event['score_before']:.2f} → {event['score_after']:.2f}")
        print(f"  🎯 New skill: '{skill.get('name', '?')}' tags={skill.get('tags', [])}")
        if retired:
            print(f"  🗃️  Retired skills: {retired}")
        print()

    elif t == "incident_blocked":
        print(f"  🛡️  BLOCKED [{event['incident_id']}] — {event.get('reason')}")


def main():
    setup_tracing()

    # ── Load data ──────────────────────────────────────────────────────────
    for name in ("incidents_training.json", "incidents_production.json",
                 "logs.json", "knowledge_base.json"):
        if not (DATA_DIR / name).exists():
            print(f"Missing {name}. Run: poetry run python data/generate_incidents.py")
            return

    training = load_json(DATA_DIR / "incidents_training.json")
    production = load_json(DATA_DIR / "incidents_production.json")
    logs = load_json(DATA_DIR / "logs.json")

    normal_prod = [i for i in production if not i.get("is_edge_case")]
    corner_cases = [i for i in production if i.get("is_edge_case")]

    print(f"Loaded: {len(training)} training  {len(normal_prod)} normal-prod  "
          f"{len(corner_cases)} corner-cases  {len(logs)} logs")

    # ── Seed MongoDB ───────────────────────────────────────────────────────
    print("Seeding MongoDB...")
    seed_incidents(training + production)
    seed_logs(logs)
    print("  ✓ incidents + logs seeded")

    # ── Create run record ─────────────────────────────────────────────────
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    create_run(run_id)
    print(f"  ✓ run_id: {run_id}\n")

    # ── Baseline: training set ─────────────────────────────────────────────
    print("── BASELINE (50 training incidents — normal only) ───────────")
    baseline_loop = DarwinLoop(on_event=on_event, run_id=run_id)
    baseline_results = baseline_loop.run(training, register_vijil=True)
    baseline_avg = (sum(r["scores"]["composite"] for r in baseline_results)
                    / len(baseline_results))
    print(f"\nBaseline avg: {baseline_avg:.3f}\n")

    # ── Production: wave-based episode ────────────────────────────────────
    episode = build_episode(normal_prod, corner_cases, washout_pool=training)
    families_in_episode = [i.get("edge_case_family") for i in episode if i.get("is_edge_case")]
    print(f"── PRODUCTION STREAM ({len(episode)} incidents) ────────────────────")
    print(f"   Family waves: {sorted(set(families_in_episode))}")
    print(f"   Window size: {baseline_loop.window.maxlen}  Threshold: {DARWIN_TRIGGER_THRESHOLD}\n")

    prod_loop = DarwinLoop(on_event=on_event, run_id=run_id)
    prod_results = prod_loop.run(episode, register_vijil=False)

    final_avg = (sum(r["scores"]["composite"] for r in prod_results)
                 / len(prod_results))

    # ── Finish run record ─────────────────────────────────────────────────
    episode_order = [i["id"] for i in episode]
    finish_run(
        run_id=run_id,
        num_generations=prod_loop.generation,
        baseline_avg=baseline_avg,
        final_avg=final_avg,
        episode_order=episode_order,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  RUN COMPLETE: {run_id}")
    print(f"  Baseline avg  : {baseline_avg:.3f}")
    print(f"  Final avg     : {final_avg:.3f}  (Δ {final_avg - baseline_avg:+.3f})")
    print(f"  Darwin fired  : {prod_loop.generation} generation(s)")
    print(f"  Skills written: {prod_loop.generation}")
    print(f"{'='*60}")
    print(f"\nTo replay in UI: GET /runs/{run_id}/timeline")


if __name__ == "__main__":
    main()
