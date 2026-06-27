"""Generate synthetic SRE dataset: incidents + log blobs + seed knowledge base.

Outputs (all in data/):
  incidents_training.json   — 50 normal incidents (categories: database/performance/network/storage/service/config)
  incidents_production.json — 30 normal + 20 corner-case incidents (8 ordered families, 2-3 each)
  logs.json                 — one timestamped log blob per incident (keyed by incident id)
  knowledge_base.json       — ~25 seed KB articles covering normal categories ONLY (no corner cases)
"""
import json
import sys
import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

_client = OpenAI(
    api_key=os.environ["DIGITAL_OCEAN_MODEL_ACCESS_KEY"],
    base_url="https://inference.do-ai.run/v1/",
)

SRE_MODEL = os.environ.get("SRE_MODEL", "anthropic-claude-4.5-sonnet")

# ---------------------------------------------------------------------------
# 8 corner-case families — each family will tank rolling avg once, triggering
# one Darwin generation.  Seed KB deliberately contains NO articles for these.
# ---------------------------------------------------------------------------
CORNER_CASE_FAMILIES = [
    {
        "family_id": "CCF-1",
        "name": "cascading_upstream_failure",
        "count": 3,
        "description": "Multi-service cascading failure where root cause is an upstream dependency; symptoms appear in downstream services",
    },
    {
        "family_id": "CCF-2",
        "name": "security_breach_disguised",
        "count": 2,
        "description": "Security breach (credential stuffing / lateral movement) disguised as high CPU or memory pressure on the app tier",
    },
    {
        "family_id": "CCF-3",
        "name": "cross_region_latency",
        "count": 2,
        "description": "Cross-region replication latency presenting as local 503/timeout errors with misleading local error codes",
    },
    {
        "family_id": "CCF-4",
        "name": "distributed_race_condition",
        "count": 3,
        "description": "Intermittent race condition in distributed transactions (idempotency key collision / double-write) — hard to reproduce",
    },
    {
        "family_id": "CCF-5",
        "name": "clock_skew_jwt",
        "count": 2,
        "description": "NTP clock skew between services causing JWT expiry false positives; appears as auth failures, not clock drift",
    },
    {
        "family_id": "CCF-6",
        "name": "sidecar_memory_leak",
        "count": 3,
        "description": "Memory leak in a sidecar container (Envoy proxy / log shipper) blamed on the main service being OOM-killed",
    },
    {
        "family_id": "CCF-7",
        "name": "config_replica_drift",
        "count": 2,
        "description": "Config drift between replicas causing split-brain / inconsistent responses — load balancer masks the affected subset",
    },
    {
        "family_id": "CCF-8",
        "name": "thundering_herd_rollback",
        "count": 3,
        "description": "Thundering herd after a deployment rollback — cache cold start + retry storms overwhelm the recovered service",
    },
]

NORMAL_CATEGORIES = ["database", "performance", "network", "storage", "service", "configuration"]

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TRAINING_PROMPT = """Generate {n} realistic SRE incident reports as a JSON array.
These are NORMAL incidents with clear, unambiguous root causes.

Categories to cover (spread evenly): database, performance, network, storage, service, configuration

Each incident must follow this exact schema:
{{
  "id": "train_{i:03d}",
  "title": "brief incident title",
  "description": "2-3 sentences describing the incident symptoms",
  "service": "<microservice-name>",
  "environment": "production",
  "category": "<one of: database|performance|network|storage|service|configuration>",
  "is_edge_case": false,
  "edge_case_family": null,
  "metrics": {{"<relevant_metric>": <value>, "<relevant_metric_2>": <value>}},
  "ground_truth": {{
    "root_cause": "precise technical description",
    "severity": "P1",
    "remediation_steps": ["step 1", "step 2", "step 3"]
  }}
}}

severity must be one of: P1 (outage), P2 (degraded), P3 (minor).
Make service names realistic microservice names (e.g. payment-service, auth-api, inventory-db).
Return ONLY the JSON array, no markdown, no extra text."""

NORMAL_PROD_PROMPT = """Generate {n} realistic SRE incident reports as a JSON array.
These are NORMAL production incidents with clear root causes (different services/scenarios than training set).

Each incident must follow this exact schema:
{{
  "id": "prod_{i:03d}",
  "title": "brief incident title",
  "description": "2-3 sentences describing symptoms",
  "service": "<microservice-name>",
  "environment": "production",
  "category": "<one of: database|performance|network|storage|service|configuration>",
  "is_edge_case": false,
  "edge_case_family": null,
  "metrics": {{"<metric>": <value>}},
  "ground_truth": {{
    "root_cause": "precise technical description",
    "severity": "P1",
    "remediation_steps": ["step 1", "step 2", "step 3"]
  }}
}}

severity must be one of: P1, P2, P3.
Return ONLY the JSON array, no markdown, no extra text."""

CORNER_CASE_PROMPT = """Generate {n} TRICKY SRE incident reports as a JSON array for failure family "{family_name}".

Family description: {family_description}

These incidents are engineered to fool a standard SRE AI agent:
- Symptoms point to the WRONG service or root cause
- Log lines look like one problem but indicate another
- Metrics are misleading — the real signal is subtle

Each incident must follow this exact schema:
{{
  "id": "prod_{i:03d}",
  "title": "brief incident title (sounds like a normal incident)",
  "description": "2-3 sentences with MISLEADING symptoms that suggest the wrong cause",
  "service": "<the-service-getting-blamed (not the real culprit)>",
  "environment": "production",
  "category": "<category matching the MISLEADING symptom>",
  "is_edge_case": true,
  "edge_case_family": "{family_id}",
  "metrics": {{"<misleading_metric>": <value>}},
  "ground_truth": {{
    "root_cause": "the REAL root cause (different from what symptoms suggest)",
    "severity": "P1",
    "remediation_steps": ["correct step 1", "correct step 2", "correct step 3"]
  }}
}}

severity must be one of: P1, P2, P3.
Return ONLY the JSON array, no markdown, no extra text."""

LOG_PROMPT = """Generate realistic timestamped server logs for this SRE incident.

Incident title: {title}
Service: {service}
Category: {category}
Is edge case: {is_edge_case}
Ground truth root cause: {root_cause}

Return a JSON object with this exact schema:
{{
  "lines": [
    {{"ts": "2026-06-27T{hh}:{mm}:{ss}Z", "level": "INFO|WARN|ERROR", "msg": "log message", "service": "{service}"}},
    ... (8-15 lines total, timestamps advancing forward, showing the incident unfolding)
  ],
  "summary": "one-sentence summary of what the logs show"
}}

For edge cases: the early log lines should look like the WRONG problem; the real root cause should only be hinted at subtly in 1-2 lines.
For normal incidents: logs should clearly show the failure progression.
Timestamps should be in the range 2026-06-27T00:00:00Z to 2026-06-27T23:59:59Z.
Return ONLY the JSON object, no markdown, no extra text."""

KB_SEED_PROMPT = """Generate {n} knowledge base runbook articles as a JSON array.
These articles cover NORMAL SRE incident categories only: database, performance, network, storage, service, configuration.
Do NOT include articles for: cascading upstream failures, security breaches, cross-region latency, distributed race conditions, clock skew, sidecar leaks, config replica drift, or thundering herds.

Each article must follow this exact schema:
{{
  "id": "kb_{i:03d}",
  "title": "Runbook: <specific problem>",
  "body": "5-8 sentences describing the problem, how to diagnose it, and how to resolve it. Include specific commands or steps.",
  "service": "<specific service type or 'general'>",
  "tags": ["tag1", "tag2", "tag3"],
  "source": "seed",
  "created_by_generation": null
}}

Make articles specific and technical (e.g. "Runbook: PostgreSQL connection pool exhaustion", "Runbook: Redis OOM eviction during traffic spike").
Spread coverage evenly across categories.
Return ONLY the JSON array, no markdown, no extra text."""


def _llm(prompt: str) -> str:
    response = _client.chat.completions.create(
        model=SRE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=8000,
    )
    return response.choices[0].message.content


def _parse(raw: str) -> list | dict:
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    data = json.loads(text)
    if isinstance(data, list):
        return data
    # unwrap {"incidents": [...]} or {"articles": [...]} etc.
    for v in data.values():
        if isinstance(v, list):
            return v
    return data


def generate_incidents(prompt: str, label: str) -> list[dict]:
    print(f"  Generating {label}...", flush=True)
    return _parse(_llm(prompt))


def generate_incidents_batched(prompt_template: str, total: int, batch_size: int, id_prefix: str, id_offset: int = 0, extra_kwargs: dict = None) -> list[dict]:
    """Generate incidents in batches to avoid token limits."""
    results = []
    extra_kwargs = extra_kwargs or {}
    for start in range(0, total, batch_size):
        n = min(batch_size, total - start)
        prompt = prompt_template.format(n=n, i=id_offset + start, **extra_kwargs)
        label = f"{n} {id_prefix} (batch {start//batch_size + 1})"
        batch = generate_incidents(prompt, label)
        results.extend(batch)
    return results


def generate_logs(incident: dict, idx: int) -> dict:
    """Generate a log blob for one incident. Returns {id, incident_id, lines[], summary}."""
    hh = f"{(idx % 24):02d}"
    prompt = LOG_PROMPT.format(
        title=incident["title"],
        service=incident["service"],
        category=incident["category"],
        is_edge_case=incident["is_edge_case"],
        root_cause=incident["ground_truth"]["root_cause"],
        hh=hh, mm="00", ss="00",
    )
    try:
        raw = _llm(prompt)
        parsed = json.loads(raw)
        log_id = f"log_{incident['id']}"
        return {
            "id": log_id,
            "incident_id": incident["id"],
            "lines": parsed.get("lines", []),
            "summary": parsed.get("summary", ""),
        }
    except Exception as e:
        print(f"    [warn] log gen failed for {incident['id']}: {e}", file=sys.stderr)
        return {
            "id": f"log_{incident['id']}",
            "incident_id": incident["id"],
            "lines": [],
            "summary": "",
        }


def main():
    out_dir = Path(__file__).parent
    print("=== Darwin SRE — synthetic data generation ===")

    # ------------------------------------------------------------------
    # 1. Training incidents (50 normal, batched 10 at a time)
    # ------------------------------------------------------------------
    print("\n[1/5] Training incidents (50 normal, 5 batches of 10)...")
    training = generate_incidents_batched(TRAINING_PROMPT, total=50, batch_size=10, id_prefix="train")
    for i, inc in enumerate(training):
        inc["id"] = f"train_{i+1:03d}"
        inc.setdefault("edge_case_family", None)
    print(f"  ✓ {len(training)} training incidents")

    # ------------------------------------------------------------------
    # 2. Normal production incidents (30, batched 10 at a time)
    # ------------------------------------------------------------------
    print("\n[2/5] Normal production incidents (30, 3 batches of 10)...")
    normal_prod = generate_incidents_batched(NORMAL_PROD_PROMPT, total=30, batch_size=10, id_prefix="prod")
    for i, inc in enumerate(normal_prod):
        inc["id"] = f"prod_{i+1:03d}"
        inc.setdefault("edge_case_family", None)
    print(f"  ✓ {len(normal_prod)} normal production incidents")

    # ------------------------------------------------------------------
    # 3. Corner-case incidents (8 families, 20 total)
    # ------------------------------------------------------------------
    print("\n[3/5] Corner-case incidents (8 families)...")
    corner_cases: list[dict] = []
    prod_offset = len(normal_prod) + 1
    for fam in CORNER_CASE_FAMILIES:
        prompt = CORNER_CASE_PROMPT.format(
            n=fam["count"],
            family_name=fam["name"],
            family_id=fam["family_id"],
            family_description=fam["description"],
            i=prod_offset,
        )
        batch = generate_incidents(prompt, f"family {fam['family_id']} ({fam['name']}, n={fam['count']})")
        for j, inc in enumerate(batch):
            inc["id"] = f"prod_{prod_offset:03d}"
            inc["edge_case_family"] = fam["family_id"]
            inc["is_edge_case"] = True
            prod_offset += 1
        corner_cases.extend(batch)
        print(f"  ✓ {fam['family_id']}: {len(batch)} incidents")

    production = normal_prod + corner_cases
    print(f"  ✓ Total production: {len(production)} ({len(normal_prod)} normal + {len(corner_cases)} corner cases)")

    # ------------------------------------------------------------------
    # 4. Log blobs (one per incident)
    # ------------------------------------------------------------------
    print("\n[4/5] Log blobs (one per incident)...")
    all_incidents = training + production
    logs: list[dict] = []
    logs_by_id: dict[str, str] = {}  # incident_id → log_id

    for idx, inc in enumerate(all_incidents):
        log = generate_logs(inc, idx)
        logs.append(log)
        logs_by_id[inc["id"]] = log["id"]
        inc["log_id"] = log["id"]
        if (idx + 1) % 10 == 0:
            print(f"  ... {idx+1}/{len(all_incidents)} logs done", flush=True)

    print(f"  ✓ {len(logs)} log blobs generated")

    # ------------------------------------------------------------------
    # 5. Seed knowledge base (~25 articles, normal categories only)
    # ------------------------------------------------------------------
    print("\n[5/5] Seed knowledge base (~25 articles)...")
    kb_raw = _llm(KB_SEED_PROMPT.format(n=25, i=0))
    kb_articles: list[dict] = _parse(kb_raw)
    for i, art in enumerate(kb_articles):
        art["id"] = f"kb_{i+1:03d}"
        art["source"] = "seed"
        art.setdefault("created_by_generation", None)
        # embedding field absent here — added in Phase B by setup_atlas_vector.py
    print(f"  ✓ {len(kb_articles)} seed KB articles")

    # Link normal incidents to 1-2 relevant KB articles (by service/category match)
    kb_by_tag: dict[str, list[str]] = {}
    for art in kb_articles:
        for tag in art.get("tags", []):
            kb_by_tag.setdefault(tag.lower(), []).append(art["id"])

    for inc in all_incidents:
        if inc.get("is_edge_case"):
            inc["kb_refs"] = []  # corner cases intentionally get no KB match
        else:
            cat = inc.get("category", "")
            refs = kb_by_tag.get(cat, [])[:2]
            inc["kb_refs"] = refs

    # ------------------------------------------------------------------
    # Save all outputs
    # ------------------------------------------------------------------
    print("\nSaving outputs...")

    (out_dir / "incidents_training.json").write_text(json.dumps(training, indent=2))
    print(f"  ✓ incidents_training.json ({len(training)} records)")

    (out_dir / "incidents_production.json").write_text(json.dumps(production, indent=2))
    print(f"  ✓ incidents_production.json ({len(production)} records)")

    (out_dir / "logs.json").write_text(json.dumps(logs, indent=2))
    print(f"  ✓ logs.json ({len(logs)} records)")

    (out_dir / "knowledge_base.json").write_text(json.dumps(kb_articles, indent=2))
    print(f"  ✓ knowledge_base.json ({len(kb_articles)} records)")

    # Summary
    families_present = {inc["edge_case_family"] for inc in production if inc.get("is_edge_case")}
    print(f"\n=== Summary ===")
    print(f"  Training incidents  : {len(training)}")
    print(f"  Normal production   : {len(normal_prod)}")
    print(f"  Corner-case families: {len(families_present)} → {sorted(families_present)}")
    print(f"  Total corner cases  : {len(corner_cases)}")
    print(f"  Log blobs           : {len(logs)}")
    print(f"  Seed KB articles    : {len(kb_articles)}")
    print("Done.")


if __name__ == "__main__":
    main()
