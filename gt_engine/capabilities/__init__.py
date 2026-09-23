"""Canonical capability API: thin facades over existing implementations.

No new analysis lives here — each function delegates to the engine/session
surface that already owns the state (plan item D; freshness landed with C5).
"""
