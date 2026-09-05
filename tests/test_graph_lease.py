from __future__ import annotations

from gt_engine.graph_lease import (
    DecisionBoundary,
    GraphBuildResult,
    GraphFreshness,
    GraphLease,
    GraphRefreshMode,
)


def lease():
    return GraphLease.current(graph_repository_revision="r0", workspace_revision="w0",
                              graph_revision="g0", graph_path="base.sqlite")


def edit(item, revision, path, operation="modify", **kwargs):
    item.mark_edit(workspace_revision=revision, dirty_paths=(path,),
                   operations=(operation,), supported_file_count=100, **kwargs)


def result(request, *, success=True):
    return GraphBuildResult(success, request.repository_revision, "g1", "new.sqlite",
                            1.0, success, request.mode)


def test_dirty_changes_accumulate_until_accepted_publication():
    item = lease()
    edit(item, "w1", "a.py")
    edit(item, "w2", "b.py", "delete")
    assert item.dirty_paths == ("a.py", "b.py")
    assert item.operations == ("modify", "delete")


def test_newer_edit_cannot_be_cleared_by_old_build():
    item = lease()
    edit(item, "w1", "a.py")

    def build(request):
        edit(item, "w2", "b.py", "delete")
        return result(request)

    receipt = item.refresh_for_boundary(DecisionBoundary.PRE_SUBMIT,
                                       repository_revision="r1", refresh=build)
    assert receipt.result.success
    assert item.freshness is GraphFreshness.STALE
    assert item.graph_revision == "g0"
    assert set(item.dirty_paths) == {"a.py", "b.py"}
    item.refresh_for_boundary(DecisionBoundary.PRE_SUBMIT,
                              repository_revision="r2", refresh=result)
    assert item.claims_current("r2")
    assert not item.dirty_paths


def test_incremental_requires_explicit_certified_capability():
    item = lease()
    edit(item, "w1", "a.py")
    receipt = item.refresh_for_boundary(DecisionBoundary.PRE_SUBMIT,
                                       repository_revision="r1", refresh=result)
    assert receipt.request.mode is GraphRefreshMode.FULL


def test_superseded_failed_incremental_does_not_retry_new_source_with_old_identity():
    item = lease()
    edit(item, "w1", "a.py", adapter_can_incremental=True)
    calls = []

    def build(request):
        calls.append(request)
        edit(item, "w2", "b.py")
        return result(request, success=False)

    item.refresh_for_boundary(DecisionBoundary.PRE_SUBMIT,
                              repository_revision="r1", refresh=build)
    assert len(calls) == 1
    assert item.freshness is GraphFreshness.STALE
    assert "b.py" in item.dirty_paths


def test_build_exception_is_typed_and_keeps_dirty_state():
    item = lease()
    edit(item, "w1", "a.py")

    def build(request):
        raise OSError("private diagnostic must not escape")

    receipt = item.refresh_for_boundary(DecisionBoundary.PRE_SUBMIT,
                                       repository_revision="r1", refresh=build)
    assert not receipt.success
    assert item.freshness is GraphFreshness.FAILED
    assert item.dirty_paths == ("a.py",)
    assert receipt.result.error == "refresh_exception:OSError"


def test_reentrant_boundary_does_not_launch_second_build():
    item = lease()
    edit(item, "w1", "a.py")

    def build(request):
        edit(item, "w2", "b.py")
        nested = item.refresh_for_boundary(DecisionBoundary.PRE_SUBMIT,
                                           repository_revision="r2", refresh=result)
        assert nested is None
        return result(request)

    item.refresh_for_boundary(DecisionBoundary.PRE_SUBMIT,
                              repository_revision="r1", refresh=build)
    assert item.freshness is GraphFreshness.STALE
