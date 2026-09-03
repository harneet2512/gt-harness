from __future__ import annotations

import hashlib
import inspect

from scripts.miniswe_gt_run import build_agent, resolve_run_task_identity

TASK = "arktype-json-schema-refs-dependencies"
TEXT = "Fix the failing reference resolution."


def _digest(task_text: str) -> str:
    return hashlib.sha256(task_text.encode("utf-8")).hexdigest()[:16]


def test_canonical_identity_is_used_when_supplied():
    assert resolve_run_task_identity(TASK, TEXT) == TASK


def test_canonical_identity_is_normalized():
    assert resolve_run_task_identity(f"  {TASK}\n", TEXT) == TASK


def test_digest_is_only_a_fallback_for_unbound_callers():
    """The workflow always binds --task-id; ad-hoc callers keep the old value."""

    assert resolve_run_task_identity("", TEXT) == _digest(TEXT)
    assert resolve_run_task_identity("   ", TEXT) == _digest(TEXT)


def test_digest_never_shadows_a_supplied_identity():
    """HAR-81: the digest is not the planned task, so it must never win.

    Diagnostics, the external state store, and the reproducibility manifest are
    all addressed by this value. When the digest shadowed a supplied identity,
    attestation reported the planned task as missing and the digest as an
    unplanned task in the same run.
    """

    assert resolve_run_task_identity(TASK, TEXT) != _digest(TEXT)


def test_build_agent_accepts_the_canonical_identity():
    assert "canonical_task_id" in inspect.signature(build_agent).parameters
