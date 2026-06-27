# Concept 2 — Data Architecture

## The Short Version

Everything lives in **MongoDB Atlas** (`darwin_sre` database). There are 8 collections. The system generates all its own training data synthetically using Claude, then embeds it with Voyage AI so it can be retrieved at resolution time.

---

## The 8 Collections

```
darwin_sre (MongoDB Atlas)
├── incidents          — the incident reports (training + production)
├── logs               — raw log blobs, one per incident
├── knowledge_articles — runbooks + Darwin-authored articles (vector-indexed)
├── skills             — Darwin-authored behavioral skills (NEW)
├── resolutions        — what the SRE agent said + judge scores
├── generations        — Darwin evolution lineage (one doc per evolution)
├── alerts             — degradation alerts (open → improving → resolved)
└── runs               — one captured session, used for UI replay
```

Think of it in three layers:

| Layer | Collections | Purpose |
|-------|-------------|---------|
| **Input** | incidents, logs, knowledge_articles, skills | What the agent sees |
| **Output** | resolutions, generations, alerts | What the agent and Darwin produced |
| **Session** | runs | The captured demo playback |

---

## Layer 1: Input Data

### incidents

The raw incident report. This is what arrives at the SRE agent's desk.

```json
{
  "id": "prod_031",
  "title": "auth-api returning 503 — database unreachable",
  "description": "Auth service logs show DB connection failures. DB CPU at 2%.",
  "service": "auth-api",
  "environment": "production",
  "category": "database",
  "is_edge_case": true,
  "edge_case_family": "CCF-1",
  "log_id": "log_prod_031",
  "kb_refs": [],
  "metrics": {"auth_error_rate": 0.94, "db_cpu": 0.02},
  "ground_truth": {
    "root_cause": "Upstream user-directory-service timing out — not the DB",
    "severity": "P1",
    "remediation_steps": ["Check user-directory-service health", "..."]
  }
}
```

Key fields to understand:
- **`ground_truth`** — the answer key. Only the judge sees this. The SRE agent never sees it.
- **`log_id`** — pointer to the raw logs in the `logs` collection
- **`kb_refs`** — pre-linked seed KB articles for normal incidents; **empty for corner cases** (intentional — this is why corner cases fail)
- **`edge_case_family`** — which of the 8 failure families this belongs to (`null` for normal incidents)

### The 8 Failure Families

These are the corner cases designed to fool the SRE agent. Each family is a class of incident where the symptoms mislead — the logs and metrics point to the wrong culprit.

| Family | Name | The Trick |
|--------|------|-----------|
| CCF-1 | cascading_upstream_failure | Service A blames DB, but upstream Service B is the real cause |
| CCF-2 | security_breach_disguised | Credential stuffing looks like high CPU load |
| CCF-3 | cross_region_latency | Cross-region replication lag presents as local 503s |
| CCF-4 | distributed_race_condition | Intermittent idempotency bug — hard to reproduce |
| CCF-5 | clock_skew_jwt | NTP drift causes JWT expiry false positives, looks like auth failure |
| CCF-6 | sidecar_memory_leak | Envoy proxy leaking memory, main service gets OOM-killed |
| CCF-7 | config_replica_drift | 2 of 5 replicas have wrong config — load balancer masks the problem |
| CCF-8 | thundering_herd_rollback | Cache cold start after rollback overwhelms the recovered service |

These families arrive as **ordered waves** in production. Each wave tanks the SRE agent's rolling average below 0.60, triggering one Darwin evolution. 8 waves = 8 evolutions = the staircase in the UI.

**Why no KB refs for corner cases?** Because the seed knowledge base only covers *normal* incident patterns. The SRE agent literally has no runbook for these. That's the failure premise. After Darwin fires, it writes a new skill + (in Phase D) a new KB article specifically for that family. On the replay, the retrieval finds it and the agent passes.

### logs

One log blob per incident. Stored separately to keep the incident doc lean.

```json
{
  "id": "log_prod_031",
  "incident_id": "prod_031",
  "lines": [
    {"ts": "2026-06-27T14:00:01Z", "level": "WARN", "msg": "DB pool at 80%", "service": "auth-api"},
    {"ts": "2026-06-27T14:00:15Z", "level": "ERROR", "msg": "Connection timeout after 30s"},
    {"ts": "2026-06-27T14:00:18Z", "level": "ERROR", "msg": "user-directory: upstream timeout 8200ms"}
  ],
  "summary": "Auth failures caused by upstream user-directory timeout, not DB"
}
```

For **normal incidents**: the log lines clearly show the failure progression — easy for the agent to read.
For **corner cases**: early lines look like the wrong problem; the real signal is buried or subtle. This is engineered to mislead.

The SRE agent uses the **top 8 log lines** as part of its resolution context. The full log is shown in the UI evidence drawer when you click on an incident.

### knowledge_articles

Runbooks. Vector-indexed. Retrieved before each resolution.

```json
{
  "id": "kb_001",
  "title": "Runbook: PostgreSQL connection pool exhaustion",
  "body": "Connection pool exhaustion occurs when all available DB connections are in use...",
  "service": "general",
  "tags": ["database", "postgresql", "connection-pool"],
  "source": "seed",
  "created_by_generation": null,
  "embedding": [0.023, -0.041, ...]  // 1024-dim Voyage AI vector
}
```

Two types:
- **`source: "seed"`** — 25 articles generated upfront, covering normal categories only. No articles for CCF-1 through CCF-8.
- **`source: "darwin"`** — articles written by Darwin after each evolution. Targeted at the failure family that just tripped the agent. These are what make the replay pass.

The `embedding` field is 1024 floats. MongoDB Atlas uses it to find the closest article to an incoming incident via cosine similarity. The embedding is **never sent to the SRE agent** — only the `title` and `body` are injected into the prompt.

### skills

Behavioral guidance that Darwin writes. One skill per evolution.

```json
{
  "id": "skill_003_a4f2c1",
  "name": "Cross-Region Latency Disambiguation",
  "guidance": "When a service reports 503s but local health checks pass, check cross-region replication lag before diagnosing the local service. Key signal: local CPU/memory normal but response times spiking.",
  "tags": ["CCF-3", "network", "cross-region"],
  "created_by_generation": 3,
  "use_count": 12,
  "last_used": "2026-06-28T03:14:22Z",
  "last_used_generation": 6,
  "active": true
}
```

Different from KB articles:
- **KB article** = *knowledge* ("here is the runbook for X")
- **Skill** = *behavior* ("when you see X, do Y first before assuming Z")

Skills are matched to incidents by tag overlap (family ID first, then category). They're injected as a "## Learned Skills" section in the system prompt — *above* the KB runbooks. A skill that hasn't been triggered in 4 generations gets archived (`active: false`). This prevents the agent accumulating stale guidance for failure patterns that no longer occur.

---

## Layer 2: Output Data

### resolutions

What the SRE agent said. One doc per incident per run.

```json
{
  "incident_id": "prod_031",
  "generation": 2,
  "resolution": {
    "root_cause": "Database connection pool exhausted",  // WRONG (it's upstream timeout)
    "severity": "P1",
    "remediation_steps": ["Increase pool size", "Restart pgbouncer"],
    "estimated_resolution_minutes": 15,
    "confidence": "high"
  },
  "scores": {
    "root_cause_accuracy": 0.10,
    "remediation_quality": 0.20,
    "severity_accuracy": 1.0,
    "composite": 0.34,
    "reasoning": "Agent blamed DB pool exhaustion; real cause was upstream service timeout."
  },
  "retrieved_kb_ids": [],
  "run_id": "run_20260628_001",
  "timestamp": "2026-06-28T02:14:33Z"
}
```

The `retrieved_kb_ids` field is empty here — because `prod_031` is a corner case and no seed KB article matched. After Darwin evolution 1, a replay of this incident would have a KB article and skill in context, and the score would jump to 0.78+.

### generations

Darwin's evolution record. One doc per Darwin trigger.

```json
{
  "generation_id": 1,
  "trigger": "score_degradation",
  "system_prompt": "...(base prompt, unchanged)...",
  "prompt_diff": "[skill written] Gen-1 skill 'Upstream Cascade Detection' targeting CCF-1",
  "score_before": 0.38,
  "score_after": 0.79,
  "failed_incident_ids": ["prod_031", "prod_032", "prod_033"],
  "failure_patterns": ["CCF-1"],
  "new_kb_article_id": "kb_darwin_001",
  "run_id": "run_20260628_001",
  "timestamp": "2026-06-28T02:22:11Z"
}
```

This is the paper trail for the staircase chart. `score_before` and `score_after` for each generation draw the sawtooth.

### alerts

The signal that triggered Darwin. Raised when rolling avg < 0.60.

```json
{
  "raised_at": "2026-06-28T02:14:40Z",
  "rolling_avg": 0.38,
  "window_scores": [0.34, 0.41, 0.35, 0.40, 0.40],
  "failing_incident_ids": ["prod_031", "prod_032", "prod_033", "prod_034", "prod_035"],
  "generation": 1,
  "status": "resolved",
  "resolved_at": "2026-06-28T02:24:55Z",
  "run_id": "run_20260628_001"
}
```

State machine: `open → improving → resolved`. The UI shows red markers at `raised_at` and green markers at `resolved_at`. Each red→green pair is one tooth of the sawtooth.

---

## Layer 3: Session Data

### runs

One document per autonomous capture run. Used by the UI to replay the session.

```json
{
  "run_id": "run_20260628_001",
  "started_at": "2026-06-28T01:00:00Z",
  "finished_at": "2026-06-28T04:30:00Z",
  "num_generations": 8,
  "baseline_avg": 0.84,
  "final_avg": 0.91,
  "episode_order": ["prod_001", "prod_002", ..., "prod_050"],
  "status": "complete"
}
```

Every other document (resolutions, generations, alerts) is tagged with this `run_id`. The UI's replay endpoint (`GET /runs/{id}/timeline`) joins all of them in chronological order and streams them back. The Play button animates through this timeline — incidents tick in, scores plot on the chart, Darwin evolution panels pop up, alerts raise and resolve.

---

## How the Data Flows (per incident)

```
incident arrives
    │
    ├─► get_log(incident.log_id)           → log lines for context
    ├─► retrieve_kbs(incident)             → top-3 KB articles by vector similarity  
    ├─► retrieve_skills(incident)          → active skills matching family/category
    │
    ▼
SRE Agent resolves (base prompt + skills + runbooks + logs)
    │
    ▼
Gemini Judge scores (resolution vs ground_truth) → composite 0.0–1.0
    │
    ├─► save_resolution(...)              → resolutions collection
    ├─► Arize span (score.composite)      → degradation.py reads this back
    └─► rolling window update             → if avg < 0.60 → Darwin fires
```

---

## Data Generation (how the synthetic data was created)

All data was generated by Claude Sonnet via DigitalOcean inference — using the $200 DO credit, not the Google credit.

Three passes:
1. **`data/generate_incidents.py`** — generates incidents in batches of 10, then generates log blobs in batches of 5 (rate limit workaround). Corner cases generated family by family so the family structure is clean.
2. **`data/generate_logs.py`** — repair script for any empty log blobs (the first pass hit DO's rate limits).
3. **`scripts/setup_atlas_vector.py`** — embeds all 25 seed KB articles in a single Voyage AI call (3 RPM free tier limit), upserts to MongoDB, creates the `kb_vector_idx` Atlas Search index.

The generated files (`incidents_training.json`, `incidents_production.json`, `logs.json`, `knowledge_base.json`) are in `.gitignore` — they're seeded into MongoDB, not committed. The *scripts* that generate them are committed.

---

## Next: [Concept 3 — The Agent Pipeline](03_agent_pipeline.md)
