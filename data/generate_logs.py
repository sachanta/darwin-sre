"""Repair/regenerate log blobs for incidents that have empty lines.

Batches 5 incidents per LLM call to stay within DO rate limits.
Safe to re-run — only fills in incidents where lines == [].
"""
import json
import time
import sys
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")

_client = OpenAI(
    api_key=os.environ["DIGITAL_OCEAN_MODEL_ACCESS_KEY"],
    base_url="https://inference.do-ai.run/v1/",
)
SRE_MODEL = os.environ.get("SRE_MODEL", "anthropic-claude-4.5-sonnet")
DATA_DIR = Path(__file__).parent

BATCH_LOG_PROMPT = """Generate realistic timestamped server logs for each of these SRE incidents.

{incidents_json}

Return a JSON array (one object per incident, in the same order):
[
  {{
    "incident_id": "<id>",
    "lines": [
      {{"ts": "2026-06-27THH:MM:SSZ", "level": "INFO|WARN|ERROR", "msg": "log message", "service": "<service>"}},
      ... (8-12 lines, timestamps advancing, showing the incident unfolding)
    ],
    "summary": "one-sentence summary of what the logs show"
  }},
  ...
]

Rules:
- For edge cases (is_edge_case=true): early lines look like the wrong problem; real root cause is only hinted subtly.
- For normal incidents: logs clearly show the failure progression.
- Timestamps between 2026-06-27T00:00:00Z and 2026-06-27T23:59:59Z.
- Return ONLY the JSON array, no markdown, no extra text."""


def _llm(prompt: str) -> str:
    response = _client.chat.completions.create(
        model=SRE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=6000,
    )
    return response.choices[0].message.content or ""


def _parse_json(raw: str) -> list | dict | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def generate_batch(incidents: list[dict], start_hour: int) -> list[dict]:
    """Generate logs for a batch of incidents. Returns list of log objects."""
    compact = [
        {
            "id": inc["id"],
            "title": inc["title"],
            "service": inc["service"],
            "category": inc["category"],
            "is_edge_case": inc.get("is_edge_case", False),
            "root_cause": inc["ground_truth"]["root_cause"],
            "start_hour": f"{(start_hour % 24):02d}",
        }
        for inc in incidents
    ]
    prompt = BATCH_LOG_PROMPT.format(incidents_json=json.dumps(compact, indent=2))
    raw = _llm(prompt)
    parsed = _parse_json(raw)
    if not isinstance(parsed, list):
        return []
    return parsed


def main():
    # Load existing data
    training = json.loads((DATA_DIR / "incidents_training.json").read_text())
    production = json.loads((DATA_DIR / "incidents_production.json").read_text())
    logs_list = json.loads((DATA_DIR / "logs.json").read_text())

    all_incidents = training + production
    logs_by_incident = {log["incident_id"]: log for log in logs_list}

    # Find incidents with empty logs
    needs_logs = [
        inc for inc in all_incidents
        if not logs_by_incident.get(inc["id"], {}).get("lines")
    ]
    print(f"Incidents needing log generation: {len(needs_logs)}")

    if not needs_logs:
        print("All logs already populated. Nothing to do.")
        return

    batch_size = 5
    batches = [needs_logs[i:i+batch_size] for i in range(0, len(needs_logs), batch_size)]
    filled = 0

    for batch_idx, batch in enumerate(batches):
        start_hour = batch_idx * 2  # spread timestamps across the day
        print(f"  Batch {batch_idx+1}/{len(batches)}: {[inc['id'] for inc in batch]}", flush=True)
        try:
            results = generate_batch(batch, start_hour)
            by_id = {r["incident_id"]: r for r in results if isinstance(r, dict)}
            for inc in batch:
                if inc["id"] in by_id:
                    log_obj = logs_by_incident.get(inc["id"])
                    if log_obj:
                        log_obj["lines"] = by_id[inc["id"]].get("lines", [])
                        log_obj["summary"] = by_id[inc["id"]].get("summary", "")
                        filled += 1
                    else:
                        new_log = {
                            "id": f"log_{inc['id']}",
                            "incident_id": inc["id"],
                            "lines": by_id[inc["id"]].get("lines", []),
                            "summary": by_id[inc["id"]].get("summary", ""),
                        }
                        logs_list.append(new_log)
                        logs_by_incident[inc["id"]] = new_log
                        filled += 1
        except Exception as e:
            print(f"    [warn] batch {batch_idx+1} failed: {e}", file=sys.stderr)

        # Pause between batches to avoid rate limiting
        if batch_idx < len(batches) - 1:
            time.sleep(2)

    # Save updated logs
    (DATA_DIR / "logs.json").write_text(json.dumps(logs_list, indent=2))
    print(f"\n✓ Filled {filled}/{len(needs_logs)} log blobs. Saved logs.json.")


if __name__ == "__main__":
    main()
