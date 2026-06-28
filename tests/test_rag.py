"""Tests for RAG pipeline: KB injection into SRE agent + retrieval_kb_ids persistence."""
import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    def _build(self, base, skills=None, kb_articles=None):
        from agents.sre_agent import _build_system_prompt
        return _build_system_prompt(base, skills, kb_articles)

    def test_base_only(self):
        result = self._build("BASE PROMPT")
        assert result == "BASE PROMPT"

    def test_kb_articles_injected(self):
        articles = [
            {"id": "kb-1", "title": "Redis Failover Runbook", "body": "Run redis-cli failover.", "similarity": 0.92},
        ]
        result = self._build("BASE", kb_articles=articles)
        assert "## Relevant Runbooks" in result
        assert "Redis Failover Runbook" in result
        assert "Run redis-cli failover." in result

    def test_kb_similarity_shown(self):
        articles = [{"id": "kb-1", "title": "Runbook", "body": "Steps.", "similarity": 0.85}]
        result = self._build("BASE", kb_articles=articles)
        assert "85%" in result

    def test_multiple_kb_articles(self):
        articles = [
            {"id": "kb-1", "title": "Article A", "body": "Body A.", "similarity": 0.9},
            {"id": "kb-2", "title": "Article B", "body": "Body B.", "similarity": 0.8},
        ]
        result = self._build("BASE", kb_articles=articles)
        assert "Article A" in result
        assert "Article B" in result
        assert "Body A." in result
        assert "Body B." in result

    def test_kb_missing_similarity_no_crash(self):
        articles = [{"id": "kb-1", "title": "Runbook", "body": "Steps."}]
        result = self._build("BASE", kb_articles=articles)
        assert "Runbook" in result

    def test_skills_and_kb_both_present(self, sample_skill):
        articles = [{"id": "kb-1", "title": "Runbook", "body": "Steps.", "similarity": 0.88}]
        result = self._build("BASE", skills=[sample_skill], kb_articles=articles)
        assert "## Learned Skills" in result
        assert "## Relevant Runbooks" in result

    def test_empty_kb_list_no_section(self):
        result = self._build("BASE", kb_articles=[])
        assert "## Relevant Runbooks" not in result

    def test_base_prompt_preserved_exactly(self):
        base = "My specific base prompt."
        result = self._build(base)
        assert result.startswith(base)


# ---------------------------------------------------------------------------
# resolve_incident passes kb_articles through the full call
# ---------------------------------------------------------------------------

class TestResolveIncidentWithKB:
    def _mock_response(self, payload: dict) -> MagicMock:
        """Build a mock OpenAI response."""
        msg = MagicMock()
        msg.content = json.dumps(payload)
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def test_kb_text_appears_in_api_call(self, sample_incident):
        from agents.sre_agent import resolve_incident
        good_response = {
            "root_cause": "cache miss",
            "severity": "P2",
            "remediation_steps": ["step1"],
            "estimated_resolution_minutes": 10,
            "confidence": "high",
        }
        with patch("agents.sre_agent._client") as mock_client:
            mock_client.chat.completions.create.return_value = self._mock_response(good_response)
            kb = [{"id": "kb-99", "title": "Cache Flush Runbook", "body": "Run flush-all.", "similarity": 0.91}]
            resolve_incident(sample_incident, kb_articles=kb)

            call_args = mock_client.chat.completions.create.call_args
            messages = call_args.kwargs["messages"] if call_args.kwargs else call_args[1]["messages"]
            system_msg = next(m for m in messages if m["role"] == "system")
            assert "Cache Flush Runbook" in system_msg["content"]
            assert "Run flush-all." in system_msg["content"]

    def test_no_kb_no_runbook_section(self, sample_incident):
        from agents.sre_agent import resolve_incident
        good_response = {
            "root_cause": "x",
            "severity": "P3",
            "remediation_steps": [],
            "estimated_resolution_minutes": 5,
            "confidence": "low",
        }
        with patch("agents.sre_agent._client") as mock_client:
            mock_client.chat.completions.create.return_value = self._mock_response(good_response)
            resolve_incident(sample_incident, kb_articles=None)
            call_args = mock_client.chat.completions.create.call_args
            messages = call_args.kwargs["messages"] if call_args.kwargs else call_args[1]["messages"]
            system_msg = next(m for m in messages if m["role"] == "system")
            assert "## Relevant Runbooks" not in system_msg["content"]


# ---------------------------------------------------------------------------
# Loop wires retrieve_kbs → resolve_incident → save_resolution(kb_ids)
# ---------------------------------------------------------------------------

class TestLoopRagIntegration:
    def test_retrieved_kb_ids_saved_to_resolution(self, sample_incident, sample_kb_article):
        """Confirm that when retrieve_kbs returns articles, their IDs are saved in the resolution doc."""
        from darwin.loop import DarwinLoop

        good_scores = {
            "composite": 0.85,
            "root_cause_accuracy": 0.9,
            "remediation_quality": 0.8,
            "severity_accuracy": 1.0,
        }
        good_resolution = {
            "root_cause": "x",
            "severity": "P2",
            "remediation_steps": ["a"],
            "estimated_resolution_minutes": 5,
            "confidence": "high",
        }
        saved_docs = []

        with patch("darwin.loop.guard_incident_input") as mock_in, \
             patch("darwin.loop.guard_resolution_output") as mock_out, \
             patch("darwin.loop.retrieve_skills", return_value=[]), \
             patch("darwin.loop.increment_skill_use"), \
             patch("darwin.retrieval.retrieve_kbs", return_value=[sample_kb_article]) as mock_kbs, \
             patch("darwin.loop.resolve_incident", return_value=good_resolution), \
             patch("darwin.loop.score_resolution", return_value=good_scores), \
             patch("darwin.loop.save_resolution", side_effect=lambda *a, **kw: saved_docs.append(kw)) as mock_save, \
             patch("darwin.loop.ensure_agent"), \
             patch("darwin.loop.create_experiment"):

            mock_in.return_value = MagicMock(allowed=True, flagged=False, triggered=[])
            mock_out.return_value = MagicMock(allowed=True, flagged=False, triggered=[])

            loop = DarwinLoop()
            loop.run([sample_incident], register_vijil=False)

        assert mock_kbs.called
        assert saved_docs, "save_resolution was never called"
        assert saved_docs[0].get("retrieved_kb_ids") == [sample_kb_article["id"]]

    def test_loop_calls_retrieve_kbs_per_incident(self, sample_incident):
        """retrieve_kbs must be called once per incident, not once per run."""
        from darwin.loop import DarwinLoop

        good_scores = {
            "composite": 0.85,
            "root_cause_accuracy": 0.9,
            "remediation_quality": 0.8,
            "severity_accuracy": 1.0,
        }
        good_resolution = {
            "root_cause": "x", "severity": "P2",
            "remediation_steps": [], "estimated_resolution_minutes": 5, "confidence": "high",
        }

        with patch("darwin.loop.guard_incident_input") as mock_in, \
             patch("darwin.loop.guard_resolution_output") as mock_out, \
             patch("darwin.loop.retrieve_skills", return_value=[]), \
             patch("darwin.loop.increment_skill_use"), \
             patch("darwin.retrieval.retrieve_kbs", return_value=[]) as mock_kbs, \
             patch("darwin.loop.resolve_incident", return_value=good_resolution), \
             patch("darwin.loop.score_resolution", return_value=good_scores), \
             patch("darwin.loop.save_resolution", return_value="x"), \
             patch("darwin.loop.ensure_agent"):

            mock_in.return_value = MagicMock(allowed=True, flagged=False, triggered=[])
            mock_out.return_value = MagicMock(allowed=True, flagged=False, triggered=[])

            incidents = [dict(sample_incident, id=f"inc-{i}") for i in range(3)]
            loop = DarwinLoop()
            loop.run(incidents, register_vijil=False)

        assert mock_kbs.call_count == 3
