"""Degradation detector — Option C: reads composite scores from Arize OTel spans.

Primary path: query recent `sre.resolve` spans in Arize for score.composite
attributes → rolling average → alert when below threshold.

Fallback: the caller passes in local results (already in memory from the run
loop). Used when Arize is unavailable, spans haven't propagated yet (<2min
latency), or ARIZE_API_KEY is absent.

The loop calls `should_trigger(window_scores)` with the local deque; this
module enriches the picture with Arize data when available but never blocks.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import httpx

from config import (
    ARIZE_API_KEY,
    ARIZE_PROJECT_NAME,
    ARIZE_SPACE_ID,
    DARWIN_TRIGGER_THRESHOLD,
    DARWIN_WINDOW_SIZE,
)

log = logging.getLogger(__name__)

_ARIZE_GRAPHQL = "https://app.arize.com/graphql"
_SPAN_QUERY = """
query RecentSpans($spaceId: ID!, $project: String!, $after: DateTime!, $limit: Int!) {
  spans(
    spaceId: $spaceId,
    projectName: $project,
    filter: {spanName: "sre.resolve", startTime: {greaterThan: $after}},
    first: $limit,
    order: {col: startTime, dir: desc}
  ) {
    edges {
      node {
        spanId
        startTime
        attributes
      }
    }
  }
}
"""


class TriggerDecision(NamedTuple):
    should_trigger: bool
    rolling_avg: float
    source: str          # "arize" | "local"
    window_scores: list[float]


def _fetch_arize_scores(limit: int = 20, lookback_minutes: int = 10) -> list[float] | None:
    """Pull recent composite scores from Arize span attributes.

    Returns a list of floats (most-recent first), or None if unavailable.
    """
    if not ARIZE_API_KEY or not ARIZE_SPACE_ID:
        return None

    after = (datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).isoformat()
    payload = {
        "query": _SPAN_QUERY,
        "variables": {
            "spaceId": ARIZE_SPACE_ID,
            "project": ARIZE_PROJECT_NAME,
            "after": after,
            "limit": limit,
        },
    }

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                _ARIZE_GRAPHQL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {ARIZE_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                log.debug("Arize GraphQL %s: %s", resp.status_code, resp.text[:200])
                return None

            data = resp.json()
            edges = (data.get("data") or {}).get("spans", {}).get("edges", [])
            scores = []
            for edge in edges:
                attrs = edge.get("node", {}).get("attributes") or {}
                # Arize flattens OTel attributes: look for score.composite or the raw key
                for key in ("score.composite", "darwin.score.composite", "composite"):
                    if key in attrs:
                        try:
                            scores.append(float(attrs[key]))
                        except (TypeError, ValueError):
                            pass
                        break
            return scores if scores else None
    except Exception as exc:
        log.debug("Arize fetch failed (using local fallback): %s", exc)
        return None


def should_trigger(local_window: deque | list[float]) -> TriggerDecision:
    """Decide whether Darwin should fire based on the recent score window.

    Tries Arize first; falls back to the caller's local window.
    Returns a TriggerDecision so the caller can log the source.
    """
    arize_scores = _fetch_arize_scores(
        limit=DARWIN_WINDOW_SIZE * 3,
        lookback_minutes=15,
    )

    if arize_scores and len(arize_scores) >= DARWIN_WINDOW_SIZE:
        # Use the most recent WINDOW_SIZE scores from Arize
        window = arize_scores[:DARWIN_WINDOW_SIZE]
        avg = sum(window) / len(window)
        return TriggerDecision(
            should_trigger=(len(window) == DARWIN_WINDOW_SIZE and avg < DARWIN_TRIGGER_THRESHOLD),
            rolling_avg=avg,
            source="arize",
            window_scores=window,
        )

    # Local fallback — the in-memory deque from the run loop
    local = list(local_window)
    avg = sum(local) / len(local) if local else 1.0
    return TriggerDecision(
        should_trigger=(len(local) >= DARWIN_WINDOW_SIZE and avg < DARWIN_TRIGGER_THRESHOLD),
        rolling_avg=avg,
        source="local",
        window_scores=local,
    )
