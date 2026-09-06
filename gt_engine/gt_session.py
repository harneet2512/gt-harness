"""GTSession — the narrow, versioned, single-owner GT interface.

T4.2: the deep-research verdict found the harness split lifecycle ownership
between the monkeypatched seam and Groundtruth's own machinery. This module is
the one supported facade the runner talks to. It owns the session lifecycle
(task received -> model round trips -> submit decision -> completion state) and
exposes a small typed API. Everything GT does is behind this surface; nothing
else in the runner reaches into the engine.

Capability negotiation: the host tells GTSession what it can actually provide.
Anything it cannot provide degrades assurance visibly (DEGRADED_ASSURANCE is
recorded) instead of being silently approximated.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from groundtruth.runtime.evidence_envelope import chain_hash

from .delivery_budget import compact_localization, delivery_byte_limit
from .output_evidence import EvidenceStore
from .persistent_execution_state import (
    CatalogItem,
    Feature18Catalog,
    Feature18Lifecycle,
    SelectCatalogAbstention,
    SelectCatalogStage,
    build_feature18_catalog,
    build_select_catalog_messages,
    build_select_catalog_tool,
    parse_select_catalog_arguments,
)
from .request_history import load_history_evidence, store_history_evidence
from .run_diagnostics import CapabilityState, DiagnosticCode, DiagnosticEvent

# Capabilities a host can declare (see the verdict's negotiation list).
# The two stages __init__ can disable GT with. Neither is a fault: one is the
# OFF mode, the other the global kill switch. Shared so the constructor and the
# capability report cannot drift - and the drift directions are not symmetric,
# since a new non-fault stage would merely be reported loudly while a fault
# stage colliding with one of these would be reported as normal and silent.
CONFIGURED_OFF_STAGES = ("off", "global_kill_switch")

# Every stage GTSession.degrade() is called with, across gt_engine, scripts and
# eval. Used to constrain what a journal row may put into a capability evidence
# string: the journal lives inside the task container and the benchmarked agent
# can write to it, so a stage is admitted only if this build defines it. Drift
# here fails loudly as unrecognized_stage rather than quietly as wrong data.
_DEGRADE_STAGES = frozenset({
    "action_identity", "after_action", "before_action", "execution_identity",
    "execution_receipt", "execution_result_identity", "observation_splice",
    "prepare_messages", "provider_failure_receipt", "provider_response_receipt",
    "session_start", "submit_detection", "submit_gate",
    "submitted_result_missing", "suppression_receipt",
    "terminal_refusal_authority", *CONFIGURED_OFF_STAGES,
})

HOST_CAPABILITIES = (
    "exact_provider_payload",     # finalized logical request bytes are exact
    "provider_response_ids",      # real provider request/response ids bound
    "structured_actions",         # actions are parsed, not free-text heuristics
    "structured_results",         # results carry exit code + ordered output
    "workspace_deltas",           # create/modify/delete workspace deltas
    "filesystem_snapshots",       # content-addressed snapshots
    "tool_call_deferral",         # tool calls can be deferred to the host
    "parsed_test_results",        # test output parsed with outcome/protocol
    "streaming",                  # ordered output streaming
    "checkpointing",              # crash-safe resume
    "trusted_verifier",           # clean-env verifier outside the agent
)


def _compress_context(value: str, limit: int) -> str:
    """Shorten only at certified independent localization item boundaries."""
    return compact_localization(value, limit)


class Assurance(StrEnum):
    FULL = "FULL"                 # all declared capabilities actually present
    DEGRADED = "DEGRADED"         # some capability declared but not honored


class GTMode(StrEnum):
    """Capability-preserving rollout modes.

    Only ``ENFORCED`` may prevent a baseline action. New mechanisms are
    expected to start in ``SHADOW`` and graduate through ``ADVISORY`` or
    ``ASSISTIVE`` after evidence supports doing so.
    """

    OFF = "off"
    SHADOW = "shadow"
    ADVISORY = "advisory"
    ASSISTIVE = "assistive"
    ENFORCED = "enforced"


@dataclass
class GTSessionConfig:
    task_id: str
    repo_root: str = ""
    state_dir: str = ""
    graph_db: str | None = None
    capabilities: tuple[str, ...] = ()
    issue_text: str = ""
    mode: GTMode | str = GTMode.ADVISORY
    fail_open: bool = True
    context_budget_bytes: int = 2_000
    capability_modes: Mapping[str, GTMode | str] = field(default_factory=dict)
    disabled_capabilities: tuple[str, ...] = ()
    delivery_path: str = "compiled"


@dataclass
class GTDecisionBatch:
    """The only thing GT returns per decision point.

    context_additions: bytes to surface in the prompt (bounded).
    evidence:           fact/evidence keys produced.
    policy:             policy decisions (e.g. deny a submit).
    verification:       verification requests/results.
    provenance:         provenance event rows.
    degraded:           assurance notes when a declared capability was missing.
    """
    context_additions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    policy: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any([self.context_additions, self.evidence, self.policy,
                        self.verification, self.provenance, self.degraded])


@dataclass(frozen=True)
class GTDecisionCandidate:
    """One producer-owned fact proposed for the current provider decision."""

    rendered: str
    kind: str
    dedup_key: str
    lane: str = "sealed"
    target: str = ""
    semantics: str = "advisory"
    artifact_sha256: str = ""
    previous_chain_head: str = ""
    next_chain_head: str = ""
    verification_candidate: str = ""
    source_ordinal: int = 0
    current_failure: bool = False
    current_obligation: bool = False
    action_index: int = 0
    unit_id: str = ""
    supersession_key: str = ""
    supersedes: tuple[str, ...] = ()
    source_revision: str = ""
    artifact_reference: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class SelectCatalogOffer:
    """One exact, revision-bound task-start selection request."""

    catalog: Feature18Catalog
    messages: tuple[dict[str, str], ...]
    tool: Mapping[str, Any]


_FAILURE_KINDS = frozenset({
    "covering_red",
    "covering_verdict",
    "recovery",
    "test_failure",
    "trace_frame",
})
_OBLIGATION_KINDS = frozenset({
    "context_contract",
    "context_delta",
    "obligations",
})
_LOCALIZATION_KINDS = frozenset({"brief_localization", "localization"})
_WEAK_HISTORY_KINDS = frozenset({"cochange_partner", "cochange_prior"})
_MINISWE_CHAIN_GENESIS = hashlib.sha256(b"miniswe-genesis").hexdigest()


def _decision_candidate_order(candidate: GTDecisionCandidate) -> tuple[int, int, str, str]:
    """Current facts first; weak historical priors consume only spare room."""

    kind = candidate.kind
    if candidate.current_failure or kind in _FAILURE_KINDS:
        priority = 0
    elif candidate.current_obligation or kind in _OBLIGATION_KINDS:
        priority = 1
    elif kind in _LOCALIZATION_KINDS:
        priority = 2
    elif kind in _WEAK_HISTORY_KINDS or "cochange" in kind:
        priority = 4
    else:
        # Edit consequences and verification evidence share the actionable
        # middle lane. Their stable kind/hash order makes replay byte-identical.
        priority = 3
    identity = hashlib.sha256(candidate.rendered.encode("utf-8")).hexdigest()
    return priority, candidate.source_ordinal, kind, identity


class GTSession:
    """Single-owner GT session facade.

    The concrete engine lives behind ``_engine`` (currently the MiniSweAdapter
    seam); the facade is the stable versioned surface. A future consolidation
    can swap the engine for Groundtruth's AttemptReasoningRuntime without the
    runner changing.
    """

    SCHEMA = "gt.session.v1"

    def __init__(self, config: GTSessionConfig, engine: Any | None = None):
        self.config = config
        self._engine = engine
        self.mode = GTMode(str(config.mode))
        self._assurance: list[str] = []
        self.assurance_state = Assurance.FULL
        global_killed = os.environ.get("GT_KILL_SWITCH", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        self.disabled = self.mode is GTMode.OFF or global_killed
        self.disabled_stage = (
            CONFIGURED_OFF_STAGES[1] if global_killed
            else CONFIGURED_OFF_STAGES[0] if self.disabled else ""
        )
        self._terminal: str | None = None
        self._task_start_shipped = False
        self._pending_contract_delta = ""
        self._pending_contract_rendered = ""
        self._pending_contract_identity = ""
        self._pending_localization = ""
        self._pending_localization_identity = ""
        self._queued_decision_candidates: list[GTDecisionCandidate] = []
        self._active_context_units: dict[str, dict[str, Any]] = {}
        self._pending_context_units: dict[str, dict[str, Any]] = {}
        self._execution_sequence = 0
        self._open_executions: set[str] = set()
        self._select_catalog_attempted = False
        self._select_catalog_lifecycle: Feature18Lifecycle | None = None
        self._capability_check()

    def _record_select_catalog(self, reason: str) -> None:
        lifecycle = self._select_catalog_lifecycle
        if lifecycle is None:
            return
        self._engine.store.append(
            "select_catalog_lifecycle",
            lifecycle_schema=lifecycle.receipt()["schema"],
            reason=reason,
            receipt=lifecycle.receipt(),
        )

    @staticmethod
    def _graph_contains_target(graph_path: str, target: str) -> bool:
        normalized = target.replace("\\", "/").lstrip("./")
        if not graph_path or not normalized:
            return False
        try:
            uri = f"file:{os.path.abspath(graph_path)}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                row = connection.execute(
                    "SELECT 1 FROM nodes WHERE file_path = ? OR file_path = ? LIMIT 1",
                    (normalized, normalized.replace("/", "\\")),
                ).fetchone()
            return row is not None
        except (OSError, sqlite3.Error):
            return False

    def prepare_select_catalog(self) -> SelectCatalogOffer | None:
        """Prepare the one eligible task-start catalog through decision admission."""

        if self._select_catalog_attempted:
            return None
        self._select_catalog_attempted = True
        if (
            self._engine is None
            or self.disabled
            or not self.capability_model_visible("select_catalog")
        ):
            return None
        snapshot = self._engine.graph_query_snapshot()
        source_revision = str(snapshot.source_revision or "")
        if not snapshot.graph_current or not source_revision or not snapshot.graph_revision:
            self._engine.store.append(
                "select_catalog_abstained", reason="stale_or_incomplete_graph"
            )
            return None
        localization = self._engine.task_start_localization(commit=False)
        metadata = self._engine.localization_delivery_metadata()
        target = str(metadata.get("target") or "").replace("\\", "/").lstrip("./")
        if not localization or not self._graph_contains_target(snapshot.graph_path, target):
            self._engine.store.append(
                "select_catalog_abstained", reason="no_graph_backed_catalog_item"
            )
            return None
        content_sha256 = hashlib.sha256(localization.encode("utf-8")).hexdigest()
        item_id = f"focus-{hashlib.sha256((target + content_sha256).encode()).hexdigest()[:20]}"
        catalog = build_feature18_catalog(
            source_revision=source_revision,
            workspace_revision=str(getattr(self._engine, "repository_revision", "") or source_revision),
            graph_revision=str(snapshot.graph_revision),
            items=(CatalogItem(item_id, "focus", target, content_sha256, target),),
        )
        self._select_catalog_lifecycle = Feature18Lifecycle.from_catalog(
            catalog, event_id=f"{self.config.task_id}:select_catalog"
        )
        rendered = "[GT_SELECT_CATALOG]\n" + json.dumps(
            catalog.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        batch = self.admit_decision_packet(
            [GTDecisionCandidate(
                rendered=rendered,
                kind="select_catalog",
                dedup_key=f"select_catalog:{catalog.content_sha256}",
                lane="sealed",
                target=target,
                source_revision=source_revision,
                unit_id=catalog.content_sha256,
                supersession_key="select_catalog:task_start",
            )],
            iteration=0,
            action_index=0,
        )
        if not batch.context_additions:
            self._select_catalog_lifecycle.abstain(SelectCatalogAbstention.INCOMPLETE)
            self._record_select_catalog("decision_admission_refused")
            return None
        messages = build_select_catalog_messages(catalog, task=self.config.issue_text)
        messages[-1]["content"] += "\n\n" + batch.context_additions[0]
        self._record_select_catalog("catalog_prepared")
        return SelectCatalogOffer(
            catalog=catalog,
            messages=tuple(messages),
            tool=build_select_catalog_tool(catalog),
        )

    def certify_select_catalog_offer(
        self, *, request_bytes: bytes, tool_schema_bytes: bytes,
        provider_request_id: str, delivery_ids: tuple[str, ...]
    ) -> None:
        lifecycle = self._select_catalog_lifecycle
        if lifecycle is None:
            return
        lifecycle.certify_offer(
            request_bytes=request_bytes,
            tool_schema_bytes=tool_schema_bytes,
            provider_request_id=provider_request_id,
        )
        if lifecycle.stage is SelectCatalogStage.CERTIFIED:
            lifecycle.deliver(delivery_id=provider_request_id)
            self.provider_request_admitted(delivery_ids)
        self._record_select_catalog("provider_request_admitted")

    def accept_select_catalog(self, arguments: Any) -> tuple[str, ...]:
        """Validate selected IDs and queue them for the ordinary Mini-SWE request."""

        lifecycle = self._select_catalog_lifecycle
        if lifecycle is None:
            return ()
        attempted, selected = parse_select_catalog_arguments(arguments, lifecycle.catalog)
        raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode()
        lifecycle.record_selection(
            attempted_ids=attempted, selected_ids=selected, argument_bytes=raw
        )
        if lifecycle.stage is SelectCatalogStage.ABSTAINED:
            self._record_select_catalog("selection_refused")
            return ()
        by_id = {item.item_id: item for item in lifecycle.catalog.items}
        rendered = "[GT_SELECT_CATALOG_RESULT]\n" + json.dumps(
            {
                "schema": "gt.select_catalog.result.v1",
                "catalog_sha256": lifecycle.catalog.content_sha256,
                "selected_items": [by_id[item_id].as_dict() for item_id in selected],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.queue_decision_candidates((GTDecisionCandidate(
            rendered=rendered,
            kind="select_catalog",
            dedup_key=f"select_catalog_result:{lifecycle.catalog.content_sha256}",
            lane="sealed",
            target=by_id[selected[0]].target,
            source_revision=lifecycle.catalog.source_revision,
            unit_id=hashlib.sha256(rendered.encode()).hexdigest(),
            supersession_key="select_catalog:selection",
        ),))
        self._record_select_catalog("selection_accepted")
        return selected

    def fail_select_catalog(self, reason: str) -> None:
        """Close a dispatched selection request after provider/parse failure."""

        lifecycle = self._select_catalog_lifecycle
        if lifecycle is None or lifecycle.stage not in {
            SelectCatalogStage.CANDIDATE,
            SelectCatalogStage.CERTIFIED,
            SelectCatalogStage.DELIVERED,
        }:
            return
        lifecycle.abstain(SelectCatalogAbstention.INCOMPLETE)
        self._record_select_catalog(reason)

    def observe_select_catalog_action(self, command: str) -> None:
        """Consume selection only when a real Mini-SWE Bash action matches it."""

        lifecycle = self._select_catalog_lifecycle
        if lifecycle is None or lifecycle.stage is not SelectCatalogStage.DELIVERED:
            return
        if lifecycle.catalog.source_revision != str(self._engine.repository_revision or ""):
            lifecycle.abstain(SelectCatalogAbstention.STALE_REVISION)
            self._record_select_catalog("selection_stale_before_action")
            return
        selected = {item.item_id: item for item in lifecycle.catalog.items
                    if item.item_id in lifecycle.selected_ids}
        try:
            command_tokens = {
                token.replace("\\", "/").lstrip("./")
                for token in shlex.split(command, posix=os.name != "nt")
            }
        except ValueError:
            return
        matched = tuple(
            item_id for item_id, item in selected.items()
            if item.target and item.target in command_tokens
        )
        if not matched:
            return
        lifecycle.consume(selected_ids=matched, resulting_action=command)
        self._record_select_catalog("matching_action_consumed")

    @property
    def engine(self) -> Any | None:
        """The integration engine, exposed read-only to the runtime seam."""
        return self._engine

    @property
    def model_visible(self) -> bool:
        return not self.disabled and self.mode in {
            GTMode.ADVISORY, GTMode.ASSISTIVE, GTMode.ENFORCED,
        }

    @property
    def allows_live_probes(self) -> bool:
        """Implicit commands require two explicit, auditable opt-ins."""
        return (
            not self.disabled
            and self.mode is GTMode.ASSISTIVE
            and os.environ.get("GT_ALLOW_LIVE_PROBES", "").strip() == "1"
            and os.environ.get("GT_VERIFY_EXECUTE", "").strip() == "1"
        )

    @property
    def can_enforce(self) -> bool:
        return not self.disabled and self.mode is GTMode.ENFORCED

    @staticmethod
    def _capability_key(capability: str) -> str:
        key = capability.strip().lower().replace("-", "_")
        if not key or any(not (char.isalnum() or char == "_") for char in key):
            raise ValueError(f"invalid capability name: {capability!r}")
        return key

    def capability_mode(self, capability: str) -> GTMode:
        """Resolve one capability's mode without changing the global posture."""
        key = self._capability_key(capability)
        disabled = {self._capability_key(item) for item in self.config.disabled_capabilities}
        disabled.update(
            self._capability_key(item)
            for item in os.environ.get("GT_DISABLED_CAPABILITIES", "").split(",")
            if item.strip()
        )
        if key in disabled:
            return GTMode.OFF
        env_name = f"GT_CAPABILITY_{key.upper()}_MODE"
        configured = os.environ.get(env_name)
        if configured is None:
            configured = self.config.capability_modes.get(key, self.mode)
        try:
            return GTMode(str(configured).lower())
        except ValueError:
            # An invalid switch must disable the capability, not broaden it.
            return GTMode.OFF

    def capability_active(self, capability: str) -> bool:
        return not self.disabled and self.capability_mode(capability) is not GTMode.OFF

    def capability_model_visible(self, capability: str) -> bool:
        return self.capability_active(capability) and self.capability_mode(capability) in {
            GTMode.ADVISORY, GTMode.ASSISTIVE, GTMode.ENFORCED,
        }

    def degrade(self, stage: str, error: BaseException) -> None:
        """Disable GT for the rest of the run while preserving Mini-SWE.

        This operation is idempotent: the first fault is the causal fault and
        later calls are merely consequences of an already disabled observer.
        """
        if self.disabled:
            return
        self.disabled = True
        self.disabled_stage = stage
        self.assurance_state = Assurance.DEGRADED
        note = f"{stage}: {type(error).__name__}: {str(error)[:300]}"
        self._assurance.append(note)
        try:
            if self._engine is not None:
                self._engine.store.append(
                    "gt_degraded_fail_open",
                    stage=stage,
                    error_type=type(error).__name__,
                    error=str(error)[:300],
                )
        except Exception:
            # The state sink may itself be the failed component. Fail-open must
            # not recurse through another telemetry failure.
            pass

    # -- capability negotiation -------------------------------------------
    def _capability_check(self) -> None:
        declared = set(self.config.capabilities)
        if not declared:
            self._assurance.append("no host capabilities declared")
        if "exact_provider_payload" in declared and not self.config.state_dir:
            self._assurance.append("exact_provider_payload declared without state_dir")
        if "trusted_verifier" in declared and os.environ.get("GT_VERIFY_EXECUTE") != "1":
            self._assurance.append("trusted_verifier declared but GT_VERIFY_EXECUTE!=1")
        if self._assurance:
            self.assurance_state = Assurance.DEGRADED

    def degraded_notes(self) -> tuple[str, ...]:
        return tuple(self._assurance)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> GTDecisionBatch:
        if self._engine is None or self.disabled:
            return GTDecisionBatch()
        self._engine.start_task()
        return GTDecisionBatch(provenance=[{"event": "session_started"}])

    def before_model(self, messages: list[dict], iteration: int) -> GTDecisionBatch:
        """Deliver context additions (contract/localization) before a model call."""
        if self._engine is None or self.disabled:
            return GTDecisionBatch()
        candidates = list(self._queued_decision_candidates)
        contract_candidate: tuple[str, str] | None = None
        contract_unit_id = ""
        localization_candidate = ""
        localization_unit_id = ""
        contract_was_shipped = bool(self._engine.contract_shipped)
        contract_kind = "context_delta" if contract_was_shipped else "context_contract"
        delta = self._engine.next_contract_delta(
            commit=False,
            max_chars=min(
                self.config.context_budget_bytes,
                delivery_byte_limit(lane="prompt", kind=contract_kind),
            )
        )
        if delta:
            tag = "GT_TASK_CONTRACT" if not contract_was_shipped else "GT_OBLIGATION_DELTA"
            rendered = f"[{tag}]\n{delta}"
            if self.model_visible:
                kind = contract_kind
                payload_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
                supersession_key = "obligations:task"
                active = self._active_context_units.get(supersession_key)
                reference = self._store_context_unit(rendered)
                candidates.append(GTDecisionCandidate(
                    rendered=rendered,
                    kind=kind,
                    dedup_key=f"prompt:{payload_hash}",
                    lane="prompt",
                    target="provider_prompt",
                    current_obligation=True,
                    unit_id=payload_hash,
                    supersession_key=supersession_key,
                    supersedes=((active["unit_id"],) if active else ()),
                    source_revision=str(
                        getattr(self._engine, "repository_revision", "") or ""
                    ),
                    artifact_sha256=reference.get("sha256", ""),
                    artifact_reference=reference or None,
                ))
                contract_candidate = (delta, rendered)
                contract_unit_id = payload_hash
            else:
                self._engine.store.append(
                    "shadow_context_computed",
                    iteration=iteration,
                    rendered_bytes=len(rendered.encode("utf-8")),
                )
        if (
            not self._task_start_shipped
            and self.config.delivery_path == "compiled"
        ):
            localization = self._engine.task_start_localization(commit=False)
            if localization:
                original_localization = localization
                original_bytes = len(original_localization.encode("utf-8"))
                localization = _compress_context(original_localization, 1_400)
                delivered_bytes = len(localization.encode("utf-8"))
                if delivered_bytes < original_bytes:
                    self._engine.store.append(
                        "localization_compressed",
                        original_bytes=original_bytes,
                        delivered_bytes=delivered_bytes,
                        lane_cap_bytes=1_400,
                    )
                    diagnostics = getattr(self._engine, "diagnostics", None)
                    if diagnostics is not None:
                        diagnostics.record(
                            DiagnosticEvent.create(
                                code=DiagnosticCode.GT_LOCALIZATION_OVERSIZED,
                                severity="WARNING",
                                phase="task_start",
                                subsystem="delivery",
                                capability="localization",
                                task_id=self._engine.task_id,
                                classification="consequential",
                                cause="localization_exceeded_lane_cap",
                                impact="localization_compressed",
                                recovery="retain_ranked_evidence_within_1400_bytes",
                                retryable=False,
                                event_sequence=int(
                                    self._engine.store.receipt()["event_count"]
                                ),
                            )
                        )
                if not self.model_visible:
                    self._engine.store.append(
                        "shadow_task_start_localization",
                        rendered_bytes=len(localization.encode("utf-8")),
                    )
                elif localization:
                    metadata = self._engine.localization_delivery_metadata()
                    payload_hash = hashlib.sha256(localization.encode("utf-8")).hexdigest()
                    supersession_key = "localization:task"
                    active = self._active_context_units.get(supersession_key)
                    # The compact localization remains useful inline, while its
                    # reference always identifies the producer's complete unit.
                    reference = self._store_context_unit(original_localization)
                    localization_unit_id = reference.get("sha256", payload_hash)
                    candidates.append(GTDecisionCandidate(
                        rendered=localization,
                        kind=metadata["kind"],
                        dedup_key=metadata["dedup_key"],
                        target=metadata.get("target", ""),
                        semantics=metadata.get("semantics", "advisory"),
                        artifact_sha256=metadata.get("artifact_sha256", ""),
                        unit_id=localization_unit_id,
                        supersession_key=supersession_key,
                        supersedes=((active["unit_id"],) if active else ()),
                        source_revision=str(
                            getattr(self._engine, "repository_revision", "") or ""
                        ),
                        artifact_reference=reference or None,
                    ))
                    localization_candidate = localization
        batch = self.admit_decision_packet(
            candidates, iteration=iteration, action_index=0
        )
        if contract_candidate and contract_candidate[1] in batch.context_additions:
            self._pending_contract_delta, self._pending_contract_rendered = contract_candidate
        elif contract_candidate:
            delivered = next(
                (
                    item for item in batch.context_additions
                    if f'"unit_id":"{contract_unit_id}"' in item
                ),
                "",
            )
            if delivered:
                self._pending_contract_delta, self._pending_contract_rendered = contract_candidate
                self._pending_contract_identity = hashlib.sha256(delivered.encode()).hexdigest()
        if localization_candidate in batch.context_additions:
            self._pending_localization = localization_candidate
        elif localization_candidate:
            delivered = next(
                (
                    item for item in batch.context_additions
                    if f'"unit_id":"{localization_unit_id}"' in item
                ),
                "",
            )
            if delivered:
                self._pending_localization = localization_candidate
                self._pending_localization_identity = hashlib.sha256(delivered.encode()).hexdigest()
        return batch

    def _store_context_unit(self, rendered: str) -> dict[str, Any]:
        try:
            root = self._engine.engine_state.layout.evidence_root
            return store_history_evidence(
                EvidenceStore(root), rendered.encode("utf-8"), kind="decision_evidence"
            )
        except (AttributeError, OSError, ValueError):
            return {}

    def _valid_context_reference(self, reference: Mapping[str, Any]) -> bool:
        """Accept only an existing immutable object in this task's evidence CAS."""

        try:
            root = self._engine.engine_state.layout.evidence_root
            payload = load_history_evidence(root, reference)
            encoding = str(reference.get("encoding") or "")
            if encoding not in {"utf-8", "base64"}:
                return False
            actual_encoding = "utf-8"
            try:
                payload.decode("utf-8", "strict")
            except UnicodeDecodeError:
                actual_encoding = "base64"
            return (
                encoding == actual_encoding
                and bool(str(reference.get("kind") or "").strip())
                and str(reference.get("retrieval_command") or "")
                == f"gt-evidence read {reference['sha256']} 0 8192"
            )
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return False

    def provider_request_admitted(self, delivery_ids: tuple[str, ...]) -> None:
        """Commit shipped latches only for bytes in an admitted final request."""
        identities = set(delivery_ids)
        contract_identity = self._pending_contract_identity or (
            hashlib.sha256(self._pending_contract_rendered.encode()).hexdigest()
            if self._pending_contract_rendered else ""
        )
        if contract_identity and contract_identity in identities:
            self._engine.acknowledge_contract_delta(self._pending_contract_delta)
            self._pending_contract_delta = ""
            self._pending_contract_rendered = ""
            self._pending_contract_identity = ""
        localization_identity = self._pending_localization_identity or (
            hashlib.sha256(self._pending_localization.encode()).hexdigest()
            if self._pending_localization else ""
        )
        if localization_identity and localization_identity in identities:
            self._engine.acknowledge_localization(self._pending_localization)
            self._pending_localization = ""
            self._pending_localization_identity = ""
            self._task_start_shipped = True
        for identity, unit in tuple(self._pending_context_units.items()):
            if identity not in identities:
                continue
            key = unit["supersession_key"]
            if not unit["historical"]:
                self._active_context_units[key] = {
                    "unit_id": unit["unit_id"],
                    "source_revision": unit["source_revision"],
                    "action_index": unit["action_index"],
                }
            self._engine.store.append(
                "decision_context_unit_admitted",
                delivery_identity=identity,
                unit_id=unit["unit_id"],
                supersession_key=key,
                supersedes=list(unit["supersedes"]),
                source_revision=unit["source_revision"],
                artifact_sha256=unit["artifact_sha256"],
                artifact_reference=unit["artifact_reference"],
                historical=unit["historical"],
                action_index=unit["action_index"],
            )
        self._pending_context_units.clear()
        # Queued action evidence belongs to this exact decision. A provider
        # refusal never calls this method, so the same candidates remain
        # available for the request retry without being promoted to history.
        self._queued_decision_candidates.clear()

    def queue_decision_candidates(
        self, candidates: list[GTDecisionCandidate] | tuple[GTDecisionCandidate, ...]
    ) -> None:
        """Retain action evidence until the next exact provider request is built."""

        if self._engine is None or self.disabled or not self.model_visible:
            return
        self._queued_decision_candidates.extend(
            candidate for candidate in candidates if candidate.rendered
        )

    def admit_decision_packet(
        self,
        candidates: list[GTDecisionCandidate] | tuple[GTDecisionCandidate, ...],
        *,
        iteration: int,
        action_index: int,
    ) -> GTDecisionBatch:
        """Admit every fitting current-decision fact through the existing owner.

        Producers keep ownership of fact bytes and derivation. GTSession only
        orders the decision-local proposals and asks the adapter's transactional
        admission boundary to prepare each one. Exposure chain state is staged
        only after that candidate is admitted; a refused candidate therefore
        cannot consume a dedup key, chain head, or verification proposal.
        """

        if self._engine is None or self.disabled or not self.model_visible:
            return GTDecisionBatch()
        additions: list[str] = []
        evidence: list[str] = []
        verification: list[str] = []
        provenance: list[dict] = []
        _, admission_chain_head = self._engine.pending_evidence_chain()
        proposed_units = dict(self._active_context_units)
        current_revision = str(
            getattr(self._engine, "repository_revision", "")
            or getattr(getattr(self._engine, "engine_state", None), "source_revision", "")
            or ""
        )

        def is_historical(candidate: GTDecisionCandidate) -> bool:
            return bool(
                current_revision
                and candidate.source_revision
                and candidate.source_revision != current_revision
            )

        ordered = sorted(
            candidates,
            key=lambda candidate: (
                1 if is_historical(candidate) else 0,
                *_decision_candidate_order(candidate),
            ),
        )
        for candidate in ordered:
            if not candidate.rendered:
                continue
            original_sha256 = hashlib.sha256(
                candidate.rendered.encode("utf-8")
            ).hexdigest()
            artifact_reference = dict(
                candidate.artifact_reference
                or self._store_context_unit(candidate.rendered)
            )
            reference_sha256 = str(artifact_reference.get("sha256") or "")
            if artifact_reference and not self._valid_context_reference(
                artifact_reference
            ):
                self._engine.store.append(
                    "decision_context_unit_refused",
                    reason="artifact_reference_identity_mismatch",
                    payload_sha256=original_sha256,
                    artifact_sha256=candidate.artifact_sha256,
                )
                continue
            unit_id = candidate.unit_id or reference_sha256 or original_sha256
            supersedes = tuple(candidate.supersedes)
            historical = is_historical(candidate)
            previous_unit = (
                None if historical else proposed_units.get(candidate.supersession_key)
            )
            if (
                candidate.supersession_key
                and previous_unit is not None
                and previous_unit["unit_id"] != unit_id
                and previous_unit["unit_id"] not in supersedes
            ):
                if (
                    candidate.source_revision
                    and previous_unit["source_revision"]
                    and candidate.source_revision != previous_unit["source_revision"]
                ):
                    supersedes = (*supersedes, previous_unit["unit_id"])
                elif (
                    candidate.action_index > 0
                    and candidate.action_index > previous_unit.get("action_index", 0)
                ):
                    # Re-running the same command against an unchanged tree can
                    # reverse its outcome. The later executed observation owns
                    # the current claim even when the repository revision did
                    # not move.
                    supersedes = (*supersedes, previous_unit["unit_id"])
                else:
                    self._engine.store.append(
                        "decision_context_unit_refused",
                        reason="implicit_supersession_forbidden",
                        unit_id=unit_id,
                        supersession_key=candidate.supersession_key,
                        active_unit_id=previous_unit["unit_id"],
                        source_revision=candidate.source_revision,
                    )
                    continue
            if historical:
                supersedes = ()
            rendered = candidate.rendered
            if candidate.supersession_key:
                visible_reference = {
                    key: artifact_reference[key]
                    for key in (
                        "schema", "sha256", "total_length", "encoding", "kind",
                        "retrieval_command",
                    )
                    if key in artifact_reference
                }
                metadata = {
                    "unit_id": unit_id,
                    "supersession_key": candidate.supersession_key,
                    "source_revision": candidate.source_revision,
                    "supersedes": list(dict.fromkeys(supersedes)),
                    "historical": historical,
                    "action_index": candidate.action_index,
                }
                header = "[GT_CONTEXT_UNIT] " + json.dumps(
                    metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                full = f"{header}\n{candidate.rendered}"
                limit = delivery_byte_limit(lane=candidate.lane, kind=candidate.kind)
                if historical and visible_reference:
                    rendered = f"{header}\n[GT_CONTEXT_UNIT_REFERENCE] " + json.dumps(
                        visible_reference, ensure_ascii=True, sort_keys=True,
                        separators=(",", ":"),
                    )
                elif len(full.encode("utf-8")) <= limit:
                    rendered = full
                elif visible_reference:
                    rendered = f"{header}\n[GT_CONTEXT_UNIT_REFERENCE] " + json.dumps(
                        visible_reference, ensure_ascii=True, sort_keys=True,
                        separators=(",", ":"),
                    )
                else:
                    self._engine.store.append(
                        "decision_context_unit_refused",
                        reason="context_unit_metadata_byte_ceiling",
                        unit_id=unit_id,
                        supersession_key=candidate.supersession_key,
                        source_revision=candidate.source_revision,
                    )
                    continue
            payload_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            admitted = self._engine.admit_model_visible_delivery(
                lane=candidate.lane,
                kind=candidate.kind,
                rendered=rendered,
                action_index=candidate.action_index or action_index,
                iteration=iteration,
                dedup_key=candidate.dedup_key,
                target=candidate.target,
                semantics=candidate.semantics,
                artifact_sha256=candidate.artifact_sha256,
            )
            if not admitted:
                continue
            if candidate.supersession_key:
                unit = {
                    "unit_id": unit_id,
                    "supersession_key": candidate.supersession_key,
                    "supersedes": tuple(dict.fromkeys(supersedes)),
                    "source_revision": candidate.source_revision,
                    "artifact_sha256": candidate.artifact_sha256,
                    "artifact_reference": artifact_reference,
                    "historical": historical,
                    "action_index": candidate.action_index,
                }
                self._pending_context_units[payload_sha256] = unit
                if not historical:
                    proposed_units[candidate.supersession_key] = {
                        "unit_id": unit_id,
                        "source_revision": candidate.source_revision,
                        "action_index": candidate.action_index,
                    }
                self._engine.store.append(
                    "decision_context_unit_prepared",
                    delivery_identity=payload_sha256,
                    unit_id=unit_id,
                    supersession_key=candidate.supersession_key,
                    supersedes=list(unit["supersedes"]),
                    source_revision=candidate.source_revision,
                    artifact_sha256=candidate.artifact_sha256,
                    artifact_reference=artifact_reference,
                    historical=historical,
                    action_index=candidate.action_index,
                )
            if candidate.dedup_key and (
                candidate.previous_chain_head
                or candidate.next_chain_head
                or candidate.verification_candidate
            ):
                previous_chain_head = candidate.previous_chain_head
                next_chain_head = candidate.next_chain_head
                if candidate.next_chain_head:
                    previous_chain_head = admission_chain_head
                    next_chain_head = chain_hash(
                        admission_chain_head or _MINISWE_CHAIN_GENESIS,
                        rendered.encode("utf-8"),
                    )
                self._engine.stage_exposure(
                    rendered=rendered,
                    dedup_key=candidate.dedup_key,
                    previous_chain_head=previous_chain_head,
                    next_chain_head=next_chain_head,
                    verification_candidate=candidate.verification_candidate,
                )
                if candidate.next_chain_head:
                    admission_chain_head = next_chain_head
            additions.append(rendered)
            evidence.append(candidate.kind)
            if candidate.kind == "verification_plan":
                verification.append(rendered)
            provenance.append({
                "event": "decision_candidate_admitted",
                "kind": candidate.kind,
                "dedup_key": candidate.dedup_key,
                "payload_sha256": payload_sha256,
            })
        return GTDecisionBatch(
            context_additions=additions,
            evidence=evidence,
            verification=verification,
            provenance=provenance,
        )

    def after_action(
        self,
        *,
        command: str,
        output: str,
        returncode: int | None,
        action_index: int,
    ) -> GTDecisionBatch:
        if self._engine is None or self.disabled:
            return GTDecisionBatch()
        batch = GTDecisionBatch()
        if self._engine.contract is not None:
            self._engine.evaluate_observation(
                command, output, returncode=returncode, action_index=action_index
            )
            self._engine.evaluate_failing_observation(
                command, output, returncode=returncode, action_index=action_index
            )
        return batch

    def request_submit(self) -> tuple[bool, GTDecisionBatch]:
        """The submit decision. Returns (accepted, decision batch)."""
        if self._engine is None or self.disabled:
            return True, GTDecisionBatch()
        if not self.can_enforce:
            blocking = tuple(getattr(self._engine, "blocking_predicates", ()))
            if blocking:
                self._engine.store.append(
                    "submit_advisory",
                    mode=self.mode.value,
                    predicate_ids=list(blocking),
                )
            accepted = self._engine.advisory_submit_decision()
            return accepted, GTDecisionBatch()
        accepted = self._engine.submit_decision()
        batch = GTDecisionBatch(policy=["accept" if accepted else "deny"])
        return accepted, batch

    def completion_state(self) -> dict[str, Any]:
        """Final session state, including honest verified/unverified."""
        if self._engine is None:
            return {"verified": False, "terminal": "internal_error"}
        state = dict(self._engine.final_state())
        state.update({
            "gt_mode": self.mode.value,
            "gt_disabled": self.disabled,
            "gt_disabled_stage": self.disabled_stage,
            "assurance": self.assurance_state.value,
            "engine_integrity": self.integrity_receipt(),
        })
        if not state["engine_integrity"]["valid"]:
            state["verified"] = False
        return state

    def integrity_receipt(self) -> dict[str, Any]:
        """Engine participation is independent of whether obligations are green."""
        issues = []
        if self._engine is None:
            issues.append("engine_missing")
        if self.disabled:
            issues.append("engine_disabled")
        if self.assurance_state is not Assurance.FULL:
            issues.append("engine_assurance_degraded")
        if self._open_executions:
            issues.append("execution_terminal_missing")
        return {"schema": "gt.engine_integrity.v1", "valid": not issues,
                "mode": self.mode.value, "disabled_stage": self.disabled_stage,
                "issues": issues}

    def suppress(self, action: Mapping[str, Any], result: Any, *, reason: str) -> Any:
        """Account for a policy refusal without claiming that an executor ran."""
        if self.mode is GTMode.OFF:
            return result
        try:
            def digest(value: Any) -> str:
                return hashlib.sha256(json.dumps(
                    value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()).hexdigest()
            head = self._engine.store.receipt()["event_head"]
            self._engine.store.append(
                "action_suppressed", action_index=self._engine.global_action,
                action_id=f"{self.config.task_id}:suppressed:{head}",
                action_sha256=digest(dict(action)), result_sha256=digest(result),
                reason=reason, executed=False,
            )
        except Exception as exc:
            self.degrade("suppression_receipt", exc)
        return result

    def execute(self, action: Mapping[str, Any], executor: Callable[[], Any]) -> Any:
        """Own execution accounting without replacing the model's chosen action."""
        if self.mode is GTMode.OFF:
            return executor()
        self._execution_sequence += 1
        try:
            journal_head = str(self._engine.store.receipt()["event_head"])
        except Exception as exc:
            self.degrade("execution_identity", exc)
            journal_head = "unavailable"
        execution_id = f"{self.config.task_id}:execution:{self._execution_sequence}:{journal_head}"
        self._open_executions.add(execution_id)

        def record(event: str, **details: Any) -> None:
            try:
                if self._engine is None:
                    raise RuntimeError("engine_missing")
                self._engine.store.append(event, execution_id=execution_id,
                                          action_index=self._engine.global_action, **details)
            except Exception as exc:
                self.degrade("execution_receipt", exc)

        try:
            action_digest = hashlib.sha256(json.dumps(
                dict(action), sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()).hexdigest()
        except (TypeError, ValueError) as exc:
            self.degrade("action_identity", exc)
            action_digest = ""
        record("execution_started", action_sha256=action_digest,
               engine_disabled=self.disabled)
        try:
            result = executor()
        except BaseException as exc:
            record("execution_finished", disposition="raised", error_type=type(exc).__name__)
            raise
        else:
            observation = result[1] if isinstance(result, tuple) and len(result) == 2 else result
            try:
                result_digest = hashlib.sha256(json.dumps(
                    observation, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()).hexdigest()
            except (TypeError, ValueError) as exc:
                self.degrade("execution_result_identity", exc)
                result_digest = ""
            record("execution_finished", disposition="returned", result_sha256=result_digest)
            return result
        finally:
            self._open_executions.discard(execution_id)

    def _mandatory_capability_rows(
        self,
    ) -> list[tuple[str, "CapabilityState", str, bool]]:
        """State of the capabilities that must never degrade silently.

        Derived from what the run actually recorded, never from configuration.
        An unreadable or absent record is FAILED, not unknown - "we could not
        tell" and "it worked" must not look alike in the one summary a human
        reads at the end of a task.

        Both readings come from the journal because that is where the code
        paths that actually do the work report. This first read the promotion
        seal beside the graph instead, which is written by indexer's
        start_lsp_promotion - a reporting-only vestige the benchmark path never
        schedules through. It always says promotion_not_scheduled, so a run
        whose coordinator promoted successfully would still have been named as
        a capability that did not work, and the gate built on it enforced
        language-server presence while claiming to enforce promotion. The
        benchmark tap is GraphBuildCoordinator.consider_enrichment ->
        _schedule_lsp_candidate, which journals lsp_promotion_scheduled and
        then lsp_promotion_terminal, so those are what get read.
        """
        import json as _json
        from pathlib import Path as _Path

        rows: list[tuple[str, CapabilityState, str, bool]] = []
        store = getattr(self._engine, "store", None)
        journal = getattr(store, "path", None)

        def _promotion_yield(row: dict[str, object]) -> tuple[int, int] | None:
            """(edges promoted, edges tombstoned), or None if it cannot be told.

            The journal row carries only status and disposition; the counts
            that say whether the tier was populated live in the terminal
            receipt blob _record_lsp_terminal writes beside the journal. None
            means unreadable, which must not be reported as success - the whole
            point of this helper is that "we could not tell" and "it worked"
            do not look alike.

            Only `verified` and `corrected` are the tier. Both stamp
            resolution_method='lsp' at confidence 1.0 (resolve.py:944-959) and
            nothing else does. `deleted` is NOT a removal and NOT a promotion:
            it stamps resolution_method='lsp_window_miss' at confidence 0.0 and
            trust tier SPECULATIVE (resolve.py:994-1008), a non-destructive
            tombstone the closure excludes from traversal. Summing it made a
            receipt of verified=0, corrected=0, deleted=40 report WORKING on a
            graph whose lsp tier was empty and forty of whose edges had just
            been demoted out of traversal - the same defect this yield check
            was added to prevent. It is real work and it is reported, but never
            in the number that decides WORKING.

            The blob directory comes from the journal's own parent rather than
            store.root, because root is not in the EvidenceStore protocol
            (request_history.py declares put_blob/blob_exists and no root) and
            a conforming store without it would make every published run
            report yield_unknown, failing closed but invisibly.
            """
            relative = str(row.get("artifact_blob") or "")
            if not relative or journal is None:
                return None
            try:
                receipt = _json.loads(
                    (_Path(journal).parent / relative).read_text(encoding="utf-8")
                )
            except Exception:  # noqa: BLE001 - an unreadable receipt is unknown
                return None
            if not isinstance(receipt, dict):
                return None
            promoted = sum(
                int(receipt.get(key) or 0) for key in ("verified", "corrected")
            )
            return promoted, int(receipt.get("deleted") or 0)

        dense_state = CapabilityState.FAILED
        dense_evidence = "dense_index_receipt_absent"
        lsp_state = CapabilityState.FAILED
        lsp_evidence = "promotion_never_scheduled"
        fail_open: dict[str, object] | None = None
        scheduled = False
        terminals: list[dict[str, object]] = []
        schedule_order: list[str] = []
        try:
            for position, line in enumerate(
                _Path(journal).read_text(encoding="utf-8").splitlines()
            ):
                if not line.strip():
                    continue
                row = _json.loads(line)
                # Journal order IS chronological order: append-only, single
                # writer, under a lock, fsynced per row. So the parse position
                # is the ordering, and it is always present. Deriving it from
                # the row's own `sequence` field needed a default for rows that
                # lack one, and every default collides with a real position -
                # zero sorts a sequence-less row oldest, exactly as -1 and
                # len(schedule_order) each mis-sorted an unscheduled revision
                # earlier in this function's history. Mixed journals are not
                # hypothetical: ExternalStateStore.__init__ contemplates them
                # and hands them to a verifier rather than blessing them.
                row["_position"] = position
                event = row.get("event")
                if event == "gt_degraded_fail_open":
                    # degrade() is idempotent, so the first row is the causal
                    # fault and any later one is a consequence of it.
                    fail_open = fail_open or row
                elif event == "dense_index_ready":
                    if row.get("query_ready") is True:
                        dense_state = CapabilityState.WORKING
                        dense_evidence = "dense_index_ready_query_ready"
                    else:
                        dense_state = CapabilityState.DEGRADED
                        dense_evidence = "dense_index_ready_not_query_ready"
                elif event == "lsp_promotion_scheduled":
                    scheduled = True
                    revision = str(row.get("graph_revision") or "")
                    if revision and revision not in schedule_order:
                        schedule_order.append(revision)
                elif event == "lsp_promotion_terminal":
                    # A run enriches every graph revision it publishes, so
                    # several terminals are normal, and poll() journals the
                    # draining ones AFTER the active one
                    # (graph_coordinator.py:430-433) - a stale receipt for r0
                    # lands after r1's success, so the literal last row is the
                    # wrong answer. Rows are therefore ranked by which graph
                    # they describe, never by disposition: filtering on the
                    # obsolete prefix looked equivalent and is not, because
                    # "obsolete" is emitted by BOTH the draining path (:395)
                    # and the ACTIVE path (:312) on the same literal. Dropping
                    # it discarded the most common real outcome - promotion
                    # succeeded and lost the publication race - and reported it
                    # as though promotion had never run at all.
                    # Rank stamped HERE, not after the loop. An unscheduled
                    # revision is newer than everything scheduled SO FAR, which
                    # is what this position knows and all that is true.
                    # Computing it afterwards ranked such a row above every
                    # scheduled revision, so a run whose FIRST enrichment threw
                    # and whose every later one published cleanly reported
                    # FAILED. Ranking it oldest had the mirror fault. Both were
                    # attempts to synthesise recency from a list that failures
                    # never join; the parse order already has it.
                    revision = str(row.get("input_graph_revision") or "")
                    row["_rank"] = (
                        schedule_order.index(revision)
                        if revision in schedule_order else len(schedule_order)
                    )
                    terminals.append(row)
        except Exception:  # noqa: BLE001 - an unreadable journal is a failure
            # Both must fail closed. The journal is parsed line by line, so a
            # truncated final line - the likeliest corruption when a process is
            # killed at its wall-clock budget - can leave dense_state WORKING
            # from an earlier row while the evidence says unreadable. A state
            # and an evidence string that contradict each other are worse than
            # either, and this is the summary a human reads.
            dense_state = CapabilityState.FAILED
            dense_evidence = "dense_index_receipt_unreadable"
            lsp_evidence = "promotion_journal_unreadable"
            terminals = []
        else:
            if terminals:
                # The newest graph the run scheduled an enrichment for is the
                # one whose fate the report is about; a terminal for an older
                # revision describes a graph that has already been replaced.
                newest = max(row["_rank"] for row in terminals)
                current = [row for row in terminals if row["_rank"] == newest]
                # Direct indexing, not .get(... or 0): every appended row is
                # stamped one line earlier, so a missing stamp is a defect and
                # should raise here rather than silently tie with the first
                # revision. This is a function whose entire history is silent
                # wrong answers.
                terminal = max(current, key=lambda row: row["_position"])
                status = str(terminal.get("status") or "")
                disposition = str(terminal.get("disposition") or "")
                if status == "no_op":
                    # no_op IS languages_promotable == [] - that is the branch
                    # condition. Its coordinator disposition is the generic
                    # not_publishable bucket, which implies something existed
                    # that could not be published, so the true reason is named
                    # here instead of borrowing a string that misleads.
                    lsp_evidence = "terminal_no_op:nothing_promotable"
                else:
                    lsp_evidence = (
                        f"terminal_{status or 'unknown'}:{disposition or 'none'}"
                    )
                if disposition == "published":
                    # Publication is necessary and NOT sufficient. A receipt can
                    # come back succeeded with verified/corrected/deleted all
                    # zero: edge_mutations is then 0, the closure is never
                    # rebuilt, and the candidate is a semantically identical
                    # copy of the base that certifies and publishes cleanly.
                    # Reporting WORKING there would reproduce the original
                    # complaint - an empty highest-precision tier described as
                    # healthy - inside the reporter built to catch it.
                    yielded = _promotion_yield(terminal)
                    if yielded is None:
                        lsp_state = CapabilityState.DEGRADED
                        lsp_evidence += ":yield_unknown"
                    else:
                        promoted, tombstoned = yielded
                        lsp_state = (
                            CapabilityState.WORKING if promoted > 0
                            else CapabilityState.DEGRADED
                        )
                        lsp_evidence += f":{promoted}_edges"
                        if tombstoned:
                            lsp_evidence += f":{tombstoned}_tombstoned"
                elif status == "succeeded":
                    # Promotion worked and the enriched graph never became the
                    # published one - obsolete, obsolete_after_certification or
                    # not_publishable, i.e. it lost the race with the next
                    # rebuild. The tier is empty, but for a completely
                    # different reason than promotion failing, and the two must
                    # not read alike: this one says tune the race, not fix LSP.
                    lsp_state = CapabilityState.DEGRADED
                elif status in {"no_op", "cancelled"}:
                    # Both add zero edges. no_op was WORKING here on the
                    # reasoning that the machinery functioned - but that axis
                    # makes cancelled WORKING too, and the axis asked for is
                    # whether the tier is populated.
                    lsp_state = CapabilityState.DEGRADED
                else:
                    # unavailable (no server for a promotable language) and
                    # failed both mean the highest-precision edge tier is empty
                    # for a reason the run could have prevented.
                    lsp_state = CapabilityState.FAILED
                if len({str(row.get("status") or "") for row in terminals}) > 1:
                    # Only when the terminals DISAGREE. Every published graph
                    # gets an enrichment, so a healthy five-edit task produces
                    # five terminals; a bare count would fire on every clean
                    # run and train the reader to ignore the field.
                    lsp_evidence += f":last_of_{len(terminals)}"
            elif scheduled:
                lsp_state = CapabilityState.DEGRADED
                lsp_evidence = "scheduled_no_terminal"
        rows.append(("dense_retrieval", dense_state, dense_evidence, True))
        rows.append(("lsp_promotion", lsp_state, lsp_evidence, True))

        # A run in which GT switched itself off partway through is the single
        # case a reader most needs told, and it was the one they were least
        # likely to learn: degrade() records gt_degraded_fail_open and nothing
        # anywhere read it - not a gate, not a receipt, not a report, not even
        # a test. Every claim GT makes after that point comes from an observer
        # that has already stopped observing.
        #
        # Both the journal and the session are consulted because degrade()
        # wraps its own append in try/except - the state sink may be the very
        # component that failed - so GT can be disabled with no row to show
        # for it. That case is named rather than passed over.
        if fail_open is not None:
            # The row also carries the exception message, which is unbounded
            # run content and never goes in evidence; capability() asserts the
            # evidence is secret-free. A stage and an exception type name are
            # enough to say what stopped observing and where.
            # Read back from the journal, which lives inside the task
            # container at <state_dir>/<task_id>/events.jsonl - a path the
            # benchmarked agent can write. So neither field is trusted as
            # text: each is admitted only if it matches a value this codebase
            # authored, and is otherwise reported as unrecognised. That makes
            # the question of whether the evidence could carry a secret moot
            # rather than argued, and it is the honest reading anyway, since a
            # stage this build does not define is not a stage.
            stage = str(fail_open.get("stage") or "")
            if stage not in _DEGRADE_STAGES:
                stage = "unrecognized_stage"
            # An exception class name is always an identifier, but an
            # identifier is unbounded in length and this field is
            # agent-writable, so the shape check is paired with a length bound.
            # stage is clamped against a closed SET and this against a SHAPE;
            # the bound is what stops the two from being different kinds of
            # promise about what can reach a diagnostics artifact.
            error_type = str(fail_open.get("error_type") or "")
            if not error_type.isidentifier() or len(error_type) > 64:
                error_type = "unrecognized_error"
            rows.append((
                "gt_engine_enabled", CapabilityState.FAILED,
                f"disabled_at_{stage}:{error_type}", True,
            ))
        elif self.disabled and self.mode is GTMode.OFF:
            # Asked to be off, and every other artifact agrees: gt_active is
            # False, the treatment reports INACTIVE. Not a fault, and not
            # required - GT running is not a requirement of a run configured
            # not to run it. capability() defaults required=True, and that
            # default is what made a control arm raise a CI error and, in
            # smoke_stage's prior-gate check, a hard ValueError blaming
            # verification rather than naming configuration. Putting it on the
            # row means no consumer has to re-derive it from the state.
            rows.append((
                "gt_engine_enabled", CapabilityState.UNEXERCISED,
                f"gt_disabled_by_configuration:{self.disabled_stage}", False,
            ))
        elif self.disabled and self.disabled_stage in CONFIGURED_OFF_STAGES:
            # The kill switch fired on a run whose mode is NOT off. Nothing
            # else in the pipeline notices: miniswe_gt_run computes
            # gt_active = not gt_off and gt_mode != "off", deliberately
            # excluding the kill switch ("a kill switch may preserve native
            # execution but cannot relabel ON as OFF"), so the run reports
            # gt_mode enforced, treatment groundtruth, treatment_status ACTIVE,
            # and treatment_not_active never fires. This row is the ONLY
            # artifact that knows GT did nothing all run.
            #
            # So it is required, and it is a failure rather than merely
            # unexercised: the run asserted GT was on and delivered none of it.
            # Grouping it with the OFF arm - which CONFIGURED_OFF_STAGES does,
            # because that constant exists to name the strings __init__ can
            # produce and not to classify whether GT was required - let a run
            # with zero GT behaviour past every gate.
            rows.append((
                "gt_engine_enabled", CapabilityState.FAILED,
                f"gt_disabled_by_kill_switch:{self.mode.value}", True,
            ))
        elif self.disabled:
            rows.append((
                "gt_engine_enabled", CapabilityState.FAILED,
                f"disabled_at_{self.disabled_stage or 'unknown'}:unrecorded", True,
            ))
        else:
            rows.append((
                "gt_engine_enabled", CapabilityState.WORKING,
                "no_fail_open_recorded", True,
            ))
        return rows

    def close(self, terminal: str) -> None:
        self._terminal = terminal
        if self._engine is not None:
            closer = getattr(self._engine, "close_graph_coordinator", None)
            if callable(closer):
                closer()
        if self._engine is not None:
            self._engine.store.append("session_closed", terminal=terminal)
            diagnostics = getattr(self._engine, "diagnostics", None)
            if diagnostics is not None:
                diagnostics.capability(
                    "receipt_writer",
                    CapabilityState.WORKING,
                    "append_only_event_journal_present",
                )
                diagnostics.capability(
                    "capability_negotiation",
                    CapabilityState.WORKING
                    if self.assurance_state is Assurance.FULL
                    else CapabilityState.DEGRADED,
                    "declared_capabilities_checked",
                )
                # Mandatory capability must say at the end of the task whether
                # it worked. Both of these were reportable only as an absence
                # in someone else's receipt: a run with no language servers or
                # no embedder finished looking normal, and the person reading
                # the result had nothing telling them GT ran with less than GT
                # has. Reported here so the end-of-task summary names them.
                for name, state, evidence, required in (
                    self._mandatory_capability_rows()
                ):
                    diagnostics.capability(
                        name, state, evidence, required=required
                    )
                if self.assurance_state is Assurance.DEGRADED:
                    diagnostics.record(
                        DiagnosticEvent.create(
                            code=DiagnosticCode.GT_CAPABILITY_DEGRADED,
                            severity="ERROR",
                            phase="startup",
                            subsystem="session",
                            capability="capability_negotiation",
                            task_id=self._engine.task_id,
                            classification="primary",
                            cause="capability_assurance_degraded",
                            impact="full_assurance_prohibited",
                            recovery="declare_and_verify_required_host_capabilities",
                            retryable=False,
                            event_sequence=int(
                                self._engine.store.receipt()["event_count"]
                            ),
                        )
                    )
                diagnostics.seal()
