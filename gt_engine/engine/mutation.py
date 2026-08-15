"""Mutation proposal/commit protocol (IE-07).

Implements the PROPOSE -> PREFLIGHT -> COMMIT pipeline with compare-and-swap
semantics over one snapshot token:

- PROPOSE is pure: it binds a snapshot token, an expected preimage hash, and
  the SHA-256 of a proposed postimage into an immutable ``MutationProposal``
  without touching the tree.
- PREFLIGHT runs registered deterministic producers over the proposed bytes
  (never the live tree) and collects ``EvidenceArtifact`` results.
- COMMIT is the CAS step. The current snapshot token must equal the
  proposal's token; the on-tree preimage must hash to the expected value;
  the full write set is then applied through one atomic seam (``write_fn``)
  or directly to the in-memory ``tree``. Any mid-apply failure restores the
  prior bytes of every affected path and raises ``AtomicWriteFailed``.

The protocol is provider-free and deterministic: no network, no provider
calls, and no filesystem I/O except through ``write_fn``. Postimage bytes are
never stored on the proposal (only their hash), so COMMIT receives them
through a keyword-only ``postimage_bytes``/``write_set`` argument.

Rename semantics: ``MutationProposal`` is a frozen contract with a single
``target_path`` and cannot carry a rename source, so a rename is expressed as
delete+create in the write set. ``build_write_set`` accepts a ``rename_from``
convenience parameter that expands ``{old: None, target: bytes}``.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Iterable, Mapping

from .contracts import CONTRACTS_SCHEMA_VERSION, EvidenceArtifact, MutationCommitReceipt, MutationProposal, object_hash

#: Expected preimage meaning "the target must not exist on the tree".
ABSENT_PREIMAGE = ""


class MutationError(Exception):
    """Base class for mutation protocol failures."""


class StaleProposal(MutationError):
    """Raised when the current snapshot token differs from the proposal's."""


class PreimageMismatch(MutationError):
    """Raised when the on-tree preimage does not hash to the expected value."""


class AtomicWriteFailed(MutationError):
    """Raised when a write set application fails partway; the tree was rolled back."""

    def __init__(self, rolled_back: Iterable[str], cause: Exception) -> None:
        self.rolled_back: tuple[str, ...] = tuple(rolled_back)
        self.cause: Exception = cause
        super().__init__(f"atomic write failed; rolled back {self.rolled_back}: {cause}")


def sha256_hex(payload: bytes) -> str:
    """SHA-256 hex digest of raw bytes (repo sha256 helper convention)."""
    return hashlib.sha256(payload).hexdigest()


def propose(
    snapshot_token: str,
    target_path: str,
    expected_preimage_hash: str,
    proposed_postimage_bytes_or_patch: bytes | str | None,
    declared_postconditions: tuple[str, ...] = (),
) -> MutationProposal:
    """Build a pure, content-addressed mutation proposal without touching the tree.

    ``proposed_postimage_bytes_or_patch`` is hashed as-is when bytes; a str is
    UTF-8 encoded for hashing and stored verbatim as ``proposed_patch``. Pass
    ``None`` to express a deletion of ``target_path`` (no postimage). An empty
    ``expected_preimage_hash`` declares the target must not yet exist.
    """
    if isinstance(proposed_postimage_bytes_or_patch, str):
        postimage_bytes = proposed_postimage_bytes_or_patch.encode("utf-8")
        proposed_patch = proposed_postimage_bytes_or_patch
    else:
        postimage_bytes = proposed_postimage_bytes_or_patch
        proposed_patch = ""
    proposed_postimage_hash = sha256_hex(postimage_bytes) if postimage_bytes is not None else ""
    proposal = MutationProposal(
        proposal_id=_proposal_id(
            snapshot_token,
            target_path,
            expected_preimage_hash,
            proposed_postimage_hash,
            proposed_patch,
            declared_postconditions,
        ),
        snapshot_token=snapshot_token,
        target_path=target_path,
        expected_preimage_hash=expected_preimage_hash,
        proposed_postimage_hash=proposed_postimage_hash,
        proposed_patch=proposed_patch,
        declared_postconditions=declared_postconditions,
    )
    return proposal


def preflight(
    proposal: MutationProposal,
    preflight_producers: Iterable[Callable[[str, bytes], EvidenceArtifact | None]],
    *,
    postimage_bytes: bytes | None = None,
    write_set: Mapping[str, bytes | None] | None = None,
) -> tuple[EvidenceArtifact, ...]:
    """Run deterministic producers over the proposed bytes without mutating the tree.

    Each producer is called as ``producer(target_path, proposed_bytes)`` and may
    return one ``EvidenceArtifact`` or ``None``. Producers are never given the
    live tree, so preflight cannot observe or disturb on-disk state. Artifacts
    are returned in write-set order.
    """
    effective = write_set if write_set is not None else build_write_set(proposal, postimage_bytes)
    artifacts: list[EvidenceArtifact] = []
    for path, content in effective.items():
        if content is None:
            continue
        for producer in preflight_producers:
            artifact = producer(path, content)
            if artifact is not None:
                artifacts.append(artifact)
    return tuple(artifacts)


def build_write_set(
    proposal: MutationProposal,
    postimage_bytes: bytes | None,
    *,
    rename_from: str | None = None,
) -> dict[str, bytes | None]:
    """Build the write set for a proposal: ``{path: bytes}`` with ``None`` = delete.

    The primary entry is ``proposal.target_path``. ``rename_from`` expands a
    rename to delete+create: the source path is deleted and the destination
    receives ``postimage_bytes``.
    """
    write_set: dict[str, bytes | None] = {proposal.target_path: postimage_bytes}
    if rename_from is not None:
        write_set[rename_from] = None
    return write_set


def commit(
    proposal: MutationProposal,
    current_snapshot_token: str,
    tree: dict[str, bytes],
    write_fn: Callable[[dict[str, bytes], Mapping[str, bytes | None]], None] | None = None,
    *,
    postimage_bytes: bytes | None = None,
    write_set: Mapping[str, bytes | None] | None = None,
    rename_from: str | None = None,
) -> MutationCommitReceipt:
    """Compare-and-swap commit of the proposal's write set.

    Requires the current snapshot token to match the proposal's token and the
    on-tree preimage of ``target_path`` to hash to ``expected_preimage_hash``.
    The full write set is then applied either through ``write_fn(tree,
    write_set)`` (the atomic seam) or directly to the in-memory ``tree``. On
    any mid-apply exception the prior bytes of every affected path are restored
    and ``AtomicWriteFailed`` is raised with the rolled-back paths. Returns a
    content-addressed ``MutationCommitReceipt`` (commit_id == commit_hash).
    """
    if current_snapshot_token != proposal.snapshot_token:
        raise StaleProposal(
            f"current snapshot token {current_snapshot_token!r} does not match "
            f"proposal token {proposal.snapshot_token!r}"
        )
    if write_set is None:
        if postimage_bytes is None and rename_from is None:
            raise MutationError("commit requires postimage_bytes, write_set, or rename_from")
        write_set = build_write_set(proposal, postimage_bytes, rename_from=rename_from)
    _verify_preimage(proposal, tree)
    prior = {path: tree.get(path) for path in write_set}
    try:
        if write_fn is not None:
            write_fn(tree, write_set)
        else:
            for path, content in write_set.items():
                if content is None:
                    tree.pop(path, None)
                else:
                    tree[path] = content
    except Exception as exc:
        rollback(tree, prior)
        raise AtomicWriteFailed(prior.keys(), exc) from exc
    committed_files = {
        path: sha256_hex(content) for path, content in write_set.items() if content is not None
    }
    commit_hash = _commit_hash(proposal, committed_files, proposal.preflight)
    return MutationCommitReceipt(
        commit_id=commit_hash,
        proposal_id=proposal.proposal_id,
        snapshot_token=proposal.snapshot_token,
        committed_files=committed_files,
        commit_hash=commit_hash,
        atomic=True,
        rollback=(),
        postflight=proposal.preflight,
    )


def rollback(tree: dict[str, bytes], prior_state: Mapping[str, bytes | None]) -> None:
    """Restore prior bytes for each affected path; ``None`` restores absence."""
    for path, content in prior_state.items():
        if content is None:
            tree.pop(path, None)
        else:
            tree[path] = content


def _verify_preimage(proposal: MutationProposal, tree: Mapping[str, bytes]) -> None:
    actual = tree.get(proposal.target_path)
    if proposal.expected_preimage_hash == ABSENT_PREIMAGE:
        if actual is not None:
            raise PreimageMismatch(
                f"expected {proposal.target_path} to be absent but it exists on the tree"
            )
        return
    if actual is None or sha256_hex(actual) != proposal.expected_preimage_hash:
        raise PreimageMismatch(
            f"on-tree preimage of {proposal.target_path} does not hash to "
            f"{proposal.expected_preimage_hash}"
        )


def _proposal_id(
    snapshot_token: str,
    target_path: str,
    expected_preimage_hash: str,
    proposed_postimage_hash: str,
    proposed_patch: str,
    declared_postconditions: tuple[str, ...],
) -> str:
    schema = f"gt.engine.mutation_proposal.v{CONTRACTS_SCHEMA_VERSION}"
    payload = {
        "snapshot_token": snapshot_token,
        "target_path": target_path,
        "expected_preimage_hash": expected_preimage_hash,
        "proposed_postimage_hash": proposed_postimage_hash,
        "proposed_patch": proposed_patch,
        "declared_postconditions": list(declared_postconditions),
    }
    return object_hash(payload, schema)


def _commit_hash(
    proposal: MutationProposal,
    committed_files: Mapping[str, str],
    postflight: tuple[EvidenceArtifact, ...],
) -> str:
    schema = f"gt.engine.mutation_commit_receipt.v{CONTRACTS_SCHEMA_VERSION}"
    payload = {
        "proposal_id": proposal.proposal_id,
        "snapshot_token": proposal.snapshot_token,
        "committed_files": dict(committed_files),
        "atomic": True,
        "rollback": [],
        "postflight": [a.to_dict() for a in postflight],
    }
    return object_hash(payload, schema)
