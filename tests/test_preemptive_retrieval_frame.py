"""RED-first contract tests for the additive pre-action retrieval frame.

These tests intentionally target the small host-owned seam that the approved
preemptive-frame change must add.  They do not inspect hidden model reasoning,
use markers, or grant the frame permission to execute an action.  Until the
runtime seam exists, the tests fail with a clear missing-feature assertion.

The contract is deliberately narrow:

* OFF/disabled is byte-for-byte provider neutral;
* one grounded frame may coexist with one distinct legacy payload in the
  exact next request;
* delivery is a provider-view transformation, never another model/tool call;
* stale, timed-out, duplicate, or over-budget frames abstain and are receipted.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from eval.gt_central_agent import MiniSweCentralAgent, _preemptive_frame_identity


def test_preemptive_frame_identity_changes_when_claim_set_changes():
    """Repeated query state must not create duplicate receipt identities."""

    first = _preemptive_frame_identity("query-r1", ("claim-a",), 43, "src-r1")
    second = _preemptive_frame_identity(
        "query-r1", ("claim-b",), 44, "src-r1"
    )
    assert first != second
    # The same concrete delivery remains replay-stable.
    assert first == _preemptive_frame_identity(
        "query-r1", ("claim-a",), 43, "src-r1"
    )


def _api():
    """Load the proposed seam without hiding a missing implementation."""

    try:
        from gt_engine.preemptive_retrieval import (  # type: ignore[import-not-found]
            PreemptiveFrame,
            PreemptiveFrameStatus,
            compile_preemptive_frame,
        )
    except ImportError:  # RED: implementation has not landed yet.
        pytest.fail(
            "approved additive preemptive retrieval seam is missing: "
            "gt_engine.preemptive_retrieval",
            pytrace=False,
        )
    return PreemptiveFrame, PreemptiveFrameStatus, compile_preemptive_frame


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "You are Mini-SWE."},
        {"role": "user", "content": "Fix the parser."},
        {"role": "tool", "content": "rg -n parser src/parser.py\n"},
    ]


def _frame(PreemptiveFrame):
    return PreemptiveFrame(
        frame_id="pf-001",
        text="Repository anchor: src/parser.py:41 parse_token; caller tests/test_parser.py:18.",
        source_revision="src-r7",
        eligible_call=3,
        evidence_action=2,
        evidence_ids=("e-parser-41",),
        claim_ids=("c-parser-41",),
    )


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_disabled_mode_preserves_provider_messages_byte_for_byte():
    PreemptiveFrame, PreemptiveFrameStatus, compile_preemptive_frame = _api()
    messages = _messages()
    before = _canonical(messages)

    result = compile_preemptive_frame(
        messages,
        frame=_frame(PreemptiveFrame),
        legacy_payload="Existing grounded validation failure: pytest -q failed.",
        enabled=False,
        current_source_revision="src-r7",
        current_call=3,
        budget_chars=20_000,
        now_ms=100,
        agent_action_count=2,
    )

    assert _canonical(result.provider_messages) == before
    assert result.status is PreemptiveFrameStatus.DISABLED
    assert result.receipt["provider_message_indices"] == []
    assert result.receipt["delivered_before_model_query"] is False


def test_frame_and_distinct_legacy_payload_share_exact_next_provider_request():
    PreemptiveFrame, PreemptiveFrameStatus, compile_preemptive_frame = _api()
    legacy = "Existing grounded validation failure: pytest -q failed."

    result = compile_preemptive_frame(
        _messages(),
        frame=_frame(PreemptiveFrame),
        legacy_payload=legacy,
        enabled=True,
        current_source_revision="src-r7",
        current_call=3,
        budget_chars=20_000,
        now_ms=100,
    )

    assert result.status is PreemptiveFrameStatus.DELIVERED
    payload = "\n".join(str(item.get("content") or "") for item in result.provider_messages)
    assert _frame(PreemptiveFrame).text in payload
    assert legacy in payload
    assert _frame(PreemptiveFrame).text != legacy
    assert result.receipt["eligible_call"] == 3
    assert result.receipt["first_eligible_request"] is True
    assert result.receipt["provider_message_indices"]
    assert result.receipt["request_payload_sha256"] == hashlib.sha256(
        _canonical(result.provider_messages)
    ).hexdigest()


def test_preemptive_delivery_does_not_add_model_or_agent_action_calls():
    PreemptiveFrame, PreemptiveFrameStatus, compile_preemptive_frame = _api()

    result = compile_preemptive_frame(
        _messages(),
        frame=_frame(PreemptiveFrame),
        legacy_payload="existing fact",
        enabled=True,
        current_source_revision="src-r7",
        current_call=3,
        budget_chars=20_000,
        now_ms=100,
        model_query_count=12,
        agent_action_count=7,
    )

    assert result.status is PreemptiveFrameStatus.DELIVERED
    assert result.receipt["model_query_count_before"] == 12
    assert result.receipt["model_query_count_after"] == 12
    assert result.receipt["agent_action_count_before"] == 7
    assert result.receipt["agent_action_count_after"] == 7
    assert result.receipt["extra_model_calls"] == 0
    assert result.receipt["extra_agent_actions"] == 0


@pytest.mark.parametrize(
    ("name", "kwargs", "reason"),
    [
        (
            "stale",
            {"current_source_revision": "src-r8"},
            "stale_source_revision",
        ),
        (
            "timeout",
            {"deadline_ms": 100, "now_ms": 101},
            "preemptive_frame_timeout",
        ),
        (
            "over_budget",
            {"budget_chars": 1},
            "preemptive_frame_over_budget",
        ),
    ],
)
def test_invalid_frame_abstains_without_provider_delivery(name, kwargs, reason):
    del name
    PreemptiveFrame, PreemptiveFrameStatus, compile_preemptive_frame = _api()
    args = {
        "messages": _messages(),
        "frame": _frame(PreemptiveFrame),
        "legacy_payload": "existing fact",
        "enabled": True,
        "current_source_revision": "src-r7",
        "current_call": 3,
        "budget_chars": 20_000,
        "now_ms": 100,
    }
    args.update(kwargs)

    result = compile_preemptive_frame(**args)

    assert result.status is PreemptiveFrameStatus.ABSTAINED
    assert result.receipt["reason_code"] == reason
    assert result.receipt["provider_message_indices"] == []
    assert result.receipt["delivered_before_model_query"] is False
    assert _canonical(result.provider_messages) == _canonical(_messages())


def test_duplicate_frame_abstains_and_does_not_duplicate_text():
    PreemptiveFrame, PreemptiveFrameStatus, compile_preemptive_frame = _api()
    frame = _frame(PreemptiveFrame)
    messages = _messages() + [{"role": "tool", "content": frame.text}]

    result = compile_preemptive_frame(
        messages,
        frame=frame,
        legacy_payload="existing fact",
        enabled=True,
        current_source_revision="src-r7",
        current_call=3,
        budget_chars=20_000,
        now_ms=100,
    )

    assert result.status is PreemptiveFrameStatus.ABSTAINED
    assert result.receipt["reason_code"] == "duplicate_preemptive_frame"
    payload = "\n".join(str(item.get("content") or "") for item in result.provider_messages)
    assert payload.count(frame.text) == 1


def test_receipt_proves_first_eligible_timing_and_hash_without_model_marker():
    PreemptiveFrame, PreemptiveFrameStatus, compile_preemptive_frame = _api()
    result = compile_preemptive_frame(
        _messages(),
        frame=_frame(PreemptiveFrame),
        legacy_payload="existing fact",
        enabled=True,
        current_source_revision="src-r7",
        current_call=3,
        budget_chars=20_000,
        now_ms=100,
        agent_action_count=2,
    )

    assert result.status is PreemptiveFrameStatus.DELIVERED
    receipt = result.receipt
    assert receipt["evidence_action"] == 2
    assert receipt["eligible_call"] == 3
    assert receipt["prepared_call"] == 3
    assert receipt["delivered_before_model_query"] is True
    assert receipt["one_step_late"] is False
    assert receipt["predictive"] is False
    assert receipt["request_payload_sha256"]
    assert "marker" not in " ".join(
        str(item.get("content") or "") for item in result.provider_messages
    ).lower()


def test_predictive_receipt_compares_action_to_action_not_action_to_model_call():
    PreemptiveFrame, PreemptiveFrameStatus, compile_preemptive_frame = _api()
    frame = PreemptiveFrame(
        frame_id="pf-batch",
        text="Repository anchor: src/parser.py:41 parse_token.",
        source_revision="src-r7",
        eligible_call=2,
        evidence_action=3,
        evidence_ids=("e-parser-41",),
        claim_ids=("c-parser-41",),
    )

    result = compile_preemptive_frame(
        _messages(),
        frame=frame,
        legacy_payload="",
        enabled=True,
        current_source_revision="src-r7",
        current_call=2,
        budget_chars=20_000,
        now_ms=100,
        model_query_count=1,
        agent_action_count=3,
    )

    assert result.status is PreemptiveFrameStatus.DELIVERED
    assert result.receipt["predictive"] is False


def _agent_request_api(agent):
    """Return the host-owned Mini-SWE seam required by the integration patch."""

    method = getattr(agent, "_prepare_preemptive_retrieval_request", None)
    if method is None:  # RED: the agent boundary has not landed yet.
        pytest.fail(
            "MiniSweCentralAgent has no _prepare_preemptive_retrieval_request "
            "integration seam",
            pytrace=False,
        )
    return method


def _agent(tmp_path, *, enabled: bool = True):
    return MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active" if enabled else "off",
        enable_context_frontier=False,
        enable_task_start_advisory=False,
        enable_feature_guidance=False,
        enable_repository_intelligence=False,
    )


def _prepare(agent, *, frame, legacy_payload="", enabled=True, **kwargs):
    method = _agent_request_api(agent)
    parameters = {
        "frame": frame,
        "legacy_payload": legacy_payload,
        "enabled": enabled,
        "current_source_revision": "src-r7",
        "current_call": 3,
        "budget_chars": 20_000,
        "now_ms": 100,
        "agent_action_count": 2,
    }
    parameters.update(kwargs)
    return method(_messages(), **parameters)


def test_miniswe_agent_disabled_request_is_byte_identical(tmp_path):
    PreemptiveFrame, PreemptiveFrameStatus, _ = _api()
    agent = _agent(tmp_path, enabled=False)
    messages = _messages()

    result = _prepare(
        agent,
        frame=_frame(PreemptiveFrame),
        legacy_payload="legacy feature payload",
        enabled=False,
    )

    assert _canonical(result.provider_messages) == _canonical(messages)
    assert result.status is PreemptiveFrameStatus.DISABLED
    assert result.receipt["provider_message_indices"] == []


def test_miniswe_agent_frame_and_legacy_payload_coexist_in_next_request(tmp_path):
    PreemptiveFrame, PreemptiveFrameStatus, _ = _api()
    agent = _agent(tmp_path)
    legacy = "legacy feature payload: signature_delta for src/parser.py"

    result = _prepare(
        agent,
        frame=_frame(PreemptiveFrame),
        legacy_payload=legacy,
    )

    payload = "\n".join(str(item.get("content") or "") for item in result.provider_messages)
    assert result.status is PreemptiveFrameStatus.DELIVERED
    assert _frame(PreemptiveFrame).text in payload
    assert legacy in payload
    assert result.receipt["prepared_call"] == 3
    assert result.receipt["eligible_call"] == 3
    assert result.receipt["delivered_before_model_query"] is True
    assert result.receipt["one_step_late"] is False


def test_miniswe_agent_integration_does_not_request_or_execute_an_extra_action(tmp_path):
    PreemptiveFrame, PreemptiveFrameStatus, _ = _api()
    agent = _agent(tmp_path)
    result = _prepare(
        agent,
        frame=_frame(PreemptiveFrame),
        legacy_payload="legacy feature payload",
        model_query_count=4,
        agent_action_count=2,
    )

    assert result.status is PreemptiveFrameStatus.DELIVERED
    assert result.receipt["extra_model_calls"] == 0
    assert result.receipt["extra_agent_actions"] == 0
    assert result.receipt["model_query_count_before"] == 4
    assert result.receipt["model_query_count_after"] == 4
    assert result.receipt["agent_action_count_before"] == 2
    assert result.receipt["agent_action_count_after"] == 2


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"current_source_revision": "src-r8"}, "stale_source_revision"),
        ({"deadline_ms": 100, "now_ms": 101}, "preemptive_frame_timeout"),
        ({"budget_chars": 1}, "preemptive_frame_over_budget"),
    ],
)
def test_miniswe_agent_integration_abstains_before_provider_query(tmp_path, kwargs, reason):
    PreemptiveFrame, PreemptiveFrameStatus, _ = _api()
    agent = _agent(tmp_path)
    result = _prepare(agent, frame=_frame(PreemptiveFrame), **kwargs)

    assert result.status is PreemptiveFrameStatus.ABSTAINED
    assert result.receipt["reason_code"] == reason
    assert result.receipt["provider_message_indices"] == []
    assert result.receipt["delivered_before_model_query"] is False
    assert _canonical(result.provider_messages) == _canonical(_messages())


def test_miniswe_agent_receipt_hash_binds_exact_provider_request(tmp_path):
    PreemptiveFrame, PreemptiveFrameStatus, _ = _api()
    agent = _agent(tmp_path)
    result = _prepare(
        agent,
        frame=_frame(PreemptiveFrame),
        legacy_payload="legacy feature payload",
    )

    assert result.status is PreemptiveFrameStatus.DELIVERED
    assert result.receipt["request_payload_sha256"] == hashlib.sha256(
        _canonical(result.provider_messages)
    ).hexdigest()
    assert result.receipt["predictive"] is False
    assert result.receipt["evidence_action"] == 2
