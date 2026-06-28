"""Darwin SRE — API route handlers."""
import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(val: Any) -> str:
    """Normalize a timestamp to ISO string."""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc).isoformat() if val.tzinfo is None else val.isoformat()
    return str(val)


def _serialize(doc: dict) -> dict:
    """Deep-convert non-serializable types (datetime, ObjectId) to strings."""
    out = {}
    for k, v in doc.items():
        if isinstance(v, datetime):
            out[k] = _ts(v)
        elif hasattr(v, "__str__") and type(v).__name__ == "ObjectId":
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        elif isinstance(v, list):
            out[k] = [_serialize(i) if isinstance(i, dict) else (_ts(i) if isinstance(i, datetime) else i) for i in v]
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
def health():
    return {"status": "ok", "service": "darwin-sre"}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@router.get("/runs")
def list_all_runs():
    from darwin.storage import list_runs
    runs = [_serialize(r) for r in list_runs()]
    return {"runs": runs}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    from darwin.storage import get_run
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return _serialize(run)


@router.get("/runs/{run_id}/timeline")
def get_timeline(run_id: str):
    """Return merged, chronologically-ordered event list for UI replay.

    Events: incident_resolved | alert_raised | alert_resolved | darwin_complete
    Each carries a 'timestamp' ISO string so the UI can sort and animate.
    """
    from darwin.storage import get_run, get_recent_resolutions, get_all_generations, get_all_alerts

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    events: list[dict] = []

    # Resolution events
    for r in get_recent_resolutions(limit=2000, run_id=run_id):
        events.append({
            "type": "incident_resolved",
            "timestamp": _ts(r.get("timestamp")),
            "incident_id": r["incident_id"],
            "scores": r.get("scores", {}),
            "generation": r.get("generation", 0),
            "retrieved_kb_ids": r.get("retrieved_kb_ids", []),
        })

    # Alert events
    for a in get_all_alerts(run_id=run_id):
        events.append({
            "type": "alert_raised",
            "timestamp": _ts(a.get("raised_at")),
            "alert_id": a.get("_id", ""),
            "rolling_avg": a.get("rolling_avg", 0.0),
            "window_scores": a.get("window_scores", []),
            "status": a.get("status", "open"),
        })
        # If alert resolved, also emit a resolved marker at the same time as the generation
        if a.get("status") == "resolved" and a.get("resolved_at"):
            events.append({
                "type": "alert_resolved",
                "timestamp": _ts(a.get("resolved_at")),
                "alert_id": a.get("_id", ""),
            })

    # Darwin evolution events
    for g in get_all_generations(run_id=run_id):
        events.append({
            "type": "darwin_complete",
            "timestamp": _ts(g.get("timestamp")),
            "generation": g.get("generation_id", 0),
            "score_before": g.get("score_before", 0.0),
            "score_after": g.get("score_after", 0.0),
            "new_kb_article_id": g.get("new_kb_article_id"),
            "failure_patterns": g.get("failure_patterns", []),
            "prompt_diff": g.get("prompt_diff", ""),
        })

    # Problem ticket events — use resolution timestamps as proxies
    from darwin.storage import get_all_problem_tickets
    for t in get_all_problem_tickets():
        events.append({
            "type": "problem_ticket_created",
            "timestamp": _ts(t.get("created_at")),
            "ticket_id": t.get("id"),
            "skill_id": t.get("skill_id"),
            "skill_name": t.get("skill_name"),
            "skill_tags": t.get("skill_tags", []),
            "use_count": t.get("use_count"),
            "summary": t.get("summary"),
            "recommended_action": t.get("recommended_action"),
        })

    events.sort(key=lambda e: e["timestamp"])
    return {"run_id": run_id, "events": events, "run": _serialize(run)}


# ---------------------------------------------------------------------------
# Incident detail (joins log + KBs + resolution)
# ---------------------------------------------------------------------------

@router.get("/incidents/{incident_id}")
def get_incident_detail(incident_id: str):
    from darwin.storage import get_incident, get_log, get_resolution, get_kb_article

    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    log = get_log(incident["log_id"]) if incident.get("log_id") else None
    resolution = get_resolution(incident_id)

    kb_articles = []
    if resolution and resolution.get("retrieved_kb_ids"):
        for kb_id in resolution["retrieved_kb_ids"]:
            art = get_kb_article(kb_id)
            if art:
                art.pop("embedding", None)  # never send embeddings to browser
                kb_articles.append(_serialize(art))

    return {
        "incident": _serialize(incident),
        "log": _serialize(log) if log else None,
        "kb_articles": kb_articles,
        "resolution": _serialize(resolution) if resolution else None,
    }


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------

@router.get("/knowledge")
def list_knowledge(source: str | None = None):
    from darwin.storage import get_all_kb_articles
    articles = get_all_kb_articles()
    if source:
        articles = [a for a in articles if a.get("source") == source]
    for a in articles:
        a.pop("embedding", None)
    return {"articles": [_serialize(a) for a in articles]}


@router.get("/knowledge/{article_id}")
def get_knowledge_article(article_id: str):
    from darwin.storage import get_kb_article
    art = get_kb_article(article_id)
    if not art:
        raise HTTPException(status_code=404, detail=f"Article '{article_id}' not found")
    art.pop("embedding", None)
    return _serialize(art)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@router.get("/alerts")
def list_alerts(run_id: str | None = None):
    from darwin.storage import get_all_alerts
    alerts = [_serialize(a) for a in get_all_alerts(run_id=run_id)]
    return {"alerts": alerts}


# ---------------------------------------------------------------------------
# Generations (Darwin lineage)
# ---------------------------------------------------------------------------

@router.get("/generations")
def list_generations(run_id: str | None = None):
    from darwin.storage import get_all_generations
    gens = [_serialize(g) for g in get_all_generations(run_id=run_id)]
    return {"generations": gens}


# ---------------------------------------------------------------------------
# Problem Tickets
# ---------------------------------------------------------------------------

@router.get("/problem-tickets")
def list_problem_tickets():
    from darwin.storage import get_all_problem_tickets
    tickets = [_serialize(t) for t in get_all_problem_tickets()]
    return {"tickets": tickets, "count": len(tickets)}


# ---------------------------------------------------------------------------
# Live run: POST /run  +  GET /stream/{run_id}
# ---------------------------------------------------------------------------

# Module-level registry: run_id → asyncio event loop + queue
# The main thread runs the Darwin loop; SSE reads from the queue.
_sse_queues: dict[str, asyncio.Queue] = {}
_sse_loops: dict[str, asyncio.AbstractEventLoop] = {}


def _start_run_background(run_id: str, loop: asyncio.AbstractEventLoop) -> None:
    """Run the Darwin autonomous loop in a thread, pushing events to SSE queue."""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    from darwin.storage import seed_incidents, seed_logs, create_run, finish_run, list_runs
    from darwin.loop import DarwinLoop
    from main import load_json, build_episode
    from pathlib import Path
    import json as _json
    import uuid
    from datetime import datetime, timezone

    DATA_DIR = Path(__file__).parent.parent / "data"

    try:
        training = load_json(DATA_DIR / "incidents_training.json")
        production = load_json(DATA_DIR / "incidents_production.json")
        logs_data = load_json(DATA_DIR / "logs.json")
    except Exception as exc:
        asyncio.run_coroutine_threadsafe(
            _sse_queues[run_id].put({"type": "error", "message": str(exc)}), loop
        )
        asyncio.run_coroutine_threadsafe(_sse_queues[run_id].put(None), loop)
        return

    seed_incidents(training + production)
    seed_logs(logs_data)
    create_run(run_id)

    def on_event(event: dict) -> None:
        asyncio.run_coroutine_threadsafe(_sse_queues[run_id].put(event), loop)

    normal_prod = [i for i in production if not i.get("is_edge_case")]
    corner_cases = [i for i in production if i.get("is_edge_case")]
    episode = build_episode(normal_prod, corner_cases, washout_pool=training)

    prod_loop = DarwinLoop(on_event=on_event, run_id=run_id)
    results = prod_loop.run(episode, register_vijil=False)

    final_avg = sum(r["scores"]["composite"] for r in results) / len(results) if results else 0.0
    finish_run(
        run_id=run_id,
        num_generations=prod_loop.generation,
        baseline_avg=0.0,
        final_avg=final_avg,
        episode_order=[i["id"] for i in episode],
    )

    asyncio.run_coroutine_threadsafe(_sse_queues[run_id].put(None), loop)


@router.post("/run")
async def start_run(background_tasks: BackgroundTasks):
    """Start a new autonomous Darwin run. Returns run_id immediately; stream via GET /stream/{run_id}."""
    from datetime import datetime, timezone
    import uuid
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    loop = asyncio.get_event_loop()
    _sse_queues[run_id] = asyncio.Queue()
    _sse_loops[run_id] = loop
    background_tasks.add_task(
        lambda: threading.Thread(
            target=_start_run_background, args=(run_id, loop), daemon=True
        ).start()
    )
    return {"run_id": run_id, "stream_url": f"/stream/{run_id}"}


@router.get("/stream/{run_id}")
async def stream_run(run_id: str):
    """SSE stream — emits one JSON event per line, ends with 'data: [DONE]'."""
    if run_id not in _sse_queues:
        raise HTTPException(status_code=404, detail=f"No active stream for run '{run_id}'")

    queue = _sse_queues[run_id]

    async def generate():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield "data: {\"type\": \"heartbeat\"}\n\n"
                continue
            if event is None:
                yield "data: [DONE]\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
