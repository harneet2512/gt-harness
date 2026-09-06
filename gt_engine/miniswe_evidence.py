"""Deterministic GT evidence pipeline for the Mini-SWE seam (W2).

Transforms one Mini-SWE command observation into a bounded decision-local set
of model-facing evidence capsules through the installed Groundtruth gateway:

    classify command -> derive semantic event -> normalize_event
        -> augment -> select -> render_envelope -> fits_budget

Sealing (``seal_delivery``) is done by the caller-owned episode chain so the
per-episode dedup chain lives on the adapter, exactly as the nano bridge's
``_deliver`` keeps state on the shared ``EpisodeState``.

Deterministic: no time, no randomness, and no writes. Repository reads
(changed-file bytes, viewed paths) are supplied by the seam as event
ingredients; an optional content-addressed observation is read through its
verified bounded iterator.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from groundtruth.runtime.adapters.miniswe import (
    fits_budget,
    normalize_event,
    render_envelope,
    seal_delivery,
)
from groundtruth.runtime.adapters.miniswe import (
    select as select_envelopes,
)
from groundtruth.runtime.evidence_envelope import EvidenceEnvelope
from groundtruth.runtime.gateway import (
    KIND_SEARCH,
    KIND_SUBMIT,
    ToolEvent,
    augment,
    classify_command,
)

from .output_evidence import EvidenceStore
from .request_history import store_history_evidence

if TYPE_CHECKING:
    from groundtruth.runtime.adapters.miniswe import StoredOutput

# The exact magic string Mini-SWE's LocalEnvironment._check_finished looks for
# in the FIRST line of a command's output to raise ``Submitted``. The seam
# intercepts the COMMAND before execution, so the submit gate must recognize
# the command that would produce it (typically ``echo "COMPLETE_..."``).
SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

# Search-command heads the gateway's classifier also recognizes, kept explicit
# here so the seam's boundary derivation is not narrower than the classifier.
_SEARCH_HEAD_RE = __import__("re").compile(
    r"(?i)(?:^|[;&|]\s*)(?:rg\b|grep\b|findstr\b|git\s+grep\b|\bfind\b|\bag\b|\back\b)"
)

_CHAIN_GENESIS = hashlib.sha256(b"miniswe-genesis").hexdigest()

_CURRENT_FAILURE_TYPES = frozenset({
    "covering_red", "covering_verdict", "recovery", "test_failure", "trace_frame",
})
_LOCALIZATION_TYPES = frozenset({"brief_localization", "localization"})
_WEAK_HISTORY_TYPES = frozenset({"cochange_partner", "cochange_prior"})


def _decision_group(envelope: EvidenceEnvelope) -> int:
    kind = str(envelope.evidence_type or "")
    if kind in _CURRENT_FAILURE_TYPES or "fail" in kind:
        return 0
    if kind in _LOCALIZATION_TYPES:
        return 2
    if kind in _WEAK_HISTORY_TYPES or "cochange" in kind:
        return 4
    return 3


def cap_evidence(text: str, max_chars: int = 1200) -> str:
    """B1: hard char cap on any evidence splice (large facts never linger big)."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...(truncated by GT)"


@dataclass(frozen=True)
class EvidenceDose:
    """One independently attributable candidate in a decision packet."""

    rendered: str
    envelope: EvidenceEnvelope
    previous_chain_head: str
    chain_head: str
    artifact_reference: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvidenceOmission:
    """A candidate that could not be represented within the decision budget."""

    evidence_type: str
    dedup_key: str
    reason: str


@dataclass(frozen=True)
class EvidenceResult:
    """A bounded ranked set with the former first-dose fields preserved."""

    rendered: str = ""
    envelope: EvidenceEnvelope | None = None
    sealed: bool = False
    chain_head: str = ""
    previous_chain_head: str = ""
    additional_doses: tuple[EvidenceDose, ...] = ()
    artifact_reference: dict[str, Any] | None = None
    omissions: tuple[EvidenceOmission, ...] = ()

    @property
    def doses(self) -> tuple[EvidenceDose, ...]:
        if not self.sealed or self.envelope is None:
            return ()
        return (
            EvidenceDose(
                self.rendered,
                self.envelope,
                self.previous_chain_head,
                self.chain_head,
                self.artifact_reference,
            ),
            *self.additional_doses,
        )


def is_submit_command(command: str) -> bool:
    """True iff executing this command would raise Mini-SWE's ``Submitted``.

    The marker can appear as the echoed literal, inside quotes, or split across
    adjacent shell string literals (``"COMPLETE_TASK_AND_SUBMIT_FINAL_""OUTPUT"``
    concatenates to the contiguous marker at the shell level). The check strips
    quote characters so those shell-joined forms still match. Over-matching is
    safe: the gate runs the real predicate gate before refusing, and a refused
    submit never executes.
    """
    if SUBMIT_MARKER in (command or ""):
        return True
    dequoted = (command or "").replace('"', "").replace("'", "")
    return SUBMIT_MARKER in dequoted


def _stored_output_from_artifact(reference: dict[str, Any]) -> StoredOutput:
    from groundtruth.runtime.adapters.miniswe import StoredOutput

    if reference.get("schema") != "gt.output_artifact.v1":
        raise ValueError("invalid output artifact schema")
    root = reference.get("root")
    digest = reference.get("sha256")
    length = reference.get("total_length")
    encoding = reference.get("encoding")
    if not isinstance(root, str) or not root:
        raise ValueError("invalid output artifact root")
    if not isinstance(digest, str):
        raise ValueError("invalid output artifact digest")
    if type(length) is not int or length < 0:
        raise ValueError("invalid output artifact length")
    if encoding not in {"utf-8", "base64"}:
        raise ValueError("invalid output artifact encoding")
    store = EvidenceStore(root)

    def open_bytes():
        return store.iter_bytes(
            digest,
            expected_length=length,
            expected_encoding=encoding,
        )

    return StoredOutput(digest, length, encoding, open_bytes)


def _search_empty(
    command: str,
    output: str,
    returncode: int | None,
    *,
    stored_output: StoredOutput | None = None,
) -> bool:
    """A search observation is ``failed_search`` when nothing came back.

    ``rg``/``grep`` use status 1 for a successful no-match search, so the empty
    output is the deciding signal, not the exit code alone.
    """
    if returncode not in (None, 0, 1):
        return False
    if stored_output is not None:
        substantive = False
        excluded = False
        line_non_whitespace = False
        marker_tail = ""
        marker = "[exit code"
        for fragment, line_end in stored_output.iter_line_fragments():
            probe = marker_tail + fragment
            if marker in probe:
                excluded = True
            marker_tail = probe[-(len(marker) - 1):]
            line_non_whitespace = line_non_whitespace or any(
                not char.isspace() for char in fragment
            )
            if line_end:
                if line_non_whitespace and not excluded:
                    substantive = True
                excluded = False
                line_non_whitespace = False
                marker_tail = ""
        return not substantive
    substantive = "".join(
        line for line in (output or "").splitlines()
        if "[exit code" not in line
    ).strip()
    return not substantive


def _derive_semantic_events(
    command: str,
    output: str,
    returncode: int | None,
    *,
    viewed_files: tuple[str, ...] = (),
    changed_files: tuple[str, ...] = (),
    edit_before_after: dict | None = None,
    test_outcome: str = "",
    stored_output: StoredOutput | None = None,
) -> tuple[str, ...]:
    """Derive the fine lifecycle boundary for one Mini-SWE command result."""
    events: list[str] = []
    # A witnessed workspace delta is authoritative regardless of the shell
    # spelling that caused it (Python scripts, copies, build tools and heredocs
    # are all common edit carriers). Preserve every applicable sub-boundary;
    # the previous precedence-only returns silently discarded real edits.
    if changed_files or edit_before_after:
        events.append("edit_result")
    if test_outcome:
        events.append(
            "test_result"
            if test_outcome in {"pass", "fail", "env_fail"}
            else "test_executed_no_tests"
        )
    if viewed_files:
        events.append("file_view")
    kind = classify_command(command or "")
    if kind == KIND_SEARCH or _SEARCH_HEAD_RE.search(command or ""):
        events.append(
            "failed_search"
            if _search_empty(
                command, output, returncode, stored_output=stored_output
            )
            else "search_result"
        )
    if kind == KIND_SUBMIT:
        events.append("submit")
    return tuple(dict.fromkeys(events))


def classify_event(
    command: str,
    output: str,
    returncode: int | None,
    *,
    action_index: int,
    cwd: str = "",
    changed_files: tuple[str, ...] = (),
    viewed_files: tuple[str, ...] = (),
    edit_before_after: dict | None = None,
    covering=None,
    test_outcome: str = "",
    test_protocol: str = "",
    state_revision: str = "",
    output_artifact: dict[str, Any] | None = None,
    stored_output: StoredOutput | None = None,
) -> ToolEvent:
    """Normalize one raw Mini-SWE action into the gateway's interception unit."""
    if output_artifact is not None:
        if stored_output is not None:
            raise ValueError("provide output_artifact or stored_output, not both")
        stored_output = _stored_output_from_artifact(output_artifact)
    semantic_events = _derive_semantic_events(
        command,
        output,
        returncode,
        viewed_files=viewed_files,
        changed_files=changed_files,
        edit_before_after=edit_before_after,
        test_outcome=test_outcome,
        stored_output=stored_output,
    )
    primary_boundary = semantic_events[0] if semantic_events else ""
    return normalize_event(
        command,
        output,
        returncode,
        action_index,
        cwd=cwd,
        changed_files=changed_files,
        viewed_files=viewed_files,
        edit_before_after=edit_before_after,
        covering=covering,
        semantic_events=semantic_events,
        primary_boundary=primary_boundary,
        test_outcome=test_outcome,
        test_protocol=test_protocol,
        state_revision=state_revision,
        **({"stored_output": stored_output} if stored_output is not None else {}),
    )


def run_evidence_pipeline(
    state,
    event: ToolEvent,
    *,
    dedup_chain: set[str],
    chain_head: str,
    episode_id: str,
    event_id: str,
    native: bool = False,
    model_prefix: bool = False,
    max_chars: int = 1400,
    max_doses: int = 4,
    artifact_store=None,
    commit: bool = False,
) -> EvidenceResult:
    """Build a bounded ranked packet: ``augment`` -> ``select`` -> render -> seal.

    Selection is decision-local. A fact delivered on an earlier request may be
    selected again when the current repository observation produces it again;
    only duplicate keys in this decision are collapsed by canonical ``select``.
    Each dose is rendered and sealed independently, so admission can preserve a
    one-to-one fact/bytes/provenance chain. The default is a proposal: callers
    must opt into immediate commit explicitly, while provider-facing consumers
    commit only after exact request admission. An oversized candidate abstains
    without suppressing smaller candidates behind it.
    """
    envelopes = augment(event, state)
    if not envelopes or max_doses <= 0:
        return EvidenceResult()
    ranked = select_envelopes(
        envelopes,
        max_doses=len(envelopes),
        multidose=True,
        # Lifetime delivery is audit history, not a current-decision exclusion.
        recently_delivered=frozenset(),
        observed_event=event.primary_boundary,
    )
    # Canonical select decides membership and preserves its calibrated order
    # within a class. Packet composition then reserves the earlier slots for
    # current failures, followed by localization and actionable consequences;
    # historical priors use only remaining capacity.
    ranked = [
        candidate
        for _, candidate in sorted(
            enumerate(ranked), key=lambda pair: (_decision_group(pair[1]), pair[0])
        )
    ]
    target_chain = dedup_chain if commit else set(dedup_chain)
    parent_head = chain_head or _CHAIN_GENESIS
    exposure_parent_head = chain_head
    doses: list[EvidenceDose] = []
    omissions: list[EvidenceOmission] = []
    for candidate in ranked:
        rendered = render_envelope(candidate, native=native)
        if model_prefix:
            rendered = f"[GT_EVIDENCE:{candidate.evidence_type}]\n{rendered}"
        artifact_reference = None
        if artifact_store is not None:
            artifact_reference = store_history_evidence(
                artifact_store, rendered.encode("utf-8"), kind="decision_evidence"
            )
        if not fits_budget(rendered, max_delta_chars=max_chars):
            if artifact_store is None:
                omissions.append(EvidenceOmission(
                    str(candidate.evidence_type or ""),
                    str(candidate.dedup_key or ""),
                    "artifact_store_unavailable",
                ))
                continue
            visible_reference = {
                "schema": artifact_reference["schema"],
                "sha256": artifact_reference["sha256"],
                "total_length": artifact_reference["total_length"],
                "encoding": artifact_reference["encoding"],
                "kind": artifact_reference["kind"],
                "retrieval_command": artifact_reference["retrieval_command"],
                "omission_reason": "decision_byte_budget_exceeded",
            }
            rendered = (
                f"[GT_EVIDENCE_REFERENCE:{candidate.evidence_type}]\n"
                + json.dumps(
                    visible_reference, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True,
                )
            )
            if not fits_budget(rendered, max_delta_chars=max_chars):
                omissions.append(EvidenceOmission(
                    str(candidate.evidence_type or ""),
                    str(candidate.dedup_key or ""),
                    "artifact_reference_byte_budget_exceeded",
                ))
                continue
        sealed, new_head = seal_delivery(
            candidate,
            episode_id=episode_id,
            event_id=event_id,
            parent_hash=parent_head,
            rendered_bytes=rendered.encode("utf-8"),
            renderer_id="miniswe.native" if native else "miniswe.generic",
            dedup_chain=target_chain,
        )
        doses.append(EvidenceDose(
            rendered, sealed, exposure_parent_head, new_head, artifact_reference
        ))
        parent_head = new_head
        exposure_parent_head = new_head
        if len(doses) >= max_doses:
            break
    if not doses:
        return EvidenceResult(omissions=tuple(omissions))
    first, *additional = doses
    return EvidenceResult(
        rendered=first.rendered,
        envelope=first.envelope,
        sealed=True,
        chain_head=first.chain_head,
        previous_chain_head=first.previous_chain_head,
        additional_doses=tuple(additional),
        artifact_reference=first.artifact_reference,
        omissions=tuple(omissions),
    )


def workspace_revision(cwd: str, paths: tuple[str, ...] = ()) -> str:
    """A cheap content revision for the state_revision field (best-effort)."""
    parts: list[str] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                parts.append(handle.read())
        except OSError:
            continue
    if not parts:
        return ""
    digest = hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()[:16]
    return digest


__all__ = [
    "SUBMIT_MARKER",
    "EvidenceDose",
    "EvidenceOmission",
    "EvidenceResult",
    "is_submit_command",
    "classify_event",
    "run_evidence_pipeline",
    "workspace_revision",
]
