"""F.2 — runtime intelligence on the real producer path.

Every test exercises the production observation pipeline end to end:

* a certified fixture graph built by ``indexer.ensure_index`` (publication
  lock, revision store, manifest — an adoption-capable parent);
* a real :class:`EditTransaction` produced by the runtime's own
  ``capture_workspace``/``diff_workspace`` sensor over one edited file in a
  ``tmp_path`` copy — the checked-in fixture is never touched;
* the synchronous batch amend ``record_edit_transaction`` performs at the
  transaction boundary, read back through the journal and EngineState;
* a real ``python -m pytest`` invocation against the edited copy, recorded
  through ``compile_execution_evidence``/``record_execution_evidence`` and
  read back through the runtime capability facades;
* journal + CAS evidence verified byte-for-byte, never fabricated.

Where the host cannot perform a step (no producer, an amend refusal the
engine declines to escalate) the test skips or asserts the journaled
refusal — honest abstention is a pass, silent staleness is a failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("groundtruth.runtime.deterministic_queries")

from gt_engine.capabilities import change, freshness, runtime, structure
from gt_engine.runtime_observation import (
    capture_workspace,
    compile_execution_evidence,
    diff_workspace,
)
from tests.canonical.conftest import FIXTURE_REVISION, producer_candidates

_PRODUCER_MAY_EXIST = any(
    Path(candidate).is_file() for candidate in producer_candidates()
) or os.environ.get("GT_INDEX_BINARY")

pytestmark = pytest.mark.skipif(
    not _PRODUCER_MAY_EXIST,
    reason="no gt-index producer candidate exists on this host",
)

SERVER = "pyapp/server.py"
GO_MAIN = "gosvc/main.go"
FIXTURE_TEST = "pyapp/test_server.py"


# ---------------------------------------------------------------------------
# Shared real-path helpers
# ---------------------------------------------------------------------------


def _move_sanitize_call(root: Path) -> None:
    """Move the ``sanitize(raw)`` call from ``list_items`` into
    ``render_item`` — a one-file semantic edit the call graph must reflect.
    Same line count on both sides so line-keyed queries stay addressable."""
    path = root / SERVER
    before = path.read_text(encoding="utf-8")
    after = before.replace(
        "    cleaned = sanitize(raw)\n", "    cleaned = raw\n"
    )
    assert after != before, "fixture drifted: list_items sanitize call gone"
    moved = after.replace(
        '    payload = store.fetch(request.args.get("key"))\n',
        '    payload = store.fetch(sanitize(request.args.get("key")))\n',
    )
    assert moved != after, "fixture drifted: render_item fetch call changed"
    path.write_text(moved, encoding="utf-8")


def _transact_edit(workspace, *, action_id: int = 1):
    """capture -> real file edit -> capture -> diff -> record.

    The transaction is the runtime's own compiled record — not a hand-built
    object — so the journal/CAS/amend assertions read exactly what the
    engine wrote."""
    before = capture_workspace(workspace.root)
    _move_sanitize_call(workspace.root)
    after = capture_workspace(workspace.root)
    txn = diff_workspace(
        before, after, action_id=action_id, command="apply_patch"
    )
    workspace.adapter.record_edit_transaction(txn)
    return txn


def _amend_outcome(workspace) -> tuple[dict | None, dict | None]:
    """(adopted_row, refused_row) for the transaction-boundary amend."""
    adopted = workspace.journal_event("graph_sync_amend")
    refused = workspace.journal_event("graph_sync_amend_refused")
    return adopted, refused


def _require_amend_or_note_refusal(workspace) -> dict | None:
    """Return the adoption row, or assert the journaled refusal and return
    None. A host that cannot amend must still produce an honest journal —
    never a silent stale graph."""
    adopted, refused = _amend_outcome(workspace)
    assert adopted is not None or refused is not None, (
        "transaction-boundary amend left no journal row at all"
    )
    if adopted is not None:
        assert adopted["adopted"] is True
        return adopted
    assert refused.get("reason"), "amend refusal must name its reason"
    assert workspace.adapter.engine_state.graph_current is False, (
        "amend refused yet graph claims to be current — silent staleness"
    )
    return None


def _run_fixture_pytest(workspace, *, action_id: int, revision: str):
    """Run the fixture's real pytest file against the (possibly edited)
    workspace copy, compile + record the execution evidence, return the
    recorded :class:`ExecutionEvidence`."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workspace.root) + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    # The recorded command string uses the interpreter's basename: the
    # certified classifier parses the invocation surface and an absolute
    # Windows path mangles it — argv[0] really is python.exe.
    python = Path(sys.executable).name
    command = f"{python} -m pytest {FIXTURE_TEST} -x -q"
    run = subprocess.run(
        [sys.executable, "-m", "pytest", FIXTURE_TEST, "-x", "-q"],
        cwd=workspace.root,
        env=env,
        capture_output=True,
        timeout=180,
    )
    output = (run.stdout + run.stderr).decode("utf-8", "replace")
    evidence = compile_execution_evidence(
        command=command,
        output=output,
        returncode=run.returncode,
        action_id=action_id,
        repository_revision=revision,
        raw_output=run.stdout + run.stderr,
    )
    assert evidence is not None, (
        "pytest invocation did not classify as executable evidence"
    )
    workspace.adapter.record_execution_evidence(evidence, command)
    return evidence


def _failing_canary(workspace, *, action_id: int, revision: str):
    """A real failing pytest run over a canary test written into the
    workspace copy (never the fixture) — produces a genuine failure
    fingerprint from real output."""
    canary = workspace.root / "pyapp" / "test_canary_fails.py"
    canary.write_text(
        "def test_canary_fails():\n    assert 1 == 2, 'canary failure'\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workspace.root) + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    python = Path(sys.executable).name
    command = f"{python} -m pytest pyapp/test_canary_fails.py -x -q"
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "pyapp/test_canary_fails.py",
         "-x", "-q"],
        cwd=workspace.root,
        env=env,
        capture_output=True,
        timeout=180,
    )
    output = (run.stdout + run.stderr).decode("utf-8", "replace")
    assert run.returncode != 0, "canary test unexpectedly passed"
    evidence = compile_execution_evidence(
        command=command,
        output=output,
        returncode=run.returncode,
        action_id=action_id,
        repository_revision=revision,
        raw_output=run.stdout + run.stderr,
    )
    assert evidence is not None
    workspace.adapter.record_execution_evidence(evidence, command)
    return evidence


# ---------------------------------------------------------------------------
# 1. Initial index
# ---------------------------------------------------------------------------


def test_initial_index_reports_current_graph(runtime_workspace):
    """The session bound to the produced graph reports it usable through
    the same CapabilityResult fields the facades expose."""
    ws = runtime_workspace("initial")
    assert ws.adapter.engine_state.graph_current is True

    index = freshness.index_revision(ws.session)
    assert index.capability == "index_revision"
    assert index.status == "ok", index.omissions
    assert index.answer["source_revision"] == FIXTURE_REVISION

    state = freshness.graph_state(ws.session)
    assert state.capability == "graph_state"
    assert state.status == "ok", state.omissions
    assert state.answer["graph_current"] is True
    assert state.answer["completeness"] == "current_complete"
    assert state.answer["graph_revision"], "empty graph revision"
    assert state.answer["graph_source_revision"] == FIXTURE_REVISION
    assert state.answer["masked_paths"] == []
    assert state.fresh is True
    assert state.graph_revision
    assert state.source_revision == FIXTURE_REVISION


# ---------------------------------------------------------------------------
# 2. Edit observation: transaction -> epoch -> journal -> CAS
# ---------------------------------------------------------------------------


def test_edit_transaction_journaled_and_cas_pinned(runtime_workspace):
    """A real EditTransaction advances the edit epoch, lands in the journal
    and persists byte-identical in CAS — all read back, none fabricated."""
    ws = runtime_workspace("edit")
    adapter = ws.adapter
    epoch_before = adapter._edit_epoch

    txn = _transact_edit(ws)

    assert adapter._edit_epoch == epoch_before + 1
    assert txn.post_revision != txn.pre_revision
    assert txn.changed_paths == (SERVER,)
    assert txn.complete is True, txn.omissions
    assert adapter.repository_revision == txn.post_revision

    row = ws.journal_event("edit_transaction")
    assert row is not None, "journal has no edit_transaction row"
    assert row["transaction_sha256"] == txn.transaction_sha256
    assert row["post_revision"] == txn.post_revision
    assert row["pre_revision"] == txn.pre_revision
    assert row["changed_paths"] == [SERVER]
    assert row["complete"] is True

    digest = row["artifact_sha256"]
    blob = ws.cas_blob("edit_transactions", digest)
    assert blob is not None, "edit transaction CAS blob missing"
    assert hashlib.sha256(blob).hexdigest() == digest, (
        "CAS content does not hash to its address"
    )
    payload = json.loads(blob)
    assert payload["kind"] == "edit_transaction"
    assert payload["post_revision"] == txn.post_revision
    assert [c["path"] for c in payload["changes"]] == [SERVER]
    assert payload["changes"][0]["operation"] == "modify"

    # The capability facade reads the same journal + CAS evidence back.
    result = change.edit_transaction(ws.session)
    assert result.status == "ok", result.omissions
    assert result.answer["post_revision"] == txn.post_revision
    assert result.answer["changed_paths"] == [SERVER]
    assert result.answer["transaction"]["kind"] == "edit_transaction"


# ---------------------------------------------------------------------------
# 3. Synchronous amend: adopted publication or journaled refusal
# ---------------------------------------------------------------------------


def test_sync_amend_adopts_or_journals_refusal(runtime_workspace):
    """After the edit, either the amend chain publishes a new current graph
    or the journal carries a named refusal. Both are honest; silence is
    not an allowed outcome."""
    ws = runtime_workspace("amend")
    txn = _transact_edit(ws)

    adopted = _require_amend_or_note_refusal(ws)
    if adopted is None:
        refused = ws.journal_event("graph_sync_amend_refused")
        pytest.skip(
            f"sync amend refused on this host: {refused['reason']}"
        )

    assert adopted["build_mode"] == "incremental"
    assert SERVER in adopted["dirty_paths"]
    assert adopted["source_revision"] == txn.post_revision

    # The adoption is a real publication: a new certified revision exists
    # and the engine now names it current against the post-edit revision.
    new_graph = Path(ws.adapter.engine_state.graph_path)
    assert new_graph != ws.graph
    assert new_graph.is_file()
    assert new_graph.with_suffix(".manifest.json").is_file()
    assert ws.adapter.engine_state.graph_current is True
    assert ws.adapter.engine_state.graph_source_revision == txn.post_revision
    assert ws.adapter.graph_db == str(new_graph)

    state = freshness.amend_state(ws.session)
    assert state.status == "ok"
    assert state.answer["last_graph_publication"] is not None
    assert state.answer["edit_epoch"] == 1
    assert state.answer["amend_failure_streaks"] == {}


def test_sync_amend_covers_go_edit(runtime_workspace):
    """The amend path is not Python-specific: a moved call in the Go
    fixture lands the same adoption/refusal journal and caller delta."""
    ws = runtime_workspace("amend-go")
    path = ws.root / GO_MAIN
    before = capture_workspace(ws.root)
    text = path.read_text(encoding="utf-8")
    moved = text.replace("\tz := helper(y)\n", "\tz := y\n")
    assert moved != text, "fixture drifted: Compute helper call missing"
    moved = moved.replace(
        "\tname := r.URL.Query().Get(\"name\")\n",
        "\tname := r.URL.Query().Get(\"name\")\n\t_ = helper(1)\n",
    )
    assert "_ = helper(1)" in moved
    path.write_text(moved, encoding="utf-8")
    txn = diff_workspace(
        before, capture_workspace(ws.root),
        action_id=1, command="apply_patch",
    )
    assert txn.changed_paths == (GO_MAIN,)
    ws.adapter.record_edit_transaction(txn)

    adopted = _require_amend_or_note_refusal(ws)
    if adopted is None:
        refused = ws.journal_event("graph_sync_amend_refused")
        pytest.skip(
            f"sync amend refused on this host: {refused['reason']}"
        )
    assert GO_MAIN in adopted["dirty_paths"]

    post = structure.callers(ws.session, "helper")
    assert post.fresh is True
    names = {
        row["name"]
        for rows in (post.answer or {}).get("callers_by_depth", {}).values()
        for row in rows
    }
    assert "Compute" not in names, names
    assert "itemsHandler" in names, names


# ---------------------------------------------------------------------------
# 4. Post-edit query correctness + freshness
# ---------------------------------------------------------------------------


def test_post_edit_query_and_freshness(runtime_workspace):
    """Capability queries still answer against the amended graph and the
    freshness facade reports the transaction's post-edit revision."""
    ws = runtime_workspace("postedit")
    txn = _transact_edit(ws)

    adopted = _require_amend_or_note_refusal(ws)
    if adopted is None:
        refused = ws.journal_event("graph_sync_amend_refused")
        pytest.skip(
            f"sync amend refused on this host: {refused['reason']}"
        )

    index = freshness.index_revision(ws.session)
    assert index.answer["source_revision"] == txn.post_revision

    state = freshness.graph_state(ws.session)
    assert state.status == "ok", state.omissions
    assert state.answer["graph_current"] is True
    assert state.answer["graph_source_revision"] == txn.post_revision

    result = structure.callers(ws.session, "sanitize")
    assert result.status in {"ok", "partial"}, (
        f"callers cannot answer post-edit: {result.status} {result.omissions}"
    )
    assert result.fresh is True
    assert result.source_revision == txn.post_revision
    answer = result.answer or {}
    assert answer.get("resolved_nodes", 0) >= 1
    names = {
        row["name"]
        for rows in answer.get("callers_by_depth", {}).values()
        for row in rows
    }
    assert "render_item" in names, names
    assert "list_items" not in names, names


# ---------------------------------------------------------------------------
# 5. Test/build observation: real pytest -> evidence -> runtime facades
# ---------------------------------------------------------------------------


def test_execution_evidence_roundtrip_and_fingerprint(runtime_workspace):
    """A real pytest run on the edited copy is compiled, journaled, stored
    in CAS and read back by last_test_result/verification_state — then a
    real failing run produces a genuine fingerprint whose recurrence the
    adapter counts."""
    ws = runtime_workspace("exec")
    txn = _transact_edit(ws)

    evidence = _run_fixture_pytest(
        ws, action_id=2, revision=txn.post_revision
    )
    assert evidence.returncode == 0, "fixture pytest did not pass"
    assert evidence.observed_test_outcome == "pass"
    assert evidence.kind == "test"
    assert evidence.repository_revision == txn.post_revision

    row = ws.journal_event("execution_evidence")
    assert row is not None
    assert row["observed_test_outcome"] == "pass"
    assert row["repository_revision"] == txn.post_revision
    blob = ws.cas_blob("execution_evidence", row["artifact_sha256"])
    assert blob is not None
    assert hashlib.sha256(blob).hexdigest() == row["artifact_sha256"]

    last = runtime.last_test_result(ws.session)
    assert last.status == "ok", last.omissions
    assert last.answer["outcome"] == "pass"
    assert last.answer["observed_test_outcome"] == "pass"
    assert last.answer["repository_revision"] == txn.post_revision

    state = runtime.verification_state(ws.session)
    assert state.status == "ok", state.omissions
    assert state.answer["state"] == "recorded"
    assert state.answer["observed_test_outcome"] == "pass"
    assert state.answer["repository_revision"] == txn.post_revision

    # A real failing run: the fingerprint is computed from real output.
    _failing_canary(ws, action_id=3, revision=txn.post_revision)
    fingerprint = runtime.failure_fingerprint(ws.session)
    assert fingerprint.status == "ok", fingerprint.omissions
    fp = fingerprint.answer["fingerprint"]
    assert fingerprint.answer["basis"] == "last_execution"
    assert fp, "empty fingerprint for a real failure"

    # Recurrence bookkeeping: first sighting records, a recurrence at a
    # LATER edit epoch is what triggers the bounded recovery steer.
    adapter = ws.adapter
    epoch = adapter._edit_epoch
    assert adapter.note_failure_fingerprint(fp, epoch=epoch) is False
    assert adapter.note_failure_fingerprint(fp, epoch=epoch + 1) is True

    repeated = runtime.repeated_failure_state(ws.session)
    assert repeated.status == "ok"
    assert repeated.omissions == ()
    entry = repeated.answer["fingerprints"][fp]
    assert entry["recurrences"] == 2
    assert entry["first_epoch"] == epoch
    assert repeated.answer["pending_recovery"] == {
        "fingerprint": fp,
        "epoch": epoch + 1,
    }


# ---------------------------------------------------------------------------
# 6. Covering attribution: honest ok/partial, never invented coverage
# ---------------------------------------------------------------------------


def test_covering_attribution_is_honest(runtime_workspace):
    """affected_tests/covering_tests return real selection or named
    omissions — never an invented test list."""
    ws = runtime_workspace("covering")
    files = [SERVER, GO_MAIN]

    affected = change.affected_tests(ws.session, files)
    assert affected.capability == "affected_tests"
    assert affected.status in {"ok", "partial", "abstain", "unavailable"}
    if affected.status == "unavailable":
        pytest.skip(f"affected_tests unavailable: {affected.omissions}")
    answer = affected.answer or {}
    assert answer.get("files") == files
    assert isinstance(answer.get("symbols", []), list)
    tests = answer.get("tests")
    if affected.status == "ok":
        assert tests, "ok with no tests is fabricated coverage"
        for row in tests:
            assert row.get("file")
    else:
        # Partial/abstain must name why — an honest empty coverage list.
        assert affected.omissions, (
            "affected_tests partial/abstain with no named omission"
        )
        assert tests is not None

    covering = runtime.covering_tests(ws.session, files)
    assert covering.capability == "covering_tests"
    assert covering.status == affected.status
    assert covering.answer == affected.answer
    assert covering.omissions == affected.omissions
