"""Behavior-neutral verifier and rollback-candidate shadow state."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VerificationVector:
    passing_checks: tuple[str, ...] = ()
    failing_checks: tuple[str, ...] = ()

    def dominates(self, other: VerificationVector) -> bool:
        """True when no evidence dimension regresses and one improves."""
        mine_pass = set(self.passing_checks)
        other_pass = set(other.passing_checks)
        mine_fail = set(self.failing_checks)
        other_fail = set(other.failing_checks)
        no_regression = mine_pass >= other_pass and mine_fail <= other_fail
        strict = mine_pass > other_pass or mine_fail < other_fail
        return no_regression and strict


@dataclass(frozen=True, slots=True)
class CheckpointCandidate:
    source_revision: str
    workspace_revision: str
    changed_paths: tuple[str, ...]
    vector: VerificationVector
    observed_after_action: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ShadowCheckpointLedger:
    """Track a best verified state without changing or restoring the workspace."""

    def __init__(self) -> None:
        self._candidates: list[CheckpointCandidate] = []
        self._best: CheckpointCandidate | None = None
        self._rollback_opportunities: list[dict[str, Any]] = []

    def observe(
        self,
        *,
        source_revision: str,
        workspace_revision: str,
        changed_paths: Iterable[str],
        passing_checks: Iterable[str],
        failing_checks: Iterable[str],
        action_id: int,
    ) -> CheckpointCandidate:
        vector = VerificationVector(
            passing_checks=tuple(sorted(set(passing_checks))),
            failing_checks=tuple(sorted(set(failing_checks))),
        )
        candidate = CheckpointCandidate(
            source_revision=source_revision,
            workspace_revision=workspace_revision,
            changed_paths=tuple(sorted(set(changed_paths))),
            vector=vector,
            observed_after_action=max(0, int(action_id)),
        )
        self._candidates.append(candidate)
        if not vector.failing_checks and (
            self._best is None
            or vector.dominates(self._best.vector)
            or (
                vector == self._best.vector
                and candidate.observed_after_action > self._best.observed_after_action
            )
        ):
            self._best = candidate
        if vector.failing_checks and self._best is not None:
            self._rollback_opportunities.append(
                {
                    "failed_revision": source_revision,
                    "failed_after_action": candidate.observed_after_action,
                    "candidate_revision": self._best.source_revision,
                    "candidate_after_action": self._best.observed_after_action,
                    "failing_checks": list(vector.failing_checks),
                    "shadow_only": True,
                }
            )
        return candidate

    def summary(self) -> dict[str, Any]:
        return {
            "mode": "shadow",
            "candidates": [item.as_dict() for item in self._candidates],
            "best": self._best.as_dict() if self._best is not None else None,
            "rollback_opportunities": list(self._rollback_opportunities),
        }
