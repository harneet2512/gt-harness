"""Fail-closed deterministic acceptance gate for the decision-value repair."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from .run_receipt_v2 import is_complete_run_receipt

_TERMINAL_FEATURE_STAGES = frozenset(
    {"NOT_APPLICABLE", "ABSTAINED", "DELIVERED", "VALIDATED", "CONTRADICTED"}
)


@dataclass(frozen=True, slots=True)
class DecisionValueGateReport:
    passed: bool
    receipt_completeness: float
    repository_revision_agreement: float
    stale_deliveries: int
    invalid_delivery_bytes: int
    silent_graph_degradations: int
    certified_source_precision: float
    implementation_owner_top3_recall: float
    duplicate_full_rebuilds: int
    incomplete_feature_lifecycles: int
    failures: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"schema": "gt.decision_value_gate.v1", **asdict(self)}


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def evaluate_decision_value_gates(
    *,
    expected_run_count: int,
    run_receipts: Iterable[dict[str, Any]],
    certified_fact_checks: Iterable[dict[str, Any]],
    implementation_owner_cases: Iterable[dict[str, Any]],
) -> DecisionValueGateReport:
    """Recompute every acceptance measure from atomic rows.

    ``certified_fact_checks`` rows carry ``source_supported`` booleans from an
    independent oracle. ``implementation_owner_cases`` carry ``expected`` and
    ordered ``ranked`` symbol identities. Empty fact/owner populations fail
    closed rather than producing a vacuous 100% score.
    """

    receipts = [dict(row) for row in run_receipts]
    valid_receipts = [row for row in receipts if is_complete_run_receipt(row)]
    receipt_completeness = _ratio(
        len(valid_receipts), max(0, int(expected_run_count)), empty=0.0
    )
    revision_matches = 0
    delivery_count = 0
    stale_deliveries = 0
    invalid_delivery_bytes = 0
    silent_degradations = 0
    duplicate_full_rebuilds = 0
    incomplete_lifecycles = 0
    for receipt in valid_receipts:
        successful_graphs = {
            (
                str(row.get("repository_revision") or ""),
                str(row.get("graph_revision") or ""),
            )
            for row in receipt.get("graph_builds") or ()
            if row.get("success") is True
            and row.get("repository_revision")
            and row.get("graph_revision")
        }
        for delivery in receipt.get("deliveries") or ():
            encoded = str(delivery.get("model_visible_bytes_hex") or "")
            expected_digest = str(
                delivery.get("model_visible_bytes_sha256") or ""
            )
            try:
                delivered_bytes = bytes.fromhex(encoded)
            except ValueError:
                delivered_bytes = b""
                invalid_delivery_bytes += 1
            else:
                if expected_digest != hashlib.sha256(delivered_bytes).hexdigest():
                    invalid_delivery_bytes += 1
            graph_revision = str(delivery.get("graph_revision") or "")
            if not graph_revision:
                continue
            delivery_count += 1
            identity = (
                str(delivery.get("repository_revision") or ""),
                graph_revision,
            )
            if identity in successful_graphs:
                revision_matches += 1
            else:
                stale_deliveries += 1
        builds_by_revision: dict[str, list[dict[str, Any]]] = {}
        for build in receipt.get("graph_builds") or ():
            workspace_revision = str(build.get("workspace_revision") or "")
            builds_by_revision.setdefault(workspace_revision, []).append(build)
        for workspace_revision, builds in builds_by_revision.items():
            full_successes = sum(
                row.get("mode") == "full" and row.get("success") is True
                for row in builds
            )
            if workspace_revision and full_successes > 1:
                duplicate_full_rebuilds += full_successes - 1
            for index, build in enumerate(builds):
                if build.get("success") is not False:
                    continue
                recovered = any(
                    later.get("success") is True for later in builds[index + 1:]
                )
                if not recovered:
                    silent_degradations += 1
        for lifecycle in receipt.get("feature_lifecycle_transitions") or ():
            if str(lifecycle.get("stage") or "") not in _TERMINAL_FEATURE_STAGES:
                incomplete_lifecycles += 1

    fact_rows = [dict(row) for row in certified_fact_checks]
    correct_facts = sum(row.get("source_supported") is True for row in fact_rows)
    source_precision = _ratio(correct_facts, len(fact_rows), empty=0.0)
    owner_rows = [dict(row) for row in implementation_owner_cases]
    owner_hits = sum(
        str(row.get("expected") or "")
        in [str(item) for item in (row.get("ranked") or ())[:3]]
        for row in owner_rows
    )
    owner_recall = _ratio(owner_hits, len(owner_rows), empty=0.0)
    revision_agreement = _ratio(
        revision_matches, delivery_count, empty=1.0 if valid_receipts else 0.0
    )

    failures: list[str] = []
    if receipt_completeness != 1.0:
        failures.append("receipt_completeness_below_100_percent")
    if revision_agreement != 1.0:
        failures.append("repository_revision_agreement_below_100_percent")
    if stale_deliveries:
        failures.append("stale_delivery_detected")
    if invalid_delivery_bytes:
        failures.append("invalid_model_visible_delivery_bytes")
    if silent_degradations:
        failures.append("silent_graph_degradation_detected")
    if source_precision < 0.98:
        failures.append("certified_source_precision_below_98_percent")
    if owner_recall < 0.90:
        failures.append("implementation_owner_top3_recall_below_90_percent")
    if duplicate_full_rebuilds:
        failures.append("duplicate_full_rebuild_for_workspace_revision")
    if incomplete_lifecycles:
        failures.append("incomplete_triggered_feature_lifecycle")
    return DecisionValueGateReport(
        passed=not failures,
        receipt_completeness=receipt_completeness,
        repository_revision_agreement=revision_agreement,
        stale_deliveries=stale_deliveries,
        invalid_delivery_bytes=invalid_delivery_bytes,
        silent_graph_degradations=silent_degradations,
        certified_source_precision=source_precision,
        implementation_owner_top3_recall=owner_recall,
        duplicate_full_rebuilds=duplicate_full_rebuilds,
        incomplete_feature_lifecycles=incomplete_lifecycles,
        failures=tuple(failures),
    )


__all__ = ["DecisionValueGateReport", "evaluate_decision_value_gates"]
