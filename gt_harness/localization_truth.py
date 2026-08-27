"""Role-aware, provider-free localization truth scoring.

Reference patches are post-hoc evidence, not a declaration that every changed
file was an edit target.  This module scores typed GT facts against an
independently reviewed oracle and keeps implementation, public, integration,
validation, and new-file responsibilities separate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

_FULL_SHA = re.compile(r"[0-9a-f]{40}")


class LocalizationRole(StrEnum):
    IMPLEMENTATION_OWNER = "IMPLEMENTATION_OWNER"
    PUBLIC_SURFACE = "PUBLIC_SURFACE"
    INTEGRATION_OR_REGISTRATION = "INTEGRATION_OR_REGISTRATION"
    VALIDATION_OR_TEST = "VALIDATION_OR_TEST"
    NEW_FILE_PRECEDENT = "NEW_FILE_PRECEDENT"
    ACCEPTABLE_ALTERNATIVE = "ACCEPTABLE_ALTERNATIVE"
    IRRELEVANT_PATH = "IRRELEVANT_PATH"


_DELIVERY_ROLES: dict[LocalizationRole, tuple[str, ...]] = {
    LocalizationRole.IMPLEMENTATION_OWNER: (
        "EXACT_EDIT_TARGET",
        "INSPECT_IMPLEMENTATION_OWNER_NOT_EDIT_AUTHORITY",
        "AMBIGUOUS_IDENTITY",
        # A bounded inspection candidate makes a repository fact available to
        # the agent without claiming edit authority.  Coverage may count that
        # availability; the separate role-precision and false-authority gates
        # prevent broad candidate flooding from scoring as success.
        "INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY",
        # A relationship-derived integration row can simultaneously be the
        # implementation file the agent must inspect. The compiler keeps the
        # integration label; availability scoring does not erase that path.
        "INSPECT_INTEGRATION",
    ),
    LocalizationRole.PUBLIC_SURFACE: ("INSPECT_PUBLIC_SURFACE",),
    LocalizationRole.INTEGRATION_OR_REGISTRATION: ("INSPECT_INTEGRATION",),
    LocalizationRole.VALIDATION_OR_TEST: ("AFFECTED_TEST",),
    LocalizationRole.NEW_FILE_PRECEDENT: ("PROPOSED_NEW_FILE",),
    LocalizationRole.ACCEPTABLE_ALTERNATIVE: (
        "EXACT_EDIT_TARGET",
        "AMBIGUOUS_IDENTITY",
        "INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY",
    ),
    LocalizationRole.IRRELEVANT_PATH: (),
}


def _normalized_path(value: str) -> str:
    path = str(PurePosixPath(str(value or "").replace("\\", "/"))).lstrip("./")
    if not path or path == "." or path.startswith("../"):
        raise ValueError(f"invalid repository-relative path: {value!r}")
    return path


@dataclass(frozen=True, slots=True)
class LocalizationFact:
    fact_id: str
    role: LocalizationRole
    acceptable_paths: tuple[str, ...]
    required: bool
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.fact_id.strip():
            raise ValueError("localization fact requires a fact_id")
        normalized = tuple(dict.fromkeys(_normalized_path(path) for path in self.acceptable_paths))
        if not normalized:
            raise ValueError(f"{self.fact_id}: acceptable_paths must not be empty")
        object.__setattr__(self, "acceptable_paths", normalized)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LocalizationFact:
        return cls(
            fact_id=str(value.get("fact_id") or ""),
            role=LocalizationRole(str(value.get("role") or "")),
            acceptable_paths=tuple(str(path) for path in value.get("acceptable_paths", ())),
            required=bool(value.get("required", False)),
            evidence=tuple(str(item) for item in value.get("evidence", ())),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "role": self.role.value,
            "acceptable_paths": list(self.acceptable_paths),
            "required": self.required,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class LocalizationOracleTask:
    task_id: str
    base_sha: str
    facts: tuple[LocalizationFact, ...]
    review_status: str = "REVIEWED"

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("localization oracle task requires task_id")
        if _FULL_SHA.fullmatch(self.base_sha) is None:
            raise ValueError(f"{self.task_id}: base_sha must be an exact 40-character SHA")
        if self.review_status != "REVIEWED":
            raise ValueError(f"{self.task_id}: oracle task is not REVIEWED")
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError(f"{self.task_id}: duplicate fact_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LocalizationOracleTask:
        return cls(
            task_id=str(value.get("task_id") or ""),
            base_sha=str(value.get("base_sha") or ""),
            facts=tuple(
                LocalizationFact.from_dict(fact)
                for fact in value.get("facts", ())
                if isinstance(fact, Mapping)
            ),
            review_status=str(value.get("review_status") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "base_sha": self.base_sha,
            "review_status": self.review_status,
            "facts": [fact.as_dict() for fact in self.facts],
        }


@dataclass(frozen=True, slots=True)
class LocalizationScore:
    task_id: str
    exact_edit_precision: float | None
    false_edit_authority: tuple[str, ...]
    required_facts: int
    required_facts_covered: int
    required_facet_coverage: float | None
    ambiguity_candidate_recall: float | None
    uncovered_fact_ids: tuple[str, ...]
    role_metrics: dict[str, dict[str, float | int | None]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "exact_edit_precision": self.exact_edit_precision,
            "false_edit_authority": list(self.false_edit_authority),
            "required_facts": self.required_facts,
            "required_facts_covered": self.required_facts_covered,
            "required_facet_coverage": self.required_facet_coverage,
            "ambiguity_candidate_recall": self.ambiguity_candidate_recall,
            "uncovered_fact_ids": list(self.uncovered_fact_ids),
            "role_metrics": self.role_metrics,
        }


def score_localization(
    oracle: LocalizationOracleTask,
    delivered_roles: Mapping[str, Sequence[str]],
) -> LocalizationScore:
    """Score typed provider-visible paths without collapsing their roles."""

    delivered = {
        role: frozenset(_normalized_path(path) for path in paths)
        for role, paths in delivered_roles.items()
    }
    acceptable_edits = frozenset(
        path
        for fact in oracle.facts
        if fact.role
        in {
            LocalizationRole.IMPLEMENTATION_OWNER,
            LocalizationRole.ACCEPTABLE_ALTERNATIVE,
        }
        for path in fact.acceptable_paths
    )
    exact_edits = delivered.get("EXACT_EDIT_TARGET", frozenset())
    false_edits = tuple(sorted(exact_edits - acceptable_edits))
    precision = (len(exact_edits) - len(false_edits)) / len(exact_edits) if exact_edits else None

    covered: dict[str, bool] = {}
    role_metrics: dict[str, dict[str, float | int | None]] = {}
    for role in LocalizationRole:
        facts = tuple(fact for fact in oracle.facts if fact.role is role)
        if not facts:
            continue
        role_paths = frozenset(
            path
            for delivery_role in _DELIVERY_ROLES[role]
            for path in delivered.get(delivery_role, ())
        )
        acceptable = frozenset(path for fact in facts for path in fact.acceptable_paths)
        hits = role_paths & acceptable
        false_paths = role_paths - acceptable
        facts_covered = sum(
            bool(role_paths & frozenset(fact.acceptable_paths)) for fact in facts
        )
        role_metrics[role.value] = {
            "facts": len(facts),
            "facts_covered": facts_covered,
            "fact_recall": facts_covered / len(facts) if facts else None,
            "delivered": len(role_paths),
            "acceptable": len(acceptable),
            "hits": len(hits),
            "false_paths": len(false_paths),
            "precision": len(hits) / len(role_paths) if role_paths else None,
            "recall": len(hits) / len(acceptable) if acceptable else None,
        }
        for fact in facts:
            covered[fact.fact_id] = bool(role_paths & frozenset(fact.acceptable_paths))

    required = tuple(fact for fact in oracle.facts if fact.required)
    required_covered = sum(bool(covered.get(fact.fact_id)) for fact in required)
    ambiguity_facts = tuple(
        fact for fact in oracle.facts if fact.role is LocalizationRole.IMPLEMENTATION_OWNER
    )
    ambiguity_paths = delivered.get("AMBIGUOUS_IDENTITY", frozenset())
    ambiguity_hits = sum(
        bool(ambiguity_paths & frozenset(fact.acceptable_paths)) for fact in ambiguity_facts
    )
    return LocalizationScore(
        task_id=oracle.task_id,
        exact_edit_precision=precision,
        false_edit_authority=false_edits,
        required_facts=len(required),
        required_facts_covered=required_covered,
        required_facet_coverage=(required_covered / len(required) if required else None),
        ambiguity_candidate_recall=(
            ambiguity_hits / len(ambiguity_facts) if ambiguity_paths and ambiguity_facts else None
        ),
        uncovered_fact_ids=tuple(
            fact.fact_id for fact in required if not covered.get(fact.fact_id)
        ),
        role_metrics=role_metrics,
    )


def delivered_roles_from_packet(
    packet: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    """Extract role paths from the typed compiler packet, never rendered text."""

    fields = {
        "EXACT_EDIT_TARGET": "primary_edit_targets",
        "INSPECT_IMPLEMENTATION_OWNER_NOT_EDIT_AUTHORITY": ("inspection_implementation_owners"),
        "INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY": "inspection_candidates",
        "INSPECT_PUBLIC_SURFACE": "inspection_public_surface",
        "INSPECT_INTEGRATION": "inspection_integration",
    }
    roles: dict[str, tuple[str, ...]] = {}
    for role, field_name in fields.items():
        rows = packet.get(field_name, ())
        roles[role] = tuple(
            dict.fromkeys(
                _normalized_path(str(row["path"]))
                for row in rows
                if isinstance(row, Mapping) and row.get("path")
            )
        )
    roles["AMBIGUOUS_IDENTITY"] = tuple(
        dict.fromkeys(
            _normalized_path(str(candidate["path"]))
            for group in packet.get("ambiguous_identities", ())
            if isinstance(group, Mapping)
            for candidate in group.get("candidates", ())
            if isinstance(candidate, Mapping) and candidate.get("path")
        )
    )
    roles["AFFECTED_TEST"] = tuple(
        dict.fromkeys(
            _normalized_path(str(path))
            for path in packet.get("affected_tests", ())
            if str(path or "").strip()
        )
    )
    roles["PROPOSED_NEW_FILE"] = tuple(
        dict.fromkeys(
            _normalized_path(str(path))
            for path in packet.get("proposed_new_files", ())
            if str(path or "").strip()
        )
    )
    return roles


def delivered_roles_from_provider_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    """Extract only paths that survived provider-view compaction."""

    if receipt.get("schema") != "gt.provider_delivery.v2":
        raise ValueError("provider delivery receipt schema must be gt.provider_delivery.v2")
    value = receipt.get("provider_visible_role_paths")
    if not isinstance(value, Mapping):
        raise ValueError("provider delivery receipt has no typed visible role paths")
    return {
        str(role): tuple(
            dict.fromkeys(_normalized_path(str(path)) for path in paths if str(path or "").strip())
        )
        for role, paths in value.items()
        if isinstance(paths, Sequence) and not isinstance(paths, (str, bytes))
    }


__all__ = [
    "delivered_roles_from_packet",
    "delivered_roles_from_provider_receipt",
    "LocalizationFact",
    "LocalizationOracleTask",
    "LocalizationRole",
    "LocalizationScore",
    "score_localization",
]
