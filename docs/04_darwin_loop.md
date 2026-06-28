# Concept 4 — The Darwin Loop

## What it is

The Darwin Loop is the self-improvement engine. It watches the SRE Agent's resolution quality, detects when a new class of failures is causing systematic degradation, and autonomously improves the agent — writing a new Skill, authoring a KB runbook, validating the fix by replay, and snapshotting the new behavioral state in Vijil Genome. No human is involved between detection and validation.

---

## The closed loop, step by step

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DETECT                                      │
│  After each resolution: append composite score to rolling window    │
│  Primary signal: Arize GraphQL → recent sre.resolve spans           │
│  Fallback:       local deque (in-memory, same-process)              │
│                                                                     │
│  Trigger condition:                                                 │
│    len(window) == DARWIN_WINDOW_SIZE (3)                            │
│    AND rolling_avg < DARWIN_TRIGGER_THRESHOLD (0.60)                │
│    AND generation < DARWIN_MAX_GENERATIONS (10)                     │
└────────────────────────────┬────────────────────────────────────────┘
                             │ triggered
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          ALERT                                      │
│  save_alert(status="open", rolling_avg, window_scores,              │
│             failing_incident_ids, run_id)                           │
│  emit: alert_raised → UI ticker + evolution panel                   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        SELF-HEAL                                    │
│                                                                     │
│  update_alert_status("improving")                                   │
│                                                                     │
│  A. Generate Skill  (Claude Haiku via DO inference)                 │
│     Input:  up to 8 recent failures (incident + resolution +        │
│             correct_root_cause + score + judge_reasoning)           │
│     Output: { name, guidance, tags }                                │
│     Saved to: skills_col (active=True, tagged by family+category)   │
│                                                                     │
│  B. Generate KB Article  (Claude Haiku via DO inference)            │
│     Input:  same failure set + skill name just written              │
│     Output: { title, body, service, tags, source="darwin" }         │
│     Embedded: Voyage AI voyage-3 (1024-dim, input_type="document")  │
│     Indexed:  Atlas $vectorSearch kb_vector_idx (upsert)            │
│     Saved to: knowledge_articles (source="darwin")                  │
└────────────────────────────┬────────────────────────────────────────┘
                             │ skill active + KB indexed
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         REPLAY                                      │
│  Re-run the 5 most recent failing incidents through the full        │
│  pipeline — same resolve_incident() call, same judge:               │
│                                                                     │
│  retrieve_skills(incident)   ← now includes new Skill               │
│  retrieve_kbs(incident)      ← now returns new KB article           │
│  resolve_incident(...)       ← Sonnet sees both                     │
│  score_resolution(...)       ← Gemini judges again                  │
│                                                                     │
│  score_after = mean of replay composite scores                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        VALIDATE                                     │
│  improved = score_after >= score_before                             │
│                                                                     │
│  If improved:                                                       │
│    update_alert_status("resolved")                                  │
│    emit: alert_resolved                                             │
│                                                                     │
│  save_generation(generation_id, score_before, score_after,          │
│                  new_kb_article_id, failure_patterns, run_id)       │
│                                                                     │
│  retire_stale_skills(generation)  ← skills unused for 4+ gens      │
│  window.clear()                   ← reset rolling window            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       RECORD                                        │
│  Arize: create_experiment(generation, all_results)                  │
│    → experiment "darwin-gen-N" with per-incident scores             │
│                                                                     │
│  Vijil: record_mutation(generation, prompt, score_before, score_after)│
│    → genome snapshot on "SRE Agent (worker)" in Vijil console       │
│    → genome lineage: gen 0 (base) → gen 1 → … → gen 8              │
│                                                                     │
│  emit: darwin_complete → UI evolution panel                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Why this produces a sawtooth

Each of the 8 corner-case families is designed as a **wave episode**:

```
15 normal incidents   → rolling avg stable ~0.85 (baseline)
CCF-1 family (3)      → scores drop to ~0.25-0.35 → Darwin fires (gen 1)
3 washout incidents   → window clears, avg recovers
CCF-2 family (2-3)   → scores drop → Darwin fires (gen 2)
...
15 normal incidents   → final validation: all 8 skills + KBs active
```

The key invariant: corner-case families have **no seed runbook in the KB**. The SRE agent fails because it literally cannot retrieve any relevant guidance. After Darwin writes the runbook, the same incident would score 0.75+. That delta is the staircase.

The window size of 3 ensures each 2-3 incident family reliably tanks the rolling average without needing a longer sequence. The 3-incident washout between families resets the window cleanly so families don't bleed into each other.

---

## The two agents, precisely

| | SRE Agent (worker) | Darwin Agent (optimizer) |
|---|---|---|
| **Model** | Claude Sonnet 4.5 (DO) | Claude Haiku 4.5 (DO) |
| **Does** | Resolves incidents | Improves the SRE Agent |
| **Mutates** | Nothing — frozen | Skills library + KB |
| **Scored by** | Gemini 3.5 Flash | Not scored (it's the improver) |
| **Vijil entry** | `SRE Agent (worker)` — genome lineage | `Darwin Agent (optimizer)` — delegates to SRE Agent |
| **Visible in demo** | Every incident ticker row | Every evolution card |

---

## What "base prompt frozen" means

The SRE Agent's system prompt never changes. `current_prompt = DEFAULT_SYSTEM_PROMPT` throughout the entire run. What changes is:

1. **Skills** — short reflexes loaded situationally by tag-match
2. **KB articles** — detailed runbooks retrieved by vector similarity

This means the agent's improvement is:
- **Composable** — multiple skills from different generations stack
- **Auditable** — any resolution has a complete record of which skills and KB articles were in context
- **Reversible** — retiring a skill is atomic; the base agent is unaffected
- **Bloat-free** — the base prompt never grows

The Vijil Genome snapshot after each generation captures the full skill library state, making the behavioral evolution cryptographically tracked even though no prompt text changed.

---

## Code map

```
darwin/loop.py          — DarwinLoop class; run() + _run_darwin()
darwin/degradation.py   — should_trigger() — Arize primary + local fallback
darwin/mutator.py       — generate_skill() + generate_kb_article()
darwin/skills.py        — save/retrieve/retire skills; format_skills_for_prompt()
darwin/retrieval.py     — embed() + index_kb_article() + retrieve_kbs()
darwin/storage.py       — save_alert/update_alert_status/save_generation
darwin/arize_client.py  — create_experiment() per generation
darwin/vijil_genome.py  — ensure_agent() + record_mutation() + extract_genome()
```
