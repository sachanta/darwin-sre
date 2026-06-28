"""Smoke tests: frontend static files served correctly + API contract for UI."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone


@pytest.fixture
def client():
    from api.app import create_app
    return TestClient(create_app())


_NOW = datetime(2026, 6, 27, 10, 0, 0, tzinfo=timezone.utc)
_RUN = {
    "run_id": "run_smoke_001",
    "started_at": _NOW,
    "finished_at": _NOW,
    "num_generations": 2,
    "baseline_avg": 0.82,
    "final_avg": 0.88,
    "episode_order": [],
    "status": "complete",
}


class TestStaticFrontend:
    def test_index_html_served(self, client):
        """index.html must be reachable at / — the UI entry point."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_index_contains_app_script(self, client):
        resp = client.get("/")
        assert b"app.js" in resp.content

    def test_style_css_served(self, client):
        resp = client.get("/style.css")
        assert resp.status_code == 200

    def test_app_js_served(self, client):
        resp = client.get("/app.js")
        assert resp.status_code == 200


class TestTimelineContract:
    """Verify the timeline shape matches what app.js expects."""

    def test_timeline_event_types_present(self, client):
        """UI checks for these four event type strings exactly."""
        res = {
            "incident_id": "i1", "generation": 0,
            "scores": {"composite": 0.85, "root_cause_accuracy": 0.9,
                       "remediation_quality": 0.8, "severity_accuracy": 1.0},
            "retrieved_kb_ids": [], "timestamp": _NOW,
        }
        alert = {
            "_id": "a1", "raised_at": _NOW, "rolling_avg": 0.35,
            "window_scores": [0.3], "status": "resolved", "resolved_at": _NOW,
            "run_id": "run_smoke_001",
        }
        gen = {
            "generation_id": 1, "system_prompt": "x", "prompt_diff": "[skill]",
            "score_before": 0.35, "score_after": 0.82,
            "failed_incident_ids": [], "failure_patterns": ["CCF-1"],
            "new_kb_article_id": "kb_d", "timestamp": _NOW,
            "run_id": "run_smoke_001",
        }

        with patch("darwin.storage.get_run", return_value=_RUN), \
             patch("darwin.storage.get_recent_resolutions", return_value=[res]), \
             patch("darwin.storage.get_all_alerts", return_value=[alert]), \
             patch("darwin.storage.get_all_generations", return_value=[gen]):
            resp = client.get("/runs/run_smoke_001/timeline")

        assert resp.status_code == 200
        types = {e["type"] for e in resp.json()["events"]}
        for expected in ("incident_resolved", "alert_raised", "alert_resolved", "darwin_complete"):
            assert expected in types, f"missing event type: {expected}"

    def test_incident_resolved_has_scores_key(self, client):
        res = {
            "incident_id": "i1", "generation": 0,
            "scores": {"composite": 0.85, "root_cause_accuracy": 0.9,
                       "remediation_quality": 0.8, "severity_accuracy": 1.0},
            "retrieved_kb_ids": [], "timestamp": _NOW,
        }
        with patch("darwin.storage.get_run", return_value=_RUN), \
             patch("darwin.storage.get_recent_resolutions", return_value=[res]), \
             patch("darwin.storage.get_all_alerts", return_value=[]), \
             patch("darwin.storage.get_all_generations", return_value=[]):
            resp = client.get("/runs/run_smoke_001/timeline")

        ev = next(e for e in resp.json()["events"] if e["type"] == "incident_resolved")
        assert "scores" in ev
        assert "composite" in ev["scores"]

    def test_darwin_complete_has_score_diff(self, client):
        gen = {
            "generation_id": 1, "system_prompt": "x", "prompt_diff": "[skill]",
            "score_before": 0.35, "score_after": 0.82,
            "failed_incident_ids": [], "failure_patterns": [],
            "new_kb_article_id": None, "timestamp": _NOW,
            "run_id": "run_smoke_001",
        }
        with patch("darwin.storage.get_run", return_value=_RUN), \
             patch("darwin.storage.get_recent_resolutions", return_value=[]), \
             patch("darwin.storage.get_all_alerts", return_value=[]), \
             patch("darwin.storage.get_all_generations", return_value=[gen]):
            resp = client.get("/runs/run_smoke_001/timeline")

        ev = next(e for e in resp.json()["events"] if e["type"] == "darwin_complete")
        assert ev["score_before"] < ev["score_after"]
        assert "new_kb_article_id" in ev
