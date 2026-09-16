"""Offline ability audit: obligations -> persistent plan -> submit -> receipt.

This file reproduces scenarios D4 and D7 end to end on the real objects, not
on component seams alone:

  prompt -> requirement ledger -> merged task contract -> compiled predicates
         -> persistent plan -> bound checks -> drain -> stale/re-bind
         -> plan gate -> submit seam -> suppression receipt -> feature census
         -> (D7) product receipt failure beside a preserved official solve.

The only canned piece is ``_StubEnv``: ``drain_plan_checks`` and
``seal_plan_recheck`` only ever call ``execution_env()`` and ``execute()`` on
the environment, so a recorded verdict stands in for the container's honest
answer - complete with the environment identity and capture completeness the
bound-check classifier requires.  Everything else is the shipped producer.

Nothing here calls a provider, mutates credentials, or runs a benchmark.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from minisweagent.exceptions import Submitted

from gt_engine.event_journal import verify_event_journal
from gt_engine.gt_session import GTSession, GTSessionConfig
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import _run_submit_gate, install_runtime_hooks
from gt_engine.persistent_plan import (
    build_plan_inputs,
    merged_plan_contract,
)
from gt_engine.persistent_plan.bootstrap import build_plan
from gt_engine.persistent_plan.ledger import build_requirement_ledger
from gt_engine.persistent_plan.recovery import checkpoint_plan, restore_plan
from gt_engine.runtime_observation import (
    _unquoted_command_surface,
    capture_workspace,
    compile_execution_evidence,
)
from gt_engine.task_contract import (
    Obligation,
    TaskContract,
    extract_task_contract,
)
from gt_engine.verification_contract import compile_obligation_predicates
from scripts.feature_accounting import account

# A prompt shaped like an unfamiliar task: a heading, a fenced example, an
# enumerated requirement block carrying a negation, a packed line with two
# requirements, and a non-normative background section.  None of it names a
# test, so every row must be proven by evidence the audit supplies.
AUDIT_PROMPT = (
    "## Summary\n"
    "The batch scheduler reorders queued jobs by priority.\n"
    "\n"
    "Example:\n"
    "```python\n"
    "scheduler.submit(job, priority=2)\n"
    "```\n"
    "\n"
    "## Requirements\n"
    "1. High priority jobs must run before lower priority jobs.\n"
    "2. A resubmitted job must not lose its queued position.\n"
    "Every job eventually runs; starvation is not permitted.\n"
    "\n"
    "**Background**\n"
    "The scheduler was introduced in the 2.x series.\n"
)

CHECK_ARGV = ("pytest", "-v", "tests/test_widget.py")
CHECK_COMMAND = "pytest -v tests/test_widget.py"
PASS_OUTPUT = (
    "tests/test_widget.py::test_widget PASSED\n"
    "===== 1 passed in 0.01s =====\n"
)
FAIL_OUTPUT = (
    "tests/test_widget.py::test_widget FAILED\n"
    "===== 1 failed in 0.01s =====\n"
)


def _repo(tmp_path: Path) -> Path:
    """The smallest repository that can host a bound check."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "widget.py").write_text(
        "def pick(jobs):\n    return jobs\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_widget.py").write_text(
        "def test_widget():\n    assert True\n", encoding="utf-8")
    return repo


class _StubEnv:
    """The task's isolation boundary with a recorded verdict.

    ``drain_plan_checks`` treats the environment as the only execution
    authority: it asks for ``execution_env()`` and calls ``execute()``.  The
    canned result carries the two fields the classifier needs -
    ``environment_sha256`` so the observation is bound to a named environment
    and ``capture_complete`` so the before/after witness is admissible.
    """

    def __init__(self, results=None, *, environment_sha256: str = "audit-env"):
        self._results = dict(results or {})
        self.environment_sha256 = environment_sha256
        self.calls: list[str] = []

    def execution_env(self) -> dict:
        return {}

    def execute(self, action, cwd=None, timeout=None):
        command = str((action or {}).get("command") or "")
        self.calls.append(command)
        result = dict(self._results.get(command) or {
            "returncode": 0,
            "output": PASS_OUTPUT,
        })
        extra = dict(result.get("extra") or {})
        extra.setdefault("environment_sha256", self.environment_sha256)
        extra.setdefault("capture_complete", True)
        result["extra"] = extra
        return result


class _FailingEnv(_StubEnv):
    """An isolation boundary that faults mid-drain."""

    def execute(self, action, cwd=None, timeout=None):
        command = str((action or {}).get("command") or "")
        self.calls.append(command)
        raise OSError("audit: executor crashed mid-drain")


def _journal(adapter) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(adapter.store.path).read_text(
            encoding="utf-8").splitlines()
        if line.strip()
    ]


def _rows(adapter, event: str) -> list[dict]:
    return [row for row in _journal(adapter) if row.get("event") == event]


def _build(tmp_path: Path, *, contract_mode: str = "merged"):
    """Assemble prompt -> ledger -> contract -> adapter on a real repository.

    ``contract_mode`` selects the bootstrap outcome under audit:
    ``merged`` is the normal path, ``raw`` leaves ledger-only rows unmapped
    to any predicate, and ``none`` is the incomplete-bootstrap shape where
    contract extraction produced nothing.
    """
    repo = _repo(tmp_path)
    raw = extract_task_contract(AUDIT_PROMPT)
    inputs = build_plan_inputs(
        AUDIT_PROMPT, contract=raw, repo_root=str(repo), capture_baseline=False)
    if contract_mode == "merged":
        contract = merged_plan_contract(raw, inputs.ledger, AUDIT_PROMPT)
    elif contract_mode == "raw":
        contract = raw
    else:
        contract = None
    predicates: list[Predicate] = []
    if contract is not None:
        compiled = compile_obligation_predicates(contract)
        predicates = [
            Predicate(item.predicate_id, obligation.text)
            for obligation in contract.obligations
            if (item := compiled.get(obligation.obligation_id)) is not None
        ]
    adapter = MiniSweAdapter(
        task_id="audit",
        state_dir=tmp_path / "state",
        predicates=predicates,
        contract=contract,
        repo_root=str(repo),
        issue_text=AUDIT_PROMPT,
    )
    adapter.plan_inputs = inputs
    return adapter, inputs, contract, repo


def _plan_for(inputs, adapter, *, command: str = CHECK_COMMAND):
    """The deterministic floor plus a provider payload offering the check."""
    plan = build_plan(
        {
            "rows": [
                {
                    "row_id": row.row_id,
                    "verification_kind": "command",
                    "verification_command": command,
                }
                for row in inputs.ledger.rows
            ]
        },
        inputs,
        repo_root=str(adapter.repo_root),
    )
    adapter.persistent_plan = plan
    return plan


def _bind_all(adapter, inputs, argv=CHECK_ARGV) -> str:
    """Bind the same argv to every ledger row; they coalesce to one check."""
    check_id = ""
    for row in inputs.ledger.rows:
        check_id = adapter.bind_plan_check(
            {"argv": list(argv), "requirement_ids": [row.row_id]})
    return check_id


def _snapshot(adapter, repo: Path, boundary: str = "audit") -> None:
    adapter.record_repository_snapshot(
        capture_workspace(repo, excluded_roots=(adapter.store.root,)),
        boundary=boundary,
    )


class TestObligations:
    """The contract front of the audit: extraction must stay complete and
    conservative on a prompt the suite was not tuned to."""

    def test_ledger_contract_and_predicates_compose(self) -> None:
        contract = extract_task_contract(AUDIT_PROMPT)
        ledger = build_requirement_ledger(AUDIT_PROMPT, contract)

        texts = [row.text for row in ledger.rows]
        # One source line per row, verbatim; bullet syntax is not requirement.
        assert "The batch scheduler reorders queued jobs by priority." in texts
        assert "High priority jobs must run before lower priority jobs." in texts
        assert "A resubmitted job must not lose its queued position." in texts
        assert "Every job eventually runs; starvation is not permitted." in texts
        for row in ledger.rows:
            assert row.text in row.source_text
        # Headings and the non-normative section mint nothing.
        assert not any("2.x series" in text for text in texts)
        assert any(
            reason == "non_normative_section"
            for _line, reason in ledger.skipped
        )
        # A fenced example attaches to its row; it is never a row itself.
        assert ledger.fenced_lines >= 1
        example_row = next(row for row in ledger.rows if row.examples)
        assert "scheduler.submit(job, priority=2)" in example_row.examples[0]

        # The packed line carries two requirements: the sentence extractor
        # split it, and the ledger links both obligations back to one row.
        packed = next(row for row in ledger.rows if "starvation" in row.text)
        assert len(packed.obligation_ids) == 2
        # The negated bullet survives into the contract unchanged in meaning.
        assert any(
            "must not lose its queued position" in item.text
            for item in contract.obligations
        )

        # merged_plan_contract is the completeness floor: every ledger row is
        # claimable, and every obligation compiles to a typed predicate.
        merged = merged_plan_contract(contract, ledger, AUDIT_PROMPT)
        merged_ids = {item.obligation_id for item in merged.obligations}
        for row in ledger.rows:
            minted = "plan-" + row.row_id.removeprefix("req-")
            assert row.obligation_ids or minted in merged_ids
        compiled = compile_obligation_predicates(merged)
        assert set(compiled) == merged_ids

    def test_failed_and_unrelated_evidence_discharge_nothing(self, tmp_path):
        adapter, _inputs, contract, _repo_root = _build(tmp_path)
        assert contract is not None
        adapter.start_task()
        unmet_at_start = adapter.unmet_predicates
        assert unmet_at_start, "fixture must start with unproven obligations"
        # Failed evidence is not positive evidence.
        assert adapter.evaluate_observation(
            CHECK_COMMAND, FAIL_OUTPUT, returncode=1, action_index=1) == ()
        # An uncollected exit code is unknown, not a pass.
        assert adapter.evaluate_observation(
            CHECK_COMMAND, PASS_OUTPUT, returncode=None, action_index=2) == ()
        # A passing run on an unrelated suite proves nothing here: the
        # obligations are behavior predicates and lexical overlap is not a
        # proof binding.
        assert adapter.evaluate_observation(
            "pytest -v tests/test_other.py",
            "tests/test_other.py::test_a PASSED\n"
            "===== 3 passed in 0.10s =====\n",
            returncode=0, action_index=3) == ()
        assert adapter.unmet_predicates == unmet_at_start


class TestPersistentPlanD4:
    """Delayed initialization, early edits, restoration, unmapped rows and
    stale checks - the plan lifecycle under audit."""

    def test_delayed_init_binds_checks_after_early_edit(
            self, tmp_path, monkeypatch) -> None:
        """D4: the plan lands AFTER the model's first edit.

        Registering predicates now would retroactively un-verify work the
        edit already did, so the mapping is withheld - and the check channel
        must still carry the plan to evidence on the current tree.
        """
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        # The model edits before bootstrap completes.
        (repo / "widget.py").write_text(
            "def pick(jobs):\n    return sorted(jobs, key=lambda j: -j)\n",
            encoding="utf-8")
        adapter.note_edit(["widget.py"])
        assert adapter.workspace_epoch == 1

        plan = _plan_for(inputs, adapter)
        assert adapter.register_plan_predicates(plan) == 0
        assert adapter.plan_row_predicates == {}
        assert not _rows(adapter, "persistent_plan_predicates")

        check_id = _bind_all(adapter, inputs)
        assert check_id in adapter._pending_check_ids
        assert set(adapter.unmet_plan_rows()) == {
            row.row_id for row in plan.rows}

        env = _StubEnv()
        adapter.drain_plan_checks(env)
        assert env.calls == [CHECK_COMMAND]
        for row in plan.rows:
            assert adapter.plan_row_state(row.row_id) == "CHECK_PASSED"
        assert adapter.unmet_plan_rows() == ()
        observed = _rows(adapter, "plan_check_observed")
        assert observed and observed[-1]["state"] == "CHECK_PASSED"
        assert observed[-1]["source_revision"] == adapter.repository_revision
        assert verify_event_journal(adapter.store.path).valid

    def test_post_check_edit_restales_the_row(self, tmp_path, monkeypatch):
        """D4: a check passed at R1 is not evidence for the submitted R2."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        assert adapter.register_plan_predicates(plan) == 0
        check_id = _bind_all(adapter, inputs)
        adapter.drain_plan_checks(_StubEnv())
        first_revision = adapter.repository_revision
        for row in plan.rows:
            assert adapter.plan_row_state(row.row_id) == "CHECK_PASSED"
        assert adapter.unmet_plan_rows() == ()

        # The edit lands after the observation: the proof predates the tree.
        (repo / "widget.py").write_text(
            "def pick(jobs):\n    return sorted(jobs)\n", encoding="utf-8")
        adapter.note_edit(["widget.py"])
        _snapshot(adapter, repo, boundary="post_edit")
        assert adapter.repository_revision != first_revision
        for row in plan.rows:
            assert adapter.plan_row_state(row.row_id) == "UNVERIFIED"
        assert check_id in adapter._stale_plan_check_ids()
        assert set(adapter.unmet_plan_rows()) == {
            row.row_id for row in plan.rows}
        # The stale check re-pends: a new revision earns a new observation.
        adapter.drain_plan_checks(_StubEnv())
        for row in plan.rows:
            assert adapter.plan_row_state(row.row_id) == "CHECK_PASSED"
        assert adapter.unmet_plan_rows() == ()
        assert verify_event_journal(adapter.store.path).valid

    def test_incomplete_bootstrap_leaves_unmapped_rows_provable(
            self, tmp_path, monkeypatch) -> None:
        """No contract means every row is unmapped - and unmapped rows stay
        unmet until the bound check proves them."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, contract, repo = _build(tmp_path, contract_mode="none")
        assert contract is None
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        assert adapter.register_plan_predicates(plan) == 0
        assert adapter.plan_row_predicates == {}
        unmet = set(adapter.unmet_plan_rows())
        assert unmet == {row.row_id for row in plan.rows}
        _bind_all(adapter, inputs)
        adapter.drain_plan_checks(_StubEnv())
        assert check_id_passing(adapter, plan)
        assert adapter.unmet_plan_rows() == ()
        assert verify_event_journal(adapter.store.path).valid

    def test_restored_plan_brings_design_never_evidence(
            self, tmp_path, monkeypatch) -> None:
        """A checkpoint restores the plan and the check definitions; the
        verdicts they once held are explicitly not restored."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        adapter.register_plan_predicates(plan)
        check_id = _bind_all(adapter, inputs)
        adapter.drain_plan_checks(_StubEnv())
        assert all(
            adapter.plan_row_state(row.row_id) == "CHECK_PASSED"
            for row in plan.rows
        )
        checkpoint_plan(adapter.store, plan, AUDIT_PROMPT)

        # Resume: a fresh adapter on the same journal sees the checkpoint and
        # the bound-check definition, but inherits no evidence.
        resumed, _inputs2, _contract2, _repo2 = _build_resume(tmp_path)
        assert resumed.store.startup_journal_valid
        restored = restore_plan(resumed.store, AUDIT_PROMPT)
        assert restored is not None
        assert restored.canonical_json() == plan.canonical_json()
        resumed.persistent_plan = restored
        touched = resumed._restore_plan_check_definitions()
        assert check_id in resumed._check_specs
        assert check_id in resumed._pending_check_ids
        assert touched
        for row in plan.rows:
            assert resumed.plan_row_state(row.row_id) == "UNVERIFIED"
        assert set(resumed.unmet_plan_rows()) == {
            row.row_id for row in plan.rows}
        assert any(
            row.get("event") == "persistent_plan_restored"
            and row.get("evidence_restored") is False
            for row in _journal(resumed)
        )
        assert any(
            row.get("event") == "plan_check_restored"
            and row.get("check_id") == check_id
            and row.get("evidence_state") == "UNVERIFIED"
            for row in _journal(resumed)
        )
        # The restored check still runs: recovery is execution, not trust.
        resumed.drain_plan_checks(_StubEnv())
        assert resumed.unmet_plan_rows() == ()
        assert verify_event_journal(resumed.store.path).valid

    def test_restore_rejects_wrong_task_and_missing_blob(self, tmp_path) -> None:
        """Startup recovery is fail-closed: a checkpoint from another task or
        a missing content blob both journal a rejection and restore nothing."""
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        checkpoint_plan(adapter.store, plan, AUDIT_PROMPT)
        digest = next(
            row["checkpoint_sha256"] for row in _journal(adapter)
            if row["event"] == "persistent_plan_checkpoint")
        resumed, _i, _c, _r = _build_resume(tmp_path)
        # A checkpoint written under a different task prompt is not this
        # task's plan.
        assert restore_plan(resumed.store, "a different task entirely") is None
        assert any(
            row.get("event") == "persistent_plan_restore_rejected"
            for row in _journal(resumed)
        )
        # And a checkpoint whose payload vanished restores nothing either.
        (Path(resumed.store.root) / "plan_checkpoints"
         / f"{digest}.json").unlink()
        assert restore_plan(resumed.store, AUDIT_PROMPT) is None
        rejections = [
            row for row in _journal(resumed)
            if row.get("event") == "persistent_plan_restore_rejected"]
        assert len(rejections) == 2

    def test_model_payload_drops_phantoms_and_preserves_gaps(
            self, tmp_path) -> None:
        adapter, inputs, _contract, repo = _build(tmp_path)
        real, other = inputs.ledger.rows[0].row_id, inputs.ledger.rows[1].row_id
        plan = build_plan(
            {
                "rows": [
                    {
                        "row_id": real,
                        "anchors": [999999],
                        "verification_kind": "command",
                        "verification_command": "pytest fail_to_pass.py",
                    },
                    {"row_id": "req-phantom", "verification_command": "pytest -q"},
                    {"row_id": other},
                ]
            },
            inputs,
            repo_root=str(adapter.repo_root),
        )
        assert plan.row("req-phantom") is None
        reasons = [reason for _target, reason in plan.abstentions]
        assert "phantom_row_id" in reasons
        assert "phantom_node_id" in reasons
        assert any(reason.startswith("verification_command_") for reason in reasons)
        assert any(reason.startswith("no_check:") for reason in reasons)
        # The inadmissible command is dropped, not kept; the row survives as
        # an explicit gap rather than a hidden one.
        row = plan.row(real)
        assert row is not None and row.verification_command == ""
        # The deterministic floor still carries every ledger row.
        assert {item.row_id for item in plan.rows} >= {
            item.row_id for item in inputs.ledger.rows}
        assert plan.status == "PARTIAL"


def check_id_passing(adapter, plan) -> bool:
    return all(
        adapter.plan_row_state(row.row_id) == "CHECK_PASSED"
        for row in plan.rows)


def _build_resume(tmp_path: Path):
    """A second adapter over the same journal - the restart the checkpoint
    exists for.  Same state_dir and task_id, fresh in-memory state."""
    repo = tmp_path / "repo"
    raw = extract_task_contract(AUDIT_PROMPT)
    contract = merged_plan_contract(raw, build_requirement_ledger(AUDIT_PROMPT, raw), AUDIT_PROMPT)
    compiled = compile_obligation_predicates(contract)
    adapter = MiniSweAdapter(
        task_id="audit",
        state_dir=tmp_path / "state",
        predicates=[
            Predicate(item.predicate_id, obligation.text)
            for obligation in contract.obligations
            if (item := compiled.get(obligation.obligation_id)) is not None
        ],
        contract=contract,
        repo_root=str(repo),
        issue_text=AUDIT_PROMPT,
    )
    return adapter, None, contract, repo


class TestSealAndGate:
    """The submit path: finalization rechecks, the plan gate, suppression
    accounting and the enforced terminal."""

    def test_seal_recheck_repends_stale_on_the_final_tree(
            self, tmp_path, monkeypatch) -> None:
        """D4 finalization: a stale observation is re-pended and re-observed
        against the tree that is actually submitted."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        adapter.register_plan_predicates(plan)
        check_id = _bind_all(adapter, inputs)
        adapter.drain_plan_checks(_StubEnv())
        # Submit-path edit: the only evidence on record now predates the tree.
        # The runtime records a snapshot after every executed action, so the
        # revision ledger is current when the seal asks what is stale.
        (repo / "widget.py").write_text(
            "def pick(jobs):\n    return sorted(jobs)\n", encoding="utf-8")
        adapter.note_edit(["widget.py"])
        _snapshot(adapter, repo, boundary="post_edit")
        assert check_id in adapter._stale_plan_check_ids()
        adapter.begin_verify()
        adapter.begin_submit()
        adapter.advisory_submit_decision()
        assert adapter.phase == "FINISHED"

        summary = adapter.seal_plan_recheck(_StubEnv())
        assert summary["layout_schema"] == "gt.plan_seal_recheck.v1"
        assert summary["ran"] is True
        assert check_id in summary["repended"]
        assert summary["reason"] == "converged"
        assert summary["remaining_unverified"] == []
        assert adapter.phase == "FINISHED"
        for row in plan.rows:
            assert adapter.plan_row_state(row.row_id) == "CHECK_PASSED"
        seal_rows = _rows(adapter, "plan_seal_recheck")
        assert seal_rows and seal_rows[-1]["reason"] == "converged"
        # The honest positive terminal exists: every predicate GREEN at the
        # current epoch and every row verified on the submitted tree is the
        # only shape that earns verified=True.
        state = adapter.final_state()
        assert state["verified"] is True
        assert state["unmet_plan_rows"] == []
        assert state["unverified_plan_rows"] == []
        assert verify_event_journal(adapter.store.path).valid

    def test_seal_without_executor_is_explicitly_unverified(
            self, tmp_path, monkeypatch) -> None:
        """No execution authority: the seal says so instead of claiming a
        recheck it could not run."""
        monkeypatch.delenv("GT_VERIFY_EXECUTE", raising=False)
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        _bind_all(adapter, inputs)
        adapter.begin_verify()
        adapter.begin_submit()
        adapter.advisory_submit_decision()
        summary = adapter.seal_plan_recheck(_StubEnv())
        assert summary["reason"] == "executor_unavailable"
        assert summary["ran"] is False
        assert adapter.phase == "FINISHED"
        assert _rows(adapter, "plan_seal_recheck")
        for row in plan.rows:
            assert adapter.plan_row_state(row.row_id) == "UNVERIFIED"

    def test_seal_survives_executor_fault(self, tmp_path, monkeypatch) -> None:
        """Interruption during finalization: the drain faults, the journal
        keeps the failure, and the phase is still restored."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        check_id = _bind_all(adapter, inputs)
        adapter.begin_verify()
        adapter.begin_submit()
        adapter.advisory_submit_decision()
        env = _FailingEnv()
        summary = adapter.seal_plan_recheck(env)
        assert summary["ran"] is True
        assert summary["reason"] == "still_unverified"
        assert check_id in summary["repended"]
        assert summary["remaining_unverified"] == [
            row.row_id for row in plan.rows]
        assert adapter.phase == "FINISHED"
        assert _rows(adapter, "plan_check_execution_failed")
        assert verify_event_journal(adapter.store.path).valid

    def test_failed_check_is_never_repended(self, tmp_path, monkeypatch) -> None:
        """Negative evidence stands: a CHECK_FAILED observation is not retried
        into a pass by the seal."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        check_id = _bind_all(adapter, inputs)
        env = _StubEnv({CHECK_COMMAND: {
            "returncode": 1,
            "output": FAIL_OUTPUT,
        }})
        adapter.drain_plan_checks(env)
        for row in plan.rows:
            assert adapter.plan_row_state(row.row_id) == "CHECK_FAILED"
        adapter.begin_verify()
        adapter.begin_submit()
        adapter.advisory_submit_decision()
        calls_before = len(env.calls)
        summary = adapter.seal_plan_recheck(env)
        assert check_id not in summary["repended"]
        assert len(env.calls) == calls_before
        assert summary["reason"] == "still_unverified"
        for row in plan.rows:
            assert adapter.plan_row_state(row.row_id) == "CHECK_FAILED"

    def _session(self, adapter, repo: Path, monkeypatch,
                 *, budget=(3000.0, 200), mode="advisory") -> GTSession:
        session = GTSession(
            GTSessionConfig(
                task_id="audit", repo_root=str(repo), mode=mode),
            engine=adapter,
        )
        session._plan_agent = SimpleNamespace(env=_StubEnv())
        monkeypatch.setattr(session, "plan_gate_budget", lambda: budget)
        return session

    def test_gate_drains_pending_checks_before_deciding(
            self, tmp_path, monkeypatch) -> None:
        """The gate cannot refuse on stale ignorance: pending bound checks
        run first, then the decision reads current evidence."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        adapter.register_plan_predicates(plan)
        _bind_all(adapter, inputs)
        env = _StubEnv()
        session = self._session(adapter, repo, monkeypatch)
        session._plan_agent.env = env

        assert session.plan_submit_gate() is True
        assert env.calls == [CHECK_COMMAND]
        events = _journal(adapter)
        observed_at = next(
            row["sequence"] for row in events
            if row["event"] == "plan_check_observed")
        decided_at = next(
            row["sequence"] for row in events
            if row["event"] == "plan_gate_decision")
        assert observed_at < decided_at
        decision = _rows(adapter, "plan_gate_decision")[-1]
        assert decision["accepted"] is True
        assert decision["reason"] == "no_blocking_evidence"
        # ...but a blind baseline still refuses to call itself proven.
        assert decision["baseline_status"] == "not_attempted"
        assert decision["completion_proven"] is False

    def test_gate_refusal_is_the_delivery_and_is_unconditional(
            self, tmp_path, monkeypatch) -> None:
        """A refused submission goes back to IMPLEMENT with a directive that
        advertises the row and its proposed check; the suppression row is the
        delivery the feature census counts; refusals without progress are
        journaled but never concede."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        adapter.register_plan_predicates(plan)
        # No checks bound: every row stays unmet regardless of the stub.
        session = self._session(adapter, repo, monkeypatch)

        adapter.begin_verify()
        adapter.begin_submit()
        assert _run_submit_gate(
            session, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
            pre_execution=True) is False
        assert adapter.phase == "IMPLEMENT"
        directive = adapter.pending_directives[-1]
        assert "GT PLAN GATE" in directive
        assert "proposed check (not proof): " + CHECK_COMMAND in directive
        unmet_ids = set(adapter.unmet_plan_rows())
        assert unmet_ids and any(
            row_id in directive for row_id in unmet_ids)
        suppressed = session.suppress(
            {"command": "echo done"},
            {"returncode": None, "output": "",
             "extra": {"not_executed": True}},
            reason="submit_refused")
        assert suppressed["extra"]["not_executed"] is True
        suppressed_rows = [
            row for row in _journal(adapter)
            if row["event"] == "action_suppressed"
            and row.get("reason") == "submit_refused"]
        assert len(suppressed_rows) == 1
        assert suppressed_rows[0]["executed"] is False

        # Consecutive refusals without progress are journaled as stalls -- and
        # still refuse: a blocking-evidence submit fails attestation wherever
        # it ships, so the gate never spends it.
        adapter.begin_verify()
        adapter.begin_submit()
        assert session.plan_submit_gate() is False
        adapter.begin_implement()
        adapter.begin_verify()
        adapter.begin_submit()
        assert session.plan_submit_gate() is False
        adapter.begin_implement()
        adapter.begin_verify()
        adapter.begin_submit()
        assert session.plan_submit_gate() is False
        last = _rows(adapter, "plan_gate_decision")[-1]
        assert last["reason"] == "unmet_plan_rows"
        assert last["accepted"] is False
        assert last["evidence"]["stalled_refusals"] >= 3

        report = account(_journal(adapter))
        by_feature = {row["feature"]: row for row in report["rows"]}
        assert by_feature["submit_refusal"]["state"] == "DELIVERED"
        assert by_feature["submit_refusal"]["delivered"] == 1
        for alias in ("GT_SS_SUBMIT_RED", "GT_CERT_DELIVERY"):
            assert by_feature[alias]["alias_of"] == "submit_refusal"
            assert by_feature[alias]["state"] == "DELIVERED"
        assert verify_event_journal(adapter.store.path).valid

    def test_low_budget_refuses_and_journals_the_escape_available(
            self, tmp_path, monkeypatch) -> None:
        """Inside the reserve the old policy shipped the dirty submit and the
        run failed attestation anyway. The contract refuses; the escape that
        would have applied is journaled as evidence, not spent."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        adapter.register_plan_predicates(plan)
        session = self._session(adapter, repo, monkeypatch, budget=(60.0, 5))
        adapter.begin_verify()
        adapter.begin_submit()
        assert session.plan_submit_gate() is False
        decision = _rows(adapter, "plan_gate_decision")[-1]
        assert decision["accepted"] is False
        assert decision["reason"] == "unmet_plan_rows"
        assert decision["escaped"] == ""
        assert decision["evidence"]["escape_available"] in {"time", "steps"}
        assert decision["unmet_rows"]
        assert adapter.pending_directives != []

    def test_baseline_regression_refuses_even_when_rows_pass(
            self, tmp_path, monkeypatch) -> None:
        """Genuine failure: a pre-edit green test failing now blocks, and the
        directive names it; baseline noise (unknown) never does."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        adapter.register_plan_predicates(plan)
        _bind_all(adapter, inputs)
        adapter.drain_plan_checks(_StubEnv())
        assert adapter.unmet_plan_rows() == ()
        session = self._session(adapter, repo, monkeypatch)
        monkeypatch.setattr(
            session, "_plan_baseline_check",
            lambda: (("tests/test_widget.py::test_widget",), "regressed"))
        adapter.begin_verify()
        adapter.begin_submit()
        assert session.plan_submit_gate() is False
        decision = _rows(adapter, "plan_gate_decision")[-1]
        assert decision["reason"] == "baseline_regression"
        assert decision["regressions"] == ["tests/test_widget.py::test_widget"]
        assert "tests/test_widget.py::test_widget" in adapter.pending_directives[-1]

    def test_advisory_submit_preserves_native_terminal(
            self, tmp_path, monkeypatch) -> None:
        """Advisory GT is not an execution authority: the submission goes
        through, the journal says it was not enforced, and final_state is
        honest about the unproven rows."""
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        adapter.register_plan_predicates(plan)
        adapter.begin_verify()
        adapter.begin_submit()
        assert adapter.advisory_submit_decision() is True
        assert adapter.phase == "FINISHED"
        decision = _rows(adapter, "submit_decision")[-1]
        assert decision["accepted"] is True
        assert decision["enforced"] is False
        state = adapter.final_state()
        assert state["verified"] is False
        assert state["unmet_plan_rows"]
        assert verify_event_journal(adapter.store.path).valid

    def test_enforced_refusal_then_concession_never_mints_verified(
            self, tmp_path, monkeypatch) -> None:
        """The first enforced submit with unmet obligations is refused; the
        retry is conceded - and acceptance is permission, not proof."""
        monkeypatch.delenv("GT_VERIFY_EXECUTE", raising=False)
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        adapter.register_plan_predicates(plan)
        adapter.begin_verify()
        adapter.begin_submit()
        assert adapter.submit_decision() is False
        assert adapter.phase == "IMPLEMENT"
        refusal = _rows(adapter, "submit_decision")[-1]
        assert refusal["accepted"] is False
        assert refusal["reason"] == "unmet_obligations"
        adapter.begin_verify()
        adapter.begin_submit()
        assert adapter.submit_decision() is True
        assert adapter.phase == "FINISHED"
        state = adapter.final_state()
        assert state["verified"] is False
        assert state["unmet_predicates"] or state["unverified_plan_rows"]
        assert verify_event_journal(adapter.store.path).valid

    def test_gt_off_is_a_noop_gate(self, tmp_path) -> None:
        """GT disabled: no gate decision, no suppression row, the native
        submit answer is the answer."""
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        _plan_for(inputs, adapter)
        session = GTSession(
            GTSessionConfig(task_id="audit-off", repo_root=str(repo), mode="off"),
            engine=adapter,
        )
        session._plan_agent = SimpleNamespace(env=_StubEnv())
        assert session.disabled
        assert session.plan_submit_gate() is True
        result = {"returncode": 0, "output": "native"}
        assert session.suppress(
            {"command": "x"}, result, reason="submit_refused") is result
        accepted, batch = session.request_submit()
        assert accepted is True and batch.empty
        assert not _rows(adapter, "action_suppressed")
        assert not _rows(adapter, "plan_gate_decision")


class _LoopModel:
    """The smallest model surface ``install_runtime_hooks`` binds to."""

    def _prepare_messages_for_api(self, messages):
        return messages

    def query(self, messages, **kwargs):
        return {"role": "assistant", "content": "ok", "extra": {"actions": []}}

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [
            {"role": "tool", "content": str(out.get("output") or ""),
             "tool_call_id": f"call-{index}"}
            for index, out in enumerate(outputs)
        ]


class _LoopAgent:
    """The smallest agent surface: env + config clock for the gate budget."""

    def __init__(self, env, *, wall_time_limit_seconds=3600):
        self.env = env
        self.model = _LoopModel()
        self.messages: list[dict] = []
        self.config = SimpleNamespace(
            step_limit=0, wall_time_limit_seconds=wall_time_limit_seconds)
        self.n_calls = 0
        self._start_time = time.time()

    def execute_actions(self, message):
        return []

    def add_messages(self, *messages):
        self.messages.extend(messages)
        return list(messages)

    def get_template_vars(self):
        return {}


class _SubmittedEnv(_StubEnv):
    """An isolation boundary whose command OUTPUT opens with the marker even
    though the command text never carried it -- the post-execution shape."""

    def execute(self, action, cwd=None, timeout=None):
        command = str((action or {}).get("command") or "")
        self.calls.append(command)
        error = Submitted({
            "role": "exit",
            "content": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\npayload",
            "extra": {"exit_status": "Submitted", "submission": "payload"},
        })
        error.gt_execution_result = {
            "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\npayload",
            "returncode": 0,
            "exception_info": "",
        }
        raise error


class TestSubmitSeamEndToEnd:
    """The action loop, not the gate primitive: pin that an ADVISORY run still
    suppresses a marker command under blocking evidence, and that a submit
    detected only in the executed output is journaled as a post-execution
    accept (the seam cannot un-run a command)."""

    def test_advisory_marker_command_is_suppressed_and_steered(
            self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        _plan_for(inputs, adapter)
        # No bound checks and no predicate mapping: every row stays unmet.
        assert adapter.unmet_plan_rows()
        env = _StubEnv()
        agent = _LoopAgent(env)
        session = GTSession(
            GTSessionConfig(
                task_id="audit", repo_root=str(repo), mode="advisory"),
            engine=adapter,
        )
        install_runtime_hooks(agent, session)
        assert session.mode.value == "advisory" and not session.can_enforce

        submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        observed = agent.execute_actions(
            {"extra": {"actions": [{"command": submit, "tool_call_id": "c1"}]}})

        # The native command never ran: the environment saw nothing at all.
        assert env.calls == []
        assert adapter.phase == "IMPLEMENT"
        decision = _rows(adapter, "plan_gate_decision")[-1]
        assert decision["accepted"] is False
        assert decision["reason"] == "unmet_plan_rows"
        suppressed = _rows(adapter, "action_suppressed")
        assert len(suppressed) == 1
        assert suppressed[0]["reason"] == "submit_refused"
        assert suppressed[0]["executed"] is False
        # The refusal is steered back to the model as a user-role directive,
        # surfaced through the same add_messages return the loop appends.
        surfaced = "\n".join(
            str(message.get("content") or "")
            for message in [*observed, *agent.messages])
        assert "GT PLAN GATE" in surfaced
        assert "submission was not executed" in surfaced
        assert verify_event_journal(adapter.store.path).valid

    def test_output_detected_submit_journals_post_execution_accept(
            self, tmp_path, monkeypatch) -> None:
        """The marker that appears only in command OUTPUT cannot be refused
        pre-execution -- the command already ran. The seam journals the
        advisory accept and re-raises the native terminal."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        _plan_for(inputs, adapter)
        env = _SubmittedEnv()
        agent = _LoopAgent(env)
        session = GTSession(
            GTSessionConfig(
                task_id="audit", repo_root=str(repo), mode="advisory"),
            engine=adapter,
        )
        install_runtime_hooks(agent, session)

        with pytest.raises(Submitted):
            agent.execute_actions({"extra": {"actions": [
                {"command": "cat submission.txt", "tool_call_id": "c1"}]}})

        assert env.calls == ["cat submission.txt"]
        assert adapter.phase == "FINISHED"
        decision = _rows(adapter, "submit_decision")[-1]
        assert decision["accepted"] is True
        assert decision["enforced"] is False
        # Blocking predicates were journaled as advisory evidence, and no
        # suppression row exists for a command that already executed.
        assert _rows(adapter, "submit_advisory")
        assert not _rows(adapter, "action_suppressed")
        assert verify_event_journal(adapter.store.path).valid


class TestAccounting:
    """The census must say what the journal says - and nothing more."""

    def test_eligible_but_undelivered_refusal_is_starved(self) -> None:
        """Unresolved RED at a submit the journal witnesses, with no
        suppression row: STARVED, never DELIVERED, never silently absent."""
        events = [
            {"event": "submit_decision", "accepted": True, "enforced": False,
             "active_red": ["pred-1"]},
            {"event": "session_closed", "terminal": "submitted_unverified"},
        ]
        report = account(events)
        by_feature = {row["feature"]: row for row in report["rows"]}
        assert by_feature["submit_refusal"]["state"] == "STARVED"
        for alias in ("GT_SS_SUBMIT_RED", "GT_CERT_DELIVERY"):
            assert by_feature[alias]["state"] == "STARVED"
            assert by_feature[alias]["alias_of"] == "submit_refusal"

    def test_missing_submit_boundary_is_boundary_unknown(self) -> None:
        """No terminal row, no submit witness: the run died before close and
        the census says BOUNDARY_UNKNOWN, not NOT_REACHED."""
        report = account([{"event": "runtime_layout"}])
        by_feature = {row["feature"]: row for row in report["rows"]}
        assert by_feature["submit_refusal"]["state"] == "BOUNDARY_UNKNOWN"
        # task_start WAS witnessed (runtime_layout) and no plan was built:
        # persistent_plan correctly declined - a witnessed boundary with zero
        # eligibility is not starvation.
        assert by_feature["persistent_plan"]["state"] == "DECLINED_CORRECTLY"
        # The unknown names the row it wanted rather than reading as silence.
        assert "no journal row witnesses submit" in \
            by_feature["submit_refusal"]["evidence"]


class TestReceiptSeparationD7:
    """D7: a product receipt failure after a valid official solve keeps both
    facts - the solve is preserved and the product failure still fails
    attestation."""

    def test_receipt_failure_after_valid_verifier_success(
            self, tmp_path, monkeypatch) -> None:
        import scripts.attest_deepswe as attest_module
        from gt_harness.runtime_receipts import (
            issue_runtime_receipt_failure,
            verify_runtime_receipt,
        )
        from scripts.attest_deepswe import attest_deepswe
        from tests.test_attest_deepswe import EFFECTIVE, REQUESTED, TASK, _fixture

        monkeypatch.setattr(attest_module, "CANONICAL_TASK_IDS", (TASK,))
        adapter_path, product_path = _fixture(tmp_path)
        agent = product_path.parent

        # The official verifier genuinely ran and solved the task.
        verifier = agent / "official-verifier-result.json"
        row = json.loads(verifier.read_text(encoding="utf-8"))
        row["reward"] = 1
        row["solved"] = True
        trial_result = next(
            path for path in (tmp_path / "tasks").rglob("result.json")
            if path.parent.name == "trial")
        trial = json.loads(trial_result.read_text(encoding="utf-8"))
        trial["verifier_result"]["rewards"]["reward"] = 1
        trial_result.write_text(json.dumps(trial), encoding="utf-8")
        aggregate = trial_result.parent.parent / "result.json"
        aggregate_row = json.loads(aggregate.read_text(encoding="utf-8"))
        aggregate_row["stats"]["evals"]["task"]["metrics"][0]["reward"] = 1
        aggregate.write_text(json.dumps(aggregate_row), encoding="utf-8")
        row["runner_result_sha256"] = hashlib.sha256(
            aggregate.read_bytes()).hexdigest()
        verifier.write_text(json.dumps(row), encoding="utf-8")

        # The product receipt then fails issuance - the real producer, not a
        # hand-edited JSON - over the completed run's own artifacts.
        report_path = agent / "miniswe_report.json"
        trajectory_path = agent / "miniswe_trajectory.json"
        product = issue_runtime_receipt_failure(
            report_path=report_path,
            trajectory_path=trajectory_path,
            product_receipt_path=product_path,
            adapter_receipt_path=adapter_path,
            task_id=TASK,
            product_source_sha="f" * 40,
            treatment="groundtruth",
            requested_model=REQUESTED,
            scaffold_version="2.4.6",
            time_budget_seconds=3600,
            terminal="submitted_unverified",
            exit_code=0,
            error=ValueError("audit: injected receipt construction failure"),
        )
        assert product["status"] == "ERROR"
        # The native terminal and exit code are preserved, not rewritten.
        assert product["terminal"] == "submitted_unverified"
        assert product["exit_code"] == 0
        assert product["receipt_issuance"]["code"] == (
            "runtime_receipt_issuance_failed")
        assert product["effective_model"] == EFFECTIVE
        assert "product_not_completed" in verify_runtime_receipt(product_path)

        receipt = attest_deepswe(
            tmp_path, source_sha="f" * 40,
            task_job_result="success", workflow_run_id="offline")
        assert receipt["status"] == "FAIL"
        assert any(
            error.startswith(f"product_receipt:{TASK}")
            for error in receipt["errors"])
        outcome = receipt["outcomes"][TASK]
        # The valid official solve is preserved beside the product failure:
        # neither erased nor upgraded.
        assert outcome["status"] == "GRADED"
        assert outcome["solved"] is True
        assert outcome["reward"] == 1


class TestLayer4Mutations:
    """Layer-4: corrupt one boundary per test; the ability evidence must go
    red. A green verdict under a mutation would be a proof defect in the
    audit itself, not a pass. Mutations are isolated substitutions in the
    test environment - the shipped product is untouched."""

    def test_executor_failure_is_never_a_pass(self, tmp_path, monkeypatch):
        """Mutation: the executor reports failure where the ability tests
        expect a pass. Every row must read CHECK_FAILED, none may clear."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        _bind_all(adapter, inputs)
        adapter.drain_plan_checks(_StubEnv({CHECK_COMMAND: {
            "returncode": 1, "output": FAIL_OUTPUT}}))
        assert not check_id_passing(adapter, plan)
        assert all(
            adapter.plan_row_state(row.row_id) == "CHECK_FAILED"
            for row in plan.rows)
        assert set(adapter.unmet_plan_rows()) == {
            row.row_id for row in plan.rows}

    def test_incomplete_capture_is_never_a_pass(self, tmp_path, monkeypatch):
        """Mutation: the boundary reports capture_complete=False. A verdict
        without a complete before/after witness stays UNVERIFIED."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        _bind_all(adapter, inputs)
        env = _StubEnv({CHECK_COMMAND: {
            "returncode": 0, "output": PASS_OUTPUT,
            "extra": {"capture_complete": False}}})
        adapter.drain_plan_checks(env)
        assert not check_id_passing(adapter, plan)
        assert all(
            adapter.plan_row_state(row.row_id) == "UNVERIFIED"
            for row in plan.rows)
        observed = _rows(adapter, "plan_check_observed")[-1]
        assert observed["state"] == "UNVERIFIED"
        assert observed["capture_complete"] is False

    def test_environment_identity_mismatch_stays_unverified(
            self, tmp_path, monkeypatch) -> None:
        """Mutation: the check was bound to one environment identity and the
        executor ran under another. Evidence from the wrong environment is
        not evidence for this tree."""
        monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        for row in inputs.ledger.rows:
            adapter.bind_plan_check({
                "argv": list(CHECK_ARGV),
                "requirement_ids": [row.row_id],
                "environment_sha256": "a-different-environment",
            })
        adapter.drain_plan_checks(_StubEnv(environment_sha256="audit-env"))
        assert not check_id_passing(adapter, plan)
        assert all(
            adapter.plan_row_state(row.row_id) == "UNVERIFIED"
            for row in plan.rows)
        assert set(adapter.unmet_plan_rows()) == {
            row.row_id for row in plan.rows}

    def test_tampered_checkpoint_blob_is_rejected(self, tmp_path) -> None:
        """Mutation: the checkpoint blob's bytes no longer match the digest
        the journal names. Recovery must reject rather than trust it."""
        adapter, inputs, _contract, repo = _build(tmp_path)
        adapter.start_task()
        plan = _plan_for(inputs, adapter)
        checkpoint_plan(adapter.store, plan, AUDIT_PROMPT)
        digest = next(
            row["checkpoint_sha256"] for row in _journal(adapter)
            if row["event"] == "persistent_plan_checkpoint")
        blob = Path(adapter.store.root) / "plan_checkpoints" / f"{digest}.json"
        blob.write_bytes(b'{"layout":"gt.plan_checkpoint.v4","plan":{}}')
        resumed, _i, _c, _r = _build_resume(tmp_path)
        assert restore_plan(resumed.store, AUDIT_PROMPT) is None
        assert any(
            row.get("event") == "persistent_plan_restore_rejected"
            for row in _journal(resumed))

    def test_suppression_with_other_reason_is_not_a_refusal_delivery(
            self) -> None:
        """Mutation: the suppression row exists but its reason is not
        ``submit_refused``. The census must not count it as the refusal
        capability - DELIVERED requires the right row, not any row."""
        events = [
            {"event": "submit_decision", "accepted": True,
             "enforced": False, "active_red": ["pred-1"]},
            {"event": "action_suppressed", "reason": "budget_hold",
             "executed": False},
            {"event": "session_closed", "terminal": "submitted_unverified"},
        ]
        report = account(events)
        by_feature = {row["feature"]: row for row in report["rows"]}
        assert by_feature["submit_refusal"]["state"] == "STARVED"
        assert by_feature["GT_SS_SUBMIT_RED"]["state"] == "STARVED"


UNITTEST_PASS_OUTPUT = (
    "test_consumer_answer "
    "(tests.test_consumer.ConsumerAnswerTest.test_consumer_answer) ... "
    "Consumer answer check\n"
    "ok\n\n"
    "----------------------------------------------------------------------\n"
    "Ran 1 test in 0.001s\n\n"
    "OK\n"
)


class TestObservationSurface:
    """Regression for the confirmed defect this audit exposed in the
    observation surface (``gt_engine/runtime_observation.py``,
    ``_unquoted_command_surface``).

    The surface mask turned EVERY quoted span into a ``Q<digest>``
    placeholder - including the quoted span in PROGRAM position, which is
    the executable the shell runs, not payload. Any test that invokes the
    interpreter as ``"<sys.executable>" -m unittest ...`` - the shape
    ``tests/test_obligation_reverify.py`` and
    ``tests/test_dependency_verification.py`` run, and a common CI quoting
    style - produced a masked program token, so ``compile_execution_evidence``
    returned None (no test kind at all) and ``evaluate_observation``
    discharged nothing: obligation proofs silently went dark even though a
    real suite ran and passed. Six tracked tests went red under the
    uncommitted E1/E2 rewrite until program-position words were kept
    readable.

    Payload masking is the protection; program masking was the defect.
    These pin both directions.
    """

    def test_quoted_interpreter_program_word_stays_visible(self) -> None:
        command = (
            f'"{sys.executable}" -B -m unittest tests.test_consumer -v')
        surface = _unquoted_command_surface(command)
        # The program word is emitted readable (backslashes normalized),
        # never as an opaque Q<digest> token.
        assert "Q" not in surface.split(" ", 1)[0]
        assert "python" in surface
        assert "unittest" in surface
        evidence = compile_execution_evidence(
            command=command, output=UNITTEST_PASS_OUTPUT,
            returncode=0, action_id=1, repository_revision="rev-1")
        assert evidence is not None
        assert evidence.kind == "test"
        assert evidence.protocol == "unittest"
        assert evidence.outcome == "pass"

    def test_interpreter_flags_before_module_still_resolve(self) -> None:
        """Adjacent gap closed with the fix: ``python -B -m unittest`` and
        ``python -W ignore -m pytest`` leave ``-m`` reachable, while ``-c``
        and a bare script path correctly end module resolution."""
        from gt_engine.runtime_observation import test_command_shape

        shape = test_command_shape(
            f'"{sys.executable}" -B -m unittest tests.test_consumer -v')
        assert (shape.scope, shape.family) == ("scoped", "unittest")
        shape = test_command_shape('python -W ignore -m pytest -q')
        assert (shape.scope, shape.family) == ("suite", "pytest")
        # `-c` executes its payload and exits - no module run follows it.
        assert test_command_shape(
            'python -c "import pytest" -m pytest').family == "unknown"
        # A bare script path is the program, not a module flag preamble.
        assert test_command_shape(
            'python tests/test_a.py -m pytest').family == "unknown"

    def test_quoted_runner_name_stays_visible(self) -> None:
        evidence = compile_execution_evidence(
            command='"pytest" -q tests/', output=PASS_OUTPUT,
            returncode=0, action_id=1, repository_revision="rev-1")
        assert evidence is not None
        assert evidence.kind == "test"
        assert evidence.outcome == "pass"

    def test_env_assignment_value_stays_masked(self) -> None:
        command = 'FOO="a;pytest" python -m unittest t'
        surface = _unquoted_command_surface(command)
        # The assignment VALUE is payload: its quoted `;` must not read as
        # a boundary, and the real program word stays readable.
        assert '"Q' in surface
        assert "a;pytest" not in surface
        evidence = compile_execution_evidence(
            command=command, output=UNITTEST_PASS_OUTPUT,
            returncode=0, action_id=1, repository_revision="rev-1")
        assert evidence is not None
        assert evidence.kind == "test"
        assert evidence.outcome == "pass"

    def test_quoted_payload_cannot_mint_a_boundary(self) -> None:
        """The E1 protection the mask exists for: `;pytest` inside a quoted
        argument is message text, never a runner boundary."""
        command = "git commit -m 'x;pytest tests/'"
        surface = _unquoted_command_surface(command)
        assert "pytest" not in surface
        evidence = compile_execution_evidence(
            command=command, output=PASS_OUTPUT,
            returncode=0, action_id=1, repository_revision="rev-1")
        assert evidence is None

    def test_metachar_program_name_cannot_mint_a_boundary(self) -> None:
        """Layer-4 on the fix itself: an executable literally named
        ``x;nox`` is one weird program word to the shell, not a `nox`
        segment. The sanitized literal must not give the boundary regexes
        a ``;`` to match."""
        surface = _unquoted_command_surface('"x;nox" -s t')
        assert surface == "x_nox -s t"
        evidence = compile_execution_evidence(
            command='"x;nox" -s t', output=PASS_OUTPUT,
            returncode=0, action_id=1, repository_revision="rev-1")
        assert evidence is None or evidence.kind != "test"

    def test_evaluate_observation_discharges_through_quoted_interpreter(
            self, tmp_path) -> None:
        """The capability impact end to end: a semantic proof observed
        under a quoted interpreter still records GREEN on the obligation
        predicate - this is the discharge path the defect had disabled."""
        repo = _repo(tmp_path)
        contract = TaskContract(
            "code_behavior",
            (Obligation(
                "consumer", "Consumer answer must contain '1'.", "task"),),
        )
        compiled = compile_obligation_predicates(contract)["consumer"]
        adapter = MiniSweAdapter(
            task_id="quoted-interp",
            state_dir=tmp_path / "state",
            predicates=[
                Predicate(compiled.predicate_id, compiled.obligation_id)],
            contract=contract,
            repo_root=repo,
        )
        adapter.start_task()
        command = (
            f'"{sys.executable}" -B -m unittest tests.test_consumer -v')
        output = (
            "test_consumer_answer "
            "(tests.test_consumer.ConsumerAnswerTest.test_consumer_answer)"
            " ... Consumer answer check\n"
            "GT_SEMANTIC_ASSERT relation=contains literal_sha256="
            + hashlib.sha256(b"1").hexdigest() + " result=pass\n"
            "ok\n\n"
            "----------------------------------------------------------------------\n"
            "Ran 1 test in 0.001s\n\n"
            "OK\n"
        )
        discharged = adapter.evaluate_observation(
            command, output, returncode=0, action_index=1)
        assert discharged == (compiled.predicate_id,)
