"""Darwin SRE — FastAPI application factory."""
import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import router

# Per-run SSE queue: run_id → asyncio.Queue[dict | None]
# None is the sentinel that signals the stream is finished.
_run_queues: dict[str, asyncio.Queue] = {}


def get_or_create_queue(run_id: str) -> asyncio.Queue:
    if run_id not in _run_queues:
        _run_queues[run_id] = asyncio.Queue()
    return _run_queues[run_id]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Darwin SRE Supervisor",
        version="1.0",
        description="Autonomous SRE agent with recursive self-improvement",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    # Serve frontend static files at root (must be last — catches everything)
    frontend = Path(__file__).parent.parent / "frontend"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=str(frontend), html=True), name="frontend")

    return app


app = create_app()
