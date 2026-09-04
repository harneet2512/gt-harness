from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_engine.miniswe_typed_actions import execute_typed_action
from gt_engine.provider_limits import (
    ProviderContextWindowUnavailable,
    ProviderRequestTooLarge,
    enforce_provider_request_limit,
    provider_request_bytes,
)


def test_literal_query_is_bounded_before_model_visibility(tmp_path: Path):
    for index in range(30):
        (tmp_path / f"f{index:02}.txt").write_text(
            "needle " + ("x" * 900) + "\n", encoding="utf-8"
        )
    request = {
        "schema": "gt.action_request.v1", "action_id": "wide",
        "kind": "exact_literal_search",
        "arguments": {"literal": "needle", "paths": ["."]},
        "repository_snapshot": "a" * 64,
    }
    result = execute_typed_action(request, repo_root=tmp_path)
    body = json.loads(result["output"])

    assert len(result["output"].encode("utf-8")) <= 16_384
    assert len(body["direct_answer"]) <= 20
    assert all(len(row["preview"].encode("utf-8")) <= 512 for row in body["direct_answer"])
    assert "query_match_limit" in body["evidence"]["omissions"]
    assert result["returncode"] == 2


def test_provider_size_refusal_occurs_before_transport():
    payload = {"messages": [{"role": "user", "content": "too many tokens"}]}
    try:
        enforce_provider_request_limit(
            payload,
            context_window_tokens=100,
            reserved_output_tokens=20,
            metadata_source="provider:/models",
            token_counter=lambda _payload: 81,
        )
    except ProviderRequestTooLarge as exc:
        assert exc.code == "GT_PROVIDER_REQUEST_TOO_LARGE"
        assert exc.retryable is False
        assert exc.request_tokens == 81
        assert exc.input_budget_tokens == 80
        assert exc.context_window_tokens == 100
        assert exc.reserved_output_tokens == 20
        assert exc.metadata_source == "provider:/models"
    else:
        raise AssertionError("oversized request was accepted")


def test_provider_limit_requires_authoritative_context_window():
    with pytest.raises(ProviderContextWindowUnavailable) as captured:
        enforce_provider_request_limit(
            {"messages": [{"role": "user", "content": "measured refusal"}]},
            context_window_tokens=0,
            reserved_output_tokens=20,
            token_counter=lambda _payload: 7,
        )
    assert captured.value.to_dict() == {
        "request_tokens": 7,
        "request_bytes": 59,
        "context_window_tokens": 0,
        "reserved_output_tokens": 20,
        "input_budget_tokens": 0,
        "metadata_source": "",
    }


def test_provider_limit_admits_against_dynamic_token_budget():
    admission = enforce_provider_request_limit(
        {"messages": [{"role": "user", "content": "ok"}]},
        context_window_tokens=1_000_000,
        reserved_output_tokens=16_384,
        metadata_source="provider:/models",
        token_counter=lambda _payload: 750_000,
    )
    assert admission.request_tokens == 750_000
    assert admission.input_budget_tokens == 983_616
    assert admission.request_bytes > 0


def test_provider_size_does_not_stringify_unsupported_wire_values():
    with pytest.raises(ValueError, match="not JSON serializable"):
        provider_request_bytes({"messages": [], "opaque": object()})
