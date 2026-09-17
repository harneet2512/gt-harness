"""Mini-SWE integration boundary with external state and provider receipts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .delivery_budget import (
    MAX_BOUNDARY_CLAIMS,
    MAX_LOCALIZATION_DELIVERIES,
    MAX_LOCALIZATION_HARD_DELIVERIES,
    TOTAL_DELIVERY_BYTE_LIMIT,
    compact_localization,
    delivery_byte_limit,
)
from .attribution import feature_for_evidence
from .engine_state import EngineState, GraphQuerySnapshot, RuntimeLayout
from .event_journal import (
    GENESIS_HASH,
    JOURNAL_SCHEMA,
    event_hash,
    read_verified_events,
    verify_event_journal,
)
from .graph_coordinator import FrozenBuildInput, GraphBuildArtifact
from .miniswe_controller import GroundtruthController, Predicate, PredicateStatus
from .provider_wait import ProviderWaitScheduler
from .persistent_plan.provenance import cleared_check_provenance
from .request_history import store_provider_request
from .run_diagnostics import DiagnosticCode, DiagnosticEvent, DiagnosticJournal
from .task_contract import (
    TaskContract,
    _is_process_directive,
    matching_obligation_ids,
    render_obligation_transitions,
    render_task_contract,
)
from .verification_contract import (
    DependencyFootprint,
    DependencyIdentity,
    certified_path_footprint,
    compile_obligation_predicates,
    conservative_execution_footprint,
    evaluate_passing_observation,
    is_executable_check,
    normalize_dependency_path,
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


_KNOWN_PROVIDER_PREFIXES = frozenset({
    "openai", "anthropic", "azure", "vertex_ai", "bedrock",
    "deepseek", "together_ai", "groq", "mistral",
})


def normalized_model_id(model: str) -> str:
    """Strip a leading litellm transport prefix (``openai/`` etc.) so receipt
    checks compare catalog identity, not the adapter's routing spelling."""
    value = (model or "").strip().lower()
    if "/" in value:
        prefix, remainder = value.split("/", 1)
        if prefix in _KNOWN_PROVIDER_PREFIXES:
            return remainder
    return value


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
        self.startup_plan_events: list[dict] = []
        self.startup_journal_valid = True
        self.anchor_path = self.root / "events.anchor.json"
        # Opt-in live tail: GT_JOURNAL_TEE=1 mirrors each committed row to
        # stderr as one compact GT_EVENT|<json> line. The journal inside the
        # task environment is invisible until artifacts upload; the tee
        # makes LSP/graph health readable from streamed logs mid-run, on any
        # benchmark, with zero protocol surface. Off by default - the file
        # remains the journal of record; the tee is observability plumbing.
        self._tee = os.environ.get("GT_JOURNAL_TEE", "").lower() in {
            "1", "true", "yes",
        }
        if self.path.exists():
            try:
                verified = verify_event_journal(self.path)
                self.startup_journal_valid = verified.valid and self._anchor_holds(verified)
                if self.startup_journal_valid:
                    self._sequence = verified.event_count
                    self._head = verified.event_head
                    with self.path.open(encoding="utf-8") as journal:
                        for line in journal:
                            if not line.strip():
                                continue
                            row = json.loads(line)
                            if row.get("event") in {"plan_check_bound", "plan_revision_applied", "plan_revision_rejected", "persistent_plan_checkpoint"}:
                                self.startup_plan_events.append(row)
            except Exception:
                self.startup_journal_valid = False
                self.startup_plan_events.clear()
                # Legacy/partial state remains untouched. The next write starts
                # a fresh v1 chain; the verifier will correctly flag the mixed
                # journal rather than silently bless it.
                pass

    def _anchor_holds(self, verified: Any) -> bool:
        """Is this journal at least everything the anchor last saw?

        The hash chain proves a journal is self-consistent, never that it is
        COMPLETE. Cut the tail off and sequence numbers still run 1..N with
        correct parent hashes, so an unanchored verify returns valid and startup
        recovery rebuilds from a journal that has silently lost its most recent
        events -- restoring a plan checkpoint and check definitions describing a
        state the run already moved past.

        The anchor is written after each row, so it may legitimately trail by one
        when a run stops uncleanly. Trailing is therefore not corruption; the
        rule is that the journal must still CONTAIN what the anchor witnessed:

          journal shorter than the anchor   -> rows were removed
          same length, different head       -> rows were rewritten
          longer, wrong hash at that depth  -> history was replaced beneath us
          longer, matching hash             -> a crash between row and anchor

        A missing anchor beside a non-empty journal is not treated as a fault:
        journals written before this file existed are legitimate, and refusing
        them would strand real state. It simply provides no completeness proof.
        """
        try:
            if not self.anchor_path.is_file():
                return True
            anchor = json.loads(self.anchor_path.read_text(encoding="utf-8"))
            count = int(anchor["event_count"])
            head = str(anchor["event_head"])
        except (OSError, ValueError, TypeError, KeyError):
            return False
        if verified.event_count < count:
            return False
        if verified.event_count == count:
            return verified.event_head == head
        rows = read_verified_events(self.path)
        if count <= 0:
            return True
        if count > len(rows):
            return False
        return str(rows[count - 1].get("event_hash") or "") == head

    def _write_anchor(self) -> None:
        """Record the head OUTSIDE the journal, atomically, after the row lands.

        Row first, then anchor: the reverse order would claim an event that the
        journal does not yet contain, which is the failure this exists to catch.
        Correct-or-quiet -- an anchor that cannot be written must not fail the
        append that already succeeded.
        """
        try:
            payload = json.dumps(
                {"event_count": self._sequence, "event_head": self._head},
                sort_keys=True, separators=(",", ":"),
            )
            temporary = self.anchor_path.with_suffix(".json.tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.anchor_path)
        except OSError:
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
            self._write_anchor()
            if self._tee:
                try:
                    sys.stderr.write(f"GT_EVENT|{encoded}\n")
                    sys.stderr.flush()
                except OSError:
                    # A dead stderr must never take a journal append down
                    # with it - the file already holds the row.
                    pass

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


# Each revision is roughly 900MB and a full disk has already cost this
# project a run. Pins protect receipts, so the bound is generous, and
# exceeding it is journaled rather than swallowed.
MAX_PINNED_REVISIONS = 4


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
        # Same accounting as the catalog bootstrap: a GT-internal provider
        # call the agent's n_calls never sees.
        self._persistent_plan_bootstrap_calls = 0
        self.plan_inputs = None
        self._suite_verdict_ledger = None
        self.persistent_plan = None
        self.plan_row_predicates: dict[str, tuple[str, ...]] = {}
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
        self._localization_candidate = ""
        self._localization_metadata: dict[str, str] = {}
        self._localization_chain: set[str] = set()
        self._localization_head = ""
        self._localization_delivered = False
        self._delivered_localization_identities: set[str] = set()
        self._delivered_localization_targets: set[str] = set()
        self._pending_verification_candidate = ""
        self._pending_verification_metadata: dict[str, str] = {}
        self._pending_verification_recipe: dict[str, Any] = {}
        # Agent search actions are the freshest statement of its information
        # need. They feed the localization query at admission so re-delivered
        # localization tracks where attention actually moved.
        self._search_drift: tuple[str, ...] = ()
        self._localization_drift_at_render: tuple[str, ...] = ()
        # Revision key of the last localization resolution attempt, including
        # empty ones: None means never resolved; an empty result at the
        # current (revision, drift) state suppresses re-queueing until one
        # side moves.
        self._localization_render_revision: str | None = None
        self._model_visible_delivery_identities: set[str] = set()
        self._decision_delivery_identities: set[str] = set()
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
        self._pending_churn_steer = ""
        self._churn_abort_signaled = False
        from .churn_governor import ChurnGovernor

        self.churn_governor = ChurnGovernor()
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
        # Set at close_graph_lifecycle. Work still in flight may not
        # append past the point the run seals its manifest.
        self._journal_sealed = False
        self._dropped_observations = 0
        self._lsp_scheduler: Any | None = None
        self._lsp_requests: dict[str, Any] = {}
        # Promotion bookkeeping moved out of the coordinator: the (request,
        # base) pair each scheduled promotion certified against, the
        # already-considered input identities, and the single active task.
        self._lsp_bases: dict[str, tuple[FrozenBuildInput, GraphBuildArtifact]] = {}
        self._lsp_considered: set[tuple[Any, ...]] = set()
        self._lsp_active: str | None = None
        # Churn backoff: a leg whose base is superseded before it lands can
        # never publish (obsolete by construction). When the workspace
        # re-mints revisions faster than a leg completes, scheduling another
        # leg just feeds the race - smoke20 bandit-taint burned 15/36 legs
        # that way while each resident LSP server starved gt_index of
        # headroom. Consecutive obsolete dispositions defer the next
        # schedule, doubling to a cap; a leg that keeps up resets it. The
        # deferral never gates salvage - a finished doomed leg still merges
        # what it proved.
        self._lsp_churn_streak = 0
        self._lsp_churn_defer_until = 0.0
        # Last terminal disposition per base revision, so close can tell a
        # base whose leg lost the publication race (retryable at seal, when
        # nothing can publish under it) from one already answered.
        self._lsp_outcomes_by_base: dict[str, str] = {}
        # Memory backoff: an index refusal on cgroup headroom means the
        # container is already pressured - often by a resident LSP server.
        # Scheduling a fresh ~1GB leg the moment a recovery lands just
        # starves the next recovery the same way (bandit-taint's 16
        # consecutive GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT refusals).
        self._index_memory_defer_until = 0.0
        # Amend-spawn backoff: every amend_failed:* is a producer process
        # that ran and died, and the next serving boundary is seconds away -
        # run 34849119441 journaled five dead spawns inside ~60 events while
        # resident LSP legs held the cgroup. A bounded window turns the
        # stampede into one spawn per window whatever the error code.
        self._graph_amend_defer_until = 0.0
        self._amend_defer_journaled_until = 0.0
        self._recovery_defer_journaled_until = 0.0
        # Async initial index: the runner starts ensure_index on a worker and
        # hands the future here. The host loop runs immediately; the graph
        # publishes through engine_state when the build lands, or becomes the
        # amend parent when agent edits beat it to the revision.
        self._startup_index: Any | None = None
        self._startup_finalize: Any | None = None
        self._unadopted_graph: tuple[str, str] = ("", "")
        # Consecutive amend_failed:* refusals, keyed on the parent path they
        # failed against. Only the serving-boundary amend escalates them; the
        # transaction-boundary amend journals its refusal and defers here by
        # contract, so it never feeds this counter.
        self._amend_failure_streak: dict[str, int] = {}
        # Provider-wait scheduler: whole-graph products (dense contract
        # store today) are launched while the agent is blocked on the
        # network and drained by the owner thread at the next boundary.
        self._wait_scheduler: ProviderWaitScheduler | None = None
        self._dense_warmed_revision = ""
        self._dense_wait_failures: dict[str, int] = {}
        # Per-edit epoch map: which paths changed, and after which edit.
        # A superseded enrichment salvages only its mutations on paths
        # untouched since it was scheduled; an unenumerated change makes
        # that set unknowable and the salvage must refuse.
        self._edit_epoch = 0
        # The edit epoch whose synchronous amend the engine adopted. Set in
        # record_edit_transaction when publish_graph leaves the engine
        # graph_current; note_edit consults it so an adopted transaction does
        # not journal a second, post-publication invalidation.
        self._adopted_edit_epoch = -1
        self._adopted_edit_paths: set[str] = set()
        self._path_edit_epochs: dict[str, int] = {}
        self._incomplete_edit_epoch = 0
        self._lsp_epochs: dict[str, int] = {}
        self.store.append(
            "runtime_layout", layout_schema="gt.runtime_layout.v1",
            evidence_root=str(layout.evidence_root.resolve()),
        )
        self._journal_compiled_predicates("task_start")

    def _journal_compiled_predicates(
        self, phase: str, predicate_ids: Iterable[str] | None = None
    ) -> None:
        """Record the compiled obligation predicates the contract produced.

        The bridge path journaled one ``contract.predicate_compiled`` row per
        predicate; the Mini-SWE path compiled the same predicates in memory
        but never journaled them, so audit showed ``predicate_compiled_count=0``
        on tasks where the channel was actually armed.
        """
        wanted = set(predicate_ids) if predicate_ids is not None else None
        for predicate in self._compiled_predicates.values():
            if wanted is not None and predicate.predicate_id not in wanted:
                continue
            self.store.append(
                "contract.predicate_compiled",
                phase=phase,
                predicate_id=predicate.predicate_id,
                obligation_id=predicate.obligation_id,
                kind=predicate.kind,
                scope=list(predicate.scope),
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

    def _pin_graph_revision(self, graph_path: str, artifact_sha256: str) -> None:
        """Keep a revision a delivered artifact will have to be certified against.

        Retention keeps the live revision plus one and cannot tell that an old
        one is load-bearing. A delivered semantic-localization advisory records
        the revision it was ranked from, and ``verify_runtime_receipt`` later
        demands exactly one surviving certified graph matching it. Without this
        the revision is evicted and receipt issuance raises before a receipt
        exists -- measured on 12 of 20 tasks, which produced no product row at
        all.

        Correct-or-quiet: a pin that cannot be written costs the receipt, not
        the run, and the run must not die trying to protect its own paperwork.

        The adopted graph can live under ``enrichments/`` as well as
        ``revisions/``: certify_lsp_candidate publishes a candidate in place,
        so an advisory ranked from a promoted enrichment names an enrichment
        directory. Refusing to pin those left delivered artifacts without
        protection against the orphan sweep.
        """
        try:
            revision = Path(graph_path).resolve().parent
            if revision.parent.name not in ("revisions", "enrichments") or not revision.is_dir():
                return
            marker = revision / "pinned.json"
            if marker.exists():
                return
            # A revision is ~900MB and this repository has already lost a run to
            # a full disk. Pins are load-bearing, so the bound is generous
            # rather than tight, and hitting it is journaled: a receipt that
            # fails for want of a pin must not fail silently.
            existing = len(list(revision.parent.glob("*/pinned.json")))
            if existing >= MAX_PINNED_REVISIONS:
                self.store.append(
                    "revision_pin_refused",
                    reason="pin_budget_exhausted",
                    pinned=existing,
                    artifact_sha256=artifact_sha256,
                )
                return
            marker.write_text(
                json.dumps(
                    {
                        "schema": "gt.revision_pin.v1",
                        "reason": "delivered_semantic_localization",
                        "artifact_sha256": artifact_sha256,
                        "task_id": self.task_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - a pin is best effort, never fatal
            return

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

    # -- provider-wait window ---------------------------------------------
    #
    # The agent blocks on the provider every turn. That block is the one
    # moment in the loop where a CPU-bound GT job is guaranteed not to
    # contend with the host, so whole-graph products are launched there
    # rather than inside the agent's own bookkeeping path. The window is a
    # launch gate: the job keeps running on its worker past window end
    # (an ONNX batch cannot be suspended), and the owner thread collects
    # results at the next boundary. Both entry points are fail-open -
    # a scheduler fault must never reach the provider call it wraps.

    def provider_wait_begin(self) -> None:
        """Launch pending whole-graph work as the agent goes out on the wire."""
        try:
            if self._wait_scheduler is None:
                self._wait_scheduler = ProviderWaitScheduler()
            finished = self._drain_wait_work()
            enqueued = self._enqueue_wait_work()
            launched = self._wait_scheduler.begin_window()
            if launched or enqueued or finished:
                self.store.append(
                    "provider_wait_begin",
                    launched=launched,
                    enqueued=enqueued,
                    drained=finished,
                )
        except Exception as exc:  # noqa: BLE001 - scheduling never blocks a call
            self._append_observation(
                "provider_wait_begin_fault", error_type=type(exc).__name__
            )

    def provider_wait_end(self) -> None:
        """Boundary after the provider call; collect whatever landed."""
        try:
            if self._wait_scheduler is not None:
                self._drain_wait_work()
            self._poll_lsp_promotions()
        except Exception as exc:  # noqa: BLE001
            self._append_observation(
                "provider_wait_end_fault", error_type=type(exc).__name__
            )

    DENSE_WAIT_MAX_FAILURES = 3

    def _contract_store_path(self, graph_path: str) -> Path:
        """The contract-embedding store for this run's layout.

        Same resolution order ``_receipt_for_published_graph`` uses, so the
        wait-window refresh writes the store the query path actually reads.
        """
        from .contract_embeddings import default_store_path

        layout = self.engine_state.layout
        return Path(
            getattr(layout, "contract_store_path", None)
            or os.environ.get("GT_CONTRACT_EMBEDDING_INDEX")
            or default_store_path(graph_path)
        )

    def _enqueue_wait_work(self) -> list[str]:
        """Decide which whole-graph products are due; enqueue each by name."""
        assert self._wait_scheduler is not None
        enqueued: list[str] = []
        model_dir = os.environ.get("GT_DENSE_MODEL_DIR", "").strip()
        graph_path = str(getattr(self.engine_state, "graph_path", "") or "")
        revision = str(getattr(self.engine_state, "graph_revision", "") or "")
        if (
            model_dir
            and graph_path
            and revision
            and self._dense_warmed_revision != revision
            and self._dense_wait_failures.get(revision, 0) < self.DENSE_WAIT_MAX_FAILURES
        ):
            name = f"dense_refresh:{revision}"
            store_path = self._contract_store_path(graph_path)

            def work() -> Mapping[str, Any]:
                from .contract_embeddings import (
                    ContractEmbeddingStore,
                    onnx_embedder,
                    onnx_token_lengths,
                )

                # No deadline: the worker's clock is the provider wait, not
                # the agent's budget. The store is content-keyed, so vectors
                # produced against this graph stay valid wherever the same
                # contracts survive later amendments.
                store = ContractEmbeddingStore(store_path)
                try:
                    return dict(
                        store.refresh(
                            graph_path,
                            embed_fn=onnx_embedder(model_dir),
                            length_fn=onnx_token_lengths(model_dir),
                        )
                    )
                finally:
                    store.close()

            if self._wait_scheduler.enqueue(name, work) in {"queued", "replaced"}:
                # Newest revision wins the pending queue: a stale revision's
                # refresh writes a store keyed to that revision's graph file,
                # so it can never serve the adopted graph's queries. Running
                # jobs are untouched -- their content-keyed vectors stay valid.
                self._wait_scheduler.drop_pending_family("dense_refresh:", name)
                enqueued.append(name)
        return enqueued

    def _journal_dense_refresh_readiness(
        self, revision: str, payload: Mapping[str, Any]
    ) -> None:
        """Emit ``dense_index_ready`` for a completed wait-window refresh.

        The query-side receipt fires only when a dense query runs; a store
        populated between localizations would stay invisible to the receipt
        even though the capability is real. The refresh proves coverage for
        its graph; the probe here proves the store file is integral and
        populated -- the two together are the honest readiness evidence, and
        ``vector_source`` says the receipt came from refresh, not ranking.
        """
        import sqlite3

        from .dense_runtime import model_identity

        graph_path = str(getattr(self.engine_state, "graph_path", "") or "")
        store_path = Path(self._contract_store_path(graph_path))
        quick_check = "missing"
        try:
            connection = sqlite3.connect(
                f"{store_path.resolve().as_uri()}?mode=ro", uri=True
            )
            try:
                quick_check = str(
                    connection.execute("PRAGMA quick_check").fetchone()[0]
                )
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            quick_check = "unreadable"
        documents_after = int(payload.get("documents_after") or 0)
        query_ready = quick_check == "ok" and documents_after > 0
        self.store.append(
            "dense_index_ready",
            query_ready=query_ready,
            **model_identity(),
            source_revision=str(payload.get("source_revision") or ""),
            graph_revision=revision,
            document_count=documents_after,
            query_result_count=documents_after if query_ready else 0,
            embedded_documents=int(payload.get("embedded") or 0),
            cached_documents=int(payload.get("unchanged") or 0),
            index_sha256=str(payload.get("index_sha256") or ""),
            sqlite_quick_check=quick_check,
            exact_rescore=True,
            vector_source="dense_wait_refresh",
            reason=None if query_ready else "dense_refresh_incomplete",
        )

    def _drain_wait_work(self) -> list[str]:
        """Journal each finished job's outcome on the owner thread."""
        assert self._wait_scheduler is not None
        drained: list[str] = []
        for name, status, payload in self._wait_scheduler.drain():
            drained.append(name)
            kind, _, key = name.partition(":")
            if kind == "dense_refresh":
                if status == "ok" and isinstance(payload, Mapping):
                    self._dense_warmed_revision = key
                    self.store.append(
                        "dense_wait_refresh",
                        outcome="refreshed",
                        graph_revision=key,
                        embedded=int(payload.get("embedded") or 0),
                        unchanged=int(payload.get("unchanged") or 0),
                        deleted=int(payload.get("deleted") or 0),
                        documents_after=int(payload.get("documents_after") or 0),
                    )
                    # A completed refresh populated the whole contract store
                    # for this revision: every later query pool is a subset of
                    # it, so readiness is derivable -- but only measured, never
                    # assumed. Probe integrity + population on the owner thread
                    # and emit the same receipt a dense query would, stamped
                    # with the revision the refresh actually covered.
                    self._journal_dense_refresh_readiness(key, payload)
                else:
                    self._dense_wait_failures[key] = (
                        self._dense_wait_failures.get(key, 0) + 1
                    )
                    self.store.append(
                        "dense_wait_refresh",
                        outcome="failed",
                        graph_revision=key,
                        error=str(payload)[:200],
                    )
            elif kind == "lsp_salvage":
                outcome = (
                    str(payload.get("outcome") or "")
                    if isinstance(payload, Mapping) else ""
                )
                fields: dict[str, Any] = {"task_id": key}
                if isinstance(payload, Mapping):
                    for field_name in (
                        "applied", "inserted", "updated", "deleted",
                        "skipped_stale", "skipped_diverged", "error",
                        "graph_revision", "source_revision",
                    ):
                        if field_name in payload:
                            fields[field_name] = payload[field_name]
                published = False
                if status == "ok" and outcome == "merged":
                    published = (
                        self.engine_state.graph_current
                        and self.engine_state.graph_path
                        == str(payload.get("live_graph_path") or "")
                        and self.engine_state.publish_graph(
                            graph_path=str(payload.get("graph_path") or ""),
                            graph_revision=str(payload.get("graph_revision") or ""),
                            source_revision=str(payload.get("source_revision") or ""),
                        )
                    )
                self.store.append(
                    "lsp_salvage",
                    outcome=(
                        "published" if published
                        else "superseded" if outcome == "merged"
                        else outcome if outcome
                        else f"failed:{str(payload)[:120]}"
                    ),
                    **fields,
                )
                if published:
                    # A salvage adoption is still an adoption: the merged
                    # graph carries only what survived the divergence sweep,
                    # so the tier converges to full coverage only if a fresh
                    # leg is scheduled on it. Every other adoption point
                    # already does this; without it here a salvage near seal
                    # freezes partial coverage as the terminal state.
                    self._record_graph_publication()
                    self._maybe_schedule_lsp_promotion()
            else:
                self.store.append(
                    "wait_work_terminal", name=name, status=status,
                    detail=str(payload)[:200],
                )
        return drained

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

    #: Wall-clock ceiling for one re-verification pass, and the per-command
    #: bound inside it. Sized against what it replaces: in run 34095557374 the
    #: model spent roughly 173 iterations at 17.8s re-proving obligations an
    #: edit had discarded - about 3,000s. A pass costing tens of seconds is
    #: worth it; one costing minutes is not, so it is bounded rather than
    #: trusted to be small. Measured there: 16 predicates were ever proven and
    #: the most reused proof command covered 7 of them, so a pass is roughly
    #: five to eight distinct commands, not fifteen.
    REVERIFY_PASS_BUDGET_SECONDS = 30.0
    REVERIFY_COMMAND_TIMEOUT_SECONDS = 15.0
    #: Total wall-clock the seal may spend re-observing stale plan checks on
    #: the submitted tree. Independent of the LSP seal-convergence window.
    SEAL_PLAN_RECHECK_BUDGET_SECONDS = 60.0
    SEAL_PLAN_RECHECK_PASSES = 3

    def _reverify_after_edit(self, candidates: dict[str, str]) -> None:
        """Invalidate now; coalesce registered checks at a verification boundary.

        Receipt commands are audit text, not executable check specifications.
        In particular, a passing compound command may also contain an edit.
        Never replay it, and never extract a guessed test suffix from it.
        """
        pending = getattr(self, "_pending_check_ids", set())
        specs = getattr(self, "_check_specs", {})
        # Dependencies are workspace-wide until a complete footprint is
        # certified. A second edit supersedes, rather than duplicates, work.
        pending.update(specs)
        self._pending_check_ids = pending
        # This row was journaled as `obligation_reverified` while running zero
        # commands -- the name claimed a pass that never happened. The work is
        # real, but it is a QUEUE: drain_plan_checks runs the registered checks
        # at the verification boundary. The kind is renamed so the journal
        # stops asserting an execution that did not occur; journals are
        # append-only, so the honest kind is a new event, not a rewrite.
        self.store.append(
            "obligation_reverify_queued", candidates=sorted(candidates),
            distinct_commands=len(set(candidates.values())), commands_run=0,
            preserved=[], skipped="queued_registered_checks" if pending else "no_registered_check",
            pending_check_ids=sorted(pending), epoch=self.workspace_epoch,
            budget_seconds=self.REVERIFY_PASS_BUDGET_SECONDS,
        )

    def bind_plan_check(self, value: dict) -> str:
        import shlex as _shlex

        from .persistent_plan.checks import (
            CheckSpec,
            _looks_like_test_source,
            validation_source_digest,
        )
        from .runtime_observation import _protocol, capture_workspace

        # Test identity is bound here, not at observation: the smoke cohort
        # journaled plan_check_bound rows with empty protocol, no selected
        # tests, no source paths and no environment -- a spec that admits any
        # execution of the same argv. What is derivable is derived; what is
        # not is named in test_identity_basis so the journal shows which part
        # of the identity was never bound.
        material = dict(value)
        argv = material.get("argv")
        argv_ok = (
            isinstance(argv, (list, tuple)) and bool(argv)
            and all(isinstance(arg, str) and arg for arg in argv)
        )
        basis: dict[str, str] = {}
        if material.get("protocol"):
            basis["protocol"] = "declared"
        elif argv_ok:
            derived_protocol = _protocol(_shlex.join(list(argv)))
            if derived_protocol != "unknown":
                material["protocol"] = derived_protocol
                basis["protocol"] = "argv_derived"
            else:
                basis["protocol"] = "unbound"
        else:
            basis["protocol"] = "unbound"
        if material.get("test_source_paths"):
            basis["test_source_paths"] = "declared"
        elif argv_ok:
            root = Path(self.repo_root).resolve()
            bound_paths: list[str] = []
            for arg in list(argv)[1:]:
                if arg.startswith("-"):
                    continue
                candidate = (
                    root / str(material.get("cwd") or ".") / arg.split("::", 1)[0]
                ).resolve()
                if candidate == root or root not in candidate.parents:
                    continue
                relative = candidate.relative_to(root).as_posix()
                if candidate.exists() and _looks_like_test_source(relative):
                    bound_paths.append(relative)
            if bound_paths:
                material["test_source_paths"] = sorted(set(bound_paths))
                basis["test_source_paths"] = "argv_derived"
            else:
                basis["test_source_paths"] = "unbound"
        else:
            basis["test_source_paths"] = "unbound"
        if material.get("selected_test_ids"):
            basis["selected_test_ids"] = "declared"
        elif argv_ok:
            node_ids = sorted({
                arg for arg in list(argv)[1:]
                if "::" in arg and not arg.startswith("-")
            })
            if node_ids:
                material["selected_test_ids"] = node_ids
                basis["selected_test_ids"] = "argv_derived"
            else:
                basis["selected_test_ids"] = "unbound"
        else:
            basis["selected_test_ids"] = "unbound"
        if material.get("environment_sha256"):
            basis["environment_sha256"] = "declared"
        else:
            environment = getattr(self, "_current_check_environment_sha256", "")
            if environment:
                material["environment_sha256"] = environment
                basis["environment_sha256"] = "bind_context"
            else:
                basis["environment_sha256"] = "unbound"

        if material.get("program"):
            return self._bind_program_check(material)
        spec = CheckSpec.from_dict(material, self.repo_root)
        known = {row.row_id for row in getattr(self.persistent_plan, "rows", ())}
        if not set(spec.requirement_ids).issubset(known):
            raise ValueError("check references unknown plan row")
        snapshot = capture_workspace(self.repo_root, excluded_roots=(self.store.root,))
        digest = validation_source_digest(spec, snapshot)
        if spec.test_source_digest and spec.test_source_digest != digest:
            raise ValueError("test source identity mismatch")
        data = spec.as_dict()
        data.pop("check_id")
        spec = CheckSpec.from_dict({**data, "test_source_digest": digest}, self.repo_root)
        specs = getattr(self, "_check_specs", {})
        if spec.check_id in specs:
            from dataclasses import replace

            spec = replace(spec, requirement_ids=tuple(sorted(
                set(spec.requirement_ids) | set(specs[spec.check_id].requirement_ids))))
        specs[spec.check_id] = spec
        self._check_specs = specs
        pending = getattr(self, "_pending_check_ids", set())
        pending.add(spec.check_id)
        self._pending_check_ids = pending
        self.store.append(
            "plan_check_bound", test_identity_basis=basis, **spec.as_dict())
        return spec.check_id

    def _bind_program_check(self, material: dict) -> str:
        """Bind a behavioral-program check: the program text is the spec."""
        from .persistent_plan.checks import (
            ProgramCheckSpec,
            program_command_is_verdict,
        )

        if not program_command_is_verdict(str(material.get("program") or "")):
            raise ValueError("program has no assertion verdict")
        environment = str(material.get("environment_sha256") or getattr(
            self, "_current_check_environment_sha256", ""))
        spec = ProgramCheckSpec.from_command(
            material["program"], material.get("requirement_ids"),
            environment_sha256=environment,
        )
        known = {row.row_id for row in getattr(self.persistent_plan, "rows", ())}
        if not set(spec.requirement_ids).issubset(known):
            raise ValueError("check references unknown plan row")
        specs = getattr(self, "_program_check_specs", {})
        if spec.check_id in specs:
            from dataclasses import replace

            spec = replace(spec, requirement_ids=tuple(sorted(
                set(spec.requirement_ids)
                | set(specs[spec.check_id].requirement_ids))))
        specs[spec.check_id] = spec
        self._program_check_specs = specs
        pending = getattr(self, "_pending_program_check_ids", set())
        pending.add(spec.check_id)
        self._pending_program_check_ids = pending
        self.store.append(
            "plan_program_check_bound",
            test_identity_basis={
                "program": "verbatim_bound",
                "environment_sha256": (
                    "declared" if material.get("environment_sha256")
                    else "bind_context" if environment else "unbound"
                ),
            },
            **spec.as_dict(),
        )
        return spec.check_id

    def bind_initial_plan_checks(self) -> None:
        """Bind admissible simple plan checks automatically, without a new tool loop."""
        import shlex

        if getattr(self, "_initial_plan_checks_bound", False):
            return
        self._initial_plan_checks_bound = True
        self._restore_plan_revisions()
        recovered_rows = self._restore_plan_check_definitions()
        if not self.store.startup_journal_valid:
            return
        from .persistent_plan.checks import (
            CheckSpec,
            decompose_check_command,
            program_command_is_verdict,
        )

        grouped: dict[tuple[tuple[str, ...], str], list[str]] = {}
        programs: dict[str, list[str]] = {}

        def _offer_program(command: str, row_id: str, fallback_reason) -> None:
            """A shell program binds only when its exit status is a verdict."""
            if program_command_is_verdict(command):
                programs.setdefault(command, []).append(row_id)
            else:
                self.store.append(
                    "plan_check_binding_pending", row_id=row_id,
                    reason=fallback_reason,
                )

        for row in getattr(self.persistent_plan, "rows", ()):
            if not row.verification_command or row.row_id in recovered_rows:
                continue
            if _is_process_directive(getattr(row, "text", "")):
                # A test run cannot evidence a workflow directive; its channel
                # is the workspace envelope probe in ``drain_plan_checks``.
                continue
            segments, _seps, _last_check, reason = decompose_check_command(
                row.verification_command
            )
            if segments is None:
                if reason == "shell_expansion" or (
                    reason and reason.startswith("unsupported_shell_operator:")
                    and reason.rsplit(":", 1)[-1] not in {"||", "&"}
                ):
                    # The command IS the check - a shell program like fd's
                    # ``test "$(fd --sort)" = "a.txt"`` whose exit status the
                    # official verifier reads the same way. ``||``/``&`` make
                    # that status ambiguous, so they never reach here.
                    _offer_program(
                        row.verification_command, row.row_id,
                        "program_has_no_assertion")
                else:
                    self.store.append(
                        "plan_check_binding_pending", row_id=row.row_id,
                        reason=reason
                    )
                continue
            # Every decomposed segment must be an admissible check on its own —
            # a composite like ``make build && pytest`` cannot drop the build
            # step without changing what the row's verification means.
            admissible = True
            for seg_argv, _seg_cwd in segments:
                try:
                    CheckSpec.from_dict(
                        {"argv": seg_argv, "requirement_ids": [row.row_id]},
                        self.repo_root,
                    )
                except ValueError:
                    admissible = False
                    break
            if not admissible:
                # Decomposed cleanly but not test-runner argv (``test``,
                # ``diff``, ``grep -q`` assertions) - the whole command is a
                # behavioral program bound verbatim when its exit status is
                # an assertion; otherwise it honestly stays pending.
                _offer_program(
                    row.verification_command, row.row_id,
                    "program_has_no_assertion")
                continue
            for seg_argv, seg_cwd in segments:
                grouped.setdefault((tuple(seg_argv), seg_cwd or "."), []).append(row.row_id)
        for (argv, cwd), row_ids in grouped.items():
            try:
                self.bind_plan_check(
                    {"argv": list(argv), "cwd": cwd, "requirement_ids": row_ids}
                )
            except ValueError as exc:
                self.store.append("plan_check_binding_pending", row_ids=row_ids, reason=str(exc))
        for program, row_ids in programs.items():
            try:
                self._bind_program_check(
                    {"program": program, "requirement_ids": row_ids}
                )
            except ValueError as exc:
                self.store.append("plan_check_binding_pending", row_ids=row_ids,
                                  reason=f"program_bind:{exc}")

    def _restore_plan_check_definitions(self) -> set[str]:
        """Recover validated definitions once; historical results confer no proof."""
        from dataclasses import replace

        from .persistent_plan.checks import CheckSpec, validation_source_digest
        from .runtime_observation import capture_workspace

        if not self.store.startup_journal_valid:
            self.store.append("plan_check_restore_rejected", reason="invalid_startup_journal")
            return set()
        known = {row.row_id for row in getattr(self.persistent_plan, "rows", ())}
        specs = {}
        touched = set()
        program_specs: dict[str, Any] = {}
        for event in self.store.startup_plan_events:
            if event["event"] == "plan_check_bound":
                try:
                    spec = CheckSpec.from_dict(event, self.repo_root)
                    if not set(spec.requirement_ids).issubset(known):
                        raise ValueError("unknown_plan_row")
                    specs[spec.check_id] = spec
                    touched.update(spec.requirement_ids)
                except (ValueError, TypeError) as exc:
                    self.store.append("plan_check_restore_rejected", reason=str(exc))
            elif event["event"] == "plan_program_check_bound":
                try:
                    from .persistent_plan.checks import ProgramCheckSpec

                    spec = ProgramCheckSpec.from_command(
                        event["program"], event.get("requirement_ids"),
                        environment_sha256=event.get("environment_sha256", ""),
                    )
                    if spec.check_id != event.get("check_id"):
                        raise ValueError("check identity mismatch")
                    if not set(spec.requirement_ids).issubset(known):
                        raise ValueError("unknown_plan_row")
                    program_specs[spec.check_id] = spec
                    touched.update(spec.requirement_ids)
                except (ValueError, TypeError, KeyError) as exc:
                    self.store.append("plan_check_restore_rejected", reason=str(exc))
            else:
                if event["event"] == "plan_revision_applied" and event.get("operation") == "revise":
                    row_id = event.get("row_id")
                    if not isinstance(row_id, str) or row_id not in known:
                        self.store.append("plan_check_restore_rejected", reason="invalid_revised_row")
                        continue
                    touched.add(row_id)
                    specs = {key: replace(spec, requirement_ids=tuple(
                        identity for identity in spec.requirement_ids if identity != row_id
                    )) for key, spec in specs.items()
                        if any(identity != row_id for identity in spec.requirement_ids)}
                    program_specs = {key: replace(spec, requirement_ids=tuple(
                        identity for identity in spec.requirement_ids if identity != row_id
                    )) for key, spec in program_specs.items()
                        if any(identity != row_id for identity in spec.requirement_ids)}
        if specs:
            snapshot = capture_workspace(self.repo_root, excluded_roots=(self.store.root,))
            restored = getattr(self, "_check_specs", {})
            pending = getattr(self, "_pending_check_ids", set())
            for key, spec in specs.items():
                if not spec.test_source_digest or validation_source_digest(spec, snapshot) != spec.test_source_digest:
                    self.store.append("plan_check_restore_rejected", check_id=key, reason="test_source_identity_changed_or_missing")
                    continue
                restored[key] = spec
                pending.add(key)
                self.store.append("plan_check_restored", check_id=key, evidence_state="UNVERIFIED")
            self._check_specs = restored
            self._pending_check_ids = pending
        if program_specs:
            # The program text is the identity - no source digest to reverify.
            # Restored checks go pending so the drain re-executes them under
            # the current environment rather than trusting a stale verdict.
            restored = getattr(self, "_program_check_specs", {})
            pending = getattr(self, "_pending_program_check_ids", set())
            for key, spec in program_specs.items():
                restored[key] = spec
                pending.add(key)
                self.store.append("plan_check_restored", check_id=key,
                                  evidence_state="UNVERIFIED",
                                  binding_basis="program_spec")
            self._program_check_specs = restored
            self._pending_program_check_ids = pending
        return touched

    @staticmethod
    def _validated_design_revision(value: Any) -> dict:
        allowed = {"approach", "verification_kind", "verification_command"}
        if not isinstance(value, dict) or set(value) - allowed or any(not isinstance(v, str) for v in value.values()):
            raise ValueError("invalid plan revision")
        return value

    def _restore_plan_revisions(self) -> None:
        """Replay only hash-linked advisory edits; never restore evidence status."""
        from dataclasses import replace

        if getattr(self, "persistent_plan", None) is None:
            return
        if getattr(self, "_plan_revision_restore_attempted", False):
            return
        self._plan_revision_restore_attempted = True
        if not self.store.startup_journal_valid:
            return
        seen = getattr(self, "_plan_requests_seen", set())
        for event in self.store.startup_plan_events:
            if event.get("event") != "plan_revision_applied":
                continue
            if event.get("revision_layout") != "gt.plan_revision.v1":
                continue
            plan = self.persistent_plan
            try:
                request_id = event["request_id"]
                if not isinstance(request_id, str) or not request_id or Path(request_id).name != request_id:
                    raise ValueError("invalid recovered request identity")
                current = hashlib.sha256(plan.canonical_json().encode()).hexdigest()
                if event["previous_plan_digest"] != current:
                    raise ValueError("plan recovery base mismatch")
                row_id = event["row_id"]
                if plan.row(row_id) is None:
                    raise ValueError("unknown recovered row")
                operation = event["operation"]
                candidate = plan
                if operation == "revise":
                    value = self._validated_design_revision(event["value"])
                    applied = {**value, **cleared_check_provenance(value)}
                    candidate = replace(plan, rows=tuple(replace(row, **applied) if row.row_id == row_id else row
                                                         for row in plan.rows))
                elif operation == "defer":
                    reason = event.get("reason")
                    if not isinstance(reason, str) or not reason.strip():
                        raise ValueError("invalid recovered deferral")
                elif operation != "bind-check":
                    raise ValueError("unknown recovered operation")
                if hashlib.sha256(candidate.canonical_json().encode()).hexdigest() != event["resulting_plan_digest"]:
                    raise ValueError("plan recovery result mismatch")
                self.persistent_plan = candidate
                if operation == "defer":
                    deferred = getattr(self, "_plan_deferred", {})
                    deferred[row_id] = reason
                    self._plan_deferred = deferred
                seen.add(event["request_id"])
                self.store.append("plan_revision_restored", request_id=event["request_id"],
                                  row_id=row_id, operation=operation, evidence_restored=False)
            except (KeyError, TypeError, ValueError) as exc:
                self.store.append("plan_revision_restore_rejected", detail=str(exc))
                break
        self._plan_requests_seen = seen

    def plan_accounting(self) -> dict:
        """Every plan row named in every dimension, for the journal and the file.

        Assembled here because this is the only object that holds all four
        pieces: the plan, the bound checks' observed states, the predicate
        channel's proofs, and the receipt for the text actually delivered.
        """
        from .persistent_plan.accounting import plan_accounting

        plan = getattr(self, "persistent_plan", None)
        if plan is None:
            return {}
        mapping = getattr(self, "plan_row_predicates", {}) or {}
        unmet = set(self.unmet_predicates)
        proven = [
            row.row_id
            for row in plan.rows
            if mapping.get(row.row_id)
            and all(key in self.predicates and key not in unmet
                    and self.predicate_status(key) == PredicateStatus.GREEN
                    for key in mapping[row.row_id])
        ]
        observations = getattr(self, "_plan_check_observations", {})
        executed = [
            row.row_id
            for row in plan.rows
            if any(key in observations
                   for key, spec in getattr(self, "_check_specs", {}).items()
                   if row.row_id in spec.requirement_ids)
        ]
        return plan_accounting(
            plan,
            states={row.row_id: self.plan_row_state(row.row_id) for row in plan.rows},
            proven=proven,
            executed=executed,
            rendering=getattr(self, "plan_rendering_receipt", {}) or {},
        )

    def publish_plan_state(self) -> None:
        from gt_harness.canonical_io import atomic_json

        plan = getattr(self, "persistent_plan", None)
        if plan is None:
            return
        ledger = getattr(plan.inputs, "ledger", None)
        payload = {
            "plan_digest": hashlib.sha256(plan.canonical_json().encode()).hexdigest(),
            "source_revision": self.repository_revision,
            "source_spans": [list(span) for span in ledger.source_spans] if ledger is not None else [],
            "unclassified_spans": [list(span) for span in ledger.unclassified_spans] if ledger is not None else [],
            "rows": [{**row.as_dict(), "state": self.plan_row_state(row.row_id),
                      "pending_interactions": [list(cell) for cell in plan.pending_interactions(row.row_id)],
                      "source": (ledger.by_id(row.row_id).as_dict()
                                 if ledger is not None and ledger.by_id(row.row_id) else {})}
                     for row in plan.rows],
        }
        payload["accounting"] = self.plan_accounting()
        state_digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if state_digest != getattr(self, "_last_plan_state_digest", None):
            atomic_json(self.store.root / "plan" / "current.json", payload)
            self._last_plan_state_digest = state_digest
            # The file is the readable copy; the journal is the auditable one.
            # Only the scalars go in a row, with the digest that ties them back
            # to the full lists in the file and to the delivered rendering.
            accounting = payload["accounting"]
            if accounting:
                self.store.append(
                    "plan_accounting",
                    accounting_layout=accounting["schema"],
                    plan_digest=accounting["plan_digest"],
                    process_id=accounting["process_id"],
                    rendered_sha256=accounting["exposure"]["rendered_sha256"],
                    state_digest=state_digest,
                    **accounting["counts"],
                )

    def apply_plan_requests(self) -> list[dict]:
        """Admit CLI proposals through the single journal/engine owner.

        Returns one typed outcome per request read this pass. The CLI already
        answered "requested", so the journal row alone leaves the agent blind
        to a refusal; the session renders rejections into the next batch.
        """
        from dataclasses import replace

        self._restore_plan_revisions()
        plan = getattr(self, "persistent_plan", None)
        if plan is None:
            return []
        seen = getattr(self, "_plan_requests_seen", set())
        root = self.store.root / "plan" / "requests"
        # The base every request in this batch could have read. `plan/current.json`
        # is rewritten once, after the whole queue drains, so two `gt-plan revise`
        # calls in one turn necessarily carry the SAME digest. Comparing each
        # against a digest that moves as the batch applies rejected everything
        # after the first -- and silently, from the agent's side, because the CLI
        # had already answered "requested". Staleness is unchanged for a request
        # authored against an earlier turn: that digest is not this one.
        published = hashlib.sha256(plan.canonical_json().encode()).hexdigest()
        outcomes: list[dict] = []
        for path in sorted(root.glob("*.json")):
            if path.name in seen:
                continue
            seen.add(path.name)
            operation, row_id = "", ""
            try:
                request = json.loads(path.read_text(encoding="utf-8"))
                # The journal still chains on the TRUE pre-application digest,
                # so a batch replays through its own intermediate states.
                digest = hashlib.sha256(plan.canonical_json().encode()).hexdigest()
                if request["plan_digest"] != published:
                    raise ValueError("stale plan revision")
                row_id = request["row_id"]
                row = plan.row(row_id)
                if row is None:
                    raise ValueError("unknown plan row")
                operation, value = request["operation"], request.get("value", {})
                if not isinstance(value, dict):
                    raise ValueError("plan request value must be an object")
                if operation == "revise":
                    value = self._validated_design_revision(value)
                    applied = {**value, **cleared_check_provenance(value)}
                    plan.rows = tuple(replace(r, **applied) if r.row_id == row_id else r for r in plan.rows)
                    # A changed design/check invalidates its old check bindings.
                    specs = getattr(self, "_check_specs", {})
                    self._check_specs = {
                        key: replace(spec, requirement_ids=tuple(
                            identity for identity in spec.requirement_ids if identity != row_id
                        )) for key, spec in specs.items()
                        if any(identity != row_id for identity in spec.requirement_ids)
                    }
                    self._pending_check_ids = getattr(self, "_pending_check_ids", set()) & self._check_specs.keys()
                elif operation == "defer":
                    reason = request.get("reason")
                    if not isinstance(reason, str) or not reason.strip():
                        raise ValueError("deferral requires a reason")
                    deferred = getattr(self, "_plan_deferred", {})
                    deferred[row_id] = reason
                    self._plan_deferred = deferred
                elif operation == "bind-check":
                    self.bind_plan_check({**value, "requirement_ids": [row_id]})
                else:
                    raise ValueError("unsupported plan operation")
                self.store.append("plan_revision_applied", request_id=path.name, operation=operation,
                                  row_id=row_id, previous_plan_digest=digest,
                                  revision_layout="gt.plan_revision.v1", value=value,
                                  reason=request.get("reason", ""),
                                  resulting_plan_digest=hashlib.sha256(plan.canonical_json().encode()).hexdigest())
                outcomes.append({"request_id": path.name, "outcome": "applied",
                                 "operation": str(operation), "row_id": str(row_id), "detail": ""})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                detail = str(exc)
                self.store.append("plan_revision_rejected", request_id=path.name, detail=detail)
                outcomes.append({"request_id": path.name, "outcome": "rejected",
                                 "operation": str(operation), "row_id": str(row_id), "detail": detail})
        self._plan_requests_seen = seen
        self.publish_plan_state()
        return outcomes

    def _rebind_check_source(self, spec: Any, digest: str) -> Any:
        """Rebind a check spec to the workspace's current test-source identity.

        The spec's meaning lives in argv+cwd+requirement bindings; the digest
        names the test source it must run against. When the workspace's test
        surface changed after binding (the agent edited tests, or the spec
        bound before the surface fallback existed), the honest update is to
        rebind to the source the check will actually run against - keeping the
        same requirement rows. The old identity is removed so state lookups
        cannot observe a spec that can no longer execute.
        """
        from dataclasses import replace

        from .persistent_plan.checks import CheckSpec

        data = spec.as_dict()
        data.pop("check_id")
        rebound = CheckSpec.from_dict(
            {**data, "test_source_digest": digest}, self.repo_root
        )
        specs = getattr(self, "_check_specs", {})
        specs.pop(spec.check_id, None)
        if rebound.check_id in specs:
            rebound = replace(rebound, requirement_ids=tuple(sorted(
                set(rebound.requirement_ids)
                | set(specs[rebound.check_id].requirement_ids))))
        specs[rebound.check_id] = rebound
        self._check_specs = specs
        pending = getattr(self, "_pending_check_ids", set())
        if spec.check_id in pending:
            pending.discard(spec.check_id)
            pending.add(rebound.check_id)
            self._pending_check_ids = pending
        self.store.append(
            "plan_check_rebound", previous_check_id=spec.check_id,
            **rebound.as_dict(),
        )
        return rebound

    def drain_plan_checks(self, environment: Any, *, budget_seconds: float = 30) -> None:
        """Execute coalesced argv checks through the task's isolation boundary."""
        if os.environ.get("GT_VERIFY_EXECUTE", "").strip() != "1":
            return
        from .persistent_plan.baseline import _parse
        from .persistent_plan.checks import (
            classify_bound_check,
            pytest_collection_mismatch,
            pytest_importlib_argv,
            validation_source_digest,
        )
        from .runtime_observation import (
            capture_workspace,
            compile_execution_evidence,
            diff_workspace,
        )

        self._observe_process_rows(environment)
        deadline = time.monotonic() + min(budget_seconds, self.REVERIFY_PASS_BUDGET_SECONDS)
        pending = getattr(self, "_pending_check_ids", set())
        for check_id in sorted(tuple(pending)):
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            spec = self._check_specs[check_id]
            # A native environment lacking explicit isolation is not an
            # authorized automatic executor. The normal agent remains usable.
            if not callable(getattr(environment, "execution_env", None)):
                break
            # A spec that failed to bind at this exact workspace revision
            # retries deterministically-identically: same content, same
            # empty digest. Skip the workspace capture entirely - only a
            # new revision can change the outcome. When the record lags the
            # workspace the comparison misses and the spec retries, which is
            # the safe direction.
            unbound = getattr(self, "_unbound_check_revisions", {})
            if unbound.get(check_id) == self.repository_revision:
                continue
            before = capture_workspace(self.repo_root, excluded_roots=(self.store.root,))
            current_digest = validation_source_digest(spec, before)
            if not current_digest:
                # The spec cannot bind yet - most often the test file it
                # names does not exist. Discarding here meant a check bound
                # before its test existed could never run, which is how the
                # smoke cohort lost every bound check. It stays pending and
                # retries on the next revision; the journal row fires once
                # per binding identity rather than once per drain cycle.
                if check_id not in unbound:
                    self.store.append("plan_check_binding_pending",
                                      check_id=check_id,
                                      reason="test_source_not_bound")
                unbound[check_id] = before.revision
                self._unbound_check_revisions = unbound
                continue
            if check_id in unbound:
                unbound.pop(check_id, None)
                self._unbound_check_revisions = unbound
            if current_digest != spec.test_source_digest:
                spec = self._rebind_check_source(spec, current_digest)
                check_id = spec.check_id
            self.record_repository_snapshot(before, boundary="before_auto_check")
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            result = None
            try:
                result = environment.execute(
                    {"command": spec.command, "argv": list(spec.argv)},
                    cwd=str(Path(self.repo_root) / spec.cwd),
                    timeout=max(1, int(min(remaining, self.REVERIFY_COMMAND_TIMEOUT_SECONDS))),
                )
                # The declared argv may be physically uncollectable: pytest
                # refuses same-basename test modules in one invocation
                # ("import file mismatch"). That is an instrument defect,
                # not tree evidence - every dynaconf-style check failed every
                # revision because no assertion ever ran. Retry once inside
                # the same capture window under importlib import mode, which
                # names modules by path, and let that verdict classify. The
                # spec identity (and command_sha256) stays the declared
                # command; the accommodation is journaled with both argvs.
                preview_extra = result.get("extra") or {}
                preview_ref = preview_extra.get("output_artifact")
                preview_output = (environment.evidence_store.bytes(
                    preview_ref["sha256"]).decode("utf-8", "replace")
                    if preview_ref else str(result.get("output", "")))
                if (spec.protocol == "pytest"
                        and pytest_collection_mismatch(preview_output)
                        and deadline - time.monotonic() >= 1):
                    accommodated = pytest_importlib_argv(spec.argv)
                    retry = environment.execute(
                        {"command": shlex.join(accommodated),
                         "argv": list(accommodated)},
                        cwd=str(Path(self.repo_root) / spec.cwd),
                        timeout=max(1, int(min(
                            deadline - time.monotonic(),
                            self.REVERIFY_COMMAND_TIMEOUT_SECONDS))),
                    )
                    self.store.append(
                        "plan_check_argv_accommodated",
                        check_id=spec.check_id,
                        reason="pytest_import_file_mismatch",
                        declared_argv=list(spec.argv),
                        executed_argv=list(accommodated),
                    )
                    result = retry
            except Exception as exc:  # an automatic check cannot submit the task
                self.store.append("plan_check_execution_failed", check_id=check_id,
                                  error_type=type(exc).__name__)
            finally:
                after = capture_workspace(self.repo_root, excluded_roots=(self.store.root,))
                transaction = diff_workspace(before, after, action_id=self.global_action,
                                             command=spec.command)
                self.record_repository_snapshot(after, boundary="after_auto_check")
                self.record_edit_transaction(transaction)
                if transaction.changes:
                    if self.phase != "IMPLEMENT":
                        self.begin_implement()
                    self.note_edit(transaction.changed_paths)
                self._automatic_check_generation = getattr(self, "_automatic_check_generation", 0) + 1
            if result is None:
                pending.discard(check_id)
                continue
            extra = result.get("extra") or {}
            self._current_check_environment_sha256 = str(extra.get("environment_sha256", ""))
            if extra.get("surviving_descendants"):
                self._background_writers_seen = True
            reference = extra.get("output_artifact")
            output = (environment.evidence_store.bytes(reference["sha256"]).decode("utf-8", "replace")
                      if reference else str(result.get("output", "")))
            execution = compile_execution_evidence(
                command=spec.command, output=output, returncode=result.get("returncode"),
                action_id=self.global_action, repository_revision=after.revision,
                timed_out=bool(extra.get("timed_out")),
                environment_sha256=str(extra.get("environment_sha256", "")),
            )
            _, passing, _ = _parse(output, spec.argv)
            observation = classify_bound_check(
                spec, execution, before_revision=before.revision, after_revision=after.revision,
                capture_complete=(extra.get("capture_complete") is True
                                  and before.complete and after.complete),
                test_ids=tuple(passing),
                test_source_digest=validation_source_digest(spec, after),
            )
            observations = getattr(self, "_plan_check_observations", {})
            observations[check_id] = observation
            self._plan_check_observations = observations
            if observation.state == "CHECK_PASSED":
                self._record_check_pass_receipts(
                    spec, result.get("returncode"), output
                )
            pending.discard(check_id)
            self.store.append("plan_check_observed", **asdict(observation))
        # Behavioral program checks drain verbatim through the same isolation
        # boundary - the program text is the spec, exit status the verdict.
        for check_id in sorted(tuple(
                getattr(self, "_pending_program_check_ids", set()))):
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            spec = self._program_check_specs[check_id]
            if not callable(getattr(environment, "execution_env", None)):
                break
            before = capture_workspace(self.repo_root, excluded_roots=(self.store.root,))
            self.record_repository_snapshot(before, boundary="before_auto_check")
            result = None
            try:
                result = environment.execute(
                    {"command": spec.program},
                    cwd=self.repo_root,
                    timeout=max(1, int(min(remaining, self.REVERIFY_COMMAND_TIMEOUT_SECONDS))),
                )
            except Exception as exc:  # an automatic check cannot submit the task
                self.store.append("plan_check_execution_failed", check_id=check_id,
                                  error_type=type(exc).__name__)
            finally:
                after = capture_workspace(self.repo_root, excluded_roots=(self.store.root,))
                transaction = diff_workspace(before, after, action_id=self.global_action,
                                             command=spec.program)
                self.record_repository_snapshot(after, boundary="after_auto_check")
                self.record_edit_transaction(transaction)
                if transaction.changes:
                    if self.phase != "IMPLEMENT":
                        self.begin_implement()
                    self.note_edit(transaction.changed_paths)
                self._automatic_check_generation = getattr(self, "_automatic_check_generation", 0) + 1
            if result is None:
                getattr(self, "_pending_program_check_ids", set()).discard(check_id)
                continue
            extra = result.get("extra") or {}
            self._current_check_environment_sha256 = str(extra.get("environment_sha256", ""))
            reference = extra.get("output_artifact")
            output = (environment.evidence_store.bytes(reference["sha256"]).decode("utf-8", "replace")
                      if reference else str(result.get("output", "")))
            from .persistent_plan.checks import classify_program_check
            from .runtime_observation import program_execution_evidence

            execution = program_execution_evidence(
                command=spec.program, output=output,
                returncode=result.get("returncode"),
                action_id=self.global_action, repository_revision=after.revision,
                timed_out=bool(extra.get("timed_out")),
                environment_sha256=str(extra.get("environment_sha256", "")),
            )
            observation = classify_program_check(
                spec, execution, before_revision=before.revision,
                after_revision=after.revision,
                capture_complete=(extra.get("capture_complete") is True
                                  and before.complete and after.complete),
            )
            observations = getattr(self, "_plan_check_observations", {})
            observations[check_id] = observation
            self._plan_check_observations = observations
            if observation.state == "CHECK_PASSED":
                self._record_check_pass_receipts(
                    spec, result.get("returncode"), output
                )
            getattr(self, "_pending_program_check_ids", set()).discard(check_id)
            self.store.append("plan_check_observed", **asdict(observation))
        self.publish_plan_state()

    def _stale_plan_check_ids(self) -> dict[str, str]:
        """Bound checks lacking conclusive evidence on the submitted tree.

        Maps check_id -> pending-set attribute so argv and program checks
        re-pend through the same drain path. Three gaps count: no observation
        at all, an observation whose source revision or environment predates
        the current tree, and a current-revision observation that classified
        UNVERIFIED - inconclusive, most often because the check's own side
        effects (a pytest cache appearing mid-run) moved the tree under it;
        side effects are usually idempotent, so one bounded retry converges.
        CHECK_FAILED is never re-pended: negative evidence stands.
        """
        observations = getattr(self, "_plan_check_observations", {})
        environment_sha = getattr(self, "_current_check_environment_sha256", "")
        stale: dict[str, str] = {}
        for attribute, specs in (
                ("_pending_check_ids", getattr(self, "_check_specs", {})),
                ("_pending_program_check_ids",
                 getattr(self, "_program_check_specs", {}))):
            for check_id in specs:
                observation = observations.get(check_id)
                if (observation is None
                        or observation.source_revision != self.repository_revision
                        or observation.environment_sha256 != environment_sha
                        or observation.state == "UNVERIFIED"):
                    stale[check_id] = attribute
        return stale

    def seal_plan_recheck(self, environment: Any, *,
                          budget_seconds: float | None = None,
                          max_passes: int = SEAL_PLAN_RECHECK_PASSES) -> dict:
        """Re-observe bound plan checks against the final submitted tree.

        A check observed at revision R stops being evidence the moment the
        workspace moves to R', and the agent's last verification command
        usually predates its last edit - so rows that honestly passed mid-run
        read UNVERIFIED on the tree that is actually scored, and the submit
        gate's drain cannot fix that when the task's remaining budget no
        longer clears the refusal reserve. At FINISHED the workspace is
        quiescent: re-pending the stale checks and draining them through the
        same isolation boundary produces current-revision evidence. A failed
        recheck stays CHECK_FAILED, a check that cannot run stays UNVERIFIED;
        nothing is relabeled.
        """
        rows = tuple(getattr(getattr(self, "persistent_plan", None), "rows", ()) or ())
        summary: dict[str, Any] = {
            "layout_schema": "gt.plan_seal_recheck.v1",
            "ran": False, "passes": 0, "repended": [],
            "remaining_unverified": [], "reason": "",
        }
        if self.phase != "FINISHED" or not rows:
            summary["reason"] = "no_final_plan"
            return summary
        if (os.environ.get("GT_VERIFY_EXECUTE", "").strip() != "1"
                or not callable(getattr(environment, "execution_env", None))):
            summary["reason"] = "executor_unavailable"
            self.store.append("plan_seal_recheck", **summary)
            return summary
        budget = (self.SEAL_PLAN_RECHECK_BUDGET_SECONDS
                  if budget_seconds is None else max(0.0, budget_seconds))
        deadline = time.monotonic() + budget
        repended: set[str] = set()
        # The drain records check side-effects through the ordinary
        # transaction path, and note_edit refuses any phase but IMPLEMENT.
        # A pytest cache write must not un-submit a finished task, so the
        # seal borrows the phase for the drain's duration and restores it
        # unconditionally.
        self._phase = "IMPLEMENT"
        try:
            while summary["passes"] < max_passes:
                stale = self._stale_plan_check_ids()
                if not stale and not (
                        getattr(self, "_pending_check_ids", set())
                        or getattr(self, "_pending_program_check_ids", set())):
                    break
                for check_id, attribute in stale.items():
                    pending = getattr(self, attribute, None) or set()
                    pending.add(check_id)
                    setattr(self, attribute, pending)
                repended |= set(stale)
                if deadline - time.monotonic() < 1:
                    break
                try:
                    self.drain_plan_checks(
                        environment,
                        budget_seconds=deadline - time.monotonic())
                except Exception as exc:  # noqa: BLE001 - never lose the seal
                    summary["reason"] = f"drain_error:{type(exc).__name__}"
                    break
                summary["passes"] += 1
                summary["ran"] = True
        finally:
            self._phase = "FINISHED"
        summary["repended"] = sorted(repended)
        summary["remaining_unverified"] = [
            row.row_id for row in rows
            if self.plan_row_state(row.row_id) not in {"CHECK_PASSED", "PROVEN"}
        ]
        if not summary["reason"]:
            if summary["remaining_unverified"]:
                summary["reason"] = "still_unverified"
            else:
                summary["reason"] = ("converged" if summary["ran"]
                                     else "already_current")
        self.store.append("plan_seal_recheck", **summary)
        return summary

    def _record_check_pass_receipts(
        self, spec: Any, returncode: Any, output: str
    ) -> None:
        """A bound check passing is evidence for the rows it is bound to.

        Without this the check stayed a row-level fact while the obligation
        predicates behind the row kept their UNKNOWN status - the contract
        delta could never tick and the submit gate kept seeing unmet work.
        Only GREEN is recorded: a failed automatic check (including
        env_fail) is not evidence the obligation is unmet.
        """
        # The receipt's dependency scope is what the bound check declared at
        # bind time. That footprint is still not complete - an import the
        # graph never recorded or a path the test opens outside its declared
        # sources can hide - but it is the declared surface on which the
        # provably-inert narrowing in note_edit may rely.
        declared_sources = tuple(
            DependencyIdentity("path", path)
            for raw in getattr(spec, "test_source_paths", ()) or ()
            if (path := normalize_dependency_path(raw))
        )
        footprint = (
            DependencyFootprint(
                identities=declared_sources,
                complete=False,
                basis="bound_check_declared_test_sources",
            )
            if declared_sources else None
        )
        for row_id in spec.requirement_ids:
            for predicate_id in getattr(self, "plan_row_predicates", {}).get(
                row_id, ()
            ):
                if predicate_id not in self.predicates:
                    continue
                if self.predicate_status(predicate_id) is PredicateStatus.RED:
                    receipt = self._receipts.get(predicate_id)
                    observed = str(
                        getattr(receipt, "source_revision_at_observation", "")
                        or ""
                    )
                    current = str(self.repository_revision or "")
                    if not observed or observed == current:
                        # The channels are independent: a passing check
                        # discharges the row's check obligation but cannot
                        # rewrite a live RED semantic receipt -- a current
                        # failure hidden by a later GREEN is exactly the
                        # precedence unmet_plan_rows enforces.
                        continue
                    # The RED was observed on a revision the tree has since
                    # left, and this check just passed on the submitted tree.
                    # A stale failure cannot outweigh current positive
                    # evidence; otherwise every agent that ever ran a failing
                    # test before fixing it could never verify.
                self.record_receipt(
                    predicate_id,
                    spec.command,
                    returncode if isinstance(returncode, int) else 0,
                    output,
                    epoch=self.workspace_epoch,
                    status="GREEN",
                    semantic=True,
                    dependency_footprint=footprint,
                    evidence_kind="bound_check",
                    coverage_basis="plan_check_observation",
                    source_revision_at_observation=self.repository_revision,
                    action_index=self.global_action,
                )

    def _observe_process_rows(self, environment: Any) -> None:
        """Discharge workflow-directive rows from workspace git state.

        A directive such as "work on a new branch ... and commit everything
        when you are done" is normative -- a submission that never commits
        grades against a pristine base -- but no test invocation can evidence
        it. Its honest channel is the submission envelope itself: a clean
        working tree on a named non-main branch, probed at each verification
        boundary so work edited after the last commit re-opens the row.
        """
        rows = [row for row in getattr(self.persistent_plan, "rows", ())
                if _is_process_directive(getattr(row, "text", ""))]
        if not rows or not callable(getattr(environment, "execution_env", None)):
            return
        captured: dict[str, tuple[str, str]] = {}
        for name, argv in (("branch", ["git", "branch", "--show-current"]),
                           ("porcelain", ["git", "status", "--porcelain"])):
            try:
                result = environment.execute(
                    {"command": shlex.join(argv), "argv": argv},
                    cwd=self.repo_root, timeout=15)
            except Exception as exc:  # the probe must never block the decision
                self.store.append("plan_process_row_probe_failed",
                                  error_type=type(exc).__name__)
                return
            extra = result.get("extra") or {}
            reference = extra.get("output_artifact")
            output = (environment.evidence_store.bytes(
                reference["sha256"]).decode("utf-8", "replace")
                if reference else str(result.get("output", "")))
            if result.get("returncode") != 0:
                self.store.append("plan_process_row_probe_failed",
                                  error_type="nonzero_returncode",
                                  returncode=result.get("returncode"))
                return
            captured[name] = (output, str(extra.get("environment_sha256", "")))
        branch = captured["branch"][0].strip().lower()
        clean = not captured["porcelain"][0].strip()
        on_named_branch = branch not in {"", "main", "master"}
        observations = getattr(self, "_process_row_observations", {})
        for row in rows:
            text = getattr(row, "text", "").lower()
            needs_branch = "branch" in text
            needs_commit = any(
                marker in text for marker in ("commit", "submit", "push"))
            # A pull-request instruction is discharged outside the workspace;
            # git state cannot evidence it and the row stays honestly open.
            unprovable_claim = "pull request" in text
            state = ("PROVEN"
                     if not unprovable_claim
                     and (not needs_branch or on_named_branch)
                     and (not needs_commit or clean)
                     else "UNVERIFIED")
            observations[row.row_id] = {
                "state": state, "source_revision": self.repository_revision,
            }
            self.store.append(
                "plan_process_row_observed", row_id=row.row_id, state=state,
                branch=branch, clean_tree=clean, needs_branch=needs_branch,
                needs_commit=needs_commit, unprovable_claim=unprovable_claim,
                source_revision=self.repository_revision,
                environment_sha256=captured["porcelain"][1],
            )
            if state == "PROVEN":
                self._record_process_pass_receipts(
                    row.row_id, captured["porcelain"][0])
        self._process_row_observations = observations

    def _record_process_pass_receipts(self, row_id: str, output: str) -> None:
        """A proven workflow directive is evidence for its mapped predicates."""
        for predicate_id in getattr(self, "plan_row_predicates", {}).get(
            row_id, ()
        ):
            if predicate_id not in self.predicates:
                continue
            if self.predicate_status(predicate_id) is PredicateStatus.RED:
                continue
            self.record_receipt(
                predicate_id,
                "git status --porcelain && git branch --show-current",
                0,
                output,
                epoch=self.workspace_epoch,
                status="GREEN",
                semantic=True,
                evidence_kind="process_observation",
                coverage_basis="plan_process_row_observed",
                source_revision_at_observation=self.repository_revision,
                action_index=self.global_action,
            )

    def plan_row_state(self, row_id: str) -> str:
        process = getattr(self, "_process_row_observations", {}).get(row_id)
        if (process is not None
                and process["source_revision"] == self.repository_revision):
            return process["state"]
        observations = getattr(self, "_plan_check_observations", {})
        bound = [
            key
            for key, spec in list(getattr(self, "_check_specs", {}).items())
            + list(getattr(self, "_program_check_specs", {}).items())
            if row_id in spec.requirement_ids
        ]
        states = [observations[key].state for key in bound if key in observations
                  and observations[key].source_revision == self.repository_revision
                  and observations[key].environment_sha256 == getattr(
                      self, "_current_check_environment_sha256", "")]
        if "CHECK_FAILED" in states:
            return "CHECK_FAILED"
        if states and len(states) == len(bound) and all(state == "CHECK_PASSED" for state in states):
            return "CHECK_PASSED"
        if row_id in getattr(self, "_plan_deferred", {}):
            return "DEFERRED"
        return "UNVERIFIED"

    def observe_plan_checks(self, command: str, result: dict, before: Any, after: Any,
                            environment: Any) -> None:
        """An equivalent agent-run check discharges the pending automatic run."""
        from .persistent_plan.baseline import _parse
        from .persistent_plan.checks import classify_bound_check, validation_source_digest
        from .runtime_observation import compile_execution_evidence

        if before is None or after is None:
            return
        from .persistent_plan.checks import decompose_check_command

        extra = result.get("extra") or {}
        self._current_check_environment_sha256 = str(extra.get("environment_sha256", ""))
        # A composite agent command (``cd tests && pytest a``) still discharges
        # each bound segment: decompose it into (argv, effective cwd) pairs the
        # same way binding decomposes a row's verification_command.
        base_cwd = str(extra.get("cwd") or Path(self.repo_root).resolve())
        segments, separators, last_raw_is_check, _ = decompose_check_command(command)
        rc = result.get("returncode")
        all_and = separators and all(sep == "&&" for sep in separators)
        all_semi = separators and all(sep == ";" for sep in separators)
        attributable: set[int] = set()
        if segments:
            if len(segments) == 1 and (not separators or last_raw_is_check):
                attributable.add(0)
            elif all_and and rc == 0:
                # every segment ran and exited 0 — all provably passed
                attributable.update(range(len(segments)))
            elif all_semi and last_raw_is_check:
                # a ;-chain's rc is the last raw segment's alone
                attributable.add(len(segments) - 1)
        observed_commands = {}
        for index, (seg_argv, seg_cwd) in enumerate(segments or []):
            if index in attributable:
                key = (shlex.join(seg_argv), str((Path(base_cwd) / (seg_cwd or ".")).resolve()))
                observed_commands[key] = shlex.join(seg_argv)
        if not segments:
            observed_commands.setdefault((command, base_cwd), command)
        observed = False
        for check_id, spec in tuple(getattr(self, "_check_specs", {}).items()):
            matched = observed_commands.get(
                (spec.command, str((Path(self.repo_root) / spec.cwd).resolve()))
            )
            if matched is None:
                continue
            reference = extra.get("output_artifact")
            output = (environment.evidence_store.bytes(reference["sha256"]).decode("utf-8", "replace")
                      if reference else str(result.get("output", "")))
            execution = compile_execution_evidence(
                command=matched, output=output, returncode=result.get("returncode"),
                action_id=self.global_action, repository_revision=after.revision,
                timed_out=bool(extra.get("timed_out")),
                environment_sha256=str(extra.get("environment_sha256", "")),
            )
            _, passing, _ = _parse(output, spec.argv)
            after_digest = validation_source_digest(spec, after)
            if after_digest and after_digest != spec.test_source_digest:
                # The test surface moved since binding (or the spec bound
                # before its source could be identified): rebind to the
                # source the agent's command actually ran against, then
                # classify - the observation is keyed to the rebound id.
                spec = self._rebind_check_source(spec, after_digest)
                check_id = spec.check_id
            observation = classify_bound_check(
                spec, execution, before_revision=before.revision, after_revision=after.revision,
                capture_complete=(extra.get("capture_complete") is True and before.complete and after.complete),
                test_ids=tuple(passing),
                test_source_digest=after_digest,
            )
            observations = getattr(self, "_plan_check_observations", {})
            observations[check_id] = observation
            self._plan_check_observations = observations
            if observation.state == "CHECK_PASSED":
                self._record_check_pass_receipts(
                    spec, result.get("returncode"), output
                )
            if observation.state in {"CHECK_PASSED", "CHECK_FAILED"}:
                getattr(self, "_pending_check_ids", set()).discard(check_id)
            self.store.append("plan_check_observed", **asdict(observation))
            observed = True
        # A bound behavioral program discharges when the agent runs its exact
        # text - verbatim identity, same revision+environment binding.
        for check_id, spec in tuple(
                getattr(self, "_program_check_specs", {}).items()):
            if command.strip() != spec.program:
                continue
            reference = extra.get("output_artifact")
            output = (environment.evidence_store.bytes(reference["sha256"]).decode("utf-8", "replace")
                      if reference else str(result.get("output", "")))
            from .persistent_plan.checks import classify_program_check
            from .runtime_observation import program_execution_evidence

            execution = program_execution_evidence(
                command=spec.program, output=output,
                returncode=result.get("returncode"),
                action_id=self.global_action, repository_revision=after.revision,
                timed_out=bool(extra.get("timed_out")),
                environment_sha256=str(extra.get("environment_sha256", "")),
            )
            observation = classify_program_check(
                spec, execution, before_revision=before.revision,
                after_revision=after.revision,
                capture_complete=(extra.get("capture_complete") is True
                                  and before.complete and after.complete),
            )
            observations = getattr(self, "_plan_check_observations", {})
            observations[check_id] = observation
            self._plan_check_observations = observations
            if observation.state == "CHECK_PASSED":
                self._record_check_pass_receipts(
                    spec, result.get("returncode"), output
                )
            if observation.state in {"CHECK_PASSED", "CHECK_FAILED"}:
                getattr(self, "_pending_program_check_ids", set()).discard(check_id)
            self.store.append("plan_check_observed", **asdict(observation))
            observed = True
        if observed:
            self.publish_plan_state()

    def note_edit(self, paths: Iterable[str]) -> None:
        normalized_paths = tuple(str(p) for p in paths)
        affected = set(self._affected_predicate_ids(normalized_paths))
        active_red = {
            predicate_id
            for predicate_id, status in self._status.items()
            if status is PredicateStatus.RED
        }
        affected.update(active_red)
        # Record WHICH obligations this edit discarded, and which survived it.
        # This is the most consequential state transition in a run and it was
        # journaled only by its consequence: run 34095557374 shows `unmet`
        # falling to 3 of 18 at iteration 90 and returning to 18, sixteen times,
        # with no row anywhere saying what was invalidated or why. The
        # trajectory had to be reconstructed by pairing `state` rows against
        # `edit_transaction` rows on sequence adjacency - which got it wrong on
        # the first attempt, in the direction of a more dramatic finding.
        #
        # `red_invalidated_by_edit` below covers only RED predicates and was
        # correctly silent here, because none were RED. Nothing covered the
        # GREEN ones, which are the ones whose loss costs the run its steps.
        #
        # It also decides a question the artifact currently cannot: whether the
        # scope test is narrow and every obligation genuinely depends on the
        # edited tree, or whether it matches everything regardless. Those imply
        # different owners - a product choice about what a ledger is for, versus
        # a defect in _affected_predicate_ids - and one journal row separates
        # them.
        proven_before = {
            predicate_id
            for predicate_id, status in self._status.items()
            if status is PredicateStatus.GREEN
        }
        # Every proof this edit could discard, WITH the command that proved it,
        # captured before the controller resets the statuses. After the reset
        # the receipts are gone, so this is the only moment the pair exists.
        #
        # NOT filtered by `affected`. That was the defect: candidacy was decided
        # by _affected_predicate_ids, which requires the obligation's English
        # text to literally quote a filename, while the invalidation that
        # actually runs is footprint-based and matches every edit. The two rules
        # never intersected, so `obligation_reverified` produced zero rows in
        # every run ever recorded while proofs were being discarded 33 times.
        # The applied set below decides candidacy now, so the rule that destroys
        # a proof is the same rule that offers to re-establish it.
        proven_commands = {
            predicate_id: (receipt.command or "")
            for predicate_id, receipt in self._receipts.items()
            if self._status.get(predicate_id) is PredicateStatus.GREEN
        }
        applied = set(super().note_edit(normalized_paths, invalidate=affected))
        # M7: a suite verdict taken before this edit does not describe the tree
        # the agent is now working on, so it must stop opening the submit
        # window. Placed after the lifecycle call: an edit the controller
        # refused never landed, and so invalidates nothing.
        if self._suite_verdict_ledger is not None:
            self._suite_verdict_ledger.note_edit(normalized_paths)
        # Reported AFTER the reset, against what the reset actually did.
        # Reporting `affected` here was not merely imprecise: with an empty
        # scope match it claimed every proof survived an edit that had already
        # wiped them all, which is the opposite of what happened.
        # `scope_matched` is retained so the narrow-versus-total question this
        # row was added to settle is still answerable from one row.
        try:
            self.store.append(
                "obligation_invalidation",
                paths=list(normalized_paths),
                epoch=self.workspace_epoch,
                invalidated=sorted(applied),
                scope_matched=sorted(affected),
                proven_before=sorted(proven_before),
                proven_discarded=sorted(proven_before & applied),
                proven_surviving=sorted(proven_before - applied),
                predicate_total=len(self._status),
            )
        except Exception:  # noqa: BLE001 - reporting never fails an edit
            pass
        self._reverify_after_edit(
            {
                predicate_id: command
                for predicate_id, command in proven_commands.items()
                if predicate_id in applied
            }
        )
        if self._pending_recovery is not None:
            self.store.append("recovery_invalidated", epoch=self.workspace_epoch)
            self._pending_recovery = None
            self.pending_transient = ""
            self._pending_provider_deliveries = [
                item for item in self._pending_provider_deliveries if item.kind != "recovery"
            ]
        if self._pending_churn_steer:
            # The edit is the steer working: an undelivered steer is moot.
            self.store.append("churn_steer_invalidated", epoch=self.workspace_epoch)
            self._pending_churn_steer = ""
            self._pending_provider_deliveries = [
                item for item in self._pending_provider_deliveries
                if item.kind != "churn_steer"
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
            # The synchronous amend inside record_edit_transaction can already
            # have adopted this exact edit: apply_transaction filled the
            # overlay, the amend consumed it, and publish_graph cleared it
            # before note_edit ran. An engine that is current BECAUSE the
            # transaction epoch was adopted holds these paths in the graph, so
            # re-dirtying would journal an invalidation after the publication
            # that closed it and force a deduplicated second amend. A current
            # engine reached any other way - a snapshot bind, a re-badge, an
            # adopted startup index - has no transaction saying these paths
            # are in the graph, and a bare note_edit still means stale. The
            # path set is part of the proof: note_edit can also run on
            # heuristic paths when the diff could not enumerate changes (an
            # incomplete or empty-changes transaction skips
            # record_edit_transaction entirely), and those were never amended
            # into anything.
            adopted = (
                self.engine_state.graph_current
                and self._edit_epoch == self._adopted_edit_epoch
                and set(normalized_paths) <= self._adopted_edit_paths
            )
            if not adopted:
                if not self.engine_state.query_snapshot().overlay:
                    self.engine_state.mark_paths_dirty(
                        normalized_paths,
                        revision=self.repository_revision or f"epoch:{self.workspace_epoch}",
                    )
                self.graph_stale_since_revision = self.repository_revision
                self.store.append(
                    "graph_invalidated",
                    paths=list(normalized_paths),
                    repository_revision=self.repository_revision,
                    graph_db_sha256=hashlib.sha256(
                        str(self.graph_db).encode("utf-8")
                    ).hexdigest(),
                )
            # GatewayState captures graph_db at construction. Drop the cached
            # wrapper immediately so automatic evidence cannot keep reading a
            # pre-edit graph while the adapter correctly reports it stale.
            # The persistent EpisodeState is retained and reattached lazily.
            self._gateway_state = None
        self._record_state()

    def record_repository_snapshot(self, snapshot: Any, *, boundary: str) -> None:
        """Persist a content-addressed revision witness outside the workspace."""
        encoded = snapshot.canonical_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("repository_snapshots", digest, encoded)
        previous = self._latest_workspace_snapshot
        self.repository_revision = str(snapshot.revision)
        self._latest_workspace_snapshot = snapshot
        self.engine_state.bind_initial_source(self.repository_revision)
        if previous is not None and not self.engine_state.graph_current:
            # A snapshot is not a transaction: bind_initial_source advanced
            # the source revision without writing overlay entries, so the
            # graph went stale with an empty dirty set -- and the boundary
            # amend reads "nothing masked" as a stale marking and never
            # resyncs. The delta between two certified witnesses IS the
            # enumeration the overlay needs; a path already covered by an
            # intervening transaction simply re-reads identically.
            prior = {
                str(item.path): (str(item.kind), str(item.sha256 or ""))
                for item in previous.files
            }
            current = {
                str(item.path): (str(item.kind), str(item.sha256 or ""))
                for item in snapshot.files
            }
            changed = tuple(sorted(
                path for path in set(prior) | set(current)
                if prior.get(path) != current.get(path)
            ))
            if changed:
                self.engine_state.mark_paths_dirty(
                    changed, revision=self.repository_revision
                )
            else:
                state = self.engine_state.query_snapshot()
                if not state.masked_paths:
                    basis = self.engine_state.graph_source_revision
                    if (
                        basis
                        and basis == str(previous.revision)
                        and not state.omissions
                    ):
                        # The file tree is provably identical to the adopted
                        # graph's basis tree -- only git history moved. The
                        # certified graph still describes exactly these
                        # files, so it carries the new revision directly
                        # rather than rebuild identical input.
                        if self.engine_state.publish_graph(
                            graph_path=self.engine_state.graph_path,
                            graph_revision=self.engine_state.graph_revision,
                            source_revision=self.repository_revision,
                        ):
                            self.store.append(
                                "graph_rebadged",
                                repository_revision=self.repository_revision,
                                graph_revision=self.engine_state.graph_revision,
                            )
                    else:
                        # Files are identical to the previous witness but the
                        # adopted graph's basis is an unreconstructable tree
                        # -- the advance cannot be enumerated against what
                        # the graph covers.
                        self.engine_state.mark_source_unenumerated(
                            revision=self.repository_revision,
                            reason="source_advance_unenumerated",
                        )
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
        # parent_graph_sha256/build_mode are what let the capability
        # report walk publication ancestry: an amend's manifest names the
        # graph it derived from, so a tier minted by an older promotion or
        # a salvage merge is provably present on every descendant - and a
        # publication with no recorded parent is a fresh build, which is
        # where the walk must stop.
        self.store.append(
            "graph_publication", artifact_sha256=manifest_digest,
            graph_sha256=payload["graph_sha256"],
            repository_revision=self.engine_state.source_revision,
            parent_graph_sha256=payload.get("parent_graph_sha256"),
            build_mode=payload.get("build_mode"),
        )
        self._last_graph_publication = identity

    def record_edit_transaction(self, transaction: Any) -> None:
        encoded = transaction.canonical_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        self.store.put_blob("edit_transactions", digest, encoded)
        self.repository_revision = str(transaction.post_revision)
        self.engine_state.apply_transaction(transaction)
        self._edit_epoch += 1
        for path in transaction.changed_paths:
            self._path_edit_epochs[str(path)] = self._edit_epoch
        if not transaction.complete or transaction.omissions:
            # An unenumerated change poisons the stale-set computation for
            # every enrichment scheduled before it: what moved is not
            # known, so "untouched since" cannot be answered.
            self._incomplete_edit_epoch = self._edit_epoch
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
        self._sync_amend_graph(transaction)
        if self.engine_state.graph_current:
            self._adopted_edit_epoch = self._edit_epoch
            self._adopted_edit_paths = {
                str(path) for path in transaction.changed_paths
            }

    #: Caps for the synchronous amend at the transaction boundary. The amend
    #: copies the certified parent, reruns the producer over the dirty paths,
    #: recomputes the source-manifest key, and certifies - seconds for a small
    #: edit on a small graph, far longer for a mass rewrite on a multi-GB
    #: graph. Past either cap the edit still amends, on the coordinator's
    #: clock: the chain is never broken, only its synchrony varies.
    SYNC_AMEND_MAX_PATHS = 12
    SYNC_AMEND_MAX_GRAPH_BYTES = 512 * 1024 * 1024
    SYNC_AMEND_EMBEDDING_BUDGET_SECONDS = 8.0

    def _sync_amend_graph(self, transaction: Any) -> None:
        """Patch the adopted graph with every path dirtied since its revision.

        The engine property: after an edit lands, the graph IS the new
        workspace state rather than a rebuild queued behind it. A bounded
        dirty set amends inside the transaction boundary and publish_graph
        clears the overlay apply_transaction just wrote - graph_current stays
        true THROUGH the edit instead of false until a worker lands.

        The dirty set is the engine's overlay, not just this transaction's
        paths: publishing a graph that covers only the latest edit would
        clear the overlay on top of changes that never reached any graph,
        which is silent staleness - the failure this path exists to remove.
        """
        if (not bool(transaction.complete) or not self.repo_root
                or self._startup_index is not None):
            return
        snapshot = self.engine_state.query_snapshot()
        if any(value != "transaction_bytes_unavailable"
               for value in snapshot.omissions):
            # The overlay is not the whole dirty state: an earlier incomplete
            # transaction recorded unenumerated changes into omissions, and
            # publish_graph would clear them on top of a graph that never
            # covered them. The serving boundary resyncs on the same data.
            # A lone transaction_bytes_unavailable is different: every dirty
            # path IS enumerated -- only its byte record is missing -- and
            # the producer re-reads live bytes, so the amend still covers
            # the whole dirty set.
            return
        dirty = tuple(sorted(snapshot.masked_paths))
        if not dirty or len(dirty) > self.SYNC_AMEND_MAX_PATHS:
            return
        # The parent is the adopted graph, or the superseded startup build
        # held as its certified fallback - same rule _frozen_graph_input
        # uses. The dirty set is complete either way: every change since the
        # parent's revision is transaction-captured into the overlay, and an
        # over-inclusive dirty set only re-parses unchanged bytes.
        parent = self.engine_state.graph_path or self._unadopted_graph[0]
        if not parent:
            return
        try:
            if Path(parent).stat().st_size > self.SYNC_AMEND_MAX_GRAPH_BYTES:
                return
        except OSError:
            return
        if self._amend_spawn_deferred():
            self._journal_amend_deferred(
                phase="transaction", parent=str(parent), dirty=dirty)
            return
        from . import indexer
        from .indexer import _graph_publication_lock

        layout = self.engine_state.layout
        started = time.monotonic()
        try:
            with _graph_publication_lock(layout.graph_root / ".graph.lock"):
                published, reason, results = indexer._ensure_index_incremental_unlocked(
                    str(self.repo_root), layout=layout,
                    parent_graph=Path(parent), changed_paths=dirty,
                    excluded_roots=tuple(layout.excluded_roots),
                )
        except Exception as exc:  # noqa: BLE001 - a refused amend degrades, never raises
            published, reason, results = None, f"{type(exc).__name__}: {exc}"[:200], ()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not published:
            # Not a rebuild trigger. The next serving boundary amends this
            # same dirty set inline; the refusal is journaled so a path that
            # never syncs is visible instead of silently slow.
            self.store.append(
                "graph_sync_amend_refused", reason=reason[:200],
                dirty_paths=list(dirty), parent_graph=str(parent),
                post_revision=str(transaction.post_revision),
            )
            self._note_amend_failure(reason)
            return
        receipt = indexer._receipt_for_published_graph(
            published, source_revision=str(transaction.post_revision),
            embedding_budget_seconds=self.SYNC_AMEND_EMBEDDING_BUDGET_SECONDS,
            layout=layout, build_mode="incremental", incremental_results=results,
        )
        self._adopt_graph_receipt(
            receipt, event="graph_sync_amend", dirty_paths=list(dirty),
            parent_graph=str(parent),
            amended=[dict(row) for row in results],
            elapsed_ms=elapsed_ms,
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
        """Register the post-edit check query for admission-time render.

        The plan used to render here, at transaction time, pinned to the
        pre-edit graph because the post-edit graph did not exist yet. With the
        synchronous amend chain the graph IS current at delivery, so the
        recipe is queued and ``_render_verification_plan_now`` computes the
        blast radius against the graph as it stands when the model reads it.
        ``graph_snapshot`` is accepted for signature stability; admission-time
        state decides resolvability.
        """
        self._pending_verification_candidate = ""
        self._pending_verification_metadata = {}
        self._pending_verification_recipe = {}
        if not self.repo_root:
            return ""
        paths = tuple(sorted({str(path) for path in transaction.changed_paths if path}))
        if not paths:
            return ""
        self._pending_verification_recipe = {
            "kind": "verification_plan",
            "params": {
                "paths": list(paths),
                "transaction_sha256": str(transaction.transaction_sha256),
                "post_revision": str(transaction.post_revision),
            },
        }
        self._pending_verification_metadata = {
            "kind": "verification_plan",
            "dedup_key": f"verification:{transaction.transaction_sha256}",
            "target": paths[0],
            "semantics": "advisory_dependency_graph",
        }
        return ""

    def _render_verification_plan_now(self, params: Mapping[str, Any]) -> str:
        """Render the pending check advice against the CURRENT graph.

        Runs at the admission choke point: entities re-resolve and the plan
        rebuilds from the graph the delivery will actually be bound to, so the
        advice can never describe a superseded dependency shape.
        """
        snapshot = self.graph_query_snapshot()
        if (
            not self.repo_root
            or not snapshot.graph_current
            or not snapshot.graph_path
        ):
            self._recipe_empty_reason = "graph_unavailable"
            return ""
        paths = tuple(sorted({str(path) for path in params.get("paths") or () if path}))
        if not paths:
            self._recipe_empty_reason = "no_changed_paths"
            return ""
        transaction_sha256 = str(params.get("transaction_sha256") or "")
        patch_revision = str(
            self.repository_revision or params.get("post_revision") or ""
        )
        try:
            placeholders = ",".join("?" for _ in paths)
            uri = Path(snapshot.graph_path).resolve().as_uri() + "?mode=ro"
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
                self._recipe_empty_reason = "no_resolution_symbols"
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
                snapshot.graph_path,
                self.repo_root,
                entities,
                obligations,
                patch_revision=patch_revision,
                graph_revision=snapshot.graph_revision,
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
                self._recipe_empty_reason = "empty_plan_checks"
                return ""
            rendered = "[GT_EVIDENCE:verification_plan]\n" + "\n".join(lines)
            dedup_key = f"verification:{transaction_sha256}:{digest}"
            self._pending_verification_candidate = rendered
            self._pending_verification_metadata = {
                "kind": "verification_plan",
                "dedup_key": dedup_key,
                "target": paths[0],
                "semantics": "advisory_dependency_graph",
                "artifact_sha256": digest,
            }
            self.store.append(
                "verification_plan_prepared",
                artifact_sha256=digest,
                artifact_blob=f"verification_plans/{digest}.json",
                transaction_sha256=transaction_sha256,
                source_revision=patch_revision,
                dependency_source_revision=snapshot.source_revision,
                graph_revision=snapshot.graph_revision,
                changed_paths=list(paths),
                changed_entities=list(entities),
                check_count=len(plan.checks),
                semantics="advisory_dependency_graph",
                rendered_at="admission",
            )
            return rendered
        except Exception as exc:  # noqa: BLE001 - selection is correct-or-quiet
            self.store.append(
                "verification_plan_unavailable",
                transaction_sha256=transaction_sha256,
                error_type=type(exc).__name__,
            )
            self._recipe_empty_reason = "render_error"
            return ""

    def verification_candidate(self) -> tuple[str, dict[str, str]]:
        metadata = dict(self._pending_verification_metadata)
        if self._pending_verification_recipe:
            metadata["recipe"] = self._pending_verification_recipe
        return (
            self._pending_verification_candidate,
            metadata,
        )

    def consume_verification_candidate(self) -> tuple[str, dict[str, str]]:
        candidate = self.verification_candidate()
        self._pending_verification_candidate = ""
        self._pending_verification_metadata = {}
        self._pending_verification_recipe = {}
        return candidate

    def record_execution_evidence(self, artifact: Any, command: str = "") -> str:
        """Store exact raw diagnostics and return a structured augmentation.

        The journal row and blob keep the full digests; the model-facing line
        restates the typed outcome in words. Shipping the canonical JSON to the
        model gave it sha256s it could not act on - 77/690 consumed on smoke-20.
        """
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
        classification, suite_green = self._classify_execution_vs_baseline(
            artifact, command
        )
        if classification is not None:
            payload["baseline_classification"] = classification.as_dict()
        advisory = ""
        if suite_green:
            advisory = self._submit_window_advisory()
            if advisory:
                payload["submit_window"] = {
                    "layout_schema": "gt.submit_window.v1",
                    "advisory": advisory,
                }
        self.store.append(
            "execution_evidence",
            artifact_sha256=artifact_digest,
            raw_blob=raw_blob,
            **payload,
        )
        from .runtime_observation import execution_evidence_model_line

        line = execution_evidence_model_line(
            command=command, kind=artifact.kind, outcome=artifact.outcome,
            returncode=artifact.returncode,
            observed_test_outcome=artifact.observed_test_outcome,
            baseline_classification=classification,
            submit_window=advisory,
        )
        return "[GT_EXECUTION_EVIDENCE]\n" + line

    def _suite_ledger(self) -> Any:
        """The baseline-vs-current ledger.

        D2: this returned None unless `baseline.captured`, which made the whole
        classifier a no-op exactly when it was needed most. Run 34996816912's
        capture ended `status=no_test_verdicts` (pytest collection interrupted,
        4 errors, 0 names), so for all 3061 s the agent got no verdict on any
        of its ten test commands. A missing baseline removes ATTRIBUTION, not
        observation: the agent's own full-suite runs still separate an
        isolation artifact from a real failure, so the ledger runs on a
        `suite_observed` basis with empty baseline sets. `None` now means only
        that there are no plan inputs at all.
        """
        ledger = self._suite_verdict_ledger
        if ledger is not None:
            return ledger
        plan_inputs = getattr(self, "plan_inputs", None)
        if plan_inputs is None:
            return None
        from .runtime_observation import SuiteVerdictLedger

        baseline = getattr(plan_inputs, "baseline", None)
        if baseline is None or not baseline.captured:
            ledger = SuiteVerdictLedger(
                baseline_passing=(), baseline_failing=(),
                basis="suite_observed",
            )
        else:
            ledger = SuiteVerdictLedger(
                baseline_passing=tuple(baseline.passing_names),
                baseline_failing=tuple(baseline.failing_names),
                basis="baseline",
            )
        # Suite extent must come from evidence wider than the observed-name
        # universe: the repository's own test files first, then the baseline
        # capture command's declared scope. Without either, a directory run
        # can never claim whole-suite truth (partial-inventory overclaim).
        from .runtime_observation import repo_test_inventory, test_command_coverage

        inventory = repo_test_inventory(self.repo_root) if self.repo_root else None
        if inventory is not None:
            ledger.note_repo_test_inventory(inventory)
        command = tuple(getattr(baseline, "command", ()) or ())
        if command:
            declared = test_command_coverage(" ".join(command))
            paths = []
            for path in declared.paths:
                if os.path.isabs(path) and self.repo_root:
                    try:
                        path = os.path.relpath(path, self.repo_root)
                    except ValueError:
                        continue
                paths.append(path.replace(os.sep, "/"))
            ledger.baseline_scope = declared.scope
            ledger.baseline_scope_paths = tuple(paths)
        self._suite_verdict_ledger = ledger
        return ledger

    def _classify_execution_vs_baseline(
        self, artifact: Any, command: str
    ) -> tuple[Any, bool]:
        """Join this run's failing names against baseline + suite truth.

        The observation is folded into the ledger BEFORE the verdict: a
        baseline-passing name failing in a suite run is a real regression, not
        an unverified scope question.

        H5: only an actual test run with a readable scope is classified. Every
        execution row used to be classified, so `cat ci_failures.txt` and
        `sed -n 1,5p out.log` - which echo `FAILED x::y` rows - handed those
        names verdicts and, worse, entered them into the ledger's observed set
        where a later green run promoted them.

        Returns ``(classification, suite_green)`` - the second element marks a
        WHOLE-SUITE run whose recorded observation had zero effective failures.
        """
        ledger = self._suite_ledger()
        if ledger is None:
            return None, False
        if str(getattr(artifact, "kind", "") or "") != "test":
            return None, False
        from .runtime_observation import (
            classify_failures_vs_baseline,
            is_pristine_tree_probe,
            test_command_coverage,
        )

        coverage = test_command_coverage(command)
        if coverage.scope == "unknown":
            return None, False
        output = artifact.raw_output
        if output is None:
            captured = str(getattr(artifact, "output_artifact_path", "") or "")
            if not captured:
                return None, False
            try:
                output = Path(captured).read_bytes()
            except OSError:
                return None, False
        text = (
            output.decode("utf-8", errors="replace")
            if isinstance(output, (bytes, bytearray))
            else output
        )
        suite_green = False
        if is_pristine_tree_probe(command):
            self._record_pristine_probe(ledger, command, text)
        else:
            suite_green = self._record_suite_observation(
                ledger, command, text, coverage
            )
        return (
            classify_failures_vs_baseline(
                command=command, output=text, ledger=ledger
            ),
            suite_green,
        )

    def _submit_window_advisory(self) -> str:
        """The positive half of the plan gate: a green suite observation with
        zero current regressions is the moment to say the submit window is
        open, not merely to stop refusing it. Advisory only - the gate's
        authority is unchanged.

        H3: it used to claim "plan requirements verified - submit window is
        open" on `unmet_plan_rows()` alone, but this run's own definition of
        verified (see `final_state`) also requires no UNVERIFIED rows - gate
        one submitted with all 28 rows UNVERIFIED while that flag read True.
        Stale-revision evidence does not verify the submitted tree, and the
        gate's own `baseline_regression` refusal comes from a fresh re-run this
        ledger never sees, so the advisory must not promise more than it saw.
        """
        ledger = self._suite_verdict_ledger
        if ledger is None:
            return ""
        # M9: after a green promotion every covered name reads pass, so
        # regression_count() alone could never be positive again.
        if ledger.regression_count() > 0 or ledger.last_run_regressions():
            return ""
        if not ledger.has_whole_suite_green():
            # Nothing to advise on: no whole-suite run has ever come back
            # green on this tree, so there is no "window" to report open.
            return ""
        if ledger.has_stale_verdicts():
            # M7: the green run predates the agent's latest edit.
            return ""
        green = (
            "no regression observed in your own full-suite run"
            if getattr(ledger, "basis", "baseline") == "suite_observed"
            else "suite green vs baseline"
        )
        plan = getattr(self, "persistent_plan", None)
        if plan is None or not getattr(plan, "rows", ()):
            # No bound plan - "verified" would be vacuous. The suite fact
            # alone is still worth surfacing; the window claim is not made.
            return green
        unmet = self.unmet_plan_rows()
        if unmet:
            return (
                f"{green}; "
                f"{len(unmet)} plan requirement(s) still unverified"
            )
        unverified = self.unverified_plan_rows()
        if unverified:
            return (
                f"{green}; {len(unverified)} plan row(s) have no current-tree"
                " evidence - re-run their checks before submitting"
            )
        return (
            f"{green} and plan requirements verified - submit window is open"
        )

    def _parse_run_aggregate(self, command: str, output: str) -> tuple:
        """``(counts, passing_names, failing_names)`` from one run's output.

        The canonical producer's extractors are the only supported name source
        - the baseline and observation sides must parse identically or the
        join compares two different name spaces. Both sides now reach them
        through `gt_engine.test_names.parse_test_names`, which delegates to
        the pinned wheel and then, for a non-pytest runner, recovers what the
        wheel's column-0 anchors cannot see: cargo's `failures:` roll-call,
        go's indented subtests, jest/vitest `FAIL file > suite > name`,
        mocha's numbered block. It never replaces a wheel answer, and it is
        the same function the baseline capture calls, so the two sides cannot
        drift apart.
        """
        try:
            words = shlex.split(command or "", posix=True)
        except ValueError:
            words = (command or "").split()
        from .test_names import family_for_command, parse_test_names

        passing, failing, counts = parse_test_names(
            output or "",
            family=family_for_command(command or ""),
            command=words,
        )
        return counts, passing, failing

    def _record_pristine_probe(
        self, ledger: Any, command: str, output: str
    ) -> None:
        """Record a `git stash` probe's result as baseline attribution.

        Action 26 of run 34996816912, at +216 s of a 3061 s budget:
        `git stash && pytest tests/test_base.py::test_get_item -q; git stash
        pop` - the test failed on the pristine tree. That answers "is this
        mine?" outright, and the agent went on chasing the test until +2859 s.

        The probe ran against a tree the agent is NOT submitting, so it never
        touches suite truth. Failing names are taken at face value (a FAILED
        row is self-evident); passing names need the same affirmative summary
        the promotion path requires, because a truncated probe that names no
        failures has not shown the test passing.
        """
        from .runtime_observation import has_terminal_summary

        _counts, passing, failing = self._parse_run_aggregate(command, output)
        ledger.note_baseline_probe(
            failing=failing,
            passing=passing if has_terminal_summary(output) else (),
        )

    def _record_suite_observation(
        self, ledger: Any, command: str, output: str, coverage: Any
    ) -> bool:
        """Fold one run into the ledger; True when a WHOLE-SUITE run was green.

        C1: promotion used to need only `failed == 0`, guarded by
        `passed == 0 and not passing`. That let ONE parsed PASSED row promote
        the entire baseline when the summary line had been cut off - which is
        what `| tail -20` of a verbose run does, and nine of the ten commands
        in run 34996816912 ended in a `tail`/`sed` pipe. Promotion now needs an
        affirmative AGGREGATE: a positive pass count AND the runner's own
        terminal summary line. Without it the names are still folded and the
        classification is still emitted - we simply do not claim the run
        proved anything about names it never printed.

        M10: `covered_prefixes` was never passed in production, so any green
        run promoted everything. The prefixes are this command's positional
        arguments minus its --ignore/--deselect targets; a narrowed run
        (-k/-m/::nodeid) never promotes, because it skipped part of its own
        coverage.
        """
        from .runtime_observation import has_terminal_summary

        counts, passing, failing = self._parse_run_aggregate(command, output)
        # A parsed FAILED row outranks a truncated summary count: names the
        # output calls failed must never reach the zero-failure promotion.
        effective_failed = max(counts["failed"] + counts["errored"], len(failing))
        affirmative = counts["passed"] > 0 and has_terminal_summary(output)
        promote = (
            effective_failed == 0 and affirmative and not coverage.narrowed
        )
        # `pytest tests/` reads `scoped` (positional path) but IS the suite
        # when the covered directories hold every test name we know. Run
        # 35016130850: dynaconf's agent ran exactly that, no suite verdict
        # was ever recorded, and the submit-window advisory stayed silent.
        suite_scope = coverage.scope == "suite" or (
            not coverage.narrowed
            and ledger.covers_known_suite(coverage.paths, coverage.excluded)
        )
        ledger.record_suite_run(
            passing=passing,
            failing=failing,
            observed_names=[*passing, *failing],
            failed_count=0 if promote else None,
            passed_count=counts["passed"],
            covered_prefixes=coverage.paths,
            excluded_prefixes=coverage.excluded,
            suite_scope=suite_scope,
        )
        return promote and suite_scope

    def _poll_startup_index(self) -> None:
        """Adopt the asynchronously-built initial index once it lands.

        The host loop runs before this finishes; every graph consumer already
        degrades honestly on a missing or stale graph, so the wait costs
        abstentions rather than the run. When the build lands it publishes
        through the same revision gate as any other graph - edits that raced
        it supersede it, and the landed graph then serves as the amend parent
        the next frozen input builds on instead of being orphaned.
        """
        future = self._startup_index
        if future is None or not future.done():
            return
        self._startup_index = None
        receipt = None
        try:
            receipt = future.result()
        except Exception as exc:  # noqa: BLE001 - the runner path raises these
            self.store.append(
                "index_unavailable",
                error_type=type(exc).__name__,
                error=str(exc)[:300],
                phase="initial_index",
            )
            if type(exc).__name__ == "BenchmarkGraphRequired":
                self.signal_startup_abort(
                    "initial_index_failed:benchmark_graph_required"
                )
        else:
            if not getattr(receipt, "success", False):
                self.store.append(
                    "index_unavailable",
                    error_type=getattr(receipt, "error_type", "") or "unsuccessful",
                    error=str(getattr(receipt, "error_diagnostic", "") or "")[:300],
                    phase="initial_index",
                )
                self._note_index_memory_pressure(receipt)
            else:
                adopted = self.engine_state.publish_graph(
                    graph_path=receipt.graph_db,
                    graph_revision=receipt.graph_revision,
                    source_revision=receipt.source_revision,
                )
                if adopted:
                    self.graph_db = self.engine_state.graph_path
                    self._unadopted_graph = ("", "")
                    self._record_graph_publication()
                    self._maybe_schedule_lsp_promotion()
                else:
                    # Superseded by edits: not publishable as current, but
                    # exactly the certified parent the next amend builds on.
                    self._unadopted_graph = (
                        str(receipt.graph_db or ""),
                        str(receipt.graph_revision or ""),
                    )
                self._reclaim_produced_graph(
                    str(receipt.graph_db or ""),
                    success=True, adopted=adopted,
                )
                self.store.append(
                    "initial_index_ready",
                    adopted=adopted,
                    graph_revision=str(receipt.graph_revision or ""),
                    source_revision=str(receipt.source_revision or ""),
                    elapsed_ms=int(getattr(receipt, "elapsed_ms", 0) or 0),
                    analysis_state=str(getattr(receipt, "analysis_state", "") or ""),
                    embedding_state=str(getattr(receipt, "embedding_state", "") or ""),
                )
        # The plan channel is not the graph channel: the ledger binds verbatim
        # task obligations and the baseline probe, neither of which needs the
        # index. A failed build must not take the plan down with it - the
        # finalize still runs, anchors simply stay unbound. `receipt` may be
        # None on the exception path; the runner's finalize guards its fields.
        self._run_startup_finalize(receipt)

    def _run_startup_finalize(self, receipt) -> None:
        finalize = self._startup_finalize
        self._startup_finalize = None
        if finalize is not None:
            try:
                finalize(receipt)
            except Exception as exc:  # noqa: BLE001 - plan setup is advisory
                self.store.append(
                    "persistent_plan_unavailable",
                    error=f"{type(exc).__name__}: {str(exc)[:200]}",
                )

    def signal_startup_abort(self, reason: str) -> None:
        """Drop the supervisor flag for a startup failure the run cannot survive.

        Same contract as ``signal_churn_abort``: the journal row is the durable
        record, the flag file is the cheap cross-process kill signal.
        """
        self.store.append("startup_abort", reason=reason)
        flag = {
            "schema": "gt.startup_abort.v1",
            "task_id": self.task_id,
            "reason": reason,
        }
        flag_path = self.engine_state.layout.state_root / "startup_abort.json"
        try:
            from gt_harness.canonical_io import atomic_write

            atomic_write(flag_path, json.dumps(flag).encode("utf-8"))
        except (OSError, ImportError):
            pass

    def adopt_startup_plan(
        self,
        plan_inputs: Any,
        contract: Any,
        predicates: tuple[Any, ...],
    ) -> None:
        """Late-bind the plan contract that the async index was still building.

        The merged obligations are verbatim requirement lines - always owed -
        so registering them at a nonzero epoch is honest in a way an inferred
        row would not be: they start UNKNOWN and only evidence recorded after
        this point can prove them.
        """
        if plan_inputs is None:
            return
        self.plan_inputs = plan_inputs
        if contract is not None and contract is not self.contract:
            self.contract = contract
            self._compiled_predicates = compile_obligation_predicates(contract)
            self._predicate_by_obligation = {
                item.obligation_id: item.predicate_id
                for item in self._compiled_predicates.values()
            }
            self._obligation_by_predicate = {
                value: key for key, value in self._predicate_by_obligation.items()
            }
            added = [
                item.predicate_id
                for item in predicates
                if item.predicate_id not in self.predicates
            ]
            for item in predicates:
                if item.predicate_id not in self.predicates:
                    self.predicates[item.predicate_id] = item
                    self._status[item.predicate_id] = PredicateStatus.UNKNOWN
            if added:
                self._journal_compiled_predicates("plan", added)

    def refresh_graph(self, *, phase: str = "graph_query") -> bool:
        """Make the adopted graph current at this boundary, inline.

        There is no scheduler: whatever the transaction-boundary amend
        deferred (over-cap dirty sets, refused amends) is completed HERE,
        before any consumer can observe the graph behind the source. A
        dirty set that cannot be enumerated is a typed failure, never a
        stale serve.
        """
        self._poll_startup_index()
        if self._startup_index is not None:
            return False
        if not self.engine_state.graph_current:
            self._amend_graph_inline(phase=phase)
        if not self.engine_state.graph_current:
            return False
        self.graph_db = self.engine_state.graph_path
        self._gateway_state = None
        self.graph_stale_since_revision = ""
        self._record_graph_publication()
        self._poll_lsp_promotions()
        self._maybe_schedule_lsp_promotion()
        return True

    #: Consecutive ``amend_failed:*`` refusals on the same parent that
    #: escalate to a recovery build. The parent's bytes are immutable, so a
    #: producer failure against them is deterministic -- the same amend on
    #: the same bytes can only fail the same way again, and journaling it
    #: forever is exactly how one run accumulated 601 refusal rows while the
    #: adopted graph stayed ~95% stale. Two bounds it: a lone failure can
    #: still indict the dirty set rather than the chain's base, and the
    #: second failure on identical parent bytes is the proof no amend can
    #: land, so the boundary buys the fresh parent a recovery build
    #: publishes.
    AMEND_FAILURE_ESCALATION_STREAK = 2

    def _amend_graph_inline(self, *, phase: str) -> None:
        """Catch the adopted graph up to the overlay, at a serving boundary.

        Same machinery as the transaction-boundary amend but unbounded on
        path count: the boundary is where mass mutations finish syncing.
        The dirty set is the engine's overlay -- never just the last edit --
        and a parent that can never serve again earns a named recovery
        build, not a silent full re-index.
        """
        if not self.repo_root:
            self._record_graph_refresh_failure("repo_root_unavailable", phase=phase)
            return
        snapshot = self.engine_state.query_snapshot()
        unenumerated = [
            value for value in snapshot.omissions
            if value != "transaction_bytes_unavailable"
        ]
        if unenumerated:
            # The dirty set is unknowable, so no amend can cover it. The one
            # honest resync is a whole-tree build: it publishes on the live
            # tree, not on the dirty set, and clears the omissions on
            # adoption. Refusing instead would leave the omissions forever,
            # which is permanent PARTIAL masquerading as caution.
            self.store.append(
                "graph_resync_incomplete", phase=phase,
                omissions=list(snapshot.omissions),
            )
            self._recovery_build_inline(phase=phase)
            return
        dirty = tuple(sorted(snapshot.masked_paths))
        parent = self.engine_state.graph_path or self._unadopted_graph[0]
        if not parent:
            # Nothing to amend from -- the one legitimate from-scratch
            # build, whether paths are masked or not.
            self._recovery_build_inline(phase=phase)
            return
        if not dirty:
            # Non-current with a bound parent and nothing masked is a stale
            # marking, not a build trigger.
            return
        if self._amend_spawn_deferred():
            self._journal_amend_deferred(
                phase=phase, parent=str(parent), dirty=dirty)
            return
        from . import indexer
        from .indexer import _graph_publication_lock

        layout = self.engine_state.layout
        started = time.monotonic()
        try:
            with _graph_publication_lock(layout.graph_root / ".graph.lock"):
                published, reason, results = indexer._ensure_index_incremental_unlocked(
                    str(self.repo_root), layout=layout,
                    parent_graph=Path(parent), changed_paths=dirty,
                    excluded_roots=tuple(layout.excluded_roots),
                ) if parent else (None, "no_parent_graph", ())
        except Exception as exc:  # noqa: BLE001 - an amend failure is data
            published, reason, results = None, f"{type(exc).__name__}: {exc}"[:200], ()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not published and (
            (reason or "").startswith("incremental_parent_uncertifiable")
            or (parent and reason in {
                "parent_graph_missing", "parent_manifest_missing",
                "immutable_graph_artifact_invalid",
            })
        ):
            # The chain's base is gone: the one legitimate rebuild.
            self._recovery_build_inline(phase=phase)
            return
        if not published:
            self.store.append(
                "graph_boundary_amend_refused", reason=reason[:200],
                dirty_paths=list(dirty), parent_graph=str(parent), phase=phase,
            )
            self._note_amend_failure(reason)
            memory_bound = (
                self._amend_error_code(reason) in self._INDEX_MEMORY_ERRORS
            )
            if memory_bound:
                # A resource-bound attempt: it will be retried when the
                # window drains, so it is consequential evidence, not the
                # terminal primary failure an unrecoverable refusal is. The
                # run-level verdict belongs to the capability row if the
                # graph is still stale at seal.
                self._record_graph_refresh_failure(
                    f"amend_refused:{reason[:80]}", phase=phase,
                    severity="WARNING", classification="consequential",
                    retryable=True,
                )
            else:
                self._record_graph_refresh_failure(
                    f"amend_refused:{reason[:80]}", phase=phase,
                )
            if (reason or "").startswith("amend_failed:") and not memory_bound:
                # Structural only: a producer nonzero-exit on a certified,
                # immutable parent is deterministic whatever the error code,
                # so a bounded streak on the SAME parent escalates to the
                # build that publishes a fresh base. Transient refusals a
                # rebuild cannot fix (capability, path scope) stay
                # journaled-only, as does every refusal on a different
                # parent - consecutive is per parent bytes, so the streak
                # holds this parent alone. Memory-family outcomes are
                # excluded too: the same amend CAN land once the cgroup
                # drains, so they are deferred, not counted deterministic.
                key = str(parent)
                streak = self._amend_failure_streak.get(key, 0) + 1
                self._amend_failure_streak = {key: streak}
                if streak >= self.AMEND_FAILURE_ESCALATION_STREAK:
                    self._amend_failure_streak.pop(key, None)
                    self.store.append(
                        "graph_amend_escalated", phase=phase,
                        parent_graph=key, streak=streak,
                        reason=reason[:200],
                    )
                    self._recovery_build_inline(phase=phase)
            return
        self._amend_failure_streak.clear()
        receipt = indexer._receipt_for_published_graph(
            published, source_revision=self.engine_state.source_revision,
            embedding_budget_seconds=self.REBUILD_EMBEDDING_BUDGET_SECONDS,
            layout=layout, build_mode="incremental", incremental_results=results,
        )
        self._adopt_graph_receipt(
            receipt, event="graph_boundary_amend", dirty_paths=list(dirty),
            parent_graph=str(parent), elapsed_ms=elapsed_ms,
            amended=[dict(row) for row in results],
        )

    def _recovery_build_inline(self, *, phase: str) -> None:
        """The only from-scratch build outside task start: the amend chain's
        base can never serve again, so the boundary rebuilds inline."""
        if self._index_memory_defer_until > time.monotonic():
            # A whole-tree build spawns the same bounded producer over far
            # more input than the amend that just died of pressure. Buying
            # a fresh base inside the window only feeds the guard another
            # process to kill; the trigger survives to the next boundary.
            if (
                self._recovery_defer_journaled_until
                < self._index_memory_defer_until
            ):
                self._recovery_defer_journaled_until = (
                    self._index_memory_defer_until
                )
                self.store.append(
                    "graph_recovery_deferred", phase=phase,
                    reason="index_memory_backoff",
                    remaining_seconds=round(
                        self._index_memory_defer_until - time.monotonic(), 3
                    ),
                )
            return
        from dataclasses import replace

        from .indexer import ensure_index_with_receipt

        started = time.monotonic()
        try:
            receipt = ensure_index_with_receipt(
                Path(self.repo_root), layout=self.engine_state.layout,
                source_revision=self.engine_state.source_revision,
                embedding_budget_seconds=self.REBUILD_EMBEDDING_BUDGET_SECONDS,
                reclaim=False,
            )
        except Exception as exc:  # noqa: BLE001 - build failure is data
            self.store.append(
                "graph_recovery_failed", phase=phase,
                error_type=type(exc).__name__, error_detail=str(exc)[:200],
            )
            self._record_graph_refresh_failure(type(exc).__name__, phase=phase)
            return
        try:
            # The requested revision is the adoption claim, not whatever the
            # producer echoed back - publish_graph compares against it.
            receipt = replace(
                receipt, source_revision=self.engine_state.source_revision)
        except TypeError:
            # A test double whose receipt is not a dataclass still carries the
            # requested revision through publish_graph's own argument.
            pass
        self._adopt_graph_receipt(
            receipt, event="graph_recovery", phase=phase,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    def _adopt_graph_receipt(
        self, receipt: Any, *, event: str, phase: str = "", **fields: Any,
    ) -> None:
        """Publish, journal, and reclaim a produced graph, on the owner thread.

        Reclamation keys on the adoption decision: adopted -> prune siblings
        the authority no longer names; refused -> the produced revision can
        never be named, so discard it directly.
        """
        adopted = bool(receipt.success) and self.engine_state.publish_graph(
            graph_path=receipt.graph_db, graph_revision=receipt.graph_revision,
            source_revision=receipt.source_revision,
        )
        row = {
            "adopted": adopted,
            "graph_revision": str(receipt.graph_revision or ""),
            "source_revision": str(receipt.source_revision or ""),
            "build_mode": str(getattr(receipt, "build_mode", "") or ""),
            "build_mode_reason": str(getattr(receipt, "build_mode_reason", "") or ""),
            "analysis_state": str(getattr(receipt, "analysis_state", "") or ""),
            "embedding_state": str(getattr(receipt, "embedding_state", "") or ""),
            **fields,
        }
        if phase:
            row["phase"] = phase
        self.store.append(event, **row)
        self._note_index_memory_pressure(receipt)
        self._reclaim_produced_graph(
            str(receipt.graph_db or ""), success=bool(receipt.success),
            adopted=adopted,
        )
        if adopted:
            self.graph_db = self.engine_state.graph_path
            self._gateway_state = None
            self.graph_stale_since_revision = ""
            self._unadopted_graph = ("", "")
            self._record_graph_publication()
            self._maybe_schedule_lsp_promotion()
        elif receipt.success:
            # publish_graph refuses only on a revision race; journaled rather
            # than silently dropped. The artifact stays: it is the certified
            # parent the next amend builds on.
            self._unadopted_graph = (
                str(receipt.graph_db or ""), str(receipt.graph_revision or "")
            )
        else:
            self._record_graph_refresh_failure(
                str(getattr(receipt, "error_type", "") or "build_failed"),
                phase=phase or event,
            )

    def _reclaim_produced_graph(
        self, graph_db: str, *, success: bool, adopted: bool
    ) -> None:
        """Prune or discard a produced revision by its adoption outcome.

        The protected set is what the authority still names: the adopted
        graph and the certified fallback parent. Pinned and
        manifest-referenced revisions are protected inside the prune.
        """
        if not graph_db:
            return
        produced = Path(graph_db).parent
        try:
            if adopted:
                from .indexer import prune_graph_revisions

                named = {
                    str(self.engine_state.graph_path or ""),
                    str(self._unadopted_graph[0] or ""),
                }
                prune_graph_revisions(
                    produced,
                    protected=frozenset(
                        Path(path).parent for path in named if path
                    ),
                )
            elif not success:
                # A failed produce named by no authority is garbage. A
                # refused-but-successful one survives as the certified
                # fallback parent.
                from .indexer import discard_revision

                discard_revision(produced)
        except Exception:  # noqa: BLE001 - reclamation never breaks a boundary
            pass

    def _maybe_schedule_lsp_promotion(self) -> None:
        """Schedule one LSP promotion against the adopted graph.

        What ``consider_enrichment`` did without a coordinator: one active
        promotion at a time, deduplicated on the frozen input identity, and
        only ever against a graph the authority currently names. The freeze
        can refuse on unreadable source -- a skipped promotion is journaled,
        never fatal.
        """
        if (
            not self.engine_state.graph_current
            or self._lsp_active is not None
            or self._latest_workspace_snapshot is None
            or self._lsp_churn_defer_until > time.monotonic()
            or self._index_memory_defer_until > time.monotonic()
        ):
            return
        try:
            request = self._frozen_graph_input(self._latest_workspace_snapshot)
        except Exception as exc:  # noqa: BLE001 - a refused freeze is data
            self.store.append(
                "lsp_promotion_schedule_refused",
                reason=str(exc)[:200],
            )
            return
        base = GraphBuildArtifact(
            True, self.engine_state.graph_path, self.engine_state.graph_revision,
        )
        identity = self._lsp_enrichment_identity(request, base)
        if identity in self._lsp_considered:
            return
        self._lsp_considered.add(identity)
        try:
            handle = self._schedule_lsp_candidate(request, base)
        except Exception as exc:  # noqa: BLE001 - a failed schedule is data
            receipt = {
                "terminal": True, "status": "failed",
                "reason": f"schedule_exception:{type(exc).__name__}:{str(exc)[:160]}",
                "publishable": False,
                "source_revision": request.source_revision,
                "input_graph_revision": base.graph_revision,
            }
            self._record_lsp_terminal(request, base, receipt, "schedule_exception")
            return
        self._lsp_bases[handle.task_id] = (request, base)
        self._lsp_active = handle.task_id

    @staticmethod
    def _lsp_enrichment_identity(
        request: FrozenBuildInput, base: GraphBuildArtifact
    ) -> tuple[Any, ...]:
        digest = hashlib.sha256()
        for path, content in request.files:
            name = path.encode("utf-8", "surrogatepass")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return (
            request.source_revision, digest.hexdigest(), request.history,
            base.graph_path, base.graph_revision,
        )

    def _poll_lsp_promotions(self) -> None:
        """Drain finished promotions: certify, adopt, salvage, or discard.

        The coordinator's ``_poll_enrichment`` disposition chain, unchanged:
        a promotion is only adopted while the graph it enriched is still the
        one the authority names; a superseded-but-valid candidate is staged
        for scoped salvage; everything else is discarded so refusal cannot
        leak ~1GB candidates onto the artifact.
        """
        if self._lsp_scheduler is None:
            return
        for handle in list(getattr(self._lsp_scheduler, "_handles", ()) or ()):
            task_id = str(getattr(handle, "task_id", "") or "")
            entry = self._lsp_bases.get(task_id)
            if entry is None:
                continue
            try:
                done = handle.done
            except Exception:  # noqa: BLE001
                done = True
            if not done:
                continue
            request, base = entry
            try:
                terminal = dict(handle.terminal_receipt(timeout=0))
            except Exception as exc:  # noqa: BLE001
                terminal = {
                    "terminal": True, "status": "failed",
                    "reason": f"receipt_exception:{type(exc).__name__}",
                    "publishable": False,
                }
            if self._lsp_active == task_id:
                self._lsp_active = None
            disposition = self._lsp_terminal_disposition(request, base, terminal)
            self._lsp_outcomes_by_base[str(base.graph_revision or "")] = disposition
            self._note_lsp_churn_outcome(disposition)
            self._record_lsp_terminal(request, base, terminal, disposition)
            if disposition != "published":
                self._discard_lsp_candidate(terminal)
            self._lsp_bases.pop(task_id, None)

    def _lsp_terminal_disposition(
        self,
        request: FrozenBuildInput,
        base: GraphBuildArtifact,
        terminal: Mapping[str, Any],
    ) -> str:
        if (
            terminal.get("terminal") is not True
            or terminal.get("status") != "succeeded"
            or terminal.get("publishable") is not True
        ):
            return "not_publishable"
        # Zero edge mutations is not worth a republication: the producer
        # still seals and returns publishable, so the refusal lands here.
        if not sum(
            int(terminal.get(key) or 0)
            for key in ("verified", "corrected", "selected", "deleted")
        ):
            return "no_edge_mutations"
        if (
            terminal.get("source_revision") != request.source_revision
            or terminal.get("input_graph_revision") != base.graph_revision
        ):
            return "identity_mismatch"
        if (
            self.engine_state.source_revision != request.source_revision
            or self.engine_state.graph_revision != base.graph_revision
            or self.engine_state.graph_path != base.graph_path
        ):
            return "obsolete"
        try:
            candidate = self._certify_lsp_candidate(request, base, terminal)
        except Exception:  # noqa: BLE001
            return "certifier_exception"
        if not candidate.success:
            return "certification_failed"
        if (
            self.engine_state.graph_revision != base.graph_revision
            or self.engine_state.graph_path != base.graph_path
            or not self.engine_state.publish_graph(
                graph_path=candidate.graph_path,
                graph_revision=candidate.graph_revision,
                source_revision=request.source_revision,
            )
        ):
            return "obsolete_after_certification"
        return "published"

    #: A leg that outlived its base by these margins cannot win the next one
    #: either - the defer window starts near observed cold-leg latency and
    #: doubles while the workspace keeps churning under it.
    _LSP_CHURN_DEFER_INITIAL_S = 60.0
    _LSP_CHURN_DEFER_MAX_S = 300.0
    _LSP_CHURN_OBSOLETE = frozenset({
        "obsolete", "obsolete_after_certification",
    })
    _LSP_CHURN_KEPT_UP = frozenset({"published", "no_edge_mutations"})

    def _note_lsp_churn_outcome(self, disposition: str) -> None:
        """Adapt the promotion schedule to the observed revision churn rate.

        ``obsolete``/``obsolete_after_certification`` are the two typed
        proofs the leg's base died mid-flight; each consecutive loss widens
        the defer window. ``published`` and ``no_edge_mutations`` prove the
        base survived a whole leg - the race is winnable again, so the
        streak resets. Every other disposition is producer-side evidence,
        not a race outcome, and leaves the window alone.
        """
        if disposition in self._LSP_CHURN_OBSOLETE:
            self._lsp_churn_streak += 1
            defer = min(
                self._LSP_CHURN_DEFER_INITIAL_S
                * (2 ** (self._lsp_churn_streak - 1)),
                self._LSP_CHURN_DEFER_MAX_S,
            )
            self._lsp_churn_defer_until = time.monotonic() + defer
            self.store.append(
                "lsp_churn_backoff",
                streak=self._lsp_churn_streak,
                defer_seconds=defer,
                disposition=disposition,
            )
        elif disposition in self._LSP_CHURN_KEPT_UP:
            self._lsp_churn_streak = 0
            self._lsp_churn_defer_until = 0.0

    #: The cgroup memory-pressure family: pre-launch headroom refusal,
    #: in-flight guard kill, and an OOM-attributed exit. A bounded defer
    #: window keeps the next promotion leg off the cgroup while whatever
    #: pressured it drains.
    _INDEX_MEMORY_DEFER_S = 120.0
    _INDEX_MEMORY_ERRORS = frozenset({
        "GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT",
        "GT_INDEX_MEMORY_GUARD_TRIGGERED",
        "GT_INDEX_CGROUP_OOM",
    })
    #: A producer spawn that died is not retried at the very next boundary:
    #: whatever killed it - cgroup pressure or a deterministic defect - is
    #: unchanged seconds later. Sixty seconds bounds the retry rate while
    #: leaving deterministic-failure escalation intact (two attempts on the
    #: same parent bytes still buy a recovery build, just a window apart).
    _AMEND_SPAWN_DEFER_S = 60.0

    def _note_index_memory_pressure(self, receipt: Any) -> None:
        """Defer index-family spawns after a memory-family index outcome.

        Successful receipts carry an empty error_type and no-op here; the
        window is set only by a typed memory refusal, never by a generic
        build failure.
        """
        self._note_index_memory_code(
            str(getattr(receipt, "error_type", "") or "")
        )

    def _note_index_memory_code(self, error_type: str) -> None:
        """Open the cgroup window for a typed memory code from any path.

        Build receipts and amend refusal reasons both carry the same codes;
        whichever observes the pressure first sets the window every
        index-family spawn consults - LSP legs, amends, recovery builds.
        """
        if error_type not in self._INDEX_MEMORY_ERRORS:
            return
        self._index_memory_defer_until = (
            time.monotonic() + self._INDEX_MEMORY_DEFER_S
        )
        self.store.append(
            "index_memory_backoff",
            error_type=error_type,
            defer_seconds=self._INDEX_MEMORY_DEFER_S,
        )

    @staticmethod
    def _amend_error_code(reason: str) -> str:
        """The typed producer code inside an ``amend_failed:*`` reason."""
        if not reason.startswith("amend_failed:"):
            return ""
        return reason[len("amend_failed:"):].split(":", 1)[0]

    def _note_amend_failure(self, reason: str) -> None:
        """Feed the defer windows from a refused amend's reason.

        Every ``amend_failed:*`` is a producer process that ran and died -
        respawning at the very next boundary is the stampede run
        34849119441 journaled. The spawn window bounds the retry rate
        whatever the code; the memory family additionally opens the cgroup
        window so legs and rebuilds stop launching into measured pressure.
        Pre-spawn refusals (capability, parent, path scope) never ran a
        producer and open no window — except the memory-coded ones: a
        pre-launch ``GT_INDEX_MEMORY_HEADROOM_INSUFFICIENT`` refusal means
        the cgroup is measurably pressured, so the cgroup window opens even
        though no producer ran. That is what lets the same amend retry after
        the pressure drains instead of stampeding or dying mid-flight.
        """
        if not reason.startswith("amend_failed:"):
            head = reason.split(":", 1)[0]
            if head in self._INDEX_MEMORY_ERRORS:
                self._note_index_memory_code(head)
            return
        self._graph_amend_defer_until = (
            time.monotonic() + self._AMEND_SPAWN_DEFER_S
        )
        self._note_index_memory_code(self._amend_error_code(reason))

    def _amend_spawn_deferred(self) -> bool:
        """Whether a producer spawn sits inside an open defer window."""
        now = time.monotonic()
        return (
            self._index_memory_defer_until > now
            or self._graph_amend_defer_until > now
        )

    def _journal_amend_deferred(
        self, *, phase: str, parent: str, dirty: tuple[str, ...]
    ) -> None:
        """One journal row per open window, not one per suppressed spawn."""
        window = max(
            self._index_memory_defer_until, self._graph_amend_defer_until
        )
        if self._amend_defer_journaled_until >= window:
            return
        self._amend_defer_journaled_until = window
        self.store.append(
            "graph_amend_deferred", phase=phase, parent_graph=str(parent),
            dirty_paths=list(dirty),
            remaining_seconds=round(window - time.monotonic(), 3),
        )

    @staticmethod
    def _discard_lsp_candidate(terminal: Mapping[str, Any]) -> None:
        """Delete a refused candidate's database files.

        The producer deletes on its own failures but keeps a publishable
        candidate for publication to consume; a refusal here owns the
        cleanup, or every refusal leaks ~1GB onto the task artifact."""
        candidate = terminal.get("candidate_path")
        if not isinstance(candidate, str) or not candidate:
            return
        path = Path(candidate)
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                path.with_name(path.name + suffix).unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _symlink_alias_target(item: Any) -> str | None:
        """Where a snapshot symlink points, as a workspace-relative path.

        ``captured`` for a symlink holds the *link target string*, not file
        bytes (``runtime_observation``, where the entry is built). Returns None
        when the link is uncaptured, absolute, or escapes the workspace -- all
        cases where this side cannot say what the target's bytes are.
        """
        raw = getattr(item, "captured", None)
        if raw is None:
            return None
        try:
            target = bytes(raw).decode("utf-8", "surrogatepass")
        except (UnicodeDecodeError, ValueError):
            return None
        if not target or PurePosixPath(target).is_absolute() or target.startswith("\\\\"):
            return None
        parts: list[str] = []
        for part in (*PurePosixPath(str(item.path)).parent.parts, *PurePosixPath(target).parts):
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return None  # escapes the workspace root
                parts.pop()
                continue
            parts.append(part)
        return "/".join(parts) if parts else None

    def _frozen_graph_input(self, snapshot: Any) -> FrozenBuildInput:
        from .indexer import _SKIP_DIRS, is_producer_input
        from .runtime_observation import canonical_repository_bytes

        files: list[tuple[str, bytes]] = []
        missing: list[str] = []
        captured_bytes = {
            str(item.path): bytes(item.captured) for item in snapshot.files
            if item.kind == "file" and item.captured is not None
        }
        sha_by_path = {
            str(item.path): item.sha256 for item in snapshot.files
            if item.kind == "file"
        }

        def witness_bytes(relative: str) -> bytes | None:
            payload = captured_bytes.get(relative)
            if payload is not None:
                return payload
            # Files above the capture cap are hash-only witnesses; the
            # snapshot's sha256 IS the byte contract. A live read that still
            # hashes identically satisfies the freeze exactly -- a mismatch
            # means the content genuinely moved, which is a real miss. The
            # smoke-20 abs tasks lost every rebuild (942 events) to a >1MiB
            # bundle whose bytes this rule recovers.
            digest = sha_by_path.get(relative)
            if not digest:
                return None
            try:
                raw = (Path(snapshot.root) / relative).read_bytes()
            except OSError:
                return None
            if hashlib.sha256(canonical_repository_bytes(raw)).hexdigest() != digest:
                return None
            return raw

        for item in snapshot.files:
            relative = str(item.path)
            # The producer's own walk prunes _SKIP_DIRS trees by name at any
            # depth. A git-tracked file under one (e.g. a committed
            # .vuepress/dist bundle) still lands in the snapshot -- the git
            # path in capture_workspace applies no dir filter -- but it is
            # never producer input, and demanding its bytes froze the abs
            # graphs at task start for the whole run.
            if any(part in _SKIP_DIRS for part in Path(relative).parts):
                continue
            if not is_producer_input(item.path):
                continue
            if item.kind == "symlink":
                # A symlink is a path alias, not absent source, and the old
                # `kind != "file"` test called it missing -- true on step one and
                # step three hundred. On arktype two producer-input symlinks
                # (README.md, ark/README.md) froze the graph at task start across
                # 105 edits in run 34064560259: graph_refresh_failed 600,
                # graph_refresh_scheduled 0.
                #
                # The alias's own captured bytes are the link TARGET STRING, so
                # they must never be fed to the producer. What the producer
                # actually saw at task start is not in doubt and is not a
                # judgement call: the initial build stages input with
                # `shutil.copyfile` (indexer.py, the excluded_roots branch),
                # which FOLLOWS the link and materialises the alias path holding
                # the target's content. Reproduce exactly that. Skipping the
                # entry instead would hand the producer 493 files where task
                # start had 495, and the rebuilt graph would silently differ
                # from the one every task-start claim was made against.
                target = self._symlink_alias_target(item)
                payload = witness_bytes(target) if target else None
                if payload is None:
                    missing.append(relative)
                else:
                    files.append((relative, payload))
                continue
            if item.kind != "file":
                missing.append(relative)
                continue
            payload = witness_bytes(relative)
            if payload is None:
                missing.append(relative)
            else:
                files.append((relative, payload))
        source_omissions = []
        for omission in snapshot.omissions:
            kind, separator, value = str(omission).partition(":")
            if kind == "unreadable" and separator:
                if (is_producer_input(value)
                        and not any(
                            part in _SKIP_DIRS
                            for part in Path(value).parts
                        )):
                    source_omissions.append(str(omission))
            else:
                # Unknown omission types remain conservative until their
                # relationship to source completeness is explicitly known.
                source_omissions.append(str(omission))
        if missing or source_omissions:
            # Named, because the journal records only the exception. Six hundred
            # bare "ValueError" rows cost a whole paid run's worth of diagnosis.
            detail = ",".join(sorted(missing)[:5]) or ",".join(sorted(source_omissions)[:5])
            raise ValueError(
                f"frozen_source_incomplete:missing={len(missing)}:"
                f"omissions={len(source_omissions)}:{detail}"
            )
        # query_snapshot() blanks graph_path for anything short of complete, so
        # the parent is read from the engine state directly: the graph is stale
        # by construction here (an edit is what scheduled this build) and it is
        # exactly that stale-but-certified graph the amend starts from. The
        # unadopted pair covers the async initial build that landed behind an
        # edit - not publishable as current, but still the certified parent.
        parent_path = str(
            self.engine_state.graph_path or self._unadopted_graph[0] or ""
        )
        parent_revision = str(
            self.engine_state.graph_revision or self._unadopted_graph[1] or ""
        )
        return FrozenBuildInput(
            str(snapshot.revision),
            self.engine_state.query_snapshot().masked_paths,
            tuple(sorted(files)),
            snapshot.history,
            parent_path,
            parent_revision,
        )

    # Embedding budget for boundary amends and recovery builds. The plan is
    # incremental by construction: the agent edited a few files, so a few
    # contracts moved. 60s is ~5 batches at the measured 12.1s, which is
    # generous for that and refuses a full-corpus re-embed outright.
    #
    # It has to refuse, because the full corpus costs ~1,458s (3,808 symbols)
    # and a boundary that spent 25 minutes embedding would starve the agent
    # the same way the unbounded fallback starved boa. A skip is honest --
    # dense degrades, the receipt says why -- while a silent full re-embed
    # inside a tool call is not.
    REBUILD_EMBEDDING_BUDGET_SECONDS = 60.0

    #: Wall-clock at import, so a directory older than this process cannot be
    #: one of its own candidates. Compared against mtime rather than tracked in
    #: a set, because the case being swept is precisely the one where no
    #: in-process record survived.
    _PROCESS_START = time.time()

    def _sweep_dead_enrichments(self, namespace: Path) -> None:
        """Remove candidate databases orphaned by a process that did not exit.

        The coordinator now deletes what it refuses, and the producer deletes
        what it fails. Neither runs when the process is killed mid-enrichment -
        and run 34095557374 ended on a deadline with sixteen enrichment
        directories against fifteen receipts, so exactly one candidate had no
        disposition at all and its ~920MB survived unreferenced.

        A directory whose mtime predates this process cannot be one of this
        run's candidates, and nothing outside the run refers to it: candidates
        live under graph_root/enrichments. Comparing mtime rather than consulting
        a registry is deliberate - the whole point is that the registry died
        with the process.

        A PUBLISHED CANDIDATE IS THE LIVE GRAPH AND MUST SURVIVE.
        certify_lsp_candidate returns GraphBuildArtifact(True, str(candidate))
        with the candidate path UNCHANGED (indexer.py:1773) and the coordinator
        publishes exactly that, so the live graph_path can point INTO this
        namespace. The mtime guard already covers it - a graph published by this
        process is newer than this process - but that is incidental protection
        for a catastrophic mistake, so the live directory is excluded by name as
        well. It is also why the tempting one-liner, excluding enrichments/ from
        the artifact upload, is wrong: it would drop the live graph.

        Two further protections beyond mtime and the live path:

        - ``pinned.json``: a delivered artifact named this directory and
          ``verify_runtime_receipt`` can still demand it -- identical to a
          pinned revision.
        - Derivation references: a surviving manifest (anywhere under the
          graph root) can cite a pre-process directory as its base. Deleting a
          cited base re-creates run 35016130850's
          ``derivation_base_manifest_unreadable`` one namespace up. References
          reprieve transitively -- a reprieved directory's own manifest still
          cites its own bases -- while a condemned directory's manifest dies
          with it and protects nothing. Anything unauditable freezes the
          sweep: unknown never authorises a delete.
        """
        live = getattr(self.engine_state, "graph_path", "")
        live_dir = Path(live).parent.resolve() if live else None
        condemned: dict[Path, Path] = {}
        try:
            entries = sorted(namespace.glob("lsp-*"))
        except OSError:
            return
        for entry in entries:
            try:
                if not entry.is_dir() or entry.stat().st_mtime >= self._PROCESS_START:
                    continue
                if live_dir is not None and entry.resolve() == live_dir:
                    continue
                if (entry / "pinned.json").exists():
                    continue
                condemned[entry.resolve()] = entry
            except OSError:
                continue
        if not condemned:
            return
        reprieved: set[Path] = set()
        try:
            manifests = list(namespace.parent.rglob("graph.manifest.json"))
        except OSError:
            return
        while True:
            newly: set[Path] = set()
            for manifest in manifests:
                try:
                    holder = manifest.parent.resolve()
                except OSError:
                    return
                if holder in condemned and holder not in reprieved:
                    continue
                try:
                    body = json.loads(manifest.read_text(encoding="utf-8"))
                    if not isinstance(body, dict):
                        return
                    derivation = body.get("derivation")
                except (OSError, ValueError):
                    return
                if not isinstance(derivation, dict):
                    continue
                for key in ("base_graph", "base_manifest", "base_resource", "terminal_receipt"):
                    reference = str(derivation.get(key) or "")
                    if not reference or "/" not in reference:
                        continue
                    try:
                        target = (manifest.parent / reference).resolve()
                    except OSError:
                        return
                    for ancestor in (target, *target.parents):
                        if ancestor in condemned and ancestor not in reprieved:
                            newly.add(ancestor)
                            break
            if not newly:
                break
            reprieved |= newly
        for resolved, entry in condemned.items():
            if resolved in reprieved:
                continue
            try:
                # Sweeping is an optimisation; failing it must never stop an
                # enrichment from being scheduled.
                shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    def _schedule_lsp_candidate(self, request: FrozenBuildInput, base: GraphBuildArtifact) -> Any:
        from groundtruth.lsp.background_promotion import (
            LSPPromotionRequest,
            LSPPromotionScheduler,
            repository_snapshot_sha256,
        )

        from .indexer import (
            _certify_published_graph,
            _source_paths,
            source_manifest_digest,
        )

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
        self._sweep_dead_enrichments(namespace)
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
            # The manifest stores only the digest, so re-walk the live root to
            # name the disagreement. Twenty-three bare ValueError rows cost
            # run 34760986248 its diagnosis.
            have = {
                path.relative_to(source).as_posix()
                for path in _source_paths(source)
            }
            live_root = Path(self.repo_root).resolve()
            want = {
                path.relative_to(live_root).as_posix()
                for path in _source_paths(
                    live_root,
                    tuple(self.engine_state.layout.excluded_roots),
                )
            }
            raise ValueError(
                f"lsp_source_input_mismatch:"
                f"expected_only={sorted(want - have)[:8]}:"
                f"materialized_only={sorted(have - want)[:8]}"
            )
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
        self._lsp_epochs[handle.task_id] = self._edit_epoch
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
        task_id = str(terminal.get("task_id") or "")
        try:
            self._maybe_salvage_lsp(request, base, terminal, disposition, task_id)
        except Exception as exc:  # noqa: BLE001 - salvage never fails a run
            self._append_observation(
                "lsp_salvage_fault", error_type=type(exc).__name__
            )
        self._lsp_requests.pop(task_id, None)
        self._lsp_epochs.pop(task_id, None)

    _SALVAGEABLE_DISPOSITIONS = frozenset({
        "obsolete", "obsolete_after_certification",
    })

    def _maybe_salvage_lsp(
        self,
        request: FrozenBuildInput,
        base: GraphBuildArtifact,
        terminal: Mapping[str, Any],
        disposition: str,
        task_id: str,
    ) -> None:
        """Stage a superseded candidate for the scoped-merge salvage.

        The candidate's mutations remain valid wherever the source did
        not move since the promotion was scheduled. The stale set is the
        epoch-tracked edit paths; an unenumerated edit after scheduling
        makes that set unknowable and the salvage refuses rather than
        merge on faith. Staging renames the file so the coordinator's
        post-observer discard no-ops on the claimed path.
        """
        if disposition not in self._SALVAGEABLE_DISPOSITIONS:
            return
        if (
            terminal.get("status") != "succeeded"
            or terminal.get("publishable") is not True
            or not sum(
                int(terminal.get(key) or 0)
                for key in ("verified", "corrected", "selected", "deleted")
            )
        ):
            return
        schedule_epoch = self._lsp_epochs.get(task_id)
        if schedule_epoch is None:
            self.store.append(
                "lsp_salvage_refused", task_id=task_id, reason="epoch_unknown"
            )
            return
        if self._incomplete_edit_epoch > schedule_epoch:
            self.store.append(
                "lsp_salvage_refused", task_id=task_id,
                reason="unenumerated_edit",
            )
            return
        if not self.engine_state.graph_current:
            self.store.append(
                "lsp_salvage_refused", task_id=task_id,
                reason="live_not_current",
            )
            return
        candidate = str(terminal.get("candidate_path") or "")
        candidate_path = Path(candidate)
        if not candidate or not candidate_path.is_file():
            return
        scheduled = self._lsp_requests.get(task_id)
        if scheduled is None:
            self.store.append(
                "lsp_salvage_refused", task_id=task_id,
                reason="scheduled_request_missing",
            )
            return
        stale = frozenset(
            path for path, epoch in self._path_edit_epochs.items()
            if epoch > schedule_epoch
        )
        staged = candidate_path.with_name(candidate_path.name + ".salvage")
        for suffix in ("", "-wal", "-shm", "-journal"):
            piece = candidate_path.with_name(candidate_path.name + suffix)
            if piece.is_file():
                os.replace(piece, staged.with_name(staged.name + suffix))
        try:
            # Fold any WAL content into the staged file: the merge attaches
            # the candidate read-only, and uncheckpointed mutations would
            # otherwise be invisible - or the open refused outright. The
            # connection is closed explicitly: a context manager commits
            # but does not close, and the staged file must be unlinkable.
            checkpoint = sqlite3.connect(staged)
            try:
                checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                checkpoint.close()
        except sqlite3.Error:
            self.store.append(
                "lsp_salvage_refused", task_id=task_id,
                reason="candidate_unreadable",
            )
            return
        if self._wait_scheduler is None:
            self._wait_scheduler = ProviderWaitScheduler()
        name = f"lsp_salvage:{task_id}"
        if self._wait_scheduler.enqueue(
            name,
            self._salvage_work(
                base_graph_path=str(base.graph_path or ""),
                staged_candidate=staged,
                stale_paths=stale,
                scheduled=scheduled,
            ),
        ) in {"queued", "replaced"}:
            self.store.append(
                "lsp_salvage_scheduled", task_id=task_id,
                stale_path_count=len(stale),
                source_revision=request.source_revision,
            )

    def _salvage_work(
        self,
        *,
        base_graph_path: str,
        staged_candidate: Path,
        stale_paths: frozenset[str],
        scheduled: Any,
    ) -> Any:
        """Build the worker closure for one scoped merge.

        The worker reads engine state only to choose merge inputs; the
        drain-side CAS on the owner thread is what makes adoption safe.
        """
        layout = self.engine_state.layout
        repo_root_sha = hashlib.sha256(
            str(Path(self.repo_root).resolve()).encode("utf-8", "surrogatepass")
        ).hexdigest()
        repository_root_sha = hashlib.sha256(
            str(Path(scheduled.repository_root).resolve()).encode(
                "utf-8", "surrogatepass"
            )
        ).hexdigest()
        enrichments = layout.graph_root / "enrichments"

        def work() -> Mapping[str, Any]:
            from groundtruth.resolve import _rebuild_closure

            from .indexer import certify_scoped_merge
            from .scoped_merge import merge_lsp_candidate, merge_receipt

            if not staged_candidate.is_file():
                return {"outcome": "candidate_missing"}
            live_path = str(self.engine_state.graph_path or "")
            if (
                not live_path
                or not Path(live_path).is_file()
                or not Path(base_graph_path).is_file()
            ):
                return {"outcome": "input_missing"}
            if not self.engine_state.graph_current:
                return {"outcome": "live_not_current"}
            live_source = str(self.engine_state.source_revision or "")
            enrichments.mkdir(parents=True, exist_ok=True)
            out_dir = Path(tempfile.mkdtemp(prefix="merge-", dir=enrichments))
            out = out_dir / "graph.db"
            result = merge_lsp_candidate(
                base_graph=base_graph_path,
                candidate_graph=staged_candidate,
                live_graph=live_path,
                out_path=out,
                stale_paths=stale_paths,
            )
            if not result.applied:
                return {"outcome": "no_clean_mutations", **result.detail}
            if not _rebuild_closure(str(out)):
                return {"outcome": "closure_rebuild_failed", **result.detail}
            payload = merge_receipt(
                base_graph=base_graph_path,
                candidate_graph=staged_candidate,
                live_graph=live_path,
                out_path=out,
                source_revision=live_source,
                stale_paths=stale_paths,
                result=result,
                closure_rebuilt=True,
            )
            artifact = certify_scoped_merge(
                live_path,
                out,
                payload,
                expected_source_revision=live_source,
                expected_repository_root_sha256=repository_root_sha,
                layout=layout,
                expected_root_sha256=repo_root_sha,
                expected_task_id=os.environ.get("GT_TASK_ID", ""),
                expected_product_source_sha=os.environ.get(
                    "GT_PRODUCT_SOURCE_SHA", ""
                ),
            )
            if not artifact.success:
                return {
                    "outcome": "certification_failed",
                    "error": artifact.error,
                    **result.detail,
                }
            staged_candidate.unlink(missing_ok=True)
            return {
                "outcome": "merged",
                "graph_path": artifact.graph_path,
                "graph_revision": artifact.graph_revision,
                "live_graph_path": live_path,
                "source_revision": live_source,
                **result.detail,
            }

        return work

    # How long to let a cancelled promotion actually stop before giving up on
    # it. close(wait=False) cancels queued work but a RUNNING _execute keeps
    # going, and CPython joins non-daemon ThreadPoolExecutor threads at
    # interpreter exit - so an in-flight pass holds the process open past its
    # deadline and the supervisor SIGTERMs it, turning a scored submission into
    # an infra timeout on a run that had already succeeded.
    #
    # This could not happen before ad58b7d9: promotion was never scheduled, so
    # nothing was ever in flight at submit. It can now, with a 912MB copy and
    # thousands of callsites in front of it.
    #
    # The wait is bounded and its outcome is journaled either way, so "the
    # promotion stopped" and "we stopped waiting for it" never read alike.
    PROMOTION_DRAIN_SECONDS = 20.0

    def _append_observation(self, event: str, **fields: Any) -> bool:
        """Journal a best-effort diagnostic, unless the journal is sealed.

        `graph_build_mode` and `graph_rebuild_embedding` are written by the
        build worker, on a background thread, after the build finishes. Both
        are diagnostics: their call sites already sit inside
        `except Exception: pass` under "reporting never fails a rebuild".

        A build that outlives `session.close` writes them AFTER the run has
        sealed its reproducibility manifest, and receipt issuance then refuses
        the whole receipt -- `event_journal_conservation_failed` -- because the
        sealed count no longer matches the journal. Measured on
        rehearsal-repair-05: manifest 186 against 188 rows, and the receipt
        went out missing input and output tokens, every call counter, both
        bootstrap counters, total_cost and the treatment receipt. Rehearsal 04,
        with nothing in flight, issued a complete one.

        So the trade is a diagnostic against a receipt, and the diagnostic
        loses. This does NOT make the coordinator wait: `close(wait=False)` is
        deliberate, because an uncooperative in-flight pass otherwise holds the
        process open past its deadline and the supervisor turns a scored
        submission into an infra timeout. The build still runs and still
        publishes its graph; only its post-seal commentary is dropped, and the
        count of what was dropped is kept so the loss is visible rather than
        silent.
        """
        if self._journal_sealed:
            self._dropped_observations = getattr(self, "_dropped_observations", 0) + 1
            return False
        try:
            self.store.append(event, **fields)
        except Exception:  # noqa: BLE001 - reporting never fails a rebuild
            return False
        return True

    def close_graph_lifecycle(self) -> None:
        # A publication that completed after the last action was never
        # observed. `_record_graph_publication` runs only from
        # `record_repository_snapshot`, so an adoption after the final action
        # leaves no record even though it happened.
        #
        # Close is the last moment the run knows no further graph will be
        # adopted, and it runs before `session_closed` and before the manifest
        # is sealed, so recording here cannot disturb conservation. It is
        # correct-or-quiet: an unrecorded publication is the bug, and failing
        # to record one must not take the run down with it.
        try:
            self._poll_startup_index()
            self._poll_lsp_promotions()
        except Exception:  # noqa: BLE001 - draining never fails a close
            pass
        try:
            self._record_graph_publication()
        except Exception:  # noqa: BLE001 - observing a refresh never fails a close
            pass
        # Converge the LSP tier on the final graph while the workspace is
        # quiescent - the only window where a leg cannot lose the
        # publication race. Bounded and journaled; never fails a close.
        try:
            self._seal_lsp_convergence()
        except Exception:  # noqa: BLE001 - convergence never fails a close
            pass
        # From here the journal is sealed for observations. The run is about to
        # write `session_closed` and then seal its manifest; work still in
        # flight must not append past that point.
        self._journal_sealed = True
        if self._lsp_scheduler is not None:
            self._lsp_scheduler.close(wait=False)
            self._drain_promotions()
        if self._wait_scheduler is not None:
            # Queued work is dropped, a running job is not joined. The dense
            # store is written by the worker's own SQLite commits, so an
            # interrupted refresh leaves a valid earlier state, not a torn one.
            self._wait_scheduler.close(wait=False)

    def _drain_promotions(self) -> None:
        handles = list(getattr(self._lsp_scheduler, "_handles", ()) or ())
        pending = [handle for handle in handles if not getattr(handle, "done", True)]
        if not pending:
            return
        deadline = time.monotonic() + self.PROMOTION_DRAIN_SECONDS
        for handle in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                handle.terminal_receipt(timeout=remaining)
            except Exception:  # noqa: BLE001 - a drain must never fail a run
                pass
        stalled = sum(1 for handle in pending if not getattr(handle, "done", True))
        try:
            self.store.append(
                "lsp_promotion_drained",
                cancelled=len(pending),
                stalled=stalled,
                budget_seconds=self.PROMOTION_DRAIN_SECONDS,
            )
        except Exception:  # noqa: BLE001
            pass

    #: Bounded seal-time convergence. A mid-run leg races every publication
    #: that lands while it runs - near seal it almost always loses, the
    #: salvage merge conserves live's newer rows as skipped_*, and the churn
    #: defer then outlives the run (run 34904339448 sealed 26s after a
    #: partial salvage, inside the armed 60s window). At close the workspace
    #: is quiescent: no boundary can publish under a leg scheduled here, so
    #: this is the one schedule whose base is still current when it lands.
    #: The budget bounds the join, not the work - an unfinished leg keeps
    #: running into the wait=False close exactly as it does today.
    SEAL_LSP_CONVERGENCE_SECONDS = 90.0

    def _seal_drain_promotion_work(self, deadline: float) -> bool:
        """Join in-flight promotion work to the journal before it seals.

        Queued salvage merges are launched and drained (a merge already
        computed is coverage the run paid for) and a running leg gets a
        bounded join sliced so pending work keeps moving around it. Returns
        True when nothing promotion-shaped is left running or queued; the
        iteration cap is what terminates the loop under a frozen test clock
        if a worker stalls.
        """
        for _ in range(2000):
            if self._wait_scheduler is not None:
                self._wait_scheduler.begin_window()
                self._drain_wait_work()
            self._poll_lsp_promotions()
            active = self._lsp_active
            pending = (
                self._wait_scheduler.pending_names()
                if self._wait_scheduler is not None
                else ()
            )
            if active is None and not pending:
                self._record_graph_publication()
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if active is not None and self._lsp_scheduler is not None:
                handle = next(
                    (
                        h
                        for h in getattr(self._lsp_scheduler, "_handles", ())
                        or ()
                        if str(getattr(h, "task_id", "") or "") == str(active)
                    ),
                    None,
                )
                if handle is not None:
                    try:
                        handle.terminal_receipt(
                            timeout=min(0.25, remaining)
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    continue
            time.sleep(0.05)
        return False

    def _seal_lsp_convergence(self) -> None:
        """Offer the last adopted graph one promotion no edit can obsolete.

        The verdict reads the tier on the FINAL graph, and the final graph
        is minted in the run's highest-churn window - so mid-run scheduling
        alone can never guarantee it. Here, nothing remains that can move
        the base: drain what is already running, then offer the adopted
        graph a leg it has not already definitively answered. Refusals and
        stalls are journaled and the run seals exactly as it would have -
        an unconverged tier still reads DEGRADED, never WORKING-by-decree.
        """
        deadline = time.monotonic() + self.SEAL_LSP_CONVERGENCE_SECONDS
        if not self._seal_drain_promotion_work(deadline):
            self.store.append("lsp_seal_convergence", outcome="drain_incomplete")
            return
        if (
            not self.engine_state.graph_current
            or self._index_memory_defer_until > time.monotonic()
        ):
            return
        # Freeze the workspace NOW, not _latest_workspace_snapshot: the last
        # action can be an edit with no boundary after it, and a request
        # carrying a stale source_revision drains obsolete against the very
        # engine state it was meant to enrich. At close the workspace is
        # quiescent, so a fresh capture is exact.
        from .runtime_observation import capture_workspace

        try:
            snapshot = capture_workspace(Path(self.repo_root))
        except Exception as exc:  # noqa: BLE001 - a failed capture is data
            self.store.append(
                "lsp_seal_convergence", outcome="capture_failed",
                reason=str(exc)[:200],
            )
            return
        if str(snapshot.revision) != str(self.engine_state.source_revision):
            # The adopted graph is behind a workspace nothing witnessed -
            # no promotion can close that; the verdict stands on the graph
            # the run actually had.
            self.store.append(
                "lsp_seal_convergence", outcome="workspace_unwitnessed",
                graph_revision=str(self.engine_state.graph_revision or ""),
            )
            return
        # The churn defer exists to throttle scheduling under churn; at
        # close there is no churn left to wait out, so it is not honored.
        try:
            request = self._frozen_graph_input(snapshot)
        except Exception as exc:  # noqa: BLE001 - a refused freeze is data
            self.store.append(
                "lsp_seal_convergence", outcome="freeze_refused",
                reason=str(exc)[:200],
            )
            return
        base = GraphBuildArtifact(
            True,
            self.engine_state.graph_path,
            self.engine_state.graph_revision,
        )
        identity = self._lsp_enrichment_identity(request, base)
        prior = self._lsp_outcomes_by_base.get(str(base.graph_revision or ""))
        if identity in self._lsp_considered and (
            prior is not None and prior not in self._LSP_CHURN_OBSOLETE
        ):
            # This exact base+input already has its definitive answer in
            # the journal - published, no_op, failed all settle the tier
            # question a seal retry cannot improve. A lost race (obsolete*)
            # or a handle that never reported is the only debt a quiescent
            # graph can still repay.
            return
        self._lsp_considered.add(identity)
        try:
            handle = self._schedule_lsp_candidate(request, base)
        except Exception as exc:  # noqa: BLE001 - a refused schedule is data
            self.store.append(
                "lsp_seal_convergence", outcome="schedule_refused",
                reason=str(exc)[:200],
            )
            return
        self._lsp_bases[handle.task_id] = (request, base)
        self._lsp_active = handle.task_id
        try:
            handle.terminal_receipt(
                timeout=max(0.0, deadline - time.monotonic())
            )
            outcome = "terminated"
        except Exception:  # noqa: BLE001 - a stall is data, not a close failure
            outcome = "timeout"
        self._poll_lsp_promotions()
        self._record_graph_publication()
        # The terminal may have staged a salvage; it merges against a graph
        # nothing can move now, so it gets the same bounded window.
        self._seal_drain_promotion_work(deadline)
        self.store.append(
            "lsp_seal_convergence", outcome=outcome,
            task_id=str(getattr(handle, "task_id", "") or ""),
            graph_revision=str(base.graph_revision or ""),
        )

    def _record_graph_refresh_failure(
        self, cause: str, *, phase: str, severity: str = "ERROR",
        classification: str = "primary", retryable: bool = False,
    ) -> None:
        self.engine_state.mark_graph_failed()
        self.diagnostics.record(
            DiagnosticEvent.create(
                code=DiagnosticCode.GT_GRAPH_REFRESH_FAILED,
                severity=severity,
                phase=phase,
                subsystem="graph",
                capability="graph_freshness",
                task_id=self.task_id,
                classification=classification,
                cause=cause,
                impact="verified_claims_prohibited",
                recovery=(
                    "rebuild_graph_for_current_workspace_revision"
                    if not retryable
                    else "retry_after_index_memory_defer_window"
                ),
                retryable=retryable,
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

    def bind_provider_payload(
        self, payload: Mapping[str, Any], *, commit: bool = True,
        request_id: str = "", carry_pending: bool = True,
        pending_kinds: tuple[str, ...] | None = None,
    ) -> ProviderDelivery | None:
        """Validate the exact provider-bound payload; commit it when asked.

        ``commit=False`` runs the request/delivery consistency checks (digest,
        membership, exposure chain) without touching ledger or pending state:
        the caller runs it before the wire so a conflicted request never
        spends provider budget, then commits after the transport returns so a
        failed attempt cannot claim a delivery it never carried.

        ``carry_pending=False`` is for GT-internal calls (catalog offer,
        persistent plan): queued deliveries and typed observations belong to
        the agent conversation, so an internal request must neither match them
        into its payload nor fault them as unmatched - it leaves every queue
        intact for the next agent turn.

        ``pending_kinds`` narrows the evaluated queue to one kind: the
        select-catalog offer is delivered BY its internal request, so that
        call evaluates only ``select_catalog`` items - every other queued
        delivery stays intact for the agent. Evaluated items leave the queue
        whether they matched or not, exactly as a full agent bind drops them.
        """
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

        pending = (
            tuple(
                item for item in self._pending_provider_deliveries
                if item.kind in pending_kinds
            )
            if pending_kinds is not None
            else tuple(self._pending_provider_deliveries) if carry_pending else ()
        )
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
        if not commit:
            return None
        request_sha256, request_manifest, request_manifest_sha256, storage = (
            store_provider_request(self.store, payload)
        )
        if request_sha256 != digest:
            raise RuntimeError("provider request CAS identity mismatch")
        self.iteration += 1
        request_id = request_id or f"{self.task_id}-{self.iteration}-{digest[:16]}"
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
                feature_id=feature_for_evidence(item.kind),
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
            self._decision_delivery_identities.add(item.identity)
            if item.kind == "localization":
                self._localization_delivered = True
                self._delivered_localization_identities.add(item.identity)
                if item.target:
                    self._delivered_localization_targets.add(item.target)
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
            if item.kind == "churn_steer" and item.rendered == self._pending_churn_steer:
                self._pending_churn_steer = ""
                self.store.append(
                    "churn_steer", delivered=True, request_id=request_id,
                    delivery_identity=item.identity,
                )
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
        if pending_kinds is not None:
            evaluated = {item.identity for item in pending}
            self._pending_provider_deliveries = [
                item for item in self._pending_provider_deliveries
                if item.identity not in evaluated
            ]
            evaluated_dedup_keys = {
                item.dedup_key for item in pending if item.dedup_key
            }
            self._pending_exposures = {
                key: exposure
                for key, exposure in self._pending_exposures.items()
                if exposure.dedup_key not in evaluated_dedup_keys
            }
        elif carry_pending:
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

    def record_receipt(self, *args, **kwargs):
        receipt = super().record_receipt(*args, **kwargs)
        self.store.append("predicate_receipt_recorded", **receipt.evidence_summary())
        return receipt

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
        if execution is None:
            # A live filesystem assertion is a separate deterministic checker,
            # not a test inferred from command wording or output.
            green = []
            if self.repo_root:
                for predicate in self._compiled_predicates.values():
                    if (predicate.kind == "artifact" and predicate.scope
                            and self._live_artifact_exists(predicate.scope)):
                        footprint = self._live_artifact_footprint(predicate.scope)
                        if not footprint.complete:
                            continue
                        self.record_receipt(predicate.predicate_id, "gt_live_verify", 0,
                                            "artifact exists", epoch=self.workspace_epoch,
                                            status="GREEN", semantic=True, dependency_footprint=footprint,
                                            evidence_kind="artifact", coverage_basis="live_filesystem_assertion",
                                            source_revision_at_observation=self.repository_revision,
                                            action_index=action_index)
                        green.append(predicate.predicate_id)
            return tuple(green)
        if execution.outcome != "pass":
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
                evidence_kind=receipt.kind, coverage_basis=receipt.coverage_basis,
                source_revision_at_observation=self.repository_revision,
                action_index=action_index, execution_protocol=execution.protocol,
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
                producer_recorder=self._producer_invocation_record,
                producer_audit_context={
                    "observation_id": f"{self.task_id}:{self.iteration}",
                    "decision_id": f"miniswe:{self.iteration}",
                    "decision_context": "miniswe.tool_result",
                    "decision_open": True,
                },
            )
        return self._gateway_state

    def _producer_invocation_record(self, row: dict[str, Any]) -> None:
        """Journal GT core's ``gt.producer_invocation.v1`` rows.

        Without this recorder every producer evaluation — entered / delivered /
        abstained / suppressed with its reason — is built then dropped, which is
        why a conditional family looks identical whether it abstained correctly
        or never ran. Wiring it closes the eligibility-vs-delivery audit gap.
        """
        if self._journal_sealed or self.store is None:
            return
        safe = dict(row)
        evidence_types = [
            str(t) for t in safe.get("evidence_types") or () if str(t)
        ]
        feature_id = next(
            (
                feature_for_evidence(t)
                for t in evidence_types
                if feature_for_evidence(t)
            ),
            None,
        )
        # Dispatch-skip rows (layer ``producer.dispatch``) carry ``skip_reason``
        # and never computed a registry verdict: the key is simply absent.
        # Mapping the absent key to ``False`` would fabricate a denial the
        # registry never made - run 34766499875 journaled 40 such rows as
        # ``registry_allowed: false`` when the real story was
        # ``kill_switch_off`` / ``not_file_creation``. None stays unknown.
        registry_allowed = safe.get("registry_allowed")
        self.store.append(
            "producer_invocation",
            layer=str(safe.get("layer") or ""),
            skip_reason=str(safe.get("skip_reason") or ""),
            invocation_schema=str(safe.get("schema") or "gt.producer_invocation.v1"),
            invocation_id=str(safe.get("invocation_id") or ""),
            producer=str(safe.get("producer") or ""),
            evidence_types=tuple(evidence_types),
            feature_id=feature_id,
            invocation_site=str(safe.get("invocation_site") or ""),
            event_type=str(safe.get("event_type") or ""),
            subject=str(safe.get("subject") or ""),
            outcome=str(safe.get("outcome") or ""),
            action_index=int(safe.get("action_index") or 0),
            observation_id=str(safe.get("observation_id") or ""),
            decision_id=str(safe.get("decision_id") or ""),
            returned_fact=bool(safe.get("returned_fact")),
            returned_nothing=bool(safe.get("returned_nothing")),
            registry_allowed=(
                bool(registry_allowed) if registry_allowed is not None else None
            ),
            authority_result=str(safe.get("authority_result") or ""),
            dedup_result=str(safe.get("dedup_result") or ""),
            abstention_reasons=tuple(
                str(r) for r in safe.get("abstention_reasons") or () if str(r)
            ),
            suppression_reasons=tuple(
                str(r) for r in safe.get("suppression_reason") or () if str(r)
            ),
        )

    def bind_provider_response(
        self,
        response: Mapping[str, Any] | None = None,
        *,
        usage: Mapping[str, Any] | None = None,
        model: str = "",
        next_actions: Iterable[Mapping[str, Any]] = (),
        request_id: str = "",
    ) -> None:
        """Record the terminal provider response and bind it to the latest delivery.

        A delivery is only ``DELIVERED`` (not merely ``EXECUTED``) once the
        provider responded; this join is the difference between attribution and
        a transcript substring guess.

        ``request_id`` names the request this response answers. GT-internal
        bootstrap calls commit their delivery under the same task-scoped tag,
        so passing it joins this row to *this* request; without it the row
        borrows ``_latest_delivery`` — the previous agent request — and
        attributes this spend to a request that did not carry it.
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
        resolved_request_id = (
            request_id
            or (self._latest_delivery.request_id if self._latest_delivery else "")
        )
        # The response echoes the delivery ids carried by the request it
        # closes — an internal catalog call's response must echo the catalog
        # delivery its request row bound, or the request/response identity
        # audit flags a mismatch.
        resolved_delivery = (
            self._latest_delivery
            if self._latest_delivery is not None
            and self._latest_delivery.request_id == resolved_request_id
            else None
        )
        self.store.append(
            "provider_response",
            iteration=self.iteration,
            request_id=resolved_request_id,
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
                resolved_delivery.delivery_ids if resolved_delivery else ()
            ),
        )
        if resolved_request_id:
            self._terminal_request_ids.add(resolved_request_id)
        if mismatch:
            raise ProviderModelMismatch(
                "provider model mismatch: requested="
                f"{self.requested_model!r}, resolved={self.resolved_model!r}, "
                f"reported={reported_model!r}"
            )

    @staticmethod
    def _normalized_model_id(model: str) -> str:
        return normalized_model_id(model)

    def _provider_model_mismatch(self, reported_model: str) -> bool:
        if not reported_model or not (self.requested_model or self.resolved_model):
            return False
        expected = {
            self._normalized_model_id(item)
            for item in (self.requested_model, self.resolved_model, self.fallback_model)
            if item
        }
        return self._normalized_model_id(reported_model) not in expected

    def bind_provider_failure(
        self, error: BaseException, *, request_id: str = ""
    ) -> None:
        """Record a provider terminal failure symmetrically with a response.

        ``request_id`` names the failed request. GT-internal bootstrap calls
        commit their delivery under the same task-scoped tag; a failure before
        the commit has no delivery to join, and without the tag the row would
        borrow the previous agent request's identity.
        """
        from .run_diagnostics import redact_secret_text

        resolved_request_id = (
            request_id
            or (self._latest_delivery.request_id if self._latest_delivery else "")
        )
        # InterruptAgentFlow subclasses (FormatError, Submitted, ...) call
        # Exception.__init__ with no args: str(error) is "". The reason rides
        # in ``messages`` or, for GT's own format errors, ``gt_error_detail``.
        detail = str(getattr(error, "gt_error_detail", "") or "")
        if not detail:
            detail = str(error)
        if not detail:
            messages = getattr(error, "messages", ())
            if messages and isinstance(messages[0], Mapping):
                extra = messages[0].get("extra") or {}
                detail = str(messages[0].get("content") or "") or str(
                    extra.get("interrupt_type") or ""
                )
        self.store.append(
            "provider_failure",
            iteration=self.iteration,
            request_id=resolved_request_id,
            error_type=type(error).__name__,
            error=redact_secret_text(detail)[:500],
        )
        if resolved_request_id:
            self._terminal_request_ids.add(resolved_request_id)

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
            # These two ceilings are per DECISION, like the boundary counters
            # above, and were the only admission state that outlived one.
            # Run-scoped, they silently stop GT contributing: after two
            # cochange partners anywhere in a task no further partner is ever
            # offered, and a fact delivered once is refused at every later
            # decision even when it is the currently relevant fact. The effect
            # grows with run length, so it is worst on exactly the long tasks
            # where the evidence matters most, and it is invisible in the
            # result - the run simply receives less.
            self._decision_delivery_identities.clear()
            self._cochange_delivery_count = 0
        candidate_ordinal = self._boundary_delivery_count + 1
        per_delivery_limit = delivery_byte_limit(lane=lane, kind=kind)

        pending_identities = {item.identity for item in self._pending_provider_deliveries}
        if delivery_identity in pending_identities:
            return True
        # The two lanes mean different things, so they dedup over different
        # spans. Prompt-lane bytes are CONTEXT: re-sending identical context
        # in a later prompt is waste however relevant it still is, so that
        # lane dedups over the run. Sealed-lane bytes are EVIDENCE about the
        # decision at hand: the same current fact can be the right thing to
        # deliver again later, so that lane dedups over the decision.
        seen_identities = (
            self._model_visible_delivery_identities if lane == "prompt"
            else self._decision_delivery_identities
        )
        reason = ""
        if delivery_identity in seen_identities:
            reason = "duplicate_delivery_identity"
        elif kind == "localization" and (
            delivery_identity in self._delivered_localization_identities
            or any(
                item.kind == "localization"
                for item in self._pending_provider_deliveries
            )
        ):
            reason = "localization_fire_once"
        elif kind == "localization" and (
            len(self._delivered_localization_identities)
            >= MAX_LOCALIZATION_HARD_DELIVERIES
            or (
                len(self._delivered_localization_identities)
                >= MAX_LOCALIZATION_DELIVERIES
                and (
                    not target
                    or target in self._delivered_localization_targets
                )
            )
        ):
            # The soft cap refuses same-top churn once the task allowance is
            # spent; a novel top-ranked target is a shifted information need
            # and still admits. The hard bound caps even novel-top rotation.
            reason = "localization_task_ceiling"
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
                target=target,
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

    def resolve_delivery_recipe(
        self, recipe: Mapping[str, Any]
    ) -> tuple[str, str, dict[str, str]] | None:
        """Evaluate one delivery query against current state.

        Returns ``(rendered, source_revision_at_render, metadata)`` or ``None``
        when the query cannot produce current bytes - the caller then issues
        the typed skip. Graph/state-derived kinds only; execution facts are
        facts at a revision and never take this path.
        """
        kind = str(recipe.get("kind") or "")
        params = recipe.get("params") or {}
        rendered = ""
        metadata: dict[str, str] = {}
        artifact_reference: dict[str, Any] | None = None
        self._recipe_empty_reason = ""
        if kind == "cochange":
            from .cochange_evidence import cochange_prior_dose, cochange_row_count

            files = tuple(
                str(path) for path in params.get("files") or () if path
            )
            if not files:
                self._recipe_empty_reason = "no_files"
                return None
            # The cochanges table is derived from repository history at index
            # time and cannot appear mid-run: a depth-1 benchmark checkout has
            # none, so the lane is dead for the whole run. Probe it once, say
            # so once, and stop re-rendering per edit (run 34766499875 logged
            # 56 empty_render rows for one lane that could never produce).
            dead = getattr(self, "_cochange_history_dead", None)
            snapshot = self.graph_query_snapshot()
            graph_path = str(getattr(snapshot, "graph_path", "") or "")
            if dead is None and graph_path:
                dead = cochange_row_count(graph_path) == 0
                self._cochange_history_dead = dead
            if dead or not graph_path:
                self._recipe_empty_reason = (
                    "cochange_history_unavailable" if dead else "graph_unavailable"
                )
                return None
            rendered = cochange_prior_dose(self, files) or ""
            if not rendered:
                self._recipe_empty_reason = "no_cochange_partners"
            metadata = dict(self.consume_model_visible_delivery_metadata() or {})
        elif kind == "verification_plan":
            rendered = self._render_verification_plan_now(params)
            metadata = dict(self._pending_verification_metadata or {})
        elif kind == "localization":
            rendered = self._render_localization_now(params)
            metadata = self.localization_delivery_metadata()
            artifact_reference = getattr(
                self, "_localization_artifact_reference", None
            )
        else:
            self._recipe_empty_reason = "unknown_recipe_kind"
            return None
        if not rendered:
            if not self._recipe_empty_reason:
                self._recipe_empty_reason = "empty_render"
            return None
        return (
            rendered,
            str(self.repository_revision or ""),
            metadata,
            artifact_reference,
        )

    def _render_localization_now(self, params: Mapping[str, Any]) -> str:
        """Rank the localization query at delivery time.

        The agent's own search commands extend the issue text: they are the
        freshest statement of its information need, and folding them in is
        what makes a re-rank at admission worth the bytes (drift
        re-localization). Consuming the drift here - not at delivery-commit -
        keeps an identical re-rank from re-queueing the recipe every request.
        """
        query = str(self.issue_text or "")
        if not query:
            return ""
        drift = tuple(getattr(self, "_search_drift", ()))
        terms = self._drift_query_terms(drift)
        if terms:
            query = f"{query}\n{terms}"
        # The attempt itself consumes the drift and binds the revision: an
        # empty render at this (revision, drift) key must not re-queue the
        # scan on every request - it re-fires only when either side moves.
        self._localization_drift_at_render = drift
        self._localization_render_revision = str(self.repository_revision or "")
        self._localization_metadata = {}
        self._localization_chain = set(self._dedup_chain)
        self._localization_head = self._chain_head
        rendered = self._prepare_task_start_localization(query)
        if not rendered:
            return ""
        # The complete ranked render is evidence: it goes to the CAS before
        # compaction so the model-visible unit can reference full bytes while
        # only whole-line selections ever reach the provider.
        self._localization_artifact_reference = self._cas_reference(
            rendered, kind="localization"
        )
        compacted = compact_localization(rendered)
        if not compacted:
            return ""
        original_bytes = len(rendered.encode("utf-8"))
        compacted_bytes = len(compacted.encode("utf-8"))
        if compacted_bytes < original_bytes:
            self.store.append(
                "localization_compressed",
                original_bytes=original_bytes,
                delivered_bytes=compacted_bytes,
                lane_cap_bytes=1_400,
            )
        self._localization_candidate = compacted
        return compacted

    def _cas_reference(self, payload: str, *, kind: str) -> dict[str, Any]:
        """Content-address the payload into this task's evidence CAS."""
        try:
            from .output_evidence import EvidenceStore
            from .request_history import store_history_evidence

            return store_history_evidence(
                EvidenceStore(self.engine_state.layout.evidence_root),
                payload.encode("utf-8"),
                kind=kind,
            )
        except Exception:  # noqa: BLE001 - reference absence degrades to inline
            return {}

    @staticmethod
    def _drift_query_terms(commands: tuple[str, ...]) -> str:
        """The searchable payload of the agent's own search commands.

        Shell tokens only: the tool head and its flags are mechanics, not
        information need; everything else - patterns, path scopings - is
        attention signal.
        """
        terms: list[str] = []
        for command in commands:
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = command.split()
            for token in tokens[1:]:
                if token.startswith("-") or len(token) < 3:
                    continue
                terms.append(token)
        return " ".join(terms)

    def note_search_drift(self, command: str) -> None:
        """Record an agent search action as a localization drift signal.

        Bounded and deduped - this is a signal for the next admission-time
        re-rank, not a transcript of the agent's searches.
        """
        from .miniswe_evidence import _SEARCH_HEAD_RE

        command = (command or "").strip()
        if not command or not _SEARCH_HEAD_RE.search(command):
            return
        drift = tuple(getattr(self, "_search_drift", ()))
        if command in drift:
            return
        self._search_drift = (*drift, command)[-3:]

    def localization_drift_pending(self) -> bool:
        """True when unconsumed search drift exists since the last render."""
        drift = tuple(getattr(self, "_search_drift", ()))
        return bool(drift) and drift != self._localization_drift_at_render

    def localization_resolution_pending(self) -> bool:
        """True when no resolution attempt covers the current state.

        A resolved-empty localization is an answer, not an absence of one:
        it stays the answer until the workspace revision or the agent's
        search drift moves. ``None`` (never attempted) is always pending.
        """
        if self._localization_render_revision is None:
            return True
        if self._localization_render_revision != str(
            self.repository_revision or ""
        ):
            return True
        drift = tuple(getattr(self, "_search_drift", ()))
        return drift != self._localization_drift_at_render

    def task_start_localization(self, *, commit: bool = True) -> str:
        """Resolve the localization query now; legacy callers may admit.

        ``commit=False`` is a PEEK: ``prepare_select_catalog`` reads the
        resolved localization to build its catalog item, and the shadow path
        logs it. A peek must not consume the pending-resolution markers --
        ``_render_localization_now`` records ``_localization_render_revision``
        and ``_localization_drift_at_render`` on every attempt, and when a
        peek left them set, ``localization_resolution_pending()`` reported
        False forever after while ``_task_start_shipped`` could never latch,
        so the ranked localization and every later drift re-rank were dead on
        any run whose catalog was merely prepared. Restore the markers so the
        agent-facing admission still resolves and ships the answer itself.
        """
        if not commit:
            render_revision = self._localization_render_revision
            drift_at_render = self._localization_drift_at_render
            resolved = self.resolve_delivery_recipe(
                {"kind": "localization", "params": {"origin": "task_start"}}
            )
            self._localization_render_revision = render_revision
            self._localization_drift_at_render = drift_at_render
            return resolved[0] if resolved else ""
        resolved = self.resolve_delivery_recipe(
            {"kind": "localization", "params": {"origin": "task_start"}}
        )
        rendered = resolved[0] if resolved else ""
        if commit and rendered:
            if not self.admit_model_visible_delivery(
                lane="sealed", rendered=rendered, action_index=0,
                iteration=self.iteration, **self.localization_delivery_metadata(),
            ):
                return ""
            self.acknowledge_localization(rendered)
        return rendered

    def localization_delivery_metadata(self) -> dict[str, str]:
        metadata = dict(self._localization_metadata or {
            "kind": "localization", "dedup_key": "task-start-localization",
        })
        if self._localization_head:
            metadata["next_chain_head"] = self._localization_head
        return metadata

    def acknowledge_localization(self, rendered: str) -> None:
        if rendered and rendered == compact_localization(self._localization_candidate):
            self._dedup_chain.update(self._localization_chain)
            self._chain_head = self._localization_head

    def _prepare_task_start_localization(self, query: str) -> str:
        """Ranked localization for the current information need.

        ``query`` is the issue text plus any unconsumed search-drift terms,
        evaluated at the admission choke point. Sealed into the episode dedup
        chain so the reactive search path never re-delivers (fire-once
        preserved).
        """
        if not query:
            return ""
        if self.graph_db and self.graph_fresh:
            semantic = self._semantic_task_start_localization(query)
            if semantic:
                return semantic
            try:
                from groundtruth.runtime.adapters.miniswe import normalize_event

                from .miniswe_evidence import run_evidence_pipeline

                event = normalize_event(
                    query,
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
        return self._lexical_task_localization(query)

    def _semantic_task_start_localization(self, query: str) -> str:
        """Use the independent dense corpus when verified assets are configured."""
        model_dir = os.environ.get("GT_DENSE_MODEL_DIR", "").strip()
        snapshot = self.graph_query_snapshot()
        if not model_dir or not snapshot.graph_current or not snapshot.graph_path:
            return ""
        try:
            from .retrieval import RetrievalSource, hybrid_rank, render_semantic_localization

            ranking = hybrid_rank(
                snapshot.graph_path,
                query,
                k=8,
                use_dense=True,
                model_dir=model_dir,
                # The same task-pinned store the index writes, so retrieval
                # reads what the refresh populated instead of falling back to
                # default_store_path - which is derived from the graph path and
                # therefore empty on every republication. The env var stays as
                # an operator override; it is no longer how the run addresses
                # its own store.
                store_path=(
                    os.environ.get("GT_CONTRACT_EMBEDDING_INDEX")
                    or str(self.engine_state.layout.contract_store_path)
                ),
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
                    "qualified_name": str(
                        getattr(provenance, "qualified_name", "")
                        or getattr(provenance, "name", "")
                    ),
                    "label": str(getattr(provenance, "label", "") or ""),
                    "snippet": str(getattr(fused, "snippet", "") or ""),
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
            # This advisory names the graph revision it was ranked from, and
            # the receipt layer later demands exactly one surviving certified
            # graph matching it. Retention keeps live + 1 and would otherwise
            # evict it: measured, three deliveries all named the task-start
            # revision, and receipt issuance then raised
            # semantic_localization_certified_graph_missing before any receipt
            # existed. Pin it here, where the reference is created.
            self._pin_graph_revision(snapshot.graph_path, digest)
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

    def _lexical_task_localization(self, query: str) -> str:
        """Bounded advisory fallback with stable anchors and score reasons."""
        if not self.repo_root or not os.path.isdir(self.repo_root):
            return ""
        stop = {
            "and", "are", "change", "code", "fix", "for", "from", "must",
            "should", "task", "test", "tests", "that", "the", "this", "with",
        }
        terms = tuple(sorted({
            token.lower()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)
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
                text_lines = text.splitlines()
                snippet = (
                    text_lines[line - 1].strip() if 0 < line <= len(text_lines) else ""
                )
                rows.append({
                    "path": relative,
                    "line": line,
                    "anchor": f"{relative}:{line}",
                    "score": score,
                    "reasons": reasons,
                    "snippet": snippet,
                    "why": "matched " + ", ".join(
                        sorted(set(path_terms) | set(content_terms))
                    ),
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
                # The re-rank embeds the lexical candidates' file texts - the
                # graph is provenance, not an input. Refusing on a stale
                # graph journaled dense_index_ready query_ready=false rows
                # that landed last and read as dense_index_not_ready on the
                # product receipt, while the measured index was serving fine
                # (run 35178222629: 58 amend refusals, then every refused
                # re-localization poisoned the last dense receipt).
                dense_order, dense_receipt = rank_documents(
                    query_text=query,
                    documents={str(row["path"]): str(row["text"]) for row in candidates},
                    lexical_scores={
                        str(row["path"]): float(row["score"]) for row in candidates
                    },
                    model_dir=Path(os.environ["GT_DENSE_MODEL_DIR"]),
                    index_path=self.store.root / "dense-index.sqlite",
                    source_revision=self.repository_revision or "repository-start",
                    graph_revision=snapshot.graph_revision or "graph_unavailable",
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
        notes = []
        if scanned > 5_000:
            notes.append("repository scan capped at 5,000 files")
        if len(candidates) > len(ranked):
            notes.append(
                f"+{len(candidates) - len(ranked)} further candidate(s) not shown"
            )
        if notes and ranked:
            # Partial coverage must ride inside the certified item format -
            # a standalone note line would fail compact_localization's check
            # and take the whole delivery down with it.
            first = dict(ranked[0])
            first["why"] = f"{first.get('why') or 'matched'}; " + "; ".join(notes)
            ranked = [first, *ranked[1:]]
        artifact = {
            "schema": "gt.localization_advisory.v1",
            "issue_sha256": hashlib.sha256(
                query.encode("utf-8", "surrogatepass")
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
        from .retrieval import render_semantic_localization

        rendered = render_semantic_localization(ranked)
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
            # Only what changed since the last delivered delta. The full
            # unmet list already shipped with the contract (and persists in
            # the agent's history), so re-listing it on every invalidation
            # cycle re-sent identical bytes - 22 full lists on oxvg alone.
            previous = dict(self._last_delta_signature)
            transitions: list[tuple[str, str]] = []
            for obligation_id, predicate_id in self._predicate_by_obligation.items():
                current = self.predicate_status(predicate_id)
                before = previous.get(predicate_id)
                if before == current.value:
                    continue
                if current is PredicateStatus.GREEN:
                    transitions.append((obligation_id, "satisfied"))
                elif current is PredicateStatus.RED:
                    transitions.append((obligation_id, "failing check observed"))
                elif before == PredicateStatus.GREEN.value:
                    transitions.append((obligation_id, "re-opened by workspace change"))
                elif before is None:
                    transitions.append((obligation_id, "new obligation"))
                else:
                    transitions.append((obligation_id, "verification state reset"))
            text, _ = render_obligation_transitions(
                self.contract, transitions, max_chars=max_chars
            )
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
                evidence_kind="failing_execution", coverage_basis="lexically_associated_failure",
                source_revision_at_observation=self.repository_revision, action_index=action_index,
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

    def queue_churn_steer(self, rendered: str) -> None:
        """Hold the churn governor's steering text for the next request."""
        self._pending_churn_steer = rendered

    def build_churn_steer(self, stall_turns: int) -> str:
        """Render a corrective steer that names the next concrete step.

        A generic "stop churning" scold cannot re-power a stalled trajectory.
        The steer carries the strongest pending evidence instead: a bound
        check the agent can run to prove existing work, else the next unmet
        plan row, else a plain redirect. No internal ids or digests - only
        text the model can act on.
        """
        lines = [
            "GT_CHURN_STEER: you have spent "
            f"{stall_turns} consecutive actions without a workspace edit "
            "or check run."
        ]
        pending_ids = getattr(self, "_pending_check_ids", set())
        pending = [
            spec.command
            for check_id, spec in getattr(self, "_check_specs", {}).items()
            if check_id in pending_ids and spec.command
        ]
        if pending:
            lines.append(
                "A bound check is waiting to prove work you already did: "
                f"`{pending[0]}` - run it."
            )
        else:
            unmet = self.unmet_plan_rows()
            row = None
            plan = self.persistent_plan
            if plan is not None and unmet:
                row = next(
                    (item for item in plan.rows if item.row_id in unmet), None
                )
            if row is not None:
                lines.append(
                    f"Next unverified requirement: {row.text.strip()[:180]}"
                )
                if row.verification_command:
                    lines.append(
                        f"Prove it with: `{row.verification_command}`"
                    )
            else:
                lines.append(
                    "Return to the task now: make the code change and run "
                    "the project's checks."
                )
        lines.append("Continued churn terminates this run.")
        return "\n".join(lines)

    def build_verify_steer(self, streak: int) -> str:
        """Render a convergence steer for a failing-verification streak.

        Smoke20 pest/bandit-taint/testem-pl burned 2-6x tokens in edit->test
        loops where every check failed and nothing steered. The steer does
        not threaten termination - the agent is working - it redirects the
        loop toward the contract the checks keep failing against. Only text
        the model can act on; no internal ids or digests.
        """
        lines = [
            "GT_VERIFY_STEER: your last "
            f"{streak} verification runs all failed. The loop is not "
            "converging - stop iterating on the same approach.",
            "Re-read the task requirements and confirm you are running the "
            "checks the task actually grades, not a substitute harness. "
            "Then change the approach: fix the contract, not just the code.",
        ]
        unmet = self.unmet_plan_rows()
        plan = self.persistent_plan
        row = None
        if plan is not None and unmet:
            row = next(
                (item for item in plan.rows if item.row_id in unmet), None
            )
        if row is not None:
            lines.append(
                f"Next unverified requirement: {row.text.strip()[:180]}"
            )
            if row.verification_command:
                lines.append(
                    f"Prove it with: `{row.verification_command}`"
                )
        else:
            lines.append(
                "If every project check passes but the task is not done, "
                "you are verifying the wrong contract - find the graded one."
            )
        return "\n".join(lines)

    def prepare_churn_steer_delivery(self) -> str:
        """Admit a queued churn steer on the same transient-delivery contract
        as a recovery steer: retried until carried, cleared on exposure."""
        if not self._pending_churn_steer:
            return ""
        if self.admit_model_visible_delivery(
            lane="sealed", kind="churn_steer",
            rendered=self._pending_churn_steer,
            action_index=self.global_action, iteration=self.iteration,
            dedup_key=f"churn_steer:{self.iteration}",
        ):
            return self._pending_churn_steer
        return ""

    def signal_churn_abort(self) -> None:
        """Journal the abort and drop the flag the supervisor polls.

        The supervisor holds kill authority so the journals seal through the
        same terminal path as a deadline kill; the flag file is the cheap
        cross-process signal, the journal row is the auditable one.
        """
        governor = self.churn_governor
        if self._churn_abort_signaled:
            return
        self._churn_abort_signaled = True
        self.store.append(
            "churn_abort",
            turns_observed=governor.turns_observed,
            stall_turns=governor.stall_turns,
            steers_issued=governor.steers_issued,
        )
        flag = {
            "schema": "gt.churn_abort.v1",
            "task_id": self.task_id,
            "turns_observed": governor.turns_observed,
            "stall_turns": governor.stall_turns,
            "steers_issued": governor.steers_issued,
        }
        flag_path = self.engine_state.layout.state_root / "churn_abort.json"
        try:
            from gt_harness.canonical_io import atomic_write

            atomic_write(flag_path, json.dumps(flag).encode("utf-8"))
        except (OSError, ImportError):
            # The journal row is the durable signal; a flag write failure only
            # delays the supervisor's notice until its next journal-aware path.
            try:
                flag_path.write_text(json.dumps(flag), encoding="utf-8")
            except OSError:
                pass

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
                        evidence_kind="artifact", coverage_basis="live_filesystem_assertion",
                        source_revision_at_observation=self.repository_revision, action_index=self.global_action,
                    )
                    green.append(predicate.predicate_id)
                    continue
                if status is PredicateStatus.RED:
                    continue  # still genuinely missing
                self.record_receipt(
                    predicate.predicate_id, "gt_live_verify", 1,
                    "artifact missing", epoch=self.workspace_epoch,
                    status="RED", semantic=True,
                    evidence_kind="artifact", coverage_basis="live_filesystem_assertion",
                    source_revision_at_observation=self.repository_revision, action_index=self.global_action,
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
        self, predicate: Any, obligation_id: str, green: list[str],
    ) -> None:
        """Schedule a registered check; never replay a historical shell receipt."""
        self._reverify_after_edit({predicate.predicate_id: ""})

    def note_persistent_plan_bootstrap(self) -> None:
        """Record the one planning provider call, at the transport boundary.

        Same accounting rule as the catalog bootstrap: the call is spent the
        moment the transport returns, so it is counted there and a later
        failure does not decrement it. Counting successes instead of attempts
        would make ``terminal_requests`` disagree with the receipt's provider
        total and fail reconciliation closed.
        """
        self._persistent_plan_bootstrap_calls += 1

    def register_plan_predicates(self, plan) -> int:
        """Give the plan's derived rows the same status machinery as any other.

        A derived row is a behaviour the plan inferred from a requirement
        crossed with an existing mode. It is a real obligation, so it belongs in
        the contract: ``evaluate_passing_observation`` walks
        ``contract.obligations``, and a predicate outside it could never turn
        GREEN, only block.

        Legal only at epoch zero, before the first edit. Registering later would
        introduce an obligation that no existing receipt could have covered and
        retroactively un-verify work that was already proven.
        """
        self.plan_row_predicates = getattr(self, "plan_row_predicates", {})
        if self.contract is None or self.workspace_epoch != 0:
            return 0
        from .task_contract import Obligation, TaskContract, _typed_predicates

        derived = tuple(row for row in plan.rows if row.is_derived)
        known = {item.obligation_id for item in self.contract.obligations}
        additions = tuple(
            Obligation(
                obligation_id=row.row_id,
                text=row.text,
                source="persistent_plan_derived",
                subjects=(),
            )
            for row in derived
            if row.row_id not in known
        )
        if additions:
            obligations = self.contract.obligations + additions
            self.contract = TaskContract(
                role=self.contract.role,
                obligations=obligations,
                task_mode=self.contract.task_mode,
                predicates=_typed_predicates(obligations, self.contract.task_mode),
            )
            self._compiled_predicates = compile_obligation_predicates(self.contract)
            self._predicate_by_obligation = {
                item.obligation_id: item.predicate_id
                for item in self._compiled_predicates.values()
            }
            self._obligation_by_predicate = {
                value: key for key, value in self._predicate_by_obligation.items()
            }
            added_predicate_ids = []
            for obligation in additions:
                predicate_id = self._predicate_by_obligation.get(
                    obligation.obligation_id
                )
                if predicate_id and predicate_id not in self.predicates:
                    self.predicates[predicate_id] = Predicate(
                        predicate_id, obligation.text
                    )
                    self._status[predicate_id] = PredicateStatus.UNKNOWN
                if predicate_id:
                    added_predicate_ids.append(predicate_id)
            self._journal_compiled_predicates("plan", added_predicate_ids)

        # Map every plan row to the predicates that can satisfy it: its own
        # derived obligation, the merged ledger obligation minted for a line the
        # sentence extractor dropped, or the obligations that swallowed it.
        ledger = getattr(plan.inputs, "ledger", None)
        mapping: dict[str, tuple[str, ...]] = {}
        for row in plan.rows:
            candidates: list[str] = [row.row_id]
            if row.row_id.startswith("req-"):
                candidates.append("plan-" + row.row_id.removeprefix("req-"))
                ledger_row = ledger.by_id(row.row_id) if ledger is not None else None
                if ledger_row is not None:
                    candidates.extend(ledger_row.obligation_ids)
            predicate_ids = tuple(
                dict.fromkeys(
                    predicate_id
                    for obligation_id in candidates
                    if (predicate_id := self._predicate_by_obligation.get(obligation_id))
                )
            )
            if predicate_ids:
                mapping[row.row_id] = predicate_ids
        self.plan_row_predicates = mapping
        self.store.append(
            "persistent_plan_predicates",
            registered=len(additions),
            mapped_rows=len(mapping),
            unmapped_rows=[
                row.row_id for row in plan.rows if row.row_id not in mapping
            ][:20],
        )
        return len(additions)

    def unverified_plan_rows(self) -> tuple[str, ...]:
        """Plan rows whose bound-check evidence does not cover the current tree.

        `unmet_plan_rows` treats a row whose mapped predicates are all GREEN as
        met, but a row can still read UNVERIFIED when its check observation
        predates the last workspace change. Gate one submitted with all 28 rows
        UNVERIFIED, so both questions have to be asked separately.
        """
        plan = getattr(self, "persistent_plan", None)
        return tuple(
            row.row_id for row in getattr(plan, "rows", ())
            if self.plan_row_state(row.row_id) not in {"CHECK_PASSED", "PROVEN"}
        )

    def unmet_plan_rows(self) -> tuple[str, ...]:
        """Plan rows that still have no current evidence.

        Unmapped rows remain outstanding. A passing check and a mapped semantic
        assertion are separate evidence channels; neither cancels a current
        failure in the other channel.
        """
        mapping = getattr(self, "plan_row_predicates", {}) or {}
        plan = getattr(self, "persistent_plan", None)
        row_ids = {row.row_id for row in getattr(plan, "rows", ())} | set(mapping)
        unmet = set(self.unmet_predicates)
        outstanding = []
        for row_id in sorted(row_ids):
            state = self.plan_row_state(row_id)
            predicates = mapping.get(row_id, ())
            failed = any(self.predicate_status(key) == PredicateStatus.RED
                         for key in predicates if key in self.predicates)
            if state == "CHECK_FAILED" or failed:
                outstanding.append(row_id)
            elif state not in {"CHECK_PASSED", "PROVEN"} and (
                not predicates or any(key in unmet or key not in self.predicates for key in predicates)
            ):
                outstanding.append(row_id)
        return tuple(outstanding)

    def unmapped_red_predicates(self) -> tuple[str, ...]:
        """RED predicates no plan row claims -- blocking evidence the row census cannot see.

        `unmet_plan_rows` censuses plan rows and the predicates mapped to
        them. A predicate outside every row's mapping -- an obligation
        `_link_obligations` could not attach to any ledger line, or one
        registered after the map was built -- still goes RED through
        `evaluate_failing_observation`, and a submission over it ships over
        live failing evidence. Only RED is reported: an unmapped UNKNOWN is
        ignorance, and the gate never blocks on its own ignorance.
        """
        bound = {
            key
            for keys in (getattr(self, "plan_row_predicates", {}) or {}).values()
            for key in keys
        }
        return tuple(
            sorted(
                key
                for key in self.predicates
                if key not in bound
                and self._status.get(key) is PredicateStatus.RED
            )
        )

    def note_select_catalog_bootstrap(self) -> None:
        """Record one GT-internal bootstrap provider call at the transport boundary.

        The agent's n_calls doubles as its step and cost limit, so a GT-internal
        turn must never increment it. Receipt reconciliation therefore compares
        api_calls + bootstrap calls against admissions and responses. Usage and
        cost continue to include this call: it is real spend.

        COUNTS ATTEMPTS, NOT SUCCESSES. The call is spent the moment the
        transport returns, so this is incremented there and a failure in the
        response handling below it does not decrement. Every consumer wants
        exactly that -- all three are arithmetic on spend
        (`runtime_receipts.py` validates the range, records it verbatim, and
        adds it to `agent_turn_calls`). Nothing may read it as evidence that a
        catalog was obtained; that lives in the `select_catalog_lifecycle`
        rows, which carry the stage the ladder actually reached.
        """
        self._select_catalog_bootstrap_calls += 1

    def _last_plan_gate_decision(self) -> dict[str, Any] | None:
        """The gate's own last answer, read back from the journal it wrote.

        `persistent_plan/gate.py` computes `completion_proven` with the
        sighted-baseline whitelist and `GTSession.plan_submit_gate` journals
        the whole decision as a `plan_gate_decision` row -- and until now
        nothing outside the tests ever read it, so the terminal could call a
        submission verified while the gate's own receipt said completion was
        never proven. The journal is the only place the decision survives:
        the session owns the gate and this engine only receives the append.

        Correct-or-quiet. An unreadable, absent or truncated journal returns
        None, which is "no gate evidence", not "the gate said no" -- a
        plan-off run must keep its existing terminal rather than be relabelled
        by a file that was never written.
        """
        latest: dict[str, Any] | None = None
        try:
            with self.store.path.open(encoding="utf-8") as journal:
                for line in journal:
                    if '"plan_gate_decision"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict) and row.get("event") == "plan_gate_decision":
                        latest = row
        except OSError:
            return None
        return latest

    def final_state(self) -> dict[str, Any]:
        state = {"phase": self.phase, "epoch": self.workspace_epoch,
                 "predicate_evidence": {key: receipt.evidence_summary() for key, receipt in self._receipts.items()},
                 "verification_scope": "current_registered_predicates_and_plan_rows_not_official_reward",
                 "unmet_predicates": list(self.unmet_predicates),
                 "iterations": self.iteration,
                 "delivered_evidence": self._accepted_sealed_delivery_count,
                 "terminal_requests": len(self._terminal_request_ids),
                 "select_catalog_bootstrap_calls": self._select_catalog_bootstrap_calls,
                 "persistent_plan_bootstrap_calls": self._persistent_plan_bootstrap_calls,
                 "persistent_plan_status": (
                     getattr(self.persistent_plan, "status", "") or ""
                 ),
                 "unmet_plan_rows": list(self.unmet_plan_rows()),
                 "contract_shipped": self._contract_shipped,
                 "requested_model": self.requested_model,
                 "resolved_model": self.resolved_model,
                 "provider_reported_model": self.provider_reported_model,
                 "fallback_model": self.fallback_model,
                 "event_journal": self.store.receipt(),
                 "usage": dict(self._usage)}
        # The gate's verdict travels with the final state so the terminal can
        # be named honestly. `completion_proven` is tri-valued on purpose:
        # True/False when a gate decision exists, None when none does.
        decision = self._last_plan_gate_decision() or {}
        proven = decision.get("completion_proven")
        state["completion_proven"] = proven if isinstance(proven, bool) else None
        state["baseline_status"] = str(decision.get("baseline_status") or "")
        if self.phase == "FINISHED":
            # T2.2: an accepted submission with UNKNOWN obligations is NOT
            # verified. Only report verified when every obligation has positive
            # evidence (GREEN). UNKNOWN -> unverified (never silently success).
            #
            # The row ledger must agree. unmet_plan_rows treats a row whose
            # mapped predicates are all GREEN as met, but plan_row_state can
            # still read UNVERIFIED when the bound-check observation predates
            # the last workspace change -- gate-one submitted with all 28 rows
            # UNVERIFIED while this flag read True. Stale-revision evidence
            # does not verify the submitted tree.
            unverified_rows = list(self.unverified_plan_rows())
            state["unverified_plan_rows"] = unverified_rows
            state["verified"] = (
                bool(self.predicates)
                and not self.unmet_predicates
                and not self.unmet_plan_rows()
                and not unverified_rows
            )
            state["unverified_predicates"] = [
                pid for pid, st in self._status.items()
                if st is PredicateStatus.UNKNOWN
            ]
        self.store.append("final_state", **state)
        return state
