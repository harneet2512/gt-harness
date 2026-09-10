"""A task-level plan built once, before the first edit.

The graph is strongest at step zero: complete, current, certified, and not yet
invalidated by any edit. Measured on the live run, caller coverage was answered
on only 18 of 33 post-edit rebuilds and 31 of 33 were refused outright, so
evidence harvested later is evidence harvested from a degraded graph. This
package harvests it once at full strength and holds the result as an immutable
artifact for the rest of the task.

Derived from exactly three things: the task prompt, the repository at its base
commit, and the graph built from it. Never the verifier's tests, never the
fail-to-pass list, never anything under the benchmark harness.

Layout:
  ledger.py     verbatim, line-level requirement rows
  anchors.py    graph anchors, callers, mode candidates, edit order
  baseline.py   the pre-edit green test set
  bootstrap.py  the single planning provider call
  render.py     the immutable prompt block and the mutable progress lines
  gate.py       the budget-aware completion gate
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .anchors import AnchorResult, build_anchor_result
from .baseline import BaselineResult, baseline_budget_seconds, run_baseline
from .ledger import (
    Ledger,
    build_requirement_ledger,
    ledger_only_contract,
    merged_plan_contract,
)

PLAN_FLAG = "GT_PERSISTENT_PLAN"
PLAN_SCHEMA = "gt.persistent_plan.v1"

STATUS_READY = "READY"
STATUS_PARTIAL = "PARTIAL"
STATUS_ABSTAINED = "ABSTAINED"


def plan_enabled() -> bool:
    """Off unless explicitly on, and an explicit ``0`` always wins."""
    return os.environ.get(PLAN_FLAG, "").strip() == "1"


@dataclass
class PlanInputs:
    """Everything Phase 0 established without spending a provider call."""

    ledger: Ledger
    anchors: AnchorResult
    baseline: BaselineResult
    source_revision: str = ""
    graph_revision: str = ""
    language: str = ""
    # Test files the graph ties to each row's own definitions. This is the
    # check that comes from context rather than from a model's suggestion.
    covering: dict[str, tuple[str, ...]] = field(default_factory=dict)
    abstentions: tuple[tuple[str, str], ...] = ()

    def counts(self) -> dict[str, Any]:
        ledger_counts = self.ledger.counts()
        return {
            "ledger_rows": ledger_counts["rows"],
            "linked_rows": ledger_counts["linked_rows"],
            "ledger_only_rows": ledger_counts["ledger_only_rows"],
            "skipped_lines": ledger_counts["skipped"],
            "anchored_rows": self.anchors.anchored_rows(),
            "no_anchor_rows": sum(
                1 for _row, reason in self.anchors.abstentions if reason == "no_anchor"
            ),
            "mode_candidates": len(self.anchors.modes),
            "rows_with_covering_tests": sum(
                1 for value in self.covering.values() if value
            ),
            "baseline_status": self.baseline.status,
            "baseline_seconds": round(self.baseline.duration_seconds, 3),
            "baseline_passing": self.baseline.passed,
            "baseline_failing": self.baseline.failed,
            "abstentions": len(self.abstentions),
        }

    def as_dict(self) -> dict:
        return {
            "schema": "gt.persistent_plan_inputs.v1",
            "source_revision": self.source_revision,
            "graph_revision": self.graph_revision,
            "language": self.language,
            "rows": [row.as_dict() for row in self.ledger.rows],
            "unclassified_spans": [list(span) for span in self.ledger.unclassified_spans],
            "anchors": {
                row_id: [anchor.as_dict() for anchor in anchors]
                for row_id, anchors in self.anchors.anchors.items()
            },
            "callers": {
                str(node_id): [caller.as_dict() for caller in callers]
                for node_id, callers in self.anchors.callers.items()
            },
            "modes": [mode.as_dict() for mode in self.anchors.modes],
            "covering": {key: list(value) for key, value in self.covering.items()},
            "edit_order": list(self.anchors.edit_order),
            "baseline": self.baseline.as_dict(),
            "abstentions": [list(item) for item in self.abstentions],
            "counts": self.counts(),
        }


@dataclass(frozen=True)
class PlanRow:
    """One requirement, anchored, with the command that would prove it."""

    row_id: str
    text: str
    anchors: tuple[int, ...] = ()
    # What must actually change for this requirement to hold, in terms of the
    # code the anchors name. Without it a plan is an index of requirements, not
    # a plan: it says where to look and how to check, but never what is meant.
    approach: str = ""
    verification_kind: str = ""
    verification_command: str = ""
    derived_from: str = ""
    mode_symbol: str = ""
    mode_member: str = ""

    @property
    def is_derived(self) -> bool:
        return bool(self.derived_from)

    def as_dict(self) -> dict:
        return {
            "row_id": self.row_id,
            "text": self.text,
            "anchors": list(self.anchors),
            "approach": self.approach,
            "verification_kind": self.verification_kind,
            "verification_command": self.verification_command,
            "derived_from": self.derived_from,
            "mode_symbol": self.mode_symbol,
            "mode_member": self.mode_member,
        }


@dataclass(frozen=True)
class InteractionCell:
    """One requirement crossed with one existing mode, decided explicitly."""

    row_id: str
    mode_symbol: str
    member: str
    applies: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "row_id": self.row_id,
            "mode_symbol": self.mode_symbol,
            "member": self.member,
            "applies": self.applies,
            "reason": self.reason,
        }


@dataclass
class PersistentPlan:
    """The immutable artifact the rest of the task reads."""

    status: str
    inputs: PlanInputs
    rows: tuple[PlanRow, ...] = ()
    # The model's reading of what the task asks for, in terms of this
    # repository. The deterministic half cannot produce this and does not try.
    understanding: str = ""
    interactions: tuple[InteractionCell, ...] = ()
    edit_order: tuple[str, ...] = ()
    abstentions: tuple[tuple[str, str], ...] = ()
    process_id: str = ""
    planning_receipt: dict = field(default_factory=dict)
    # "deterministic" when built from context alone; "enriched" when a
    # planning call added interactions, derived rows or sharper checks on top.
    origin: str = "deterministic"

    @property
    def derived_rows(self) -> tuple[PlanRow, ...]:
        return tuple(row for row in self.rows if row.is_derived)

    @property
    def applicable_cells(self) -> tuple[InteractionCell, ...]:
        return tuple(cell for cell in self.interactions if cell.applies)

    def row(self, row_id: str) -> PlanRow | None:
        for item in self.rows:
            if item.row_id == row_id:
                return item
        return None

    def counts(self) -> dict[str, Any]:
        counts = dict(self.inputs.counts())
        counts.update(
            {
                "status": self.status,
                "plan_rows": len(self.rows),
                "derived_rows": len(self.derived_rows),
                "interaction_cells": len(self.interactions),
                "applies_true": len(self.applicable_cells),
                "rows_with_check_commands": sum(
                    1 for row in self.rows if row.verification_command
                ),
                # Historical readers consume this name. It never counted
                # executed checks; retain it as an explicitly versioned alias.
                "verified_methods": sum(1 for row in self.rows if row.verification_command),
                "counts_layout": "check_commands_not_verification.v2",
                "plan_abstentions": len(self.abstentions),
                "origin": self.origin,
                "has_understanding": bool(self.understanding),
                "rows_with_approach": sum(1 for row in self.rows if row.approach),
                "process_id": self.process_id,
            }
        )
        return counts

    def as_dict(self) -> dict:
        return {
            "schema": PLAN_SCHEMA,
            "status": self.status,
            "origin": self.origin,
            "understanding": self.understanding,
            "process_id": self.process_id,
            "source_revision": self.inputs.source_revision,
            "graph_revision": self.inputs.graph_revision,
            "rows": [row.as_dict() for row in self.rows],
            "interactions": [cell.as_dict() for cell in self.interactions],
            "edit_order": list(self.edit_order),
            "abstentions": [list(item) for item in self.abstentions],
            "baseline": self.inputs.baseline.as_dict(),
            "counts": self.counts(),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


def build_plan_inputs(
    issue_text: str,
    *,
    contract=None,
    graph_db: str | None = None,
    repo_root: str = "",
    source_revision: str = "",
    graph_revision: str = "",
    wall_time_limit_seconds: float | None = None,
    capture_baseline: bool = True,
    execution_env: dict[str, str] | None = None,
) -> PlanInputs:
    """Phase 0: everything derivable with no provider call.

    Correct-or-quiet as a whole: any stage that faults degrades to an
    abstention, because a plan that reports its gaps is usable and a plan that
    hides them is not.
    """
    abstentions: list[tuple[str, str]] = []
    try:
        ledger = build_requirement_ledger(issue_text, contract)
    except Exception as exc:  # noqa: BLE001 - the ledger must never break the run
        ledger = build_requirement_ledger("")
        abstentions.append(("*", f"ledger_failed:{type(exc).__name__}"))

    try:
        anchors = build_anchor_result(graph_db, ledger)
    except Exception as exc:  # noqa: BLE001
        anchors = AnchorResult(abstentions=(("*", f"anchors_failed:{type(exc).__name__}"),))
    abstentions.extend(anchors.abstentions)

    baseline = BaselineResult(status="not_attempted")
    if capture_baseline and repo_root:
        try:
            baseline = run_baseline(
                repo_root, budget_seconds=baseline_budget_seconds(wall_time_limit_seconds),
                execution_env=execution_env,
            )
        except Exception as exc:  # noqa: BLE001
            baseline = BaselineResult(
                status="probe_failed", detail=type(exc).__name__
            )
    if not baseline.captured:
        abstentions.append(("*", f"baseline_{baseline.status}"))

    # The checks. Derived from the graph's own covering edges plus the caller
    # closure, so each requirement carries the tests that actually exercise the
    # definitions it names -- not a command a model thought sounded right.
    covering: dict[str, tuple[str, ...]] = {}
    if graph_db and repo_root:
        from .deterministic import covering_tests_for

        for row in ledger.rows:
            names = tuple(
                dict.fromkeys(
                    anchor.name
                    for anchor in anchors.anchors.get(row.row_id, ())
                    if anchor.name
                )
            )
            found = covering_tests_for(graph_db, repo_root, names)
            if found:
                covering[row.row_id] = found

    languages = {
        anchor.language
        for anchor_list in anchors.anchors.values()
        for anchor in anchor_list
        if anchor.language
    }
    return PlanInputs(
        ledger=ledger,
        anchors=anchors,
        baseline=baseline,
        source_revision=source_revision,
        graph_revision=graph_revision,
        language=sorted(languages)[0] if len(languages) == 1 else "",
        covering=covering,
        abstentions=tuple(abstentions),
    )


__all__ = [
    "PLAN_FLAG",
    "PLAN_SCHEMA",
    "STATUS_ABSTAINED",
    "STATUS_PARTIAL",
    "STATUS_READY",
    "InteractionCell",
    "PersistentPlan",
    "PlanInputs",
    "PlanRow",
    "build_plan_inputs",
    "ledger_only_contract",
    "merged_plan_contract",
    "plan_enabled",
]
