import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.config import CORS_ORIGINS
from app.loop import LoopState, build_default_worker, run_worker, shutdown_worker
from app.routes.jobs import router as jobs_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop the background worker task based on config.ENABLE_WORKER.

    Reads ENABLE_WORKER from the config *module* at lifespan time so that
    tests can monkeypatch config.ENABLE_WORKER before entering the lifespan.
    """
    if config.ENABLE_WORKER:
        closures = build_default_worker()
        stop_event = asyncio.Event()
        state = LoopState()
        task = asyncio.create_task(
            run_worker(
                stop_event,
                claim=closures["claim"],
                run_step=closures["run_step"],
                sweep=closures["sweep"],
                state=state,
            ),
            name="worker",
        )
        yield
        await shutdown_worker(task, stop_event, state, closures["release"])
    else:
        yield


app = FastAPI(title="Audio Notes API", lifespan=lifespan)

# Browser calls from the Vercel site / localhost. X-Session-Id is a custom header, so it
# triggers a preflight on every API call and MUST be listed here (Context.md 16.1).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-Session-Id", "Content-Type"],
)

app.include_router(jobs_router)


@app.get("/health")
async def health():
    return {"ok": True}
