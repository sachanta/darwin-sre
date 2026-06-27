# Vijil Integration — Dome + Genome

## What Vijil Does for Darwin SRE

Vijil provides two distinct capabilities used in this project:

| Capability | What it is | Where we use it |
|-----------|-----------|----------------|
| **Vijil Dome** | Runtime guardrail — scans input/output at inference time | Every incident resolution |
| **Vijil Genome** | Agent versioning — tracks the agent's prompt/skill evolution | Every Darwin generation |

Console: https://console.vijil.ai
Auth: Bearer JWT in `~/.vijil/config.yaml` + `~/.vijil/credentials.json`

---

## Part 1: Vijil Dome (Runtime Guardrails)

### What it is

Dome sits in the inference path and scans text before it goes into the LLM (input guard) and after the LLM responds (output guard). It catches things like prompt injection, harmful content, and secret leakage — without you writing detection logic yourself.

### What we scan

```
Incident arrives
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  INPUT GUARD (Dome)                                 │
│  ├── encoding-heuristics   (detect obfuscated text) │
│  └── moderation-flashtext  (detect harmful content) │
└──────────────────────┬──────────────────────────────┘
                       │ allowed / blocked
                       ▼
              SRE Agent resolves
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  OUTPUT GUARD (Dome)                                │
│  ├── moderation-flashtext  (harmful content check)  │
│  └── detect-secrets        (API keys / passwords)   │
└─────────────────────────────────────────────────────┘
```

We use a **light profile** — no heavy ML models (no torch, no GPU). This keeps it fast and dependency-light. The detectors we use:

| Detector | What it catches | Applied to |
|----------|----------------|-----------|
| `encoding-heuristics` | Base64/hex obfuscation tricks, prompt injection via encoding | Input |
| `moderation-flashtext` | Known harmful phrases (fast keyword matching) | Input + Output |
| `detect-secrets` | AWS keys, API tokens, passwords, private keys in text | Output |

### Fail-open policy

If Dome throws an exception for any reason, we **allow the request through**. This is intentional — the SRE system is operational-critical. A guardrail failure should never block incident resolution. We log the error and flag it.

```python
# darwin/vijil_dome.py
except Exception as exc:
    return GuardResult(allowed=True, flagged=False,
                       triggered=[f"dome_error:{exc}"], text=text)
```

### What happens when Dome flags something

- **Input flagged and blocked** → incident is skipped, `incident_blocked` event emitted, logged
- **Output flagged** → resolution still returned but tagged with `resolution_flagged` event; the judge still scores it (so you can see the agent produced questionable output)

### The code

```
darwin/vijil_dome.py   — GuardResult class, guard_incident_input(), guard_resolution_output()
darwin/loop.py         — calls both guards per incident in the main run loop
```

### Why it matters for the demo

In the supervisor UI, any flagged incident has a shield icon. The evidence drawer shows which detector triggered and what text was flagged. For a security-breach corner case (CCF-2 — credential stuffing disguised as high CPU), there's a chance the synthetic incident description contains patterns that Dome catches — which makes the demo more interesting.

---

## Part 2: Vijil Genome (Agent Lineage)

### What it is

Genome is Vijil's version control for AI agents. Instead of tracking code versions (that's git's job), it tracks the **behavioral state** of an agent — its system prompt, its capabilities, its evolution over time.

Think of it as `git blame` but for agent behavior: "at generation 3, the Darwin SRE agent gained the ability to handle cross-region latency incidents — here is the snapshot of its state at that moment."

### How Darwin uses it

After each Darwin evolution (each generation), we:

1. Update the agent's system prompt in Vijil (even though the base prompt is frozen, the **skill context** represents the agent's current behavioral state)
2. Call `genome extract` to snapshot the current genome
3. Store the `genome_id` locally in `.vijil_agent_state.json`

```
Gen 0: agent created in Vijil  →  genome snapshot 0 (base state)
Gen 1: CCF-1 skill added       →  genome snapshot 1
Gen 2: CCF-2 skill added       →  genome snapshot 2
...
Gen 8: CCF-8 skill added       →  genome snapshot 8
```

The genome lineage in the Vijil console shows the full evolution arc of Darwin SRE across the hackathon run. Each snapshot is a point-in-time capture of what the agent knew.

### The code

```
darwin/vijil_genome.py
  ├── ensure_agent(system_prompt)     — register agent once, save agent_id
  ├── extract_genome(agent_id)        — snapshot current state, save genome_id
  ├── record_mutation(gen, prompt, score_before, score_after)  — update + snapshot
  └── get_mutation_lineage()          — returns full history with genome_ids

.vijil_agent_state.json              — local state file (in .gitignore)
  {
    "agent_id": "...",
    "genome_history": ["genome_id_1", "genome_id_2", ...],
    "current_genome_id": "...",
    "mutations": [
      {"generation": 1, "genome_id": "...", "score_before": 0.38, "score_after": 0.79}
    ]
  }
```

### MCP integration

Darwin SRE exposes Vijil's genome tools via MCP (Model Context Protocol), so Claude Code itself can query the agent's evolution history during a session:

```json
// .mcp.json
{
  "mcpServers": {
    "vijil": {
      "command": "python",
      "args": ["-m", "vijil_mcp"],
      "env": {
        "VIJIL_BEARER_TOKEN": "${VIJIL_BEARER_TOKEN}",
        "VIJIL_URL": "https://console.vijil.ai"
      }
    }
  }
}
```

Available MCP tools: `genome_create`, `genome_extract`, `genome_diff`, `genome_versions`, `evolution_*`, `proposal_*`

### Why it matters for the demo

In the evidence drawer (click any Darwin generation in the UI), you can show the Vijil genome diff between generation N-1 and generation N — **proving the agent's behavioral state changed** in a verifiable, audited way. This is stronger evidence than just showing a score went up. It's the difference between "trust me, it improved" and "here is the cryptographically-tracked behavioral diff in Vijil."

---

## Credentials Setup

The Vijil CLI reads credentials from two files:

```yaml
# ~/.vijil/config.yaml
endpoint: https://console.vijil.ai
```

```json
// ~/.vijil/credentials.json
{"token": "<bearer JWT>"}
```

The bearer token has a ~30 day TTL. It was obtained from the Vijil/HCLTech sponsor team at the hackathon. The token is in `.env` as `VIJIL_BEARER_TOKEN` and is also loaded into the credentials file at setup time.

---

## Registration: Darwin SRE in Vijil Console

The agent is registered via `ensure_agent()` in `darwin/vijil_genome.py`. This runs automatically on the first call to `DarwinLoop.run()`. The registration creates a "Darwin SRE" agent entry in the Vijil console with:

- **Model**: `anthropic-claude-4.5-sonnet`
- **Hub**: custom
- **Protocol**: chat_completions
- **System prompt**: the base SRE prompt (frozen)

After registration, `agent_id` is saved to `.vijil_agent_state.json` so subsequent runs don't re-register.
