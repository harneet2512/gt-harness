from __future__ import annotations

from gt_engine.batch_continuation import assess_batch_continuation
from gt_engine.preflight import adapt_proposed_action


def _action(command: str, index: int, size: int = 2):
    return adapt_proposed_action(
        {"command": command},
        source_revision="source-1",
        workspace_revision="workspace-1",
        model_call=1,
        batch_index=index,
        batch_size=size,
    )


def test_ordered_mutation_batch_is_not_cancelled_for_expected_workspace_change() -> None:
    decision = assess_batch_continuation(
        executed=_action("touch app.py", 0),
        next_action=_action("rm app.py", 1),
        action_returncode=0,
        material_workspace_change=True,
        source_revision_changed=True,
        changed_paths=("app.py",),
    )

    assert decision.interrupt is False
    assert decision.reason == "ordered_batch_continuation"


def test_failed_validation_cancels_immediate_predecided_submit() -> None:
    decision = assess_batch_continuation(
        executed=_action("pytest -q", 0),
        next_action=_action("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", 1),
        action_returncode=1,
        material_workspace_change=False,
        source_revision_changed=False,
        changed_paths=(),
    )

    assert decision.interrupt is True
    assert decision.reason == "failed_validation_before_submit"


def test_unexplained_revision_change_fails_closed() -> None:
    decision = assess_batch_continuation(
        executed=_action("cat app.py", 0),
        next_action=_action("python app.py", 1),
        action_returncode=0,
        material_workspace_change=False,
        source_revision_changed=True,
        changed_paths=(),
    )

    assert decision.interrupt is True
    assert decision.reason == "unexplained_repository_revision_change"
