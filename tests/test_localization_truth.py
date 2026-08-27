from __future__ import annotations

import pytest

from gt_harness.localization_truth import (
    LocalizationFact,
    LocalizationOracleTask,
    LocalizationRole,
    delivered_roles_from_packet,
    delivered_roles_from_provider_receipt,
    score_localization,
)


def test_typed_packet_extraction_preserves_role_boundaries() -> None:
    roles = delivered_roles_from_packet(
        {
            "primary_edit_targets": [{"path": "src/core.py"}],
            "inspection_implementation_owners": [{"path": "src/owner.py"}],
            "inspection_public_surface": [{"path": "src/__init__.py"}],
            "inspection_integration": [{"path": "src/registry.py"}],
            "inspection_candidates": [{"path": "src/possible.py"}],
            "ambiguous_identities": [
                {
                    "candidates": [
                        {"path": "src/one.py"},
                        {"path": "src/two.py"},
                    ]
                }
            ],
            "affected_tests": ["tests/test_core.py"],
            "proposed_new_files": ["src/new.py"],
        }
    )

    assert roles == {
        "EXACT_EDIT_TARGET": ("src/core.py",),
        "INSPECT_IMPLEMENTATION_OWNER_NOT_EDIT_AUTHORITY": ("src/owner.py",),
        "INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY": ("src/possible.py",),
        "INSPECT_PUBLIC_SURFACE": ("src/__init__.py",),
        "INSPECT_INTEGRATION": ("src/registry.py",),
        "AMBIGUOUS_IDENTITY": ("src/one.py", "src/two.py"),
        "AFFECTED_TEST": ("tests/test_core.py",),
        "PROPOSED_NEW_FILE": ("src/new.py",),
    }


def test_provider_receipt_extraction_scores_only_serialized_roles() -> None:
    roles = delivered_roles_from_provider_receipt(
        {
            "schema": "gt.provider_delivery.v2",
            "provider_visible_role_paths": {
                "EXACT_EDIT_TARGET": ["src/core.py"],
                "AMBIGUOUS_IDENTITY": ["src/one.py", "src/two.py"],
            },
        }
    )

    assert roles == {
        "EXACT_EDIT_TARGET": ("src/core.py",),
        "AMBIGUOUS_IDENTITY": ("src/one.py", "src/two.py"),
    }


def test_test_file_does_not_count_as_required_edit_authority() -> None:
    oracle = LocalizationOracleTask(
        task_id="task",
        base_sha="a" * 40,
        facts=(
            LocalizationFact(
                fact_id="implementation",
                role=LocalizationRole.IMPLEMENTATION_OWNER,
                acceptable_paths=("src/core.py",),
                required=True,
            ),
            LocalizationFact(
                fact_id="validation",
                role=LocalizationRole.VALIDATION_OR_TEST,
                acceptable_paths=("tests/test_core.py",),
                required=False,
            ),
        ),
    )

    score = score_localization(
        oracle,
        {
            "EXACT_EDIT_TARGET": ("tests/test_core.py",),
            "AFFECTED_TEST": ("tests/test_core.py",),
        },
    )

    assert score.false_edit_authority == ("tests/test_core.py",)
    assert score.exact_edit_precision == 0.0
    assert score.required_facts_covered == 0


def test_typed_ambiguity_covers_owner_without_granting_edit_authority() -> None:
    oracle = LocalizationOracleTask(
        task_id="task",
        base_sha="b" * 40,
        facts=(
            LocalizationFact(
                fact_id="owner",
                role=LocalizationRole.IMPLEMENTATION_OWNER,
                acceptable_paths=("src/one.py", "src/two.py"),
                required=True,
            ),
        ),
    )

    score = score_localization(
        oracle,
        {
            "AMBIGUOUS_IDENTITY": ("src/one.py", "src/other.py"),
        },
    )

    assert score.false_edit_authority == ()
    assert score.required_facts_covered == 1
    assert score.ambiguity_candidate_recall == 1.0


def test_inspection_candidate_covers_availability_without_edit_authority() -> None:
    oracle = LocalizationOracleTask(
        task_id="task",
        base_sha="c" * 40,
        facts=(
            LocalizationFact(
                fact_id="owner",
                role=LocalizationRole.IMPLEMENTATION_OWNER,
                acceptable_paths=("src/owner.py",),
                required=True,
            ),
        ),
    )

    score = score_localization(
        oracle,
        {
            "INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY": (
                "src/owner.py",
                "src/noise.py",
            ),
        },
    )

    assert score.required_facts_covered == 1
    assert score.false_edit_authority == ()
    assert score.role_metrics["IMPLEMENTATION_OWNER"]["precision"] == 0.5
    assert score.role_metrics["IMPLEMENTATION_OWNER"]["fact_recall"] == 1.0
    assert score.role_metrics["IMPLEMENTATION_OWNER"]["recall"] == 1.0
    assert score.ambiguity_candidate_recall is None


def test_role_fact_recall_treats_acceptable_paths_as_alternatives() -> None:
    oracle = LocalizationOracleTask(
        task_id="task",
        base_sha="d" * 40,
        facts=(
            LocalizationFact(
                fact_id="owner",
                role=LocalizationRole.IMPLEMENTATION_OWNER,
                acceptable_paths=("src/one.py", "src/two.py", "src/three.py"),
                required=True,
            ),
        ),
    )

    score = score_localization(
        oracle,
        {"INSPECT_IMPLEMENTATION_OWNER_NOT_EDIT_AUTHORITY": ("src/two.py",)},
    )

    metrics = score.role_metrics["IMPLEMENTATION_OWNER"]
    assert metrics["fact_recall"] == 1.0
    assert metrics["recall"] == pytest.approx(1 / 3)


def test_oracle_rejects_abbreviated_revision() -> None:
    with pytest.raises(ValueError, match="40-character"):
        LocalizationOracleTask(
            task_id="task",
            base_sha="68dafce",
            facts=(),
        )
