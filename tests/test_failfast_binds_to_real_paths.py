from __future__ import annotations

import ast
from pathlib import Path

import pytest

import gt_engine
from gt_engine import indexer
from gt_engine.indexer import BenchmarkGraphRequired

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def benchmark_run(monkeypatch):
    monkeypatch.setenv("GT_TASK_ID", "arktype-json-schema-refs-dependencies")
    monkeypatch.setenv("GT_PRODUCT_SOURCE_SHA", "2" * 40)


def test_create_bridge_propagates_instead_of_going_dormant(
    benchmark_run, monkeypatch, tmp_path: Path
):
    """The gap REV-245 found: a broad handler swallowed the refusal.

    `create_bridge` promises GT never breaks the harness, which is right for
    local work. On a benchmark run it turned a refusal back into a dormant
    bridge and the run continued to provider calls.
    """

    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(indexer, "_ensure_index_unlocked", lambda root, state_dir=None, **_: None)

    with pytest.raises(BenchmarkGraphRequired):
        gt_engine.create_bridge(str(tmp_path))


def test_create_bridge_still_goes_dormant_for_local_work(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("GT_TASK_ID", raising=False)
    monkeypatch.delenv("GT_PRODUCT_SOURCE_SHA", raising=False)
    monkeypatch.setattr(indexer, "is_code_repo", lambda root: True)
    monkeypatch.setattr(indexer, "_ensure_index_unlocked", lambda root, state_dir=None, **_: None)

    # A dormant bridge, not an exception: unchanged behaviour outside a benchmark.
    assert gt_engine.create_bridge(str(tmp_path)) is not None


def _handlers_guarding(call_name: str, source: str) -> list[str]:
    """Names of exceptions handled by the try block wrapping `call`."""

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        calls = (child for statement in node.body for child in ast.walk(statement)
                 if isinstance(child, ast.Call))
        if not any(isinstance(child.func, ast.Name) and child.func.id == call_name
                   for child in calls):
            continue
        names = []
        for handler in node.handlers:
            names.append("" if handler.type is None else ast.unparse(handler.type))
        return names
    return []


def test_the_benchmark_runner_carries_the_refusal_to_the_owner_thread():
    """`scripts/miniswe_gt_run.py` is the path a paid run actually takes.

    The initial index builds on a daemon worker so large repositories do not
    starve the host. A refusal must therefore NOT be handled at the call site:
    the bare call raises into the startup future, whose `BaseException` carry
    transports it to the owner-thread poll for classification. Swallowing it
    here - the failure REV-245 found - would make the run look index-healthy.
    """

    source = (REPO / "scripts" / "miniswe_gt_run.py").read_text(encoding="utf-8")

    # The index call is bare inside the worker fn: it must propagate.
    assert _handlers_guarding("ensure_index_with_receipt", source) == []
    # The future catches BaseException into `_exc`: carried, never swallowed.
    assert "BaseException" in _handlers_guarding("fn", source)


def test_the_refusal_reaches_the_startup_abort_flag():
    """The poll on the owner thread is where the carried refusal lands.

    `BenchmarkGraphRequired` is a benchmark-admission refusal, not an index
    gap the run can absorb: the poll must route it to `signal_startup_abort`
    so the supervisor seals the run as a typed setup failure.
    """

    source = (REPO / "gt_engine" / "miniswe_integration.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    poll = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_poll_startup_index"
    )
    text = ast.get_source_segment(source, poll) or ""
    assert "BenchmarkGraphRequired" in text
    assert "signal_startup_abort" in text
    assert text.index("BenchmarkGraphRequired") < text.index("signal_startup_abort")


def test_the_bridge_path_lets_the_refusal_through():
    source = (REPO / "gt_engine" / "__init__.py").read_text(encoding="utf-8")
    handlers = _handlers_guarding("ensure_index", source)

    assert "_must_propagate" in handlers
    assert handlers.index("_must_propagate") < handlers.index("Exception")


def test_real_build_agent_surfaces_refusal_as_startup_abort(monkeypatch, tmp_path):
    """The async-startup contract: `build_agent` returns a live agent while the
    index builds on the worker, so a benchmark-admission refusal cannot raise
    here. What it must do instead is surface - through the owner-thread poll -
    as the `startup_abort` flag the supervisor maps to a typed setup failure."""
    import json

    from gt_engine import bridge
    from gt_engine.miniswe_typed_actions import GroundTruthLitellmModel
    from scripts import miniswe_gt_run as runner

    monkeypatch.delenv("GT_KILL_SWITCH", raising=False)
    monkeypatch.setattr(bridge, "apply_profile_env", lambda: None)
    monkeypatch.setattr(runner, "_model_and_kwargs", lambda *_: ("openai/test", {}))
    calls = []

    def refuse(*args, **kwargs):
        calls.append("index")
        raise BenchmarkGraphRequired("regression refusal")

    def provider(*args, **kwargs):
        pytest.fail("provider must not be called after refused product admission")

    monkeypatch.setattr(indexer, "ensure_index_with_receipt", refuse)
    monkeypatch.setattr(GroundTruthLitellmModel, "query", provider)
    agent, adapter, _session = runner.build_agent(
        task="Repair the parser", model="test", cwd=str(tmp_path),
        state_dir=str(tmp_path / "state"), output=None,
        temperature=0, gt_off=False)
    assert agent is not None and calls == ["index"]

    future = adapter._startup_index
    assert future is not None
    assert future._event.wait(timeout=10)
    adapter._poll_startup_index()

    flag = adapter.engine_state.layout.state_root / "startup_abort.json"
    assert flag.is_file()
    payload = json.loads(flag.read_text(encoding="utf-8"))
    assert payload["reason"] == "initial_index_failed:benchmark_graph_required"
    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        row.get("event") == "index_unavailable"
        and row.get("error_type") == "BenchmarkGraphRequired"
        for row in rows
    )
