"""Closed, deterministic diagnostics for benchmark runtime and artifact audit."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA = "gt.diagnostic.v1"
DOCUMENT_SCHEMA = "gt.diagnostics.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|secret|token)$", re.I
)
_SECRET_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.I)

class DiagnosticCode(StrEnum):
    GT_DENSE_MODEL_UNAVAILABLE = "GT_DENSE_MODEL_UNAVAILABLE"
    GT_DENSE_MODEL_DIGEST_MISMATCH = "GT_DENSE_MODEL_DIGEST_MISMATCH"
    GT_GRAPH_STALE = "GT_GRAPH_STALE"
    GT_GRAPH_REFRESH_FAILED = "GT_GRAPH_REFRESH_FAILED"
    GT_LOCALIZATION_OVERSIZED = "GT_LOCALIZATION_OVERSIZED"
    GT_QUERY_MATCH_LIMIT = "GT_QUERY_MATCH_LIMIT"
    GT_QUERY_LINE_LIMIT = "GT_QUERY_LINE_LIMIT"
    GT_QUERY_RESULT_TOO_LARGE = "GT_QUERY_RESULT_TOO_LARGE"
    GT_QUERY_TURN_BUDGET_EXCEEDED = "GT_QUERY_TURN_BUDGET_EXCEEDED"
    GT_QUERY_FANOUT_REFUSED = "GT_QUERY_FANOUT_REFUSED"
    GT_QUERY_SCAN_LIMIT = "GT_QUERY_SCAN_LIMIT"
    GT_PROVIDER_REQUEST_TOO_LARGE = "GT_PROVIDER_REQUEST_TOO_LARGE"
    GT_PROVIDER_CONTEXT_WINDOW_UNAVAILABLE = "GT_PROVIDER_CONTEXT_WINDOW_UNAVAILABLE"
    GT_VERIFICATION_SEMANTIC_MISMATCH = "GT_VERIFICATION_SEMANTIC_MISMATCH"
    GT_VERIFICATION_PLAN_MISSING = "GT_VERIFICATION_PLAN_MISSING"
    GT_RESOURCE_EXHAUSTED = "GT_RESOURCE_EXHAUSTED"
    GT_PROVIDER_BILLING = "GT_PROVIDER_BILLING"
    GT_PROVIDER_RATE_LIMIT = "GT_PROVIDER_RATE_LIMIT"
    GT_PROVIDER_BAD_REQUEST = "GT_PROVIDER_BAD_REQUEST"
    GT_PROVIDER_TIMEOUT = "GT_PROVIDER_TIMEOUT"
    GT_PROVIDER_DISCONNECT = "GT_PROVIDER_DISCONNECT"
    GT_PROVIDER_MALFORMED_RESPONSE = "GT_PROVIDER_MALFORMED_RESPONSE"
    GT_RECEIPT_MISSING = "GT_RECEIPT_MISSING"
    GT_RECEIPT_INVALID = "GT_RECEIPT_INVALID"
    GT_ATTRIBUTION_MISSING = "GT_ATTRIBUTION_MISSING"
    GT_PROVENANCE_MISMATCH = "GT_PROVENANCE_MISMATCH"
    GT_CAPABILITY_DEGRADED = "GT_CAPABILITY_DEGRADED"
    GT_CREDENTIAL_ISOLATION_FAILED = "GT_CREDENTIAL_ISOLATION_FAILED"
    GT_VERIFIER_FAILED = "GT_VERIFIER_FAILED"
    GT_MODEL_PARITY_MISMATCH = "GT_MODEL_PARITY_MISMATCH"
    GT_PLAN_CONSERVATION_FAILED = "GT_PLAN_CONSERVATION_FAILED"


_PRIMARY_PRECEDENCE = {
    DiagnosticCode.GT_QUERY_RESULT_TOO_LARGE: 10,
    DiagnosticCode.GT_QUERY_SCAN_LIMIT: 11,
    DiagnosticCode.GT_PROVIDER_REQUEST_TOO_LARGE: 20,
    DiagnosticCode.GT_PROVIDER_CONTEXT_WINDOW_UNAVAILABLE: 20,
    DiagnosticCode.GT_DENSE_MODEL_UNAVAILABLE: 30,
    DiagnosticCode.GT_GRAPH_REFRESH_FAILED: 40,
    DiagnosticCode.GT_VERIFICATION_SEMANTIC_MISMATCH: 50,
    DiagnosticCode.GT_PROVIDER_BAD_REQUEST: 80,
    DiagnosticCode.GT_VERIFIER_FAILED: 90,
    DiagnosticCode.GT_RECEIPT_MISSING: 100,
}


class CapabilityState(StrEnum):
    WORKING = "WORKING"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNEXERCISED = "UNEXERCISED"


def classify_provider_failure(exc: BaseException) -> tuple[DiagnosticCode, bool]:
    """Classify provider failures without collapsing deterministic failures."""

    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    provider_code = str(getattr(exc, "code", "") or "").lower()
    message = str(exc).lower()
    signal = " ".join((name, provider_code, message))
    if status == 402 or any(word in signal for word in ("billing", "credits", "balance")):
        return DiagnosticCode.GT_PROVIDER_BILLING, False
    if status == 429 or "ratelimit" in name or "rate limit" in signal:
        return DiagnosticCode.GT_PROVIDER_RATE_LIMIT, True
    if isinstance(exc, MemoryError) or "resource exhausted" in signal:
        return DiagnosticCode.GT_RESOURCE_EXHAUSTED, False
    if "timeout" in name or "timed out" in signal:
        return DiagnosticCode.GT_PROVIDER_TIMEOUT, True
    if "connection" in name or "disconnect" in signal:
        return DiagnosticCode.GT_PROVIDER_DISCONNECT, True
    if status == 400 or "badrequest" in name or "invalid request" in signal:
        return DiagnosticCode.GT_PROVIDER_BAD_REQUEST, False
    return DiagnosticCode.GT_PROVIDER_MALFORMED_RESPONSE, False


def _normalize(value: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return words or "unspecified"


def _assert_secret_free(value: Any, *, key: str = "") -> None:
    if key and _SECRET_KEY.search(key):
        raise ValueError(f"secret-like diagnostic field is prohibited: {key}")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _assert_secret_free(child, key=str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_secret_free(child)
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise ValueError("secret-like diagnostic value is prohibited")


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    code: DiagnosticCode
    severity: str
    phase: str
    subsystem: str
    capability: str
    task_id: str
    classification: str
    cause: str
    impact: str
    recovery: str
    retryable: bool
    event_sequence: int
    identities: dict[str, str] = field(default_factory=dict)
    evidence_refs: tuple[dict[str, str], ...] = ()
    schema: str = SCHEMA

    @classmethod
    def create(cls, *, code: str | DiagnosticCode, **kwargs: Any) -> DiagnosticEvent:
        try:
            parsed = DiagnosticCode(code)
        except ValueError as exc:
            raise ValueError(f"unknown closed diagnostic code: {code}") from exc
        identities = {str(k): str(v) for k, v in dict(kwargs.pop("identities", {})).items()}
        evidence = tuple(dict(row) for row in kwargs.pop("evidence_refs", ()))
        event = cls(code=parsed, identities=identities, evidence_refs=evidence, **kwargs)
        if event.severity not in {"INFO", "WARNING", "ERROR"}:
            raise ValueError("severity must be INFO, WARNING, or ERROR")
        if event.classification not in {"primary", "consequential"}:
            raise ValueError("classification must be primary or consequential")
        if event.event_sequence < 0:
            raise ValueError("event_sequence must be non-negative")
        _assert_secret_free(asdict(event))
        return event

    @property
    def normalized_cause(self) -> str:
        return _normalize(self.cause)

    @property
    def fingerprint(self) -> str:
        identities = [self.task_id]
        identities.extend(value for _, value in sorted(self.identities.items()))
        parts = (
            "gt.incident.v1", self.code.value, self.phase, self.subsystem,
            self.normalized_cause, *identities,
        )
        return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DiagnosticPaths:
    json: Path
    text: Path
    replay: Path


class DiagnosticJournal:
    def __init__(self, root: str | Path, *, task_id: str):
        self.root = Path(root)
        self.task_id = str(task_id)
        self._events: list[DiagnosticEvent] = []
        self._capabilities: dict[str, dict[str, Any]] = {}

    def record(self, event: DiagnosticEvent) -> None:
        if event.task_id != self.task_id:
            raise ValueError("diagnostic task identity mismatch")
        self._events.append(event)

    def capability(
        self, name: str, state: str | CapabilityState, evidence: str, *, required: bool = True
    ) -> None:
        parsed = CapabilityState(state)
        _assert_secret_free(evidence)
        self._capabilities[str(name)] = {
            "capability": str(name), "state": parsed.value, "required": bool(required),
            "declared": True, "initialized": parsed is not CapabilityState.FAILED,
            "triggered": parsed is not CapabilityState.UNEXERCISED,
            "delivered": parsed is CapabilityState.WORKING,
            "refused": parsed is CapabilityState.FAILED,
            "degraded": parsed is CapabilityState.DEGRADED,
            "verified": parsed is CapabilityState.WORKING,
            "evidence": _normalize(evidence),
        }

    def _aggregated(self) -> list[dict[str, Any]]:
        groups: dict[str, list[DiagnosticEvent]] = {}
        for event in self._events:
            groups.setdefault(event.fingerprint, []).append(event)
        rows: list[dict[str, Any]] = []
        for fingerprint, events in sorted(groups.items()):
            events.sort(key=lambda item: item.event_sequence)
            first = events[0]
            row = asdict(first)
            row["code"] = first.code.value
            row["normalized_cause"] = first.normalized_cause
            row["fingerprint"] = fingerprint
            row["occurrence_count"] = len(events)
            row["first_event_sequence"] = events[0].event_sequence
            row["last_event_sequence"] = events[-1].event_sequence
            row.pop("event_sequence", None)
            rows.append(row)
        return rows

    def seal(self) -> DiagnosticPaths:
        self.root.mkdir(parents=True, exist_ok=True)
        rows = self._aggregated()
        capabilities = [self._capabilities[key] for key in sorted(self._capabilities)]
        payload = {
            "schema": DOCUMENT_SCHEMA, "task_id": self.task_id,
            "diagnostics": rows, "capabilities": capabilities,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        replay = {
            "schema": "gt.incident_replay.v1", "task_id": self.task_id,
            "diagnostics_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "fingerprints": [row["fingerprint"] for row in rows],
            "evidence_refs": [ref for row in rows for ref in row["evidence_refs"]],
        }
        json_path = self.root / "diagnostics.json"
        text_path = self.root / "diagnostics.txt"
        replay_path = self.root / "incident-replay.json"
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines = [
            f"[GT][{row['severity']}][{row['code']}] task={self.task_id} "
            f"phase={row['phase']} cause={row['normalized_cause']} "
            f"impact={_normalize(row['impact'])} recovery={_normalize(row['recovery'])} "
            f"count={row['occurrence_count']}"
            for row in rows
        ]
        if not lines:
            lines = [f"[GT][INFO][HEALTHY] task={self.task_id}"]
        text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        replay_path.write_text(
            json.dumps(replay, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return DiagnosticPaths(json_path, text_path, replay_path)


@dataclass(frozen=True)
class DiagnosisReport:
    exit_code: int
    diagnostics: tuple[DiagnosticEvent, ...]
    primary_by_task: dict[str, DiagnosticEvent]
    artifact_issues: tuple[str, ...]
    capabilities: tuple[dict[str, Any], ...]
    task_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        task_rows = []
        for task in self.task_ids:
            event = self.primary_by_task.get(task)
            task_rows.append(
                {
                    "task_id": task,
                    "primary_diagnostic": event.code.value if event else "HEALTHY",
                    "fingerprint": event.fingerprint if event else "",
                    "recovery": _normalize(event.recovery) if event else "none",
                }
            )
        return {
            "schema": "gt.diagnostic_summary.v1", "exit_code": self.exit_code,
            "artifact_issues": list(self.artifact_issues),
            "tasks": task_rows,
            "capabilities": list(self.capabilities),
        }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _planned_tasks(root: Path) -> set[str]:
    planned: set[str] = set()
    for path in sorted(root.rglob("*.json")):
        if "plan" not in path.name.lower():
            continue
        try:
            value = _load_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        tasks = value.get("tasks") if isinstance(value, dict) else None
        if not isinstance(tasks, list) and isinstance(value, dict):
            tasks = value.get("task_ids")
        if isinstance(tasks, list) and all(isinstance(item, str) for item in tasks):
            planned.update(tasks)
    return planned


def diagnose_artifact_root(root: str | Path, *, strict: bool = False) -> DiagnosisReport:
    base = Path(root).resolve()
    issues: list[str] = []
    events: list[DiagnosticEvent] = []
    capabilities: list[dict[str, Any]] = []
    diagnosed_tasks: set[str] = set()
    paths = sorted(base.rglob("diagnostics.json")) if base.is_dir() else []
    if not paths:
        issues.append("no diagnostics.json artifacts discovered")
    for path in paths:
        try:
            payload = _load_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            issues.append(f"{path}: malformed JSON: {type(exc).__name__}")
            continue
        if not isinstance(payload, dict) or payload.get("schema") != DOCUMENT_SCHEMA:
            issues.append(f"{path}: invalid diagnostics schema")
            continue
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            issues.append(f"{path}: missing task identity")
            continue
        if task_id in diagnosed_tasks:
            issues.append(f"task {task_id}: duplicate diagnostics artifacts")
        diagnosed_tasks.add(task_id)
        capability_rows = payload.get("capabilities")
        if not isinstance(capability_rows, list):
            issues.append(f"{path}: capabilities must be an array")
            capability_rows = []
        if strict and not capability_rows:
            issues.append(f"{path}: no capability health rows")
        capability_names: set[str] = set()
        for capability in capability_rows:
            if not isinstance(capability, dict):
                issues.append(f"{path}: non-object capability row")
                continue
            name = str(capability.get("capability") or "")
            try:
                state = CapabilityState(capability.get("state", ""))
            except ValueError:
                issues.append(f"{path}: capability {name!r} has invalid state")
                continue
            if not name or name in capability_names:
                issues.append(f"{path}: missing or duplicate capability identity {name!r}")
                continue
            capability_names.add(name)
            bool_fields = (
                "required", "declared", "initialized", "triggered", "delivered",
                "refused", "degraded", "verified",
            )
            if any(not isinstance(capability.get(field), bool) for field in bool_fields):
                issues.append(f"{path}: capability {name!r} has malformed state flags")
                continue
            if state is CapabilityState.WORKING and not capability.get("verified"):
                issues.append(f"{path}: capability {name!r} claims WORKING without verification")
                continue
            capabilities.append({"task_id": task_id, **capability})
        replay_path = path.with_name("incident-replay.json")
        text_path = path.with_name("diagnostics.txt")
        if strict and not text_path.is_file():
            issues.append(f"{path}: missing diagnostics.txt")
        if not replay_path.is_file():
            issues.append(f"{path}: missing incident-replay.json")
        else:
            try:
                replay = _load_json(replay_path)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                issues.append(f"{replay_path}: malformed JSON: {type(exc).__name__}")
            else:
                canonical = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                if (
                    not isinstance(replay, dict)
                    or replay.get("schema") != "gt.incident_replay.v1"
                    or replay.get("task_id") != task_id
                ):
                    issues.append(f"{replay_path}: invalid replay identity")
                elif replay.get("diagnostics_sha256") != hashlib.sha256(canonical).hexdigest():
                    issues.append(f"{replay_path}: replay diagnostics digest mismatch")
                else:
                    expected_fingerprints = sorted(
                        str(row.get("fingerprint") or "")
                        for row in payload.get("diagnostics") or ()
                        if isinstance(row, dict)
                    )
                    if sorted(replay.get("fingerprints") or ()) != expected_fingerprints:
                        issues.append(
                            f"{replay_path}: replay fingerprint inventory mismatch"
                        )
        for row in payload.get("diagnostics") or ():
            if not isinstance(row, dict):
                issues.append(f"{path}: non-object diagnostic row")
                continue
            try:
                first_sequence = int(row.get("first_event_sequence") or 0)
                last_sequence = int(row.get("last_event_sequence") or 0)
                occurrence_count = int(row.get("occurrence_count") or 0)
                if (
                    row.get("schema") != SCHEMA
                    or occurrence_count < 1
                    or last_sequence < first_sequence
                ):
                    raise ValueError("invalid diagnostic occurrence envelope")
                event = DiagnosticEvent.create(
                    code=row.get("code", ""), severity=row.get("severity", ""),
                    phase=str(row.get("phase") or ""), subsystem=str(row.get("subsystem") or ""),
                    capability=str(row.get("capability") or ""),
                    task_id=str(row.get("task_id") or ""),
                    classification=str(row.get("classification") or ""),
                    cause=str(row.get("cause") or ""),
                    impact=str(row.get("impact") or ""), recovery=str(row.get("recovery") or ""),
                    retryable=bool(row.get("retryable")),
                    event_sequence=first_sequence,
                    identities=row.get("identities") or {},
                    evidence_refs=row.get("evidence_refs") or (),
                )
            except (TypeError, ValueError) as exc:
                issues.append(f"{path}: invalid diagnostic: {exc}")
                continue
            if event.task_id != task_id or row.get("fingerprint") != event.fingerprint:
                issues.append(f"{path}: task identity or fingerprint mismatch")
            events.append(event)
            for ref in event.evidence_refs:
                relative = str(ref.get("path") or "")
                expected = str(ref.get("sha256") or "")
                target = (path.parent / relative).resolve()
                try:
                    target.relative_to(base)
                except ValueError:
                    issues.append(f"{path}: evidence path escapes artifact root")
                    continue
                if not relative or not _SHA256.fullmatch(expected) or not target.is_file():
                    issues.append(f"{path}: missing or malformed evidence reference {relative!r}")
                    continue
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != expected:
                    issues.append(f"{target}: digest mismatch")
    if strict:
        planned = _planned_tasks(base)
        if not planned:
            issues.append("strict audit requires a discoverable task plan")
        for task in sorted(planned - diagnosed_tasks):
            issues.append(f"planned task {task}: missing diagnostics")
        for task in sorted(diagnosed_tasks - planned):
            issues.append(f"unplanned task {task}: unexpected diagnostics")
        for row in capabilities:
            if row.get("required") and row.get("state") != CapabilityState.WORKING:
                # Operational state is represented by exit 1, not malformed exit 2.
                pass
    primary: dict[str, DiagnosticEvent] = {}
    def precedence(event: DiagnosticEvent) -> tuple[int, int, str]:
        return (
            0 if event.classification == "primary" else 1,
            _PRIMARY_PRECEDENCE.get(event.code, 70),
            event.fingerprint,
        )

    for event in sorted(events, key=precedence):
        if event.task_id not in primary:
            primary[event.task_id] = event
    unhealthy = bool(events) or any(
        row.get("required") and row.get("state") != CapabilityState.WORKING
        for row in capabilities
    )
    exit_code = 2 if issues else 1 if unhealthy else 0
    return DiagnosisReport(
        exit_code, tuple(events), primary, tuple(issues), tuple(capabilities),
        tuple(sorted(diagnosed_tasks)),
    )


__all__ = [
    "CapabilityState", "DiagnosticCode", "DiagnosticEvent", "DiagnosticJournal",
    "DiagnosticPaths", "DiagnosisReport", "classify_provider_failure",
    "diagnose_artifact_root",
]
