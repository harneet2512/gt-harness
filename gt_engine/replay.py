"""Exact per-iteration reconstruction from the content-safe attribution trace."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

_USEFUL_OUTCOMES = {
    "success",
    "useful_red",
    "expected_negative_probe",
    "product_failure",
}


def build_iteration_replay(
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Join provider, response, and tool receipts without reading prose.

    Tool outcomes belong to the response after the last provider request and
    before the next provider request. ``action_index`` gives that boundary
    exactly, including parallel tool batches.
    """
    requests: dict[int, list[dict[str, Any]]] = defaultdict(list)
    responses: dict[int, list[dict[str, Any]]] = defaultdict(list)
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        event_type = str(row.get("event_type") or "")
        iteration = int(payload.get("iteration") or 0)
        if event_type == "provider.request" and iteration > 0:
            requests[iteration].append(row)
        elif event_type == "model.response" and iteration > 0:
            responses[iteration].append(row)
        elif event_type == "tool.outcome_classified":
            outcomes.append(row)

    all_iterations = sorted(set(requests) | set(responses))
    issues: list[str] = []
    report_rows: list[dict[str, Any]] = []
    cumulative_input = 0
    cumulative_output = 0
    cumulative_cache = 0
    for position, iteration in enumerate(all_iterations):
        request_rows = requests.get(iteration, [])
        response_rows = responses.get(iteration, [])
        if not request_rows:
            issues.append(f"iteration {iteration}: missing provider.request receipt")
        elif len(request_rows) > 1:
            issues.append(
                f"iteration {iteration}: duplicate provider.request receipts"
            )
        if not response_rows:
            issues.append(f"iteration {iteration}: missing model.response receipt")
        elif len(response_rows) > 1:
            issues.append(
                f"iteration {iteration}: duplicate model.response receipts"
            )

        request = request_rows[0] if request_rows else {}
        response = response_rows[0] if response_rows else {}
        req = request.get("payload") or {}
        res = response.get("payload") or {}
        start_action = int(response.get("action_index") or 0)
        if position + 1 < len(all_iterations):
            next_rows = requests.get(all_iterations[position + 1], [])
            end_action = (
                int(next_rows[0].get("action_index") or start_action)
                if next_rows else start_action
            )
        else:
            end_action = max(
                [start_action]
                + [int(row.get("action_index") or 0) for row in outcomes]
            )
        iteration_outcomes = [
            row.get("payload") or {}
            for row in outcomes
            if start_action < int(row.get("action_index") or 0) <= end_action
        ]
        input_tokens = int(res.get("input_tokens") or 0)
        output_tokens = int(res.get("output_tokens") or 0)
        cache_tokens = int(res.get("cache_read_tokens") or 0)
        cumulative_input += input_tokens
        cumulative_output += output_tokens
        cumulative_cache += cache_tokens
        classifications = [
            str(item.get("classification") or "") for item in iteration_outcomes
        ]
        report_rows.append({
            "iteration": iteration,
            "request_payload_chars": int(req.get("payload_chars") or 0),
            "active_message_chars": int(req.get("active_message_chars") or 0),
            "raw_message_chars": int(req.get("raw_message_chars") or 0),
            "message_count": int(req.get("message_count") or 0),
            "delivery_ids": list(req.get("delivery_ids") or ()),
            "checkpoint_sha256": str(req.get("checkpoint_sha256") or ""),
            "compacted": bool(req.get("compacted")),
            "omitted_message_count": int(
                req.get("omitted_message_count") or 0
            ),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_tokens,
            "cumulative_input_tokens": cumulative_input,
            "cumulative_output_tokens": cumulative_output,
            "cumulative_cache_read_tokens": cumulative_cache,
            "tool_outcomes": classifications,
            "useful_observation_count": sum(
                item in _USEFUL_OUTCOMES for item in classifications
            ),
            "harmful_observation_count": sum(
                bool(item.get("harmful")) for item in iteration_outcomes
            ),
        })
    return {
        "version": "gt.iteration_replay.v1",
        "iteration_count": len(report_rows),
        "accounted_input_tokens": cumulative_input,
        "accounted_output_tokens": cumulative_output,
        "accounted_cache_read_tokens": cumulative_cache,
        "iterations": report_rows,
        "issues": issues,
    }

