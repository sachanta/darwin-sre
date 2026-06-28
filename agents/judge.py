import json
import re
from google import genai
from google.genai import types
from opentelemetry.trace import SpanKind
from config import GOOGLE_API_KEY, JUDGE_MODEL
from observability import get_tracer, span_ok

_client = genai.Client(api_key=GOOGLE_API_KEY)

JUDGE_PROMPT = """You are a strict SRE evaluator. Score this incident resolution against the ground truth.

INCIDENT:
{incident}

AGENT RESOLUTION:
{resolution}

GROUND TRUTH:
{ground_truth}

Score each criterion from 0.0 to 1.0:
- root_cause_accuracy (0.4 weight): Does the identified root cause match the ground truth's SPECIFIC root cause? Generic or partial answers score 0.2-0.4.
- remediation_quality (0.4 weight): STRICT — does the resolution include the EXACT specific steps from the ground truth? If the ground truth contains specific internal commands, config keys, tool names, kubectl patches, or runbook steps and the resolution omits them or uses only generic alternatives, score 0.1-0.3. Only score 0.8+ if the specific steps are explicitly addressed.
- severity_accuracy (0.2 weight): Is the P1/P2/P3 severity correct?

Respond in valid JSON only:
{{
  "root_cause_accuracy": <0.0-1.0>,
  "remediation_quality": <0.0-1.0>,
  "severity_accuracy": <0.0-1.0>,
  "composite": <weighted average>,
  "reasoning": "<10 words max>"
}}"""


def score_resolution(incident: dict, resolution: dict) -> dict:
    tracer = get_tracer()
    ground_truth = incident.get("ground_truth", {})

    with tracer.start_as_current_span("darwin.judge", kind=SpanKind.CLIENT) as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("incident.id", incident["id"])
        span.set_attribute("incident.is_edge_case", incident.get("is_edge_case", False))
        span.set_attribute("llm.model", JUDGE_MODEL)

        prompt = JUDGE_PROMPT.format(
            incident=json.dumps({
                "title": incident["title"],
                "service": incident["service"],
                "description": incident["description"],
                "category": incident["category"],
            }, indent=2),
            resolution=json.dumps(resolution, indent=2),
            ground_truth=json.dumps(ground_truth, indent=2),
        )

        try:
            response = _client.models.generate_content(
                model=JUDGE_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=2048,
                ),
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            scores = json.loads(raw)
        except Exception:
            # Fallback: extract floats via regex so a truncated response never crashes
            scores = _parse_scores_fallback(response.text if "response" in dir() else "")

        scores["composite"] = round(
            scores.get("root_cause_accuracy", 0.5) * 0.4
            + scores.get("remediation_quality", 0.5) * 0.4
            + scores.get("severity_accuracy", 0.5) * 0.2,
            3,
        )
        span.set_attribute("score.composite", scores["composite"])
        span.set_attribute("score.root_cause_accuracy", scores.get("root_cause_accuracy", 0.5))
        span.set_attribute("score.remediation_quality", scores.get("remediation_quality", 0.5))
        span.set_attribute("score.severity_accuracy", scores.get("severity_accuracy", 0.5))
        span_ok(span)
        return scores


def _parse_scores_fallback(text: str) -> dict:
    """Extract numeric scores from partial/malformed JSON via regex."""
    def extract(key: str) -> float:
        m = re.search(rf'"{key}"\s*:\s*([0-9.]+)', text)
        return float(m.group(1)) if m else 0.5

    return {
        "root_cause_accuracy": extract("root_cause_accuracy"),
        "remediation_quality": extract("remediation_quality"),
        "severity_accuracy": extract("severity_accuracy"),
        "reasoning": "fallback parse",
    }
