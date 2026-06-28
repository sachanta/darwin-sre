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


def _build_system_prompt(
    base_prompt: str,
    skills: list[dict] | None,
    kb_articles: list[dict] | None,
) -> str:
    """Assemble the full system prompt: base + skills + runbooks."""
    parts = [base_prompt]

    if skills:
        from darwin.skills import format_skills_for_prompt
        skill_section = format_skills_for_prompt(skills)
        if skill_section:
            parts.append(skill_section)

    if kb_articles:
        runbook_lines = ["", "## Relevant Runbooks", ""]
        for art in kb_articles:
            sim = art.get("similarity", "")
            sim_str = f" (similarity: {sim:.0%})" if isinstance(sim, float) else ""
            runbook_lines.append(f"### {art.get('title', 'Runbook')}{sim_str}")
            runbook_lines.append(art.get("body", ""))
            runbook_lines.append("")
        parts.append("\n".join(runbook_lines))

    return "\n".join(parts)


def _build_user_message(incident: dict) -> str:
    log_text = ""
    if incident.get("log_id"):
        from darwin.storage import get_log
        log = get_log(incident["log_id"])
        if log and log.get("lines"):
            log_text = "\n".join(
                f"[{l.get('ts','')}] {l.get('level','')} {l.get('msg','')}"
                for l in log["lines"][:8]
            )
    if not log_text:
        log_text = "(no logs available)"

    return f"""INCIDENT REPORT
Title: {incident['title']}
Service: {incident['service']}
Environment: {incident.get('environment', 'production')}
Category: {incident['category']}

Description:
{incident['description']}

Recent Logs:
{log_text}

Metrics:
{json.dumps(incident.get('metrics', {}), indent=2)}

Analyze this incident and provide your resolution in JSON."""


def _parse_response(raw: str) -> dict:
    """Parse JSON from model response, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def resolve_incident(
    incident: dict,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    skills: list[dict] | None = None,
    kb_articles: list[dict] | None = None,
    run_id: str | None = None,
    generation: int = 0,
) -> dict:
    tracer = get_tracer()

    full_system = _build_system_prompt(system_prompt, skills, kb_articles)
    user_message = _build_user_message(incident)

    with tracer.start_as_current_span("sre.resolve", kind=SpanKind.CLIENT) as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        span.set_attribute("incident.id", incident["id"])
        span.set_attribute("incident.title", incident["title"])
        span.set_attribute("incident.service", incident["service"])
        span.set_attribute("incident.category", incident["category"])
        span.set_attribute("incident.is_edge_case", incident.get("is_edge_case", False))
        span.set_attribute("incident.edge_case_family", incident.get("edge_case_family") or "")
        span.set_attribute("llm.model", SRE_MODEL)
        span.set_attribute("context.num_skills", len(skills) if skills else 0)
        span.set_attribute("context.num_kb_articles", len(kb_articles) if kb_articles else 0)
        span.set_attribute("context.skill_ids", str([s["id"] for s in skills] if skills else []))
        span.set_attribute("context.kb_ids", str([a["id"] for a in kb_articles] if kb_articles else []))
        if run_id:
            span.set_attribute("darwin.run_id", run_id)
        span.set_attribute("darwin.generation", generation)

        try:
            response = _client.chat.completions.create(
                model=SRE_MODEL,
                messages=[
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": user_message},
                ],
                max_completion_tokens=1024,
            )
            raw = response.choices[0].message.content
            result = _parse_response(raw)
            span.set_attribute("output.root_cause", result.get("root_cause", "")[:200])
            span.set_attribute("output.severity", result.get("severity", ""))
            span.set_attribute("output.confidence", str(result.get("confidence", "")))
            span_ok(span)
            return result
        except Exception as exc:
            span_error(span, exc)
            raise
