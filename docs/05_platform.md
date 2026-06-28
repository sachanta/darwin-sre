# Concept 5 — Platform & Sponsor Stack

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DARWIN SRE  (DO App Platform)                      │
│                                                                              │
│   FastAPI  ──── /stream/{run_id}  SSE  ──────────────────────┐             │
│   + static        /runs/{id}/timeline                          │             │
│   frontend        /incidents/{id}                              ▼             │
│                   /health                             ┌─────────────────┐   │
│                                                       │  Supervisor UI  │   │
│   ┌──────────────────────────────────────────┐       │  (Chart.js)     │   │
│   │              Darwin Loop                  │       │  Score chart    │   │
│   │                                           │       │  Evolution cards│   │
│   │  SRE Agent  ──resolve──►  LLM-as-Judge   │       │  Incident ticker│   │
│   │  (Sonnet)               (Gemini Flash)   │       │  Evidence drawer│   │
│   │       │                      │            │       └─────────────────┘   │
│   │       ▼                      ▼            │                              │
│   │  Degradation Detector  ← Arize GraphQL   │                              │
│   │       │                                   │                              │
│   │       ▼ (trigger)                         │                              │
│   │  Darwin Agent  ──►  Skill + KB write      │                              │
│   │  (Haiku)        ▼                         │                              │
│   │            Vijil Genome snapshot          │                              │
│   │                  │                        │                              │
│   │                  ▼                        │                              │
│   │           Replay + validate               │                              │
│   └──────────────────────────────────────────┘                              │
└─────────────────────────────────────────────────────────────────────────────┘
         │             │              │              │              │
         ▼             ▼              ▼              ▼              ▼
   DigitalOcean    MongoDB Atlas   Voyage AI     Arize AX       Vijil
   (inference +    (documents +   (embeddings)  (observability (guardrails +
    deploy)         vector search)               + eval)        genome)
```

---

## Sponsor Components

### DigitalOcean — Inference + Deployment

**Inference (DO AI)**
All LLM calls route to `https://inference.do-ai.run/v1/` via the OpenAI-compatible SDK:

```python
client = OpenAI(
    api_key=DIGITAL_OCEAN_MODEL_ACCESS_KEY,
    base_url="https://inference.do-ai.run/v1/"
)
```

| Model | Used for | Why |
|-------|----------|-----|
| `anthropic-claude-4.5-sonnet` | SRE Agent resolution | Best reasoning for incident diagnosis |
| `anthropic-claude-haiku-4.5` | Darwin mutator (Skill + KB authoring) | Fast + cheap for generative improvement |

Two models on one endpoint, one key. The LLM-as-Judge (Gemini) uses Google's API separately.

**Deployment (DO App Platform)**
Single-container service defined in `.do/app.yaml`:
- `basic-s` instance, `nyc` region
- Dockerfile: `poetry export → pip install → uvicorn`
- FastAPI serves both the API (`/runs`, `/stream`, `/incidents`, `/health`) and the static frontend (`/`)
- Health check: `GET /health` every 10s after 30s warm-up
- `deploy_on_push: true` — every push to `main` autodeploys

---

### MongoDB Atlas — Document Store + Vector Search

**Collections used:**

| Collection | Documents |
|-----------|-----------|
| `incidents` | 120+ synthetic incidents with ground truth |
| `logs` | Timestamped log blobs per incident |
| `knowledge_articles` | Seed runbooks + Darwin-authored runbooks |
| `resolutions` | SRE Agent outputs + judge scores |
| `skills` | Darwin-written skill library |
| `generations` | Darwin generation metadata |
| `alerts` | open → improving → resolved lifecycle |
| `runs` | Run-level metadata for replay |

**Vector Search index (`kb_vector_idx`):**
- Field: `knowledge_articles.embedding`
- Dimension: 1024 (Voyage AI `voyage-3`)
- Similarity: cosine
- Query: `$vectorSearch` pipeline stage, `numCandidates=20`, `limit=3`

**Why Atlas:** the same DB that stores incident documents also serves semantic search over KB articles — no separate vector store. The Darwin-authored KB article is embedded and indexed in-place immediately after generation, making it available to the next retrieval call within the same run.

---

### Voyage AI — Embeddings

Model: `voyage-3` (1024-dimensional)

| Call site | `input_type` | Content |
|-----------|-------------|---------|
| `index_kb_article()` | `"document"` | KB article title + body |
| `retrieve_kbs()` | `"query"` | incident title + description + log summary |

Using typed `input_type` is important: Voyage optimizes document and query vectors separately for asymmetric retrieval. This improves recall meaningfully for runbook-style content vs. short incident descriptions.

**Fail-safe:** KB article embedding is wrapped in `try/except` so a transient Voyage error doesn't abort an evolution cycle. The article is still authored and saved; it just won't be retrievable until the embedding succeeds on retry.

---

### Arize AX — Observability + Degradation Sensor

**Spans emitted (OpenInference + OTel):**

```
darwin.run
  └─ sre.resolve          ← one per incident
       └─ darwin.judge    ← composite + per-dimension scores
```

Arize is used in two directions:

1. **Write:** every resolution emits spans with `score.composite` as an attribute. This builds a continuous quality timeline in the Arize console visible to judges.

2. **Read (Option C):** `darwin/degradation.py` queries the Arize GraphQL API to fetch recent `sre.resolve` spans and extract `score.composite` values. The rolling average that triggers the Darwin loop is computed from **live Arize telemetry**, not just an in-memory deque. This makes "the agent detected degradation in its observability layer" a literally true statement.

```python
def _fetch_arize_scores(limit=20, lookback_minutes=10) -> list[float] | None:
    # GraphQL → filter by project + model + span_name
    # Extract "score.composite" from span attributes
    # 5s timeout; returns None on any failure (falls back to local)
```

**Experiments:** `darwin/arize_client.py` creates an Arize experiment per Darwin generation (`darwin-gen-N`), logging before/after scores for all replayed incidents. This gives judges a model-level eval comparison across generations.

---

### Vijil — Guardrails (Dome) + Agent Lineage (Genome)

**Dome (runtime guardrails)**
`darwin/vijil_dome.py` — wraps every SRE Agent LLM call:

| Guard | Type | Action |
|-------|------|--------|
| `encoding-heuristics` | Input | Detect encoded payloads in incident descriptions |
| `moderation-flashtext` | Input | Block prohibited content keywords |
| `detect-secrets` | Output | Redact secrets accidentally in SRE resolutions |

Policy: **fail-open** — if Vijil is unreachable, the resolution proceeds and the Dome call is logged as skipped. This prevents a guardrail service outage from blocking production incident resolution.

**Genome (agent lineage)**
Two agents registered in the Vijil console:

| Agent | ID | Role |
|-------|----|------|
| SRE Agent (worker) | `55f52ac2-b669-40a5-97a6-2326e983efb1` | Resolves incidents; genome lineage tracked here |
| Darwin Agent (optimizer) | `b2c130d1-3597-4679-91cf-abe18d4959b5` | Delegates to SRE Agent via DO inference URL |

`darwin/vijil_genome.py` — `record_mutation()` posts a genome snapshot after each Darwin generation:

```python
{
  "generation": N,
  "prompt_hash": sha256(current_prompt),
  "prompt_length": len(current_prompt),
  "score_before": 0.31,
  "score_after": 0.78,
  "num_active_skills": 3,
  "failure_patterns": ["CCF-2: ..."],
  "skill_names": ["Cache Eviction Diagnosis", ...]
}
```

The Vijil console shows an 8-step lineage for the SRE Agent — each step a Darwin generation — even though the agent was running autonomously with no human prompt changes. This is the **Recursive Self-Improvement lineage, cryptographically tracked**.

---

### Google DeepMind (Gemini) — LLM-as-Judge

Model: `gemini-1.5-flash` (via `google-genai` SDK)

The judge evaluates every SRE Agent resolution on three dimensions:

| Dimension | Weight | Evaluates |
|-----------|--------|-----------|
| `root_cause_accuracy` | 40% | Did the agent identify the actual root cause? |
| `remediation_quality` | 40% | Are the remediation steps correct, ordered, complete? |
| `severity_accuracy` | 20% | Did the agent assign the right P1/P2/P3 tier? |

Output: `composite` in [0, 1]. This scalar is the primary signal for everything: rolling window, Darwin trigger, Arize span attribute, replay validation, staircase chart.

Ground truth (embedded in synthetic incident data) makes judging deterministic enough to be a reliable eval signal across runs.

---

## Deployment Checklist

```bash
# 1. Generate data + seed MongoDB
poetry run python data/generate_incidents.py
poetry run python scripts/setup_atlas_vector.py   # embeds seed KB, creates vector index

# 2. Capture autonomous run (~15-20 min)
poetry run python main.py                         # 8 Darwin generations → MongoDB

# 3. Serve locally (dev)
poetry run uvicorn api.app:app --host 0.0.0.0 --port 8080

# 4. Integration smoke (needs DARWIN_DEPLOY_URL after DO deploy)
DARWIN_DEPLOY_URL=https://darwin-sre-xxxxx.ondigitalocean.app \
  poetry run pytest tests/test_health.py -m integration -v
```

Public URL format after DO deploy: `https://darwin-sre-<hash>-<region>.ondigitalocean.app`
