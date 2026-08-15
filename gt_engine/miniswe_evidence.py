"""Deterministic GT evidence pipeline for the Mini-SWE seam (W2).

Transforms one Mini-SWE command observation into at most one model-facing
evidence capsule through the installed Groundtruth gateway:

    classify command -> derive semantic event -> normalize_event
        -> augment -> arbitrate -> render_envelope -> fits_budget

Sealing (``seal_delivery``) is done by the caller-owned episode chain so the
per-episode dedup chain lives on the adapter, exactly as the nano bridge's
``_deliver`` keeps state on the shared ``EpisodeState``.

Pure and deterministic: no time, no randomness, no I/O. Repository reads
(changed-file bytes, viewed paths) are supplied by the seam as event
ingredients.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from groundtruth.runtime.adapters.miniswe import (
    arbitrate,
    fits_budget,
    normalize_event,
    render_envelope,
    seal_delivery,
)
from groundtruth.runtime.evidence_envelope import EvidenceEnvelope
from groundtruth.runtime.gateway import (
    KIND_EDIT,
    KIND_SEARCH,
    KIND_SUBMIT,
    ToolEvent,
    augment,
    classify_command,
)

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


def cap_evidence(text: str, max_chars: int = 1200) -> str:
    """B1: hard char cap on any evidence splice (large facts never linger big)."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...(truncated by GT)"


@dataclass(frozen=True)
class EvidenceResult:
    rendered: str = ""
    envelope: EvidenceEnvelope | None = None
    sealed: bool = False
    chain_head: str = ""


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


def _search_empty(command: str, output: str, returncode: int | None) -> bool:
    """A search observation is ``failed_search`` when nothing came back.

    ``rg``/``grep`` use status 1 for a successful no-match search, so the empty
    output is the deciding signal, not the exit code alone.
    """
    if returncode not in (None, 0, 1):
        return False
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
) -> tuple[str, ...]:
    """Derive the fine lifecycle boundary for one Mini-SWE command result."""
    if test_outcome:
        return (
            ("test_result",)
            if test_outcome in {"pass", "fail", "env_fail"}
            else ("test_executed_no_tests",)
        )
    if viewed_files:
        return ("file_view",)
    kind = classify_command(command or "")
    if kind == KIND_SEARCH or _SEARCH_HEAD_RE.search(command or ""):
        return (
            ("failed_search",)
            if _search_empty(command, output, returncode)
            else ("search_result",)
        )
    if kind == KIND_EDIT and (changed_files or edit_before_after):
        return ("edit_result",)
    if kind == KIND_SUBMIT:
        return ("submit",)
    return ()


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
) -> ToolEvent:
    """Normalize one raw Mini-SWE action into the gateway's interception unit."""
    semantic_events = _derive_semantic_events(
        command,
        output,
        returncode,
        viewed_files=viewed_files,
        changed_files=changed_files,
        edit_before_after=edit_before_after,
        test_outcome=test_outcome,
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
) -> EvidenceResult:
    """The one-dose evidence call: ``augment`` -> ``arbitrate`` -> render -> seal.

    Returns an empty ``EvidenceResult`` on correct-quiet (no envelopes, no
    winner, or an over-budget delta). Sealing mutates ``dedup_chain`` in place
    so a delivered fact is never re-offered in this episode.
    """
    envelopes = augment(event, state)
    if not envelopes:
        return EvidenceResult()
    winner = arbitrate(
        envelopes,
        recently_delivered=frozenset(dedup_chain),
        observed_event=event.primary_boundary,
    )
    if winner is None:
        return EvidenceResult()
    if winner.dedup_key in dedup_chain:
        # Fire-once enforcement: an already-delivered fact (the issue-fixed
        # localization answer) is re-produced by the gateway on every search
        # but must NOT be re-sealed into the history 80+ times. One delivery
        # per episode; repeats are correct-or-quiet.
        return EvidenceResult()
    rendered = render_envelope(winner, native=native)
    if not fits_budget(rendered):
        return EvidenceResult()
    sealed, new_head = seal_delivery(
        winner,
        episode_id=episode_id,
        event_id=event_id,
        parent_hash=chain_head or _CHAIN_GENESIS,
        rendered_bytes=rendered.encode("utf-8"),
        renderer_id="miniswe.native" if native else "miniswe.generic",
        dedup_chain=dedup_chain,
    )
    return EvidenceResult(
        rendered=rendered,
        envelope=winner,
        sealed=True,
        chain_head=new_head,
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
    "EvidenceResult",
    "is_submit_command",
    "classify_event",
    "run_evidence_pipeline",
    "workspace_revision",
]
