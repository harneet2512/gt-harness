from __future__ import annotations

import hashlib
import json

from gt_engine.delivery_budget import (
    DELIVERY_BYTE_LIMITS,
    MAX_TASK_DELIVERIES,
    PROMPT_CONTEXT_BYTE_LIMIT,
    TOTAL_DELIVERY_BYTE_LIMIT,
)
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter


def _adapter(tmp_path) -> MiniSweAdapter:
    return MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path,
        predicates=[Predicate("p", "p")],
    )


def _events(adapter: MiniSweAdapter) -> list[dict]:
    return [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]


def test_delivery_policy_preserves_size_caps_and_uses_storm_backstop() -> None:
    assert DELIVERY_BYTE_LIMITS == {
        "repository_start": 2_000,
        "repository_update": 1_400,
    }
    assert PROMPT_CONTEXT_BYTE_LIMIT == 1_400
    assert TOTAL_DELIVERY_BYTE_LIMIT == 9_600
    assert MAX_TASK_DELIVERIES == 24


def test_five_distinct_legitimate_deliveries_are_admitted(tmp_path) -> None:
    adapter = _adapter(tmp_path)

    admitted = [
        adapter.admit_model_visible_delivery(
            lane="prompt",
            kind="context_delta",
            rendered=f"distinct-{ordinal}",
            action_index=0,
            iteration=ordinal,
            dedup_key=f"legacy-iteration-key-{ordinal}",
        )
        for ordinal in range(5)
    ]

    assert admitted == [True] * 5


def test_pathological_twenty_fifth_delivery_is_refused_and_journaled(
    tmp_path,
) -> None:
    adapter = _adapter(tmp_path)
    admitted = [
        adapter.admit_model_visible_delivery(
            lane="sealed",
            kind="recovery",
            rendered=f"{ordinal:02d}:" + ("x" * 296),
            action_index=ordinal,
            iteration=ordinal,
            dedup_key=f"storm-{ordinal}",
        )
        for ordinal in range(25)
    ]

    assert admitted == ([True] * 24) + [False]
    refusals = [row for row in _events(adapter) if row["event"] == "delivery_refused"]
    assert len(refusals) == 1
    assert refusals[0]["reason"] == "task_delivery_storm_backstop"
    assert refusals[0]["candidate_ordinal"] == 25
    assert refusals[0]["task_delivery_limit"] == 24


def test_prompt_lane_drops_identical_bytes_across_iterations(tmp_path) -> None:
    adapter = _adapter(tmp_path)
    first = adapter.admit_model_visible_delivery(
        lane="prompt",
        kind="context_delta",
        rendered="identical prompt bytes",
        action_index=0,
        iteration=1,
        dedup_key="prompt:iteration:1",
    )
    second = adapter.admit_model_visible_delivery(
        lane="prompt",
        kind="context_delta",
        rendered="identical prompt bytes",
        action_index=0,
        iteration=2,
        dedup_key="prompt:iteration:2",
    )

    assert first is True
    assert second is False
    rows = _events(adapter)
    deliveries = [row for row in rows if row["event"] == "context_addition_delivery"]
    assert len(deliveries) == 1
    identity = hashlib.sha256(b"identical prompt bytes").hexdigest()
    assert deliveries[0]["dedup_key"] == f"prompt:{identity}"
    assert deliveries[0]["delivery_identity"] == identity
    refused = [row for row in rows if row["event"] == "delivery_refused"]
    assert refused[-1]["reason"] == "duplicate_delivery_identity"


def test_total_budget_refusal_is_journaled_with_conservation_fields(tmp_path) -> None:
    adapter = _adapter(tmp_path)
    admitted = [
        adapter.admit_model_visible_delivery(
            lane="sealed",
            kind="recovery",
            rendered=f"{ordinal}:" + (chr(65 + ordinal) * 1_298),
            action_index=ordinal,
            iteration=ordinal,
            dedup_key=f"budget-{ordinal}",
        )
        for ordinal in range(8)
    ]

    assert admitted == ([True] * 7) + [False]
    refusal = [
        row for row in _events(adapter) if row["event"] == "delivery_refused"
    ][0]
    assert refusal["reason"] == "task_delivery_byte_ceiling"
    assert refusal["admitted_count"] == 7
    assert refusal["admitted_bytes"] == 9_100
    assert refusal["task_byte_limit"] == 9_600
