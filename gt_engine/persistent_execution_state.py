"""Persistent execution snapshots for the central runtime substrate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ExecutionStateSnapshot:
    repository_revision: str
    workspace_revision: str
    graph_revision: str
    graph_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "repository_revision": self.repository_revision,
            "workspace_revision": self.workspace_revision,
            "graph_revision": self.graph_revision,
            "graph_path": self.graph_path,
        }


SELECT_CATALOG_FEATURE_ID = "select_catalog"
SELECT_CATALOG_SCHEMA = "gt.select_catalog_lifecycle.v1"


class SelectCatalogStage(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CANDIDATE = "CANDIDATE"
    CERTIFIED = "CERTIFIED"
    DELIVERED = "DELIVERED"
    CONSUMED = "CONSUMED"
    VALIDATED = "VALIDATED"
    CONTRADICTED = "CONTRADICTED"
    ABSTAINED = "ABSTAINED"


class SelectCatalogAbstention(StrEnum):
    DUPLICATE_ID = "duplicate_catalog_id"
    OUT_OF_CATALOG = "out_of_catalog"
    NO_SELECTION = "no_selection"
    INCOMPLETE = "incomplete_catalog_evidence"
    STALE_REVISION = "stale_catalog_revision"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8", "surrogatepass")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """One immutable, content-addressed item exposed to select_catalog."""

    item_id: str
    kind: str
    label: str
    content_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "label": self.label,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True)
class Feature18Catalog:
    source_revision: str
    workspace_revision: str
    graph_revision: str
    items: tuple[CatalogItem, ...]
    schema: str = "gt.select_catalog.v1"

    @property
    def item_ids(self) -> frozenset[str]:
        return frozenset(item.item_id for item in self.items)

    @property
    def content_sha256(self) -> str:
        return _sha256(_canonical_bytes(self.as_dict()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_revision": self.source_revision,
            "workspace_revision": self.workspace_revision,
            "graph_revision": self.graph_revision,
            "items": [item.as_dict() for item in self.items],
        }


def build_feature18_catalog(
    *,
    source_revision: str,
    workspace_revision: str,
    graph_revision: str,
    items: Iterable[CatalogItem],
) -> Feature18Catalog:
    """Build the canonical, duplicate-free catalog used by select_catalog."""

    revisions = (source_revision, workspace_revision, graph_revision)
    if not all(str(value).strip() for value in revisions):
        raise ValueError("catalog revisions are required")
    materialized = tuple(items)
    if not materialized:
        raise ValueError("feature-18 catalog cannot be empty")
    seen: set[str] = set()
    for item in materialized:
        if not item.item_id or item.item_id in seen:
            raise ValueError("catalog item IDs must be unique and non-empty")
        if item.kind not in {"focus", "dependency", "validation", "deliverable"}:
            raise ValueError(f"unsupported catalog item kind: {item.kind}")
        if len(item.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in item.content_sha256
        ):
            raise ValueError(f"invalid content hash for catalog item {item.item_id}")
        seen.add(item.item_id)
    return Feature18Catalog(
        source_revision=str(source_revision),
        workspace_revision=str(workspace_revision),
        graph_revision=str(graph_revision),
        items=tuple(sorted(materialized, key=lambda item: item.item_id)),
    )


@dataclass(slots=True)
class Feature18Lifecycle:
    """Content-safe lifecycle for the one model-selected catalog capability."""

    catalog: Feature18Catalog
    event_id: str
    stage: SelectCatalogStage = SelectCatalogStage.CANDIDATE
    attempted_ids: tuple[str, ...] = ()
    selected_ids: tuple[str, ...] = ()
    request_sha256: str = ""
    tool_schema_sha256: str = ""
    argument_sha256: str = ""
    provider_request_id: str = ""
    delivery_id: str = ""
    resulting_agent_action: str = ""
    validation_result: str = ""
    abstention_reason: SelectCatalogAbstention | None = None
    transitions: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_catalog(cls, catalog: Feature18Catalog, *, event_id: str) -> "Feature18Lifecycle":
        if not event_id.strip():
            raise ValueError("feature-18 event identity is required")
        instance = cls(catalog=catalog, event_id=event_id)
        instance.transitions.append({"from": "", "to": SelectCatalogStage.CANDIDATE.value, "reason": "catalog_constructed"})
        return instance

    def _move(self, expected: SelectCatalogStage, target: SelectCatalogStage, reason: str) -> None:
        if self.stage is not expected:
            raise ValueError(f"{target.value} requires {expected.value}; found {self.stage.value}")
        self.transitions.append({"from": self.stage.value, "to": target.value, "reason": reason})
        self.stage = target

    def _abstain(self, reason: SelectCatalogAbstention) -> None:
        if self.stage not in {SelectCatalogStage.CANDIDATE, SelectCatalogStage.CERTIFIED}:
            raise ValueError(f"ABSTAINED requires CANDIDATE or CERTIFIED; found {self.stage.value}")
        self.transitions.append({"from": self.stage.value, "to": SelectCatalogStage.ABSTAINED.value, "reason": reason.value})
        self.stage = SelectCatalogStage.ABSTAINED
        self.abstention_reason = reason

    @staticmethod
    def _has_duplicates(values: Iterable[str]) -> bool:
        values = tuple(values)
        return len(values) != len(set(values))

    def certify(
        self,
        *,
        attempted_ids: tuple[str, ...],
        selected_ids: tuple[str, ...],
        request_bytes: bytes,
        tool_schema_bytes: bytes,
        argument_bytes: bytes,
        provider_request_id: str,
    ) -> None:
        if self.stage is not SelectCatalogStage.CANDIDATE:
            raise ValueError(f"CERTIFIED requires CANDIDATE; found {self.stage.value}")
        if self._has_duplicates(attempted_ids) or self._has_duplicates(selected_ids):
            self._abstain(SelectCatalogAbstention.DUPLICATE_ID)
            return
        catalog_ids = self.catalog.item_ids
        if any(item_id not in catalog_ids for item_id in (*attempted_ids, *selected_ids)):
            self._abstain(SelectCatalogAbstention.OUT_OF_CATALOG)
            return
        if not selected_ids:
            self._abstain(SelectCatalogAbstention.NO_SELECTION)
            return
        if any(item_id not in attempted_ids for item_id in selected_ids):
            self._abstain(SelectCatalogAbstention.OUT_OF_CATALOG)
            return
        if not request_bytes or not tool_schema_bytes or not argument_bytes or not provider_request_id.strip():
            self._abstain(SelectCatalogAbstention.INCOMPLETE)
            return
        self.attempted_ids = tuple(attempted_ids)
        self.selected_ids = tuple(selected_ids)
        self.request_sha256 = _sha256(request_bytes)
        self.tool_schema_sha256 = _sha256(tool_schema_bytes)
        self.argument_sha256 = _sha256(argument_bytes)
        self.provider_request_id = provider_request_id
        self._move(SelectCatalogStage.CANDIDATE, SelectCatalogStage.CERTIFIED, "selection_request_sealed")

    def deliver(self, *, delivery_id: str) -> None:
        if self.stage is not SelectCatalogStage.CERTIFIED:
            raise ValueError(f"DELIVERED requires CERTIFIED; found {self.stage.value}")
        if not delivery_id.strip():
            raise ValueError("DELIVERED requires a delivery identity")
        if not self.provider_request_id:
            raise ValueError("DELIVERED requires provider dispatch")
        self.delivery_id = delivery_id
        self._move(SelectCatalogStage.CERTIFIED, SelectCatalogStage.DELIVERED, "provider_dispatch_started")

    def consume(self, *, selected_ids: tuple[str, ...], resulting_action: str) -> None:
        if not resulting_action.strip():
            raise ValueError("CONSUMED requires a resulting agent action")
        if self._has_duplicates(selected_ids) or not selected_ids:
            raise ValueError("CONSUMED requires a non-empty duplicate-free selection")
        if any(item_id not in self.selected_ids for item_id in selected_ids):
            raise ValueError("CONSUMED selection is not a visible selected-ID subset")
        self._move(SelectCatalogStage.DELIVERED, SelectCatalogStage.CONSUMED, "versioned_bootstrap_transition_observed")
        self.resulting_agent_action = resulting_action

    def validate(self, *, validation: str, contradicted: bool) -> None:
        if not validation.strip():
            raise ValueError("validation or contradiction evidence is required")
        target = SelectCatalogStage.CONTRADICTED if contradicted else SelectCatalogStage.VALIDATED
        self._move(SelectCatalogStage.CONSUMED, target, validation)
        self.validation_result = validation

    def receipt(self) -> dict[str, Any]:
        return {
            "schema": SELECT_CATALOG_SCHEMA,
            "feature_id": SELECT_CATALOG_FEATURE_ID,
            "stage": self.stage.value,
            "event_id": self.event_id,
            "catalog_schema": self.catalog.schema,
            "catalog_sha256": self.catalog.content_sha256,
            "source_revision": self.catalog.source_revision,
            "workspace_revision": self.catalog.workspace_revision,
            "graph_revision": self.catalog.graph_revision,
            "attempted_ids": list(self.attempted_ids),
            "selected_ids": list(self.selected_ids),
            "request_sha256": self.request_sha256,
            "tool_schema_sha256": self.tool_schema_sha256,
            "argument_sha256": self.argument_sha256,
            "provider_request_id": self.provider_request_id,
            "delivery_id": self.delivery_id,
            "resulting_agent_action": self.resulting_agent_action,
            "validation_result": self.validation_result,
            "abstention_reason": self.abstention_reason.value if self.abstention_reason else "",
            "transitions": list(self.transitions),
        }


__all__ = [
    "CatalogItem",
    "ExecutionStateSnapshot",
    "Feature18Catalog",
    "Feature18Lifecycle",
    "SELECT_CATALOG_FEATURE_ID",
    "SELECT_CATALOG_SCHEMA",
    "SelectCatalogAbstention",
    "SelectCatalogStage",
    "build_feature18_catalog",
]
