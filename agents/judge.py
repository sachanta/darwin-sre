import json
from google import genai
from google.genai import types
from opentelemetry.trace import SpanKind
from config import GOOGLE_API_KEY, JUDGE_MODEL
from observability import get_tracer, span_ok, span_error

_client = genai.Client(api_key=GOOGLE_API_KEY)

JUDGE_PROMPT = """You are an expert SRE evaluator. Score this incident resolution against the ground truth.

INCIDENT:
{incident}

AGENT RESOLUTION:
{resolution}

GROUND TRUTH:
{ground_truth}

Score each criterion from 0.0 to 1.0:
- root_cause_accuracy (0.4 weight): Does the identified root cause match the ground truth?
- remediation_quality (0.4 weight): Are the remediation steps correct, ordered, and actionable?
- severity_accuracy (0.2 weight): Is the P1/P2/P3 severity correct?

Respond in valid JSON only:
{{
  "root_cause_accuracy": <0.0-1.0>,
  "remediation_quality": <0.0-1.0>,
  "severity_accuracy": <0.0-1.0>,
  "composite": <weighted average>,
  "reasoning": "brief explanation of scores"
}}"""


def score_resolution(incident: dict, resolution: dict) -> dict:
    tracer = get_tracer()
    ground_truth = incident.get("ground_truth", {})

    with tracer.start_as_current_span("darwin.judge", kind=SpanKind.CLIENT) as span:
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
                    max_output_tokens=512,
                ),
            )

            scores = json.loads(response.text)
            scores["composite"] = round(
                scores["root_cause_accuracy"] * 0.4
                + scores["remediation_quality"] * 0.4
                + scores["severity_accuracy"] * 0.2,
                3,
            )
            span.set_attribute("score.composite", scores["composite"])
            span.set_attribute("score.root_cause_accuracy", scores["root_cause_accuracy"])
            span.set_attribute("score.remediation_quality", scores["remediation_quality"])
            span.set_attribute("score.severity_accuracy", scores["severity_accuracy"])
            span_ok(span)
            return scores
        except Exception as exc:
            span_error(span, exc)
            raise
