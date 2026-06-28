# Concept 3 — The SRE Agent Pipeline

## Overview

Each incident passes through a four-stage pipeline before a score reaches Arize. Everything between "incident arrives" and "score recorded" happens inside a single `resolve_incident()` call.

```
Incident
   │
   ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. CONTEXT ASSEMBLY                                             │
│    Skills retrieval  (tag-match against skill library)          │
│    KB retrieval      (Voyage embed → Atlas $vectorSearch top-3) │
│    Log fetch         (MongoDB logs_col by log_id)               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. RESOLUTION  (Claude Sonnet via DO inference)                 │
│    system = base_prompt + "## Learned Skills" + "## Runbooks"   │
│    user   = incident title + description + log lines + metrics  │
│    output = { root_cause, severity, remediation_steps,          │
│               estimated_resolution_minutes, confidence }        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. JUDGING  (Gemini 3.5 Flash — LLM-as-Judge)                  │
│    Compares resolution against ground_truth embedded in data    │
│    root_cause_accuracy   40%  (did agent find the real cause?)  │
│    remediation_quality   40%  (correct, ordered, complete?)     │
│    severity_accuracy     20%  (P1 / P2 / P3 match?)            │
│    composite = weighted sum → scalar in [0, 1]                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. OBSERVABILITY  (Arize AX via OpenTelemetry)                  │
│    sre.resolve span:  incident attrs + context counts           │
│    darwin.judge span: score.composite + per-dimension scores    │
│    darwin.run span:   generation, rolling_avg (per full run)    │
│    → all visible in Arize project "darwin-sre"                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Stage 1: Context Assembly

Before the SRE agent sees a single token, two retrieval passes run in parallel:

### Skills (tag-match)
`darwin/skills.py` — `retrieve_skills(incident)`

Skills are short, targeted guidance blobs written by the Darwin agent after each failure. They are matched to the incident by tag overlap:

1. Exact `edge_case_family` match (e.g. `"CCF-3"`) — highest priority
2. Category match (e.g. `"database"`, `"cache"`)
3. General skills (tagged `"general"`) — lowest priority

At most 3 skills are injected. Each use is tracked (`use_count`), and skills that haven't matched in 4+ generations are retired.

### Knowledge Base (vector search)
`darwin/retrieval.py` — `retrieve_kbs(incident)`

1. Build a query string: `title + description + top log summary`
2. Embed with Voyage AI `voyage-3` (`input_type="query"`, 1024-dim)
3. Run Atlas `$vectorSearch` over `knowledge_articles.embedding` (cosine, `kb_vector_idx`)
4. Return top-3 articles with similarity scores, filtered by service where relevant

The seed KB covers normal failure categories. Corner-case families have **no seed runbook** — so the agent fails until Darwin writes one. The new Darwin-authored article is immediately embedded and indexed, so it surfaces in the very next retrieval.

---

## Stage 2: Resolution — Prompt Construction

`agents/sre_agent.py` — `_build_system_prompt(base, skills, kb_articles)`

```
[Base prompt — frozen, never mutated]

## Learned Skills        ← injected if skills matched
### [Skill Name]
[guidance text]

## Relevant Runbooks     ← injected if KB articles retrieved
### [Article Title] (similarity: 87%)
[runbook body]
```

The base prompt is **permanently frozen**. All behavioral improvement happens through:
- Skills — short reflexes loaded situationally
- KB articles — detailed runbooks retrieved by semantic similarity

This means the agent's improvement is **composable and auditable**: you can inspect exactly which skills and runbooks were in context for any incident.

---

## Stage 3: Judging

`agents/judge.py` — `score_resolution(incident, resolution)`

The judge receives:
- The original incident (title, description, metrics)
- The agent's resolution
- The ground truth (embedded in the synthetic incident data)

It returns structured scores per dimension, with the composite used as the primary signal everywhere downstream: rolling window, Darwin trigger, Arize annotation, replay validation.

DigitalOcean Note: The DO inference endpoint does not support `response_format={"type":"json_object"}`. All JSON parsing strips markdown fences explicitly (`_parse_response()`).

---

## Stage 4: Observability

`observability.py` — wraps Arize AX via OpenInference + OTel

Every pipeline run emits three nested spans:

| Span | Key attributes |
|------|---------------|
| `sre.resolve` | `incident.id`, `incident.edge_case_family`, `context.num_skills`, `context.num_kb_articles`, `output.severity` |
| `darwin.judge` | `score.composite`, `score.root_cause_accuracy`, `score.remediation_quality`, `score.severity_accuracy` |
| `darwin.run` | `generation`, `total_incidents`, `final_rolling_avg` |

The `darwin.degradation` module reads `score.composite` back from these spans via the Arize GraphQL API to compute the rolling average signal — so "the system saw in its Arize telemetry that it was degrading" is literally true.

---

## Data flow summary

```
resolve_incident(incident, system_prompt, skills, kb_articles)
 └─ _build_system_prompt(base, skills, kb_articles)
 └─ _build_user_message(incident)   ← reads log from MongoDB
 └─ OpenAI(DO).chat.completions.create(SRE_MODEL)
 └─ _parse_response(raw)            ← strips markdown fences
 └─ Arize sre.resolve span
    └─ score_resolution(incident, resolution)   ← Gemini judge
       └─ Arize darwin.judge span (score.composite)
```

All resolved incidents are saved to `resolutions` collection with `retrieved_kb_ids`, `generation`, and `run_id` tags for replay.
