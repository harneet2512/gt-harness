"""Application-scoped dependencies, populated by the app lifespan."""
from __future__ import annotations

from .events import EventBus
from .runner import SessionRunner
from .store import SessionStore

_store: SessionStore | None = None
_event_bus: EventBus | None = None
_runner: SessionRunner | None = None


def configure(store: SessionStore, event_bus: EventBus, runner: SessionRunner) -> None:
    global _store, _event_bus, _runner
    _store, _event_bus, _runner = store, event_bus, runner


def get_store() -> SessionStore:
    if _store is None:
        raise RuntimeError("app not initialised")
    return _store


def get_event_bus() -> EventBus:
    if _event_bus is None:
        raise RuntimeError("app not initialised")
    return _event_bus


def get_runner() -> SessionRunner:
    if _runner is None:
        raise RuntimeError("app not initialised")
    return _runner
