import hashlib
import json
import pytest

from gt_engine.event_journal import verify_event_journal
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.persistent_plan import build_plan_inputs
from gt_engine.persistent_plan.bootstrap import build_plan
from gt_engine.task_contract import extract_task_contract
from gt_engine.verification_contract import compile_obligation_predicates


@pytest.mark.parametrize("outcomes", [("pass", "fail"), ("fail", "pass"), ("pass", "unknown")])
def test_contradictory_exact_assertions_cannot_advance_predicates(tmp_path, outcomes):
    contract = extract_task_contract('The identifier must start with "urn:gt:".')
    compiled = compile_obligation_predicates(contract)
    adapter = MiniSweAdapter(task_id="contradiction", state_dir=tmp_path / "state", repo_root=tmp_path,
                             predicates=[Predicate(row.predicate_id, row.kind) for row in compiled.values()],
                             contract=contract)
    adapter.start_task()
    literal = hashlib.sha256(b"urn:gt:").hexdigest()
    output = "test_id.py::test_prefix PASSED\n1 passed\n" + "\n".join(
        f"GT_SEMANTIC_ASSERT relation=starts_with literal_sha256={literal} result={result}"
        for result in outcomes)
    assert adapter.evaluate_observation("pytest -v test_id.py", output, returncode=0, action_index=1) == ()
    assert adapter.unmet_predicates
    assert adapter.final_state()["predicate_evidence"] == {}


def test_exact_assertion_metadata_survives_controller_journal_and_summary(tmp_path):
    task = 'The identifier must start with "urn:gt:".'
    contract = extract_task_contract(task)
    compiled = compile_obligation_predicates(contract)
    adapter = MiniSweAdapter(task_id="metadata", state_dir=tmp_path / "state", repo_root=tmp_path,
                             predicates=[Predicate(row.predicate_id, row.kind) for row in compiled.values()],
                             contract=contract)
    adapter.start_task()
    output = ("test_id.py::test_prefix PASSED\n1 passed\n"
              f"GT_SEMANTIC_ASSERT relation=starts_with literal_sha256={hashlib.sha256(b'urn:gt:').hexdigest()} result=pass")
    green = adapter.evaluate_observation("pytest -v test_id.py", output, returncode=0, action_index=1)
    assert len(green) == 1
    evidence = adapter.final_state()["predicate_evidence"][green[0]]
    assert evidence["coverage_basis"] == "exact_operator_literal_assertion"
    assert evidence["evidence_kind"] == "behavior"
    assert evidence["action_index"] == 1
    assert evidence["execution_protocol"] == "pytest"
    assert "command" not in evidence
    events = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    persisted = next(row for row in events if row["event"] == "predicate_receipt_recorded")
    assert persisted["coverage_basis"] == evidence["coverage_basis"]
    assert persisted["output_hash"] == evidence["output_hash"]
    assert verify_event_journal(adapter.store.path).valid


def test_legacy_receipt_does_not_gain_an_inferred_assertion_basis(tmp_path):
    adapter = MiniSweAdapter(task_id="legacy", state_dir=tmp_path, predicates=[Predicate("p", "p")])
    adapter.record_receipt("p", "pytest", 0, "1 passed", epoch=0, semantic=True)
    evidence = adapter.final_state()["predicate_evidence"]["p"]
    assert evidence["coverage_basis"] == "legacy_unspecified"
    assert evidence["source_revision_at_observation"] == ""
    assert evidence["action_index"] is None


def test_empty_predicates_or_unmapped_plan_cannot_claim_verified_completion(tmp_path):
    for with_plan in (False, True):
        adapter = MiniSweAdapter(task_id=str(with_plan), state_dir=tmp_path, predicates=())
        if with_plan:
            adapter.persistent_plan = build_plan(None, build_plan_inputs(
                "The widget must handle retries.", repo_root=str(tmp_path), capture_baseline=False))
        adapter.start_task()
        adapter.begin_verify()
        adapter.begin_submit()
        assert adapter.submit_decision() is True
        assert adapter.final_state()["verified"] is False


def test_historical_evidence_keeps_its_scope_and_is_not_restored_as_current(tmp_path):
    first = MiniSweAdapter(task_id="history", state_dir=tmp_path, predicates=[Predicate("p", "p")])
    first.record_receipt("p", "pytest", 0, "1 passed", epoch=0, semantic=True,
                         evidence_kind="behavior", coverage_basis="exact_operator_literal_assertion",
                         source_revision_at_observation="old", action_index=1)
    resumed = MiniSweAdapter(task_id="history", state_dir=tmp_path, predicates=[Predicate("p", "p")])
    assert resumed.unmet_predicates == ("p",)
    assert resumed.final_state()["predicate_evidence"] == {}
    rows = [json.loads(line) for line in resumed.store.path.read_text().splitlines()]
    original = next(row for row in rows if row["event"] == "predicate_receipt_recorded")
    assert original["source_revision_at_observation"] == "old"
    assert original["coverage_basis"] == "exact_operator_literal_assertion"


def test_unmapped_plan_stays_unverified_even_with_all_registered_predicates_green(tmp_path):
    adapter = MiniSweAdapter(task_id="unmapped", state_dir=tmp_path, predicates=[Predicate("p", "p")])
    adapter.persistent_plan = build_plan(None, build_plan_inputs(
        "The widget must preserve its caller contract.", repo_root=str(tmp_path), capture_baseline=False))
    adapter.start_task()
    adapter.begin_verify()
    adapter.record_receipt("p", "pytest", 0, "1 passed", epoch=0, semantic=True)
    adapter.begin_submit()
    assert adapter.submit_decision() is True
    assert adapter.final_state()["verified"] is False
