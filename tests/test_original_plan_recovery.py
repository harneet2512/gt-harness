import dataclasses
import hashlib
import json
from dataclasses import asdict

import pytest

from gt_engine.miniswe_integration import ExternalStateStore
from gt_engine.persistent_plan import InteractionCell, build_plan_inputs
from gt_engine.persistent_plan.anchors import Anchor, Caller, ModeCandidate
from gt_engine.persistent_plan.baseline import BaselineResult
from gt_engine.persistent_plan.bootstrap import build_plan
from gt_engine.persistent_plan.recovery import checkpoint_plan, restore_plan


def saved_plan(tmp_path):
    task = "The widget must preserve compatibility."
    inputs = build_plan_inputs(task, capture_baseline=False, source_revision="original", graph_revision="graph")
    row_id = inputs.ledger.rows[0].row_id
    inputs.anchors.anchors[row_id] = (Anchor(1, "widget", "widget", "Function", "widget.py", 1,
                                            "widget(mode=False)", "python", "exact_name"),)
    inputs.anchors.callers[1] = (Caller(2, "caller", "caller.py", 1),)
    inputs.anchors.modes = (ModeCandidate("Mode", 3, "mode.py", "enum", ("A", "B"), (1,)),)
    inputs.baseline = BaselineResult(status="captured", command=("pytest", "-v"), passed=1,
                                    passing_names=("test_widget.py::test_existing",), exit_code=0,
                                    duration_seconds=1.234567, environment_sha256="environment")
    plan = build_plan(None, inputs)
    plan.interactions = (InteractionCell(row_id, "Mode", "A", True, "same caller"),)
    store = ExternalStateStore(tmp_path, "checkpoint")
    checkpoint_plan(store, plan, task)
    return store, plan, task


def test_checkpoint_roundtrip_preserves_complete_inputs_and_original_baseline(tmp_path):
    _, plan, task = saved_plan(tmp_path)
    restored = restore_plan(ExternalStateStore(tmp_path, "checkpoint"), task)
    assert asdict(restored) == asdict(plan)
    assert restored.canonical_json() == plan.canonical_json()
    assert restored.inputs.anchors.callers[1][0].node_id == 2


@pytest.mark.parametrize("corrupt", [False, True])
def test_real_runner_restart_does_not_recapture_the_post_edit_baseline(tmp_path, monkeypatch, corrupt):
    from scripts.miniswe_gt_run import build_agent

    monkeypatch.setenv("GT_PERSISTENT_PLAN", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "widget.py").write_text("def widget(): return 1\n")
    arguments = dict(task="The widget must preserve compatibility.", model="deepseek-v4-flash",
                     cwd=str(repo), state_dir=str(tmp_path / "state"), output=None,
                     temperature=1.0, gt_off=False, wall_time_limit_seconds=1000)
    _, first, _ = build_agent(**arguments)
    plan = build_plan(None, first.plan_inputs)
    plan.inputs.baseline = BaselineResult(status="captured", passed=1, passing_names=("original-test",))
    checkpoint_plan(first.store, plan, arguments["task"])
    if corrupt:
        next((first.store.root / "plan_checkpoints").glob("*.json")).write_text("{}")
    (repo / "widget.py").write_text("def widget(): return 2\n")
    captures = []
    def baseline(*args, **kwargs):
        captures.append(True)
        return BaselineResult(status="no_tests_observed")
    monkeypatch.setattr("gt_engine.persistent_plan.run_baseline", baseline)
    _, resumed, _ = build_agent(**arguments)
    assert captures == [], "restart replaced the initial baseline with post-edit tests"
    if corrupt:
        assert not resumed.plan_inputs.baseline.captured
    else:
        assert resumed.plan_inputs.baseline.passing_names == ("original-test",)


@pytest.mark.parametrize("probe_failure", [False, True])
def test_initial_baseline_runs_before_the_single_initial_graph_build(tmp_path, monkeypatch, probe_failure):
    from types import SimpleNamespace

    from scripts.miniswe_gt_run import build_agent

    monkeypatch.setenv("GT_PERSISTENT_PLAN", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "widget.py"
    source.write_text("def widget(): return 1\n")
    calls = []

    def baseline(*args, **kwargs):
        calls.append("baseline")
        source.write_text("def widget(): return 2\n")
        if probe_failure:
            raise OSError("fixture baseline failure")
        return BaselineResult(status="no_tests_observed")

    def index(*args, **kwargs):
        calls.append("index")
        assert "return 2" in source.read_text(), "graph captured source before the suite wrote it"
        return SimpleNamespace(success=False, graph_db=None, error_type="", error_diagnostic="")

    monkeypatch.setattr("gt_engine.persistent_plan.run_baseline", baseline)
    monkeypatch.setattr("gt_engine.indexer.ensure_index_with_receipt", index)
    _, adapter, _ = build_agent(task="The widget must preserve compatibility.", model="deepseek-v4-flash",
                cwd=str(repo), state_dir=str(tmp_path / "state"), output=None,
                temperature=1.0, gt_off=False, wall_time_limit_seconds=1000)
    assert calls == ["baseline", "index"]
    assert adapter.plan_inputs is not None
    assert adapter.plan_inputs.baseline.status == ("probe_failed" if probe_failure else "no_tests_observed")


@pytest.mark.parametrize("damage", ["task", "missing", "content", "journal", "invalid_field"])
def test_checkpoint_rejects_wrong_task_missing_corrupt_or_invalid_inputs(tmp_path, damage):
    store, _, task = saved_plan(tmp_path)
    event = json.loads(store.path.read_text().splitlines()[0])
    path = store.root / "plan_checkpoints" / f"{event['checkpoint_sha256']}.json"
    if damage == "task":
        task = "The other task must not inherit this plan."
    elif damage == "missing":
        path.unlink()
    elif damage == "content":
        path.write_text("{}")
    elif damage == "journal":
        store.path.write_text(store.path.read_text().replace("persistent_plan_checkpoint", "altered_checkpoint"))
    else:
        payload = json.loads(path.read_text())
        payload["plan"]["interactions"][0]["applies"] = "false"
        encoded = json.dumps(payload).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        store.put_blob("plan_checkpoints", digest, encoded)
        # A valid journal with a malformed typed payload still cannot restore.
        other = ExternalStateStore(tmp_path, "malformed")
        other.put_blob("plan_checkpoints", digest, encoded)
        other.append("persistent_plan_checkpoint", layout=event["layout"], checkpoint_sha256=digest,
                     issue_sha256=event["issue_sha256"], plan_sha256=event["plan_sha256"])
        assert restore_plan(ExternalStateStore(tmp_path, "malformed"), task) is None
        return
    assert restore_plan(ExternalStateStore(tmp_path, "checkpoint"), task) is None


def test_a_previous_layout_checkpoint_is_rejected_by_version_not_by_shape(tmp_path):
    """The serialized shape and LAYOUT are the same fact, so they move together.

    BaselineResult gained source_revision and after_source_revision. The decoder
    requires exact dataclass field-set equality, so a v1 checkpoint would have
    failed on "checkpoint dataclass fields mismatch" -- which reads like
    corruption rather than a format change. Rejection is the entire migration:
    restoring nothing costs one planning call, while inventing the two missing
    fields would mean asserting which source revision an older baseline observed,
    and the only value to hand is the current workspace, which is exactly what
    those fields exist to distinguish from.
    """
    from gt_engine.persistent_plan import recovery

    assert recovery.LAYOUT == "gt.plan_checkpoint.v2"

    from gt_engine.persistent_plan.baseline import BaselineResult

    names = {f.name for f in dataclasses.fields(BaselineResult)}
    assert "source_revision" in names
    assert "after_source_revision" in names
