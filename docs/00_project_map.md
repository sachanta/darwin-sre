# Darwin SRE — Intern Onboarding Guide

Welcome. This project was built solo at the AIEWF Hackathon 2026 (June 27–28, Shack15 SF).
Theme: **Recursive Self-Improvement.** Deadline: 12 hours from now when you're reading this.

This guide is split into five concepts. Read them in order.

---

## The Five Concepts

| # | Concept | What it answers |
|---|---------|----------------|
| [01](01_problem_and_vision.md) | **Problem & Vision** | Why does this exist? What is "Darwin SRE"? What problem does it solve? |
| [02](02_data_architecture.md) | **Data Architecture** | What data does the system use? How is it structured? What are the 8 failure families? |
| [03](03_agent_pipeline.md) | **The Agent Pipeline** | What happens when an incident arrives? Walk through every step, every service. |
| [04](04_darwin_loop.md) | **The Darwin Loop** | How does the system improve itself? What triggers evolution? What changes? |
| [05](05_platform_and_deploy.md) | **Platform & Deploy** | FastAPI, the supervisor UI, Arize observability, DO deploy, and how the demo works. |

---

## Project at a Glance

```
Incident arrives
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│  VIJIL DOME  ←── input guardrail (prompt injection check)   │
└──────────────────────┬──────────────────────────────────────┘
                       │ allowed
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  RAG RETRIEVAL  ←── Voyage AI embed → Atlas $vectorSearch   │
│  "Relevant Runbooks" injected into SRE Agent context        │
└──────────────────────┬──────────────────────────────────────┘
                       │ retrieved KB articles
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  SRE AGENT  ←── Claude 4.5 Sonnet via DigitalOcean          │
│  Produces: root_cause, severity, remediation_steps          │
└──────────────────────┬──────────────────────────────────────┘
                       │ resolution
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  VIJIL DOME  ←── output guardrail (secrets / moderation)    │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  GEMINI JUDGE  ←── Gemini 3.5 Flash                         │
│  Scores: root_cause_accuracy, remediation_quality,          │
│          severity_accuracy → composite [0.0–1.0]            │
│  Span exported to Arize AX                                  │
└──────────────────────┬──────────────────────────────────────┘
                       │ composite < 0.60 (rolling window)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  DARWIN LOOP  ←── degradation detected via Arize trace      │
│  1. ALERT raised                                            │
│  2. Mutate SRE prompt  (Claude Haiku / DO)                  │
│  3. Write new KB article  (Claude Sonnet / DO)              │
│  4. Embed + index new KB  (Voyage AI → Atlas)               │
│  5. REPLAY failing incidents through full pipeline          │
│  6. Validate: score_after ≥ score_before → ALERT resolved   │
│  7. Vijil genome snapshot of the evolved agent              │
└─────────────────────────────────────────────────────────────┘
```

## Repo Layout

```
darwin-sre/
├── agents/
│   ├── sre_agent.py       # Claude 4.5 Sonnet — resolves incidents
│   └── judge.py           # Gemini 3.5 Flash — scores resolutions
├── darwin/
│   ├── loop.py            # DarwinLoop — orchestrates everything
│   ├── mutator.py         # Claude Haiku — rewrites the SRE prompt
│   ├── storage.py         # MongoDB read/write helpers
│   ├── retrieval.py       # Voyage embed + Atlas $vectorSearch (Phase B)
│   ├── degradation.py     # Reads Arize traces → rolling quality signal
│   ├── arize_client.py    # Arize AX experiments + annotation configs
│   ├── vijil_dome.py      # Runtime input/output guardrails
│   └── vijil_genome.py    # Vijil agent registration + genome lineage
├── api/
│   ├── app.py             # FastAPI app
│   └── routes.py          # REST + SSE endpoints
├── data/
│   ├── generate_incidents.py  # Synthetic incident + log + KB generator
│   ├── generate_logs.py       # Log repair script (batched)
│   ├── incidents_training.json
│   ├── incidents_production.json
│   ├── logs.json
│   └── knowledge_base.json
├── frontend/
│   ├── index.html         # Supervisor mission-control UI
│   ├── app.js
│   └── style.css
├── scripts/
│   ├── setup_atlas_vector.py  # Embed KB + create vector index
│   └── setup_arize.py         # Golden dataset + evaluators
├── tests/
│   ├── conftest.py
│   ├── test_data.py       # Phase A
│   ├── test_storage.py    # Phase B
│   ├── test_retrieval.py  # Phase B
│   ├── test_rag.py        # Phase C
│   ├── test_degradation.py# Phase C
│   ├── test_selfheal.py   # Phase D
│   ├── test_api.py        # Phase E
│   └── test_frontend_smoke.py # Phase F
├── config.py
├── observability.py       # Arize OTel setup
├── main.py                # Entry point for autonomous capture run
└── pyproject.toml
```

## Sponsor Tech Used

| Sponsor | What it does in this project |
|---------|------------------------------|
| DigitalOcean | Inference endpoint (Claude Sonnet + Haiku) + App Platform deploy |
| Google DeepMind | Gemini 3.5 Flash judge — scores every resolution |
| MongoDB Atlas | Stores everything + Vector Search for KB retrieval |
| Voyage AI | `voyage-3` embeddings (1024-dim) for KB articles |
| Arize AX | Traces every inference call; degradation detected by reading span scores back |
| Vijil | Dome runtime guardrails; genome lineage tracking of prompt evolution |
