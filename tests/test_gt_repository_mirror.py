from __future__ import annotations

from gt_engine.central_runtime import FileState, WorkspaceSnapshot, graph_revision_receipt
from gt_engine.repository_mirror import plan_source_mirror


def _file(size: int) -> FileState:
    return FileState("f", size, "1", "1", "")


def test_source_mirror_excludes_task_data_binaries_and_caches():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "gpt2.c": _file(20_000),
            "include/gpt2.h": _file(2_000),
            "pyproject.toml": _file(800),
            "gpt2-124M.ckpt": _file(498_000_000),
            "vocab.bpe": _file(1_000_000),
            "__pycache__/helper.pyc": _file(4_000),
            "a.out": _file(100_000),
            "report.jsonl": _file(2_000),
        },
    )

    plan = plan_source_mirror(snapshot)

    assert plan.paths == ("gpt2.c", "include/gpt2.h", "pyproject.toml")
    assert plan.source_files == 2
    assert plan.metadata_files == 1
    assert plan.total_bytes == 22_800
    assert plan.complete is True
    assert "gpt2-124M.ckpt" not in plan.paths
    assert "vocab.bpe" not in plan.paths
    assert "a.out" not in plan.paths


def test_source_mirror_budget_failure_is_explicit_not_partial_success():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "a.py": _file(700),
            "b.py": _file(700),
        },
    )

    plan = plan_source_mirror(snapshot, max_total_bytes=1_000)

    assert plan.paths == ("a.py",)
    assert plan.complete is False
    assert plan.excluded_budget == 1
    assert plan.excluded_source_budget == 1
    assert plan.reason_codes == ("source_mirror_source_budget_exceeded",)


def test_unhealthy_workspace_cannot_claim_a_complete_source_mirror():
    plan = plan_source_mirror(
        WorkspaceSnapshot("", {}, False, "manifest failed")
    )

    assert plan.paths == ()
    assert plan.complete is False
    assert plan.reason_codes == ("workspace_snapshot_unhealthy",)


def test_multi_megabyte_authored_source_is_selected_for_archive_transfer():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "large.py": _file(2_000_001),
            "package-lock.json": _file(300_000),
        },
    )

    plan = plan_source_mirror(snapshot)

    assert plan.paths == ("large.py",)
    assert plan.excluded_oversize == 1
    assert plan.excluded_source_oversize == 0
    assert plan.complete is True
    assert plan.reason_codes == ()


def test_metadata_budget_pressure_does_not_claim_authored_source_is_incomplete():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "main.py": _file(700),
            "package.json": _file(700),
        },
    )

    plan = plan_source_mirror(snapshot, max_total_bytes=1_000)

    assert plan.paths == ("main.py",)
    assert plan.excluded_budget == 1
    assert plan.excluded_source_budget == 0
    assert plan.complete is True


def test_nonstructural_json_does_not_enter_graph_mirror():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "config.json": _file(500),
            "report.json": _file(700),
        },
    )

    plan = plan_source_mirror(snapshot, excluded_paths={"/app/report.json"})

    assert plan.paths == ()
    assert plan.excluded_deliverables == 1
    assert plan.complete is True


def test_authored_code_deliverable_is_not_excluded_from_graph_mirror():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "parallel_linear.py": FileState(
                "f", 100, "1", "1", "", content="def parallel_linear():\n    pass\n"
            ),
            "report.json": _file(700),
        },
    )

    plan = plan_source_mirror(
        snapshot,
        excluded_paths={"parallel_linear.py", "report.json"},
    )

    assert plan.paths == ("parallel_linear.py",)
    assert plan.source_files == 1
    assert plan.excluded_deliverables == 1


def test_allowlisted_external_nginx_source_is_mirrored_under_safe_prefix():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "/etc/nginx/nginx.conf": FileState(
                "f", 120, "1", "1", "", content="events {}\n"
            ),
            "/var/log/nginx/access.log": _file(40),
        },
    )

    plan = plan_source_mirror(snapshot)

    assert plan.paths == ("__external__/etc/nginx/nginx.conf",)
    assert plan.source_files == 1


def test_source_mirror_is_bound_to_exact_graph_receipt_entries():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "src/app.py": FileState("f", 8, "1", "1", "", digest="a" * 64),
            "requirements.txt": FileState("f", 12, "1", "1", "", digest="b" * 64),
        },
    )
    receipt = graph_revision_receipt(snapshot)

    plan = plan_source_mirror(snapshot, graph_revision=receipt)

    assert plan.complete is True
    assert plan.graph_source_revision == receipt.revision
    assert plan.selected_entries == receipt.entries
    assert plan.paths == tuple(entry.path for entry in receipt.entries)


def test_source_mirror_rejects_incomplete_graph_receipt():
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={"requirements.txt": FileState("f", 12, "1", "1", "")},
    )
    receipt = graph_revision_receipt(snapshot)

    plan = plan_source_mirror(snapshot, graph_revision=receipt)

    assert receipt.complete is False
    assert plan.complete is False
    assert "graph_revision_incomplete" in plan.reason_codes
