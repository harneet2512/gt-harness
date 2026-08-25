"""Isolate Mini-SWE 2.4.6 import-time environment side effects."""

from __future__ import annotations

import os

_ORIGINAL_ENVIRONMENT = dict(os.environ)
_RESTORED = False
os.environ["MSWEA_SILENT_STARTUP"] = "1"


def restore_environment() -> None:
    """Remove values loaded from Mini-SWE's user .env and restore the caller."""
    global _RESTORED
    if _RESTORED:
        return
    for key in tuple(os.environ):
        if key not in _ORIGINAL_ENVIRONMENT:
            os.environ.pop(key, None)
    for key, value in _ORIGINAL_ENVIRONMENT.items():
        os.environ[key] = value
    _ORIGINAL_ENVIRONMENT.clear()
    _RESTORED = True


__all__ = ["restore_environment"]
