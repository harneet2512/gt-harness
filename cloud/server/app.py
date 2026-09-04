"""FastAPI application factory for the GT cloud coding agent."""
from __future__ import annotations

import os

# LiteLLM aborts a run when it cannot price a model, and the free OpenRouter
# models have no price entry. `LitellmModelConfig.cost_tracking` reads this at
# *class definition* time, so it must be set before `minisweagent.models.*` is
# first imported. The chain below (deps -> routes -> runner -> steerable_agent)
# reaches `minisweagent.agents.default`, so this line has to stay above it.
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")

from collections.abc import AsyncGenerator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from . import deps  # noqa: E402
from .auth import auth_router  # noqa: E402
from .events import EventBus  # noqa: E402
from .routes import router  # noqa: E402
from .runner import SessionRunner  # noqa: E402
from .store import SessionStore  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    store = SessionStore(os.environ.get("DB_PATH", "cloud_harness.db"))
    await store.init()
    event_bus = EventBus(store)
    deps.configure(store, event_bus, SessionRunner(store, event_bus))
    yield
    await store.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="GT Cloud Coding Agent",
        description="Internal cloud coding agent powered by the GT mini-SWE harness",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(router, prefix="/api")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
