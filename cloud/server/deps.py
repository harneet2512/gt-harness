"""Application-scoped dependencies, populated by the app lifespan."""
from __future__ import annotations

from .events import EventBus
from .runner import SessionManager
from .store import SessionStore

_store: SessionStore | None = None
_event_bus: EventBus | None = None
_manager: SessionManager | None = None


def configure(
    store: SessionStore, event_bus: EventBus, manager: SessionManager
) -> None:
    global _store, _event_bus, _manager
    _store, _event_bus, _manager = store, event_bus, manager


def get_store() -> SessionStore:
    if _store is None:
        raise RuntimeError("app not initialised")
    return _store


def get_event_bus() -> EventBus:
    if _event_bus is None:
        raise RuntimeError("app not initialised")
    return _event_bus


def get_manager() -> SessionManager:
    if _manager is None:
        raise RuntimeError("app not initialised")
    return _manager
