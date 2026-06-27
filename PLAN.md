# Darwin SRE — Build Plan
## AI Engineer World's Fair Hackathon 2026

**Theme:** Recursive Intelligence (RSI)
**Submission deadline:** Sunday June 28, 12:00 PM PDT

---

## What We're Building

An AI SRE (Site Reliability Engineer) that resolves IT incidents — and **automatically rewrites its own brain** when its performance degrades in production.

The key insight: most AI agents have a fixed system prompt forever. Darwin SRE watches its own eval scores in real time. When a wave of edge-case incidents tanks the scores, **Darwin activates**: it reads the failure patterns, mutates the system prompt, re-evaluates, keeps the winner, and continues. The agent gets measurably better, on its own, without human intervention.

---

## Demo Story (3 minutes)

1. Agent handles normal incidents: scores ~0.80 (root cause ✓, remediation ✓, severity ✓)
2. Edge case wave arrives: cascading failures, security incidents disguised as perf issues → score drops to ~0.40
3. Darwin detects degradation (rolling average < 0.60) → activates
4. Live on screen: Darwin reads failures, proposes new prompt, shows diff
5. Re-evaluation on same failed incidents → score climbs to ~0.75
6. Agent now handles the edge cases it couldn't before

---

## Tech Stack

| Component | Service | Sponsor |
|-----------|---------|---------|
| SRE Agent LLM | Claude Sonnet via DigitalOcean inference | DigitalOcean |
| Judge/Evaluator | Gemini 3.5 Flash via Google AI | Google DeepMind |
| Darwin Mutator | Claude Haiku via DigitalOcean inference | DigitalOcean |
| Lineage store | MongoDB Atlas | MongoDB |
| Backend | FastAPI + SSE | — |
| Deploy | DO App Platform | DigitalOcean |

**Inference endpoint:** `https://inference.do-ai.run/v1/` (OpenAI-compatible)
**Judge endpoint:** Google Generative AI SDK (`gemini-2.0-flash`)

---

## Data Design

### Synthetic Incidents (100 total)

**50 Training incidents** — used to establish baseline SRE agent behavior. Normal, well-defined incidents with clear root causes:
- Database connection pool exhaustion
- High CPU / memory leak
- API timeout / slow queries
- Disk space warnings
- SSL certificate near-expiry
- Load balancer health check failures
- Queue backlog buildup
- Cache hit rate degradation
- Service restart loops
- Network latency spikes

**50 Production incidents** — streamed in waves as if arriving in real-time:
- First 30: normal incidents (agent scores well, ~0.80)
- Last 20: **edge cases** designed to degrade scores:
  - Multi-service cascading failures (misleading root cause)
  - Security breach disguised as high CPU
  - Cross-region latency with ambiguous error codes
  - Race condition in distributed transactions
  - DNS intermittent failures causing auth errors
  - Clock skew causing JWT expiry false positives
  - Memory leak in sidecar (not main container)
  - Config drift between replicas causing split responses
  - Noisy neighbor on shared infra
  - Thundering herd after deployment

### Incident Schema
```json
{
  "id": "inc_001",
  "title": "Payment service P99 latency spike",
  "description": "...",
  "logs": "ERROR: connection pool timeout after 30s...",
  "metrics": {"p99_latency_ms": 2800, "error_rate": 0.18, "cpu_pct": 45},
  "service": "payment-service",
  "environment": "production",
  "category": "performance",
  "is_edge_case": false,
  "ground_truth": {
    "root_cause": "Database connection pool exhausted due to long-running queries",
    "severity": "P1",
    "remediation": ["Increase connection pool size", "Kill long-running queries", "Add read replica"]
  }
}
```

### Resolution Schema (stored in MongoDB)
```json
{
  "incident_id": "inc_001",
  "generation": 2,
  "root_cause": "...",
  "severity": "P1",
  "remediation_steps": ["...", "..."],
  "estimated_resolution_minutes": 30,
  "scores": {
    "root_cause_accuracy": 0.85,
    "remediation_quality": 0.70,
    "severity_accuracy": 1.0,
    "composite": 0.85
  },
  "timestamp": "2026-06-27T12:00:00Z"
}
```

### Generation Schema (Darwin lineage in MongoDB)
```json
{
  "generation_id": 3,
  "trigger": "score_degradation",
  "score_before": 0.42,
  "score_after": 0.74,
  "system_prompt": "...",
  "prompt_diff": "...",
  "failure_patterns": ["cascading_failure", "security_as_perf"],
  "incidents_that_failed": ["inc_081", "inc_085", "inc_089"],
  "timestamp": "2026-06-27T14:30:00Z"
}
```

---

## Project Structure

```
darwin-sre/
├── PLAN.md                    # This file
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile                 # For DO App Platform
│
├── data/
│   ├── generate_incidents.py  # Generates all 100 synthetic incidents
│   ├── incidents_training.json
│   └── incidents_production.json
│
├── agents/
│   ├── __init__.py
│   ├── sre_agent.py           # SRE agent: incident → resolution (Claude/DO)
│   └── judge.py               # Evaluator: resolution → scores (Gemini)
│
├── darwin/
│   ├── __init__.py
│   ├── loop.py                # Main Darwin orchestration loop
│   ├── mutator.py             # Prompt mutation: failures → improved prompt
│   └── storage.py             # MongoDB lineage store
│
├── api/
│   ├── __init__.py
│   ├── app.py                 # FastAPI app factory
│   └── routes.py              # /run /status /generations /incidents SSE
│
└── frontend/
    ├── index.html
    ├── app.js                 # Live score chart + incident feed + prompt diff
    └── style.css
```

---

## Layer Build Plan

### Layer 1 — Core Darwin Loop (~2h) ✦ MVP
Everything runs locally with `python main.py`. No server needed.

1. `data/generate_incidents.py` — LLM generates 100 realistic incidents
2. `agents/sre_agent.py` — calls DO inference, structured JSON output
3. `agents/judge.py` — Gemini scores each resolution on 3 criteria
4. `darwin/mutator.py` — reads failures, calls Haiku to rewrite prompt
5. `darwin/storage.py` — saves generations + resolutions to MongoDB
6. `darwin/loop.py` — orchestrates: stream incidents → score → detect drop → mutate → continue
7. `main.py` — entry point, prints live score table

**Definition of done:** running `python main.py` shows score table with at least one Darwin mutation triggered.

### Layer 2 — API + Live UI (~2h)
Deploy it, make it visual.

1. `api/app.py` + `api/routes.py` — FastAPI serving SSE stream of events
2. `frontend/` — vanilla JS + Chart.js:
   - Live score line (per incident, colored by generation)
   - Incident feed (title, score, pass/fail)
   - Darwin panel: fires, prompt diff, score before/after
3. `Dockerfile` — port 8080, gunicorn
4. Push to GitHub → DO App Platform deploy

### Layer 3 — Polish (if time)
- MongoDB Vector Search to skip mutations similar to past failures
- MiniMax as third model option (parallel evaluation)
- Better UI styling

---

## Judging Alignment

| Criterion | Weight | How we hit it |
|-----------|--------|---------------|
| Technicality | 40% | Eval loop + LLM-as-judge + prompt mutation + MongoDB lineage + streaming |
| Creativity | 25% | Agent rewrites its own system prompt autonomously — never done live |
| Live Demo | 20% | Score chart visibly climbs; Darwin fires on stage |
| Future Potential | 15% | Direct path to production SRE systems that never degrade |

---

## Scoring Criteria (Judge)

Gemini 3.5 Flash evaluates each resolution on:

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Root cause accuracy | 40% | Does the identified root cause match ground truth? |
| Remediation quality | 40% | Are the remediation steps correct, ordered, actionable? |
| Severity accuracy | 20% | Is the P1/P2/P3 severity correct? |

**Darwin trigger:** rolling 5-incident composite average drops below **0.60**
**Darwin success:** new prompt scores >= 0.10 higher on the triggering incidents

---

## Environment Variables

```bash
# DigitalOcean
DIGITAL_OCEAN_MODEL_ACCESS_KEY=...

# Google AI
GOOGLE_API_KEY=...

# MongoDB
MONGODB_URI=mongodb+srv://...
MONGODB_DB=darwin_sre

# Models
SRE_MODEL=claude-sonnet-4-5          # DO inference
MUTATOR_MODEL=claude-haiku-4-5       # DO inference (fast)
JUDGE_MODEL=gemini-2.0-flash         # Google AI

# Darwin config
DARWIN_TRIGGER_THRESHOLD=0.60
DARWIN_MAX_GENERATIONS=10
DARWIN_WINDOW_SIZE=5
```
