"""Generate 100 synthetic SRE incidents using DO inference."""
import json
import sys
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent / ".env")

_client = OpenAI(
    api_key=os.environ["DIGITAL_OCEAN_MODEL_ACCESS_KEY"],
    base_url="https://inference.do-ai.run/v1/",
)

TRAINING_PROMPT = """Generate {n} realistic SRE incident reports as a JSON array.
These are NORMAL incidents with clear, unambiguous root causes.

Categories to cover (spread evenly): database, performance, network, storage, service, security, configuration

Each incident must follow this exact schema:
{{
  "id": "train_{i:03d}",
  "title": "...",
  "description": "2-3 sentences describing the incident symptoms",
  "logs": "3-5 lines of realistic log output showing the error",
  "metrics": {{"<relevant_metric>": <value>, "<relevant_metric_2>": <value>}},
  "service": "<microservice-name>",
  "environment": "production",
  "category": "<category>",
  "is_edge_case": false,
  "ground_truth": {{
    "root_cause": "precise technical description",
    "severity": "P1" | "P2" | "P3",
    "remediation": ["step 1", "step 2", "step 3"]
  }}
}}

Make logs and metrics realistic. Severity: P1=outage, P2=degraded, P3=minor.
Return ONLY the JSON array, no other text."""

EDGE_CASE_PROMPT = """Generate {n} TRICKY SRE incident reports as a JSON array.
These are EDGE CASES designed to fool a standard SRE AI agent.

Edge case types to include:
1. Multi-service cascading failure (root cause is upstream, symptoms appear downstream)
2. Security breach disguised as high CPU/memory usage
3. Cross-region latency with misleading local error codes
4. Race condition in distributed transactions (intermittent, hard to reproduce)
5. DNS intermittent failure causing authentication errors
6. Clock skew between services causing JWT expiry false positives
7. Memory leak in sidecar container (not the main service being alerted)
8. Config drift between replicas causing split/inconsistent responses
9. Noisy neighbor on shared infrastructure masking the real bottleneck
10. Thundering herd after a deployment rollback

Each incident must follow this exact schema:
{{
  "id": "prod_{i:03d}",
  "title": "...",
  "description": "2-3 sentences with MISLEADING symptoms that point to the wrong service/cause",
  "logs": "3-5 log lines that look like one problem but indicate another",
  "metrics": {{"<metric>": <value>}},
  "service": "<the-service-getting-blamed>",
  "environment": "production",
  "category": "<category>",
  "is_edge_case": true,
  "edge_case_type": "<type from list above>",
  "ground_truth": {{
    "root_cause": "the REAL root cause (different from what the symptoms suggest)",
    "severity": "P1" | "P2" | "P3",
    "remediation": ["correct step 1", "correct step 2", "correct step 3"]
  }}
}}

Return ONLY the JSON array, no other text."""

NORMAL_PROD_PROMPT = """Generate {n} realistic SRE incident reports as a JSON array.
These are NORMAL production incidents (same as training, but with prod_ IDs).

Each incident must follow this exact schema:
{{
  "id": "prod_{i:03d}",
  "title": "...",
  "description": "2-3 sentences describing symptoms",
  "logs": "3-5 lines of realistic log output",
  "metrics": {{"<metric>": <value>}},
  "service": "<microservice-name>",
  "environment": "production",
  "category": "database" | "performance" | "network" | "storage" | "service" | "configuration",
  "is_edge_case": false,
  "ground_truth": {{
    "root_cause": "precise technical description",
    "severity": "P1" | "P2" | "P3",
    "remediation": ["step 1", "step 2", "step 3"]
  }}
}}

Return ONLY the JSON array, no other text."""


def generate(prompt: str, label: str) -> list[dict]:
    print(f"Generating {label}...", flush=True)
    response = _client.chat.completions.create(
        model=os.environ.get("SRE_MODEL", "claude-sonnet-4-5"),
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=8000,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content
    data = json.loads(raw)
    # Handle both {"incidents": [...]} and [...]
    if isinstance(data, list):
        return data
    return next(iter(data.values()))


def main():
    out_dir = Path(__file__).parent

    # 50 training incidents (normal)
    training = generate(TRAINING_PROMPT.format(n=50, i=0), "50 training incidents")
    for i, inc in enumerate(training):
        inc["id"] = f"train_{i+1:03d}"
    with open(out_dir / "incidents_training.json", "w") as f:
        json.dump(training, f, indent=2)
    print(f"  ✓ Saved {len(training)} training incidents")

    # 30 normal production incidents
    normal_prod = generate(NORMAL_PROD_PROMPT.format(n=30, i=0), "30 normal production incidents")
    for i, inc in enumerate(normal_prod):
        inc["id"] = f"prod_{i+1:03d}"

    # 20 edge case production incidents
    edge_cases = generate(EDGE_CASE_PROMPT.format(n=20, i=0), "20 edge case incidents")
    for i, inc in enumerate(edge_cases):
        inc["id"] = f"prod_{i+31:03d}"

    production = normal_prod + edge_cases
    with open(out_dir / "incidents_production.json", "w") as f:
        json.dump(production, f, indent=2)
    print(f"  ✓ Saved {len(production)} production incidents ({len(normal_prod)} normal + {len(edge_cases)} edge cases)")
    print("Done.")


if __name__ == "__main__":
    main()
