"""Typed, conservative call-resolution provenance contracts.

The graph producer currently exposes selected edges and a scalar candidate
count, not retained candidate identities.  This module defines the first-party
contract that a future producer can populate without pretending legacy edges
contain information that was discarded upstream.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1


class NormalizedSymbolKind(StrEnum):
    UNKNOWN = "unknown"
    FILE = "file"
    MODULE = "module"
    PACKAGE = "package"
    NAMESPACE = "namespace"
    TYPE = "type"
    CLASS = "class"
    INTERFACE = "interface"
    TRAIT = "trait"
    ENUM = "enum"
    STRUCT = "struct"
    UNION = "union"
    ALIAS = "alias"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    PROPERTY = "property"
    FIELD = "field"
    VARIABLE = "variable"
    CONSTANT = "constant"
    PARAMETER = "parameter"


class ProvenanceMechanism(StrEnum):
    UNKNOWN_LEGACY = "unknown_legacy"
    SAME_FILE = "same_file"
    IMPORT_EXACT = "import_exact"
    QUALIFIED_EXACT = "qualified_exact"
    RECEIVER_TYPE = "receiver_type"
    FIELD_BASED = "field_based"
    NAME_MATCH = "name_match"
    DYNAMIC = "dynamic"
    EXTERNAL = "external"
    PARSER_INCOMPLETE = "parser_incomplete"
    VTA = "vta"


class VerificationStatus(StrEnum):
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"
    CANDIDATE_ONLY = "candidate_only"


class DispatchState(StrEnum):
    ZERO = "zero"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    DYNAMIC = "dynamic"
    EXTERNAL_UNRESOLVED = "external_unresolved"
    PARSER_INCOMPLETE = "parser_incomplete"
    UNKNOWN_LEGACY = "unknown_legacy"


class ResolutionTier(StrEnum):
    """A score-free authority label derived from observable resolver evidence."""

    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    DYNAMIC = "dynamic"
    EXTERNAL = "external"
    UNRESOLVED = "unresolved"
    INDETERMINATE = "indeterminate"
    HEURISTIC = "heuristic"
    UNKNOWN_LEGACY = "unknown_legacy"


_KIND_ALIASES = {
    "file": NormalizedSymbolKind.FILE,
    "module": NormalizedSymbolKind.MODULE,
    "package": NormalizedSymbolKind.PACKAGE,
    "namespace": NormalizedSymbolKind.NAMESPACE,
    "type": NormalizedSymbolKind.TYPE,
    "class": NormalizedSymbolKind.CLASS,
    "interface": NormalizedSymbolKind.INTERFACE,
    "trait": NormalizedSymbolKind.TRAIT,
    "enum": NormalizedSymbolKind.ENUM,
    "struct": NormalizedSymbolKind.STRUCT,
    "union": NormalizedSymbolKind.UNION,
    "alias": NormalizedSymbolKind.ALIAS,
    "typealias": NormalizedSymbolKind.ALIAS,
    "function": NormalizedSymbolKind.FUNCTION,
    "func": NormalizedSymbolKind.FUNCTION,
    "method": NormalizedSymbolKind.METHOD,
    "constructor": NormalizedSymbolKind.CONSTRUCTOR,
    "property": NormalizedSymbolKind.PROPERTY,
    "field": NormalizedSymbolKind.FIELD,
    "variable": NormalizedSymbolKind.VARIABLE,
    "var": NormalizedSymbolKind.VARIABLE,
    "constant": NormalizedSymbolKind.CONSTANT,
    "const": NormalizedSymbolKind.CONSTANT,
    "parameter": NormalizedSymbolKind.PARAMETER,
    "param": NormalizedSymbolKind.PARAMETER,
}


def normalize_symbol_kind(native_kind: str) -> NormalizedSymbolKind:
    """Map a native parser label without erasing the original label."""
    key = "".join(char for char in str(native_kind).strip().lower() if char.isalnum())
    return _KIND_ALIASES.get(key, NormalizedSymbolKind.UNKNOWN)


_EXACT_MECHANISMS = frozenset(
    {
        ProvenanceMechanism.SAME_FILE,
        ProvenanceMechanism.IMPORT_EXACT,
        ProvenanceMechanism.QUALIFIED_EXACT,
        ProvenanceMechanism.RECEIVER_TYPE,
        ProvenanceMechanism.FIELD_BASED,
        ProvenanceMechanism.VTA,
    }
)


def derive_resolution_tier(
    *,
    provenance: ProvenanceMechanism | str,
    candidate_count: int,
    declared_scope: str,
    receiver_type: str,
    parser_complete: bool | None,
    dynamic_dispatch: bool,
) -> ResolutionTier:
    """Derive a conservative tier without consulting confidence scores.

    Missing/failed parser or oracle state is indeterminate.  Candidate
    cardinality is evaluated before structural evidence so an ambiguous call
    can never be presented as an exact singleton.
    """

    mechanism = ProvenanceMechanism(str(provenance))
    count = int(candidate_count)
    if count < 0:
        raise ValueError("candidate_count must be non-negative")
    if parser_complete is not True:
        return ResolutionTier.INDETERMINATE
    if mechanism is ProvenanceMechanism.UNKNOWN_LEGACY:
        return ResolutionTier.UNKNOWN_LEGACY
    if dynamic_dispatch or mechanism is ProvenanceMechanism.DYNAMIC:
        return ResolutionTier.DYNAMIC
    if mechanism is ProvenanceMechanism.EXTERNAL:
        return ResolutionTier.EXTERNAL
    if count == 0:
        return ResolutionTier.UNRESOLVED
    if count > 1:
        return ResolutionTier.AMBIGUOUS
    if mechanism in _EXACT_MECHANISMS and (declared_scope or receiver_type):
        return ResolutionTier.EXACT
    return ResolutionTier.HEURISTIC


def _stable_digest(schema: str, values: Iterable[Any]) -> str:
    encoded = json.dumps(
        [schema, *values], ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8", "surrogatepass")
    return hashlib.sha256(encoded).hexdigest()


def stable_symbol_id(
    *,
    language: str,
    path: str,
    qualified_name: str,
    native_kind: str,
    start_line: int,
    end_line: int,
) -> str:
    return _stable_digest(
        "gt.symbol.identity.v1",
        (
            language.strip().lower(),
            path.replace("\\", "/"),
            qualified_name,
            native_kind,
            int(start_line),
            int(end_line),
        ),
    )


def stable_callsite_id(
    *,
    repository_revision: str,
    source_stable_id: str,
    path: str,
    start_line: int,
    end_line: int,
    callee: str,
) -> str:
    return _stable_digest(
        "gt.callsite.identity.v1",
        (
            repository_revision,
            source_stable_id,
            path.replace("\\", "/"),
            int(start_line),
            int(end_line),
            callee,
        ),
    )


@dataclass(frozen=True)
class SymbolRecord:
    stable_id: str
    native_id: str
    native_kind: str
    normalized_kind: NormalizedSymbolKind
    language: str
    path: str
    qualified_name: str
    start_line: int
    end_line: int
    export_status: str

    @classmethod
    def build(
        cls,
        *,
        native_id: str,
        native_kind: str,
        language: str,
        path: str,
        qualified_name: str,
        start_line: int,
        end_line: int,
        export_status: str,
    ) -> SymbolRecord:
        if int(start_line) < 0 or int(end_line) < int(start_line):
            raise ValueError("invalid symbol span")
        normalized_path = path.replace("\\", "/")
        return cls(
            stable_id=stable_symbol_id(
                language=language,
                path=normalized_path,
                qualified_name=qualified_name,
                native_kind=native_kind,
                start_line=start_line,
                end_line=end_line,
            ),
            native_id=str(native_id),
            native_kind=str(native_kind),
            normalized_kind=normalize_symbol_kind(native_kind),
            language=str(language).strip().lower(),
            path=normalized_path,
            qualified_name=str(qualified_name),
            start_line=int(start_line),
            end_line=int(end_line),
            export_status=str(export_status or "unknown"),
        )

    def to_row(self) -> dict[str, Any]:
        row = dict(self.__dict__)
        row["normalized_kind"] = self.normalized_kind.value
        return row

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> SymbolRecord:
        return cls(
            stable_id=str(row["stable_id"]),
            native_id=str(row.get("native_id") or ""),
            native_kind=str(row.get("native_kind") or ""),
            normalized_kind=NormalizedSymbolKind(str(row["normalized_kind"])),
            language=str(row.get("language") or ""),
            path=str(row.get("path") or ""),
            qualified_name=str(row.get("qualified_name") or ""),
            start_line=int(row.get("start_line") or 0),
            end_line=int(row.get("end_line") or 0),
            export_status=str(row.get("export_status") or "unknown"),
        )


@dataclass(frozen=True)
class CallCandidate:
    target_stable_id: str
    target_native_id: str
    ordinal: int
    mechanism: ProvenanceMechanism
    declared_scope: str
    receiver_type: str
    receiver_origin: str
    receiver_shape: str
    receiver_chain: tuple[str, ...]
    import_chain: tuple[str, ...]
    dynamic_dispatch: bool
    export_status: str
    parser_complete: bool | None
    verification_status: VerificationStatus
    selected: bool = False

    def to_row(self) -> dict[str, Any]:
        row = dict(self.__dict__)
        row.update(
            mechanism=self.mechanism.value,
            receiver_chain=list(self.receiver_chain),
            import_chain=list(self.import_chain),
            verification_status=self.verification_status.value,
        )
        return row

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> CallCandidate:
        def sequence(name: str) -> tuple[str, ...]:
            value = row.get(name) or ()
            if isinstance(value, str):
                value = json.loads(value)
            return tuple(str(item) for item in value)

        parser_complete = row.get("parser_complete")
        if parser_complete is not None:
            parser_complete = bool(parser_complete)
        return cls(
            target_stable_id=str(row["target_stable_id"]),
            target_native_id=str(row.get("target_native_id") or ""),
            ordinal=int(row["ordinal"]),
            mechanism=ProvenanceMechanism(str(row["mechanism"])),
            declared_scope=str(row.get("declared_scope") or ""),
            receiver_type=str(row.get("receiver_type") or ""),
            receiver_origin=str(row.get("receiver_origin") or ""),
            receiver_shape=str(row.get("receiver_shape") or ""),
            receiver_chain=sequence("receiver_chain"),
            import_chain=sequence("import_chain"),
            dynamic_dispatch=bool(row.get("dynamic_dispatch")),
            export_status=str(row.get("export_status") or "unknown"),
            parser_complete=parser_complete,
            verification_status=VerificationStatus(str(row["verification_status"])),
            selected=bool(row.get("selected")),
        )


@dataclass(frozen=True)
class CallsiteRecord:
    callsite_id: str
    repository_revision: str
    source_stable_id: str
    source_native_id: str
    path: str
    start_line: int
    end_line: int
    callee: str
    language: str
    dispatch_state: DispatchState
    candidate_count: int
    selected_target_stable_id: str | None
    selected_target_native_id: str | None
    mechanism: ProvenanceMechanism
    verification_status: VerificationStatus
    candidates: tuple[CallCandidate, ...] = ()
    legacy_reported_candidate_count: int | None = None
    legacy_selected_native_target_id: str = ""

    @classmethod
    def build(
        cls,
        *,
        repository_revision: str,
        source: SymbolRecord,
        path: str,
        start_line: int,
        end_line: int,
        callee: str,
        language: str,
        dispatch_state: DispatchState,
        candidates: Iterable[CallCandidate],
        mechanism: ProvenanceMechanism,
        selected_target_stable_id: str | None = None,
        selected_target_native_id: str | None = None,
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
    ) -> CallsiteRecord:
        normalized_path = path.replace("\\", "/")
        values = tuple(candidates)
        record = cls(
            callsite_id=stable_callsite_id(
                repository_revision=repository_revision,
                source_stable_id=source.stable_id,
                path=normalized_path,
                start_line=start_line,
                end_line=end_line,
                callee=callee,
            ),
            repository_revision=repository_revision,
            source_stable_id=source.stable_id,
            source_native_id=source.native_id,
            path=normalized_path,
            start_line=int(start_line),
            end_line=int(end_line),
            callee=callee,
            language=language.strip().lower(),
            dispatch_state=DispatchState(dispatch_state),
            candidate_count=len(values),
            selected_target_stable_id=selected_target_stable_id,
            selected_target_native_id=selected_target_native_id,
            mechanism=ProvenanceMechanism(mechanism),
            verification_status=VerificationStatus(verification_status),
            candidates=values,
        )
        record.validate()
        return record

    def validate(self) -> None:
        if self.start_line < 0 or self.end_line < self.start_line:
            raise ValueError("invalid callsite span")
        if self.candidate_count != len(self.candidates):
            raise ValueError("candidate_count must equal retained candidate count")
        ordinals = [item.ordinal for item in self.candidates]
        if ordinals != list(range(len(self.candidates))):
            raise ValueError("candidate ordinals must be dense zero-based")
        target_ids = [item.target_stable_id for item in self.candidates]
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("candidate targets must be unique within a callsite")
        selected = [item for item in self.candidates if item.selected]
        if self.selected_target_stable_id is None:
            if selected or self.selected_target_native_id:
                raise ValueError("selection metadata requires selected target")
        else:
            if self.selected_target_stable_id not in target_ids:
                raise ValueError("selected target must be a retained candidate")
            if len(selected) != 1 or selected[0].target_stable_id != self.selected_target_stable_id:
                raise ValueError("selected flag must identify the selected target")
        if self.dispatch_state is DispatchState.UNIQUE and self.candidate_count != 1:
            raise ValueError("unique callsite requires exactly one candidate")
        if self.dispatch_state is DispatchState.AMBIGUOUS and self.candidate_count < 2:
            raise ValueError("ambiguous callsite requires at least two candidates")
        unresolved = {
            DispatchState.ZERO,
            DispatchState.DYNAMIC,
            DispatchState.EXTERNAL_UNRESOLVED,
            DispatchState.PARSER_INCOMPLETE,
            DispatchState.UNKNOWN_LEGACY,
        }
        if self.dispatch_state in unresolved and (
            self.candidate_count or self.selected_target_stable_id is not None
        ):
            raise ValueError("unresolved callsite cannot retain selection authority")
        if (
            self.dispatch_state is DispatchState.AMBIGUOUS
            and self.verification_status is VerificationStatus.VERIFIED
        ):
            raise ValueError("ambiguous callsite cannot certify a single target")

    def to_row(self) -> dict[str, Any]:
        return {
            "callsite_id": self.callsite_id,
            "repository_revision": self.repository_revision,
            "source_stable_id": self.source_stable_id,
            "source_native_id": self.source_native_id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "callee": self.callee,
            "language": self.language,
            "dispatch_state": self.dispatch_state.value,
            "candidate_count": self.candidate_count,
            "selected_target_stable_id": self.selected_target_stable_id,
            "selected_target_native_id": self.selected_target_native_id,
            "mechanism": self.mechanism.value,
            "verification_status": self.verification_status.value,
            "legacy_reported_candidate_count": self.legacy_reported_candidate_count,
            "legacy_selected_native_target_id": self.legacy_selected_native_target_id,
        }

    @classmethod
    def from_row(
        cls,
        row: Mapping[str, Any],
        *,
        candidates: Iterable[CallCandidate] = (),
        validate: bool = True,
    ) -> CallsiteRecord:
        record = cls(
            callsite_id=str(row["callsite_id"]),
            repository_revision=str(row["repository_revision"]),
            source_stable_id=str(row["source_stable_id"]),
            source_native_id=str(row.get("source_native_id") or ""),
            path=str(row["path"]),
            start_line=int(row.get("start_line") or 0),
            end_line=int(row.get("end_line") or 0),
            callee=str(row.get("callee") or ""),
            language=str(row.get("language") or ""),
            dispatch_state=DispatchState(str(row["dispatch_state"])),
            candidate_count=int(row.get("candidate_count") or 0),
            selected_target_stable_id=row.get("selected_target_stable_id") or None,
            selected_target_native_id=row.get("selected_target_native_id") or None,
            mechanism=ProvenanceMechanism(str(row["mechanism"])),
            verification_status=VerificationStatus(str(row["verification_status"])),
            candidates=tuple(candidates),
            legacy_reported_candidate_count=(
                int(row["legacy_reported_candidate_count"])
                if row.get("legacy_reported_candidate_count") is not None
                else None
            ),
            legacy_selected_native_target_id=str(row.get("legacy_selected_native_target_id") or ""),
        )
        if validate:
            record.validate()
        return record


def legacy_callsite_from_edge(
    *,
    repository_revision: str,
    source: SymbolRecord,
    path: str,
    source_line: int,
    callee: str,
    selected_native_target_id: str,
    reported_candidate_count: int,
) -> CallsiteRecord:
    """Represent a selected legacy edge without laundering it into a candidate."""
    record = CallsiteRecord(
        callsite_id=stable_callsite_id(
            repository_revision=repository_revision,
            source_stable_id=source.stable_id,
            path=path,
            start_line=source_line,
            end_line=source_line,
            callee=callee,
        ),
        repository_revision=repository_revision,
        source_stable_id=source.stable_id,
        source_native_id=source.native_id,
        path=path.replace("\\", "/"),
        start_line=int(source_line),
        end_line=int(source_line),
        callee=callee,
        language=source.language,
        dispatch_state=DispatchState.UNKNOWN_LEGACY,
        candidate_count=0,
        selected_target_stable_id=None,
        selected_target_native_id=None,
        mechanism=ProvenanceMechanism.UNKNOWN_LEGACY,
        verification_status=VerificationStatus.UNKNOWN,
        candidates=(),
        legacy_reported_candidate_count=max(0, int(reported_candidate_count)),
        legacy_selected_native_target_id=str(selected_native_target_id or ""),
    )
    record.validate()
    return record


class OracleOutcome(StrEnum):
    AGREED = "agreed"
    DISAGREED = "disagreed"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class ResolutionEvent:
    """Persisted resolution/oracle comparison at one callsite."""

    repository_revision: str
    mechanism: ProvenanceMechanism
    oracle_outcome: OracleOutcome
    callsite_id: str = ""

    def to_row(self) -> dict[str, str]:
        return {
            "repository_revision": self.repository_revision,
            "mechanism": self.mechanism.value,
            "oracle_outcome": self.oracle_outcome.value,
            "callsite_id": self.callsite_id,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ResolutionEvent:
        return cls(
            repository_revision=str(row["repository_revision"]),
            mechanism=ProvenanceMechanism(str(row["mechanism"])),
            oracle_outcome=OracleOutcome(str(row["oracle_outcome"])),
            callsite_id=str(row.get("callsite_id") or ""),
        )


@dataclass(frozen=True, slots=True)
class ResolverMetricReport:
    mechanism: ProvenanceMechanism
    population: int
    labeled: int
    agreed: int
    disagreed: int
    indeterminate: int
    precision: float | None
    coverage: float
    precision_ci_low: float | None
    precision_ci_high: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "mechanism": self.mechanism.value,
            "population": self.population,
            "labeled": self.labeled,
            "agreed": self.agreed,
            "disagreed": self.disagreed,
            "indeterminate": self.indeterminate,
            "precision": self.precision,
            "coverage": self.coverage,
            "precision_ci_low": self.precision_ci_low,
            "precision_ci_high": self.precision_ci_high,
        }


def wilson_interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("invalid wilson interval inputs")
    if total == 0:
        return (0.0, 0.0)
    proportion = successes / total
    z_squared = z * z
    denominator = 1.0 + z_squared / total
    center = (proportion + z_squared / (2.0 * total)) / denominator
    margin = (
        z * ((proportion * (1.0 - proportion) + z_squared / (4.0 * total)) / total) ** 0.5
    ) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def report_per_resolver_metrics(
    events: Iterable[ResolutionEvent],
    *,
    repository_revision: str,
) -> tuple[ResolverMetricReport, ...]:
    """Compute revision-bound precision, coverage, CI, and indeterminate counts."""
    buckets: dict[ProvenanceMechanism, list[ResolutionEvent]] = {}
    for event in events:
        if event.repository_revision != repository_revision:
            raise ValueError("resolution event repository_revision mismatch")
        buckets.setdefault(event.mechanism, []).append(event)

    reports: list[ResolverMetricReport] = []
    for mechanism in sorted(buckets, key=lambda item: item.value):
        rows = buckets[mechanism]
        agreed = sum(1 for row in rows if row.oracle_outcome is OracleOutcome.AGREED)
        disagreed = sum(1 for row in rows if row.oracle_outcome is OracleOutcome.DISAGREED)
        indeterminate = sum(
            1 for row in rows if row.oracle_outcome is OracleOutcome.INDETERMINATE
        )
        labeled = agreed + disagreed
        population = len(rows)
        if labeled:
            precision = agreed / labeled
            ci_low, ci_high = wilson_interval(agreed, labeled)
        else:
            precision = None
            ci_low = None
            ci_high = None
        coverage = labeled / population if population else 0.0
        reports.append(
            ResolverMetricReport(
                mechanism=mechanism,
                population=population,
                labeled=labeled,
                agreed=agreed,
                disagreed=disagreed,
                indeterminate=indeterminate,
                precision=precision,
                coverage=coverage,
                precision_ci_low=ci_low,
                precision_ci_high=ci_high,
            )
        )
    return tuple(reports)


@dataclass(frozen=True, slots=True)
class LabeledResolutionCase:
    case_id: str
    mechanism: ProvenanceMechanism
    candidate_count: int
    declared_scope: str
    receiver_type: str
    parser_complete: bool | None
    dynamic_dispatch: bool
    oracle_outcome: OracleOutcome
    expected_tier: ResolutionTier


def execute_labeled_resolution_cases(
    cases: Sequence[LabeledResolutionCase],
    *,
    repository_revision: str,
) -> dict[str, object]:
    """Execute labeled cases through tier derivation and persist resolver events."""
    events: list[ResolutionEvent] = []
    tier_trace: list[str] = []
    for case in cases:
        tier = derive_resolution_tier(
            provenance=case.mechanism,
            candidate_count=case.candidate_count,
            declared_scope=case.declared_scope,
            receiver_type=case.receiver_type,
            parser_complete=case.parser_complete,
            dynamic_dispatch=case.dynamic_dispatch,
        )
        if tier is not case.expected_tier:
            raise ValueError(f"{case.case_id}: tier {tier.value} != {case.expected_tier.value}")
        tier_trace.append(f"{case.case_id}:{tier.value}")
        events.append(
            ResolutionEvent(
                repository_revision=repository_revision,
                mechanism=case.mechanism,
                oracle_outcome=case.oracle_outcome,
                callsite_id=case.case_id,
            )
        )
    reports = report_per_resolver_metrics(events, repository_revision=repository_revision)
    payload = {
        "schema": "gt.resolution.labeled_execution.v1",
        "repository_revision": repository_revision,
        "tier_trace": tier_trace,
        "events": [event.to_row() for event in events],
        "reports": [report.as_dict() for report in reports],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["payload_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


__all__ = [
    "SCHEMA_VERSION",
    "CallCandidate",
    "CallsiteRecord",
    "DispatchState",
    "ResolutionTier",
    "NormalizedSymbolKind",
    "ProvenanceMechanism",
    "SymbolRecord",
    "VerificationStatus",
    "LabeledResolutionCase",
    "execute_labeled_resolution_cases",
    "legacy_callsite_from_edge",
    "derive_resolution_tier",
    "normalize_symbol_kind",
    "OracleOutcome",
    "ResolutionEvent",
    "ResolverMetricReport",
    "report_per_resolver_metrics",
    "wilson_interval",
    "stable_callsite_id",
    "stable_symbol_id",
]
