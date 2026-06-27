# Concept 1 — Problem & Vision

## The Problem

Modern production systems generate hundreds of incidents per day. Each one pages an on-call engineer at 3am, who has to:

1. Read noisy, contradictory logs
2. Search a wiki for the right runbook
3. Figure out the real root cause (which is often not what the alerts say)
4. Execute a remediation and verify it worked

This is expensive, slow, and exhausting. The industry has tried rule-based automation, but rules break the moment the system changes. What if the SRE agent itself could learn from its own mistakes?

---

## The Vision: A Self-Healing SRE

Darwin SRE is an AI agent that:

- **Resolves incidents** automatically (no human on-call needed for P2/P3)
- **Monitors its own quality** via an evaluation loop
- **Improves itself** when its quality degrades — without any human intervention

The key insight is: **the agent knows when it's getting worse** (it can see its own Arize telemetry), and **it can do something about it** (rewrite its own prompt and add new knowledge to its retrieval base).

This is the "Recursive Self-Improvement" theme: the AI acts as its own AI engineer.

---

## The Darwin Metaphor

The project is named after Charles Darwin deliberately.

| Darwin's Theory | Darwin SRE |
|----------------|-----------|
| Organism | SRE Agent (its prompt + knowledge base) |
| Environment | Production incidents — some easy, some tricky |
| Selection pressure | Corner-case incident families that tank the score |
| Mutation | Darwin Agent rewrites the SRE prompt |
| New trait acquisition | Darwin Agent writes a new KB runbook article |
| Fitness test | Gemini judge re-scores the agent on the same failing incidents |
| Survival | If score improves, the new prompt/KB is kept |
| Lineage | Vijil genome snapshots every generation |

The system doesn't evolve randomly — it evolves in **response to specific failures**, making it fast and targeted.

---

## Two Agents, One Goal

This is important: there are **two distinct AI agents** in the system.

### The SRE Agent (the worker)
- **Model:** Claude 4.5 Sonnet (via DigitalOcean inference)
- **Job:** Resolve incidents
- **What it produces:** `root_cause`, `severity`, `remediation_steps`
- **What can change about it:** Its system prompt and the KB articles it retrieves
- **Who changes it:** Only the Darwin Agent

### The Darwin Agent (the improver)
- **Model:** Claude Haiku (for prompt mutation, cheap + fast) + Claude Sonnet (for writing KB articles)
- **Job:** Detect when the SRE Agent is degrading, then improve it
- **What it produces:** A new system prompt diff + a new KB article
- **What triggers it:** Rolling average composite score drops below 0.60 over a 5-incident window

Think of it like this: the **SRE Agent is the employee** being evaluated. The **Darwin Agent is the automated manager** that rewrites the job description and adds new training material when the employee starts failing.

---

## Operating Model

There are two modes:

### Build Phase (human + AI pair-programming)
- Human (Srikar) + Claude Code build the platform
- We test the Darwin loop together
- All platform code is finalized by midnight

### Autonomous Mode (the captured demo run)
- Human is only a **supervisor** — watching the dashboard, not intervening
- Incidents arrive automatically
- The Darwin Agent takes full control of improving the SRE Agent
- **Only two things are allowed to change:** the SRE Agent's system prompt, and the knowledge base
- Everything else (code, infra, evaluation logic) is frozen

The demo narrative: *"I built the platform in 10 hours. Then I fed it incidents and let it run. These 8 improvements you see in the chart — the system did those by itself."*

---

## Why This Is Hard (and Interesting)

A naive approach would just retry failed incidents or alert a human. Darwin SRE does something harder:

1. **It identifies what category of failure is happening** (e.g., "cascading upstream failures are tripping up the agent")
2. **It generates a hypothesis** about why the prompt is failing for that category
3. **It authors new knowledge** (a runbook article) that didn't exist before
4. **It validates its own improvement** by replaying the exact incidents that failed

The validation step is the key: after evolution, the system replays the failing incidents through the **full pipeline** (including retrieval — so the new KB article is now available). If the score goes up, the evolution is accepted. This closes the loop.

---

## What This Is NOT

- Not a rule-based system ("if error contains X, do Y")
- Not just prompt engineering (the KB is also mutable)
- Not a system that improves by training (no fine-tuning — weights are frozen; only the prompt and retrieval corpus change)
- Not dependent on human feedback during autonomous mode

---

## Key Numbers for the Demo

| Metric | Value |
|--------|-------|
| Incident categories (normal) | 6 (database, performance, network, storage, service, configuration) |
| Corner-case failure families | 8 (CCF-1 through CCF-8) |
| Darwin generations | 8 (one per family, clean staircase) |
| Degradation threshold | composite score < 0.60 over a 5-incident window |
| Seed KB articles | 25 (cover normal categories only) |
| KB articles after 8 evolutions | ~33 (25 seed + 8 Darwin-authored) |

---

## Next: [Concept 2 — Data Architecture](02_data_architecture.md)
