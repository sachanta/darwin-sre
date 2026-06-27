from datetime import datetime, timezone
from pymongo import MongoClient
from config import MONGODB_URI, MONGODB_DB

_client = MongoClient(MONGODB_URI)
_db = _client[MONGODB_DB]

resolutions = _db["resolutions"]
generations = _db["generations"]
incidents_col = _db["incidents"]


def save_resolution(incident: dict, resolution: dict, scores: dict, generation: int) -> str:
    doc = {
        "incident_id": incident["id"],
        "generation": generation,
        "resolution": resolution,
        "scores": scores,
        "timestamp": datetime.now(timezone.utc),
    }
    result = resolutions.insert_one(doc)
    return str(result.inserted_id)


def save_generation(
    generation_id: int,
    system_prompt: str,
    prompt_diff: str,
    score_before: float,
    score_after: float,
    failed_incident_ids: list[str],
    failure_patterns: list[str],
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
        "timestamp": datetime.now(timezone.utc),
    }
    result = generations.insert_one(doc)
    return str(result.inserted_id)


def get_all_generations() -> list[dict]:
    docs = list(generations.find({}, {"_id": 0}).sort("generation_id", 1))
    return docs


def get_recent_resolutions(limit: int = 20) -> list[dict]:
    docs = list(resolutions.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
    return docs


def seed_incidents(incident_list: list[dict]) -> None:
    incidents_col.drop()
    incidents_col.insert_many(incident_list)
