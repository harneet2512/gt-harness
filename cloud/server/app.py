"""FastAPI application factory for the GT cloud coding agent."""
from __future__ import annotations

import os

# LiteLLM aborts a run when it cannot price a model, and the free OpenRouter
# models have no price entry. `LitellmModelConfig.cost_tracking` reads this at
# *class definition* time, so it must be set before `minisweagent.models.*` is
# first imported. The chain below (deps -> routes -> runner ->
# conversational_agent) reaches `minisweagent.agents.default`, so this line has
# to stay above it.
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")

import asyncio  # noqa: E402
from collections.abc import AsyncGenerator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from . import deps  # noqa: E402
from .auth import auth_router  # noqa: E402
from .events import EventBus  # noqa: E402
from .routes import router  # noqa: E402
from .runner import SessionManager  # noqa: E402
from .store import SessionStore  # noqa: E402


def build_sha() -> str:
    """The commit this image was built from, stamped in by the Dockerfile.

    ``cloud/deploy.sh`` exports ``BUILD_SHA`` from ``git rev-parse --short
    HEAD`` and compose passes it as a build arg. Without it there is no way to
    tell a running deployment from a stale image, which is exactly how the
    round-2 QA ran against a UI two commits behind the server.
    """
    return os.environ.get("BUILD_SHA", "") or "unknown"


def cors_origins() -> list[str]:
    """Explicit allow-list from ``CORS_ORIGINS``; empty by default.

    The UI is same-origin behind a proxy, so no origin is trusted unless one is
    named. ``allow_origins=["*"]`` with credentials is never valid.
    """
    raw = os.environ.get("CORS_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    store = SessionStore(os.environ.get("DB_PATH", "cloud_harness.db"))
    await store.init()
    event_bus = EventBus(store)
    manager = SessionManager(store, event_bus)
    deps.configure(store, event_bus, manager)
    await manager.recover()
    manager.start_reaper(asyncio.get_running_loop())
    try:
        yield
    finally:
        await manager.stop_reaper()
        await store.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="GT Cloud Coding Agent",
        description="Internal cloud coding agent powered by the GT mini-SWE harness",
        version="0.2.0",
        lifespan=lifespan,
    )
    origins = cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        )
    app.include_router(auth_router)
    app.include_router(router, prefix="/api")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "commit": build_sha()}

    return app


app = create_app()
