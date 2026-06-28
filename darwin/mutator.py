"""Darwin skill generator.

Instead of mutating the base system prompt (which causes bloat), Darwin writes
a focused Skill that the SRE agent loads situationally for matching incidents.

Returns a skill dict ready to be saved and injected into future resolutions.
"""
import json
import uuid
from datetime import datetime, timezone
from openai import OpenAI
from config import DO_API_KEY, DO_BASE_URL, MUTATOR_MODEL

_client = OpenAI(api_key=DO_API_KEY, base_url=DO_BASE_URL)

SKILL_WRITER_SYSTEM = """You are a senior SRE knowledge engineer.
Your job: given a set of incidents an AI SRE agent failed to resolve correctly,
write a concise, reusable Skill the agent can load to handle this class of incidents better.

A Skill is a short, focused piece of operational guidance — NOT a rewrite of the agent's
entire personality. Think of it as a specialist reflex: "when you see X, do Y first."

Return ONLY valid JSON with this exact structure:
{
  "name": "short skill name (5-10 words)",
  "guidance": "2-4 sentences of precise, actionable guidance for this failure pattern",
  "tags": ["failure_family_id", "category", ...up to 4 tags]
}"""

SKILL_WRITER_USER = """The SRE agent failed on these incidents. Write a Skill to handle this pattern.

FAILURE PATTERN SUMMARY:
{failures}

The skill should:
- Address the SPECIFIC class of failure shown (not generic advice)
- Describe what signal to look for and what action to take first
- Be precise enough that an agent could apply it to a new unseen incident of the same class
- Include the failure family ID in the tags (e.g. "CCF-1")

Return ONLY the JSON skill object:"""


def generate_skill(failed_incidents: list[dict], generation: int) -> tuple[dict, str]:
    """Generate a new Skill from a set of failed incidents.

    Returns (skill_dict, description_for_genome) where skill_dict is ready to save.
    """
    failure_summary = []
    family_ids = set()
    categories = set()

    for item in failed_incidents[:8]:
        inc = item["incident"]
        family_ids.add(inc.get("edge_case_family", "unknown"))
        categories.add(inc.get("category", "general"))
        failure_summary.append({
            "incident_title": inc["title"],
            "incident_category": inc["category"],
            "edge_case_family": inc.get("edge_case_family"),
            "agent_root_cause": item["resolution"].get("root_cause", ""),
            "correct_root_cause": inc["ground_truth"]["root_cause"],
            "score": item["scores"]["composite"],
            "judge_reasoning": item["scores"].get("reasoning", ""),
        })

    response = _client.chat.completions.create(
        model=MUTATOR_MODEL,
        messages=[
            {"role": "system", "content": SKILL_WRITER_SYSTEM},
            {"role": "user", "content": SKILL_WRITER_USER.format(
                failures=json.dumps(failure_summary, indent=2),
            )},
        ],
        max_completion_tokens=400,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    parsed = json.loads(raw)

    # Merge any auto-detected tags with known families/categories
    tags = list(set(parsed.get("tags", []) + list(family_ids) + list(categories)))
    tags = [t for t in tags if t and t != "unknown"]

    skill = {
        "id": f"skill_{generation:03d}_{uuid.uuid4().hex[:6]}",
        "name": parsed["name"],
        "guidance": parsed["guidance"],
        "tags": tags,
        "created_by_generation": generation,
        "use_count": 0,
        "last_used": None,
        "last_used_generation": generation,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    description = (
        f"Gen-{generation} skill '{skill['name']}' "
        f"targeting {', '.join(sorted(family_ids))} | tags: {tags}"
    )

    return skill, description


KB_WRITER_SYSTEM = """You are a senior SRE knowledge engineer writing a runbook for an AI agent knowledge base.
Your job: given incidents that an AI SRE agent failed to handle, write a concise runbook article
so the agent can recognize and resolve this class of incidents in the future.

Return ONLY valid JSON:
{
  "title": "Runbook: <short descriptive title>",
  "body": "3-6 sentences. Describe: (1) what this failure pattern looks like, (2) the most common root cause, (3) the first 2-3 diagnostic steps, (4) the resolution action.",
  "tags": ["failure_family_id", "category", ...up to 4 tags],
  "service": "the impacted service or 'general'"
}"""

KB_WRITER_USER = """Write a runbook for this failure pattern. The agent previously had no runbook for it.

FAILURE PATTERN:
{failures}

Return ONLY the JSON runbook:"""


def generate_kb_article(failed_incidents: list[dict], generation: int, skill_name: str) -> dict:
    """Author a new KB runbook article from failed incidents.

    Darwin writes this alongside the Skill so that (a) the skill is the quick
    agent reflex, and (b) the KB article is the detailed runbook retrieved
    by the RAG pipeline in future resolutions.
    Returns a KB article dict (no embedding — retrieval.index_kb_article adds that).
    """
    failure_summary = []
    family_ids = set()
    categories = set()

    for item in failed_incidents[:6]:
        inc = item["incident"]
        family_ids.add(inc.get("edge_case_family", "unknown"))
        categories.add(inc.get("category", "general"))
        failure_summary.append({
            "title": inc["title"],
            "category": inc["category"],
            "edge_case_family": inc.get("edge_case_family"),
            "correct_root_cause": inc["ground_truth"]["root_cause"],
            "correct_remediation": inc["ground_truth"]["remediation_steps"],
        })

    response = _client.chat.completions.create(
        model=MUTATOR_MODEL,
        messages=[
            {"role": "system", "content": KB_WRITER_SYSTEM},
            {"role": "user", "content": KB_WRITER_USER.format(
                failures=json.dumps(failure_summary, indent=2),
            )},
        ],
        max_completion_tokens=500,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    parsed = json.loads(raw)
    tags = list(set(parsed.get("tags", []) + list(family_ids) + list(categories)))
    tags = [t for t in tags if t and t != "unknown"]

    families_str = "_".join(sorted(family_ids))
    return {
        "id": f"kb_darwin_{generation:03d}_{uuid.uuid4().hex[:6]}",
        "title": parsed["title"],
        "body": parsed["body"],
        "service": parsed.get("service", "general"),
        "tags": tags,
        "source": "darwin",
        "created_by_generation": generation,
        "related_skill": skill_name,
    }


# Keep a shim so any old callers referencing mutate_prompt don't crash at import
def mutate_prompt(current_prompt: str, failed_incidents: list[dict]) -> tuple[str, str]:
    """Deprecated shim — use generate_skill instead."""
    skill, desc = generate_skill(failed_incidents, generation=0)
    return current_prompt, f"[skill written] {desc}"
