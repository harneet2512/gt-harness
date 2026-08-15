"""Inline Engine package.

The ENGINE phase's sole action-to-observation interface. Contains the public
contracts (``contracts``), the authoritative lifecycle transition table
(``transitions``), the five-decision executor (``decide``), the canonical
observation compiler (``observe``), and the mutation proposal/commit protocol
(``mutation``).
"""
from __future__ import annotations

from .contracts import (
    ActionBatch,
    ActionKind,
    ActionRequest,
    ActionResult,
    CanonicalObservation,
    Decision,
    DeliveryReceipt,
    EngineMode,
    EngineModeBinding,
    EvidenceArtifact,
    ExecutionState,
    Fidelity,
    InterceptionDecision,
    LifecycleState,
    MutationCommitReceipt,
    MutationCommitRequest,
    MutationProposal,
    RepositorySnapshot,
    SnapshotToken,
    TimingClass,
)

__all__ = [
    "ActionBatch",
    "ActionKind",
    "ActionRequest",
    "ActionResult",
    "CanonicalObservation",
    "Decision",
    "DeliveryReceipt",
    "EngineMode",
    "EngineModeBinding",
    "EvidenceArtifact",
    "ExecutionState",
    "Fidelity",
    "InterceptionDecision",
    "LifecycleState",
    "MutationCommitReceipt",
    "MutationCommitRequest",
    "MutationProposal",
    "RepositorySnapshot",
    "SnapshotToken",
    "TimingClass",
]
