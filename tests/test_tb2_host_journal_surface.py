"""TB2 host route: the ``no_incontainer_journal`` offline reproduction.

``tests/fixtures/capability_matrix.json`` records the second open TB2 mode,
"receipt surface differs - host-side state only", with ``covering_tests: []``.
Per ``D:\\gt-context-plan\\docs\\AUDIT-ABILITY-SPEC.md`` section 4 the mode
stays open until its own offline reproduction passes, and no in-container
Mini-SWE proof may be borrowed to close it.  This module is that
reproduction; it changes nothing under ``eval/`` or ``gt_engine/``.

The difference being reproduced.  The in-container arms
(``eval/miniswe_agent.py``, ``eval/tb_agent.py``) write an append-only
hash-chained journal: ``ExternalStateStore``
(``gt_engine/miniswe_integration.py:73``) creates
``<root>/<task_id>/events.jsonl`` with ``sequence`` / ``parent_hash`` /
``event_hash`` anchored on ``GENESIS_HASH``, verified by
``verify_event_journal``.  ``eval/gt_central_agent.py`` has zero references to
``events.jsonl``, ``event_journal``, ``gt-state`` or ``EventJournal``; its
surface is flat host files under ``logs_dir``.  The consequences are
tamper-evidence, a tail-truncation anchor, and the ability to cut a replay
fixture from a run - none of which exist on the host arm.

Everything here is asserted from ``logs_dir`` alone.  No provider call, no
benchmark dispatch, no Docker.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext

import eval.gt_central_agent as gt_central_agent
from eval.gt_central_agent import MiniSweCentralAgent
from gt_engine.central_runtime import FeatureReceipt, feature_payload_valid

SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

# The in-container journal filename the host arm never writes.
JOURNAL_NAME = "events.jsonl"

CONTEXT_PLAN_ROOT = Path(os.environ.get("GT_CONTEXT_PLAN_ROOT", r"D:\gt-context-plan"))


class _Environment:
    """Mirror of the fake Harbor environment at tests/test_gt_central_agent.py:880."""

    default_user = "root"

    def __init__(self):
        self.commands: list[tuple[str, dict | None]] = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.commands.append((command, env))
        if command == "pwd -P":
            return ExecResult(stdout="/app\n", return_code=0)
        if command.startswith("uname "):
            return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
        if "-printf" in command:
            return ExecResult(stdout="", return_code=0)
        if command == "rg --line-number save_user .":
            return ExecResult(stdout="src/service.py:7:def save_user(user_id):\n", return_code=0)
        if command == SUBMIT:
            return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
        return ExecResult(stdout="", return_code=0)


class _ScriptedModel:
    """Mirror of the injected model at tests/test_gt_central_agent.py:1341."""

    config = type("Config", (), {"model_name": "test"})()
    tools = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]

    def __init__(self, commands):
        self.script = list(commands)
        self.index = 0
        self.observed: list[str] = []
        self.observed_history: list[list[str]] = []

    def format_message(self, **kwargs):
        return kwargs

    def get_template_vars(self):
        return {"observation_template": "{{ output.output }}", "format_error_template": "error"}

    def query(self, messages):
        self.observed = [str(item.get("content") or "") for item in messages]
        self.observed_history.append(list(self.observed))
        command = self.script[min(self.index, len(self.script) - 1)]
        self.index += 1
        return {
            "role": "assistant",
            "content": "act",
            "extra": {
                "actions": [{"command": command, "tool_call_id": "call-1"}],
                "response": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
                },
                "cost": 0.0,
            },
        }

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [{"role": "tool", "content": outputs[0]["output"]}]


SCRIPT = ("rg --line-number save_user .", "ls -la", SUBMIT, SUBMIT)


async def _run_to_submitted(logs_dir: Path) -> Path:
    model = _ScriptedModel(SCRIPT)
    agent = MiniSweCentralAgent(
        logs_dir=logs_dir,
        model_name="test",
        integration_mode="active",
        policy_mode="certified_active",
        step_limit=6,
    )
    agent._model_factory = lambda: model
    await agent.run(
        "Repair save_user in src/service.py and submit.", _Environment(), AgentContext()
    )
    return logs_dir


def _receipt(logs_dir: Path) -> dict:
    return json.loads((logs_dir / "central_receipt.json").read_text(encoding="utf-8"))


def _terminal(logs_dir: Path) -> str:
    document = json.loads((logs_dir / "miniswe_trajectory.json").read_text(encoding="utf-8"))
    return str((document.get("info") or {}).get("exit_status") or "")


@pytest.mark.asyncio
async def test_scripted_run_reaches_submitted_on_the_host_arm(tmp_path):
    """Precondition for the four assertions below."""
    logs_dir = await _run_to_submitted(tmp_path)
    assert _terminal(logs_dir) == "Submitted"
    assert (logs_dir / "central_receipt.json").is_file()


@pytest.mark.asyncio
async def test_host_arm_writes_no_event_journal(tmp_path):
    """The surface difference itself, stated as an assertion rather than a gap."""
    logs_dir = await _run_to_submitted(tmp_path)
    assert list(logs_dir.rglob(JOURNAL_NAME)) == []
    assert list(logs_dir.rglob("gt-state")) == []
    assert sorted(path.name for path in logs_dir.iterdir()) == [
        "central_receipt.json",
        "gt-run-receipt.json",
        "intervention_chain.json",
        "miniswe_trajectory.json",
        "provider_query_started.json",
        "trajectory.json",
    ]


# ---------------------------------------------------------------------------
# (1) central_receipt.json declares its own integrity class.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_receipt_no_journal_field: the central-runtime-receipt-v3 document "
        "written at eval/gt_central_agent.py:11574 has no field that names its own "
        "integrity class.  'host-side state only' is an absence a reader has to "
        "infer from the missing events.jsonl, not a stated property, so a "
        "tail-truncated receipt and a complete one are indistinguishable and "
        "gt_engine/uptake_audit.py's delivery_missing_from_event_journal check is "
        "unreachable by construction."
    ),
)
@pytest.mark.asyncio
async def test_receipt_declares_its_integrity_class(tmp_path):
    logs_dir = await _run_to_submitted(tmp_path)
    receipt = _receipt(logs_dir)

    assert "journal" in receipt, sorted(receipt)
    journal = receipt["journal"]
    assert isinstance(journal, dict)
    # Whatever it says, it must say it: the class, and whether the rows below
    # are hash-chained.
    assert journal.get("kind") in {"event_journal", "host_files_only"}
    assert "hash_chained" in journal


@pytest.mark.asyncio
async def test_receipt_integrity_class_current_truth_is_undeclared(tmp_path):
    """GREEN companion: pin today's silence so a fix has to move this row."""
    logs_dir = await _run_to_submitted(tmp_path)
    receipt = _receipt(logs_dir)

    assert "journal" not in receipt
    assert not [key for key in receipt if "journal" in key.lower()]
    assert receipt["schema"] == "central-runtime-receipt-v3"


# ---------------------------------------------------------------------------
# (2) Every DELIVERED FeatureReceipt is reconstructible from the receipt.
# ---------------------------------------------------------------------------


def _reconstruct(row: dict) -> FeatureReceipt:
    """Rebuild the dataclass from the JSON row alone - no journal, no run."""
    return FeatureReceipt(
        feature_id=row["feature_id"],
        kind=row["kind"],
        boundary=row["boundary"],
        action_id=row["action"],
        revision=row["revision"],
        decision=row["decision"],
        reason=row["reason"],
        payload=row["payload"],
        fresh=row["fresh"],
        model_visible=row["model_visible"],
        delivery_status=row["delivery_status"],
        delivery_reason=row["delivery_reason"],
        source_revision=row["source_revision"],
        source_epoch=row["source_epoch"],
    )


@pytest.mark.asyncio
async def test_delivered_feature_receipts_are_reconstructible_without_a_journal(tmp_path):
    logs_dir = await _run_to_submitted(tmp_path)
    receipt = _receipt(logs_dir)
    rows = [row for row in receipt["features"]["receipts"] if row["decision"] == "DELIVERED"]
    assert rows, "the scripted run delivered no feature receipt at all"

    host_actions = {int(row["action_id"]) for row in receipt["host_execution"]["receipts"]}
    for row in rows:
        rebuilt = _reconstruct(row)
        # Independent re-admission: the payload the receipt carries must still
        # satisfy the production validity contract on its own terms.
        assert feature_payload_valid(
            rebuilt.feature_id,
            rebuilt.payload,
            boundary=rebuilt.boundary,
            revision=rebuilt.revision,
            fresh=rebuilt.fresh,
        ), rebuilt.feature_id
        # And it must bind to a host execution the same document records, so
        # the row is anchored to an action rather than floating.
        assert rebuilt.action_id in host_actions or rebuilt.action_id == 0
        assert rebuilt.source_revision
        assert rebuilt.revision


# ---------------------------------------------------------------------------
# (3) The two receipt writes are consistent; the second is a superset.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_receipt_write_is_a_superset_of_the_first(tmp_path, monkeypatch):
    """``central_receipt.json`` is written twice: :11574 then rewritten :12109.

    Without a journal the second write is the only surviving copy, so a
    dropped row between them would leave no anchor.  Capture both payloads at
    the ``_atomic_write_text`` boundary and compare.
    """
    writes: list[str] = []
    original = gt_central_agent._atomic_write_text

    def _capture(path, payload, *, encoding="utf-8"):
        if Path(path).name == "central_receipt.json":
            writes.append(payload)
        return original(path, payload, encoding=encoding)

    monkeypatch.setattr(gt_central_agent, "_atomic_write_text", _capture)
    logs_dir = await _run_to_submitted(tmp_path)

    assert len(writes) == 2, f"expected two central_receipt.json writes, saw {len(writes)}"
    first = json.loads(writes[0])
    second = json.loads(writes[1])

    assert set(first) <= set(second), sorted(set(first) - set(second))
    # The re-read/rewrite at :12083-12110 only adds; it must not rewrite what
    # the first write already established.
    for key, value in first.items():
        assert second[key] == value, key
    assert "intervention_chain" in second and "intervention_chain" not in first

    first_rows = first["features"]["receipts"]
    second_rows = second["features"]["receipts"]
    assert len(second_rows) >= len(first_rows)
    delivered_first = [
        (row["feature_id"], row["action"], row["revision"])
        for row in first_rows
        if row["decision"] == "DELIVERED"
    ]
    delivered_second = [
        (row["feature_id"], row["action"], row["revision"])
        for row in second_rows
        if row["decision"] == "DELIVERED"
    ]
    assert delivered_first, "no delivered row in the first write to compare"
    assert delivered_first == delivered_second[: len(delivered_first)]
    assert _receipt(logs_dir)["features"]["receipts"] == second_rows

    host_first = first["host_execution"]["receipts"]
    host_second = second["host_execution"]["receipts"]
    assert host_first == host_second[: len(host_first)]


# ---------------------------------------------------------------------------
# (4) extract_replay_fixture.py fails with a typed reason, not a bare
#     exception.
# ---------------------------------------------------------------------------


def _load_extract_replay_fixture():
    path = CONTEXT_PLAN_ROOT / "scripts" / "extract_replay_fixture.py"
    if not path.is_file():
        pytest.skip(
            "UNPROVEN, not a pass: "
            f"{path} is absent, so the host-arm replay-fixture refusal cannot be "
            "exercised here.  Set GT_CONTEXT_PLAN_ROOT to the gt-context-plan "
            "checkout to run it."
        )
    spec = importlib.util.spec_from_file_location("tb2_extract_replay_fixture", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _trial_directory(tmp_path: Path) -> Path:
    """A recorded trial laid out the way the extractor expects to find one."""
    logs_dir = await _run_to_submitted(tmp_path / "logs")
    trial = tmp_path / "task__trial"
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({"resolved": True}), encoding="utf-8")
    for name in ("miniswe_trajectory.json",):
        (trial / "agent" / name).write_text(
            (logs_dir / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return trial


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_replay_fixture_untyped_failure: scripts/extract_replay_fixture.py:133-137 "
        "hard-requires exactly one agent/gt-state/*/events.jsonl and raises a bare "
        "ValueError with a counting message when it finds none.  On the TB2 host arm "
        "that is not a corrupt trial, it is the arm's declared surface, so the "
        "refusal must be a typed 'no journal on the host arm' reason a caller can "
        "branch on rather than a string about how many files were found."
    ),
)
@pytest.mark.asyncio
async def test_replay_fixture_extraction_refuses_the_host_arm_with_a_typed_reason(tmp_path):
    module = _load_extract_replay_fixture()
    trial = await _trial_directory(tmp_path)

    with pytest.raises(Exception) as caught:  # noqa: PT011 - the type is the assertion
        module.extract_fixture(trial, tmp_path / "fixture")

    error = caught.value
    assert type(error) is not ValueError, (
        "a bare ValueError cannot be branched on; the host arm needs its own type"
    )
    assert getattr(error, "reason_code", "") == "no_journal_on_host_arm"


@pytest.mark.asyncio
async def test_replay_fixture_extraction_current_truth_is_a_bare_valueerror(tmp_path):
    """GREEN companion: pin today's refusal so a fix has to move this row."""
    module = _load_extract_replay_fixture()
    trial = await _trial_directory(tmp_path)

    with pytest.raises(ValueError) as caught:
        module.extract_fixture(trial, tmp_path / "fixture")

    assert type(caught.value) is ValueError
    assert "events.jsonl" in str(caught.value)
    assert not hasattr(caught.value, "reason_code")
