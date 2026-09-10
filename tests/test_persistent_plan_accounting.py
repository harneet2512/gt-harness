"""The plan's complete accounting: every row, in every dimension, by name.

Counts without names cannot be audited, and the one count that was published
turned out to mean something other than what it said. So these tests check the
partitions rather than the totals: every row lands in exactly one bucket of each
dimension, the numbers are derived from the lists, and the exposure dimension
refuses to count a row the model could have retrieved as a row the model was
shown.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from gt_engine.persistent_plan import build_plan_inputs
from gt_engine.persistent_plan.accounting import plan_accounting
from gt_engine.persistent_plan.bootstrap import build_plan
from gt_engine.persistent_plan.render import render_plan_block

PROMPT = (
    "Add a strict mode to the container loader.\n"
    "\n"
    "Assumptions:\n"
    " build_container must accept a registry argument\n"
    " The loader must document its retry behaviour for operators\n"
    " Errors must name the failing service\n"
)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_container.py").write_text(
        "def test_container(): assert True\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def plan(repo):
    inputs = build_plan_inputs(PROMPT, repo_root=str(repo), source_revision="src1",
                               capture_baseline=False)
    ids = [row.row_id for row in inputs.ledger.rows]
    payload = {"rows": [
        {"row_id": ids[0], "verification_command": "pytest tests/test_container.py"},
        {"row_id": ids[1], "verification_command": "pytest tests/test_absent.py"},
        {"row_id": ids[2], "verification_command": "pytest"},
    ]}
    return build_plan(payload, inputs, repo_root=str(repo))


def test_every_row_lands_in_exactly_one_bucket_of_every_dimension(plan):
    report = plan_accounting(plan)
    ids = set(report["row_ids"])
    assert len(ids) == report["rows_total"] == len(plan.rows)
    for dimension in ("symbols", "checks", "evidence"):
        placed = [row_id for values in report[dimension].values() for row_id in values]
        assert sorted(placed) == sorted(ids), dimension
        assert len(placed) == len(set(placed)), f"{dimension} placed a row twice"


def test_the_numbers_are_derived_from_the_lists_not_counted_apart(plan):
    report = plan_accounting(plan)
    for dimension in ("symbols", "checks", "evidence"):
        for name, values in report[dimension].items():
            assert report["counts"][f"{dimension}_{name}"] == len(values)
    assert report["counts"]["rows_total"] == len(report["row_ids"])


def test_checks_are_split_into_existing_proposed_and_unnamed(plan):
    report = plan_accounting(plan)
    assert report["counts"]["checks_existing"] == 1
    assert report["counts"]["checks_proposed"] == 1
    assert report["counts"]["checks_unnamed"] == 1


def test_a_retrievable_row_id_is_not_counted_as_model_exposed(plan):
    receipt = {}
    render_plan_block(plan, receipt=receipt, limit=len("[GT_PERSISTENT_PLAN]") + 400)
    report = plan_accounting(plan, rendering=receipt)
    exposure = report["exposure"]
    # The index always lists every row; the block is capped, so some rows are
    # offered and never delivered. Only the delivered ones are exposure.
    assert set(exposure["retrievable_row_ids"]) == set(report["row_ids"])
    assert exposure["omitted_row_ids"]
    assert set(exposure["model_exposed_row_ids"]) < set(exposure["retrievable_row_ids"])
    assert report["counts"]["model_exposed"] < report["counts"]["retrievable"]


def test_a_fully_delivered_block_exposes_every_row_and_pins_its_digest(plan):
    receipt = {}
    block = render_plan_block(plan, receipt=receipt)
    report = plan_accounting(plan, rendering=receipt)
    exposure = report["exposure"]
    assert set(exposure["model_exposed_row_ids"]) == set(report["row_ids"])
    assert not exposure["omitted_row_ids"]
    assert exposure["rendered_sha256"] == hashlib.sha256(block.encode("utf-8")).hexdigest()


def test_a_failure_outranks_a_pass_and_a_proof_outranks_a_pass(plan):
    ids = [row.row_id for row in plan.rows]
    report = plan_accounting(
        plan,
        states={ids[0]: "CHECK_PASSED", ids[1]: "CHECK_FAILED", ids[2]: "CHECK_PASSED"},
        proven=[ids[2]],
        executed=ids,
    )
    assert report["evidence"]["passed"] == [ids[0]]
    assert report["evidence"]["failed"] == [ids[1]]
    assert report["evidence"]["proven"] == [ids[2]]
    assert report["executed"] == sorted(ids)


def test_a_row_the_plan_does_not_contain_cannot_be_reported_as_proven(plan):
    report = plan_accounting(plan, proven=["req-not-in-this-plan"], executed=["req-not-in-this-plan"])
    assert report["evidence"]["proven"] == []
    assert report["executed"] == []


def test_the_adapter_publishes_the_accounting_to_the_file_and_the_journal(tmp_path, repo, plan):
    from gt_engine.event_journal import verify_event_journal
    from gt_engine.miniswe_integration import MiniSweAdapter

    adapter = MiniSweAdapter(task_id="accounting", state_dir=tmp_path / "state",
                             repo_root=str(repo), predicates=())
    adapter.persistent_plan = plan
    receipt = {}
    render_plan_block(plan, receipt=receipt)
    adapter.plan_rendering_receipt = receipt
    adapter.publish_plan_state()

    published = json.loads((adapter.store.root / "plan" / "current.json").read_text(encoding="utf-8"))
    accounting = published["accounting"]
    assert accounting["schema"] == "gt.plan_accounting.v1"
    assert sorted(accounting["row_ids"]) == sorted(row.row_id for row in plan.rows)
    assert accounting["exposure"]["rendered_sha256"] == receipt["rendered_sha256"]

    rows = [json.loads(line) for line in adapter.store.path.read_text(encoding="utf-8").splitlines()]
    event = next(row for row in rows if row.get("event") == "plan_accounting")
    assert event["accounting_layout"] == "gt.plan_accounting.v1"
    assert event["rendered_sha256"] == receipt["rendered_sha256"]
    assert event["rows_total"] == len(plan.rows)
    assert event["model_exposed"] == len(accounting["exposure"]["model_exposed_row_ids"])
    assert verify_event_journal(adapter.store.path).valid
