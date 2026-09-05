from __future__ import annotations

from scripts import gt_engine_acceptance as acceptance


def test_acceptance_families_reference_existing_tests():
    assert tuple(acceptance.SUITES) == (
        "baseline",
        "context",
        "state",
        "retrieval",
        "features",
        "performance",
    )
    assert all(
        (acceptance.ROOT / path).is_file()
        for paths in acceptance.SUITES.values()
        for path in paths
    )


def test_source_identity_carries_head_and_worktree_components():
    identity = acceptance._source_identity()
    assert len(identity["head"]) == 40
    assert len(identity["status_sha256"]) == 64
    assert len(identity["tracked_diff_sha256"]) == 64
    assert len(identity["untracked_source_sha256"]) == 64
    assert isinstance(identity["dirty"], bool)
