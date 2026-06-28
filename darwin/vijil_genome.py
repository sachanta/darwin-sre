"""Vijil genome tracker for Darwin SRE.

Registers Darwin SRE as a Vijil agent (once), then after each Darwin
generation extracts the current genome so Vijil tracks the mutation lineage.

Uses vijil-console REST API directly (same as vijil CLI).
"""
from __future__ import annotations
import json
import subprocess
import shutil
from pathlib import Path

_STATE_FILE = Path(__file__).parent.parent / ".vijil_agent_state.json"

# Two-agent model in Vijil:
#   SRE Agent (worker)   — the agent being improved; its genome lineage tracks
#                          the accumulating skill library across generations.
#   Darwin Agent (optim) — the optimizer; delegates to / improves the SRE Agent.
# Genome snapshots are taken on the SRE AGENT (the thing whose state evolves).
_SRE_AGENT_NAME = "SRE Agent (worker)"
_DARWIN_AGENT_NAME = "Darwin Agent (optimizer)"
_SRE_MODEL = "anthropic-claude-4.5-sonnet"
_DARWIN_MODEL = "anthropic-claude-haiku-4.5"


def _vijil(*args: str) -> dict | list | None:
    """Run a vijil CLI command with --json, return parsed output or None on error."""
    vijil = shutil.which("vijil")
    if not vijil:
        return None
    cmd = [vijil] + list(args) + ["--json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _load_state() -> dict:
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text())
    return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def ensure_agent(system_prompt: str) -> str | None:
    """Ensure both Vijil agents exist: the SRE Agent (worker) and the Darwin
    Agent (optimizer) that delegates to it.

    The returned agent_id is the SRE AGENT — it is the one whose genome lineage
    we snapshot, because its skill library is what evolves across generations.
    """
    state = _load_state()
    if "agent_id" in state:
        return state["agent_id"]

    # 1. SRE Agent (worker) — the agent being improved
    sre = _vijil(
        "agent", "create",
        "--agent-name", _SRE_AGENT_NAME,
        "--model-name", _SRE_MODEL,
        "--agent-system-prompt", system_prompt,
        "--hub", "custom",
        "--protocol", "chat_completions",
        "--deployment", "local",
    )
    if not sre or "id" not in sre:
        return None
    sre_id = sre["id"]

    # 2. Darwin Agent (optimizer) — improves the SRE Agent
    darwin = _vijil(
        "agent", "create",
        "--agent-name", _DARWIN_AGENT_NAME,
        "--model-name", _DARWIN_MODEL,
        "--agent-system-prompt",
        "You are the Darwin optimizer. You detect degradation in the SRE Agent's "
        "evaluation scores via Arize telemetry, analyze the failing incident family, "
        "and author a targeted Skill to improve the SRE Agent. You do not resolve "
        "incidents yourself; you improve the agent that does.",
        "--hub", "custom",
        "--protocol", "chat_completions",
        "--deployment", "local",
    )
    darwin_id = darwin.get("id") if darwin else None

    # 3. Link: Darwin delegates to (improves) the SRE Agent
    if darwin_id:
        _vijil(
            "agent", "update", darwin_id,
            "--delegated-agents",
            json.dumps([{
                "agent_id": sre_id,
                "name": _SRE_AGENT_NAME,
                "url": "https://inference.do-ai.run/v1/",
            }]),
        )

    # The genome lineage is tracked on the SRE AGENT (the evolving worker)
    state["agent_id"] = sre_id
    state["sre_agent_id"] = sre_id
    state["darwin_agent_id"] = darwin_id
    _save_state(state)
    print(f"  ✅ Vijil SRE Agent (worker): {sre_id}")
    print(f"  ✅ Vijil Darwin Agent (optimizer): {darwin_id} → delegates to SRE Agent")
    return sre_id


def extract_genome(agent_id: str) -> str | None:
    """Extract the current genome from the Darwin SRE agent.

    Called after each Darwin generation to create a genome snapshot.
    Returns genome_id or None.
    """
    data = _vijil("genome", "extract", agent_id)
    if not data or "id" not in data:
        return None
    genome_id = data["id"]
    state = _load_state()
    history = state.get("genome_history", [])
    history.append(genome_id)
    state["genome_history"] = history
    state["current_genome_id"] = genome_id
    _save_state(state)
    return genome_id


def get_genome_diff(genome_id: str, version_a: int, version_b: int) -> dict | None:
    """Get the diff between two genome versions."""
    return _vijil(
        "genome", "diff", genome_id,
        "--version-a", str(version_a),
        "--version-b", str(version_b),
    )


def get_genome_versions(genome_id: str) -> list | None:
    """List all versions of a genome."""
    data = _vijil("genome", "versions", genome_id)
    if isinstance(data, dict):
        return data.get("items", [])
    return data


def record_mutation(generation: int, new_prompt: str, score_before: float, score_after: float) -> None:
    """Record a Darwin mutation in Vijil genome lineage.

    Updates the agent's system prompt in Vijil, then extracts the new genome.
    """
    state = _load_state()
    agent_id = state.get("agent_id")
    if not agent_id:
        return

    # Update agent system prompt via vijil agent update
    _vijil(
        "agent", "update", agent_id,
        "--agent-system-prompt", new_prompt,
    )

    # Extract new genome version
    genome_id = extract_genome(agent_id)

    mutations = state.get("mutations", [])
    mutations.append({
        "generation": generation,
        "genome_id": genome_id,
        "score_before": score_before,
        "score_after": score_after,
        "improved": score_after >= score_before,
    })
    state["mutations"] = mutations
    _save_state(state)

    if genome_id:
        print(f"  🧬 Vijil genome updated: gen-{generation} → genome {genome_id}")


def get_mutation_lineage() -> list[dict]:
    """Return the full Darwin mutation history with Vijil genome IDs."""
    state = _load_state()
    return state.get("mutations", [])
