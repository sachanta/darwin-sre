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
_AGENT_NAME = "Darwin SRE"


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
    """Create the Darwin SRE agent in Vijil if it doesn't exist yet.

    Returns the Vijil agent_id, or None if creation fails.
    """
    state = _load_state()
    if "agent_id" in state:
        return state["agent_id"]

    data = _vijil(
        "agent", "create",
        "--agent-name", _AGENT_NAME,
        "--model-name", "anthropic-claude-4.5-sonnet",
        "--agent-system-prompt", system_prompt,
        "--hub", "custom",
        "--protocol", "chat_completions",
        "--deployment", "local",
    )
    if not data or "id" not in data:
        return None

    agent_id = data["id"]
    state["agent_id"] = agent_id
    _save_state(state)
    print(f"  ✅ Vijil agent registered: {agent_id} ('{_AGENT_NAME}')")
    return agent_id


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
