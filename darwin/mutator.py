import json
from openai import OpenAI
from config import DO_API_KEY, DO_BASE_URL, MUTATOR_MODEL

_client = OpenAI(api_key=DO_API_KEY, base_url=DO_BASE_URL)

MUTATOR_SYSTEM = """You are a prompt engineer specializing in AI SRE systems.
Your job: improve a system prompt based on failure patterns from production incidents.
Return ONLY the improved system prompt text — no explanations, no markdown, no JSON wrapper."""

MUTATOR_USER = """The SRE agent's current system prompt is performing poorly on these incidents.

CURRENT SYSTEM PROMPT:
{current_prompt}

FAILURE PATTERNS (incidents the agent got wrong):
{failures}

INSTRUCTIONS:
- Identify what types of incidents the agent is failing on
- Add specific guidance to handle those patterns
- Keep all existing capabilities intact
- Do NOT overfit to specific incident details — extract general principles
- Keep the prompt under 800 words

Return the complete improved system prompt:"""


def mutate_prompt(current_prompt: str, failed_incidents: list[dict]) -> tuple[str, str]:
    failure_summary = []
    for item in failed_incidents[:8]:
        failure_summary.append({
            "incident_title": item["incident"]["title"],
            "incident_category": item["incident"]["category"],
            "is_edge_case": item["incident"].get("is_edge_case", False),
            "agent_root_cause": item["resolution"].get("root_cause", ""),
            "correct_root_cause": item["incident"]["ground_truth"]["root_cause"],
            "score": item["scores"]["composite"],
            "judge_reasoning": item["scores"].get("reasoning", ""),
        })

    response = _client.chat.completions.create(
        model=MUTATOR_MODEL,
        messages=[
            {"role": "system", "content": MUTATOR_SYSTEM},
            {"role": "user", "content": MUTATOR_USER.format(
                current_prompt=current_prompt,
                failures=json.dumps(failure_summary, indent=2),
            )},
        ],
        max_completion_tokens=1200,
    )

    new_prompt = response.choices[0].message.content.strip()
    diff = _build_diff(current_prompt, new_prompt)
    return new_prompt, diff


def _build_diff(old: str, new: str) -> str:
    old_lines = set(old.splitlines())
    new_lines = set(new.splitlines())
    added = [f"+ {l}" for l in new_lines - old_lines if l.strip()]
    removed = [f"- {l}" for l in old_lines - new_lines if l.strip()]
    return "\n".join(removed[:5] + added[:5])
