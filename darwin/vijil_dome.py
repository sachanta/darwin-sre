"""Vijil Dome runtime guardrail for Darwin SRE.

Light profile (no torch/ML models):
  Input  → security-guard (encoding-heuristics) + moderation-guard (moderation-flashtext)
  Output → moderation-guard (moderation-flashtext) + privacy-guard (detect-secrets)

Fail-open: if Dome errors, the incident is allowed through (operational continuity).
"""
from __future__ import annotations
import json
from typing import Any
from opentelemetry.trace import SpanKind
from observability import get_tracer, span_ok

_DOME_CONFIG = {
    "input": [
        {"category": "security-guard", "detectors": ["encoding-heuristics"]},
        {"category": "moderation-guard", "detectors": ["moderation-flashtext"]},
    ],
    "output": [
        {"category": "moderation-guard", "detectors": ["moderation-flashtext"]},
        {"category": "privacy-guard", "detectors": ["detect-secrets"]},
    ],
}

_dome = None


def _get_dome():
    global _dome
    if _dome is None:
        from vijil_dome import Dome, create_dome_config
        _dome = Dome(create_dome_config(_DOME_CONFIG))
    return _dome


class GuardResult:
    def __init__(self, allowed: bool, flagged: bool, triggered: list[str], text: str):
        self.allowed = allowed
        self.flagged = flagged
        self.triggered = triggered
        self.text = text  # possibly redacted output

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "flagged": self.flagged,
            "triggered_methods": self.triggered,
        }


def guard_incident_input(incident: dict) -> GuardResult:
    text = f"{incident.get('title', '')} {incident.get('description', '')} {incident.get('logs', '')}"
    tracer = get_tracer()
    with tracer.start_as_current_span("vijil.guard_input", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("openinference.span.kind", "GUARDRAIL")
        span.set_attribute("incident.id", incident.get("id", ""))
        try:
            dome = _get_dome()
            result = dome.guard_input(text)
            guard = GuardResult(
                allowed=not result.flagged,
                flagged=result.flagged,
                triggered=result.triggered_methods or [],
                text=text,
            )
        except Exception as exc:
            guard = GuardResult(allowed=True, flagged=False, triggered=[f"dome_error:{exc}"], text=text)
        span.set_attribute("vijil.flagged", guard.flagged)
        span.set_attribute("vijil.allowed", guard.allowed)
        span.set_attribute("vijil.triggered", str(guard.triggered))
        span_ok(span)
        return guard


def guard_resolution_output(resolution: dict) -> GuardResult:
    text = json.dumps(resolution)
    tracer = get_tracer()
    with tracer.start_as_current_span("vijil.guard_output", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("openinference.span.kind", "GUARDRAIL")
        try:
            dome = _get_dome()
            result = dome.guard_output(text)
            guard = GuardResult(
                allowed=not result.flagged,
                flagged=result.flagged,
                triggered=result.triggered_methods or [],
                text=result.response_string if result.flagged else text,
            )
        except Exception as exc:
            guard = GuardResult(allowed=True, flagged=False, triggered=[f"dome_error:{exc}"], text=text)
        span.set_attribute("vijil.flagged", guard.flagged)
        span.set_attribute("vijil.allowed", guard.allowed)
        span.set_attribute("vijil.triggered", str(guard.triggered))
        span_ok(span)
        return guard
