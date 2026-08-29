"""Fail-closed per-call and per-task mechanical-completeness proofs.

This module does not claim that a stochastic model will solve a task.  It
proves the narrower product contract: every admitted provider request used a
complete, current, fully-accounted GT state and every terminal release check
for the task passed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

_SATISFIED = "SATISFIED"
_NOT_APPLICABLE = "PROVEN_NOT_APPLICABLE"
_FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ProviderBarrierInputsV2:
    call: int
    request_payload_sha256: str
    provider_messages_sha256: str
    observation_id: str
    decision_boundary: str
    repository_applicability: str
    graph_required: bool
    graph_input_revision: str
    graph_revision: str
    graph_freshness: str
    dense_required: bool
    dense_status: str
    augmentation_disposition: str
    source_snapshot_complete: bool
    runtime_contract_ready: bool
    task_semantic_ready: bool
    graph_current: bool
    repository_intelligence_ready: bool
    retrieval_ready: bool
    persistent_state_ready: bool
    previous_actions_finalized: bool
    context_candidate_count: int
    context_accounted_count: int
    contribution_candidate_count: int
    contribution_accounted_count: int
    selected_contribution_ids: tuple[str, ...]
    provider_value_contribution_ids: tuple[str, ...]
    replay_capture_enabled: bool
    dispatch_proof_sha256: str = ""
    runtime_attestation_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["schema"] = "gt.provider_barrier_inputs.v2"
        row["selected_contribution_ids"] = list(self.selected_contribution_ids)
        row["provider_value_contribution_ids"] = list(
            self.provider_value_contribution_ids
        )
        return row

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _requirement(
    requirement_id: str,
    *,
    satisfied: bool,
    failure: str,
    evidence: Mapping[str, Any],
    not_applicable: bool = False,
) -> tuple[dict[str, Any], str | None]:
    if not_applicable:
        status = _NOT_APPLICABLE
        failure_value = None
    elif satisfied:
        status = _SATISFIED
        failure_value = None
    else:
        status = _FAILED
        failure_value = failure
    return (
        {
            "requirement_id": requirement_id,
            "status": status,
            "evidence": dict(evidence),
        },
        failure_value,
    )


def _is_sha256(value: object) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def evaluate_provider_barrier(
    *,
    call: int,
    request_payload_sha256: str,
    provider_messages_sha256: str,
    source_snapshot_complete: bool,
    runtime_contract_ready: bool,
    task_semantic_ready: bool,
    graph_applicable: bool,
    graph_current: bool,
    repository_intelligence_ready: bool,
    retrieval_ready: bool,
    persistent_state_ready: bool,
    previous_actions_finalized: bool,
    context_candidate_count: int,
    context_accounted_count: int,
    contribution_candidate_count: int,
    contribution_accounted_count: int,
    selected_contribution_ids: Iterable[str],
    provider_value_contribution_ids: Iterable[str],
    replay_capture_enabled: bool,
) -> dict[str, Any]:
    """Evaluate the last host-owned barrier before a provider invocation."""

    requirements: list[dict[str, Any]] = []
    failures: list[str] = []

    def add(
        requirement_id: str,
        *,
        satisfied: bool,
        failure: str,
        evidence: Mapping[str, Any],
        not_applicable: bool = False,
    ) -> None:
        row, failed = _requirement(
            requirement_id,
            satisfied=satisfied,
            failure=failure,
            evidence=evidence,
            not_applicable=not_applicable,
        )
        requirements.append(row)
        if failed:
            failures.append(failed)

    add(
        "request_identity",
        satisfied=_is_sha256(request_payload_sha256),
        failure="request_identity_missing",
        evidence={"request_payload_sha256": request_payload_sha256},
    )
    add(
        "provider_view_identity",
        satisfied=_is_sha256(provider_messages_sha256),
        failure="provider_view_identity_missing",
        evidence={"provider_messages_sha256": provider_messages_sha256},
    )
    add(
        "runtime_contract",
        satisfied=bool(runtime_contract_ready),
        failure="runtime_contract_missing",
        evidence={"ready": bool(runtime_contract_ready)},
    )
    add(
        "task_semantic_substrate",
        satisfied=bool(task_semantic_ready),
        failure="task_semantic_substrate_not_ready",
        evidence={"ready": bool(task_semantic_ready)},
    )
    add(
        "source_snapshot_complete",
        satisfied=bool(source_snapshot_complete),
        failure="source_snapshot_incomplete",
        evidence={"complete": bool(source_snapshot_complete)},
    )
    add(
        "graph_current",
        satisfied=bool(graph_current),
        failure="graph_not_current",
        evidence={
            "applicable": bool(graph_applicable),
            "current": bool(graph_current),
        },
        not_applicable=not graph_applicable,
    )
    for requirement_id, ready, failure in (
        (
            "repository_intelligence",
            repository_intelligence_ready,
            "repository_intelligence_not_ready",
        ),
        ("retrieval", retrieval_ready, "retrieval_not_ready"),
        (
            "persistent_state",
            persistent_state_ready,
            "persistent_state_not_ready",
        ),
    ):
        add(
            requirement_id,
            satisfied=bool(ready),
            failure=failure,
            evidence={"applicable": bool(graph_applicable), "ready": bool(ready)},
            not_applicable=not graph_applicable,
        )
    add(
        "previous_action_finalized",
        satisfied=bool(previous_actions_finalized),
        failure="previous_action_not_finalized",
        evidence={"finalized": bool(previous_actions_finalized)},
    )
    add(
        "context_fact_accounting",
        satisfied=(
            int(context_candidate_count) == int(context_accounted_count)
            and int(context_candidate_count) >= 0
        ),
        failure="context_fact_accounting_mismatch",
        evidence={
            "candidate_count": int(context_candidate_count),
            "accounted_count": int(context_accounted_count),
        },
    )
    add(
        "contribution_accounting",
        satisfied=(
            int(contribution_candidate_count) == int(contribution_accounted_count)
            and int(contribution_candidate_count) >= 0
        ),
        failure="contribution_accounting_mismatch",
        evidence={
            "candidate_count": int(contribution_candidate_count),
            "accounted_count": int(contribution_accounted_count),
        },
    )
    selected_ids = tuple(str(item) for item in selected_contribution_ids if str(item))
    value_ids = {
        str(item) for item in provider_value_contribution_ids if str(item)
    }
    add(
        "provider_value_certification",
        satisfied=(
            len(selected_ids) == len(set(selected_ids))
            and set(selected_ids) <= value_ids
        ),
        failure="provider_value_certificate_mismatch",
        evidence={
            "selected_contribution_ids": list(selected_ids),
            "certified_contribution_ids": sorted(value_ids),
        },
    )
    add(
        "replay_capture",
        satisfied=bool(replay_capture_enabled),
        failure="replay_capture_disabled",
        evidence={"enabled": bool(replay_capture_enabled)},
    )
    return {
        "schema": "gt.provider_mechanical_barrier.v1",
        "call": int(call),
        "status": "PASS" if not failures else "BLOCKED",
        "requirements": requirements,
        "failures": failures,
    }


def evaluate_provider_barrier_v2(inputs: ProviderBarrierInputsV2) -> dict[str, Any]:
    """Evaluate and receipt the exact immutable facts present before dispatch."""

    result = evaluate_provider_barrier(
        call=inputs.call,
        request_payload_sha256=inputs.request_payload_sha256,
        provider_messages_sha256=inputs.provider_messages_sha256,
        source_snapshot_complete=inputs.source_snapshot_complete,
        runtime_contract_ready=inputs.runtime_contract_ready,
        task_semantic_ready=inputs.task_semantic_ready,
        graph_applicable=inputs.graph_required,
        graph_current=inputs.graph_current,
        repository_intelligence_ready=inputs.repository_intelligence_ready,
        retrieval_ready=inputs.retrieval_ready,
        persistent_state_ready=inputs.persistent_state_ready,
        previous_actions_finalized=inputs.previous_actions_finalized,
        context_candidate_count=inputs.context_candidate_count,
        context_accounted_count=inputs.context_accounted_count,
        contribution_candidate_count=inputs.contribution_candidate_count,
        contribution_accounted_count=inputs.contribution_accounted_count,
        selected_contribution_ids=inputs.selected_contribution_ids,
        provider_value_contribution_ids=inputs.provider_value_contribution_ids,
        replay_capture_enabled=inputs.replay_capture_enabled,
    )
    return {
        **result,
        "schema": "gt.provider_mechanical_barrier.v2",
        "inputs": inputs.as_dict(),
        "inputs_sha256": inputs.sha256,
    }


def build_task_execution_certificate(
    *,
    task: str,
    provider_barriers: Iterable[Mapping[str, Any]],
    dispatched_calls: int,
    barrier_context_count: int | None = None,
    non_dispatched_calls: Iterable[int] = (),
    release_checks: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Join live barriers and authoritative task release checks."""

    barriers = [dict(row) for row in provider_barriers]
    checks = [dict(row) for row in release_checks]
    failures: list[str] = []
    non_dispatched = {int(call) for call in non_dispatched_calls}
    expected_barrier_count = (
        int(dispatched_calls)
        if barrier_context_count is None
        else int(barrier_context_count)
    )
    if len(barriers) != expected_barrier_count:
        failures.append("provider_barrier_count_mismatch")
    for barrier in barriers:
        # A prepared request that the host correctly refused to send can have
        # a blocked barrier (for example, a graph refresh became stale). It is
        # not a provider delivery failure because no model call occurred.
        if barrier.get("status") != "PASS" and int(barrier.get("call") or 0) not in non_dispatched:
            failures.extend(str(item) for item in barrier.get("failures") or ())
    requirement_rows: list[dict[str, Any]] = []
    for check in checks:
        passed = check.get("passed") is True
        check_failures = [str(item) for item in check.get("failures") or ()]
        details = dict(check.get("details") or {})
        proven_not_applicable = bool(
            passed
            and (
                details.get("required") is False
                or details.get("applicability")
                == "not_applicable_no_supported_source"
            )
        )
        requirement_rows.append(
            {
                "requirement_id": str(check.get("name") or "unknown"),
                "status": (
                    _NOT_APPLICABLE
                    if proven_not_applicable
                    else _SATISFIED
                    if passed
                    else _FAILED
                ),
                "evidence": {
                    "failures": check_failures,
                    "details": details,
                },
            }
        )
        if not passed:
            failures.extend(check_failures or [f"{check.get('name')}:failed"])
    failed_count = sum(row["status"] == _FAILED for row in requirement_rows)
    pending_count = sum(
        row["status"] not in {_SATISFIED, _NOT_APPLICABLE, _FAILED}
        for row in requirement_rows
    )
    return {
        "schema": "gt.task_execution_certificate.v1",
        "task": str(task),
        "status": "PASS" if not failures and pending_count == 0 else "BLOCKED",
        "provider_barrier_count": len(barriers),
        "dispatched_provider_call_count": int(dispatched_calls),
        "provider_barriers": barriers,
        "requirements": requirement_rows,
        "pending_requirement_count": pending_count,
        "failed_requirement_count": failed_count,
        "failures": list(dict.fromkeys(failures)),
    }
