"""Typed honesty envelope for model-visible GroundTruth results.

The envelope is deliberately small and conservative.  A result is never
called complete unless a producer supplied a known true total and returned all
of it; unknown/legacy producers are surfaced as ``legacy_unknown``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

HONESTY_SCHEMA = "gt.honesty_envelope.v1"
COMPLETENESS = frozenset({"complete", "truncated", "incomplete", "legacy_unknown"})


@dataclass(frozen=True, slots=True)
class HonestyEnvelope:
    source_revision: str
    workspace_revision: str
    completeness: str
    returned_count: int
    true_total: int | None
    ambiguities: tuple[str, ...] = ()
    unresolved_identities: tuple[str, ...] = ()
    payload: Any = None
    abstention_reason: str | None = None

    def __post_init__(self) -> None:
        if self.completeness not in COMPLETENESS:
            raise ValueError(f"unknown completeness: {self.completeness}")
        if self.returned_count < 0:
            raise ValueError("returned_count must be non-negative")
        if self.true_total is not None and self.true_total < 0:
            raise ValueError("true_total must be non-negative")
        if self.completeness == "truncated":
            if self.true_total is None or self.returned_count >= self.true_total:
                raise ValueError("truncated requires returned_count < known true_total")
        if self.completeness == "complete":
            if self.true_total is None or self.returned_count != self.true_total:
                raise ValueError("complete requires returned_count == known true_total")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": HONESTY_SCHEMA,
            "source_revision": self.source_revision,
            "workspace_revision": self.workspace_revision,
            "completeness": self.completeness,
            "returned_count": self.returned_count,
            "true_total": self.true_total,
            "ambiguities": list(self.ambiguities),
            "unresolved_identities": list(self.unresolved_identities),
            "payload": self.payload,
            "abstention_reason": self.abstention_reason,
        }


def envelope_for_result(
    *,
    source_revision: str,
    workspace_revision: str,
    payload: Any,
    returned_count: int,
    true_total: int | None,
    ambiguities: tuple[str, ...] = (),
    unresolved_identities: tuple[str, ...] = (),
    abstention_reason: str | None = None,
    legacy: bool = False,
    incomplete: bool = False,
) -> dict[str, Any]:
    """Build a validated envelope, conservatively mapping old producers."""
    if legacy:
        completeness = "legacy_unknown"
    elif incomplete:
        completeness = "incomplete"
    elif true_total is not None and returned_count < true_total:
        completeness = "truncated"
    elif true_total is not None and returned_count == true_total:
        completeness = "complete"
    else:
        completeness = "incomplete"
    return HonestyEnvelope(
        source_revision=str(source_revision or ""),
        workspace_revision=str(workspace_revision or ""),
        completeness=completeness,
        returned_count=int(returned_count),
        true_total=None if true_total is None else int(true_total),
        ambiguities=tuple(sorted({str(item) for item in ambiguities if item})),
        unresolved_identities=tuple(sorted({str(item) for item in unresolved_identities if item})),
        payload=payload,
        abstention_reason=abstention_reason,
    ).as_dict()


def envelope_from_mapping(value: Mapping[str, Any]) -> HonestyEnvelope:
    """Validate and parse an envelope received from an older boundary."""
    if value.get("schema") != HONESTY_SCHEMA:
        return HonestyEnvelope(
            source_revision=str(value.get("source_revision") or ""),
            workspace_revision=str(value.get("workspace_revision") or ""),
            completeness="legacy_unknown",
            returned_count=int(value.get("returned_count") or 0),
            true_total=None,
            payload=value.get("payload"),
        )
    return HonestyEnvelope(
        source_revision=str(value.get("source_revision") or ""),
        workspace_revision=str(value.get("workspace_revision") or ""),
        completeness=str(value.get("completeness") or "legacy_unknown"),
        returned_count=int(value.get("returned_count") or 0),
        true_total=value.get("true_total"),
        ambiguities=tuple(value.get("ambiguities") or ()),
        unresolved_identities=tuple(value.get("unresolved_identities") or ()),
        payload=value.get("payload"),
        abstention_reason=value.get("abstention_reason"),
    )
