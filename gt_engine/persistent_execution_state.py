"""Persistent execution snapshots for the central runtime substrate."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


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

    def persist(self, path: str | Path) -> Path:
        """Atomically persist a revision-bound snapshot for restart/reopen."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": "gt.execution_state.v1", **self.as_dict()}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        envelope = {**payload, "payload_sha256": hashlib.sha256(canonical).hexdigest()}
        encoded = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    @classmethod
    def load(cls, path: str | Path, *, expected_repository_revision: str = "",
             expected_workspace_revision: str = "", expected_graph_revision: str = "",
             expected_graph_path: str = "") -> ExecutionStateSnapshot:
        """Read a complete snapshot and reject corruption or stale identity."""
        target = Path(path)
        raw = json.loads(target.read_text(encoding="utf-8"))
        if raw.get("schema") != "gt.execution_state.v1":
            raise ValueError("execution state schema mismatch")
        fields = cls(
            repository_revision=str(raw.get("repository_revision", "")),
            workspace_revision=str(raw.get("workspace_revision", "")),
            graph_revision=str(raw.get("graph_revision", "")),
            graph_path=str(raw.get("graph_path", "")),
        )
        canonical = json.dumps({"schema": raw["schema"], **fields.as_dict()},
                               sort_keys=True, separators=(",", ":")).encode("utf-8")
        if raw.get("payload_sha256") != hashlib.sha256(canonical).hexdigest():
            raise ValueError("execution state digest mismatch")
        expected = {
            "repository_revision": expected_repository_revision,
            "workspace_revision": expected_workspace_revision,
            "graph_revision": expected_graph_revision,
            "graph_path": expected_graph_path,
        }
        for name, value in expected.items():
            if value and getattr(fields, name) != value:
                raise ValueError(f"execution state {name} mismatch")
        if not all(fields.as_dict().values()):
            raise ValueError("execution state is incomplete")
        return fields

    @classmethod
    def reopen(cls, path: str | Path, *, repository_revision: str,
               workspace_revision: str, graph_revision: str, graph_path: str) -> ExecutionStateSnapshot:
        return cls.load(path, expected_repository_revision=repository_revision,
                        expected_workspace_revision=workspace_revision,
                        expected_graph_revision=graph_revision, expected_graph_path=graph_path)

    def persist_witnessed_process(
        self, path: str | os.PathLike[str], process: Any
    ) -> dict[str, Any]:
        """Atomically persist a process bound to this execution snapshot."""
        if process.source_revision != self.repository_revision:
            raise ValueError("process source revision does not match execution state")
        if process.graph_revision != self.graph_revision:
            raise ValueError("process graph revision does not match execution state")
        receipt = process.receipt
        payload = {
            "schema": "gt.execution_state.witnessed_process.v1",
            "execution_state": self.as_dict(),
            "process": receipt,
            "process_receipt_sha256": _sha256(_canonical_bytes(receipt)),
        }
        target = os.fspath(path)
        parent = os.path.dirname(os.path.abspath(target)) or "."
        os.makedirs(parent, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=parent, prefix=".witnessed-process.", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(_canonical_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return payload

    @classmethod
    def reopen_witnessed_process(
        cls, path: str | os.PathLike[str]
    ) -> tuple[ExecutionStateSnapshot, WitnessedProcess]:
        """Reopen and verify a persisted process without inventing facts."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("persisted witnessed process is unreadable") from exc
        if payload.get("schema") != "gt.execution_state.witnessed_process.v1":
            raise ValueError("persisted witnessed process schema mismatch")
        receipt = payload.get("process")
        if not isinstance(receipt, dict) or payload.get(
            "process_receipt_sha256"
        ) != _sha256(_canonical_bytes(receipt)):
            raise ValueError("persisted witnessed process receipt mismatch")
        state_data = payload.get("execution_state")
        if not isinstance(state_data, dict):
            raise ValueError("persisted witnessed process state missing")
        try:
            state = cls(
                **{
                    key: state_data[key]
                    for key in (
                        "repository_revision",
                        "workspace_revision",
                        "graph_revision",
                        "graph_path",
                    )
                }
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("persisted witnessed process state malformed") from exc
        process = witnessed_process_from_receipt(receipt)
        if (
            process.source_revision != state.repository_revision
            or process.graph_revision != state.graph_revision
        ):
            raise ValueError("persisted witnessed process revision mismatch")
        return state, process

SELECT_CATALOG_FEATURE_ID = "select_catalog"
SELECT_CATALOG_SCHEMA = "gt.select_catalog_lifecycle.v1"
SELECT_CATALOG_TOOL_NAME = "select_catalog"


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
    target: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "label": self.label,
            "content_sha256": self.content_sha256,
            "target": self.target,
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


def build_select_catalog_tool(catalog: Feature18Catalog) -> dict[str, Any]:
    """Return a provider tool constrained to this exact catalog surface."""

    item_ids = sorted(catalog.item_ids)
    return {
        "type": "function",
        "function": {
            "name": SELECT_CATALOG_TOOL_NAME,
            "description": (
                "Select and order existing catalog IDs for the next execution "
                "focus. Do not invent IDs, paths, commands, or facts."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ids"],
                "properties": {
                    "ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": item_ids},
                    }
                },
            },
        },
    }


def build_select_catalog_messages(
    catalog: Feature18Catalog, *, task: str, max_chars: int = 8_000
) -> list[dict[str, str]]:
    """Render a bounded ID-only selection request without changing item facts."""

    items = [item.as_dict() for item in catalog.items]
    payload = json.dumps(
        {
            "schema": catalog.schema,
            "source_revision": catalog.source_revision,
            "workspace_revision": catalog.workspace_revision,
            "graph_revision": catalog.graph_revision,
            "task": str(task)[:2_000],
            "items": items,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload) > max_chars:
        raise ValueError("select_catalog request exceeds bounded input")
    return [
        {
            "role": "system",
            "content": (
                "Select existing catalog IDs with the select_catalog tool. "
                "Selection only orders deterministic context; Mini-SWE retains "
                "all reasoning and action authority."
            ),
        },
        {"role": "user", "content": payload},
    ]


def parse_select_catalog_arguments(
    raw: Any, catalog: Feature18Catalog
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return attempted and accepted IDs from one strict typed payload."""

    value = raw
    if isinstance(raw, (bytes, bytearray)):
        value = raw.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return (), ()
    if not isinstance(value, dict) or set(value) != {"ids"}:
        return (), ()
    ids = value.get("ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        return (), ()
    attempted = tuple(ids)
    if not attempted or len(attempted) != len(set(attempted)):
        return attempted, ()
    if any(item not in catalog.item_ids for item in attempted):
        return attempted, ()
    return attempted, attempted


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
    def from_catalog(
        cls, catalog: Feature18Catalog, *, event_id: str
    ) -> Feature18Lifecycle:
        if not event_id.strip():
            raise ValueError("feature-18 event identity is required")
        instance = cls(catalog=catalog, event_id=event_id)
        instance.transitions.append({
            "from": "", "to": SelectCatalogStage.CANDIDATE.value,
            "reason": "catalog_constructed",
        })
        return instance

    def _move(self, expected: SelectCatalogStage, target: SelectCatalogStage, reason: str) -> None:
        if self.stage is not expected:
            raise ValueError(f"{target.value} requires {expected.value}; found {self.stage.value}")
        self.transitions.append({"from": self.stage.value, "to": target.value, "reason": reason})
        self.stage = target

    def _abstain(self, reason: SelectCatalogAbstention) -> None:
        if self.stage not in {
            SelectCatalogStage.CANDIDATE,
            SelectCatalogStage.CERTIFIED,
            SelectCatalogStage.DELIVERED,
        }:
            raise ValueError(
                "ABSTAINED requires CANDIDATE, CERTIFIED, or DELIVERED; "
                f"found {self.stage.value}"
            )
        self.transitions.append({
            "from": self.stage.value,
            "to": SelectCatalogStage.ABSTAINED.value,
            "reason": reason.value,
        })
        self.stage = SelectCatalogStage.ABSTAINED
        self.abstention_reason = reason

    def abstain(self, reason: SelectCatalogAbstention) -> None:
        """Record a typed terminal abstention at the current legal stage."""

        self._abstain(reason)

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
        self.certify_offer(
            request_bytes=request_bytes,
            tool_schema_bytes=tool_schema_bytes,
            provider_request_id=provider_request_id,
        )
        self.record_selection(
            attempted_ids=attempted_ids,
            selected_ids=selected_ids,
            argument_bytes=argument_bytes,
        )

    def certify_offer(
        self,
        *,
        request_bytes: bytes,
        tool_schema_bytes: bytes,
        provider_request_id: str,
    ) -> None:
        """Certify the exact request before provider dispatch."""

        if self.stage is not SelectCatalogStage.CANDIDATE:
            raise ValueError(f"CERTIFIED requires CANDIDATE; found {self.stage.value}")
        if not request_bytes or not tool_schema_bytes or not provider_request_id.strip():
            self._abstain(SelectCatalogAbstention.INCOMPLETE)
            return
        self.request_sha256 = _sha256(request_bytes)
        self.tool_schema_sha256 = _sha256(tool_schema_bytes)
        self.provider_request_id = provider_request_id
        self._move(
            SelectCatalogStage.CANDIDATE,
            SelectCatalogStage.CERTIFIED,
            "selection_offer_certified",
        )

    def record_selection(
        self,
        *,
        attempted_ids: tuple[str, ...],
        selected_ids: tuple[str, ...],
        argument_bytes: bytes,
    ) -> None:
        """Bind a provider selection to IDs present in the certified offer."""

        if self.stage not in {SelectCatalogStage.CERTIFIED, SelectCatalogStage.DELIVERED}:
            raise ValueError(
                f"selection requires CERTIFIED or DELIVERED; found {self.stage.value}"
            )
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
        if not argument_bytes:
            self._abstain(SelectCatalogAbstention.INCOMPLETE)
            return
        self.attempted_ids = tuple(attempted_ids)
        self.selected_ids = tuple(selected_ids)
        self.argument_sha256 = _sha256(argument_bytes)

    def deliver(self, *, delivery_id: str) -> None:
        if self.stage is not SelectCatalogStage.CERTIFIED:
            raise ValueError(f"DELIVERED requires CERTIFIED; found {self.stage.value}")
        if not delivery_id.strip():
            raise ValueError("DELIVERED requires a delivery identity")
        if not self.provider_request_id:
            raise ValueError("DELIVERED requires provider dispatch")
        self.delivery_id = delivery_id
        self._move(
            SelectCatalogStage.CERTIFIED,
            SelectCatalogStage.DELIVERED,
            "provider_dispatch_started",
        )

    def consume(self, *, selected_ids: tuple[str, ...], resulting_action: str) -> None:
        if not resulting_action.strip():
            raise ValueError("CONSUMED requires a resulting agent action")
        if self._has_duplicates(selected_ids) or not selected_ids:
            raise ValueError("CONSUMED requires a non-empty duplicate-free selection")
        if any(item_id not in self.selected_ids for item_id in selected_ids):
            raise ValueError("CONSUMED selection is not a visible selected-ID subset")
        self._move(
            SelectCatalogStage.DELIVERED,
            SelectCatalogStage.CONSUMED,
            "versioned_bootstrap_transition_observed",
        )
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

@dataclass(frozen=True, slots=True)
class ProcessStep:
    node_id: str
    edge_id: str
    evidence_id: str
    relation: str
    verification_state: str


@dataclass(frozen=True, slots=True)
class WitnessedProcess:
    process_id: str
    source_revision: str
    graph_revision: str
    projection: str
    anchors: tuple[str, ...]
    steps: tuple[ProcessStep, ...]
    branches: tuple[tuple[str, ...], ...]
    gaps: tuple[str, ...]
    verification_state: str
    stale_reason: str

    @property
    def has_gaps(self) -> bool:
        return bool(self.gaps)

    @property
    def receipt(self) -> dict[str, Any]:
        return {
            "schema": "gt.witnessed_process.v1",
            "process_id": self.process_id,
            "source_revision": self.source_revision,
            "graph_revision": self.graph_revision,
            "projection": self.projection,
            "anchors": self.anchors,
            "steps": tuple(
                {
                    "node_id": step.node_id,
                    "edge_id": step.edge_id,
                    "evidence_id": step.evidence_id,
                    "relation": step.relation,
                    "verification_state": step.verification_state,
                }
                for step in self.steps
            ),
            "branches": self.branches,
            "gaps": self.gaps,
            "verification_state": self.verification_state,
            "stale_reason": self.stale_reason,
        }


def witnessed_process_from_receipt(receipt: dict[str, Any]) -> WitnessedProcess:
    if receipt.get("schema") != "gt.witnessed_process.v1":
        raise ValueError("witnessed process schema mismatch")
    try:
        steps = tuple(
            ProcessStep(
                str(step["node_id"]), str(step["edge_id"]),
                str(step["evidence_id"]), str(step["relation"]),
                str(step["verification_state"]),
            )
            for step in receipt["steps"]
        )
        return WitnessedProcess(
            process_id=str(receipt["process_id"]),
            source_revision=str(receipt["source_revision"]),
            graph_revision=str(receipt["graph_revision"]),
            projection=str(receipt["projection"]),
            anchors=tuple(map(str, receipt["anchors"])),
            steps=steps,
            branches=tuple(tuple(map(str, branch)) for branch in receipt["branches"]),
            gaps=tuple(map(str, receipt["gaps"])),
            verification_state=str(receipt["verification_state"]),
            stale_reason=str(receipt["stale_reason"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("witnessed process receipt malformed") from exc


def build_planning_payload(
    process: WitnessedProcess,
    *,
    source_revision: str,
    graph_revision: str,
) -> dict[str, Any]:
    """Assemble planning input from persisted process facts only."""
    if process.source_revision != source_revision or process.graph_revision != graph_revision:
        return {
            "schema": "gt.planning_process.v1",
            "status": "ABSTAINED",
            "process_id": process.process_id,
            "source_revision": source_revision,
            "graph_revision": graph_revision,
            "steps": (),
            "citations": (),
            "gaps": ("stale_process_revision",),
        }
    citations = tuple(
        {
            "node_id": step.node_id,
            "edge_id": step.edge_id,
            "evidence_id": step.evidence_id,
            "source_revision": process.source_revision,
            "graph_revision": process.graph_revision,
        }
        for step in process.steps
    )
    return {
        "schema": "gt.planning_process.v1",
        "status": "PARTIAL" if process.gaps else "READY",
        "process_id": process.process_id,
        "source_revision": process.source_revision,
        "graph_revision": process.graph_revision,
        "steps": tuple(step.node_id for step in process.steps),
        "citations": citations,
        "gaps": process.gaps,
    }


def build_witnessed_process(
    *,
    anchors: tuple[str, ...],
    steps: tuple[ProcessStep, ...],
    branches: tuple[tuple[str, ...], ...],
    gaps: tuple[str, ...],
    graph_revision: str,
    source_revision: str,
    projection: str,
    current_graph_revision: str | None = None,
) -> WitnessedProcess:
    canonical_anchors = tuple(sorted(dict.fromkeys(str(item) for item in anchors)))
    canonical_steps = tuple(sorted(
        steps, key=lambda step: (step.node_id, step.edge_id, step.evidence_id)
    ))
    canonical_branches = tuple(sorted(tuple(sorted(branch)) for branch in branches))
    canonical_gaps = tuple(sorted(dict.fromkeys(str(item) for item in gaps)))
    payload = {
        "source_revision": source_revision,
        "graph_revision": graph_revision,
        "projection": projection,
        "anchors": canonical_anchors,
        "steps": [
            {
                "node_id": step.node_id,
                "edge_id": step.edge_id,
                "evidence_id": step.evidence_id,
                "relation": step.relation,
                "verification_state": step.verification_state,
            }
            for step in canonical_steps
        ],
        "branches": canonical_branches,
        "gaps": canonical_gaps,
    }
    process_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    stale = current_graph_revision is not None and current_graph_revision != graph_revision
    return WitnessedProcess(
        process_id=process_id,
        source_revision=source_revision,
        graph_revision=graph_revision,
        projection=projection,
        anchors=canonical_anchors,
        steps=canonical_steps,
        branches=canonical_branches,
        gaps=canonical_gaps,
        verification_state="abstained" if stale else "witnessed",
        stale_reason="graph_revision_stale" if stale else "",
    )


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
    "ProcessStep",
    "WitnessedProcess",
    "build_witnessed_process",
    "build_planning_payload",
    "witnessed_process_from_receipt",
]
