"""Arize AX tracing setup for Darwin SRE.

Call setup_tracing() once at startup. After that:
- All OpenAI client calls (DO inference) are auto-instrumented.
- Use get_tracer() for manual Darwin event spans.
"""
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

_tracer: trace.Tracer | None = None


def setup_tracing() -> trace.Tracer:
    global _tracer
    if _tracer is not None:
        return _tracer

    from arize.otel import register
    from openinference.instrumentation.openai import OpenAIInstrumentor
    import config

    if not config.ARIZE_API_KEY or not config.ARIZE_SPACE_ID:
        # Tracing disabled — return a no-op tracer
        _tracer = trace.get_tracer("darwin-sre")
        return _tracer

    register(
        space_id=config.ARIZE_SPACE_ID,
        api_key=config.ARIZE_API_KEY,
        project_name=config.ARIZE_PROJECT_NAME,
    )

    # Auto-instrument all OpenAI client calls (covers DO inference)
    OpenAIInstrumentor().instrument()

    _tracer = trace.get_tracer("darwin-sre")
    return _tracer


def get_tracer() -> trace.Tracer:
    if _tracer is None:
        return setup_tracing()
    return _tracer


def span_ok(span: trace.Span) -> None:
    span.set_status(Status(StatusCode.OK))


def span_error(span: trace.Span, exc: Exception) -> None:
    span.set_status(Status(StatusCode.ERROR, str(exc)))
    span.record_exception(exc)
