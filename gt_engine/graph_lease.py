"""Revision-scoped graph freshness and refresh coalescing.

The lease is the single authority for graph-backed claims.  Edits invalidate
the current graph immediately; a refresh is attempted at most once per
workspace revision and never publishes an unhealthy result.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum

try:
    from gt_engine.decision_value import DecisionBoundary
except ImportError:  # the central substrate must remain importable in isolation
    class DecisionBoundary(StrEnum):
        REPOSITORY_START = "repository_start"
        IDENTITY_AMBIGUITY = "identity_ambiguity"
        PRE_EDIT = "pre_edit"
        POST_EDIT_GRAPH_DELTA = "post_edit_graph_delta"
        FAILURE_OBSERVATION = "failure_observation"
        VERIFICATION_SELECTION = "verification_selection"
        PRE_SUBMIT = "pre_submit"


class GraphFreshness(StrEnum):
    ABSENT = "ABSENT"
    CURRENT = "CURRENT"
    STALE = "STALE"
    BUILDING = "BUILDING"
    FAILED = "FAILED"


class GraphRefreshMode(StrEnum):
    INCREMENTAL = "incremental"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class GraphRefreshRequest:
    build_identity: str
    workspace_revision: str
    repository_revision: str
    prior_graph_revision: str
    dirty_paths: tuple[str, ...]
    operations: tuple[str, ...]
    mode: GraphRefreshMode
    reason: str


@dataclass(frozen=True, slots=True)
class GraphBuildResult:
    success: bool
    graph_repository_revision: str
    graph_revision: str
    graph_path: str
    duration_ms: float
    health_valid: bool
    mode: GraphRefreshMode
    error: str = ""


@dataclass(frozen=True, slots=True)
class GraphRefreshReceipt:
    request: GraphRefreshRequest
    result: GraphBuildResult

    @property
    def success(self) -> bool:
        return self.result.success and self.result.health_valid

    def as_dict(self) -> dict:
        return {"request": asdict(self.request), "result": asdict(self.result)}


_GRAPH_BOUNDARIES = frozenset({
    DecisionBoundary.REPOSITORY_START,
    DecisionBoundary.IDENTITY_AMBIGUITY,
    DecisionBoundary.PRE_EDIT,
    DecisionBoundary.POST_EDIT_GRAPH_DELTA,
    DecisionBoundary.FAILURE_OBSERVATION,
    DecisionBoundary.VERIFICATION_SELECTION,
    DecisionBoundary.PRE_SUBMIT,
})


class GraphLease:
    """Mutable lease carrying one authoritative graph identity."""

    def __init__(
        self,
        *,
        graph_repository_revision: str = "",
        workspace_revision: str = "",
        graph_revision: str = "",
        graph_path: str = "",
        freshness: GraphFreshness = GraphFreshness.ABSENT,
    ) -> None:
        self.graph_repository_revision = graph_repository_revision
        self.graph_input_revision = graph_repository_revision
        self.workspace_revision = workspace_revision
        self.graph_revision = graph_revision
        self.graph_path = graph_path
        self.freshness = freshness
        self.dirty_paths: tuple[str, ...] = ()
        self.operations: tuple[str, ...] = ()
        self._supported_file_count = 0
        self._dependency_closure_size = 0
        self._adapter_can_incremental = True
        self._refreshed_workspace_revision = ""
        self._attempts: dict[str, int] = {}
        self.last_error = ""

    @classmethod
    def current(cls, *, graph_repository_revision: str, workspace_revision: str,
                graph_revision: str, graph_path: str) -> GraphLease:
        if not graph_repository_revision or not graph_revision:
            raise ValueError("current graph requires repository and graph revisions")
        return cls(graph_repository_revision=graph_repository_revision,
                   workspace_revision=workspace_revision, graph_revision=graph_revision,
                   graph_path=graph_path, freshness=GraphFreshness.CURRENT)

    @classmethod
    def absent(cls, *, workspace_revision: str = "") -> GraphLease:
        return cls(workspace_revision=workspace_revision)

    def mark_edit(self, *, workspace_revision: str, dirty_paths: tuple[str, ...],
                  operations: tuple[str, ...], supported_file_count: int,
                  dependency_closure_size: int | None = None,
                  adapter_can_incremental: bool = True) -> None:
        if not workspace_revision:
            raise ValueError("workspace_revision is required")
        self.workspace_revision = workspace_revision
        self.dirty_paths = tuple(dict.fromkeys(str(p) for p in dirty_paths if p))
        self.operations = tuple(str(o) for o in operations)
        self._supported_file_count = max(0, int(supported_file_count))
        self._dependency_closure_size = max(0, int(dependency_closure_size or len(self.dirty_paths)))
        self._adapter_can_incremental = bool(adapter_can_incremental)
        self.freshness = GraphFreshness.STALE
        self.last_error = ""

    def claims_current(self, repository_revision: str, graph_revision: str = "") -> bool:
        return (self.freshness is GraphFreshness.CURRENT
                and self.graph_repository_revision == repository_revision
                and (not graph_revision or self.graph_revision == graph_revision))

    def _mode(self) -> GraphRefreshMode:
        too_large = self._supported_file_count > 0 and (
            self._dependency_closure_size / self._supported_file_count > 0.20
        )
        return (GraphRefreshMode.INCREMENTAL
                if self._adapter_can_incremental and not too_large
                else GraphRefreshMode.FULL)

    def _request(self, repository_revision: str, mode: GraphRefreshMode,
                 reason: str) -> GraphRefreshRequest:
        identity = hashlib.sha256("|".join((repository_revision, self.workspace_revision,
                                             ",".join(self.dirty_paths),
                                             ",".join(self.operations), mode.value)).encode()).hexdigest()
        return GraphRefreshRequest(identity, self.workspace_revision, repository_revision,
                                   self.graph_revision, self.dirty_paths, self.operations,
                                   mode, reason)

    def refresh_for_boundary(self, boundary: DecisionBoundary, *, repository_revision: str,
                             refresh: Callable[[GraphRefreshRequest], GraphBuildResult]
                             ) -> GraphRefreshReceipt | None:
        if boundary not in _GRAPH_BOUNDARIES or self.freshness is GraphFreshness.CURRENT:
            return None
        if not repository_revision:
            raise ValueError("repository_revision is required")
        if self._refreshed_workspace_revision == self.workspace_revision:
            return None
        self.freshness = GraphFreshness.BUILDING
        self._refreshed_workspace_revision = self.workspace_revision
        mode = self._mode()
        request = self._request(repository_revision, mode, f"{boundary.value}: refresh")
        result = refresh(request)
        receipt = GraphRefreshReceipt(request, result)
        if (not result.success or not result.health_valid
                or result.graph_repository_revision != repository_revision
                or not result.graph_revision):
            if mode is GraphRefreshMode.INCREMENTAL:
                full_request = self._request(repository_revision, GraphRefreshMode.FULL,
                                             f"{boundary.value}: incremental fallback")
                full_result = refresh(full_request)
                receipt = GraphRefreshReceipt(full_request, full_result)
                result = full_result
            if (not result.success or not result.health_valid
                    or result.graph_repository_revision != repository_revision
                    or not result.graph_revision):
                self.freshness = GraphFreshness.FAILED
                self.last_error = result.error or "graph refresh failed validation"
                return receipt
        self.graph_repository_revision = result.graph_repository_revision
        self.graph_input_revision = result.graph_repository_revision
        self.graph_revision = result.graph_revision
        self.graph_path = result.graph_path
        self.freshness = GraphFreshness.CURRENT
        self.dirty_paths = ()
        self.operations = ()
        self.last_error = ""
        return receipt

    def as_dict(self) -> dict:
        return {
            "graph_repository_revision": self.graph_repository_revision,
            "workspace_revision": self.workspace_revision,
            "graph_revision": self.graph_revision,
            "graph_path": self.graph_path,
            "freshness": self.freshness.value,
            "dirty_paths": self.dirty_paths,
            "operations": self.operations,
            "last_error": self.last_error,
        }
