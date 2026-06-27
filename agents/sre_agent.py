import json
from openai import OpenAI
from opentelemetry.trace import SpanKind
from config import DO_API_KEY, DO_BASE_URL, SRE_MODEL
from observability import get_tracer, span_ok, span_error

_client = OpenAI(api_key=DO_API_KEY, base_url=DO_BASE_URL)

DEFAULT_SYSTEM_PROMPT = """You are an expert Site Reliability Engineer (SRE) with 10+ years of experience in production systems.

When given an incident report, you will:
1. Identify the root cause precisely
2. Assess the correct severity (P1=critical/down, P2=degraded, P3=minor)
3. Provide clear, ordered remediation steps
4. Estimate resolution time in minutes

Always respond in valid JSON with this exact structure:
{
  "root_cause": "precise technical description of what caused the incident",
  "severity": "P1" | "P2" | "P3",
  "remediation_steps": ["step 1", "step 2", "step 3"],
  "estimated_resolution_minutes": <integer>,
  "confidence": "high" | "medium" | "low"
}"""


def resolve_incident(incident: dict, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> dict:
    tracer = get_tracer()

    with tracer.start_as_current_span(
        "sre.resolve",
        kind=SpanKind.CLIENT,
    ) as span:
        span.set_attribute("incident.id", incident["id"])
        span.set_attribute("incident.title", incident["title"])
        span.set_attribute("incident.service", incident["service"])
        span.set_attribute("incident.category", incident["category"])
        span.set_attribute("incident.is_edge_case", incident.get("is_edge_case", False))
        span.set_attribute("llm.model", SRE_MODEL)

        user_message = f"""INCIDENT REPORT
Title: {incident['title']}
Service: {incident['service']}
Environment: {incident.get('environment', 'production')}
Category: {incident['category']}

Description:
{incident['description']}

Recent Logs:
{incident['logs']}

Metrics:
{json.dumps(incident['metrics'], indent=2)}

Analyze this incident and provide your resolution."""

        try:
            # OpenAI call is auto-instrumented by OpenAIInstrumentor
            response = _client.chat.completions.create(
                model=SRE_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_completion_tokens=1024,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
            result = json.loads(raw)
            span.set_attribute("output.root_cause", result.get("root_cause", "")[:200])
            span.set_attribute("output.severity", result.get("severity", ""))
            span.set_attribute("output.confidence", result.get("confidence", ""))
            span_ok(span)
            return result
        except Exception as exc:
            span_error(span, exc)
            raise
