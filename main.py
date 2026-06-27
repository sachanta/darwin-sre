"""Layer 1 entry point — runs Darwin loop locally, prints live table."""
import json
from pathlib import Path
from observability import setup_tracing
from darwin.loop import DarwinLoop
from darwin.storage import seed_incidents


def load_incidents(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def on_event(event: dict) -> None:
    t = event["type"]
    if t == "incident_start":
        edge = " [EDGE]" if event["is_edge_case"] else ""
        print(f"  → [{event['incident_id']}] {event['title'][:55]}{edge}")
    elif t == "incident_resolved":
        s = event["scores"]
        avg = event["rolling_avg"]
        marker = "🔴" if s["composite"] < 0.5 else "🟡" if s["composite"] < 0.75 else "🟢"
        print(f"    {marker} composite={s['composite']:.2f}  rolling_avg={avg:.2f}  gen={event['generation']}")
    elif t == "darwin_start":
        print(f"\n{'='*60}")
        print(f"  🧬 DARWIN ACTIVATED — generation {event['generation']}")
        print(f"     score_before={event['score_before']:.2f}  failures={event['num_failures']}")
        print(f"{'='*60}")
    elif t == "darwin_complete":
        improved = "✅ IMPROVED" if event["prompt_improved"] else "⚠️  no improvement"
        print(f"  {improved}  {event['score_before']:.2f} → {event['score_after']:.2f}")
        print(f"  Diff preview:\n{event['prompt_diff'][:300]}\n")


def main():
    setup_tracing()
    training_path = Path("data/incidents_training.json")
    production_path = Path("data/incidents_production.json")

    if not training_path.exists() or not production_path.exists():
        print("Incident data not found. Run: python data/generate_incidents.py")
        return

    training = load_incidents(training_path)
    production = load_incidents(production_path)

    print(f"Loaded {len(training)} training + {len(production)} production incidents")
    print("Seeding MongoDB...")
    seed_incidents(training + production)

    print("\n── BASELINE (training set) ──────────────────────────────")
    baseline_loop = DarwinLoop(on_event=on_event)
    baseline_results = baseline_loop.run(training)
    baseline_avg = sum(r["scores"]["composite"] for r in baseline_results) / len(baseline_results)
    print(f"\nBaseline avg score: {baseline_avg:.3f}\n")

    print("── PRODUCTION STREAM ────────────────────────────────────")
    prod_loop = DarwinLoop(on_event=on_event)
    prod_results = prod_loop.run(production)
    final_avg = sum(r["scores"]["composite"] for r in prod_results) / len(prod_results)
    print(f"\nFinal avg score: {final_avg:.3f}")
    print(f"Darwin fired {prod_loop.generation} time(s)")


if __name__ == "__main__":
    main()
