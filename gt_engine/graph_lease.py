"""Revision-scoped graph freshness and refresh coalescing.

The lease is the sole authority for graph-backed claims.  Edits invalidate it
immediately; a refresh is attempted lazily at a graph-dependent decision
boundary and at most once for a workspace revision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum

from gt_engine.decision_value import DecisionBoundary


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
        return self.result.success

    def as_dict(self) -> dict:
        return {"request": asdict(self.request), "result": asdict(self.result)}


_GRAPH_BOUNDARIES = frozenset(
    {
        DecisionBoundary.REPOSITORY_START,
        DecisionBoundary.IDENTITY_AMBIGUITY,
        DecisionBoundary.PRE_EDIT,
        DecisionBoundary.POST_EDIT_GRAPH_DELTA,
        DecisionBoundary.VERIFICATION_SELECTION,
        DecisionBoundary.PRE_SUBMIT,
    }
)


class GraphLease:
    def __init__(
        self,
        *,
        graph_repository_revision: str,
        workspace_revision: str,
        graph_revision: str,
        graph_path: str,
        freshness: GraphFreshness,
    ) -> None:
        self.graph_repository_revision = graph_repository_revision
        self.workspace_revision = workspace_revision
        self.graph_revision = graph_revision
        self.graph_path = graph_path
        self.freshness = freshness
        self.active_build_identity = ""
        self.last_successful_graph_receipt: GraphRefreshReceipt | None = None
        self.receipts: list[GraphRefreshReceipt] = []
        self._dirty_paths: set[str] = set()
        self._operations: set[str] = set()
        self._refresh_attempted_revisions: set[str] = set()
        self._supported_file_count = 0
        self._dependency_closure_size = 0
        self._adapter_can_incremental = True

    @classmethod
    def current(
        cls,
        *,
        graph_repository_revision: str,
        workspace_revision: str,
        graph_revision: str,
        graph_path: str,
    ) -> GraphLease:
        if not all((graph_repository_revision, workspace_revision, graph_revision, graph_path)):
            raise ValueError("a current graph lease requires complete identity")
        return cls(
            graph_repository_revision=graph_repository_revision,
            workspace_revision=workspace_revision,
            graph_revision=graph_revision,
            graph_path=graph_path,
            freshness=GraphFreshness.CURRENT,
        )

    @classmethod
    def absent(cls, *, workspace_revision: str = "") -> GraphLease:
        return cls(
            graph_repository_revision="",
            workspace_revision=workspace_revision,
            graph_revision="",
            graph_path="",
            freshness=GraphFreshness.ABSENT,
        )

    @property
    def dirty_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._dirty_paths))

    def mark_edit(
        self,
        *,
        workspace_revision: str,
        dirty_paths: tuple[str, ...],
        operations: tuple[str, ...],
        supported_file_count: int,
        dependency_closure_size: int = 0,
        adapter_can_incremental: bool = True,
    ) -> None:
        if not workspace_revision or not dirty_paths:
            return
        if self.workspace_revision != workspace_revision:
            self._dirty_paths.clear()
            self._operations.clear()
            self.workspace_revision = workspace_revision
        self._dirty_paths.update(path.replace("\\", "/") for path in dirty_paths if path)
        self._operations.update(operation.lower() for operation in operations if operation)
        self._supported_file_count = max(0, supported_file_count)
        self._dependency_closure_size = max(
            self._dependency_closure_size, dependency_closure_size
        )
        self._adapter_can_incremental = (
            self._adapter_can_incremental and adapter_can_incremental
        )
        self.freshness = GraphFreshness.STALE
        self.active_build_identity = ""

    def claims_current(self, repository_revision: str, graph_revision: str) -> bool:
        return bool(
            self.freshness is GraphFreshness.CURRENT
            and repository_revision == self.graph_repository_revision
            and graph_revision == self.graph_revision
        )

    def _refresh_mode(self) -> tuple[GraphRefreshMode, str]:
        ratio = (
            self._dependency_closure_size / self._supported_file_count
            if self._supported_file_count
            else 1.0
        )
        unsafe_operation = bool(self._operations & {"create", "delete", "rename"})
        if not self._adapter_can_incremental:
            return GraphRefreshMode.FULL, "language_adapter_not_incrementally_safe"
        if unsafe_operation:
            return GraphRefreshMode.FULL, "file_identity_change_requires_full_rebuild"
        if ratio > 0.20:
            return GraphRefreshMode.FULL, "dependency_closure_exceeds_20_percent"
        return GraphRefreshMode.INCREMENTAL, "bounded_modified_file_closure"

    def refresh_for_boundary(
        self,
        boundary: DecisionBoundary,
        *,
        repository_revision: str,
        refresh: Callable[[GraphRefreshRequest], GraphBuildResult],
    ) -> GraphRefreshReceipt | None:
        if (
            self.freshness is GraphFreshness.CURRENT
            and repository_revision != self.graph_repository_revision
        ):
            self.freshness = GraphFreshness.STALE
        if boundary not in _GRAPH_BOUNDARIES or self.freshness is GraphFreshness.CURRENT:
            return None
        if self.workspace_revision in self._refresh_attempted_revisions:
            return None
        self._refresh_attempted_revisions.add(self.workspace_revision)
        mode, reason = self._refresh_mode()
        identity_payload = "\0".join(
            (
                self.workspace_revision,
                repository_revision,
                mode.value,
                *self.dirty_paths,
            )
        ).encode("utf-8")
        build_identity = hashlib.sha256(identity_payload).hexdigest()
        request = GraphRefreshRequest(
            build_identity=build_identity,
            workspace_revision=self.workspace_revision,
            repository_revision=repository_revision,
            prior_graph_revision=self.graph_revision,
            dirty_paths=self.dirty_paths,
            operations=tuple(sorted(self._operations)),
            mode=mode,
            reason=reason,
        )
        self.active_build_identity = build_identity
        self.freshness = GraphFreshness.BUILDING
        try:
            result = refresh(request)
        except Exception as exc:  # correct-or-quiet; prior graph stays non-authoritative
            result = GraphBuildResult(
                success=False,
                graph_repository_revision=self.graph_repository_revision,
                graph_revision=self.graph_revision,
                graph_path=self.graph_path,
                duration_ms=0.0,
                health_valid=False,
                mode=mode,
                error=f"{type(exc).__name__}: {exc}",
            )
        receipt = GraphRefreshReceipt(request=request, result=result)
        self.receipts.append(receipt)
        self.active_build_identity = ""
        if (
            result.success
            and result.health_valid
            and result.graph_repository_revision == repository_revision
            and result.graph_revision
            and result.graph_path
        ):
            self.graph_repository_revision = result.graph_repository_revision
            self.graph_revision = result.graph_revision
            self.graph_path = result.graph_path
            self.freshness = GraphFreshness.CURRENT
            self.last_successful_graph_receipt = receipt
            self._dirty_paths.clear()
            self._operations.clear()
        else:
            self.freshness = GraphFreshness.FAILED
        return receipt


__all__ = [
    "GraphBuildResult",
    "GraphFreshness",
    "GraphLease",
    "GraphRefreshMode",
    "GraphRefreshReceipt",
    "GraphRefreshRequest",
]
