"""Phase A tests — synthetic data schema + structural invariants.

Unit tests (no LLM calls, no network):
  - Schema completeness on fixture data
  - 8 corner-case families present in generated production data
  - Corner cases have no seed KB refs (failure premise)
  - Counts are within acceptable ranges

Integration tests (@pytest.mark.integration):
  - Actually run generate_incidents.py and verify outputs
"""
import json
import pytest
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

REQUIRED_INCIDENT_FIELDS = {
    "id", "title", "description", "service", "environment",
    "category", "is_edge_case", "edge_case_family", "log_id",
    "kb_refs", "metrics", "ground_truth",
}
REQUIRED_GROUND_TRUTH_FIELDS = {"root_cause", "severity", "remediation_steps"}
VALID_SEVERITIES = {"P1", "P2", "P3"}
VALID_CATEGORIES = {"database", "performance", "network", "storage", "service", "configuration"}
EXPECTED_FAMILIES = {"CCF-1", "CCF-2", "CCF-3", "CCF-4", "CCF-5", "CCF-6", "CCF-7", "CCF-8"}
EXPECTED_LOG_FIELDS = {"id", "incident_id", "lines", "summary"}
EXPECTED_KB_FIELDS = {"id", "title", "body", "service", "tags", "source"}


# ---------------------------------------------------------------------------
# Unit tests on fixture data (fast, no I/O)
# ---------------------------------------------------------------------------

class TestIncidentSchema:
    def test_normal_incident_has_required_fields(self, sample_normal_incident):
        missing = REQUIRED_INCIDENT_FIELDS - set(sample_normal_incident.keys())
        assert not missing, f"Missing fields: {missing}"

    def test_normal_incident_ground_truth_fields(self, sample_normal_incident):
        gt = sample_normal_incident["ground_truth"]
        missing = REQUIRED_GROUND_TRUTH_FIELDS - set(gt.keys())
        assert not missing, f"Missing ground_truth fields: {missing}"

    def test_normal_incident_severity_valid(self, sample_normal_incident):
        assert sample_normal_incident["ground_truth"]["severity"] in VALID_SEVERITIES

    def test_normal_incident_remediation_is_list(self, sample_normal_incident):
        steps = sample_normal_incident["ground_truth"]["remediation_steps"]
        assert isinstance(steps, list) and len(steps) >= 1

    def test_normal_incident_has_log_id(self, sample_normal_incident):
        assert sample_normal_incident["log_id"].startswith("log_")

    def test_normal_incident_has_kb_refs(self, sample_normal_incident):
        assert isinstance(sample_normal_incident["kb_refs"], list)

    def test_edge_case_has_no_kb_refs(self, sample_edge_case_incident):
        assert sample_edge_case_incident["kb_refs"] == [], "Corner cases must have empty kb_refs"

    def test_edge_case_has_family_id(self, sample_edge_case_incident):
        assert sample_edge_case_incident["edge_case_family"] in EXPECTED_FAMILIES

    def test_edge_case_flag_set(self, sample_edge_case_incident):
        assert sample_edge_case_incident["is_edge_case"] is True


class TestLogSchema:
    def test_log_has_required_fields(self, sample_log):
        missing = EXPECTED_LOG_FIELDS - set(sample_log.keys())
        assert not missing, f"Missing log fields: {missing}"

    def test_log_lines_are_list(self, sample_log):
        assert isinstance(sample_log["lines"], list)

    def test_log_lines_have_ts_level_msg(self, sample_log):
        for line in sample_log["lines"]:
            assert "ts" in line
            assert "level" in line
            assert "msg" in line

    def test_log_id_matches_incident(self, sample_log, sample_normal_incident):
        assert sample_log["incident_id"] == sample_normal_incident["id"]
        assert sample_log["id"] == sample_normal_incident["log_id"]


class TestKBSchema:
    def test_kb_has_required_fields(self, sample_kb_article):
        missing = EXPECTED_KB_FIELDS - set(sample_kb_article.keys())
        assert not missing

    def test_kb_source_is_seed(self, sample_kb_article):
        assert sample_kb_article["source"] == "seed"

    def test_kb_no_generation_for_seed(self, sample_kb_article):
        assert sample_kb_article["created_by_generation"] is None

    def test_kb_tags_is_list(self, sample_kb_article):
        assert isinstance(sample_kb_article["tags"], list) and len(sample_kb_article["tags"]) >= 1


# ---------------------------------------------------------------------------
# Integration tests — run against real generated JSON files
# ---------------------------------------------------------------------------

def _load_if_exists(filename: str) -> list | None:
    path = DATA_DIR / filename
    if not path.exists():
        return None
    return json.loads(path.read_text())


@pytest.mark.integration
class TestGeneratedData:
    """Require actual data files to be present (run generate_incidents.py first)."""

    def test_training_file_exists(self):
        assert (DATA_DIR / "incidents_training.json").exists(), \
            "Run: poetry run python data/generate_incidents.py"

    def test_production_file_exists(self):
        assert (DATA_DIR / "incidents_production.json").exists()

    def test_logs_file_exists(self):
        assert (DATA_DIR / "logs.json").exists()

    def test_kb_file_exists(self):
        assert (DATA_DIR / "knowledge_base.json").exists()

    def test_training_count(self, loaded_training_data):
        assert len(loaded_training_data) >= 40, f"Expected ~50 training incidents, got {len(loaded_training_data)}"

    def test_production_count(self, loaded_production_data):
        assert len(loaded_production_data) >= 45, f"Expected ~50 production incidents, got {len(loaded_production_data)}"

    def test_all_training_have_required_fields(self, loaded_training_data):
        for inc in loaded_training_data:
            missing = REQUIRED_INCIDENT_FIELDS - set(inc.keys())
            assert not missing, f"{inc.get('id')}: missing {missing}"

    def test_all_production_have_required_fields(self, loaded_production_data):
        for inc in loaded_production_data:
            missing = REQUIRED_INCIDENT_FIELDS - set(inc.keys())
            assert not missing, f"{inc.get('id')}: missing {missing}"

    def test_all_incidents_have_log_id(self, loaded_training_data, loaded_production_data):
        for inc in loaded_training_data + loaded_production_data:
            assert inc.get("log_id"), f"{inc['id']} missing log_id"

    def test_exactly_8_corner_case_families(self, loaded_production_data):
        families = {inc["edge_case_family"] for inc in loaded_production_data if inc.get("is_edge_case")}
        assert families == EXPECTED_FAMILIES, f"Got families: {families}"

    def test_corner_cases_have_no_kb_refs(self, loaded_production_data):
        for inc in loaded_production_data:
            if inc.get("is_edge_case"):
                assert inc.get("kb_refs") == [], \
                    f"{inc['id']} (family {inc.get('edge_case_family')}) has kb_refs: {inc.get('kb_refs')}"

    def test_normal_incidents_have_no_embeddings_yet(self, loaded_kb):
        for art in loaded_kb:
            assert "embedding" not in art, f"{art['id']} has embedding — should be added in Phase B"

    def test_kb_source_all_seed(self, loaded_kb):
        for art in loaded_kb:
            assert art["source"] == "seed", f"{art['id']} source={art['source']}"

    def test_logs_cover_all_incidents(self, loaded_training_data, loaded_production_data, loaded_logs):
        incident_ids = {inc["id"] for inc in loaded_training_data + loaded_production_data}
        log_incident_ids = {log["incident_id"] for log in loaded_logs}
        uncovered = incident_ids - log_incident_ids
        assert not uncovered, f"Incidents missing logs: {uncovered}"

    def test_log_ids_match_incident_log_id_field(self, loaded_training_data, loaded_production_data, loaded_logs):
        logs_by_incident = {log["incident_id"]: log["id"] for log in loaded_logs}
        for inc in loaded_training_data + loaded_production_data:
            expected_log_id = logs_by_incident.get(inc["id"])
            assert inc.get("log_id") == expected_log_id, \
                f"{inc['id']}: log_id={inc.get('log_id')} but log has id={expected_log_id}"

    def test_kb_article_count(self, loaded_kb):
        assert len(loaded_kb) >= 15, f"Expected ~25 KB articles, got {len(loaded_kb)}"

    def test_all_severities_valid(self, loaded_training_data, loaded_production_data):
        for inc in loaded_training_data + loaded_production_data:
            sev = inc["ground_truth"].get("severity")
            assert sev in VALID_SEVERITIES, f"{inc['id']} has invalid severity: {sev}"

    def test_remediation_steps_non_empty(self, loaded_training_data, loaded_production_data):
        for inc in loaded_training_data + loaded_production_data:
            steps = inc["ground_truth"].get("remediation_steps", [])
            assert len(steps) >= 1, f"{inc['id']} has no remediation steps"
