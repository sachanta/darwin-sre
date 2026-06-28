"""Skill library for Darwin SRE.

Darwin writes one targeted Skill per evolution instead of mutating the base
system prompt. Skills are:
  - Focused: one skill per failure family, not a wall of caveats
  - Tagged: matched to incidents by category + edge_case_family
  - Retirable: archived when a failure pattern stops occurring
  - Auditable: each skill has a generation, use_count, and last_used timestamp

The base system prompt stays frozen. Resolution context =
  Base Prompt + Active Skills (injected as "## Learned Skills") + KB Runbooks
"""
from __future__ import annotations
from datetime import datetime, timezone
from pymongo import MongoClient
from config import MONGODB_URI, MONGODB_DB

_client = MongoClient(MONGODB_URI)
_db = _client[MONGODB_DB]
skills_col = _db["skills"]

RETIRE_AFTER_GENERATIONS = 4   # archive a skill not triggered in N consecutive generations
ESCALATION_THRESHOLD    = 3   # create a problem ticket when a skill fires this many times


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def save_skill(skill: dict) -> str:
    existing = skills_col.find_one({"id": skill["id"]})
    if existing:
        skills_col.replace_one({"id": skill["id"]}, skill)
        return skill["id"]
    result = skills_col.insert_one(skill)
    return str(result.inserted_id)


def get_skill(skill_id: str) -> dict | None:
    return skills_col.find_one({"id": skill_id}, {"_id": 0})


def get_active_skills() -> list[dict]:
    return list(skills_col.find({"active": True}, {"_id": 0}).sort("created_by_generation", 1))


def get_all_skills() -> list[dict]:
    return list(skills_col.find({}, {"_id": 0}).sort("created_by_generation", 1))


def increment_skill_use(skill_id: str) -> int:
    """Increment use_count and return the new count."""
    result = skills_col.find_one_and_update(
        {"id": skill_id},
        {"$inc": {"use_count": 1}, "$set": {"last_used": datetime.now(timezone.utc)}},
        return_document=True,
    )
    return result["use_count"] if result else 1


def retire_stale_skills(current_generation: int) -> list[str]:
    """Archive skills that haven't been used in RETIRE_AFTER_GENERATIONS generations."""
    cutoff_gen = current_generation - RETIRE_AFTER_GENERATIONS
    result = skills_col.update_many(
        {"active": True, "last_used_generation": {"$lt": cutoff_gen}},
        {"$set": {"active": False, "retired_at_generation": current_generation}},
    )
    retired = list(skills_col.find(
        {"active": False, "retired_at_generation": current_generation},
        {"_id": 0, "id": 1},
    ))
    return [s["id"] for s in retired]


# ---------------------------------------------------------------------------
# Retrieval — tag-based matching (no extra vector index needed)
# ---------------------------------------------------------------------------

def retrieve_skills(incident: dict) -> list[dict]:
    """Return active skills relevant to this incident.

    Matching priority:
    1. Skills tagged with the incident's edge_case_family (exact family match)
    2. Skills tagged with the incident's category
    3. Skills tagged 'general'
    """
    active = get_active_skills()
    if not active:
        return []

    family = incident.get("edge_case_family")
    category = incident.get("category", "")

    family_match = [s for s in active if family and family in s.get("tags", [])]
    if family_match:
        return family_match

    category_match = [s for s in active if category in s.get("tags", [])]
    general = [s for s in active if "general" in s.get("tags", [])]

    seen = {s["id"] for s in category_match}
    combined = category_match + [s for s in general if s["id"] not in seen]
    return combined[:3]  # cap at 3 skills to avoid context bloat


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def format_skills_for_prompt(skills: list[dict]) -> str:
    """Format active skills as a prompt section injected after the base prompt."""
    if not skills:
        return ""
    lines = ["", "## Learned Skills (accumulated from past incidents)", ""]
    for skill in skills:
        lines.append(f"### Skill: {skill['name']}")
        lines.append(skill["guidance"])
        lines.append("")
    return "\n".join(lines)
