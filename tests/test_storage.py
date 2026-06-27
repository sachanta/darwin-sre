"""Phase B tests — storage layer with mongomock."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Unit tests using mongomock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db(monkeypatch):
    """Replace pymongo client with mongomock."""
    import mongomock
    fake_client = mongomock.MongoClient()
    fake_db = fake_client["darwin_sre"]

    import darwin.storage as storage
    monkeypatch.setattr(storage, "_client", fake_client)
    monkeypatch.setattr(storage, "_db", fake_db)
    monkeypatch.setattr(storage, "resolutions", fake_db["resolutions"])
    monkeypatch.setattr(storage, "generations", fake_db["generations"])
    monkeypatch.setattr(storage, "incidents_col", fake_db["incidents"])
    monkeypatch.setattr(storage, "logs_col", fake_db["logs"])
    monkeypatch.setattr(storage, "knowledge_articles", fake_db["knowledge_articles"])
    monkeypatch.setattr(storage, "alerts_col", fake_db["alerts"])
    monkeypatch.setattr(storage, "runs_col", fake_db["runs"])
    return fake_db


class TestResolutionStorage:
    def test_save_resolution_returns_id(self, mock_db, sample_normal_incident, sample_resolution):
        from darwin.storage import save_resolution
        rid = save_resolution(sample_normal_incident, sample_resolution, {"composite": 0.8}, 0)
        assert rid

    def test_save_resolution_stores_fields(self, mock_db, sample_normal_incident, sample_resolution):
        from darwin.storage import save_resolution
        save_resolution(sample_normal_incident, sample_resolution, {"composite": 0.75}, 1,
                        retrieved_kb_ids=["kb_001"], run_id="run_001")
        doc = mock_db["resolutions"].find_one({"incident_id": sample_normal_incident["id"]})
        assert doc["generation"] == 1
        assert doc["retrieved_kb_ids"] == ["kb_001"]
        assert doc["run_id"] == "run_001"
        assert doc["scores"]["composite"] == 0.75

    def test_get_recent_resolutions(self, mock_db, sample_normal_incident, sample_resolution):
        from darwin.storage import save_resolution, get_recent_resolutions
        save_resolution(sample_normal_incident, sample_resolution, {"composite": 0.7}, 0)
        results = get_recent_resolutions(limit=10)
        assert len(results) == 1

    def test_get_recent_resolutions_filtered_by_run(self, mock_db, sample_normal_incident, sample_resolution):
        from darwin.storage import save_resolution, get_recent_resolutions
        save_resolution(sample_normal_incident, sample_resolution, {"composite": 0.7}, 0, run_id="run_A")
        inc2 = {**sample_normal_incident, "id": "train_002"}
        save_resolution(inc2, sample_resolution, {"composite": 0.8}, 0, run_id="run_B")
        assert len(get_recent_resolutions(run_id="run_A")) == 1
        assert len(get_recent_resolutions(run_id="run_B")) == 1


class TestLogStorage:
    def test_save_and_get_log(self, mock_db, sample_log):
        from darwin.storage import save_log, get_log
        save_log(sample_log)
        retrieved = get_log(sample_log["id"])
        assert retrieved["incident_id"] == sample_log["incident_id"]
        assert retrieved["lines"] == sample_log["lines"]

    def test_save_log_is_idempotent(self, mock_db, sample_log):
        from darwin.storage import save_log, get_log
        save_log(sample_log)
        updated = {**sample_log, "summary": "updated summary"}
        save_log(updated)
        retrieved = get_log(sample_log["id"])
        assert retrieved["summary"] == "updated summary"
        assert mock_db["logs"].count_documents({}) == 1

    def test_get_log_missing_returns_none(self, mock_db):
        from darwin.storage import get_log
        assert get_log("nonexistent") is None

    def test_seed_logs_replaces_existing(self, mock_db, sample_log):
        from darwin.storage import seed_logs, get_log
        seed_logs([sample_log])
        seed_logs([{**sample_log, "id": "log_other", "incident_id": "other"}])
        assert mock_db["logs"].count_documents({}) == 1


class TestKBStorage:
    def test_save_and_get_kb_article(self, mock_db, sample_kb_article):
        from darwin.storage import save_kb_article, get_kb_article
        save_kb_article(sample_kb_article)
        retrieved = get_kb_article(sample_kb_article["id"])
        assert retrieved["title"] == sample_kb_article["title"]
        assert retrieved["source"] == "seed"

    def test_save_kb_article_idempotent(self, mock_db, sample_kb_article):
        from darwin.storage import save_kb_article, get_kb_article
        save_kb_article(sample_kb_article)
        updated = {**sample_kb_article, "body": "updated body"}
        save_kb_article(updated)
        assert mock_db["knowledge_articles"].count_documents({}) == 1
        assert get_kb_article(sample_kb_article["id"])["body"] == "updated body"

    def test_seed_knowledge_base_replaces_seed_articles(self, mock_db, sample_kb_article):
        from darwin.storage import seed_knowledge_base, get_all_kb_articles
        seed_knowledge_base([sample_kb_article])
        seed_knowledge_base([{**sample_kb_article, "id": "kb_002", "title": "New article"}])
        articles = get_all_kb_articles()
        assert len(articles) == 1
        assert articles[0]["id"] == "kb_002"

    def test_darwin_kb_not_deleted_by_seed(self, mock_db, sample_kb_article):
        from darwin.storage import save_kb_article, seed_knowledge_base, get_all_kb_articles
        darwin_article = {**sample_kb_article, "id": "kb_darwin_001", "source": "darwin",
                          "created_by_generation": 1}
        save_kb_article(darwin_article)
        seed_knowledge_base([sample_kb_article])
        articles = get_all_kb_articles()
        ids = {a["id"] for a in articles}
        assert "kb_darwin_001" in ids  # darwin article preserved


class TestGenerationStorage:
    def test_save_generation_stores_all_fields(self, mock_db):
        from darwin.storage import save_generation, get_all_generations
        save_generation(
            generation_id=1,
            system_prompt="new prompt",
            prompt_diff="+ added cascade handling",
            score_before=0.45,
            score_after=0.78,
            failed_incident_ids=["prod_031", "prod_032"],
            failure_patterns=["CCF-1"],
            new_kb_article_id="kb_darwin_001",
            run_id="run_001",
        )
        gens = get_all_generations()
        assert len(gens) == 1
        g = gens[0]
        assert g["generation_id"] == 1
        assert g["new_kb_article_id"] == "kb_darwin_001"
        assert g["run_id"] == "run_001"
        assert g["score_after"] > g["score_before"]

    def test_get_generations_ordered(self, mock_db):
        from darwin.storage import save_generation, get_all_generations
        for i in [3, 1, 2]:
            save_generation(i, f"p{i}", f"diff{i}", 0.4, 0.7, [], [], run_id="r")
        gens = get_all_generations(run_id="r")
        ids = [g["generation_id"] for g in gens]
        assert ids == sorted(ids)


class TestAlertStorage:
    def test_save_alert_defaults_to_open(self, mock_db):
        from darwin.storage import save_alert, get_open_alert
        aid = save_alert(0.45, [0.4, 0.5, 0.4, 0.45, 0.5], ["prod_031"], 1, run_id="r")
        alert = get_open_alert(run_id="r")
        assert alert is not None
        assert alert["status"] == "open"

    def test_alert_state_machine(self, mock_db):
        from darwin.storage import save_alert, update_alert_status, get_open_alert
        aid = save_alert(0.45, [0.4], ["prod_031"], 1)
        update_alert_status(aid, "improving")
        assert get_open_alert() is None  # no longer "open"
        update_alert_status(aid, "resolved")

    def test_resolved_alert_has_timestamp(self, mock_db):
        from darwin.storage import save_alert, update_alert_status
        from bson import ObjectId
        aid = save_alert(0.45, [0.4], ["prod_031"], 1)
        update_alert_status(aid, "resolved")
        from darwin.storage import alerts_col
        doc = alerts_col.find_one({"_id": ObjectId(aid)})
        assert doc["resolved_at"] is not None
        assert doc["status"] == "resolved"


class TestRunStorage:
    def test_create_and_finish_run(self, mock_db):
        from darwin.storage import create_run, finish_run, get_run
        create_run("run_001")
        run = get_run("run_001")
        assert run["status"] == "running"
        finish_run("run_001", num_generations=8, baseline_avg=0.45,
                   final_avg=0.82, episode_order=["prod_031", "prod_032"])
        run = get_run("run_001")
        assert run["status"] == "complete"
        assert run["num_generations"] == 8
        assert run["final_avg"] == 0.82

    def test_list_runs(self, mock_db):
        from darwin.storage import create_run, list_runs
        create_run("run_A")
        create_run("run_B")
        runs = list_runs()
        assert len(runs) == 2


# ---------------------------------------------------------------------------
# Integration test — hits real Atlas
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestStorageIntegration:
    def test_roundtrip_kb_article_real_db(self, sample_kb_article):
        from darwin.storage import save_kb_article, get_kb_article
        test_article = {**sample_kb_article, "id": "kb_integration_test"}
        save_kb_article(test_article)
        retrieved = get_kb_article("kb_integration_test")
        assert retrieved is not None
        assert retrieved["title"] == test_article["title"]
        # cleanup
        from darwin.storage import knowledge_articles
        knowledge_articles.delete_one({"id": "kb_integration_test"})
