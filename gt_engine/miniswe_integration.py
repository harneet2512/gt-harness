"""Mini-SWE integration boundary with external state and provider receipts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .delivery_budget import (
    MAX_TASK_DELIVERIES,
    TOTAL_DELIVERY_BYTE_LIMIT,
    delivery_byte_limit,
)
from .event_journal import GENESIS_HASH, JOURNAL_SCHEMA, event_hash
from .miniswe_controller import GroundtruthController, Predicate, PredicateStatus
from .run_diagnostics import DiagnosticCode, DiagnosticEvent, DiagnosticJournal
from .task_contract import (
    TaskContract,
    matching_obligation_ids,
    render_obligation_delta,
    render_task_contract,
)
from .verification_contract import (
    compile_obligation_predicates,
    evaluate_passing_observation,
    is_executable_check,
)


@dataclass(frozen=True)
class ProviderDelivery:
    request_id: str
    iteration: int
    payload_sha256: str
    phase: str
    suffix: str
    model_visible_sha256: str = ""


class ProviderModelMismatch(RuntimeError):
    """The provider reported a model outside the requested alias set."""


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
                rows = [
                    json.loads(line)
                    for line in self.path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if rows and all(row.get("schema") == JOURNAL_SCHEMA for row in rows):
                    self._sequence = int(rows[-1].get("sequence") or len(rows))
                    self._head = str(rows[-1].get("event_hash") or GENESIS_HASH)
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
                 resolved_model: str = "", fallback_model: str = ""):
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
        self.store = ExternalStateStore(state_dir, task_id)
        self.diagnostics = DiagnosticJournal(self.store.root, task_id=task_id)
        self.iteration = 0
        self.deliveries: list[ProviderDelivery] = []
        self._last_payload_hash = ""
        self._last_control_state: tuple[str, int, tuple[str, ...]] | None = None
        self.repo_root = str(repo_root or "")
        self.graph_db = graph_db or None
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
        self._terminal_request_ids: set[str] = set()
        self._contract_shipped = False
        self._last_delta_signature: tuple[tuple[str, str], ...] = ()
        self._edited_files: set[str] = set()
        self._failure_first_epoch: dict[str, int] = {}
        self._failure_recurrences: dict[str, int] = {}
        self._recovery_delivered = 0
        self._model_visible_delivery_count = 0
        self._model_visible_delivery_bytes = 0
        self._model_visible_delivery_identities: set[str] = set()
        self._accepted_sealed_delivery_count = 0
        self._pending_delivery_metadata: dict[str, str] = {}
        self.pending_transient = ""
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
        self.graph_fresh = bool(self.graph_db)
        self.graph_stale_since_revision = ""
        self._latest_transaction_sha256 = ""
        self.terminal_evidence_session: Any | None = None
        self.provider_boundary: Any | None = None
        self._closed_blockers: Any | None = None
        self._submit_invalidation_keys: dict[str, str] = {}

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
            schema=receipt.schema,
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
        if active_red:
            self.store.append(
                "red_invalidated_by_edit",
                predicate_ids=sorted(active_red),
                paths=list(normalized_paths),
                epoch=self.workspace_epoch,
            )
        self._edited_files.update(normalized_paths)
        if normalized_paths and self.graph_db:
            self.graph_fresh = False
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
        self.store.append(
            "repository_snapshot",
            boundary=boundary,
            repository_revision=self.repository_revision,
            snapshot_sha256=digest,
            complete=bool(snapshot.complete),
            omissions=list(snapshot.omissions),
            file_count=len(snapshot.files),
        )

    def record_edit_transaction(self, transaction: Any) -> None:
        encoded = transaction.canonical_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("edit_transactions", digest, encoded)
        self.repository_revision = str(transaction.post_revision)
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

    def record_execution_evidence(self, artifact: Any) -> str:
        """Store exact raw diagnostics and return a structured augmentation."""
        raw_digest = artifact.raw_output_sha256
        self.store.put_blob("raw_execution_output", raw_digest, artifact.raw_output)
        encoded = artifact.canonical_bytes()
        artifact_digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("execution_evidence", artifact_digest, encoded)
        payload = json.loads(encoded)
        self.store.append(
            "execution_evidence",
            artifact_sha256=artifact_digest,
            raw_blob=f"raw_execution_output/{raw_digest}.json",
            **payload,
        )
        return "[GT_EXECUTION_EVIDENCE]\n" + json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def refresh_graph(self, *, phase: str = "graph_query") -> bool:
        """Try one full rebuild; failure leaves graph queries unavailable."""
        if not self.repo_root:
            return False
        try:
            from .indexer import ensure_index

            rebuilt = ensure_index(
                self.repo_root, state_dir=str(self.store.root.parent)
            )
        except Exception as exc:  # noqa: BLE001 - freshness is fail-open
            self.store.append(
                "graph_refresh_failed", error_type=type(exc).__name__
            )
            self._record_graph_refresh_failure(type(exc).__name__, phase=phase)
            return False
        if not rebuilt:
            self.store.append("graph_refresh_failed", error_type="index_unavailable")
            self._record_graph_refresh_failure("index_unavailable", phase=phase)
            return False
        self.graph_db = rebuilt
        self.graph_fresh = True
        self.graph_stale_since_revision = ""
        self.store.append(
            "graph_refreshed",
            repository_revision=self.repository_revision,
            graph_db_sha256=hashlib.sha256(rebuilt.encode("utf-8")).hexdigest(),
        )
        return True

    def _record_graph_refresh_failure(self, cause: str, *, phase: str) -> None:
        self.graph_fresh = False
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
        self.store.put_blob("provider_requests", digest, encoded)
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
        )
        self.deliveries.append(delivery)
        self._last_payload_hash = digest
        self._latest_delivery = delivery
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
            request_blob=f"provider_requests/{digest}.json",
        )
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
        for receipt in receipts:
            if receipt.predicate_id not in predicate_ids:
                continue
            self.record_receipt(
                receipt.predicate_id,
                command,
                returncode if returncode is not None else 1,
                output,
                epoch=self.workspace_epoch,
                status="GREEN",
                semantic=True,
            )
            green.append(receipt.predicate_id)
        self.store.append(
            "semantic_observation",
            command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest(),
            action_index=action_index,
            predicate_ids=green,
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
                graph_db=self.graph_db,
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

        Prompt context and sealed evidence share one task-level ceiling. A
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
        candidate_ordinal = self._model_visible_delivery_count + 1
        per_delivery_limit = delivery_byte_limit(lane=lane, kind=kind)

        reason = ""
        if delivery_identity in self._model_visible_delivery_identities:
            reason = "duplicate_delivery_identity"
        elif candidate_ordinal > MAX_TASK_DELIVERIES:
            reason = "task_delivery_storm_backstop"
        elif rendered_bytes > per_delivery_limit:
            reason = "delivery_byte_ceiling"
        elif self._model_visible_delivery_bytes + rendered_bytes > TOTAL_DELIVERY_BYTE_LIMIT:
            reason = "task_delivery_byte_ceiling"
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
                admitted_count=self._model_visible_delivery_count,
                admitted_bytes=self._model_visible_delivery_bytes,
                task_delivery_limit=MAX_TASK_DELIVERIES,
                task_byte_limit=TOTAL_DELIVERY_BYTE_LIMIT,
                action_index=action_index,
                iteration=iteration,
            )
            return False

        self._model_visible_delivery_count = candidate_ordinal
        self._model_visible_delivery_bytes += rendered_bytes
        self._model_visible_delivery_identities.add(delivery_identity)
        if lane == "sealed":
            self._accepted_sealed_delivery_count += 1
        event = "context_addition_delivery" if lane == "prompt" else "evidence_delivery"
        self.store.append(
            event,
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
            semantics=semantics,
            artifact_sha256=artifact_sha256,
            delivery_ordinal=candidate_ordinal,
        )
        self.record_delivery_receipt(
            evidence_type=kind,
            dedup_key=effective_dedup_key,
            target=target,
            payload_hash=payload_sha256,
            action_index=action_index,
            iteration=iteration,
        )
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

    def task_start_localization(self) -> str:
        """Ranked issue-keyed localization for the iteration-1 request.

        Reframed trigger: the ranked files are delivered at TASK START, not
        after the model happens to search. Sealed into the episode dedup chain
        so the reactive search path never re-delivers (fire-once preserved).
        """
        if not self.issue_text:
            return ""
        if self.graph_db and self.graph_fresh:
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
                    dedup_chain=self._dedup_chain,
                    chain_head=self._chain_head,
                    episode_id=self.task_id,
                    event_id=f"{self.task_id}:task_start",
                    native=os.environ.get("GT_GATEWAY_NATIVE") == "1",
                    model_prefix=True,
                    max_chars=600,
                )
                if result.chain_head:
                    self._chain_head = result.chain_head
                if result.sealed and result.envelope is not None:
                    if not self.admit_model_visible_delivery(
                        lane="sealed",
                        kind=str(result.envelope.evidence_type or ""),
                        rendered=result.rendered,
                        action_index=0,
                        iteration=0,
                        dedup_key=str(result.envelope.dedup_key or ""),
                        target=str(getattr(result.envelope, "target", "") or ""),
                    ):
                        return ""
                    return result.rendered
            except Exception:  # noqa: BLE001 - deterministic lexical fallback follows
                pass
        return self._lexical_task_localization()

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

                graph_revision = (
                    hashlib.sha256(Path(self.graph_db).read_bytes()).hexdigest()
                    if self.graph_db and Path(self.graph_db).is_file()
                    else "graph-unavailable"
                )
                dense_order, dense_receipt = rank_documents(
                    query_text=self.issue_text,
                    documents={str(row["path"]): str(row["text"]) for row in candidates},
                    lexical_scores={
                        str(row["path"]): float(row["score"]) for row in candidates
                    },
                    model_dir=Path(os.environ["GT_DENSE_MODEL_DIR"]),
                    index_path=self.store.root / "dense-index.sqlite",
                    source_revision=self.repository_revision or "repository-start",
                    graph_revision=graph_revision,
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
        if not self.admit_model_visible_delivery(
            lane="sealed",
            kind="localization",
            rendered=rendered,
            action_index=0,
            iteration=0,
            dedup_key=f"lexical-localization:{digest}",
            target=str(ranked[0]["path"]),
            semantics="advisory",
            artifact_sha256=digest,
        ):
            return ""
        return rendered

    def next_contract_delta(self, *, max_chars: int = 2400) -> str:
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
            self._contract_shipped = True
            self._last_delta_signature = signature
            return text
        if signature == self._last_delta_signature:
            return ""
        self._last_delta_signature = signature
        shipped = tuple(
            obligation_id
            for obligation_id, predicate_id in self._predicate_by_obligation.items()
            if self.predicate_status(predicate_id) is PredicateStatus.GREEN
        )
        text, _ = render_obligation_delta(self.contract, shipped, max_chars=max_chars)
        return text

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
        # C6: the SAME failure recurring (with or without an edit between) is a
        # stuck loop - fire the steer on recurrence, not only on epoch change.
        if recurrences >= 2 and self._recovery_delivered < 2:
            self._recovery_delivered += 1
            self.pending_transient = (
                "GT_RECOVERY: the same test failure has recurred after your last "
                "edit. The previous approach is falsified - change the hypothesis "
                "or the edited surface rather than repeating it."
            )
            self.store.append(
                "recovery_steer",
                fingerprint=fingerprint,
                epoch=epoch,
                delivered=self._recovery_delivered,
            )
            return True
        return False

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

    def final_state(self) -> dict[str, Any]:
        state = {"phase": self.phase, "epoch": self.workspace_epoch,
                 "unmet_predicates": list(self.unmet_predicates),
                 "iterations": self.iteration,
                 "delivered_evidence": self._accepted_sealed_delivery_count,
                 "terminal_requests": len(self._terminal_request_ids),
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
