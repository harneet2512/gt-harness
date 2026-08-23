"""Mechanical continuation policy for model-proposed action batches.

A batch is an ordered program chosen by the agent.  A successful mutation is
therefore not, by itself, evidence that the remaining program is stale.  This
module interrupts only when an observed result mechanically invalidates the
immediately following action.
"""

from __future__ import annotations

from dataclasses import dataclass

from gt_engine.preflight import ActionOperation, ProposedAction


@dataclass(frozen=True, slots=True)
class BatchContinuationDecision:
    interrupt: bool
    reason: str


def assess_batch_continuation(
    *,
    executed: ProposedAction,
    next_action: ProposedAction,
    action_returncode: int,
    material_workspace_change: bool,
    source_revision_changed: bool,
    changed_paths: tuple[str, ...],
) -> BatchContinuationDecision:
    """Decide whether the next predecided action is mechanically stale.

    Reads, writes, and validations in a batch deliberately observe effects of
    earlier commands.  Cancelling them merely because the checkout changed
    destroys the model's chosen control flow and consumes another provider
    turn.  We fail closed only for a failed check immediately followed by
    submission, an unexplained revision transition, or an explicit
    must-be-absent precondition invalidated by the preceding action.
    """

    if (
        executed.operation is ActionOperation.VALIDATE
        and int(action_returncode) != 0
        and next_action.operation is ActionOperation.SUBMIT
    ):
        return BatchContinuationDecision(True, "failed_validation_before_submit")

    if source_revision_changed and not material_workspace_change:
        return BatchContinuationDecision(True, "unexplained_repository_revision_change")

    if next_action.target_must_be_absent and material_workspace_change:
        changed = {str(path or "").replace("\\", "/") for path in changed_paths}
        targets = {
            str(target.path or "").replace("\\", "/")
            for target in next_action.targets
            if target.path
        }
        if changed & targets:
            return BatchContinuationDecision(True, "next_action_precondition_invalidated")

    return BatchContinuationDecision(False, "ordered_batch_continuation")


__all__ = ["BatchContinuationDecision", "assess_batch_continuation"]

