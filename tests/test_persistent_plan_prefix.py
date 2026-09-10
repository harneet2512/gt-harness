"""Delivery: the plan goes into the prefix once and never moves again.

The plan block is appended to the durable task message. Measured across a live
run that message was byte-identical in 182 of 184 requests, which is why it is
the right home for a large artifact: it is paid for once. These tests pin that
property rather than trusting it.
"""
from __future__ import annotations

from gt_engine.persistent_plan import (
    InteractionCell,
    PersistentPlan,
    PlanInputs,
    PlanRow,
)
from gt_engine.persistent_plan.anchors import Anchor, AnchorResult, Caller
from gt_engine.persistent_plan.baseline import BaselineResult
from gt_engine.persistent_plan.ledger import build_requirement_ledger
from gt_engine.persistent_plan.render import (
    MAX_BLOCK_CHARS,
    PLAN_TAG,
    render_plan_block,
)

PROMPT = (
    "The loader must retry twice.\n"
    "The loader must report the final error.\n"
)


def test_render_receipt_distinguishes_index_from_delivered_row_content():
    import hashlib

    plan = _plan()
    receipt = {}
    block = render_plan_block(plan, limit=1, receipt=receipt)
    assert receipt["indexed_row_ids"] == [row.row_id for row in plan.rows]
    assert receipt["rendered_requirement_row_ids"] == []
    assert receipt["complete_row_block_ids"] == []
    assert receipt["omitted_requirement_row_ids"] == receipt["indexed_row_ids"]
    assert receipt["rendered_sha256"] == hashlib.sha256(block.encode()).hexdigest()
    full_receipt = {}
    render_plan_block(plan, receipt=full_receipt)
    assert full_receipt["complete_row_block_ids"] == receipt["indexed_row_ids"]
    assert full_receipt["omitted_requirement_row_ids"] == []


def test_render_receipt_does_not_call_partial_row_complete():
    plan = _plan()
    full = render_plan_block(plan)
    first = plan.rows[0]
    row_end = full.index(f"  {first.row_id}: {first.text}") + len(f"  {first.row_id}: {first.text}") + 1
    receipt = {}
    render_plan_block(plan, limit=row_end, receipt=receipt)
    assert receipt["rendered_requirement_row_ids"] == [first.row_id]
    assert receipt["complete_row_block_ids"] == []
    assert receipt["omitted_requirement_row_ids"] == [plan.rows[1].row_id]


def test_render_receipt_clears_previous_delivery_on_abstention():
    plan = _plan()
    receipt = {}
    render_plan_block(plan, receipt=receipt)
    plan.status = "ABSTAINED"
    assert render_plan_block(plan, receipt=receipt) == ""
    assert receipt == {}


def _anchor(node_id: int = 1, name: str = "load") -> Anchor:
    return Anchor(
        node_id=node_id, name=name, qualified_name=name, label="Function",
        file_path="src/loader.py", start_line=12, signature=f"def {name}():",
        language="python", basis="exact_name",
    )


def _plan(*, captured: bool = True, interactions=(), abstentions=()) -> PersistentPlan:
    ledger = build_requirement_ledger(PROMPT)
    rows = ledger.rows
    anchors = AnchorResult(
        anchors={rows[0].row_id: (_anchor(),), rows[1].row_id: ()},
        callers={1: (Caller(2, "boot", "src/app.py", 1),)},
    )
    baseline = (
        BaselineResult(
            status="captured", command=("pytest",), passed=40, failed=1,
            failing_names=("tests/test_old.py::test_known",),
        )
        if captured
        else BaselineResult(status="no_test_command")
    )
    inputs = PlanInputs(ledger=ledger, anchors=anchors, baseline=baseline)
    return PersistentPlan(
        status="READY",
        inputs=inputs,
        rows=(
            PlanRow(
                row_id=rows[0].row_id, text=rows[0].text, anchors=(1,),
                verification_kind="existing_test",
                verification_command="pytest tests/test_loader.py",
            ),
            PlanRow(row_id=rows[1].row_id, text=rows[1].text),
        ),
        interactions=tuple(interactions),
        edit_order=(rows[0].row_id, rows[1].row_id),
        abstentions=tuple(abstentions),
    )


def test_the_block_states_requirements_proofs_and_the_completion_rule():
    block = render_plan_block(_plan())
    assert block.startswith(f"[{PLAN_TAG}]")
    assert "The loader must retry twice." in block
    assert "acceptance: pytest tests/test_loader.py" in block
    assert "load @ src/loader.py:12" in block
    assert "DONE when every requirement above has acceptance evidence" in block


def test_the_block_says_it_is_advisory():
    """A plan the agent cannot disagree with is a cage, not evidence."""
    block = render_plan_block(_plan())
    assert "advisory" in block
    assert "not a boundary" in block


def test_the_block_carries_the_blast_radius():
    block = render_plan_block(_plan())
    assert "IMPACT - callers reached" in block
    assert "boot @ src/app.py" in block


def test_the_block_carries_the_green_baseline_and_its_known_failures():
    block = render_plan_block(_plan())
    assert "40 passing" in block
    assert "Every test passing now must still pass" in block
    assert "tests/test_old.py::test_known" in block


def test_a_missing_baseline_is_stated_not_hidden():
    block = render_plan_block(_plan(captured=False))
    assert "not captured" in block
    assert "no_test_command" in block


def test_applying_interactions_are_listed():
    ledger = build_requirement_ledger(PROMPT)
    cells = (
        InteractionCell(ledger.rows[0].row_id, "DebugMode", "ALL", True, "differs"),
        InteractionCell(ledger.rows[0].row_id, "DebugMode", "OFF", False, "same"),
    )
    block = render_plan_block(_plan(interactions=cells))
    assert "CONFIGURATION INTERACTIONS that apply" in block
    assert "DebugMode.ALL" in block
    assert "DebugMode.OFF" not in block


def test_gaps_are_rendered_rather_than_dropped():
    block = render_plan_block(_plan(abstentions=(("req-x", "no_anchor"),)))
    assert "OPEN ITEMS" in block
    assert "no_anchor" in block


def test_an_abstained_plan_renders_nothing():
    plan = _plan()
    plan.status = "ABSTAINED"
    assert render_plan_block(plan) == ""


def test_the_block_is_capped_and_says_what_it_dropped():
    ledger = build_requirement_ledger(
        "".join(f"Requirement number {index} must hold.\n" for index in range(400))
    )
    inputs = PlanInputs(
        ledger=ledger, anchors=AnchorResult(), baseline=BaselineResult(status="none")
    )
    plan = PersistentPlan(
        status="READY",
        inputs=inputs,
        rows=tuple(
            PlanRow(row_id=row.row_id, text=row.text) for row in ledger.rows
        ),
    )
    block = render_plan_block(plan)
    assert len(block) <= MAX_BLOCK_CHARS + 200
    assert "more plan lines omitted" in block


def test_progress_rides_the_existing_obligation_delta(tmp_path):
    """No second progress channel: plan rows move through the contract delta.

    The plan's rows are merged into the contract, so ``next_contract_delta``
    already reports them. A parallel renderer would be a second way to say the
    same thing and a second thing to keep correct.
    """
    from gt_engine.miniswe_controller import Predicate
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.persistent_plan import merged_plan_contract
    from gt_engine.persistent_plan.ledger import build_requirement_ledger
    from gt_engine.task_contract import extract_task_contract
    from gt_engine.verification_contract import compile_obligation_predicates

    contract = extract_task_contract(PROMPT)
    ledger = build_requirement_ledger(PROMPT, contract)
    merged = merged_plan_contract(contract, ledger, PROMPT)
    compiled = compile_obligation_predicates(merged)
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = MiniSweAdapter(
        task_id="delta", state_dir=tmp_path / "state",
        predicates=[
            Predicate(compiled[item.obligation_id].predicate_id, item.text)
            for item in merged.obligations
            if item.obligation_id in compiled
        ],
        contract=merged, repo_root=str(repo),
    )
    text = adapter.next_contract_delta(max_chars=4000)
    assert text, "the contract delta must carry the merged plan rows"
    assert "retry" in text or "report" in text


def test_the_render_module_exposes_no_progress_renderer():
    import gt_engine.persistent_plan.render as render

    assert not hasattr(render, "render_progress_lines")


def test_a_lexical_anchor_is_marked_as_a_guess():
    """A text match against prose must not read like a resolved symbol."""
    from dataclasses import replace as _replace

    plan = _plan()
    weak = _replace(_anchor(node_id=1, name="load"), basis="lexical")
    plan.inputs.anchors.anchors[plan.rows[0].row_id] = (weak,)
    block = render_plan_block(plan)
    assert "name guess, unconfirmed" in block


def test_an_exact_anchor_carries_no_caveat():
    block = render_plan_block(_plan())
    assert "load @ src/loader.py:12" in block
    assert "name guess" not in block


def test_the_plan_summary_prints_rows_gaps_and_status(capsys):
    """The job log must answer "what plan was built" without an artifact download."""
    from gt_engine.miniswe_runtime import _print_plan_summary

    plan = _plan(
        interactions=(
            InteractionCell(
                _plan().rows[0].row_id, "DebugMode", "ALL", True, "differs"
            ),
        ),
        abstentions=(("req-x", "no_anchor"),),
    )
    _print_plan_summary(plan, "stop")
    out = capsys.readouterr().out
    assert "[GT_PLAN_SUMMARY]" in out
    assert "status=READY" in out
    assert "finish_reason=stop" in out
    assert "[GT_PLAN_ROW]" in out
    assert "pytest tests/test_loader.py" in out
    assert "[GT_PLAN_GAP] req-x: no_anchor" in out


def test_the_plan_summary_never_raises_on_a_broken_plan(capsys):
    from gt_engine.miniswe_runtime import _print_plan_summary

    class Broken:
        def counts(self):
            raise RuntimeError("boom")

    _print_plan_summary(Broken(), "stop")
    assert capsys.readouterr().out == ""


def test_the_block_carries_the_understanding_and_the_per_row_change():
    """A plan says what is meant and what changes, not only where to look."""
    from dataclasses import replace as _replace

    plan = _plan()
    plan.understanding = (
        "The container already resolves singletons eagerly; what is missing is "
        "an async initialisation pass that runs before resolution."
    )
    plan.rows = (
        _replace(plan.rows[0], approach="Add an initializer hook to the resolver."),
        plan.rows[1],
    )
    block = render_plan_block(plan)
    assert "DESIGN INTENT:" in block
    assert "async initialisation pass" in block
    assert "design: Add an initializer hook to the resolver." in block


def test_a_plan_without_understanding_omits_the_section():
    block = render_plan_block(_plan())
    assert "DESIGN INTENT:" not in block
    assert "design:" not in block
