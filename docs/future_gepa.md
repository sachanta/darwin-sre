# Future Work — Replacing the Heuristic Mutator with DSPy + GEPA

> **Status: FUTURE / v2 direction. Not implemented in the hackathon build.**
>
> Darwin SRE (this repo) implements a **working closed self-improvement loop**: a
> heuristic Darwin agent detects degradation from Arize telemetry, writes a
> targeted Skill, replays the failing incidents, and validates the recovery —
> producing the eight-step staircase you see in the demo.
>
> This document is the **planned evolution** of that loop. The hand-rolled mutator
> in `darwin/mutator.py` (single-winner skill proposal) has a known weakness: it
> can silently regress incident classes it previously handled, because it has no
> Pareto view across families. The principled fix is to replace the mutator with
> **`dspy.GEPA`** — a reflective, Pareto-frontier prompt optimizer — using our
> existing Gemini judge as the `GEPAFeedbackMetric` and Claude Opus as the
> reflection LM.
>
> **Why it isn't in the hackathon build:** GEPA is a *batch offline optimizer* that
> emits a single compiled program over a trainset pulled from Arize. That model is
> excellent for production quality but does not produce the live, per-family,
> watch-it-recover staircase that is the heart of this demo. It also depends on the
> Arize trace-export path (deferred-index latency) and a Vijil genome-version write
> route we did not want on the critical path under a deadline. We chose to ship the
> working loop and document GEPA as the next step. See `docs/04_darwin_loop.md` for
> what we built; this is where it goes next.
>
> **What carries over unchanged:** Arize tracing, the Gemini judge (now as an
> optimization *metric*, not just a label), Vijil Genome versioning, Claude Sonnet
> as the task LM, and DigitalOcean infra. The design below is preserved as
> originally written.

---

## Original design brief

> **Goal:** Refactor the self-improving SRE agent so that prompt/skill evolution is driven by `dspy.GEPA` instead of the hand-rolled darwin mutator, while preserving Arize tracing, the Gemini judge, Vijil Genome versioning, Sonnet as the task LM, and DigitalOcean infra.

---

## 0. TL;DR for Claude Code

1. Wrap the SRE agent as a `dspy.ReAct` module with typed `dspy.Signature`s.
2. Replace the bespoke darwin-agent with `dspy.GEPA` using the existing Gemini Flash judge as the `GEPAFeedbackMetric`.
3. Use Claude Sonnet as `task_lm`, Claude Opus (or a higher-budget Sonnet) as `reflection_lm`, Gemini Flash as the metric/judge.
4. Pull low-score traces from Arize via API, convert to `dspy.Example` objects, feed as `trainset`.
5. Persist each compiled program as a new Vijil Genome version; promote via `/v1/genomes/{id}/to-agent-update` after a shadow eval.
6. Keep OpenInference instrumentation for Arize so all DSPy module + LiteLLM spans continue to land in the existing Arize project.
7. **Do not** introduce LangChain, LangGraph, CrewAI, or any other agent framework. DSPy + GEPA only.

---

## 1. Current Architecture (as built)

```
Incident ──► SRE Agent (Sonnet) ──► Trace ──► Arize
                                       │
                                       ▼
                          Gemini 2.5 Flash Judge
                          - scores trace
                          - annotates trace in Arize
                                       │
                          score < threshold
                                       ▼
                          darwin-agent (custom)
                          - analyzes failed traces
                          - proposes a new skill
                                       │
                                       ▼
                          Vijil Genome (version bump)
                                       │
                                       ▼
                          New SRE Agent deployed
```

**Pain points to fix:**

- Single-winner mutation by darwin-agent ⇒ silent regressions on previously-handled incident classes.
- Skill = opaque text blob ⇒ no addressable units to mutate surgically.
- No Pareto frontier ⇒ improvements on one class can erase improvements on another.
- Judge output is consumed only as a label, not as optimization feedback.
- Skill addition and tool-description tuning are separate manual processes.

---

## 2. Target Architecture

```
                      ┌──────────────────────────────────────┐
                      │  SRE Agent = dspy.ReAct(Sonnet)      │
                      │   - Signatures: typed I/O            │
                      │   - Tools: each has optimizable .desc│
                      │   - Compiled from Genome vN          │
                      └──────────────┬───────────────────────┘
                                     │ runs incident
                                     ▼
                      ┌──────────────────────────────────────┐
                      │ OpenInference → Arize span tree      │
                      │  (DSPy + LiteLLM auto-instrumented)  │
                      └──────────────┬───────────────────────┘
                                     ▼
                      ┌──────────────────────────────────────┐
                      │ Gemini 2.5 Flash Judge               │
                      │  → {score: float, feedback: str}     │
                      │  annotates Arize trace               │
                      └──────────────┬───────────────────────┘
                                     │ score < threshold
                                     ▼
                      ┌──────────────────────────────────────┐
                      │ Trace Puller (DO worker, cron)       │
                      │  Arize API → List[dspy.Example]      │
                      └──────────────┬───────────────────────┘
                                     ▼
                      ┌──────────────────────────────────────┐
                      │ dspy.GEPA.compile()                  │
                      │   task_lm     = Claude Sonnet        │
                      │   reflection  = Claude Opus          │
                      │   metric      = wraps Gemini judge   │
                      │   strategy    = pareto               │
                      │   auto        = "light" | "medium"   │
                      └──────────────┬───────────────────────┘
                                     ▼
                      ┌──────────────────────────────────────┐
                      │ Register compiled artifact           │
                      │  → Vijil Genome v(N+1)               │
                      │  → shadow eval vs vN                 │
                      │  → promote via /to-agent-update      │
                      └──────────────────────────────────────┘
```

---

## 3. Component Mapping (old → new)

| Concern | Today | After this refactor |
|---|---|---|
| Agent definition | Prompt strings + tool wrappers | `dspy.Signature` + `dspy.ReAct` |
| Skills | Opaque text blobs | `Tool.desc` strings + per-predictor instructions, both optimizable |
| Mutation engine | Custom darwin-agent | `dspy.GEPA` with reflection LM |
| Selection | Single winner | Pareto frontier across incident classes |
| Judge | Standalone labeler | `GEPAFeedbackMetric` returning `dspy.ScoreWithFeedback` |
| Trace store | Arize | Arize (unchanged; add DSPy OpenInference) |
| Versioning | Vijil Genome | Vijil Genome (each compiled program = a genome version) |
| Promotion | Manual | `/v1/genomes/{id}/to-agent-update` after shadow eval |
| Infra | DigitalOcean droplet/worker | Same; add a `gepa_compile_worker` service |

---

## 4. Repository Layout (create exactly this)

```
sre-agent/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── sre_agent/
│       ├── __init__.py
│       ├── config.py                  # env, model names, thresholds
│       ├── instrumentation.py         # Arize OpenInference setup (import-first)
│       ├── signatures.py              # all dspy.Signature classes
│       ├── tools.py                   # tool functions + dspy.Tool wrappers
│       ├── agent.py                   # build_agent() -> dspy.ReAct
│       ├── judge.py                   # Gemini Flash judge client + parsing
│       ├── metric.py                  # GEPAFeedbackMetric wrapping judge
│       ├── arize_puller.py            # pull low-score traces → dspy.Example
│       ├── optimize.py                # gepa.compile entrypoint
│       ├── genome.py                  # Vijil Genome client (save/load/promote)
│       ├── shadow_eval.py             # vN vs v(N+1) comparison
│       └── runtime.py                 # production agent loader (loads from genome)
├── workers/
│   ├── gepa_compile_worker.py         # cron/queue entrypoint on DO
│   └── promote_worker.py              # post-shadow-eval promotion
├── scripts/
│   ├── bootstrap_first_genome.py      # one-time: compile v1
│   └── manual_optimize.py             # ad-hoc CLI
└── tests/
    ├── test_signatures.py
    ├── test_metric.py
    ├── test_arize_puller.py
    └── test_shadow_eval.py
```

**Naming rules:**

- Module names lower_snake_case, class names PascalCase, Signatures suffixed `Signature` only if disambiguation is needed (otherwise plain noun, e.g., `IncidentTriage`).
- All env vars uppercase with `SRE_` prefix except third-party SDK conventions (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, `VIJIL_API_KEY`).

---

## 5. Dependencies

Add to `pyproject.toml`:

```toml
[project]
name = "sre-agent"
requires-python = ">=3.11"
dependencies = [
  "dspy>=3.2",
  "gepa>=0.4",
  "litellm>=1.50",
  "anthropic>=0.40",
  "google-generativeai>=0.8",
  "arize-otel>=0.7",
  "openinference-instrumentation-dspy>=0.1.20",
  "openinference-instrumentation-litellm>=0.1.10",
  "httpx>=0.27",
  "pydantic>=2.7",
  "python-dotenv>=1.0",
  "tenacity>=9.0",
]
```

> **Pin upper bounds only if CI fails.** Do not install LangChain/LangGraph/CrewAI/Mem0/MLflow — out of scope.

---

## 6. Environment Variables (`.env.example`)

```bash
# --- Model providers ---
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...               # Gemini Flash judge

# --- Models ---
SRE_TASK_LM=anthropic/claude-sonnet-4
SRE_REFLECTION_LM=anthropic/claude-opus-4
SRE_JUDGE_LM=gemini/gemini-2.5-flash

# --- Arize ---
ARIZE_SPACE_ID=...
ARIZE_API_KEY=...
ARIZE_PROJECT_NAME=sre-agent-prod
ARIZE_DEVELOPER_KEY=...          # for read API (trace puller)

# --- Vijil Genome ---
VIJIL_API_KEY=...
VIJIL_BASE_URL=https://api.vijil.ai
SRE_AGENT_GENOME_ID=<uuid>       # set after bootstrap

# --- Optimization ---
SRE_SCORE_THRESHOLD=0.7
SRE_GEPA_BUDGET=medium           # light | medium | heavy
SRE_GEPA_MAX_METRIC_CALLS=300
SRE_TRACE_PULL_WINDOW_HOURS=24
SRE_MIN_TRAINSET_SIZE=30
SRE_MAX_TRAINSET_SIZE=200
SRE_SHADOW_EVAL_SIZE=50
SRE_PROMOTION_DELTA=0.03         # +3% absolute required to promote
```

---

## 7. Phased Implementation

### Phase 1 — Wrap agent in DSPy, keep Arize traces flowing

**Acceptance criteria**

- Running `python -m sre_agent.runtime --incident-fixture tests/fixtures/incident_1.json` produces a DSPy ReAct trace in Arize with both `DSPy.*.forward` parent spans and `LiteLLM.*` child spans.
- No change in business behavior vs. current agent (parity test on 5 fixtures).

**`src/sre_agent/instrumentation.py`** — must be imported before any `dspy` import:

```python
import os
from arize.otel import register
from openinference.instrumentation.dspy import DSPyInstrumentor
from openinference.instrumentation.litellm import LiteLLMInstrumentor

def setup_tracing(project_name: str | None = None):
    tp = register(
        space_id=os.environ["ARIZE_SPACE_ID"],
        api_key=os.environ["ARIZE_API_KEY"],
        project_name=project_name or os.environ.get("ARIZE_PROJECT_NAME", "sre-agent"),
    )
    DSPyInstrumentor().instrument(tracer_provider=tp)
    LiteLLMInstrumentor().instrument(tracer_provider=tp)
    return tp
```

**`src/sre_agent/signatures.py`**:

```python
from typing import Literal
import dspy

class IncidentTriage(dspy.Signature):
    """Diagnose the most likely root cause of an SRE incident
    using the alert payload and recent telemetry. Output a concise
    hypothesis and the single best runbook to execute next."""
    alert: str = dspy.InputField(desc="Raw alert JSON or text from the monitoring system.")
    telemetry: str = dspy.InputField(desc="Recent metrics, logs, traces excerpts relevant to the alert.")
    service_topology: str = dspy.InputField(desc="Upstream/downstream dependencies of the impacted service.")
    hypothesis: str = dspy.OutputField(desc="One-paragraph root-cause hypothesis citing evidence.")
    runbook_id: Literal[
        "restart_pod","scale_out","rollback_deploy","failover_db",
        "drain_node","clear_cache","escalate_human","noop"
    ] = dspy.OutputField(desc="The single runbook to execute next.")
    confidence: float = dspy.OutputField(desc="Confidence in [0,1].")
```

**`src/sre_agent/tools.py`** — every `dspy.Tool` must have a precise `desc` (GEPA will optimize these):

```python
import dspy

def query_dynatrace(service: str, window_minutes: int = 30) -> str:
    """Return JSON string of metrics/logs/traces for `service` over the window."""
    ...

def exec_runbook(runbook_id: str, target: str, dry_run: bool = True) -> str:
    """Execute (or dry-run) a named runbook. Returns structured result."""
    ...

def lookup_dependency_graph(service: str) -> str:
    """Return upstream and downstream services with health summaries."""
    ...

TOOLS = [
    dspy.Tool(query_dynatrace,
              desc="Fetch recent telemetry for a service. Use BEFORE proposing a hypothesis."),
    dspy.Tool(lookup_dependency_graph,
              desc="Resolve service dependencies. Use when symptom may be cascading."),
    dspy.Tool(exec_runbook,
              desc="Run a runbook. Prefer dry_run=True unless confidence>=0.8."),
]
```

**`src/sre_agent/agent.py`**:

```python
import os
import dspy
from .signatures import IncidentTriage
from .tools import TOOLS

def configure_lms():
    dspy.configure(lm=dspy.LM(os.environ["SRE_TASK_LM"]))

def build_agent() -> dspy.ReAct:
    return dspy.ReAct(IncidentTriage, tools=TOOLS, max_iters=8)
```

---

### Phase 2 — Gemini judge as a GEPA feedback metric

**Acceptance criteria**

- `sre_metric(gold, pred)` returns a `dspy.ScoreWithFeedback` with `score in [0,1]` and a non-empty `feedback` string.
- Unit test asserts the metric runs without a `trace` argument (eval mode) and with one (GEPA mode).

**`src/sre_agent/judge.py`**:

```python
import os
import json
import google.generativeai as genai
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

class JudgeVerdict(BaseModel):
    score: float                # 0..1
    rationale: str              # short explanation
    missing_capabilities: list[str] = []   # e.g. ["redis cascade detection"]
    runbook_ok: bool = True
    hypothesis_ok: bool = True

JUDGE_PROMPT = """You are an SRE judge. Given the incident, the agent's hypothesis,
the chosen runbook, and the execution trace, return a JSON object matching this schema:
{ "score": float in [0,1],
  "rationale": str,
  "missing_capabilities": [str, ...],
  "runbook_ok": bool,
  "hypothesis_ok": bool }
Be terse and specific. Cite the exact step that failed."""

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def judge(incident: dict, prediction: dict, trace_text: str) -> JudgeVerdict:
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    model = genai.GenerativeModel(os.environ["SRE_JUDGE_LM"].split("/", 1)[-1])
    payload = {
        "incident": incident,
        "prediction": prediction,
        "trace": trace_text[:20000],   # cap
    }
    resp = model.generate_content([JUDGE_PROMPT, json.dumps(payload)])
    return JudgeVerdict.model_validate_json(resp.text)
```

**`src/sre_agent/metric.py`**:

```python
from typing import Optional
import dspy
from .judge import judge

def _trace_to_text(trace) -> str:
    if trace is None:
        return ""
    # DSPy trace is a list of (predictor, inputs, outputs) triples
    chunks = []
    for predictor, inputs, outputs in trace:
        chunks.append(f"[{getattr(predictor, 'signature', type(predictor).__name__)}]\n"
                      f"INPUTS: {inputs}\nOUTPUTS: {outputs}\n")
    return "\n".join(chunks)

def sre_metric(
    gold: dspy.Example,
    pred: dspy.Prediction,
    trace: Optional[list] = None,
    pred_name: Optional[str] = None,
    pred_trace: Optional[list] = None,
):
    incident = {"alert": gold.alert, "telemetry": gold.telemetry,
                "service_topology": gold.service_topology,
                "expected_runbook": getattr(gold, "expected_runbook", None)}
    prediction = {"hypothesis": pred.hypothesis,
                  "runbook_id": pred.runbook_id,
                  "confidence": pred.confidence}
    trace_text = _trace_to_text(pred_trace or trace)
    verdict = judge(incident, prediction, trace_text)
    feedback = (
        f"score={verdict.score:.2f}; "
        f"runbook_ok={verdict.runbook_ok}; "
        f"hypothesis_ok={verdict.hypothesis_ok}; "
        f"missing={verdict.missing_capabilities}; "
        f"rationale={verdict.rationale}"
    )
    return dspy.Prediction(score=verdict.score, feedback=feedback) \
        if False else dspy.ScoreWithFeedback(score=verdict.score, feedback=feedback)
```

> **Note for Claude Code:** if `dspy.ScoreWithFeedback` is not exported in the installed DSPy version, import via `from dspy.teleprompt.gepa import ScoreWithFeedback`. Add a fallback.

---

### Phase 3 — Arize trace puller → `dspy.Example` trainset

**Acceptance criteria**

- `python -m sre_agent.arize_puller --hours 24 --max 200` writes `data/trainset_<ts>.jsonl`.
- Each line deserializes to a `dspy.Example` with `.with_inputs("alert","telemetry","service_topology")`.
- Only traces with judge score `< SRE_SCORE_THRESHOLD` are included.

```python
# src/sre_agent/arize_puller.py
import os, json, time, httpx, argparse, pathlib
import dspy

ARIZE_GRAPHQL = "https://app.arize.com/graphql"  # Arize AX export endpoint; confirm in your tenant

def fetch_low_score_traces(hours: int, threshold: float, max_n: int) -> list[dict]:
    # Implementation: query Arize for spans in project, filter by annotation
    # judge.score < threshold within last `hours`. Return list of dicts.
    # NOTE: confirm exact Arize export API per tenant; this is a placeholder.
    raise NotImplementedError("Wire to Arize export API per tenant docs.")

def to_examples(rows: list[dict]) -> list[dspy.Example]:
    out = []
    for r in rows:
        ex = dspy.Example(
            alert=r["alert"],
            telemetry=r["telemetry"],
            service_topology=r["service_topology"],
            expected_runbook=r.get("expected_runbook"),
        ).with_inputs("alert", "telemetry", "service_topology")
        out.append(ex)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=int(os.environ.get("SRE_TRACE_PULL_WINDOW_HOURS", 24)))
    ap.add_argument("--max",   type=int, default=int(os.environ.get("SRE_MAX_TRAINSET_SIZE", 200)))
    ap.add_argument("--threshold", type=float, default=float(os.environ.get("SRE_SCORE_THRESHOLD", 0.7)))
    args = ap.parse_args()

    rows = fetch_low_score_traces(args.hours, args.threshold, args.max)
    examples = to_examples(rows)
    out_dir = pathlib.Path("data"); out_dir.mkdir(exist_ok=True)
    out = out_dir / f"trainset_{int(time.time())}.jsonl"
    with out.open("w") as f:
        for ex in examples:
            f.write(json.dumps(ex.toDict()) + "\n")
    print(f"wrote {len(examples)} examples → {out}")
```

> Claude Code: do **not** invent an Arize endpoint URL. Locate the correct export API in `arize/docs/export` (use Arize Python SDK if simpler) and wire it up. Keep `fetch_low_score_traces` as the only function that talks to Arize.

---

### Phase 4 — GEPA compile loop

**Acceptance criteria**

- `python -m sre_agent.optimize --trainset data/trainset_*.jsonl --out artifacts/agent_v{N}.json` produces a saved compiled program.
- Compile run is bounded by `SRE_GEPA_MAX_METRIC_CALLS`.
- Logs the Pareto frontier size and best score.

```python
# src/sre_agent/optimize.py
import os, glob, json, argparse, pathlib
import dspy
from .instrumentation import setup_tracing
from .agent import configure_lms, build_agent
from .metric import sre_metric

def load_trainset(globpat: str) -> list[dspy.Example]:
    files = sorted(glob.glob(globpat))
    examples = []
    for f in files:
        for line in open(f):
            d = json.loads(line)
            ex = dspy.Example(**d).with_inputs("alert", "telemetry", "service_topology")
            examples.append(ex)
    return examples

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trainset", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", default=os.environ.get("SRE_GEPA_BUDGET", "medium"))
    args = ap.parse_args()

    setup_tracing(project_name=f"{os.environ.get('ARIZE_PROJECT_NAME','sre-agent')}-gepa")
    configure_lms()
    student = build_agent()

    reflection_lm = dspy.LM(os.environ["SRE_REFLECTION_LM"], max_tokens=32000, temperature=1.0)

    tp = dspy.GEPA(
        metric=sre_metric,
        auto=args.budget,
        max_metric_calls=int(os.environ.get("SRE_GEPA_MAX_METRIC_CALLS", 300)),
        reflection_lm=reflection_lm,
        candidate_selection_strategy="pareto",
        track_stats=True,
        track_best_outputs=True,
        log_dir=os.environ.get("SRE_GEPA_LOG_DIR", "gepa_runs"),
    )

    train = load_trainset(args.trainset)
    if len(train) < int(os.environ.get("SRE_MIN_TRAINSET_SIZE", 30)):
        raise SystemExit(f"trainset too small: {len(train)}")

    # Split: 80% train, 20% val
    split = int(len(train) * 0.8)
    trainset, valset = train[:split], train[split:]

    optimized = tp.compile(student=student, trainset=trainset, valset=valset)
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    optimized.save(args.out)
    print(f"saved compiled program → {args.out}")
```

---

### Phase 5 — Vijil Genome integration

**Acceptance criteria**

- After compile, a new Genome version is created via Vijil's Darwin-proxy API.
- `genome.load_current()` returns a runnable `dspy.ReAct` that matches what was saved.

```python
# src/sre_agent/genome.py
import os, json, base64, httpx, pathlib
import dspy
from .agent import build_agent

BASE = os.environ["VIJIL_BASE_URL"]
HEAD = {"Authorization": f"Bearer {os.environ['VIJIL_API_KEY']}"}
GENOME_ID = os.environ["SRE_AGENT_GENOME_ID"]

def _client(): return httpx.Client(base_url=BASE, headers=HEAD, timeout=30.0)

def register_version(compiled_path: str, notes: dict) -> int:
    """Create a new genome version whose dome_genes carry the compiled DSPy JSON."""
    blob = pathlib.Path(compiled_path).read_text()
    payload = {
        "dome_genes": {
            "compiled_dspy_json_b64": base64.b64encode(blob.encode()).decode(),
            "notes": notes,
        }
    }
    with _client() as c:
        r = c.post(f"/v1/genomes/{GENOME_ID}/versions", json=payload)
        r.raise_for_status()
        return r.json()["version"]

def fetch_version(version: int | None = None) -> dict:
    with _client() as c:
        if version is None:
            r = c.get(f"/v1/genomes/{GENOME_ID}")
        else:
            r = c.get(f"/v1/genomes/{GENOME_ID}/versions/{version}")
        r.raise_for_status()
        return r.json()

def to_agent_update(version: int) -> dict:
    with _client() as c:
        r = c.get(f"/v1/genomes/{GENOME_ID}/to-agent-update",
                  params={"version": version})
        r.raise_for_status()
        return r.json()

def load_current() -> dspy.ReAct:
    g = fetch_version()
    blob = base64.b64decode(g["dome_genes"]["compiled_dspy_json_b64"]).decode()
    tmp = pathlib.Path("/tmp/_current_agent.json")
    tmp.write_text(blob)
    agent = build_agent()
    agent.load(str(tmp))
    return agent
```

> Claude Code: confirm exact Vijil request/response shapes against the docs in the Appendix. If `dome_genes` schema differs in the tenant, keep `compiled_dspy_json_b64` as a key and adjust the wrapper only.

---

### Phase 6 — Shadow eval + promotion

**Acceptance criteria**

- `shadow_eval.run(vN, vN_plus_1)` returns `{mean_old, mean_new, delta, per_class}` over `SRE_SHADOW_EVAL_SIZE` recent traces.
- Promotion only happens if `delta >= SRE_PROMOTION_DELTA` **and** no per-class regression > 5%.

```python
# src/sre_agent/shadow_eval.py
import os, statistics
import dspy
from .metric import sre_metric
from .arize_puller import fetch_low_score_traces, to_examples
from .genome import fetch_version
from .agent import build_agent

def _load(version: int) -> dspy.ReAct:
    import base64, pathlib
    g = fetch_version(version)
    blob = base64.b64decode(g["dome_genes"]["compiled_dspy_json_b64"]).decode()
    p = pathlib.Path(f"/tmp/_agent_v{version}.json"); p.write_text(blob)
    a = build_agent(); a.load(str(p)); return a

def run(vN: int, vN1: int) -> dict:
    n = int(os.environ.get("SRE_SHADOW_EVAL_SIZE", 50))
    rows = fetch_low_score_traces(hours=72, threshold=1.01, max_n=n)  # all recent
    examples = to_examples(rows)
    agent_old, agent_new = _load(vN), _load(vN1)

    old_scores, new_scores = [], []
    for ex in examples:
        po = agent_old(**ex.inputs())
        pn = agent_new(**ex.inputs())
        old_scores.append(sre_metric(ex, po).score)
        new_scores.append(sre_metric(ex, pn).score)

    mean_old = statistics.mean(old_scores) if old_scores else 0.0
    mean_new = statistics.mean(new_scores) if new_scores else 0.0
    return {"mean_old": mean_old, "mean_new": mean_new,
            "delta": mean_new - mean_old, "n": len(examples)}
```

```python
# workers/promote_worker.py
import os, sys
from sre_agent.shadow_eval import run
from sre_agent.genome import to_agent_update

def main(vN: int, vN1: int):
    result = run(vN, vN1)
    delta_required = float(os.environ.get("SRE_PROMOTION_DELTA", 0.03))
    print(result)
    if result["delta"] >= delta_required:
        upd = to_agent_update(vN1)
        print("PROMOTED:", upd)
    else:
        print("NOT PROMOTED")

if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]))
```

---

### Phase 7 — Worker on DigitalOcean

**Acceptance criteria**

- A systemd unit or DO App Platform worker runs `gepa_compile_worker.py` on a configurable cron (default: every 6h).
- Worker does: pull traces → if `>= SRE_MIN_TRAINSET_SIZE` low-score traces, run compile → register Genome version → trigger shadow eval → promote or abort.

```python
# workers/gepa_compile_worker.py
import os, subprocess, time, glob, json, pathlib
from sre_agent.genome import register_version, fetch_version

def latest_compiled() -> str | None:
    files = sorted(glob.glob("artifacts/agent_v*.json"))
    return files[-1] if files else None

def main():
    ts = int(time.time())
    train_path = f"data/trainset_{ts}.jsonl"

    subprocess.check_call(["python", "-m", "sre_agent.arize_puller"])
    files = sorted(glob.glob("data/trainset_*.jsonl"))
    if not files:
        print("no trainset"); return
    latest_train = files[-1]

    n = sum(1 for _ in open(latest_train))
    if n < int(os.environ.get("SRE_MIN_TRAINSET_SIZE", 30)):
        print(f"trainset too small ({n})"); return

    out = f"artifacts/agent_v{ts}.json"
    pathlib.Path("artifacts").mkdir(exist_ok=True)
    subprocess.check_call([
        "python", "-m", "sre_agent.optimize",
        "--trainset", latest_train, "--out", out,
    ])

    current = fetch_version()
    vN = current["version"]
    vN1 = register_version(out, notes={"trainset": latest_train, "size": n})
    print(f"registered genome v{vN1} (was v{vN})")

    subprocess.check_call(["python", "workers/promote_worker.py", str(vN), str(vN1)])

if __name__ == "__main__":
    main()
```

---

## 8. Explicit Claude Code Instructions

**Do**

1. Create files at the exact paths in §4. Do not refactor into a different layout.
2. Keep `instrumentation.setup_tracing()` called **before** any `dspy` import path that builds an agent at process startup.
3. Use `dspy.LM(...)` with the model strings from env vars. Do not hardcode model names elsewhere.
4. Treat `sre_metric` as the single source of truth for scoring; reuse it in optimize, shadow eval, and CI.
5. When wiring the Arize puller, locate the official Arize export API in the tenant docs and implement `fetch_low_score_traces` only. Leave the rest of the puller untouched.
6. Persist every compiled program as a Vijil Genome version. Never overwrite `artifacts/agent_v*.json` files.
7. Add a `Makefile` with targets: `install`, `lint`, `test`, `bootstrap`, `optimize`, `promote`, `worker`.
8. Add type hints and run `ruff check` + `mypy --strict` in CI.
9. Use `tenacity` for all external API calls (Arize, Vijil, Gemini).
10. Cap judge prompts at 20k chars; truncate from the middle of the trace if larger.

**Do not**

1. Do not introduce LangChain, LangGraph, CrewAI, Letta, Mem0, MLflow, or any other agent/memory framework.
2. Do not replace Sonnet with another model in the task LM. Reflection LM may be configurable.
3. Do not change the Arize project name without updating `ARIZE_PROJECT_NAME`.
4. Do not invent Vijil/Arize endpoint URLs — confirm against the links in the Appendix.
5. Do not call the judge from inside the agent loop. The judge is a post-hoc evaluator.
6. Do not bypass the shadow eval; promotion **must** be gated.
7. Do not store API keys in the repo. `.env.example` only.
8. Do not commit `data/*.jsonl`, `artifacts/*.json`, or `gepa_runs/`. Add to `.gitignore`.
9. Do not use `dspy.MIPROv2` or other optimizers; this project standardizes on `dspy.GEPA`.
10. Do not change `Tool.desc` strings manually after Phase 1 — let GEPA evolve them.

**Conventions**

- All modules are import-safe (no side effects at import time except `instrumentation.py` when explicitly called).
- All workers are idempotent and re-runnable.
- Logging via `logging.getLogger(__name__)`; JSON logs in production.
- Tests use `pytest`; fixtures live in `tests/fixtures/`.

---

## 9. Acceptance Criteria (overall)

1. ✅ Production SRE agent runs as `dspy.ReAct` and traces land in Arize.
2. ✅ Gemini Flash judge is the GEPA metric; verdicts include score + textual feedback.
3. ✅ Compile worker produces a new Vijil Genome version from low-score traces every cycle.
4. ✅ Shadow eval gates promotion; Pareto frontier prevents regressions.
5. ✅ One full cycle (trace → compile → genome → shadow → promote-or-abort) demonstrably completes on staging.
6. ✅ Documented runbook for rollback to a prior genome version via `to-agent-update`.

---

## 10. Risk & Rollback

| Risk | Mitigation |
|---|---|
| GEPA proposes an instruction that breaks tool calling | Shadow eval gate; per-class regression check |
| Judge drift gives misleading feedback | Pin judge model version; track inter-rater on a fixed eval set weekly |
| Reflection LM cost spike | Cap `max_metric_calls`; use `auto="light"` for first runs |
| Arize export schema changes | Isolate in `arize_puller.fetch_low_score_traces` only |
| Vijil schema drift in `dome_genes` | Wrap all access through `genome.py`; keep `compiled_dspy_json_b64` key stable |
| Trainset too small / biased | Enforce `SRE_MIN_TRAINSET_SIZE`; stratify by incident class if available |
| Tool side effects during optimize | All tools must honor `dry_run=True` during compile runs; enforce via env flag `SRE_OPTIMIZE_MODE=1` |

**Rollback procedure:** call `genome.to_agent_update(vN-1)` and restart the runtime; no rebuild required.

---

## 11. Sequenced Work Plan for Claude Code

1. Scaffold repo per §4; add `pyproject.toml`, `.env.example`, `.gitignore`, `Makefile`.
2. Implement Phase 1 (instrumentation, signatures, tools, agent, runtime). Add 5 parity fixtures.
3. Implement Phase 2 (judge, metric). Add unit tests.
4. Implement Phase 3 (arize_puller). Wire to the real Arize export API per tenant docs; add an integration test that uses a recorded fixture.
5. Implement Phase 4 (optimize). Run `auto="light"` end-to-end on a fixture trainset.
6. Implement Phase 5 (genome). Round-trip a compiled program through Vijil and back.
7. Implement Phase 6 (shadow_eval, promote_worker).
8. Implement Phase 7 (gepa_compile_worker). Add DO App Platform spec or systemd unit.
9. Add CI: ruff, mypy, pytest. Add make targets.
10. Write `docs/runbook.md` covering: promote, rollback, force-skip-cycle, debug judge disagreement.

---

# Appendix

## A. Reference snippets

### A.1 Minimal DSPy + GEPA example (from gepa-ai)

```python
import dspy
gepa = dspy.GEPA(
    metric=your_metric,
    max_metric_calls=150,
    reflection_lm=dspy.LM("anthropic/claude-opus-4", max_tokens=32000),
    candidate_selection_strategy="pareto",
)
optimized = gepa.compile(student=RAG(), trainset=trainset, valset=valset)
```

### A.2 GEPAFeedbackMetric signature

```python
def metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> "float | ScoreWithFeedback":
    ...
```

### A.3 Arize OpenInference setup (must run before `import dspy` in entrypoints)

```python
from arize.otel import register
from openinference.instrumentation.dspy import DSPyInstrumentor
from openinference.instrumentation.litellm import LiteLLMInstrumentor

tp = register(space_id=..., api_key=..., project_name="sre-agent-prod")
DSPyInstrumentor().instrument(tracer_provider=tp)
LiteLLMInstrumentor().instrument(tracer_provider=tp)
```

### A.4 Vijil Genome endpoints used

- `GET  /v1/genomes/{genome_id}` — current
- `GET  /v1/genomes/{genome_id}/versions/{version}` — specific version
- `POST /v1/genomes/{genome_id}/versions` — create a new version (tenant-confirm exact route)
- `GET  /v1/genomes/{genome_id}/to-agent-update?version={v}` — promotion payload

---

## B. Glossary

- **Signature** — DSPy class declaring typed inputs/outputs + a docstring instruction. GEPA evolves the instruction.
- **Predictor** — Concrete LM call (e.g., `dspy.Predict`, `dspy.ChainOfThought`, `dspy.ReAct`).
- **Module** — A composition of predictors. The compiled artifact is a module.
- **Tool** — A callable wrapped with `dspy.Tool(fn, desc=...)`. GEPA evolves `desc`.
- **GEPAFeedbackMetric** — A metric returning `ScoreWithFeedback` so the reflection LM can read *why* a run failed.
- **Reflection LM** — The model GEPA uses to read execution traces and propose instruction rewrites.
- **Pareto frontier** — Set of candidates none of which is dominated across all training examples; prevents regressions.
- **Genome** — A Vijil-managed versioned representation of the agent; in this project, the compiled DSPy JSON.
- **Shadow eval** — Run vN and vN+1 on the same recent inputs; compare via the same metric.

---

## C. Links

- DSPy framework — https://dspy.ai/
- DSPy GEPA API reference — https://dspy.ai/api/optimizers/GEPA/overview/
- DSPy GEPA advanced — https://dspy.ai/api/optimizers/GEPA/GEPA_Advanced/
- DSPy GEPA tutorials — https://dspy.ai/tutorials/gepa_ai_program/
- DSPy GitHub — https://github.com/stanfordnlp/dspy
- GEPA package — https://github.com/gepa-ai/gepa
- GEPA paper (arXiv 2507.19457) — https://arxiv.org/abs/2507.19457
- GEPA × DSPy integration deep-dive — https://deepwiki.com/gepa-ai/gepa/5.4-dspy-integration
- Optimize anything with GEPA (landing) — https://gepa-ai.github.io/gepa/
- Arize AX × DSPy tracing — https://arize.com/docs/ax/integrations/python-agent-frameworks/dspy/dspy-tracing
- Arize Phoenix × DSPy tracing — https://arize.com/docs/phoenix/integrations/python/dspy/dspy-tracing
- Self-improving agent w/ Phoenix + DSPy walkthrough — https://mytechexpertise.com/building-a-self-improving-agent-with-arize-phoenix-and-dspy/
- DSPy + GEPA on incident reports (Raja Patnaik) — https://www.rajapatnaik.com/blog/2025/10/14/dspy-gepa-deidentification
- LangStruct GEPA usage — https://langstruct.dev/examples/gepa/
- HF cookbook: DSPy GEPA — https://huggingface.co/learn/cookbook/dspy_gepa
- GEPA production traces issue (#178) — https://github.com/gepa-ai/gepa/issues/178
- Vijil Genome — Get version — https://docs.vijil.ai/api-reference/agent-environment/get-genome-version
- Vijil Genome — To agent update — https://docs.vijil.ai/api-reference/agent-environment/genome-to-agent-update

---

## D. Out of scope (explicitly)

- Memory frameworks (Letta/MemGPT, Mem0).
- Multi-agent orchestrators (CrewAI, AutoGen, LangGraph).
- Weight-level self-adaptation (SEAL).
- Self-modifying code agents (Darwin Gödel Machine).
- RL post-training stacks (Prime Intellect, verl, Open-AgentRL).
- Skill-library RL (SkillRL, SAGE).

Reintroduce later only if a) Pareto + GEPA can no longer improve the metric for ≥2 consecutive cycles, and b) a written ADR justifies the complexity.

---

*End of plan. Implement strictly as specified.*
