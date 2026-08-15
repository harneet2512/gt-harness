"""Inline Engine public contracts (IE-01).

Authoritative, versioned schemas for the GroundTruth Inline Engine phase. The
engine is the sole action-to-observation interface whenever ``ENGINE`` mode is
selected: Mini-SWE remains the planner and reasoner, GT receives an
already-selected action, binds it to a repository snapshot, runs only
deterministic producers justified by that action, executes an interception
decision, compiles one canonical observation, and binds the exact delivered
bytes to the provider exchange and the immediate next action.

Every schema is versioned, strictly validated on decode, serializable to a
plain ``dict``, and hash-stable for replay. No schema may silently drop a field
across a minor version: decoders reject unknown major versions and require the
declared fields on every minor.

Conventions:
- ``schema`` strings are ``gt.engine.<name>.v<N>``.
- ``_v1`` suffixes in class names are avoided; version lives in the schema
  string and in ``CONTRACTS_SCHEMA_VERSION``.
- Content hashes use SHA-256 over a canonical UTF-8 serialization.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

CONTRACTS_SCHEMA_VERSION = 1


def _sha256(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON serialization for hashing (sorted keys, UTF-8)."""
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EngineMode(Enum):
    """Runtime posture of the Inline Engine.

    - ``OFF``: byte- and state-equivalent to stock Mini-SWE (GT-off parity).
    - ``ENGINE``: every selected action crosses the engine boundary; GT owns
      the action-to-observation interface.
    - ``ADVISORY``: historical/diagnostic path only. It is never the ENGINE
      posture and never a default for a benchmark arm.
    """

    OFF = "off"
    ENGINE = "engine"
    ADVISORY = "advisory"


class ActionKind(Enum):
    """Closed typed kinds an action can be normalized to.

    ``SHELL`` is the literal fallback for any command the engine cannot type.
    Mutation kinds are the proposal/commit interface; arbitrary raw shell
    writes remain ``SHELL`` and receive postflight evidence only.
    """

    SHELL = "shell"
    FILE_READ = "file_read"
    SEARCH = "search"
    SYMBOL_DEFINITIONS = "symbol_definitions"
    SYMBOL_REFERENCES = "symbol_references"
    SYMBOL_CALLERS = "symbol_callers"
    LOCALIZE = "localize"
    CREATE_PROPOSAL = "create_proposal"
    EDIT_PROPOSAL = "edit_proposal"
    COMMIT_MUTATION = "commit_mutation"
    RUN_VERIFICATION = "run_verification"
    SYNTAX_QUERY = "syntax_query"
    SUBMIT = "submit"


class Decision(Enum):
    """The five interception decisions (decision law)."""

    PASS_THROUGH = "pass_through"
    AUGMENT = "augment"
    REPLACE = "replace"
    REWRITE = "rewrite"
    SUPPRESS = "suppress"


class TimingClass(Enum):
    """When model-visible bytes become available relative to the action.

    - ``PREFLIGHT``: before execution (mutation proposals, submit inspection).
    - ``POSTFLIGHT``: after execution, joined into the same observation before
      the next reasoning call.
    - ``TERMINAL``: at the submit/termination boundary.
    - ``PASSIVE``: measured and recorded, never model-visible.
    """

    PREFLIGHT = "preflight"
    POSTFLIGHT = "postflight"
    TERMINAL = "terminal"
    PASSIVE = "passive"


class ExecutionState(Enum):
    """ActionResult execution state."""

    EXECUTED = "executed"
    HELD = "held"
    REWRITTEN = "rewritten"
    SUPPRESSED = "suppressed"


class Fidelity(Enum):
    """Requested fidelity for an evidence artifact."""

    EXACT = "exact"
    SOUND_OVERAPPROXIMATE = "sound_overapproximate"
    EXECUTION_SPECIFIC = "execution_specific"
    RAW = "raw"


class LifecycleState(Enum):
    """Authoritative engine lifecycle (IE-09 transition table)."""

    SELECTED = "selected"
    NORMALIZED = "normalized"
    SNAPSHOT_BOUND = "snapshot_bound"
    PREFLIGHTED = "preflighted"
    DECIDED = "decided"
    EXECUTED = "executed"
    REPLACED = "replaced"
    REWRITTEN = "rewritten"
    SUPPRESSED = "suppressed"
    POSTFLIGHTED = "postflighted"
    COMPILED = "compiled"
    JOINED = "joined"
    DISPATCHED = "dispatched"
    PROVIDER_ACCEPTED = "provider_accepted"
    DELIVERED = "delivered"
    RESPONSE_COMMITTED = "response_committed"
    NEXT_ACTION_BOUND = "next_action_bound"
    RECEIPT_FINAL = "receipt_final"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------


def object_hash(value: Mapping[str, Any], schema: str) -> str:
    """Content-addressed hash of a schema's canonical serialization."""
    payload = {"schema": schema, **value}
    return _sha256(canonical_json(payload))


# ---------------------------------------------------------------------------
# 1. EngineMode binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineModeBinding:
    """Mode + per-capability overrides for one agent run.

    Invariant: when mode is OFF no engine code path may add model-visible
    bytes; when mode is ENGINE every action is normalized and every
    model-visible byte has a registered FACT owner.
    """

    mode: EngineMode
    schema: str = f"gt.engine.engine_mode_binding.v{CONTRACTS_SCHEMA_VERSION}"
    disabled_capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "mode": self.mode.value,
            "disabled_capabilities": list(self.disabled_capabilities),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EngineModeBinding":
        schema = str(value.get("schema") or "")
        _require_major(schema, "engine_mode_binding")
        return cls(
            mode=EngineMode(str(value.get("mode"))),
            schema=schema,
            disabled_capabilities=tuple(str(x) for x in value.get("disabled_capabilities") or ()),
        )


# ---------------------------------------------------------------------------
# 2. ActionRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionRequest:
    """The normalized, snapshot-bound request the engine receives for every action.

    Binds: action ID, typed kind, exact arguments, literal shell form, the
    repository snapshot token, a configuration digest, requested fidelity,
    batch ID, and sequence position within the batch.
    """

    action_id: str
    kind: ActionKind
    arguments: Mapping[str, Any]
    literal_shell_form: str
    snapshot_token: str
    configuration_digest: str
    requested_fidelity: Fidelity = Fidelity.RAW
    batch_id: str = ""
    sequence_position: int = 0
    raw_fallback: bool = True
    schema: str = f"gt.engine.action_request.v{CONTRACTS_SCHEMA_VERSION}"

    def request_hash(self) -> str:
        """Hash of the canonical serialization excluding no semantic field."""
        return object_hash(self.to_dict(), self.schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "action_id": self.action_id,
            "kind": self.kind.value,
            "arguments": dict(self.arguments),
            "literal_shell_form": self.literal_shell_form,
            "snapshot_token": self.snapshot_token,
            "configuration_digest": self.configuration_digest,
            "requested_fidelity": self.requested_fidelity.value,
            "batch_id": self.batch_id,
            "sequence_position": self.sequence_position,
            "raw_fallback": self.raw_fallback,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionRequest":
        schema = str(value.get("schema") or "")
        _require_major(schema, "action_request")
        return cls(
            action_id=str(value.get("action_id") or ""),
            kind=ActionKind(str(value.get("kind"))),
            arguments=dict(value.get("arguments") or {}),
            literal_shell_form=str(value.get("literal_shell_form") or ""),
            snapshot_token=str(value.get("snapshot_token") or ""),
            configuration_digest=str(value.get("configuration_digest") or ""),
            requested_fidelity=Fidelity(str(value.get("requested_fidelity") or "raw")),
            batch_id=str(value.get("batch_id") or ""),
            sequence_position=int(value.get("sequence_position") or 0),
            raw_fallback=bool(value.get("raw_fallback", True)),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 3. RepositorySnapshot / SnapshotToken
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepositorySnapshot:
    """Content-addressed repository state at one instant.

    The revision vector covers HEAD, dirty tracked files, untracked files, and
    relevant configuration; a single HEAD hash is never sufficient. The token
    is the content hash of this snapshot and authorizes one matching commit.
    """

    revision_heads: Mapping[str, str]
    dirty_files: Mapping[str, str]
    untracked_files: tuple[str, ...]
    configuration_digest: str
    complete: bool = True
    schema: str = f"gt.engine.repository_snapshot.v{CONTRACTS_SCHEMA_VERSION}"

    def token(self) -> str:
        return object_hash(self.to_dict(), self.schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "revision_heads": dict(self.revision_heads),
            "dirty_files": dict(self.dirty_files),
            "untracked_files": list(self.untracked_files),
            "configuration_digest": self.configuration_digest,
            "complete": self.complete,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepositorySnapshot":
        schema = str(value.get("schema") or "")
        _require_major(schema, "repository_snapshot")
        return cls(
            revision_heads=dict(value.get("revision_heads") or {}),
            dirty_files=dict(value.get("dirty_files") or {}),
            untracked_files=tuple(str(x) for x in value.get("untracked_files") or ()),
            configuration_digest=str(value.get("configuration_digest") or ""),
            complete=bool(value.get("complete", True)),
            schema=schema,
        )


SnapshotToken = str  # content hash of a RepositorySnapshot; authorizes one commit


# ---------------------------------------------------------------------------
# 4. EvidenceArtifact
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceArtifact:
    """Deterministic evidence bound to one action.

    Every model-visible artifact must carry a registered FACT owner, stable
    anchors, witnesses, producer identity/version, semantics, freshness,
    coverage, ambiguity, omissions, configuration, and a raw fallback.
    """

    artifact_id: str
    owner: str  # registered FACT owner identity (FactOwnerRegistration.owner)
    semantics: str
    content: Mapping[str, Any]
    anchors: tuple[str, ...] = ()
    witnesses: tuple[str, ...] = ()
    producer: str = ""
    producer_version: str = ""
    freshness_revision: str = ""
    coverage: str = ""  # "complete" or declared limitation
    ambiguity: tuple[str, ...] = ()
    omissions: tuple[str, ...] = ()
    configuration_digest: str = ""
    raw_fallback: str = ""  # bytes/ref preserved when replacement is revoked
    model_visible: bool = False
    schema: str = f"gt.engine.evidence_artifact.v{CONTRACTS_SCHEMA_VERSION}"

    def hash(self) -> str:
        return object_hash(self.to_dict(), self.schema)

    def render_content(self) -> str:
        """Deterministic text rendering of the artifact content for model bytes."""
        import json

        return json.dumps(self.content, sort_keys=True, ensure_ascii=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "artifact_id": self.artifact_id,
            "owner": self.owner,
            "semantics": self.semantics,
            "content": dict(self.content),
            "anchors": list(self.anchors),
            "witnesses": list(self.witnesses),
            "producer": self.producer,
            "producer_version": self.producer_version,
            "freshness_revision": self.freshness_revision,
            "coverage": self.coverage,
            "ambiguity": list(self.ambiguity),
            "omissions": list(self.omissions),
            "configuration_digest": self.configuration_digest,
            "raw_fallback": self.raw_fallback,
            "model_visible": self.model_visible,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceArtifact":
        schema = str(value.get("schema") or "")
        _require_major(schema, "evidence_artifact")
        return cls(
            artifact_id=str(value.get("artifact_id") or ""),
            owner=str(value.get("owner") or ""),
            semantics=str(value.get("semantics") or ""),
            content=dict(value.get("content") or {}),
            anchors=tuple(str(x) for x in value.get("anchors") or ()),
            witnesses=tuple(str(x) for x in value.get("witnesses") or ()),
            producer=str(value.get("producer") or ""),
            producer_version=str(value.get("producer_version") or ""),
            freshness_revision=str(value.get("freshness_revision") or ""),
            coverage=str(value.get("coverage") or ""),
            ambiguity=tuple(str(x) for x in value.get("ambiguity") or ()),
            omissions=tuple(str(x) for x in value.get("omissions") or ()),
            configuration_digest=str(value.get("configuration_digest") or ""),
            raw_fallback=str(value.get("raw_fallback") or ""),
            model_visible=bool(value.get("model_visible", False)),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 5. InterceptionDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InterceptionDecision:
    """What the engine decided to do with one selected action."""

    decision: Decision
    reason: str
    eligibility: tuple[str, ...] = ()
    decision_id: str = ""
    producers: tuple[str, ...] = ()
    schema: str = f"gt.engine.interception_decision.v{CONTRACTS_SCHEMA_VERSION}"

    def hash(self) -> str:
        return object_hash(self.to_dict(), self.schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "decision": self.decision.value,
            "reason": self.reason,
            "eligibility": list(self.eligibility),
            "decision_id": self.decision_id,
            "producers": list(self.producers),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InterceptionDecision":
        schema = str(value.get("schema") or "")
        _require_major(schema, "interception_decision")
        return cls(
            decision=Decision(str(value.get("decision"))),
            reason=str(value.get("reason") or ""),
            eligibility=tuple(str(x) for x in value.get("eligibility") or ()),
            decision_id=str(value.get("decision_id") or ""),
            producers=tuple(str(x) for x in value.get("producers") or ()),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 6. ActionResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionResult:
    """The result of executing one interception decision."""

    action_request: ActionRequest
    decision: InterceptionDecision
    execution_state: ExecutionState
    raw_result: str = ""
    raw_result_hash: str = ""
    evidence: tuple[EvidenceArtifact, ...] = ()
    pre_snapshot_token: str = ""
    post_snapshot_token: str = ""
    final_observation_hash: str = ""
    producer_latency_ms: int = 0
    schema: str = f"gt.engine.action_result.v{CONTRACTS_SCHEMA_VERSION}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "action_request": self.action_request.to_dict(),
            "decision": self.decision.to_dict(),
            "execution_state": self.execution_state.value,
            "raw_result": self.raw_result,
            "raw_result_hash": self.raw_result_hash,
            "evidence": [e.to_dict() for e in self.evidence],
            "pre_snapshot_token": self.pre_snapshot_token,
            "post_snapshot_token": self.post_snapshot_token,
            "final_observation_hash": self.final_observation_hash,
            "producer_latency_ms": self.producer_latency_ms,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionResult":
        schema = str(value.get("schema") or "")
        _require_major(schema, "action_result")
        return cls(
            action_request=ActionRequest.from_dict(value.get("action_request") or {}),
            decision=InterceptionDecision.from_dict(value.get("decision") or {}),
            execution_state=ExecutionState(str(value.get("execution_state"))),
            raw_result=str(value.get("raw_result") or ""),
            raw_result_hash=str(value.get("raw_result_hash") or ""),
            evidence=tuple(EvidenceArtifact.from_dict(e) for e in value.get("evidence") or ()),
            pre_snapshot_token=str(value.get("pre_snapshot_token") or ""),
            post_snapshot_token=str(value.get("post_snapshot_token") or ""),
            final_observation_hash=str(value.get("final_observation_hash") or ""),
            producer_latency_ms=int(value.get("producer_latency_ms") or 0),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 7. CanonicalObservation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalObservation:
    """One observation per selected action, deterministic ordering:

    1. action identity and decision; 2. raw result or declared replacement;
    3. FACT-backed deterministic evidence; 4. stable source anchors and
    witnesses; 5. freshness and semantic qualification; 6. ambiguity and
    omission declarations; 7. fallback or incompleteness notice; 8. receipt id.
    """

    action_request: ActionRequest
    decision: InterceptionDecision
    raw_result: str = ""
    replaced: str = ""  # declared replacement when decision is REPLACE/REWRITE
    evidence: tuple[EvidenceArtifact, ...] = ()
    anchors: tuple[str, ...] = ()
    witnesses: tuple[str, ...] = ()
    freshness_qualification: str = ""
    ambiguity: tuple[str, ...] = ()
    omissions: tuple[str, ...] = ()
    fallback_notice: str = ""
    receipt_id: str = ""
    raw_exact: bool = True
    schema: str = f"gt.engine.canonical_observation.v{CONTRACTS_SCHEMA_VERSION}"

    def render(self) -> str:
        """Render the canonical observation to its model-visible text form.

        Decision-aid shape (frontier context engineering): the actionable fact
        leads (attention), the raw result follows byte-exact as evidence, and
        affordances give the model a next step. A pure pass-through with no
        facts renders the raw output alone (no wrapper) — the raw IS the
        answer and any framing would be noise.
        """
        facts = [a for a in self.evidence if a.model_visible]
        decision = self.decision.decision

        if decision in (Decision.REPLACE, Decision.REWRITE):
            answer = self.replaced
            raw_part = ""
        elif self.raw_exact:
            answer = ""
            raw_part = self.raw_result
        else:
            answer = ""
            raw_part = ""

        if decision is Decision.PASS_THROUGH and not facts and not answer:
            # Pure literal: the raw output is the observation. No wrapper, no
            # GT framing — byte-exact and minimal.
            return raw_part

        block = [
            f"<result action=\"{self.action_request.action_id}\" "
            f"decision=\"{decision.value}\" receipt=\"{self.receipt_id}\">"
        ]
        if answer:
            block.append(f"<answer>{answer}</answer>")
        for artifact in facts:
            block.append(
                f"<fact owner=\"{artifact.owner}\" "
                f"semantics=\"{artifact.semantics}\">"
                f"{artifact.render_content()}</fact>"
            )
        affordances = _affordances(facts)
        if affordances:
            block.append("affordances: " + " | ".join(affordances))
        if self.anchors:
            block.append("anchors: " + " ".join(self.anchors))
        if self.ambiguity:
            block.append("ambiguity: " + "; ".join(self.ambiguity))
        if self.omissions:
            block.append("omissions: " + "; ".join(self.omissions))
        if self.fallback_notice:
            block.append(f"notice: {self.fallback_notice}")
        block.append("</result>")
        parts = ["\n".join(block)]
        if raw_part:
            parts.append(raw_part)
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "action_request": self.action_request.to_dict(),
            "decision": self.decision.to_dict(),
            "raw_result": self.raw_result,
            "replaced": self.replaced,
            "evidence": [e.to_dict() for e in self.evidence],
            "anchors": list(self.anchors),
            "witnesses": list(self.witnesses),
            "freshness_qualification": self.freshness_qualification,
            "ambiguity": list(self.ambiguity),
            "omissions": list(self.omissions),
            "fallback_notice": self.fallback_notice,
            "receipt_id": self.receipt_id,
            "raw_exact": self.raw_exact,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CanonicalObservation":
        schema = str(value.get("schema") or "")
        _require_major(schema, "canonical_observation")
        return cls(
            action_request=ActionRequest.from_dict(value.get("action_request") or {}),
            decision=InterceptionDecision.from_dict(value.get("decision") or {}),
            raw_result=str(value.get("raw_result") or ""),
            replaced=str(value.get("replaced") or ""),
            evidence=tuple(EvidenceArtifact.from_dict(e) for e in value.get("evidence") or ()),
            anchors=tuple(str(x) for x in value.get("anchors") or ()),
            witnesses=tuple(str(x) for x in value.get("witnesses") or ()),
            freshness_qualification=str(value.get("freshness_qualification") or ""),
            ambiguity=tuple(str(x) for x in value.get("ambiguity") or ()),
            omissions=tuple(str(x) for x in value.get("omissions") or ()),
            fallback_notice=str(value.get("fallback_notice") or ""),
            receipt_id=str(value.get("receipt_id") or ""),
            raw_exact=bool(value.get("raw_exact", True)),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 8-9. MutationProposal / MutationCommitRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationProposal:
    """Deterministic proposed mutation computed without mutating the tree."""

    proposal_id: str
    snapshot_token: str  # must match the tree the proposal was computed on
    target_path: str
    expected_preimage_hash: str
    proposed_postimage_hash: str
    proposed_patch: str = ""
    declared_postconditions: tuple[str, ...] = ()
    preflight: tuple[EvidenceArtifact, ...] = ()
    schema: str = f"gt.engine.mutation_proposal.v{CONTRACTS_SCHEMA_VERSION}"

    def hash(self) -> str:
        return object_hash(self.to_dict(), self.schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "proposal_id": self.proposal_id,
            "snapshot_token": self.snapshot_token,
            "target_path": self.target_path,
            "expected_preimage_hash": self.expected_preimage_hash,
            "proposed_postimage_hash": self.proposed_postimage_hash,
            "proposed_patch": self.proposed_patch,
            "declared_postconditions": list(self.declared_postconditions),
            "preflight": [e.to_dict() for e in self.preflight],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MutationProposal":
        schema = str(value.get("schema") or "")
        _require_major(schema, "mutation_proposal")
        return cls(
            proposal_id=str(value.get("proposal_id") or ""),
            snapshot_token=str(value.get("snapshot_token") or ""),
            target_path=str(value.get("target_path") or ""),
            expected_preimage_hash=str(value.get("expected_preimage_hash") or ""),
            proposed_postimage_hash=str(value.get("proposed_postimage_hash") or ""),
            proposed_patch=str(value.get("proposed_patch") or ""),
            declared_postconditions=tuple(str(x) for x in value.get("declared_postconditions") or ()),
            preflight=tuple(EvidenceArtifact.from_dict(e) for e in value.get("preflight") or ()),
            schema=schema,
        )


@dataclass(frozen=True)
class MutationCommitRequest:
    """COMMIT uses the same snapshot token the proposal was computed on."""

    proposal: MutationProposal
    commit_token: str  # hash of proposal + matching current snapshot token
    schema: str = f"gt.engine.mutation_commit_request.v{CONTRACTS_SCHEMA_VERSION}"

    def commit_hash(self) -> str:
        return object_hash(self.to_dict(), self.schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "proposal": self.proposal.to_dict(),
            "commit_token": self.commit_token,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MutationCommitRequest":
        schema = str(value.get("schema") or "")
        _require_major(schema, "mutation_commit_request")
        return cls(
            proposal=MutationProposal.from_dict(value.get("proposal") or {}),
            commit_token=str(value.get("commit_token") or ""),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 10. MutationCommitReceipt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationCommitReceipt:
    """Receipt of one atomic committed mutation write set."""

    commit_id: str
    proposal_id: str
    snapshot_token: str
    committed_files: Mapping[str, str]  # path -> committed content hash
    commit_hash: str
    atomic: bool = True
    rollback: tuple[str, ...] = ()
    postflight: tuple[EvidenceArtifact, ...] = ()
    schema: str = f"gt.engine.mutation_commit_receipt.v{CONTRACTS_SCHEMA_VERSION}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "commit_id": self.commit_id,
            "proposal_id": self.proposal_id,
            "snapshot_token": self.snapshot_token,
            "committed_files": dict(self.committed_files),
            "commit_hash": self.commit_hash,
            "atomic": self.atomic,
            "rollback": list(self.rollback),
            "postflight": [e.to_dict() for e in self.postflight],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MutationCommitReceipt":
        schema = str(value.get("schema") or "")
        _require_major(schema, "mutation_commit_receipt")
        return cls(
            commit_id=str(value.get("commit_id") or ""),
            proposal_id=str(value.get("proposal_id") or ""),
            snapshot_token=str(value.get("snapshot_token") or ""),
            committed_files=dict(value.get("committed_files") or {}),
            commit_hash=str(value.get("commit_hash") or ""),
            atomic=bool(value.get("atomic", True)),
            rollback=tuple(str(x) for x in value.get("rollback") or ()),
            postflight=tuple(EvidenceArtifact.from_dict(e) for e in value.get("postflight") or ()),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 11. DeliveryReceipt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliveryReceipt:
    """Binds the selected action to the provider exchange and next action.

    Required binding: selected action, pre-state, raw-result hash,
    transformation version, exact final observation bytes, provider
    request/response identity, and the immediate next action.
    """

    delivery_id: str
    action_request: ActionRequest
    pre_state_hash: str
    raw_result_hash: str
    transformation_version: str
    final_observation_bytes: str  # exact bytes appended to the conversation
    provider_request_id: str
    provider_response_id: str
    next_action_hash: str = ""
    schema: str = f"gt.engine.delivery_receipt.v{CONTRACTS_SCHEMA_VERSION}"

    def hash(self) -> str:
        return object_hash(self.to_dict(), self.schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "delivery_id": self.delivery_id,
            "action_request": self.action_request.to_dict(),
            "pre_state_hash": self.pre_state_hash,
            "raw_result_hash": self.raw_result_hash,
            "transformation_version": self.transformation_version,
            "final_observation_bytes": self.final_observation_bytes,
            "provider_request_id": self.provider_request_id,
            "provider_response_id": self.provider_response_id,
            "next_action_hash": self.next_action_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeliveryReceipt":
        schema = str(value.get("schema") or "")
        _require_major(schema, "delivery_receipt")
        return cls(
            delivery_id=str(value.get("delivery_id") or ""),
            action_request=ActionRequest.from_dict(value.get("action_request") or {}),
            pre_state_hash=str(value.get("pre_state_hash") or ""),
            raw_result_hash=str(value.get("raw_result_hash") or ""),
            transformation_version=str(value.get("transformation_version") or ""),
            final_observation_bytes=str(value.get("final_observation_bytes") or ""),
            provider_request_id=str(value.get("provider_request_id") or ""),
            provider_response_id=str(value.get("provider_response_id") or ""),
            next_action_hash=str(value.get("next_action_hash") or ""),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 12. ActionBatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionBatch:
    """A model response's action set with sequential dependency barriers."""

    batch_id: str
    actions: tuple[ActionRequest, ...]
    barriers_after: tuple[int, ...] = ()  # sequence positions that force barriers
    fail_open: bool = True
    schema: str = f"gt.engine.action_batch.v{CONTRACTS_SCHEMA_VERSION}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "batch_id": self.batch_id,
            "actions": [a.to_dict() for a in self.actions],
            "barriers_after": list(self.barriers_after),
            "fail_open": self.fail_open,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionBatch":
        schema = str(value.get("schema") or "")
        _require_major(schema, "action_batch")
        return cls(
            batch_id=str(value.get("batch_id") or ""),
            actions=tuple(ActionRequest.from_dict(a) for a in value.get("actions") or ()),
            barriers_after=tuple(int(x) for x in value.get("barriers_after") or ()),
            fail_open=bool(value.get("fail_open", True)),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# 13. FactOwnerRegistration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactOwnerRegistration:
    """Only registered FACT owners may add model-visible deterministic bytes."""

    owner: str  # FACT identity (e.g. syntax_result, submit_refusal)
    role: str  # "FACT" in the 129-row inventory
    producer: str
    producer_version: str
    semantics: str
    freshness_authority: str
    model_visible: bool
    schema: str = f"gt.engine.fact_owner_registration.v{CONTRACTS_SCHEMA_VERSION}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "owner": self.owner,
            "role": self.role,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "semantics": self.semantics,
            "freshness_authority": self.freshness_authority,
            "model_visible": self.model_visible,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FactOwnerRegistration":
        schema = str(value.get("schema") or "")
        _require_major(schema, "fact_owner_registration")
        return cls(
            owner=str(value.get("owner") or ""),
            role=str(value.get("role") or ""),
            producer=str(value.get("producer") or ""),
            producer_version=str(value.get("producer_version") or ""),
            semantics=str(value.get("semantics") or ""),
            freshness_authority=str(value.get("freshness_authority") or ""),
            model_visible=bool(value.get("model_visible", False)),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_major(schema: str, name: str) -> None:
    """Reject unknown major schema versions safely."""
    prefix = f"gt.engine.{name}.v"
    if not schema.startswith(prefix):
        raise ValueError(f"unknown {name} schema: {schema!r}")
    major = schema[len(prefix):].split(".")[0]
    try:
        major_num = int(major)
    except ValueError as exc:
        raise ValueError(f"malformed {name} schema: {schema!r}") from exc
    if major_num > CONTRACTS_SCHEMA_VERSION:
        raise ValueError(
            f"{name} schema {schema!r} is newer than supported "
            f"v{CONTRACTS_SCHEMA_VERSION}"
        )


def _affordances(facts: tuple["EvidenceArtifact", ...]) -> tuple[str, ...]:
    """Deterministic next-step affordances from fact anchors.

    Options, not recommendations: the model chooses whether to act. Derived
    from the fact's stable anchors so the pointer is exact (path, or path:line).
    """
    opts: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        for anchor in fact.anchors:
            if anchor and anchor not in seen:
                seen.add(anchor)
                opts.append(f"read({anchor})")
    return tuple(opts[:4])
