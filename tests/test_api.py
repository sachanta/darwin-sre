"""Tests for FastAPI backend — replay endpoints + SSE contract."""
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# App fixture — fresh per test to avoid state bleed from _sse_queues
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    # Import late so storage.py doesn't try to connect at collection time
    from api.app import create_app
    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 27, 10, 0, 0, tzinfo=timezone.utc)
_NOW2 = datetime(2026, 6, 27, 10, 5, 0, tzinfo=timezone.utc)
_NOW3 = datetime(2026, 6, 27, 10, 10, 0, tzinfo=timezone.utc)

MOCK_RUN = {
    "run_id": "run_test_001",
    "started_at": _NOW,
    "finished_at": _NOW3,
    "num_generations": 2,
    "baseline_avg": 0.82,
    "final_avg": 0.88,
    "episode_order": ["inc_001", "inc_002"],
    "status": "complete",
}

MOCK_RESOLUTION = {
    "incident_id": "inc_001",
    "generation": 0,
    "resolution": {"root_cause": "cache miss", "severity": "P2",
                   "remediation_steps": ["flush"], "estimated_resolution_minutes": 5, "confidence": "high"},
    "scores": {"composite": 0.85, "root_cause_accuracy": 0.9, "remediation_quality": 0.8, "severity_accuracy": 1.0},
    "retrieved_kb_ids": ["kb_001"],
    "run_id": "run_test_001",
    "timestamp": _NOW,
}

MOCK_ALERT = {
    "_id": "alert_abc",
    "raised_at": _NOW2,
    "rolling_avg": 0.35,
    "window_scores": [0.30, 0.38, 0.37],
    "failing_incident_ids": ["inc_002"],
    "generation": 0,
    "status": "resolved",
    "resolved_at": _NOW3,
    "run_id": "run_test_001",
}

MOCK_GENERATION = {
    "generation_id": 1,
    "system_prompt": "...",
    "prompt_diff": "[skill written] CCF-1 skill",
    "score_before": 0.35,
    "score_after": 0.82,
    "failed_incident_ids": ["inc_002"],
    "failure_patterns": ["CCF-1"],
    "new_kb_article_id": "kb_darwin_001",
    "run_id": "run_test_001",
    "timestamp": _NOW3,
}

MOCK_INCIDENT = {
    "id": "inc_001",
    "title": "Redis cache miss",
    "description": "Cache miss rate at 90%",
    "service": "checkout-service",
    "environment": "production",
    "category": "cache",
    "is_edge_case": False,
    "edge_case_family": None,
    "log_id": "log_001",
    "kb_refs": [],
    "metrics": {"cache_miss_rate": 0.9},
    "ground_truth": {"root_cause": "eviction", "severity": "P2", "remediation_steps": ["flush"]},
}

MOCK_LOG = {
    "id": "log_001",
    "incident_id": "inc_001",
    "lines": [{"ts": "2026-06-27T10:00:01Z", "level": "WARN", "msg": "Cache miss spike"}],
    "summary": "Cache miss",
}

MOCK_KB = {
    "id": "kb_001",
    "title": "Runbook: Redis eviction",
    "body": "Check maxmemory policy.",
    "service": "general",
    "tags": ["cache", "redis"],
    "source": "seed",
    "created_by_generation": None,
    "embedding": [0.1] * 1024,  # should be stripped before response
}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

class TestRuns:
    def test_list_runs(self, client):
        with patch("darwin.storage.list_runs", return_value=[MOCK_RUN]):
            resp = client.get("/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert data["runs"][0]["run_id"] == "run_test_001"

    def test_get_run_found(self, client):
        with patch("darwin.storage.get_run", return_value=MOCK_RUN):
            resp = client.get("/runs/run_test_001")
        assert resp.status_code == 200
        assert resp.json()["run_id"] == "run_test_001"

    def test_get_run_not_found(self, client):
        with patch("darwin.storage.get_run", return_value=None):
            resp = client.get("/runs/nonexistent")
        assert resp.status_code == 404

    def test_run_timestamps_serialized(self, client):
        with patch("darwin.storage.get_run", return_value=MOCK_RUN):
            resp = client.get("/runs/run_test_001")
        data = resp.json()
        # Timestamps must be strings, not datetime objects
        assert isinstance(data["started_at"], str)
        assert isinstance(data["finished_at"], str)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

class TestTimeline:
    def _mock_timeline(self, client, run=MOCK_RUN, resolutions=None, alerts=None, generations=None):
        with patch("darwin.storage.get_run", return_value=run), \
             patch("darwin.storage.get_recent_resolutions", return_value=resolutions or [MOCK_RESOLUTION]), \
             patch("darwin.storage.get_all_alerts", return_value=alerts or [MOCK_ALERT]), \
             patch("darwin.storage.get_all_generations", return_value=generations or [MOCK_GENERATION]):
            return client.get(f"/runs/{run['run_id']}/timeline")

    def test_timeline_found(self, client):
        resp = self._mock_timeline(client)
        assert resp.status_code == 200

    def test_timeline_not_found(self, client):
        with patch("darwin.storage.get_run", return_value=None):
            resp = client.get("/runs/nope/timeline")
        assert resp.status_code == 404

    def test_timeline_contains_expected_types(self, client):
        resp = self._mock_timeline(client)
        types = {e["type"] for e in resp.json()["events"]}
        assert "incident_resolved" in types
        assert "alert_raised" in types
        assert "darwin_complete" in types

    def test_timeline_chronological_order(self, client):
        resp = self._mock_timeline(client)
        events = resp.json()["events"]
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps), "Timeline events must be in chronological order"

    def test_timeline_includes_alert_resolved(self, client):
        resp = self._mock_timeline(client)
        types = [e["type"] for e in resp.json()["events"]]
        assert "alert_resolved" in types

    def test_timeline_darwin_complete_has_score_fields(self, client):
        resp = self._mock_timeline(client)
        dc = next(e for e in resp.json()["events"] if e["type"] == "darwin_complete")
        assert "score_before" in dc
        assert "score_after" in dc
        assert dc["score_after"] > dc["score_before"]

    def test_timeline_incident_resolved_has_scores(self, client):
        resp = self._mock_timeline(client)
        ir = next(e for e in resp.json()["events"] if e["type"] == "incident_resolved")
        assert "scores" in ir
        assert "composite" in ir["scores"]

    def test_timeline_run_summary_included(self, client):
        resp = self._mock_timeline(client)
        data = resp.json()
        assert "run" in data
        assert data["run"]["num_generations"] == 2


# ---------------------------------------------------------------------------
# Incident detail
# ---------------------------------------------------------------------------

class TestIncidentDetail:
    def test_incident_detail_found(self, client):
        with patch("darwin.storage.get_incident", return_value=MOCK_INCIDENT), \
             patch("darwin.storage.get_log", return_value=MOCK_LOG), \
             patch("darwin.storage.get_resolution", return_value=MOCK_RESOLUTION), \
             patch("darwin.storage.get_kb_article", return_value=dict(MOCK_KB)):
            resp = client.get("/incidents/inc_001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["incident"]["id"] == "inc_001"
        assert data["log"]["id"] == "log_001"
        assert len(data["kb_articles"]) == 1
        assert data["resolution"]["incident_id"] == "inc_001"

    def test_incident_detail_not_found(self, client):
        with patch("darwin.storage.get_incident", return_value=None):
            resp = client.get("/incidents/nope")
        assert resp.status_code == 404

    def test_incident_detail_no_log(self, client):
        inc = dict(MOCK_INCIDENT, log_id=None)
        with patch("darwin.storage.get_incident", return_value=inc), \
             patch("darwin.storage.get_resolution", return_value=None):
            resp = client.get("/incidents/inc_001")
        assert resp.status_code == 200
        assert resp.json()["log"] is None

    def test_kb_embedding_stripped_from_response(self, client):
        with patch("darwin.storage.get_incident", return_value=MOCK_INCIDENT), \
             patch("darwin.storage.get_log", return_value=MOCK_LOG), \
             patch("darwin.storage.get_resolution", return_value=MOCK_RESOLUTION), \
             patch("darwin.storage.get_kb_article", return_value=dict(MOCK_KB)):
            resp = client.get("/incidents/inc_001")
        for art in resp.json()["kb_articles"]:
            assert "embedding" not in art, "embedding must be stripped before sending to browser"


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------

class TestKnowledge:
    def test_list_all_articles(self, client):
        with patch("darwin.storage.get_all_kb_articles", return_value=[dict(MOCK_KB)]):
            resp = client.get("/knowledge")
        assert resp.status_code == 200
        assert len(resp.json()["articles"]) == 1

    def test_filter_by_source(self, client):
        seed = dict(MOCK_KB, source="seed")
        darwin_kb = dict(MOCK_KB, id="kb_d", source="darwin")
        with patch("darwin.storage.get_all_kb_articles", return_value=[seed, darwin_kb]):
            resp = client.get("/knowledge?source=darwin")
        assert len(resp.json()["articles"]) == 1
        assert resp.json()["articles"][0]["source"] == "darwin"

    def test_embedding_stripped(self, client):
        with patch("darwin.storage.get_all_kb_articles", return_value=[dict(MOCK_KB)]):
            resp = client.get("/knowledge")
        for art in resp.json()["articles"]:
            assert "embedding" not in art


# ---------------------------------------------------------------------------
# Alerts + generations
# ---------------------------------------------------------------------------

class TestAlertsAndGenerations:
    def test_list_alerts(self, client):
        with patch("darwin.storage.get_all_alerts", return_value=[MOCK_ALERT]):
            resp = client.get("/alerts")
        assert resp.status_code == 200
        assert len(resp.json()["alerts"]) == 1

    def test_list_generations(self, client):
        with patch("darwin.storage.get_all_generations", return_value=[MOCK_GENERATION]):
            resp = client.get("/generations")
        assert resp.status_code == 200
        gens = resp.json()["generations"]
        assert gens[0]["generation_id"] == 1
        assert gens[0]["score_after"] > gens[0]["score_before"]


# ---------------------------------------------------------------------------
# SSE stream contract
# ---------------------------------------------------------------------------

class TestSSEContract:
    def test_stream_unknown_run_404(self, client):
        resp = client.get("/stream/nonexistent_run")
        assert resp.status_code == 404

    def test_post_run_returns_run_id(self, client):
        """POST /run should return a run_id without blocking."""
        with patch("api.routes._start_run_background"), \
             patch("threading.Thread"):
            resp = client.post("/run")
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert data["run_id"].startswith("run_")
        assert "stream_url" in data
