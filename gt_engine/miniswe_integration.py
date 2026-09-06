"""Mini-SWE integration boundary with external state and provider receipts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .delivery_budget import (
    MAX_BOUNDARY_CLAIMS,
    TOTAL_DELIVERY_BYTE_LIMIT,
    compact_localization,
    delivery_byte_limit,
)
from .engine_state import EngineState, GraphQuerySnapshot, RuntimeLayout
from .event_journal import GENESIS_HASH, JOURNAL_SCHEMA, event_hash, verify_event_journal
from .graph_coordinator import FrozenBuildInput, GraphBuildArtifact, GraphBuildCoordinator
from .miniswe_controller import GroundtruthController, Predicate, PredicateStatus
from .request_history import store_provider_request
from .run_diagnostics import DiagnosticCode, DiagnosticEvent, DiagnosticJournal
from .task_contract import (
    TaskContract,
    matching_obligation_ids,
    render_obligation_delta,
    render_task_contract,
)
from .verification_contract import (
    certified_path_footprint,
    compile_obligation_predicates,
    conservative_execution_footprint,
    evaluate_passing_observation,
    is_executable_check,
    predicate_receipt_footprint,
)


def _initial_graph_revision(graph_db: str | None) -> str:
    if not graph_db:
        return ""
    graph = Path(graph_db)
    manifest = graph.with_suffix(".manifest.json")
    try:
        row = json.loads(manifest.read_text(encoding="utf-8"))
        revision = str(row.get("graph_revision") or row.get("graph_sha256") or "")
        if revision:
            return revision
    except (OSError, TypeError, ValueError):
        pass
    from .graph_context import graph_revision

    return graph_revision(str(graph))


@dataclass(frozen=True)
class ProviderDelivery:
    request_id: str
    iteration: int
    payload_sha256: str
    phase: str
    suffix: str
    model_visible_sha256: str = ""
    delivery_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingModelDelivery:
    identity: str
    rendered: str
    lane: str
    kind: str
    action_index: int
    iteration: int
    dedup_key: str
    target: str
    semantics: str
    artifact_sha256: str
    ordinal: int


@dataclass(frozen=True)
class PendingExposure:
    rendered: str
    dedup_key: str
    previous_chain_head: str
    next_chain_head: str
    verification_candidate: str = ""


class ProviderModelMismatch(RuntimeError):
    """The provider reported a model outside the requested alias set."""


class ExposureChainConflict(ValueError):
    """A proposed request omits or conflicts with a chained predecessor."""


class ExternalStateStore:
    """Append-only state sink outside the Mini-SWE task workspace."""

    def __init__(self, root: str | Path, task_id: str):
        self.root = Path(root) / task_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "events.jsonl"
        self._lock = threading.Lock()
        self._sequence = 0
        self._head = GENESIS_HASH
        if self.path.exists():
            try:
                verified = verify_event_journal(self.path)
                if verified.valid:
                    self._sequence = verified.event_count
                    self._head = verified.event_head
            except Exception:
                # Legacy/partial state remains untouched. The next write starts
                # a fresh v1 chain; the verifier will correctly flag the mixed
                # journal rather than silently bless it.
                pass

    def append(self, event: str, **payload: Any) -> None:
        with self._lock:
            sequence = self._sequence + 1
            row = {
                "schema": JOURNAL_SCHEMA,
                "sequence": sequence,
                "parent_hash": self._head,
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "event": event,
                **payload,
            }
            row["event_hash"] = event_hash(row)
            encoded = json.dumps(
                row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._sequence = sequence
            self._head = row["event_hash"]

    def receipt(self) -> dict[str, int | str]:
        with self._lock:
            return {"event_count": self._sequence, "event_head": self._head}

    def put_blob(self, namespace: str, digest: str, payload: bytes) -> Path:
        """Persist immutable content-addressed bytes beside the journal."""
        if not namespace.replace("_", "").replace("-", "").isalnum():
            raise ValueError("invalid blob namespace")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid sha256 digest")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("blob digest does not match payload")
        directory = self.root / namespace
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.json"
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"content-address collision at {target}")
            return target
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix=f".{digest}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    def blob_exists(self, namespace: str, digest: str) -> bool:
        """Return whether the exact immutable CAS object already exists."""
        if not namespace.replace("_", "").replace("-", "").isalnum():
            raise ValueError("invalid blob namespace")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid sha256 digest")
        target = self.root / namespace / f"{digest}.json"
        return target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest


class MiniSweAdapter(GroundtruthController):
    """Controller plus external state/provider-bound request witness.

    The adapter does not alter Mini-SWE's messages.  A caller supplies the final
    normalized payload, receives a request-bound receipt, then performs the actual
    provider call through Mini-SWE's native model object.
    """

    def __init__(self, *, task_id: str, state_dir: str | Path,
                 predicates: Iterable[Predicate], repeat_budget: int = 2,
                 contract: TaskContract | None = None,
                 repo_root: str | Path = "", graph_db: str | None = None,
                 issue_text: str = "", requested_model: str = "",
                 resolved_model: str = "", fallback_model: str = "",
                 layout: RuntimeLayout | None = None):
        super().__init__(predicates, repeat_budget=repeat_budget)
        self.task_id = task_id
        self.contract = contract
        self._compiled_predicates = (
            compile_obligation_predicates(contract) if contract is not None else {}
        )
        self._predicate_by_obligation = (
            {pc.obligation_id: pc.predicate_id for pc in self._compiled_predicates.values()}
            if contract is not None else {}
        )
        self._obligation_by_predicate = {
            value: key for key, value in self._predicate_by_obligation.items()
        }
        layout = layout or RuntimeLayout.resolve(
            workspace=repo_root or Path.cwd(), state_root=state_dir, task_id=task_id,
        )
        self.store = ExternalStateStore(layout.state_root, task_id)
        self.diagnostics = DiagnosticJournal(self.store.root, task_id=task_id)
        self.iteration = 0
        self.deliveries: list[ProviderDelivery] = []
        self._last_payload_hash = ""
        self._last_control_state: tuple[str, int, tuple[str, ...]] | None = None
        self.repo_root = str(repo_root or "")
        self.graph_db = graph_db or None
        self.engine_state = EngineState(
            layout=layout,
            graph_path=str(self.graph_db or ""),
            graph_revision=_initial_graph_revision(self.graph_db),
        )
        self.issue_text = issue_text or ""
        self.requested_model = requested_model or ""
        self.resolved_model = resolved_model or requested_model or ""
        self.fallback_model = fallback_model or ""
        self.provider_reported_model = ""
        self._episode = None
        self._gateway_state = None
        self._dedup_chain: set[str] = set()
        self._chain_head = ""
        self._latest_delivery: ProviderDelivery | None = None
        self._last_graph_publication: tuple[str, str] | None = None
        self._terminal_request_ids: set[str] = set()
        # F10: a GT-internal bootstrap turn is a real provider call the agent's
        # own n_calls never sees, because it bypasses agent.query(). Counting it
        # here keeps "api_calls = agent turns" intact while letting receipt
        # reconciliation compare like with like at the transport boundary.
        self._select_catalog_bootstrap_calls = 0
        self._contract_shipped = False
        self._last_delta_signature: tuple[tuple[str, str], ...] = ()
        self._prepared_contract_delta: tuple[str, tuple[tuple[str, str], ...]] | None = None
        self._edited_files: set[str] = set()
        self._failure_first_epoch: dict[str, int] = {}
        self._failure_recurrences: dict[str, int] = {}
        self._recovery_delivered = 0
        self._model_visible_delivery_count = 0
        self._model_visible_delivery_bytes = 0
        self._admission_iteration: int | None = None
        self._boundary_delivery_count = 0
        self._boundary_delivery_bytes = 0
        self._localization_cache_key: tuple | None = None
        self._localization_candidate = ""
        self._localization_metadata: dict[str, str] = {}
        self._localization_chain: set[str] = set()
        self._localization_head = ""
        self._pending_verification_candidate = ""
        self._pending_verification_metadata: dict[str, str] = {}
        self._model_visible_delivery_identities: set[str] = set()
        self._accepted_sealed_delivery_count = 0
        self._cochange_delivery_count = 0
        self._pending_delivery_metadata: dict[str, str] = {}
        # Exact admitted bytes awaiting the immediate provider-final request.
        # This is deliberately per-request transient state, not carried chat
        # history: attribution asks which decision boundary first exposed a
        # delivery, not every later request that still contains it.
        self._pending_provider_deliveries: list[PendingModelDelivery] = []
        self._pending_exposures: dict[str, PendingExposure] = {}
        self.pending_transient = ""
        self._pending_recovery: tuple[str, int] | None = None
        self.pending_directives: list[str] = []
        self._refusal_count = 0
        self._last_refusal_signature: tuple[tuple[str, str], ...] = ()
        self._usage = {
            "prompt_tokens": 0,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "completion_tokens": 0,
        }
        # Global monotonic action identity (the receipt ladder + audit join on
        # THIS, not the per-message action index - they were inconsistent).
        self.global_action = 0
        # Typed observations are joined to the *next* provider payload only
        # after Mini-SWE has rendered the exact tool-result message bytes.
        self._pending_typed_observations: list[dict[str, Any]] = []
        # Revision/freshness authority for harness-observed actions. The graph
        # is fresh at task start only when an index actually exists; any edit
        # makes it stale until a successful deterministic rebuild.
        self.repository_revision = ""
        self.graph_stale_since_revision = ""
        self._latest_transaction_sha256 = ""
        self.terminal_evidence_session: Any | None = None
        self.provider_boundary: Any | None = None
        self._closed_blockers: Any | None = None
        self._submit_invalidation_keys: dict[str, str] = {}
        self._latest_workspace_snapshot: Any | None = None
        self._graph_coordinator: GraphBuildCoordinator | None = None
        self._lsp_scheduler: Any | None = None
        self._lsp_requests: dict[str, Any] = {}
        self.store.append(
            "runtime_layout", layout_schema="gt.runtime_layout.v1",
            evidence_root=str(layout.evidence_root.resolve()),
        )

    @property
    def graph_fresh(self) -> bool:
        return self.engine_state.graph_current

    @graph_fresh.setter
    def graph_fresh(self, value: bool) -> None:
        if value and self.graph_db:
            self.engine_state.publish_graph(
                graph_path=str(self.graph_db),
                graph_revision=(self.engine_state.graph_revision
                                or hashlib.sha256(str(self.graph_db).encode()).hexdigest()),
                source_revision=self.engine_state.source_revision,
            )
        elif not value:
            self.engine_state.mark_graph_failed()

    def graph_query_snapshot(self) -> GraphQuerySnapshot:
        """The only supported graph identity consumed by native features."""
        return self.engine_state.query_snapshot()

    def _record_state(self) -> None:
        self.store.append(
            "state",
            phase=self.phase,
            epoch=self.workspace_epoch,
            unmet=list(self.unmet_predicates),
            iteration=self.iteration,
        )

    def start_task(self) -> None:
        super().start_task()
        self._bind_terminal_evidence()
        self._record_state()

    def _bind_terminal_evidence(self) -> None:
        """Bind GroundTruth's terminal authority to the exact UTF-8 task bytes."""
        try:
            from groundtruth.runtime.terminal_evidence import (
                ClosedBlockerRegistry,
                bind_episode_terminal_evidence,
            )

            episode = self.gateway_state().episode
            task_bytes = self.issue_text.encode("utf-8", "surrogatepass")
            task_revision = hashlib.sha256(task_bytes).hexdigest()
            self.terminal_evidence_session = bind_episode_terminal_evidence(
                episode,
                issue_text=self.issue_text,
                task_revision=task_revision,
            )
            self._closed_blockers = ClosedBlockerRegistry(enforce=False)
            self.store.append(
                "terminal_evidence_bound",
                task_bytes_sha256=task_revision,
                task_bytes=len(task_bytes),
            )
        except Exception as exc:  # noqa: BLE001 - terminal memory is fail-open
            self.store.append(
                "terminal_evidence_unavailable", error_type=type(exc).__name__
            )

    def attach_provider_boundary(self, model: Any, agent: Any) -> Any | None:
        """Install the canonical boundary once; absence preserves native Mini-SWE."""
        if self.provider_boundary is not None:
            return self.provider_boundary
        try:
            from groundtruth.runtime.miniswe_provider_boundary import (
                MiniSweProviderBoundary,
            )

            self.provider_boundary = MiniSweProviderBoundary(
                model=model,
                agent=agent,
                fault_handler=lambda stage, exc: self.store.append(
                    "provider_boundary_fault",
                    stage=stage,
                    error_type=type(exc).__name__,
                ),
            )
            self.store.append("provider_boundary_attached")
        except Exception as exc:  # noqa: BLE001 - canonical seam is fail-open
            self.store.append(
                "provider_boundary_unavailable", error_type=type(exc).__name__
            )
        return self.provider_boundary

    def record_episode_failure(
        self,
        *,
        command: str,
        output: str,
        returncode: int,
        pre_state_revision: str,
    ) -> str:
        """Record one exact failed-action identity in the bound terminal session."""
        if self._episode is None or self.terminal_evidence_session is None:
            return ""
        try:
            from groundtruth.runtime.terminal_evidence import (
                EvidenceStatus,
                FailureIdentity,
                record_episode_failure,
            )

            identity = FailureIdentity.build(
                action=(command,),
                cwd=(self.repo_root or os.getcwd()),
                environment={},
                pre_state_revision=pre_state_revision or self.repository_revision,
                exit_code=returncode,
                signal=None,
                diagnostics=output,
            )
            record_episode_failure(
                self._episode,
                identity,
                remedy="none_recorded",
                outcome="failed",
            )
            blocker_id = ""
            candidate_blocker_id = f"failed-action:{identity.sha256}"
            invalidation_key = hashlib.sha256(
                (identity.sha256 + "|any_repository_edit").encode("utf-8")
            ).hexdigest()
            if (
                self._closed_blockers is not None
                and self.repository_revision
                and is_executable_check(command)
            ):
                self._closed_blockers.register(
                    blocker_id=candidate_blocker_id,
                    producer="miniswe.executed_action",
                    witness=identity.diagnostic_sha256,
                    scope=command,
                    creating_revision=self.repository_revision,
                    current_revision=self.repository_revision,
                    invalidation_rule="invalidate_on_repository_revision_change",
                    invalidation_key=invalidation_key,
                    status=EvidenceStatus.EXACT,
                    scope_closed=True,
                )
                blocker_id = candidate_blocker_id
                self._submit_invalidation_keys[blocker_id] = invalidation_key
            self.store.append(
                "episode_failure_recorded",
                failure_identity_sha256=identity.sha256,
                pre_state_revision=identity.pre_state_revision,
                diagnostic_sha256=identity.diagnostic_sha256,
                blocker_id=blocker_id,
            )
            return identity.sha256
        except Exception as exc:  # noqa: BLE001 - failure memory is subordinate
            self.store.append(
                "episode_failure_unavailable", error_type=type(exc).__name__
            )
            return ""

    def authorize_submit_suppression(self, command: str) -> Any | None:
        """Return a durable canonical zero-delivery receipt or fail open."""
        if self.provider_boundary is None or self._closed_blockers is None:
            return None
        if os.environ.get("GT_SUBMIT_SUPPRESSION_ENFORCE", "").strip() != "1":
            return None
        try:
            self._closed_blockers.enforce = True
            receipt = self.provider_boundary.authorize_submit_suppression(
                registry=self._closed_blockers,
                current_revision=self.repository_revision,
                current_invalidation_keys=dict(self._submit_invalidation_keys),
                action_bytes=command.encode("utf-8", "surrogatepass"),
                provider_payload_bytes=b"",
            )
        except Exception:  # noqa: BLE001 - suppression must fail open
            return None
        if receipt is None:
            return None
        self.store.append(
            "submit_suppression_zero_delivery",
            receipt_schema=receipt.schema,
            repository_revision=receipt.repository_revision,
            action_sha256=receipt.action_sha256,
            provider_payload_sha256=receipt.provider_payload_sha256,
            blocker_ids=list(receipt.blocker_ids),
            provider_dispatched=receipt.provider_dispatched,
            chars_delivered=receipt.chars_delivered,
        )
        return receipt

    def begin_implement(self) -> None:
        super().begin_implement()
        self._record_state()

    def begin_verify(self) -> None:
        super().begin_verify()
        self._record_state()

    def begin_submit(self) -> None:
        super().begin_submit()
        self._record_state()

    def note_edit(self, paths: Iterable[str]) -> None:
        normalized_paths = tuple(str(p) for p in paths)
        affected = set(self._affected_predicate_ids(normalized_paths))
        active_red = {
            predicate_id
            for predicate_id, status in self._status.items()
            if status is PredicateStatus.RED
        }
        affected.update(active_red)
        super().note_edit(normalized_paths, invalidate=affected)
        if self._pending_recovery is not None:
            self.store.append("recovery_invalidated", epoch=self.workspace_epoch)
            self._pending_recovery = None
            self.pending_transient = ""
            self._pending_provider_deliveries = [
                item for item in self._pending_provider_deliveries if item.kind != "recovery"
            ]
        if active_red:
            self.store.append(
                "red_invalidated_by_edit",
                predicate_ids=sorted(active_red),
                paths=list(normalized_paths),
                epoch=self.workspace_epoch,
            )
        self._edited_files.update(normalized_paths)
        if normalized_paths and self.graph_db:
            if not self.engine_state.query_snapshot().overlay:
                self.engine_state.mark_paths_dirty(
                    normalized_paths,
                    revision=self.repository_revision or f"epoch:{self.workspace_epoch}",
                )
            # GatewayState captures graph_db at construction. Drop the cached
            # wrapper immediately so automatic evidence cannot keep reading a
            # pre-edit graph while the adapter correctly reports it stale.
            # The persistent EpisodeState is retained and reattached lazily.
            self._gateway_state = None
            self.graph_stale_since_revision = self.repository_revision
            self.store.append(
                "graph_invalidated",
                paths=list(normalized_paths),
                repository_revision=self.repository_revision,
                graph_db_sha256=hashlib.sha256(
                    str(self.graph_db).encode("utf-8")
                ).hexdigest(),
            )
        self._record_state()

    def record_repository_snapshot(self, snapshot: Any, *, boundary: str) -> None:
        """Persist a content-addressed revision witness outside the workspace."""
        encoded = snapshot.canonical_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("repository_snapshots", digest, encoded)
        self.repository_revision = str(snapshot.revision)
        self._latest_workspace_snapshot = snapshot
        self.engine_state.bind_initial_source(self.repository_revision)
        self.store.append(
            "repository_snapshot",
            boundary=boundary,
            repository_revision=self.repository_revision,
            snapshot_sha256=digest,
            complete=bool(snapshot.complete),
            omissions=list(snapshot.omissions),
            file_count=len(snapshot.files),
        )
        self._record_graph_publication()

    def _record_graph_publication(self) -> None:
        if not self.engine_state.graph_current:
            return
        graph = Path(self.engine_state.graph_path)
        manifest = graph.with_suffix(".manifest.json")
        if not manifest.is_file():
            return
        manifest_bytes = manifest.read_bytes()
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        identity = (manifest_digest, self.engine_state.source_revision)
        if identity == self._last_graph_publication:
            return
        payload = json.loads(manifest_bytes)
        self.store.append(
            "graph_publication", artifact_sha256=manifest_digest,
            graph_sha256=payload["graph_sha256"],
            repository_revision=self.engine_state.source_revision,
        )
        self._last_graph_publication = identity

    def record_edit_transaction(self, transaction: Any) -> None:
        encoded = transaction.canonical_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("edit_transactions", digest, encoded)
        self.repository_revision = str(transaction.post_revision)
        self.engine_state.apply_transaction(transaction)
        self._latest_transaction_sha256 = str(transaction.transaction_sha256)
        self.store.append(
            "edit_transaction",
            action_index=int(transaction.action_id),
            transaction_sha256=str(transaction.transaction_sha256),
            artifact_sha256=digest,
            pre_revision=str(transaction.pre_revision),
            post_revision=str(transaction.post_revision),
            changed_paths=list(transaction.changed_paths),
            complete=bool(transaction.complete),
            omissions=list(transaction.omissions),
        )

    def record_transaction_artifacts(self, artifacts: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            dict(artifacts), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("transaction_artifacts", digest, encoded)
        self.store.append(
            "transaction_artifacts",
            artifact_sha256=digest,
            artifact_blob=f"transaction_artifacts/{digest}.json",
            transaction_sha256=str(artifacts.get("transaction_sha256") or ""),
            syntax_count=len(artifacts.get("syntax") or ()),
            patch_count=len(artifacts.get("patches") or ()),
            caller_count=len(artifacts.get("callers") or ()),
            caller_coverage=str(artifacts.get("caller_coverage") or "unavailable"),
        )
        return digest

    def prepare_verification_candidate(
        self, transaction: Any, graph_snapshot: GraphQuerySnapshot
    ) -> str:
        """Prepare revision-bound check advice from the usable pre-edit graph.

        The planner is pure and the result remains advisory.  The pre-edit
        graph may identify changed entities and covering tests, but it cannot
        establish facts about edited bytes or execute a check on Mini-SWE's
        behalf.
        """
        self._pending_verification_candidate = ""
        self._pending_verification_metadata = {}
        if (
            not self.repo_root
            or not graph_snapshot.graph_current
            or not graph_snapshot.graph_path
        ):
            return ""
        paths = tuple(sorted({str(path) for path in transaction.changed_paths if path}))
        if not paths:
            return ""
        try:
            placeholders = ",".join("?" for _ in paths)
            uri = Path(graph_snapshot.graph_path).resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                entities = tuple(
                    row[0]
                    for row in connection.execute(
                        f"SELECT DISTINCT stable_id FROM resolution_symbols "
                        f"WHERE path IN ({placeholders}) ORDER BY stable_id",
                        paths,
                    )
                    if row[0]
                )
            if not entities:
                return ""
            from groundtruth.runtime.verification_plan import build_verification_plan

            obligations = tuple(
                sorted(
                    obligation_id
                    for obligation_id, predicate_id in self._predicate_by_obligation.items()
                    if predicate_id in self.unmet_predicates
                )
            )
            plan = build_verification_plan(
                graph_snapshot.graph_path,
                self.repo_root,
                entities,
                obligations,
                patch_revision=str(transaction.post_revision),
                graph_revision=graph_snapshot.graph_revision,
            )
            encoded = plan.canonical_json().encode("utf-8")
            digest = hashlib.sha256(encoded).hexdigest()
            self.store.put_blob("verification_plans", digest, encoded)
            lines: list[str] = []
            for check in plan.checks:
                command = (
                    shlex.join(check.command)
                    if check.command
                    else "edit_check " + " ".join(check.targets)
                ).strip()
                if not command:
                    continue
                line = (
                    f"{check.kind}: {command} "
                    f"basis={check.selection_basis} cost={check.expected_cost}"
                )
                if sum(len(item.encode("utf-8")) + 1 for item in (*lines, line)) > 960:
                    break
                lines.append(line)
                if len(lines) == 3:
                    break
            if not lines:
                return ""
            rendered = "[GT_EVIDENCE:verification_plan]\n" + "\n".join(lines)
            dedup_key = f"verification:{transaction.transaction_sha256}:{digest}"
            self._pending_verification_candidate = rendered
            self._pending_verification_metadata = {
                "kind": "verification_plan",
                "dedup_key": dedup_key,
                "target": paths[0],
                "semantics": "advisory_pre_edit_dependency_graph",
                "artifact_sha256": digest,
            }
            self.store.append(
                "verification_plan_prepared",
                artifact_sha256=digest,
                artifact_blob=f"verification_plans/{digest}.json",
                transaction_sha256=str(transaction.transaction_sha256),
                source_revision=str(transaction.post_revision),
                dependency_source_revision=graph_snapshot.source_revision,
                graph_revision=graph_snapshot.graph_revision,
                changed_paths=list(paths),
                changed_entities=list(entities),
                check_count=len(plan.checks),
                semantics="advisory_pre_edit_dependency_graph",
            )
            return rendered
        except Exception as exc:  # noqa: BLE001 - selection is correct-or-quiet
            self.store.append(
                "verification_plan_unavailable",
                transaction_sha256=str(transaction.transaction_sha256),
                error_type=type(exc).__name__,
            )
            return ""

    def verification_candidate(self) -> tuple[str, dict[str, str]]:
        return (
            self._pending_verification_candidate,
            dict(self._pending_verification_metadata),
        )

    def consume_verification_candidate(self) -> tuple[str, dict[str, str]]:
        candidate = self.verification_candidate()
        self._pending_verification_candidate = ""
        self._pending_verification_metadata = {}
        return candidate

    def record_execution_evidence(self, artifact: Any) -> str:
        """Store exact raw diagnostics and return a structured augmentation."""
        raw_digest = artifact.raw_output_sha256
        raw_blob = f"raw_execution_output/{raw_digest}.json"
        captured_path = str(getattr(artifact, "output_artifact_path", "") or "")
        if captured_path:
            path = Path(captured_path).resolve()
            try:
                relative = path.relative_to(self.store.root.resolve())
            except ValueError:
                # Legacy environments may have their own external capture root.
                # Production uses this task's store for capture and analysis.
                relative = None
            if relative is not None:
                from .output_evidence import EvidenceStore

                page = EvidenceStore(path.parent).read(raw_digest, 0, 1)
                expected_length = (len(artifact.raw_output) if artifact.raw_output is not None
                                   else artifact.stored_output_length)
                if path.name != raw_digest or page["total_length"] != expected_length:
                    raise ValueError("execution_output_identity_mismatch")
                raw_blob = relative.as_posix()
        if raw_blob.startswith("raw_execution_output/"):
            if artifact.raw_output is not None:
                self.store.put_blob("raw_execution_output", raw_digest, artifact.raw_output)
            elif captured_path:
                from .output_evidence import EvidenceStore

                target_store = EvidenceStore(self.store.root / "output_evidence")
                with tempfile.NamedTemporaryFile(dir=target_store.root, delete=False) as copied:
                    copied_path = Path(copied.name)
                shutil.copyfile(captured_path, copied_path)
                reference = target_store.publish(copied_path)
                if reference["sha256"] != raw_digest:
                    raise ValueError("execution_output_identity_mismatch")
                raw_blob = f"output_evidence/{raw_digest}"
            else:
                raise ValueError("execution_output_missing")
        encoded = artifact.canonical_bytes()
        artifact_digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("execution_evidence", artifact_digest, encoded)
        payload = json.loads(encoded)
        self.store.append(
            "execution_evidence",
            artifact_sha256=artifact_digest,
            raw_blob=raw_blob,
            **payload,
        )
        return "[GT_EXECUTION_EVIDENCE]\n" + json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def refresh_graph(self, *, phase: str = "graph_query") -> bool:
        """Poll or schedule a frozen-input rebuild without blocking Mini-SWE."""
        if self._graph_coordinator is not None:
            self._graph_coordinator.poll()
            if self.engine_state.graph_current:
                self.graph_db = self.engine_state.graph_path
                self._gateway_state = None
                self.graph_stale_since_revision = ""
                self._record_graph_publication()
                return True
        if not self.repo_root or self._latest_workspace_snapshot is None:
            self._record_graph_refresh_failure("frozen_source_unavailable", phase=phase)
            return False
        try:
            request = self._frozen_graph_input(self._latest_workspace_snapshot)
            from .indexer import SOURCE_EXTS

            if not any(Path(path).suffix.lower() in SOURCE_EXTS for path, _ in request.files):
                return False
            if self._graph_coordinator is None:
                self._graph_coordinator = GraphBuildCoordinator(
                    self.engine_state, self._build_frozen_graph,
                    enrichment_factory=self._schedule_lsp_candidate,
                    candidate_certifier=self._certify_lsp_candidate,
                    enrichment_observer=self._record_lsp_terminal,
                )
            if self.engine_state.graph_current:
                self._graph_coordinator.consider_enrichment(request, GraphBuildArtifact(
                    True, self.engine_state.graph_path, self.engine_state.graph_revision,
                ))
                return True
            disposition = self._graph_coordinator.schedule(request)
        except Exception as exc:  # noqa: BLE001 - freshness is fail-open
            self.store.append(
                "graph_refresh_failed", error_type=type(exc).__name__
            )
            self._record_graph_refresh_failure(type(exc).__name__, phase=phase)
            return False
        self.store.append(
            "graph_refresh_scheduled",
            disposition=disposition,
            repository_revision=self.repository_revision,
            dirty_paths=list(request.dirty_paths),
        )
        return False

    def _frozen_graph_input(self, snapshot: Any) -> FrozenBuildInput:
        from .indexer import is_producer_input

        files: list[tuple[str, bytes]] = []
        missing: list[str] = []
        for item in snapshot.files:
            if not is_producer_input(item.path):
                continue
            if item.kind != "file" or item.captured is None:
                missing.append(str(item.path))
            else:
                files.append((str(item.path), bytes(item.captured)))
        source_omissions = []
        for omission in snapshot.omissions:
            kind, separator, value = str(omission).partition(":")
            if kind == "unreadable" and separator:
                if is_producer_input(value):
                    source_omissions.append(str(omission))
            else:
                # Unknown omission types remain conservative until their
                # relationship to source completeness is explicitly known.
                source_omissions.append(str(omission))
        if missing or source_omissions:
            raise ValueError("frozen_source_incomplete")
        return FrozenBuildInput(
            str(snapshot.revision),
            self.engine_state.query_snapshot().masked_paths,
            tuple(sorted(files)),
            snapshot.history,
        )

    def _build_frozen_graph(self, request: FrozenBuildInput) -> GraphBuildArtifact:
        from .indexer import _freeze_history, ensure_index_with_receipt

        with tempfile.TemporaryDirectory(prefix="gt-frozen-source-") as temporary:
            root = Path(temporary)
            _freeze_history(Path(self.repo_root), root, request.history)
            for relative, payload in request.files:
                target = (root / relative).resolve()
                if root.resolve() not in target.parents:
                    return GraphBuildArtifact(False, "", "", "unsafe_source_path")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            receipt = ensure_index_with_receipt(
                root, layout=self.engine_state.layout,
                source_revision=request.source_revision,
            )
        return GraphBuildArtifact(
            bool(receipt.success and receipt.graph_db), str(receipt.graph_db or ""),
            str(receipt.graph_revision or ""), receipt.error_type or "",
        )

    def _schedule_lsp_candidate(self, request: FrozenBuildInput, base: GraphBuildArtifact) -> Any:
        from groundtruth.lsp.background_promotion import (
            LSPPromotionRequest,
            LSPPromotionScheduler,
            repository_snapshot_sha256,
        )

        from .indexer import _certify_published_graph, source_manifest_digest

        base_path = Path(base.graph_path).resolve()
        valid, reason = _certify_published_graph(
            base_path, base_path.with_suffix(".manifest.json"),
            expected_root=Path(self.repo_root),
        )
        if not valid:
            raise ValueError(f"lsp_base_uncertified:{reason}")
        layout = self.engine_state.layout
        namespace = layout.graph_root / "enrichments"
        namespace.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="lsp-", dir=namespace))
        source = directory / "source"
        source.mkdir()
        for relative, payload in request.files:
            target = (source / relative).resolve()
            if source.resolve() not in target.parents:
                raise ValueError("unsafe_lsp_source_path")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        base_manifest = json.loads(base_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        if source_manifest_digest(source) != base_manifest.get("source_manifest_sha256"):
            raise ValueError("lsp_source_input_mismatch")
        if self._lsp_scheduler is None:
            self._lsp_scheduler = LSPPromotionScheduler()
        promotion_request = LSPPromotionRequest(
            source_revision=request.source_revision,
            graph_revision=base.graph_revision,
            graph_path=str(base_path),
            graph_sha256=hashlib.sha256(base_path.read_bytes()).hexdigest(),
            repository_root=str(source),
            repository_snapshot_sha256=repository_snapshot_sha256(source),
            candidate_path=str(directory / "graph.db"),
        )
        handle = self._lsp_scheduler.schedule(promotion_request)
        self._lsp_requests[handle.task_id] = promotion_request
        self.store.append(
            "lsp_promotion_scheduled", task_id=handle.task_id,
            source_revision=request.source_revision, graph_revision=base.graph_revision,
            repository_snapshot_sha256=promotion_request.repository_snapshot_sha256,
        )
        return handle

    def _certify_lsp_candidate(self, request: FrozenBuildInput, base: GraphBuildArtifact,
                               terminal: Mapping[str, Any]) -> GraphBuildArtifact:
        from .indexer import certify_lsp_candidate

        scheduled = self._lsp_requests.get(str(terminal.get("task_id") or ""))
        if (scheduled is None or scheduled.source_revision != request.source_revision
                or scheduled.graph_revision != base.graph_revision
                or str(terminal.get("candidate_path") or "") != scheduled.candidate_path):
            return GraphBuildArtifact(False, "", "", "lsp_scheduled_identity_mismatch")
        return certify_lsp_candidate(
            base.graph_path, str(terminal.get("candidate_path") or ""), terminal,
            expected_source_revision=request.source_revision,
            expected_repository_snapshot_sha256=scheduled.repository_snapshot_sha256,
            expected_repository_root_sha256=hashlib.sha256(
                str(Path(scheduled.repository_root).resolve()).encode("utf-8", "surrogatepass")
            ).hexdigest(),
            layout=self.engine_state.layout,
            expected_root_sha256=hashlib.sha256(
                str(Path(self.repo_root).resolve()).encode("utf-8", "surrogatepass")
            ).hexdigest(),
            expected_task_id=os.environ.get("GT_TASK_ID", ""),
            expected_product_source_sha=os.environ.get("GT_PRODUCT_SOURCE_SHA", ""),
        )

    def _record_lsp_terminal(self, request: FrozenBuildInput, base: GraphBuildArtifact,
                             terminal: Mapping[str, Any], disposition: str) -> None:
        encoded = json.dumps(dict(terminal), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("lsp_receipts", digest, encoded)
        self.store.append(
            "lsp_promotion_terminal", artifact_sha256=digest,
            artifact_blob=f"lsp_receipts/{digest}.json", disposition=disposition,
            source_revision=request.source_revision, input_graph_revision=base.graph_revision,
            status=terminal.get("status"),
        )
        self._lsp_requests.pop(str(terminal.get("task_id") or ""), None)

    def close_graph_coordinator(self) -> None:
        if self._graph_coordinator is not None:
            self._graph_coordinator.close(wait=False)
        if self._lsp_scheduler is not None:
            self._lsp_scheduler.close(wait=False)

    def _record_graph_refresh_failure(self, cause: str, *, phase: str) -> None:
        self.engine_state.mark_graph_failed()
        self.diagnostics.record(
            DiagnosticEvent.create(
                code=DiagnosticCode.GT_GRAPH_REFRESH_FAILED,
                severity="ERROR",
                phase=phase,
                subsystem="graph",
                capability="graph_freshness",
                task_id=self.task_id,
                classification="primary",
                cause=cause,
                impact="verified_claims_prohibited",
                recovery="rebuild_graph_for_current_workspace_revision",
                retryable=False,
                event_sequence=int(self.store.receipt()["event_count"]),
                identities={"repository": self.repository_revision},
            )
        )

    def _affected_predicate_ids(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        """D3-V: only predicates whose file scope touches the edited paths reset.

        A proven obligation on an unrelated file survives the edit; otherwise
        every rewrite of one file would wipe the whole contract to UNKNOWN and
        force the model to re-prove unrelated obligations (measured loop:
        modernize rewrote analyze_climate_modern.py 4x).
        """
        if self.contract is None or not paths:
            return ()
        edited = {os.path.normpath(p).lstrip(".\\/") for p in paths}
        affected: list[str] = []
        for obligation_id, predicate in self._compiled_predicates.items():
            scope = tuple(os.path.normpath(s).lstrip(".\\/") for s in predicate.scope)
            if not scope:
                # Global behavior/numeric obligations are re-checked at submit
                # (D3-F live re-verify) rather than wiped here.
                continue
            if any(
                path == base or path.startswith(base + "/") or base.startswith(path + "/")
                for path in edited
                for base in scope
            ):
                predicate_id = self._predicate_by_obligation.get(obligation_id)
                if predicate_id:
                    affected.append(predicate_id)
        return tuple(sorted(set(affected)))

    def bind_provider_payload(self, payload: Mapping[str, Any]) -> ProviderDelivery:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("provider payload requires messages")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        model_visible = json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        model_visible_digest = hashlib.sha256(model_visible).hexdigest()
        def contains_text(value: Any, needle: str) -> bool:
            if isinstance(value, str):
                return needle in value
            if isinstance(value, Mapping):
                return any(contains_text(item, needle) for item in value.values())
            if isinstance(value, (list, tuple)):
                return any(contains_text(item, needle) for item in value)
            return False

        pending = tuple(self._pending_provider_deliveries)
        matched = tuple(
            item.identity for item in pending
            if contains_text(messages, item.rendered)
        )
        unmatched = tuple(
            item.identity for item in pending
            if not contains_text(messages, item.rendered)
        )
        # Validate the complete request before committing even a valid prefix.
        # An omitted predecessor cannot be treated as exposed merely because a
        # later proposal was prepared on top of it.
        proposed_head = self._chain_head
        exposures_by_delivery: dict[str, list[PendingExposure]] = {}
        for item in pending:
            if item.identity not in matched:
                continue
            exposures = [exposure for exposure in self._pending_exposures.values()
                         if exposure.dedup_key == item.dedup_key
                         and exposure.rendered in item.rendered]
            exposures_by_delivery[item.identity] = exposures
            for exposure in exposures:
                if not exposure.next_chain_head:
                    continue
                if proposed_head != exposure.previous_chain_head:
                    self.store.append(
                        "exposure_chain_conflict", delivery_identity=item.identity,
                        payload_sha256=digest, disposition="request_refused",
                        expected_head=proposed_head,
                        supplied_head=exposure.previous_chain_head,
                    )
                    raise ExposureChainConflict("provider request exposure chain conflict")
                proposed_head = exposure.next_chain_head
        request_sha256, request_manifest, request_manifest_sha256, storage = (
            store_provider_request(self.store, payload)
        )
        if request_sha256 != digest:
            raise RuntimeError("provider request CAS identity mismatch")
        self.iteration += 1
        request_id = f"{self.task_id}-{self.iteration}-{digest[:16]}"
        suffix = self.provider_suffix()
        delivery = ProviderDelivery(
            request_id,
            self.iteration,
            digest,
            self.phase,
            suffix,
            model_visible_digest,
            matched,
        )
        self.deliveries.append(delivery)
        self._last_payload_hash = digest
        self._latest_delivery = delivery
        for item in pending:
            if item.identity not in matched:
                continue
            event = (
                "context_addition_delivery" if item.lane == "prompt"
                else "evidence_delivery"
            )
            self.store.append(
                event, lane=item.lane, kind=item.kind,
                action_index=item.action_index, iteration=item.iteration,
                evidence_type=item.kind, dedup_key=item.dedup_key,
                target=item.target, rendered_bytes=len(item.rendered.encode("utf-8")),
                payload_sha256=item.identity, delivery_identity=item.identity,
                delivery_blob=f"deliveries/{item.identity}.json",
                semantics=item.semantics, artifact_sha256=item.artifact_sha256,
                delivery_ordinal=item.ordinal, request_id=request_id,
            )
            self.record_delivery_receipt(
                evidence_type=item.kind, dedup_key=item.dedup_key,
                target=item.target, payload_hash=item.identity,
                action_index=item.action_index, iteration=item.iteration,
            )
            self._model_visible_delivery_count += 1
            self._model_visible_delivery_bytes += len(item.rendered.encode("utf-8"))
            self._model_visible_delivery_identities.add(item.identity)
            if (item.kind == "recovery" and self._pending_recovery is not None
                    and item.rendered == self.pending_transient):
                fingerprint, epoch = self._pending_recovery
                self._failure_first_epoch[fingerprint] = epoch
                self._recovery_delivered += 1
                self.store.append("recovery_steer", fingerprint=fingerprint, epoch=epoch,
                                  delivered=self._recovery_delivered, request_id=request_id,
                                  delivery_identity=item.identity)
                self._pending_recovery = None
                self.pending_transient = ""
            for exposure in exposures_by_delivery.get(item.identity, ()):
                if exposure.next_chain_head:
                    self._chain_head = exposure.next_chain_head
                if exposure.dedup_key:
                    self._dedup_chain.add(exposure.dedup_key)
                if (exposure.verification_candidate
                        and self.verification_candidate()[0] == exposure.verification_candidate):
                    self.consume_verification_candidate()
            if item.lane == "sealed":
                self._accepted_sealed_delivery_count += 1
            if item.kind == "cochange_partner":
                self._cochange_delivery_count += 1
        self.store.append(
            "provider_delivery",
            request_id=request_id,
            iteration=self.iteration,
            payload_sha256=digest,
            phase=self.phase,
            suffix=suffix,
            model_visible_sha256=model_visible_digest,
            requested_model=self.requested_model,
            resolved_model=self.resolved_model,
            request_manifest=request_manifest,
            request_manifest_sha256=request_manifest_sha256,
            request_storage="message_cas",
            **storage,
            delivery_ids=list(matched),
            matches=[
                {"delivery_id": identity, "rendered_sha256": identity}
                for identity in matched
            ],
            unmatched_delivery_ids=list(unmatched),
        )
        self._pending_provider_deliveries.clear()
        self._pending_exposures.clear()
        for typed in self._pending_typed_observations:
            self.store.append(
                "typed_observation_provider_join",
                request_id=request_id,
                provider_payload_sha256=digest,
                model_visible_sha256=model_visible_digest,
                **typed,
            )
        self._pending_typed_observations.clear()
        return delivery

    def discard_pending_provider_deliveries(self, *, reason: str) -> None:
        """Roll back prepared delivery accounting after final-request refusal."""
        pending = tuple(self._pending_provider_deliveries)
        if pending:
            self.store.append(
                "prepared_deliveries_discarded", reason=reason,
                delivery_ids=[item.identity for item in pending],
            )
        self._pending_provider_deliveries.clear()
        self._pending_exposures.clear()
        self._boundary_delivery_count = 0
        self._boundary_delivery_bytes = 0

    def pending_evidence_chain(self) -> tuple[set[str], str]:
        """Build proposals on admitted pending predecessors, without committing."""
        dedup, head = set(self._dedup_chain), self._chain_head
        for delivery in self._pending_provider_deliveries:
            for exposure in self._pending_exposures.values():
                if exposure.dedup_key != delivery.dedup_key or exposure.rendered not in delivery.rendered:
                    continue
                if exposure.dedup_key:
                    dedup.add(exposure.dedup_key)
                if exposure.next_chain_head and head == exposure.previous_chain_head:
                    head = exposure.next_chain_head
        return dedup, head

    def stage_exposure(self, *, rendered: str, dedup_key: str,
                       previous_chain_head: str, next_chain_head: str = "",
                       verification_candidate: str = "") -> None:
        identity = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        self._pending_exposures[identity] = PendingExposure(
            rendered, dedup_key, previous_chain_head, next_chain_head, verification_candidate
        )

    def record_typed_observation(
        self,
        *,
        action_index: int,
        tool_call_id: str,
        kind: str,
        action_request_sha256: str,
        compiled_observation_sha256: str,
        final_observation_sha256: str,
        interception_decision: str,
        canonical_contract: bool,
    ) -> None:
        """Stage exact typed-result lineage for the next provider delivery.

        This is not a second delivery authority. The core
        ``MiniSweProviderBoundary`` remains authoritative for delivered
        capsules; this pending join simply exposes the typed tool observation
        to that provider-bound lifecycle instead of inferring it from prose.
        """
        row = {
            "action_index": action_index,
            "iteration": self.iteration,
            "tool_call_id": tool_call_id,
            "kind": kind,
            "action_request_sha256": action_request_sha256,
            "compiled_observation_sha256": compiled_observation_sha256,
            "final_observation_sha256": final_observation_sha256,
            "interception_decision": interception_decision,
            "canonical_contract": canonical_contract,
        }
        self.store.append("typed_action_compiled", **row)
        self._pending_typed_observations.append(row)

    def next_provider_suffix(self) -> str:
        """Return one control delta per state vector, never an unchanged dose."""
        state = (self.phase, self.workspace_epoch, self.unmet_predicates)
        if state == self._last_control_state:
            return ""
        self._last_control_state = state
        return self.provider_suffix()

    def evaluate_observation(
        self,
        command: str,
        output: str,
        *,
        returncode: int | None,
        action_index: int,
    ) -> tuple[str, ...]:
        """Convert a real PASSING command result into semantic predicate receipts.

        A non-zero exit can never certify GREEN: the failing-executable path
        (``evaluate_failing_observation``) owns RED for those. An unknown exit
        (``None``) is correct-or-quiet — no receipt at all.
        """
        if self.contract is None:
            return ()
        if returncode != 0:
            return ()
        from .runtime_observation import compile_execution_evidence

        execution = compile_execution_evidence(command=command, output=output,
            returncode=returncode, action_id=action_index,
            repository_revision=self.repository_revision)
        if execution is not None and execution.outcome != "pass":
            return ()
        receipts = evaluate_passing_observation(
            self.contract,
            self._compiled_predicates,
            command,
            output,
            action_index=action_index,
            returncode=returncode,
        )
        predicate_ids = {item.predicate_id for item in self.predicates.values()}
        green: list[str] = []
        dependencies: dict[str, Any] = {}
        for receipt in receipts:
            if receipt.predicate_id not in predicate_ids:
                continue
            footprint = predicate_receipt_footprint(
                self._compiled_predicates[receipt.obligation_id], receipt,
            )
            self.record_receipt(
                receipt.predicate_id,
                command,
                returncode if returncode is not None else 1,
                output,
                epoch=self.workspace_epoch,
                status="GREEN",
                semantic=True,
                dependency_footprint=footprint,
            )
            dependencies[receipt.predicate_id] = asdict(footprint)
            green.append(receipt.predicate_id)
        self.store.append(
            "semantic_observation",
            command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest(),
            action_index=action_index,
            predicate_ids=green,
            dependency_footprints=dependencies,
        )
        return tuple(green)

    def gateway_state(self):
        """The shared per-run GatewayState over one persistent EpisodeState.

        Built lazily so a GT-off construction never touches the engine. The
        episode + delivered-dedup chain persist for the whole task, matching the
        nano bridge's ``_deliver`` production pattern.
        """
        if self._gateway_state is None:
            from groundtruth.runtime.episode_state import EpisodeState
            from groundtruth.runtime.gateway import GatewayState

            episode = self._episode or EpisodeState(episode_id=self.task_id)
            self._episode = episode
            self._gateway_state = GatewayState(
                graph_db=self.graph_db if self.graph_fresh else None,
                repo_root=self.repo_root,
                issue_text=self.issue_text,
                episode=episode,
                producer_audit_context={
                    "observation_id": f"{self.task_id}:{self.iteration}",
                    "decision_id": f"miniswe:{self.iteration}",
                    "decision_context": "miniswe.tool_result",
                    "decision_open": True,
                },
            )
        return self._gateway_state

    def bind_provider_response(
        self,
        response: Mapping[str, Any] | None = None,
        *,
        usage: Mapping[str, Any] | None = None,
        model: str = "",
        next_actions: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        """Record the terminal provider response and bind it to the latest delivery.

        A delivery is only ``DELIVERED`` (not merely ``EXECUTED``) once the
        provider responded; this join is the difference between attribution and
        a transcript substring guess.
        """
        digest = ""
        response_blob = ""
        if response is not None:
            encoded = json.dumps(
                response, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str,
            ).encode("utf-8")
            digest = hashlib.sha256(encoded).hexdigest()
            self.store.put_blob("provider_responses", digest, encoded)
            response_blob = f"provider_responses/{digest}.json"
        for key in self._usage:
            self._usage[key] = self._usage.get(key, 0) + (dict(usage or {}).get(key) or 0)
        reported_model = model
        if not reported_model and isinstance(response, Mapping):
            reported_model = str(response.get("model") or "")
        self.provider_reported_model = reported_model
        mismatch = self._provider_model_mismatch(reported_model)
        action_rows = []
        for index, action in enumerate(next_actions, start=1):
            canonical = json.dumps(
                dict(action), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str,
            ).encode("utf-8")
            action_rows.append({
                "ordinal": index,
                "action_sha256": hashlib.sha256(canonical).hexdigest(),
                "tool_name": str(
                    action.get("tool_name") or action.get("name")
                    or ("groundtruth" if action.get("gt_action") else "bash")
                ),
            })
        self.store.append(
            "provider_response",
            iteration=self.iteration,
            request_id=self._latest_delivery.request_id if self._latest_delivery else "",
            response_sha256=digest,
            response_blob=response_blob,
            provider_response_id=(
                str(response.get("id") or "") if isinstance(response, Mapping) else ""
            ),
            immediate_next_actions=action_rows,
            usage=dict(usage or {}),
            model=reported_model,
            requested_model=self.requested_model,
            resolved_model=self.resolved_model,
            fallback_model=self.fallback_model,
            model_mismatch=mismatch,
            delivery_ids=list(
                self._latest_delivery.delivery_ids
                if self._latest_delivery else ()
            ),
        )
        if self._latest_delivery is not None:
            self._terminal_request_ids.add(self._latest_delivery.request_id)
        if mismatch:
            raise ProviderModelMismatch(
                "provider model mismatch: requested="
                f"{self.requested_model!r}, resolved={self.resolved_model!r}, "
                f"reported={reported_model!r}"
            )

    @staticmethod
    def _normalized_model_id(model: str) -> str:
        value = (model or "").strip().lower()
        known_provider_prefixes = {
            "openai", "anthropic", "azure", "vertex_ai", "bedrock",
            "deepseek", "together_ai", "groq", "mistral",
        }
        if "/" in value:
            prefix, remainder = value.split("/", 1)
            if prefix in known_provider_prefixes:
                return remainder
        return value

    def _provider_model_mismatch(self, reported_model: str) -> bool:
        if not reported_model or not (self.requested_model or self.resolved_model):
            return False
        expected = {
            self._normalized_model_id(item)
            for item in (self.requested_model, self.resolved_model, self.fallback_model)
            if item
        }
        return self._normalized_model_id(reported_model) not in expected

    def bind_provider_failure(self, error: BaseException) -> None:
        """Record a provider terminal failure symmetrically with a response."""
        self.store.append(
            "provider_failure",
            iteration=self.iteration,
            request_id=self._latest_delivery.request_id if self._latest_delivery else "",
            error_type=type(error).__name__,
            error=str(error)[:500],
        )
        if self._latest_delivery is not None:
            self._terminal_request_ids.add(self._latest_delivery.request_id)

    def terminal_confirmed(self, request_id: str) -> bool:
        return request_id in self._terminal_request_ids

    @property
    def contract_shipped(self) -> bool:
        return self._contract_shipped

    def record_delivery_receipt(
        self,
        *,
        evidence_type: str,
        dedup_key: str,
        target: str,
        payload_hash: str,
        action_index: int,
        iteration: int,
    ) -> None:
        """L1 of the receipt ladder: a sealed delivery (bytes appended to the
        observation). L2-L4 are promoted post-hoc by the auditor from the
        agent's own trajectory (see gt_engine.miniswe_receipt).
        """
        self.store.append(
            "receipt",
            schema="gt_receipt.v1",
            transition="delivered",
            layer="miniswe",
            evidence_type=evidence_type,
            dedup_key=dedup_key,
            target=target,
            payload_hash=payload_hash,
            action_index=action_index,
            iteration=iteration,
            epoch=self.workspace_epoch,
            transaction_sha256=self._latest_transaction_sha256,
        )

    def admit_model_visible_delivery(
        self,
        *,
        lane: str,
        kind: str,
        rendered: str,
        action_index: int,
        iteration: int,
        dedup_key: str,
        target: str = "",
        semantics: str = "advisory",
        artifact_sha256: str = "",
    ) -> bool:
        """Admit one model-visible dose or record a typed refusal.

        Prompt context and sealed evidence share one request-level ceiling. A
        refusal is durable journal evidence and never a process exception.
        """

        if lane not in {"prompt", "sealed"}:
            raise ValueError(f"unsupported delivery lane: {lane}")
        encoded = rendered.encode("utf-8")
        rendered_bytes = len(encoded)
        payload_sha256 = hashlib.sha256(encoded).hexdigest()
        delivery_identity = payload_sha256
        effective_dedup_key = (
            f"prompt:{delivery_identity}" if lane == "prompt" else dedup_key
        )
        if iteration != self._admission_iteration:
            self._admission_iteration = iteration
            self._boundary_delivery_count = 0
            self._boundary_delivery_bytes = 0
        candidate_ordinal = self._boundary_delivery_count + 1
        per_delivery_limit = delivery_byte_limit(lane=lane, kind=kind)

        pending_identities = {item.identity for item in self._pending_provider_deliveries}
        if delivery_identity in pending_identities:
            return True
        reason = ""
        if delivery_identity in self._model_visible_delivery_identities:
            reason = "duplicate_delivery_identity"
        elif candidate_ordinal > MAX_BOUNDARY_CLAIMS:
            reason = "boundary_claim_ceiling"
        elif kind == "cochange_partner" and self._cochange_delivery_count >= 2:
            reason = "cochange_task_ceiling"
        elif rendered_bytes > per_delivery_limit:
            reason = "delivery_byte_ceiling"
        elif self._boundary_delivery_bytes + rendered_bytes > TOTAL_DELIVERY_BYTE_LIMIT:
            reason = "request_delivery_byte_ceiling"
        if reason:
            self.store.append(
                "delivery_refused",
                lane=lane,
                kind=kind,
                dedup_key=effective_dedup_key,
                reason=reason,
                candidate_ordinal=candidate_ordinal,
                rendered_bytes=rendered_bytes,
                payload_sha256=payload_sha256,
                delivery_identity=delivery_identity,
                per_delivery_limit=per_delivery_limit,
                admitted_count=self._boundary_delivery_count,
                admitted_bytes=self._boundary_delivery_bytes,
                boundary_claim_limit=MAX_BOUNDARY_CLAIMS,
                request_byte_limit=TOTAL_DELIVERY_BYTE_LIMIT,
                action_index=action_index,
                iteration=iteration,
            )
            return False

        self.store.put_blob(
            "deliveries", delivery_identity, rendered.encode("utf-8")
        )
        self.store.append(
            "delivery_prepared",
            lane=lane,
            kind=kind,
            action_index=action_index,
            iteration=iteration,
            evidence_type=kind,
            dedup_key=effective_dedup_key,
            target=target,
            rendered_bytes=rendered_bytes,
            payload_sha256=payload_sha256,
            delivery_identity=delivery_identity,
            delivery_blob=f"deliveries/{delivery_identity}.json",
            semantics=semantics,
            artifact_sha256=artifact_sha256,
            delivery_ordinal=candidate_ordinal,
        )
        self._boundary_delivery_count = candidate_ordinal
        self._boundary_delivery_bytes += rendered_bytes
        self._pending_provider_deliveries.append(PendingModelDelivery(
            delivery_identity, rendered, lane, kind, action_index, iteration,
            effective_dedup_key, target, semantics, artifact_sha256, candidate_ordinal,
        ))
        return True

    def stage_model_visible_delivery(
        self,
        *,
        kind: str,
        dedup_key: str,
        target: str = "",
        semantics: str = "advisory",
        artifact_sha256: str = "",
    ) -> None:
        """Stage classification until the exact action-lane bytes are final."""

        self._pending_delivery_metadata = {
            "kind": kind,
            "dedup_key": dedup_key,
            "target": target,
            "semantics": semantics,
            "artifact_sha256": artifact_sha256,
        }

    def consume_model_visible_delivery_metadata(self) -> dict[str, str]:
        metadata = self._pending_delivery_metadata
        self._pending_delivery_metadata = {}
        return metadata

    def unmet_obligation_texts(self) -> tuple[str, ...]:
        """The actual requirement text for every unmet predicate.

        Refusals that name opaque ``pred-<hash>`` IDs are unactionable - the
        model cannot act on a hash. Name the requirement itself (per Anthropic:
        error responses must be specific + actionable, not opaque codes).
        """
        text_by_id = (
            {
                obligation.obligation_id: obligation.text
                for obligation in self.contract.obligations
            }
            if self.contract is not None
            else {}
        )
        out: list[str] = []
        for predicate_id in self.unmet_predicates:
            obligation_id = self._obligation_by_predicate.get(predicate_id)
            text = text_by_id.get(obligation_id) if obligation_id else None
            out.append(text if text else predicate_id)
        return tuple(dict.fromkeys(out))

    def blocking_obligation_texts(self) -> tuple[str, ...]:
        """Only the obligation text of predicates that are actually RED.

        D3-G: a refusal must name the obligations GT has real failing evidence
        for, never the ones it merely lacks evidence about (UNKNOWN). Naming
        UNKNOWN obligations as "unmet" was the false claim that sent the model
        re-proving already-satisfied work.
        """
        text_by_id = (
            {
                obligation.obligation_id: obligation.text
                for obligation in self.contract.obligations
            }
            if self.contract is not None
            else {}
        )
        out: list[str] = []
        for predicate_id in self.blocking_predicates:
            obligation_id = self._obligation_by_predicate.get(predicate_id)
            text = text_by_id.get(obligation_id) if obligation_id else None
            out.append(text if text else predicate_id)
        return tuple(dict.fromkeys(out))

    def task_start_localization(self, *, commit: bool = True) -> str:
        """Prepare once per source state; legacy callers may admit immediately."""
        key = (self.issue_text, self.workspace_epoch, self.repository_revision,
               self.graph_db, self.graph_fresh)
        if key != self._localization_cache_key:
            self._localization_metadata = {}
            self._localization_chain = set(self._dedup_chain)
            self._localization_head = self._chain_head
            self._localization_candidate = self._prepare_task_start_localization()
            self._localization_cache_key = key
        rendered = compact_localization(self._localization_candidate)
        if commit and rendered:
            if not self.admit_model_visible_delivery(
                lane="sealed", rendered=rendered, action_index=0,
                iteration=self.iteration, **self.localization_delivery_metadata(),
            ):
                return ""
            self.acknowledge_localization(rendered)
        return rendered

    def localization_delivery_metadata(self) -> dict[str, str]:
        return dict(self._localization_metadata or {
            "kind": "localization", "dedup_key": "task-start-localization",
        })

    def acknowledge_localization(self, rendered: str) -> None:
        if rendered and rendered == compact_localization(self._localization_candidate):
            self._dedup_chain.update(self._localization_chain)
            self._chain_head = self._localization_head

    def _prepare_task_start_localization(self) -> str:
        """Ranked issue-keyed localization for the iteration-1 request.

        Reframed trigger: the ranked files are delivered at TASK START, not
        after the model happens to search. Sealed into the episode dedup chain
        so the reactive search path never re-delivers (fire-once preserved).
        """
        if not self.issue_text:
            return ""
        if self.graph_db and self.graph_fresh:
            semantic = self._semantic_task_start_localization()
            if semantic:
                return semantic
            try:
                from groundtruth.runtime.adapters.miniswe import normalize_event

                from .miniswe_evidence import run_evidence_pipeline

                event = normalize_event(
                    self.issue_text,
                    "",
                    0,
                    0,
                    cwd=self.repo_root,
                    semantic_events=("search_result",),
                    primary_boundary="search_result",
                )
                result = run_evidence_pipeline(
                    self.gateway_state(),
                    event,
                    dedup_chain=self._localization_chain,
                    chain_head=self._chain_head,
                    episode_id=self.task_id,
                    event_id=f"{self.task_id}:task_start",
                    native=os.environ.get("GT_GATEWAY_NATIVE") == "1",
                    model_prefix=True,
                    max_chars=600,
                )
                if result.chain_head:
                    self._localization_head = result.chain_head
                if result.sealed and result.envelope is not None:
                    self._localization_metadata = {
                        "kind": str(result.envelope.evidence_type or "localization"),
                        "dedup_key": str(result.envelope.dedup_key or ""),
                        "target": str(getattr(result.envelope, "target", "") or ""),
                    }
                    return result.rendered
            except Exception:  # noqa: BLE001 - deterministic lexical fallback follows
                pass
        return self._lexical_task_localization()

    def _semantic_task_start_localization(self) -> str:
        """Use the independent dense corpus when verified assets are configured."""
        model_dir = os.environ.get("GT_DENSE_MODEL_DIR", "").strip()
        snapshot = self.graph_query_snapshot()
        if not model_dir or not snapshot.graph_current or not snapshot.graph_path:
            return ""
        try:
            from .retrieval import RetrievalSource, hybrid_rank, render_semantic_localization

            ranking = hybrid_rank(
                snapshot.graph_path,
                self.issue_text,
                k=8,
                use_dense=True,
                model_dir=model_dir,
                store_path=os.environ.get("GT_CONTRACT_EMBEDDING_INDEX") or None,
            )
            dense = next(
                source for source in ranking.sources
                if source.source is RetrievalSource.DENSE
            )
            dense_receipt = dense.detail.get("execution_receipt")
            if isinstance(dense_receipt, dict):
                self.store.append(
                    "dense_index_ready",
                    **{key: value for key, value in dense_receipt.items() if key != "schema"},
                )
            if not dense.available or not dense.ranking:
                self.store.append(
                    "semantic_localization_unavailable",
                    reason=dense.reason or "dense_result_empty",
                    source_revision=snapshot.source_revision,
                    graph_revision=snapshot.graph_revision,
                )
                return ""
            items = []
            seen_paths: set[str] = set()
            for fused in ranking.fused:
                provenance = ranking.provenance.get(fused.stable_id)
                if provenance is None or not provenance.file_path:
                    continue
                path = provenance.file_path.replace("\\", "/")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                sources = ranking.contributing_sources(fused.stable_id)
                items.append({
                    "path": path,
                    "line": max(1, provenance.start_line),
                    "anchor": f"{path}:{max(1, provenance.start_line)}",
                    "score": fused.score,
                    "reasons": [f"retrieval:{source}" for source in sources],
                    "stable_id": fused.stable_id,
                })
                if len(items) == 4:
                    break
            if not items:
                return ""
            artifact = {
                "schema": "gt.semantic_localization.v1",
                "source_revision": snapshot.source_revision,
                "graph_revision": snapshot.graph_revision,
                "ranking": ranking.attribution_record(),
                "items": items,
                "semantics": "advisory_ranking_not_verification",
            }
            encoded = json.dumps(
                artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            digest = hashlib.sha256(encoded).hexdigest()
            self.store.put_blob("localization_advisory", digest, encoded)
            rendered = render_semantic_localization(items)
            self._localization_metadata = {
                "kind": "localization",
                "dedup_key": f"semantic-localization:{digest}",
                "target": str(items[0]["path"]),
                "semantics": "advisory",
                "artifact_sha256": digest,
            }
            return rendered
        except Exception as exc:  # noqa: BLE001 - graph/gateway fallback follows
            self.store.append(
                "semantic_localization_unavailable",
                reason=f"{type(exc).__name__}:{str(exc)[:160]}",
                source_revision=snapshot.source_revision,
                graph_revision=snapshot.graph_revision,
            )
            return ""

    def _lexical_task_localization(self) -> str:
        """Bounded advisory fallback with stable anchors and score reasons."""
        if not self.repo_root or not os.path.isdir(self.repo_root):
            return ""
        stop = {
            "and", "are", "change", "code", "fix", "for", "from", "must",
            "should", "task", "test", "tests", "that", "the", "this", "with",
        }
        terms = tuple(sorted({
            token.lower()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", self.issue_text)
            if token.lower() not in stop
        }))
        if not terms:
            return ""
        from .indexer import _SKIP_DIRS, SOURCE_EXTS

        rows: list[dict[str, Any]] = []
        scanned = 0
        root = Path(self.repo_root).resolve()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
            for filename in sorted(filenames):
                if Path(filename).suffix.lower() not in SOURCE_EXTS:
                    continue
                scanned += 1
                if scanned > 5_000:
                    break
                path = Path(dirpath) / filename
                try:
                    relative = path.relative_to(root).as_posix()
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                path_lower = relative.lower()
                text_lower = text.lower()
                path_terms = tuple(term for term in terms if term in path_lower)
                content_terms = tuple(term for term in terms if term in text_lower)
                if not path_terms and not content_terms:
                    continue
                score = 3 * len(path_terms) + len(content_terms)
                anchor_term = (path_terms or content_terms)[0]
                line = next(
                    (index for index, value in enumerate(text.splitlines(), start=1)
                     if anchor_term in value.lower()),
                    1,
                )
                reasons = [f"path_token:{term}" for term in path_terms]
                reasons += [f"content_token:{term}" for term in content_terms]
                rows.append({
                    "path": relative,
                    "line": line,
                    "anchor": f"{relative}:{line}",
                    "score": score,
                    "reasons": reasons,
                    "text": text[:4_000],
                })
            if scanned > 5_000:
                break
        candidates = sorted(
            rows, key=lambda row: (-int(row["score"]), str(row["anchor"]))
        )[:20]
        if not candidates:
            return ""
        ranked = candidates[:4]
        if os.environ.get("GT_RETRIEVAL_MODE") == "hybrid_required":
            try:
                from .dense_runtime import rank_documents

                snapshot = self.graph_query_snapshot()
                if not snapshot.graph_current:
                    raise RuntimeError("graph_snapshot_not_current")
                dense_order, dense_receipt = rank_documents(
                    query_text=self.issue_text,
                    documents={str(row["path"]): str(row["text"]) for row in candidates},
                    lexical_scores={
                        str(row["path"]): float(row["score"]) for row in candidates
                    },
                    model_dir=Path(os.environ["GT_DENSE_MODEL_DIR"]),
                    index_path=self.store.root / "dense-index.sqlite",
                    source_revision=self.repository_revision or "repository-start",
                    graph_revision=snapshot.graph_revision,
                    limit=4,
                )
                by_path = {str(row["path"]): row for row in candidates}
                ranked = [by_path[path] for path in dense_order if path in by_path]
                self.store.append(
                    "dense_index_ready",
                    **{key: value for key, value in dense_receipt.items() if key != "schema"},
                )
            except Exception as exc:  # noqa: BLE001 - readiness fails closed in receipt
                self.store.append(
                    "dense_index_ready",
                    query_ready=False,
                    reason=f"{type(exc).__name__}:{str(exc)[:200]}",
                )
        artifact = {
            "schema": "gt.localization_advisory.v1",
            "issue_sha256": hashlib.sha256(
                self.issue_text.encode("utf-8", "surrogatepass")
            ).hexdigest(),
            "scope": ".",
            "coverage": {
                "files_scanned": min(scanned, 5_000),
                "scan_limit": 5_000,
                "complete": scanned <= 5_000,
            },
            "semantics": "advisory",
            "items": ranked,
            "omissions": [
                "graph_localization_stale"
                if self.graph_db and not self.graph_fresh
                else "graph_localization_unavailable"
            ],
        }
        encoded = json.dumps(
            artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("localization_advisory", digest, encoded)
        rendered = "\n".join(
            f"{row['anchor']} score={row['score']} reasons={','.join(row['reasons'])}"
            for row in ranked
        )
        rendered = "[GT_EVIDENCE:localization]\n" + rendered
        self._localization_metadata = {
            "kind": "localization", "dedup_key": f"lexical-localization:{digest}",
            "target": str(ranked[0]["path"]), "semantics": "advisory",
            "artifact_sha256": digest,
        }
        return rendered

    def next_contract_delta(self, *, max_chars: int = 2400, commit: bool = True) -> str:
        """One full contract dose at task start, then obligation deltas only.

        E1: the full typed task contract is rendered once (into the first
        provider request); afterwards only genuinely changed unmet obligations
        re-surface, so immutable contract prose is never rematerialized.
        """
        if self.contract is None or not self._predicate_by_obligation:
            return ""
        signature = tuple(
            sorted((key, status.value) for key, status in self._status.items())
        )
        if not self._contract_shipped:
            text, _ = render_task_contract(self.contract, max_chars=max_chars)
        else:
            if signature == self._last_delta_signature:
                return ""
            shipped = tuple(
                obligation_id
                for obligation_id, predicate_id in self._predicate_by_obligation.items()
                if self.predicate_status(predicate_id) is PredicateStatus.GREEN
            )
            text, _ = render_obligation_delta(self.contract, shipped, max_chars=max_chars)
        self._prepared_contract_delta = (text, signature)
        if commit:
            self.acknowledge_contract_delta(text)
        return text

    def acknowledge_contract_delta(self, text: str) -> None:
        """Commit only the state represented by an admitted prepared delta.

        Legacy callers may consume next_contract_delta directly; the native
        session previews with commit=False so refusal cannot lose the delta.
        """
        prepared = self._prepared_contract_delta
        if prepared is not None and prepared[0] == text:
            self._contract_shipped = True
            self._last_delta_signature = prepared[1]
            self._prepared_contract_delta = None

    def evaluate_failing_observation(
        self,
        command: str,
        output: str,
        *,
        returncode: int | None,
        action_index: int,
    ) -> tuple[str, ...]:
        """Convert a real FAILING executable check into semantic RED receipts.

        C2: GREEN alone cannot tell the model which obligation is actively
        failing. A non-zero exit on an executable check (test/build/import)
        whose output lexically matches obligations marks those predicates RED.
        """
        if self.contract is None or self._predicate_by_obligation is None:
            return ()
        if not returncode or not is_executable_check(command):
            return ()
        matched = matching_obligation_ids(self.contract, command, output)
        red: list[str] = []
        for obligation_id in matched:
            predicate_id = self._predicate_by_obligation.get(obligation_id)
            if predicate_id is None:
                continue
            self.record_receipt(
                predicate_id, command, returncode, output,
                epoch=self.workspace_epoch, status="RED", semantic=True,
            )
            red.append(predicate_id)
        if red:
            self.store.append("semantic_red", action_index=action_index,
                              predicate_ids=red)
        return tuple(red)

    def note_failure_fingerprint(self, fingerprint: str, *, epoch: int) -> bool:
        """Track a failing-test fingerprint; True when a recovery steer is due.

        A4/GT_HYPOTHESIS: the SAME test failure recurring after an intervening
        edit (epoch advanced) with no progress warrants one bounded recovery
        steer (transient, delivered once via ``pending_transient``). Bounded to
        two steers per task; correct-or-quiet otherwise.
        """
        first = self._failure_first_epoch.get(fingerprint)
        if first is None:
            self._failure_first_epoch[fingerprint] = epoch
            self._failure_recurrences[fingerprint] = 1
            return False
        recurrences = self._failure_recurrences.get(fingerprint, 1) + 1
        self._failure_recurrences[fingerprint] = recurrences
        # Repeated output in the same epoch cannot establish post-edit evidence.
        # Pending proposals do not consume the recurrence or delivery budget.
        if self._pending_recovery is not None:
            return False
        if epoch > first and recurrences >= 2 and self._recovery_delivered < 2:
            self._pending_recovery = (fingerprint, epoch)
            self.pending_transient = (
                "GT_RECOVERY: the same test failure has recurred after your last "
                "observed edit. That change has not cleared this failure; inspect "
                "the check and changed surface before repeating the same action."
                f" [workspace epoch {epoch}; failure {hashlib.sha256(fingerprint.encode()).hexdigest()}]"
            )
            self.store.append(
                "recovery_prepared",
                fingerprint=fingerprint,
                epoch=epoch,
            )
            return True
        return False

    def prepare_recovery_delivery(self) -> str:
        """Admit a retryable recovery proposal without consuming exposure state."""
        if self._pending_recovery is None or not self.pending_transient:
            return ""
        fingerprint, epoch = self._pending_recovery
        if self.admit_model_visible_delivery(
            lane="sealed", kind="recovery", rendered=self.pending_transient,
            action_index=self.global_action, iteration=self.iteration,
            dedup_key=f"recovery:{fingerprint}:{epoch}",
        ):
            return self.pending_transient
        return ""

    def _refusal_escalates(self) -> bool:
        """True once two consecutive refusals show NO predicate-state change.

        STUCK-bound: the model must not be allowed to loop on ignored refusals
        (measured: fix-code/headless/modernize resubmitted immediately after a
        refusal). A predicate-state change (e.g. a GREEN, an edit) resets it.
        """
        signature = tuple(sorted((k, v.value) for k, v in self._status.items()))
        if signature == self._last_refusal_signature:
            self._refusal_count += 1
        else:
            self._refusal_count = 1
            self._last_refusal_signature = signature
        return self._refusal_count >= 2

    def _refuse(self, reason: str) -> bool:
        self.begin_implement()
        self.store.append(
            "submit_decision", accepted=False, phase=self.phase,
            iteration=self.iteration, reason=reason,
        )
        # Keep repetition as audit telemetry; never terminate Mini-SWE because
        # a model retried a refused submission without changing GT state.
        self._refusal_escalates()
        return False

    def submit_decision(self) -> bool:
        if self.graph_db and not self.graph_fresh:
            if not self.refresh_graph(phase="submit"):
                return self._refuse("graph_refresh_failed")
        self.verify_live_submit()
        accepted = super().submit_decision()
        if not accepted:
            code = (
                DiagnosticCode.GT_VERIFICATION_PLAN_MISSING
                if self.verification_plan and not self._verification_plan_evaluated
                else DiagnosticCode.GT_VERIFICATION_SEMANTIC_MISMATCH
            )
            self.diagnostics.record(
                DiagnosticEvent.create(
                    code=code,
                    severity="ERROR",
                    phase="submit",
                    subsystem="verification",
                    capability="semantic_verification",
                    task_id=self.task_id,
                    classification="primary",
                    cause="required_semantic_evidence_not_green",
                    impact="submission_refused",
                    recovery="run_exact_obligation_checks_and_resubmit_once",
                    retryable=True,
                    event_sequence=int(self.store.receipt()["event_count"]),
                    identities={"repository": self.repository_revision},
                )
            )
            self._refuse("unmet_obligations")
            return False
        self._refusal_count = 0
        self.store.append("submit_decision", accepted=accepted, phase=self.phase,
                          iteration=self.iteration)
        return accepted

    def advisory_submit_decision(self) -> bool:
        """Observe a baseline submission without applying GT policy.

        Advisory/assistive GT is not an execution authority. This transition
        keeps lifecycle telemetry honest while allowing exactly what stock
        Mini-SWE would have done, even when a GT predicate is RED.
        """
        if self.phase != "SUBMIT":
            raise RuntimeError(f"advisory submit requires SUBMIT, got {self.phase}")
        if self.graph_db and not self.graph_fresh:
            # Advisory mode cannot consume a refreshed graph to change the
            # native submission decision. A synchronous whole-repository build
            # here would only consume the finalization window. Keep the stale
            # state explicit; enforced submit and typed graph queries remain
            # genuine demand boundaries.
            self.store.append(
                "graph_refresh_deferred",
                phase="submit_advisory",
                reason="advisory_submit_cannot_consume_refresh",
                graph_fresh=False,
            )
        self._transition("FINISHED")
        self.store.append(
            "submit_decision",
            accepted=True,
            enforced=False,
            active_red=list(self.blocking_predicates),
            phase=self.phase,
            iteration=self.iteration,
        )
        return True

    def verify_live_submit(self) -> tuple[str, ...]:
        """D3-F: re-verify obligations against the LIVE workspace at submit.

        The lexical classifier cannot certify obligations from the model's own
        commands on real tasks (measured: modernize's "no py2 syntax" and
        portfolio's numeric obligations never flipped GREEN, so the gate refused
        valid submissions). Before the gate decides, check the actual filesystem:
        artifact obligations -> file exists; numeric obligations -> re-run the
        recorded proof command if one was executed. Only affects predicates that
        were UNKNOWN (already-GREEN and already-RED keep their receipts).
        """
        if self.contract is None or not self.repo_root:
            return ()
        if os.environ.get("GT_VERIFY_EXECUTE", "").strip() != "1":
            return ()
        green: list[str] = []
        for obligation_id, predicate in self._compiled_predicates.items():
            status = self.predicate_status(predicate.predicate_id)
            # Re-verify UNKNOWN (no evidence) AND stale RED (an early failing run
            # that the model has since fixed). A RED must be clearable by the live
            # workspace, otherwise a single early test failure blocks every
            # submit forever (measured gton13: headless-terminal ran all-13 PASS
            # but the gate kept refusing because GREEN needs a keyworded
            # executable check the model's real commands never match).
            if status is not PredicateStatus.UNKNOWN and status is not PredicateStatus.RED:
                continue
            if predicate.kind == "artifact" and predicate.scope:
                if self._live_artifact_exists(predicate.scope):
                    self.record_receipt(
                        predicate.predicate_id, "gt_live_verify", 0,
                        "artifact exists", epoch=self.workspace_epoch,
                        status="GREEN", semantic=True,
                        dependency_footprint=self._live_artifact_footprint(predicate.scope),
                    )
                    green.append(predicate.predicate_id)
                    continue
                if status is PredicateStatus.RED:
                    continue  # still genuinely missing
                self.record_receipt(
                    predicate.predicate_id, "gt_live_verify", 1,
                    "artifact missing", epoch=self.workspace_epoch,
                    status="RED", semantic=True,
                )
                continue
            if predicate.kind == "numeric_threshold":
                self._live_renumber(predicate, obligation_id, green)
        if green:
            self.store.append("live_verify", action_index=self.global_action,
                              predicate_ids=green)
        return tuple(green)

    def _live_artifact_exists(self, scope: tuple[str, ...]) -> bool:
        for rel in scope:
            abs_path = rel if os.path.isabs(rel) else os.path.join(self.repo_root, rel)
            if not os.path.isfile(abs_path):
                return False
        return True

    def _live_artifact_footprint(self, scope: tuple[str, ...]):
        root = Path(self.repo_root).resolve()
        paths: list[str] = []
        for relative in scope:
            original = Path(os.path.abspath(root / relative))
            resolved = original.resolve()
            if resolved != original or root not in resolved.parents:
                return conservative_execution_footprint(basis="artifact_external_or_symlink")
            paths.append(resolved.relative_to(root).as_posix())
        return certified_path_footprint(paths, basis="live_artifact_stat")

    def _live_renumber(
        self,
        predicate: Any,
        obligation_id: str,
        green: list[str],
    ) -> None:
        """RE-EXECUTE the model's recorded proof for a numeric obligation.

        The old implementation called ``evaluate_observation`` with empty output
        and a forced returncode 0, so it could never legitimately certify a
        bound - it was a false re-run. Here we actually execute the recorded
        command in the live workspace (bounded timeout, cwd=repo_root) and feed
        the REAL output back through the evaluator. If the command cannot be
        re-run or the output does not satisfy the bound, the obligation stays
        UNKNOWN (never silently GREEN).
        """
        receipt = self._receipts.get(predicate.predicate_id)
        if receipt is None or not receipt.command:
            return
        if self.predicate_status(predicate.predicate_id) is not PredicateStatus.UNKNOWN:
            return
        command = receipt.command
        import subprocess

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:  # noqa: BLE001 - live re-number is correct-or-quiet
            return
        output = (proc.stdout or "") + (proc.stderr or "")
        if not output.strip():
            return
        try:
            result = self.evaluate_observation(
                command, output, returncode=proc.returncode,
                action_index=self.global_action,
            )
        except Exception:  # noqa: BLE001 - live re-number is correct-or-quiet
            return
        if predicate.predicate_id in result:
            green.append(predicate.predicate_id)

    def note_select_catalog_bootstrap(self) -> None:
        """Record one GT-internal bootstrap provider call at the transport boundary.

        The agent's n_calls doubles as its step and cost limit, so a GT-internal
        turn must never increment it. Receipt reconciliation therefore compares
        api_calls + bootstrap calls against admissions and responses. Usage and
        cost continue to include this call: it is real spend.
        """
        self._select_catalog_bootstrap_calls += 1

    def final_state(self) -> dict[str, Any]:
        state = {"phase": self.phase, "epoch": self.workspace_epoch,
                 "unmet_predicates": list(self.unmet_predicates),
                 "iterations": self.iteration,
                 "delivered_evidence": self._accepted_sealed_delivery_count,
                 "terminal_requests": len(self._terminal_request_ids),
                 "select_catalog_bootstrap_calls": self._select_catalog_bootstrap_calls,
                 "contract_shipped": self._contract_shipped,
                 "requested_model": self.requested_model,
                 "resolved_model": self.resolved_model,
                 "provider_reported_model": self.provider_reported_model,
                 "fallback_model": self.fallback_model,
                 "event_journal": self.store.receipt(),
                 "usage": dict(self._usage)}
        if self.phase == "FINISHED":
            # T2.2: an accepted submission with UNKNOWN obligations is NOT
            # verified. Only report verified when every obligation has positive
            # evidence (GREEN). UNKNOWN -> unverified (never silently success).
            state["verified"] = not self.unmet_predicates
            state["unverified_predicates"] = [
                pid for pid, st in self._status.items()
                if st is PredicateStatus.UNKNOWN
            ]
        self.store.append("final_state", **state)
        return state
