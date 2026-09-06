from __future__ import annotations

import os
import subprocess
import sys

import pytest

from gt_engine.miniswe_controller import GroundtruthController, Predicate, PredicateStatus
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.runtime_observation import capture_workspace, diff_workspace
from gt_engine.task_contract import Obligation, TaskContract
from gt_engine.verification_contract import (
    DependencyFootprint,
    DependencyIdentity,
    PredicateReceipt,
    certified_path_footprint,
    compile_obligation_predicates,
    dependency_footprint_affected,
    predicate_receipt_footprint,
    recorded_graph_dependency_footprint,
)


def _green(controller: GroundtruthController, predicate_id: str, footprint: DependencyFootprint):
    return controller.record_receipt(
        predicate_id,
        "python -m pytest tests/test_consumer.py",
        0,
        "1 passed",
        epoch=controller.workspace_epoch,
        status="GREEN",
        semantic=True,
        dependency_footprint=footprint,
    )


def test_imported_dependency_edit_invalidates_actual_green_before_rerun(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests" / "test_consumer.py").write_text(
        "import unittest\n"
        "from src.consumer import value\n"
        "class ConsumerAnswerTest(unittest.TestCase):\n"
        "    def test_consumer_answer(self):\n"
        "        print('Consumer answer check')\n"
        "        self.assertEqual(value(), 1)\n",
        encoding="utf-8",
    )
    (repo / "src" / "consumer.py").write_text(
        "from .helper import answer\ndef value(): return answer\n", encoding="utf-8"
    )
    helper = repo / "src" / "helper.py"
    helper.write_text("answer = 1\n", encoding="utf-8")
    contract = TaskContract(
        "code_behavior",
        (Obligation("consumer", "Consumer answer must remain one.", "task"),),
    )
    compiled = compile_obligation_predicates(contract)["consumer"]
    adapter = MiniSweAdapter(
        task_id="dependency",
        state_dir=tmp_path / "state",
        predicates=[Predicate(compiled.predicate_id, compiled.obligation_id)],
        contract=contract,
        repo_root=repo,
    )
    adapter.start_task()
    command = f'"{sys.executable}" -B -m unittest tests.test_consumer -v'
    first = subprocess.run(
        command, cwd=repo, shell=True, capture_output=True, text=True, timeout=20
    )
    first_output = first.stdout + first.stderr
    assert first.returncode == 0, first_output
    assert adapter.evaluate_observation(
        command, first_output, returncode=first.returncode, action_index=1
    ) == (compiled.predicate_id,)
    recorded = recorded_graph_dependency_footprint(
        ("tests/test_consumer.py",),
        (
            ("tests/test_consumer.py", "src/consumer.py", "import"),
            ("src/consumer.py", "src/helper.py", "import"),
        ),
    )
    assert DependencyIdentity("path", "src/helper.py") in recorded.identities
    assert adapter.predicate_status(compiled.predicate_id) is PredicateStatus.GREEN

    before = capture_workspace(repo)
    helper.write_text("answer = 2\n", encoding="utf-8")
    transaction = diff_workspace(
        before, capture_workspace(repo), action_id=2, command="edit src/helper.py"
    )
    assert transaction.changed_paths == ("src/helper.py",)
    adapter.note_edit(transaction.changed_paths)

    assert adapter.predicate_status(compiled.predicate_id) is PredicateStatus.UNKNOWN
    assert adapter.blocking_predicates == (compiled.predicate_id,)

    second = subprocess.run(
        command, cwd=repo, shell=True, capture_output=True, text=True, timeout=20
    )
    second_output = second.stdout + second.stderr
    assert second.returncode != 0, second_output
    assert adapter.evaluate_failing_observation(
        command, second_output, returncode=second.returncode, action_index=2
    ) == (compiled.predicate_id,)
    assert adapter.predicate_status(compiled.predicate_id) is PredicateStatus.RED


def test_complete_disjoint_artifact_proof_survives_and_is_rebased() -> None:
    controller = GroundtruthController([Predicate("artifact", "artifact exists")])
    controller.start_task()
    footprint = certified_path_footprint(
        ("dist/report.json",), basis="live_artifact_stat"
    )
    original = _green(controller, "artifact", footprint)

    controller.note_edit(["docs/readme.md"], invalidate=())

    assert controller.predicate_status("artifact") is PredicateStatus.GREEN
    assert controller.blocking_predicates == ()
    assert original.epoch == 0
    assert controller._receipts["artifact"].epoch == controller.workspace_epoch == 1


def test_live_artifact_stat_survives_unrelated_actual_edit(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    artifact = repo / "dist" / "report.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}\n", encoding="utf-8")
    (repo / "docs").mkdir()
    readme = repo / "docs" / "readme.md"
    readme.write_text("before\n", encoding="utf-8")
    contract = TaskContract(
        "code_behavior",
        (Obligation("artifact", "The artifact `dist/report.json` must exist.", "task"),),
    )
    compiled = compile_obligation_predicates(contract)["artifact"]
    adapter = MiniSweAdapter(
        task_id="artifact",
        state_dir=tmp_path / "state",
        predicates=[Predicate(compiled.predicate_id, compiled.obligation_id)],
        contract=contract,
        repo_root=repo,
    )
    adapter.start_task()
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    assert adapter.verify_live_submit() == (compiled.predicate_id,)
    receipt = adapter._receipts[compiled.predicate_id]
    assert receipt.dependency_footprint is not None
    assert receipt.dependency_footprint.complete is True

    before = capture_workspace(repo)
    readme.write_text("after\n", encoding="utf-8")
    transaction = diff_workspace(
        before, capture_workspace(repo), action_id=1, command="edit docs/readme.md"
    )
    adapter.note_edit(transaction.changed_paths)

    assert adapter.predicate_status(compiled.predicate_id) is PredicateStatus.GREEN
    assert adapter._receipts[compiled.predicate_id].epoch == adapter.workspace_epoch == 1
    assert adapter.blocking_predicates == ()


def test_external_or_symlink_artifact_scope_is_never_complete(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    external = tmp_path / "external.json"
    external.write_text("{}\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="external",
        state_dir=tmp_path / "state",
        predicates=[],
        repo_root=repo,
    )
    adapter.start_task()
    assert adapter._live_artifact_footprint((str(external),)).complete is False

    link = repo / "linked.json"
    try:
        os.symlink(external, link)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    assert adapter._live_artifact_footprint(("linked.json",)).complete is False


def test_unknown_dependency_scope_invalidates_on_any_edit() -> None:
    controller = GroundtruthController([Predicate("global", "global behavior")])
    controller.start_task()
    _green(
        controller,
        "global",
        DependencyFootprint(
            identities=(DependencyIdentity("workspace", "."),),
            complete=False,
            basis="unknown_dynamic_dependencies",
        ),
    )

    controller.note_edit(["unrelated.txt"], invalidate=())

    assert controller.predicate_status("global") is PredicateStatus.UNKNOWN


def test_stale_epoch_green_is_unmet_in_context_and_submission_truth() -> None:
    controller = GroundtruthController([Predicate("proof", "current proof")])
    controller.start_task()
    _green(
        controller,
        "proof",
        certified_path_footprint(("proof.txt",), basis="live_artifact_stat"),
    )
    # Simulate restored/corrupt legacy state whose status survived without the
    # receipt being rebased. Both public truth surfaces must fail closed.
    controller.workspace_epoch += 1

    assert controller.unmet_predicates == ("proof",)
    assert controller.blocking_predicates == ("proof",)
    assert "proof" in controller.provider_suffix()


def test_arbitrary_artifact_command_does_not_claim_complete_path_scope() -> None:
    contract = TaskContract(
        "code_behavior",
        (Obligation("artifact", "The artifact `dist/report.json` must exist.", "task"),),
    )
    predicate = compile_obligation_predicates(contract)["artifact"]
    receipt = PredicateReceipt(
        predicate_id=predicate.predicate_id,
        obligation_id="artifact",
        kind="artifact",
        outcome="pass",
        command_sha256="a" * 64,
        output_sha256="b" * 64,
        action_index=1,
        coverage_basis="scoped_artifact_assertion",
    )

    footprint = predicate_receipt_footprint(predicate, receipt)

    assert footprint.complete is False
    assert DependencyIdentity("workspace", ".") in footprint.identities
    assert DependencyIdentity("environment", "*") in footprint.identities
    assert dependency_footprint_affected(footprint, ["src/helper.py"]) is True
    assert dependency_footprint_affected(footprint, ["dist/report.json"]) is True


def test_complete_path_scope_requires_trusted_live_stat_basis() -> None:
    footprint = certified_path_footprint(
        ("./dist/report.json",), basis="live_artifact_stat"
    )
    assert footprint.complete is True
    assert footprint.identities == (DependencyIdentity("path", "dist/report.json"),)
    assert dependency_footprint_affected(footprint, ["docs/readme.md"]) is False

    with pytest.raises(ValueError, match="live_artifact_stat"):
        certified_path_footprint(("dist/report.json",), basis="model_command")
