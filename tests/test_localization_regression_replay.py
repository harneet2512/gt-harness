from __future__ import annotations

from scripts.replay_localization_regressions import CASES, _path_lines


def test_replay_oracle_is_separate_from_task_input() -> None:
    for case in CASES.values():
        for path in (*case.existing_oracle_paths, *case.new_oracle_paths):
            assert path not in case.task


def test_replay_extracts_only_role_bearing_delivery_paths() -> None:
    paths = _path_lines(
        "\n".join(
            (
                'GROUNDTRUTH_CONTEXT schema="gt.agent_context.v6"',
                "EXACT_EDIT_TARGET src/container.ts:1#symbol",
                "INSPECT_PUBLIC_SURFACE src/awilix.ts reason=manifest",
                "PROPOSED_NEW_FILE core/engine/src/evaluation.rs fact=false",
                "UNCOVERED_FACET facet-1",
            )
        )
    )

    assert paths == {
        "src/container.ts": ["EXACT_EDIT_TARGET"],
        "src/awilix.ts": ["INSPECT_PUBLIC_SURFACE"],
        "core/engine/src/evaluation.rs": ["PROPOSED_NEW_FILE"],
    }
