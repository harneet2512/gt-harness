"""FastAPI application factory for the GT cloud coding agent."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import auth_router
from .events import EventBus
from .routes import router
from .runner import SessionRunner
from .store import SessionStore

_store: SessionStore | None = None
_event_bus: EventBus | None = None
_runner: SessionRunner | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _store, _event_bus, _runner
    db_path = os.environ.get("DB_PATH", "cloud_harness.db")
    _store = SessionStore(db_path)
    await _store.init()
    _event_bus = EventBus(_store)
    _runner = SessionRunner(_store, _event_bus)
    yield
    await _store.close()


def get_store() -> SessionStore:
    assert _store is not None
    return _store


def get_event_bus() -> EventBus:
    assert _event_bus is not None
    return _event_bus


def get_runner() -> SessionRunner:
    assert _runner is not None
    return _runner


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

    app.dependency_overrides[SessionStore] = get_store
    app.dependency_overrides[EventBus] = get_event_bus
    app.dependency_overrides[SessionRunner] = get_runner

    app.include_router(auth_router)
    app.include_router(router, prefix="/api")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
