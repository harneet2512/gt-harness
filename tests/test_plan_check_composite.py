"""Composite verification commands decompose into per-segment bound checks."""
import json

import pytest

from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.persistent_plan import build_plan_inputs
from gt_engine.persistent_plan.bootstrap import build_plan
from gt_engine.persistent_plan.checks import decompose_check_command

PROMPT = "The widget must preserve compatibility.\nThe widget must reject invalid input."


def adapter_at(tmp_path, commands):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "test_a.py").write_text("def test_a(): assert True\n", encoding="utf-8")
    (repo / "tests" / "test_b.py").write_text("def test_b(): assert True\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="composite", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    inputs = build_plan_inputs(PROMPT, repo_root=str(repo), capture_baseline=False)
    ids = [row.row_id for row in inputs.ledger.rows]
    payload = {"rows": [
        {"row_id": rid, "verification_command": cmd}
        for rid, cmd in zip(ids, commands)
    ]}
    adapter.persistent_plan = build_plan(payload, inputs, repo_root=str(repo))
    adapter.store.startup_journal_valid = True
    return adapter, ids


def test_decompose_and_chain():
    segments, seps, last_check, reason = decompose_check_command(
        "pytest tests/test_a.py && pytest tests/test_b.py"
    )
    assert reason is None
    assert seps == ("&&",)
    assert last_check
    assert segments == [
        (["pytest", "tests/test_a.py"], None),
        (["pytest", "tests/test_b.py"], None),
    ]


def test_decompose_cd_fold():
    segments, seps, last_check, reason = decompose_check_command(
        "cd tests && pytest test_a.py"
    )
    assert reason is None
    assert segments == [(["pytest", "test_a.py"], "tests")]


def test_decompose_rejects_unattributable_operators():
    for command in ("pytest a | tail", "pytest a || echo fail", "pytest a > out.txt"):
        segments, seps, last_check, reason = decompose_check_command(command)
        assert segments is None, command
        assert reason and reason.startswith("unsupported_shell_operator"), command


def test_decompose_semicolon_last_raw_cd():
    # ``pytest; cd x`` puts the composite rc on cd — not the check segment.
    segments, seps, last_check, reason = decompose_check_command(
        "pytest tests/test_a.py ; cd tests"
    )
    assert segments == [(["pytest", "tests/test_a.py"], None)]
    assert not last_check


def test_composite_and_chain_binds_both_segments(tmp_path):
    adapter, ids = adapter_at(
        tmp_path,
        ["pytest tests/test_a.py && pytest tests/test_b.py", "pytest tests/test_a.py"],
    )
    adapter.bind_initial_plan_checks()
    bound = [
        json.loads(line) for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if '"plan_check_bound"' in line
    ]
    argvs = {tuple(row["argv"]) for row in bound}
    assert ("pytest", "tests/test_a.py") in argvs
    assert ("pytest", "tests/test_b.py") in argvs
    pending = [
        json.loads(line) for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if '"plan_check_binding_pending"' in line
    ]
    assert not pending, pending


def test_cd_prefixed_command_binds_with_cwd(tmp_path):
    adapter, ids = adapter_at(
        tmp_path, ["cd tests && pytest test_a.py", "pytest tests/test_a.py"]
    )
    adapter.bind_initial_plan_checks()
    cwds = {spec.cwd for spec in adapter._check_specs.values()}
    assert "tests" in cwds


def test_piped_command_stays_pending(tmp_path):
    adapter, ids = adapter_at(
        tmp_path, ["pytest tests/test_a.py | tail -1", "pytest tests/test_a.py"]
    )
    adapter.bind_initial_plan_checks()
    pending = [
        json.loads(line) for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if '"plan_check_binding_pending"' in line
    ]
    assert any("unsupported_shell_operator" in str(row.get("reason")) for row in pending)


def test_mixed_composite_refuses_partial_binding(tmp_path):
    # ``make build && pytest`` — dropping the build step would change what the
    # row's verification means; the row must stay pending, not half-bound.
    adapter, ids = adapter_at(
        tmp_path, ["make build && pytest tests/test_a.py", "pytest tests/test_a.py"]
    )
    adapter.bind_initial_plan_checks()
    pending = [
        json.loads(line) for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if '"plan_check_binding_pending"' in line
    ]
    assert any("non_test_segment" in str(row.get("reason")) for row in pending)
    specs = [
        spec for spec in adapter._check_specs.values()
        if ids[0] in spec.requirement_ids
    ]
    assert not specs
