"""Tests for Phase D: alert lifecycle, KB-write, replay-validate, run_id tagging."""
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _good_scores(composite=0.85):
    return {
        "composite": composite,
        "root_cause_accuracy": 0.9,
        "remediation_quality": 0.8,
        "severity_accuracy": 1.0,
    }

def _bad_scores(composite=0.30):
    return {
        "composite": composite,
        "root_cause_accuracy": 0.2,
        "remediation_quality": 0.3,
        "severity_accuracy": 0.5,
    }

def _resolution():
    return {
        "root_cause": "x", "severity": "P2",
        "remediation_steps": ["a"], "estimated_resolution_minutes": 5, "confidence": "high",
    }

def _skill():
    return {
        "id": "skill_001_abc123",
        "name": "CCF-1 Upstream Cascade Fix",
        "guidance": "Check upstream dependencies first.",
        "tags": ["CCF-1", "database"],
        "created_by_generation": 1,
        "use_count": 0,
        "active": True,
    }

def _kb_article():
    return {
        "id": "kb_darwin_001_abc123",
        "title": "Runbook: CCF-1 Upstream Cascade",
        "body": "When auth fails with DB error but DB CPU is low, check upstream service.",
        "service": "general",
        "tags": ["CCF-1", "database"],
        "source": "darwin",
        "created_by_generation": 1,
    }

def _make_incidents(n, is_edge_case=False, family="CCF-1", service="auth-api"):
    return [
        {
            "id": f"inc_{i:03d}",
            "title": f"Incident {i}",
            "description": "Test incident",
            "service": service,
            "environment": "production",
            "category": "database",
            "is_edge_case": is_edge_case,
            "edge_case_family": family if is_edge_case else None,
            "log_id": None,
            "kb_refs": [],
            "metrics": {},
            "ground_truth": {
                "root_cause": "upstream timeout",
                "severity": "P1",
                "remediation_steps": ["check upstream"],
            },
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Alert lifecycle
# ---------------------------------------------------------------------------

class TestAlertLifecycle:
    def _run_with_trigger(self, incident_scores, replay_score=0.85):
        """Run the loop with given per-incident scores; replay returns replay_score."""
        from darwin.loop import DarwinLoop

        # Replay in _run_darwin calls score_resolution up to 5 more times
        replay_s = {"composite": replay_score, "root_cause_accuracy": 0.9,
                    "remediation_quality": 0.8, "severity_accuracy": 1.0}
        all_scores = incident_scores + [replay_s] * 5
        score_seq = iter(all_scores)
        replay_seq = iter([replay_s] * 10)

        events = []

        def on_event(e):
            events.append(e)

        with patch("darwin.loop.guard_incident_input") as gi, \
             patch("darwin.loop.guard_resolution_output") as go, \
             patch("darwin.loop.retrieve_skills", return_value=[]), \
             patch("darwin.loop.increment_skill_use"), \
             patch("darwin.retrieval.retrieve_kbs", return_value=[]), \
             patch("darwin.loop.resolve_incident", return_value=_resolution()), \
             patch("darwin.loop.score_resolution", side_effect=lambda *a: next(score_seq)), \
             patch("darwin.loop.save_resolution", return_value="res_id"), \
             patch("darwin.loop.save_alert", return_value="alert_001") as mock_save_alert, \
             patch("darwin.loop.update_alert_status") as mock_update, \
             patch("darwin.loop.generate_skill", return_value=(_skill(), "desc")), \
             patch("darwin.loop.generate_kb_article", return_value=_kb_article()), \
             patch("darwin.retrieval.index_kb_article", side_effect=lambda a: a), \
             patch("darwin.loop.save_kb_article"), \
             patch("darwin.loop.save_skill"), \
             patch("darwin.loop.save_generation", return_value="gen_id"), \
             patch("darwin.loop.retire_stale_skills", return_value=[]), \
             patch("darwin.loop.create_experiment"), \
             patch("darwin.loop.record_mutation"), \
             patch("darwin.loop.ensure_agent"):

            gi.return_value = MagicMock(allowed=True, flagged=False, triggered=[])
            go.return_value = MagicMock(allowed=True, flagged=False, triggered=[])

            # override score_resolution so replay uses replay_seq
            incidents = _make_incidents(3, is_edge_case=True)
            loop = DarwinLoop(on_event=on_event)
            loop.run(incidents, register_vijil=False)

        return events, mock_save_alert, mock_update

    def test_alert_raised_when_window_tanks(self):
        scores = [_bad_scores(0.25), _bad_scores(0.30), _bad_scores(0.28)]
        events, mock_alert, _ = self._run_with_trigger(scores)
        alert_types = [e["type"] for e in events]
        assert "alert_raised" in alert_types

    def test_alert_not_raised_when_scores_good(self):
        scores = [_good_scores(0.85), _good_scores(0.90), _good_scores(0.88)]
        events, mock_alert, _ = self._run_with_trigger(scores)
        assert not mock_alert.called

    def test_alert_status_transitions_to_improving(self):
        scores = [_bad_scores(0.25), _bad_scores(0.30), _bad_scores(0.28)]
        _, _, mock_update = self._run_with_trigger(scores, replay_score=0.85)
        calls = [c.args[1] for c in mock_update.call_args_list]
        assert "improving" in calls

    def test_alert_resolved_when_replay_improves(self):
        scores = [_bad_scores(0.25), _bad_scores(0.30), _bad_scores(0.28)]
        events, _, mock_update = self._run_with_trigger(scores, replay_score=0.85)
        calls = [c.args[1] for c in mock_update.call_args_list]
        assert "resolved" in calls
        assert any(e["type"] == "alert_resolved" for e in events)

    def test_alert_id_passed_to_darwin_complete(self):
        scores = [_bad_scores(0.25), _bad_scores(0.30), _bad_scores(0.28)]
        events, _, _ = self._run_with_trigger(scores)
        darwin_start = next((e for e in events if e["type"] == "darwin_start"), None)
        assert darwin_start is not None
        assert darwin_start.get("alert_id") == "alert_001"


# ---------------------------------------------------------------------------
# KB-write on evolution
# ---------------------------------------------------------------------------

class TestKBWriteOnEvolution:
    def _run_darwin_direct(self):
        """Trigger _run_darwin directly with controlled mocks."""
        from darwin.loop import DarwinLoop

        loop = DarwinLoop(run_id="run_test")
        loop.generation = 0
        loop.all_results = [
            {
                "incident": _make_incidents(1, is_edge_case=True)[0],
                "resolution": _resolution(),
                "scores": _bad_scores(0.25),
                "generation": 0,
                "rolling_avg": 0.25,
                "skills_used": [],
                "kb_used": [],
            }
        ] * 3
        for s in [0.25, 0.30, 0.28]:
            loop.window.append(s)

        saved_kb = []

        with patch("darwin.loop.generate_skill", return_value=(_skill(), "desc")), \
             patch("darwin.loop.generate_kb_article", return_value=_kb_article()) as mock_gen_kb, \
             patch("darwin.retrieval.index_kb_article", side_effect=lambda a: {**a, "embedding": [0.1] * 1024}) as mock_idx, \
             patch("darwin.loop.save_kb_article", side_effect=lambda a: saved_kb.append(a)) as mock_save_kb, \
             patch("darwin.loop.save_skill"), \
             patch("darwin.loop.retrieve_skills", return_value=[]), \
             patch("darwin.retrieval.retrieve_kbs", return_value=[]), \
             patch("darwin.loop.resolve_incident", return_value=_resolution()), \
             patch("darwin.loop.score_resolution", return_value=_good_scores(0.85)), \
             patch("darwin.loop.save_generation"), \
             patch("darwin.loop.retire_stale_skills", return_value=[]), \
             patch("darwin.loop.update_alert_status"), \
             patch("darwin.loop.create_experiment"), \
             patch("darwin.loop.record_mutation"):

            loop._run_darwin(loop.all_results[-1], alert_id="alert_001")

        return saved_kb, mock_gen_kb, mock_idx, mock_save_kb

    def test_generate_kb_article_called(self):
        _, mock_gen, _, _ = self._run_darwin_direct()
        assert mock_gen.called

    def test_kb_article_indexed(self):
        _, _, mock_idx, _ = self._run_darwin_direct()
        assert mock_idx.called

    def test_kb_article_saved_with_darwin_source(self):
        saved, _, _, _ = self._run_darwin_direct()
        assert saved, "save_kb_article was never called"
        assert saved[0]["source"] == "darwin"

    def test_kb_article_id_in_generation_doc(self):
        from darwin.loop import DarwinLoop

        loop = DarwinLoop(run_id="run_test")
        loop.generation = 0
        loop.all_results = [
            {
                "incident": _make_incidents(1, is_edge_case=True)[0],
                "resolution": _resolution(),
                "scores": _bad_scores(0.25),
                "generation": 0,
                "rolling_avg": 0.25,
                "skills_used": [],
                "kb_used": [],
            }
        ] * 3
        for s in [0.25, 0.30, 0.28]:
            loop.window.append(s)

        saved_gen = {}

        def capture_gen(**kw):
            saved_gen.update(kw)
            return "gen_id"

        with patch("darwin.loop.generate_skill", return_value=(_skill(), "desc")), \
             patch("darwin.loop.generate_kb_article", return_value=_kb_article()), \
             patch("darwin.retrieval.index_kb_article", side_effect=lambda a: {**a, "embedding": [0.1] * 1024}), \
             patch("darwin.loop.save_kb_article"), \
             patch("darwin.loop.save_skill"), \
             patch("darwin.loop.retrieve_skills", return_value=[]), \
             patch("darwin.retrieval.retrieve_kbs", return_value=[]), \
             patch("darwin.loop.resolve_incident", return_value=_resolution()), \
             patch("darwin.loop.score_resolution", return_value=_good_scores(0.85)), \
             patch("darwin.loop.save_generation", side_effect=capture_gen), \
             patch("darwin.loop.retire_stale_skills", return_value=[]), \
             patch("darwin.loop.update_alert_status"), \
             patch("darwin.loop.create_experiment"), \
             patch("darwin.loop.record_mutation"):

            loop._run_darwin(loop.all_results[-1], alert_id="alert_001")

        assert saved_gen.get("new_kb_article_id") is not None

    def test_kb_write_failure_does_not_crash_darwin(self):
        from darwin.loop import DarwinLoop

        loop = DarwinLoop()
        loop.generation = 0
        loop.all_results = [
            {
                "incident": _make_incidents(1, is_edge_case=True)[0],
                "resolution": _resolution(),
                "scores": _bad_scores(0.25),
                "generation": 0, "rolling_avg": 0.25,
                "skills_used": [], "kb_used": [],
            }
        ] * 3
        for s in [0.25, 0.30, 0.28]:
            loop.window.append(s)

        with patch("darwin.loop.generate_skill", return_value=(_skill(), "desc")), \
             patch("darwin.loop.generate_kb_article", side_effect=Exception("LLM timeout")), \
             patch("darwin.loop.save_skill"), \
             patch("darwin.loop.retrieve_skills", return_value=[]), \
             patch("darwin.retrieval.retrieve_kbs", return_value=[]), \
             patch("darwin.loop.resolve_incident", return_value=_resolution()), \
             patch("darwin.loop.score_resolution", return_value=_good_scores(0.85)), \
             patch("darwin.loop.save_generation"), \
             patch("darwin.loop.retire_stale_skills", return_value=[]), \
             patch("darwin.loop.update_alert_status"), \
             patch("darwin.loop.create_experiment"), \
             patch("darwin.loop.record_mutation"):

            # Should not raise
            loop._run_darwin(loop.all_results[-1])


# ---------------------------------------------------------------------------
# run_id tagging
# ---------------------------------------------------------------------------

class TestRunIdTagging:
    def test_run_id_passed_to_save_resolution(self, sample_incident):
        from darwin.loop import DarwinLoop

        saved = []

        with patch("darwin.loop.guard_incident_input") as gi, \
             patch("darwin.loop.guard_resolution_output") as go, \
             patch("darwin.loop.retrieve_skills", return_value=[]), \
             patch("darwin.loop.increment_skill_use"), \
             patch("darwin.retrieval.retrieve_kbs", return_value=[]), \
             patch("darwin.loop.resolve_incident", return_value=_resolution()), \
             patch("darwin.loop.score_resolution", return_value=_good_scores()), \
             patch("darwin.loop.save_resolution", side_effect=lambda *a, **kw: saved.append(kw)) as mock_save, \
             patch("darwin.loop.ensure_agent"):

            gi.return_value = MagicMock(allowed=True, flagged=False, triggered=[])
            go.return_value = MagicMock(allowed=True, flagged=False, triggered=[])

            loop = DarwinLoop(run_id="run_abc123")
            loop.run([sample_incident], register_vijil=False)

        assert saved[0].get("run_id") == "run_abc123"

    def test_run_id_passed_to_save_alert(self):
        from darwin.loop import DarwinLoop

        with patch("darwin.loop.guard_incident_input") as gi, \
             patch("darwin.loop.guard_resolution_output") as go, \
             patch("darwin.loop.retrieve_skills", return_value=[]), \
             patch("darwin.loop.increment_skill_use"), \
             patch("darwin.retrieval.retrieve_kbs", return_value=[]), \
             patch("darwin.loop.resolve_incident", return_value=_resolution()), \
             patch("darwin.loop.score_resolution", return_value=_bad_scores(0.25)), \
             patch("darwin.loop.save_resolution", return_value="r"), \
             patch("darwin.loop.save_alert", return_value="alert_x") as mock_alert, \
             patch("darwin.loop.update_alert_status"), \
             patch("darwin.loop.generate_skill", return_value=(_skill(), "d")), \
             patch("darwin.loop.generate_kb_article", return_value=_kb_article()), \
             patch("darwin.retrieval.index_kb_article", side_effect=lambda a: a), \
             patch("darwin.loop.save_kb_article"), \
             patch("darwin.loop.save_skill"), \
             patch("darwin.loop.save_generation"), \
             patch("darwin.loop.retire_stale_skills", return_value=[]), \
             patch("darwin.loop.create_experiment"), \
             patch("darwin.loop.record_mutation"), \
             patch("darwin.loop.ensure_agent"):

            gi.return_value = MagicMock(allowed=True, flagged=False, triggered=[])
            go.return_value = MagicMock(allowed=True, flagged=False, triggered=[])

            loop = DarwinLoop(run_id="run_xyz789")
            loop.run(_make_incidents(3, is_edge_case=True), register_vijil=False)

        assert mock_alert.called
        call_kw = mock_alert.call_args.kwargs
        assert call_kw.get("run_id") == "run_xyz789"
