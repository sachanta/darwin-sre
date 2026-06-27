from datetime import datetime, timezone
from pymongo import MongoClient
from config import MONGODB_URI, MONGODB_DB

_client = MongoClient(MONGODB_URI)
_db = _client[MONGODB_DB]

resolutions = _db["resolutions"]
generations = _db["generations"]
incidents_col = _db["incidents"]
logs_col = _db["logs"]
knowledge_articles = _db["knowledge_articles"]
alerts_col = _db["alerts"]
runs_col = _db["runs"]


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

def seed_incidents(incident_list: list[dict]) -> None:
    incidents_col.drop()
    incidents_col.insert_many(incident_list)


def get_incident(incident_id: str) -> dict | None:
    return incidents_col.find_one({"id": incident_id}, {"_id": 0})


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def save_log(log: dict) -> str:
    existing = logs_col.find_one({"id": log["id"]})
    if existing:
        logs_col.replace_one({"id": log["id"]}, log)
        return log["id"]
    result = logs_col.insert_one(log)
    return str(result.inserted_id)


def get_log(log_id: str) -> dict | None:
    return logs_col.find_one({"id": log_id}, {"_id": 0})


def seed_logs(log_list: list[dict]) -> None:
    logs_col.drop()
    if log_list:
        logs_col.insert_many(log_list)


# ---------------------------------------------------------------------------
# Knowledge articles
# ---------------------------------------------------------------------------

def save_kb_article(article: dict) -> str:
    existing = knowledge_articles.find_one({"id": article["id"]})
    if existing:
        knowledge_articles.replace_one({"id": article["id"]}, article)
        return article["id"]
    result = knowledge_articles.insert_one(article)
    return str(result.inserted_id)


def get_kb_article(article_id: str) -> dict | None:
    return knowledge_articles.find_one({"id": article_id}, {"_id": 0})


def get_all_kb_articles() -> list[dict]:
    return list(knowledge_articles.find({}, {"_id": 0}))


def seed_knowledge_base(articles: list[dict]) -> None:
    """Load seed KB articles into the collection (replaces existing seed articles)."""
    knowledge_articles.delete_many({"source": "seed"})
    if articles:
        knowledge_articles.insert_many(articles)


# ---------------------------------------------------------------------------
# Resolutions
# ---------------------------------------------------------------------------

def save_resolution(
    incident: dict,
    resolution: dict,
    scores: dict,
    generation: int,
    retrieved_kb_ids: list[str] | None = None,
    run_id: str | None = None,
) -> str:
    doc = {
        "incident_id": incident["id"],
        "generation": generation,
        "resolution": resolution,
        "scores": scores,
        "retrieved_kb_ids": retrieved_kb_ids or [],
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc),
    }
    result = resolutions.insert_one(doc)
    return str(result.inserted_id)


def get_resolution(incident_id: str) -> dict | None:
    return resolutions.find_one(
        {"incident_id": incident_id},
        {"_id": 0},
        sort=[("timestamp", -1)],
    )


def get_recent_resolutions(limit: int = 20, run_id: str | None = None) -> list[dict]:
    query = {"run_id": run_id} if run_id else {}
    return list(resolutions.find(query, {"_id": 0}).sort("timestamp", -1).limit(limit))


# ---------------------------------------------------------------------------
# Generations (Darwin evolution lineage)
# ---------------------------------------------------------------------------

def save_generation(
    generation_id: int,
    system_prompt: str,
    prompt_diff: str,
    score_before: float,
    score_after: float,
    failed_incident_ids: list[str],
    failure_patterns: list[str],
    new_kb_article_id: str | None = None,
    run_id: str | None = None,
) -> str:
    doc = {
        "generation_id": generation_id,
        "trigger": "score_degradation",
        "system_prompt": system_prompt,
        "prompt_diff": prompt_diff,
        "score_before": score_before,
        "score_after": score_after,
        "failed_incident_ids": failed_incident_ids,
        "failure_patterns": failure_patterns,
        "new_kb_article_id": new_kb_article_id,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc),
    }
    result = generations.insert_one(doc)
    return str(result.inserted_id)


def get_all_generations(run_id: str | None = None) -> list[dict]:
    query = {"run_id": run_id} if run_id else {}
    return list(generations.find(query, {"_id": 0}).sort("generation_id", 1))


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def save_alert(
    rolling_avg: float,
    window_scores: list[float],
    failing_incident_ids: list[str],
    generation: int,
    run_id: str | None = None,
) -> str:
    doc = {
        "raised_at": datetime.now(timezone.utc),
        "rolling_avg": rolling_avg,
        "window_scores": window_scores,
        "failing_incident_ids": failing_incident_ids,
        "generation": generation,
        "status": "open",
        "resolved_at": None,
        "run_id": run_id,
    }
    result = alerts_col.insert_one(doc)
    return str(result.inserted_id)


def update_alert_status(alert_id: str, status: str) -> None:
    """Transition alert: open → improving → resolved."""
    update = {"status": status}
    if status == "resolved":
        update["resolved_at"] = datetime.now(timezone.utc)
    alerts_col.update_one({"_id": _oid(alert_id)}, {"$set": update})


def get_open_alert(run_id: str | None = None) -> dict | None:
    query = {"status": "open"}
    if run_id:
        query["run_id"] = run_id
    doc = alerts_col.find_one(query, sort=[("raised_at", -1)])
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def get_all_alerts(run_id: str | None = None) -> list[dict]:
    query = {"run_id": run_id} if run_id else {}
    docs = list(alerts_col.find(query, sort=[("raised_at", 1)]))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs


# ---------------------------------------------------------------------------
# Runs (session capture for replay)
# ---------------------------------------------------------------------------

def create_run(run_id: str) -> str:
    doc = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "num_generations": 0,
        "baseline_avg": None,
        "final_avg": None,
        "episode_order": [],
        "status": "running",
    }
    runs_col.insert_one(doc)
    return run_id


def finish_run(
    run_id: str,
    num_generations: int,
    baseline_avg: float,
    final_avg: float,
    episode_order: list[str],
) -> None:
    runs_col.update_one(
        {"run_id": run_id},
        {"$set": {
            "finished_at": datetime.now(timezone.utc),
            "num_generations": num_generations,
            "baseline_avg": baseline_avg,
            "final_avg": final_avg,
            "episode_order": episode_order,
            "status": "complete",
        }},
    )


def get_run(run_id: str) -> dict | None:
    doc = runs_col.find_one({"run_id": run_id}, {"_id": 0})
    return doc


def list_runs() -> list[dict]:
    return list(runs_col.find({}, {"_id": 0}).sort("started_at", -1))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _oid(id_str: str):
    from bson import ObjectId
    return ObjectId(id_str)
