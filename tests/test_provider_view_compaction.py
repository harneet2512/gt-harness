"""Stale-read/validation elision and typed recap receipt contract tests.

Both mechanisms are deterministic, typed-ledger-only, and fire exclusively
inside a compaction epoch (only when the provider view exceeds its trigger).
Elided bodies are replaced whole by typed markers; cleared bodies become
either a bounded typed recap receipt (atomic, 200-char cap) or the historical
bare hash receipt byte-for-byte.  Assistant messages are never rewritten.
"""

from __future__ import annotations

import copy
import hashlib

from gt_engine.central_runtime import CentralFeatureRuntime, WorkspaceTransition
from gt_engine.provider_view import (
    ProviderViewSession,
    _assemble_recap_text,
    _recent_read_observations,
    _turn_semantic_parts,
    build_provider_view,
)


def _turn(command: str, output: str, *, index: int, returncode: int = 0) -> list[dict]:
    tool_id = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": "act",
            "extra": {"actions": [{"command": command, "tool_call_id": tool_id}]},
        },
        {
            "role": "tool",
            "tool_call_id": tool_id,
            "content": output,
            "extra": {"raw_output": output, "returncode": returncode},
        },
    ]


def _history(*turns) -> list[dict]:
    messages = [{"role": "user", "content": "task"}]
    for index, (command, output, returncode) in enumerate(turns):
        messages.extend(_turn(command, output, index=index, returncode=returncode))
    return messages


def _read_observation(
    path: str,
    output: str,
    *,
    source_revision: str,
    returncode: int = 0,
) -> dict:
    return {
        "path": path,
        "start_line": 1,
        "end_line": None,
        "whole_file": True,
        "source_revision": source_revision,
        "workspace_revision": "w",
        "action_id": 1,
        "returncode": returncode,
        "output_hash": hashlib.sha256(
            (output or "").encode("utf-8", "replace")
        ).hexdigest(),
        "content_mapped": False,
        "observation_kind": "read",
    }


def test_stale_read_body_elided_after_reread_at_current_revision():
    old_body = "A" * 40_000
    new_body = "B" * 100
    messages = _history(
        ("cat /app/src/app.py", old_body, 0),
        ("sed -i s/old/new/ /app/src/app.py", "edited", 0),
        ("cat /app/src/app.py", new_body, 0),
    )
    active_state = {
        "source_revision": "s2",
        "recent_reads": [
            _read_observation("/app/src/app.py", old_body, source_revision="s1"),
            _read_observation("/app/src/app.py", new_body, source_revision="s2"),
        ],
    }

    view, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=2,
    )

    joined = " ".join(str(m.get("content") or "") for m in view)
    assert metrics.stale_reads_elided == 1
    assert "[Superseded read result cleared:" in joined
    assert "path=/app/src/app.py" in joined
    assert "revision=s1 reread_revision=s2" in joined
    assert old_body not in joined
    assert new_body in joined
    assert metrics.unique_assistant_reasoning_chars_removed == 0
    assistant_messages = [m for m in view if m.get("role") == "assistant"]
    assert all(m["content"] == "act" for m in assistant_messages)


def test_newest_read_not_elided_without_newer_typed_observation():
    body = "C" * 40_000
    messages = _history(
        ("cat /app/src/app.py", body, 0),
        ("sed -i s/x/y/ /app/src/app.py", "edited", 0),
        ("wc -l /app/src/app.py", "42 /app/src/app.py", 0),
    )
    active_state = {
        "source_revision": "s2",
        "recent_reads": [
            _read_observation("/app/src/app.py", body, source_revision="s2"),
        ],
    }

    view, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=2,
    )

    joined = " ".join(str(m.get("content") or "") for m in view)
    assert metrics.stale_reads_elided == 0
    assert "[Superseded read result cleared:" not in joined


def test_failed_validation_body_elided_after_pass_at_current_revision():
    failure_body = "F" * 40_000
    pass_body = "1 passed" * 100
    messages = _history(
        ("pytest -q", failure_body, 1),
        ("pytest -q", pass_body, 0),
    )
    active_state = {
        "source_revision": "s3",
        "latest_validation": {
            "command": "pytest -q",
            "returncode": 0,
            "source_revision": "s3",
        },
    }

    view, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=1,
    )

    joined = " ".join(str(m.get("content") or "") for m in view)
    assert metrics.stale_reads_elided == 1
    assert "[Superseded validation result cleared:" in joined
    assert "command_sha256=" in joined
    assert "passed_revision=s3" in joined
    assert failure_body not in joined
    assert pass_body in joined


def test_failed_validation_not_elided_while_failure_still_unresolved():
    failure_body = "F" * 40_000
    messages = _history(
        ("pytest -q", failure_body, 1),
        ("pytest -q", "1 passed", 0),
    )
    active_state = {
        "source_revision": "s3",
        "latest_validation": {
            "command": "pytest -q",
            "returncode": 0,
            "source_revision": "s3",
        },
        "unresolved_failure": {
            "command": "pytest -q",
            "diagnostic": "1 failed: assert x",
            "source_revision": "s3",
        },
    }

    _, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=1,
    )

    assert metrics.stale_reads_elided == 0


def test_failed_validation_not_elided_when_latest_run_failed():
    failure_body = "F" * 40_000
    messages = _history(
        ("pytest -q", failure_body, 1),
        ("pytest -q", failure_body, 1),
    )
    active_state = {
        "source_revision": "s3",
        "latest_validation": {
            "command": "pytest -q",
            "returncode": 1,
            "source_revision": "s3",
        },
    }

    _, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=1,
    )

    assert metrics.stale_reads_elided == 0


def test_cleared_read_body_becomes_typed_recap_receipt_without_command_text():
    body = "D" * 40_000
    messages = _history(("cat /app/src/app.py", body, 0), ("pytest -q", "ok", 0))
    active_state = {
        "source_revision": "s1",
        "recent_reads": [
            _read_observation("/app/src/app.py", body, source_revision="s1"),
        ],
    }

    view, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=1,
    )

    recap = next(
        m["content"]
        for m in view
        if str(m.get("content") or "").startswith("[Earlier tool result cleared:")
    )
    assert metrics.recap_receipts == 1
    assert "read /app/src/app.py@s1" in recap
    assert "command_sha256=" in recap
    assert "cat /app/src/app.py" not in recap
    assert "chars=" in recap and "sha256=" in recap
    assert metrics.recap_fallbacks == 0
    assert metrics.recap_chars_added > 0


def test_recap_atomic_overflow_falls_back_to_bare_receipt():
    body = "E" * 100
    bare = "[Earlier tool result cleared: chars=100 sha256=abc returncode=0.]"
    semantic = _turn_semantic_parts(
        {"extra": {"raw_output": body, "returncode": 0}},
        ("cat /app/src/app.py",),
        [_read_observation("/app/src/app.py", body, source_revision="s1")],
        None,
        "s1",
    )
    assert semantic

    overflow = _assemble_recap_text(
        ("cat /app/src/app.py",),
        {"extra": {"raw_output": body, "returncode": 0}},
        semantic,
        body,
        "abc",
        bare,
        cap=60,
    )
    assert overflow is None

    within = _assemble_recap_text(
        ("cat /app/src/app.py",),
        {"extra": {"raw_output": body, "returncode": 0}},
        semantic,
        body,
        "abc",
        bare,
        cap=200,
    )
    assert within is not None
    assert len(within) <= 200
    assert "read /app/src/app.py@s1" in within
    assert "cat /app/src/app.py" not in within


def test_below_trigger_view_remains_byte_identical():
    body = "G" * 10_000
    messages = _history(
        ("cat /app/src/app.py", body, 0),
        ("pytest -q", "1 failed", 1),
    )
    active_state = {
        "source_revision": "s1",
        "recent_reads": [
            _read_observation("/app/src/app.py", body, source_revision="s1"),
        ],
        "latest_validation": {
            "command": "pytest -q",
            "returncode": 1,
            "source_revision": "s1",
        },
    }
    original = copy.deepcopy(messages)

    view, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=10**18,
        target_chars=10**18,
        transform=True,
    )

    assert view == original
    assert metrics.stale_reads_elided == 0
    assert metrics.recap_receipts == 0
    assert metrics.recap_chars_added == 0
    assert metrics.recap_fallbacks == 0
    assert metrics.old_tool_results_cleared == 0


def test_compaction_epoch_receipt_records_elision_and_recap():
    old_body = "H" * 40_000
    new_body = "I" * 100
    messages = _history(
        ("cat /app/src/app.py", old_body, 0),
        ("sed -i s/a/b/ /app/src/app.py", "edited", 0),
        ("cat /app/src/app.py", new_body, 0),
        ("pytest -q", "ok", 0),
    )
    session = ProviderViewSession()
    active_state = {
        "source_revision": "s2",
        "recent_reads": [
            _read_observation("/app/src/app.py", old_body, source_revision="s1"),
            _read_observation("/app/src/app.py", new_body, source_revision="s2"),
        ],
    }

    _, metrics = session.compact(
        messages,
        active_state=active_state,
        target_chars=30_000,
        keep_recent_turns=1,
        trigger_tokens=900_000,
    )

    assert session.epoch == 1
    receipt = session.receipts[0]
    assert receipt.stale_reads_elided == metrics.stale_reads_elided == 1
    assert receipt.recap_receipts == metrics.recap_receipts
    assert receipt.recap_fallbacks == metrics.recap_fallbacks
    assert metrics.unique_assistant_reasoning_chars_removed == 0


def _ledger_read(
    path: str, output: str, *, source_revision: str, kind: str = "read"
) -> dict:
    return {
        "path": path,
        "start_line": 1,
        "end_line": None,
        "whole_file": True,
        "source_revision": source_revision,
        "workspace_revision": "w",
        "action_id": 1,
        "returncode": 0,
        "output_hash": hashlib.sha256(
            (output or "").encode("utf-8", "replace")
        ).hexdigest(),
        "content_mapped": False,
        "observation_kind": kind,
    }


def test_live_ledger_shape_fires_elision_via_read_history():
    """progress_ledger exposes current reads filtered AND full read_history.

    Elision must succeed when recent_reads holds only the current revision
    (the provider-visible frame contract) while read_history retains the
    old-revision read needed for hash identity.  Without read_history this
    would be the live-path dead-code defect the audit caught.
    """
    old_body = "J" * 40_000
    new_body = "K" * 100
    messages = _history(
        ("cat /app/src/app.py", old_body, 0),
        ("sed -i s/old/new/ /app/src/app.py", "edited", 0),
        ("cat /app/src/app.py", new_body, 0),
    )
    active_state = {
        "source_revision": "s2",
        "recent_reads": [
            _ledger_read("/app/src/app.py", new_body, source_revision="s2"),
        ],
        "read_history": [
            _ledger_read("/app/src/app.py", old_body, source_revision="s1"),
            _ledger_read("/app/src/app.py", new_body, source_revision="s2"),
        ],
    }

    view, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=2,
    )

    joined = " ".join(str(m.get("content") or "") for m in view)
    assert metrics.stale_reads_elided == 1
    assert "[Superseded read result cleared:" in joined
    assert "revision=s1 reread_revision=s2" in joined


def test_search_anchor_observations_never_authorize_elision():
    """Search-anchor ledger rows hash search output, not file bytes.

    A read body must never be elided using a search-anchor observation, even
    if their output hashes happen to collide.  The anchor row is filtered out
    of the elision corpus, so the stale read has no current-revision reread
    to authorize supersession.
    """
    body = "L" * 40_000
    messages = _history(
        ("cat /app/src/app.py", body, 0),
        ("grep -n def /app/src/app.py", "1: def main():", 0),
    )
    active_state = {
        "source_revision": "s2",
        "recent_reads": [],
        "read_history": [
            _ledger_read("/app/src/app.py", body, source_revision="s1"),
            _ledger_read(
                "/app/src/app.py", "1: def main():", source_revision="s2", kind="search_anchor"
            ),
        ],
    }

    _, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=1,
    )

    assert metrics.stale_reads_elided == 0


def test_recap_read_identity_works_with_live_filtered_ledger():
    """Cleared old read bodies gain typed recap identity from read_history.

    The recap receipt must name the read path@old-revision even when the
    provider-visible recent_reads frame is filtered to the current revision
    and no current reread exists (so the body is not superseded-elided but is
    still cleared under budget pressure in Phase B).
    """
    body = "M" * 40_000
    messages = _history(
        ("cat /app/src/app.py", body, 0),
        ("pytest -q", "ok", 0),
    )
    active_state = {
        "source_revision": "s2",
        "recent_reads": [],
        "read_history": [
            _ledger_read("/app/src/app.py", body, source_revision="s1"),
        ],
    }

    view, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=1,
    )

    recap = next(
        m["content"]
        for m in view
        if str(m.get("content") or "").startswith("[Earlier tool result cleared:")
    )
    assert metrics.recap_receipts == 1
    assert "read /app/src/app.py@s1" in recap
    assert "cat /app/src/app.py" not in recap


def _runtime_ledger(old_body: str, new_body: str) -> dict:
    runtime = CentralFeatureRuntime(enabled=True, model_visible=True)

    def transition(action_id: int, command: str, rev: str) -> WorkspaceTransition:
        return WorkspaceTransition(
            action_id=action_id,
            command=command,
            before_revision=rev,
            after_revision=rev,
        )

    runtime.observe_action(
        action_id=1,
        command="cat /app/src/app.py",
        output=old_body,
        returncode=0,
        transition=transition(1, "cat /app/src/app.py", "s1"),
        revision="w",
        source_revision="s1",
        snapshot=None,
    )
    runtime.observe_action(
        action_id=2,
        command="sed -i s/old/new/ /app/src/app.py",
        output="edited",
        returncode=0,
        transition=transition(2, "sed -i s/old/new/ /app/src/app.py", "s2"),
        revision="w",
        source_revision="s2",
        snapshot=None,
    )
    runtime.observe_action(
        action_id=3,
        command="cat /app/src/app.py",
        output=new_body,
        returncode=0,
        transition=transition(3, "cat /app/src/app.py", "s2"),
        revision="w",
        source_revision="s2",
        snapshot=None,
    )
    return runtime.progress_ledger()


def test_real_runtime_ledger_enables_elision_end_to_end():
    """The real CentralFeatureRuntime ledger (not a hand-built fixture) must
    expose read_history so stale-read elision can fire through the actual
    observe_action -> progress_ledger -> build_provider_view chain."""
    old_body = "N" * 40_000
    new_body = "O" * 100
    messages = _history(
        ("cat /app/src/app.py", old_body, 0),
        ("sed -i s/old/new/ /app/src/app.py", "edited", 0),
        ("cat /app/src/app.py", new_body, 0),
    )
    ledger = _runtime_ledger(old_body, new_body)

    assert [i["source_revision"] for i in ledger["recent_reads"]] == ["s2"]
    assert sorted(i["source_revision"] for i in ledger["read_history"]) == ["s1", "s2"]
    assert len(_recent_read_observations(ledger)) == 2

    view, metrics = build_provider_view(
        messages,
        active_state=ledger,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=2,
    )

    joined = " ".join(str(m.get("content") or "") for m in view)
    assert metrics.stale_reads_elided == 1
    assert (
        "[Superseded read result cleared: path=/app/src/app.py revision=s1 reread_revision=s2"
        in joined
    )
    assert old_body not in joined
    assert new_body in joined


def test_real_runtime_ledger_recap_read_identity():
    """A cleared real read body gains its typed read@revision recap identity
    from the runtime ledger's read_history even when recent_reads is filtered
    to the current revision."""
    body = "P" * 40_000
    messages = _history(
        ("cat /app/src/app.py", body, 0),
        ("pytest -q", "ok", 0),
    )
    runtime = CentralFeatureRuntime(enabled=True, model_visible=True)
    transition = WorkspaceTransition(
        action_id=1, command="cat /app/src/app.py", before_revision="s1", after_revision="s1"
    )
    runtime.observe_action(
        action_id=1,
        command="cat /app/src/app.py",
        output=body,
        returncode=0,
        transition=transition,
        revision="w",
        source_revision="s1",
        snapshot=None,
    )
    ledger = runtime.progress_ledger()

    view, metrics = build_provider_view(
        messages,
        active_state=ledger,
        trigger_chars=200,
        target_chars=150,
        keep_recent_turns=1,
    )

    recap = next(
        m["content"]
        for m in view
        if str(m.get("content") or "").startswith("[Earlier tool result cleared:")
    )
    assert metrics.recap_receipts == 1
    assert "read /app/src/app.py@s1" in recap
    assert "cat /app/src/app.py" not in recap