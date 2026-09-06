"""Tests for scripts/gt_audit.py - the Tier-2 GT-conduct auditor.

Fixture tests run against a REAL crashed-run task dir copied from GHA run
tb2-gt-30496848157 (tests/fixtures/gt_audit/crashed_run/). Synthetic tests
build transcripts in the exact nano-CLI panel shapes (rich Panel tee'd
without ANSI) so healthy-run behaviors - dormancy, dose counting, leak
detection, pairing - are pinned before a healthy artifact exists.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gt_engine.event_journal import GENESIS_HASH, event_hash
from scripts import gt_audit

REPO = Path(__file__).resolve().parent.parent
SCRIPT = Path(gt_audit.__file__)
CRASHED_RUN = Path(__file__).resolve().parent / "fixtures" / "gt_audit" / "crashed_run"
SMOKE_RUN = Path(__file__).resolve().parent / "fixtures" / "gt_audit" / "smoke_run"

# --------------------------------------------------------------------------- #
# synthetic transcript builders (nano CLI rich-panel shape)
# --------------------------------------------------------------------------- #
def panel(title: str, text: str, width: int = 78) -> str:
    inner = width - 4  # "| " + " |"
    lines: list[str] = []
    for raw in text.splitlines() or [""]:
        while True:
            lines.append(raw[:inner])
            raw = raw[inner:]
            if not raw:
                break
    pad = width - 4 - len(title)
    top = "╭" + "─" * (pad // 2) + f" {title} " + "─" * (pad - pad // 2) + "╮"
    body = [f"│ {ln.ljust(inner)} │" for ln in lines]
    bottom = "╰" + "─" * (width - 2) + "╯"
    return "\n".join([top, *body, bottom])


def stop_line(reason="end_turn", iters=3, in_t=1000, out_t=200, cache=0) -> str:
    return (f"stop: {reason}  iterations={iters}  in={in_t}  out={out_t}  "
            f"cache_read={cache}")


def make_task_dir(root: Path, trial: str, task_name: str, transcript: str,
                  reward: float = 0.0) -> Path:
    d = root / trial
    (d / "agent").mkdir(parents=True)
    (d / "agent" / "nano.txt").write_text(transcript, encoding="utf-8")
    (d / "result.json").write_text(json.dumps({
        "task_name": task_name,
        "verifier_result": {"rewards": {"reward": reward}},
        "exception_info": None,
    }), encoding="utf-8")
    return d


def make_native_miniswe_task(
    root: Path,
    *,
    exception_info: dict | None = None,
    tamper_request: bool = False,
    delivery_text: str | None = None,
    expose_delivery: bool = True,
) -> Path:
    """Write the canonical Mini-SWE artifact shape (no legacy nano.txt)."""
    task = root / "native-task__trial"
    agent = task / "agent"
    state = agent / "gt-state" / "native-task"
    requests = state / "provider_requests"
    responses = state / "provider_responses"
    requests.mkdir(parents=True)
    responses.mkdir(parents=True)

    request_content = "solve it"
    if delivery_text is not None and expose_delivery:
        request_content += "\n" + delivery_text
    request_bytes = json.dumps(
        {"messages": [{"role": "user", "content": request_content}]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    request_sha = hashlib.sha256(request_bytes).hexdigest()
    (requests / f"{request_sha}.json").write_bytes(
        request_bytes + (b"tampered" if tamper_request else b"")
    )
    response_bytes = b'{"choices":[]}'
    response_sha = hashlib.sha256(response_bytes).hexdigest()
    (responses / f"{response_sha}.json").write_bytes(response_bytes)

    delivery_identity = (
        hashlib.sha256(delivery_text.encode()).hexdigest()
        if delivery_text is not None else ""
    )
    rows = []
    if delivery_text is not None:
        deliveries = state / "deliveries"
        deliveries.mkdir()
        (deliveries / f"{delivery_identity}.json").write_text(
            delivery_text, encoding="utf-8"
        )
        rows.append({
            "event": "evidence_delivery",
            "iteration": 0,
            "evidence_type": "cochange_partner",
            "delivery_identity": delivery_identity,
            "payload_sha256": delivery_identity,
            "delivery_blob": f"deliveries/{delivery_identity}.json",
            "rendered_bytes": len(delivery_text.encode()),
        })
    rows.extend([
        {
            "event": "provider_delivery",
            "iteration": 1,
            "request_id": "req-1",
            "payload_sha256": request_sha,
            "model_visible_sha256": hashlib.sha256(json.dumps(
                [{"content": request_content, "role": "user"}],
                sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
            "request_blob": f"provider_requests/{request_sha}.json",
            "delivery_ids": [delivery_identity] if delivery_text is not None else [],
            "matches": ([{"delivery_id": delivery_identity,
                           "rendered_sha256": delivery_identity}]
                        if delivery_text is not None else []),
            "unmatched_delivery_ids": [],
        },
        {
            "event": "provider_response",
            "iteration": 1,
            "request_id": "req-1",
            "response_sha256": response_sha,
            "response_blob": f"provider_responses/{response_sha}.json",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
            "delivery_ids": [delivery_identity] if delivery_text is not None else [],
        },
    ])
    parent = GENESIS_HASH
    encoded_rows = []
    for sequence, payload in enumerate(rows, 1):
        row = {
            "schema": "gt.event.v1",
            "sequence": sequence,
            "parent_hash": parent,
            "timestamp_utc": "2026-09-04T00:00:00+00:00",
            **payload,
        }
        row["event_hash"] = event_hash(row)
        parent = row["event_hash"]
        encoded_rows.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    (state / "events.jsonl").write_text("\n".join(encoded_rows) + "\n")

    trajectory = {
        "trajectory_format": "mini-swe-agent-1.1",
        "exit_status": "submitted",
        "submission": "done",
        "info": {"model_stats": {"api_calls": 1}},
        "messages": [
            {"role": "system", "content": "system"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "bash", "arguments": "{}"}}],
                "extra": {"response": {"usage": {"prompt_tokens": 10,
                                                     "completion_tokens": 3}}},
            },
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>ok</output>",
             "extra": {"returncode": 0}},
        ],
    }
    (agent / "miniswe_trajectory.json").write_text(json.dumps(trajectory))
    (task / "result.json").write_text(json.dumps({
        "task_name": "native-task",
        "verifier_result": {"rewards": {"reward": 1}},
        "exception_info": exception_info,
    }))
    return task


def rewrite_native_events(task: Path, mutate) -> list[dict]:
    events_path = next((task / "agent" / "gt-state").glob("*/events.jsonl"))
    rows = [json.loads(line) for line in events_path.read_text().splitlines()]
    mutate(rows)
    parent = GENESIS_HASH
    for row in rows:
        row["parent_hash"] = parent
        row["event_hash"] = event_hash(row)
        parent = row["event_hash"]
    events_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":"))
                  for row in rows) + "\n"
    )
    return rows


def test_native_miniswe_audit_uses_real_artifacts_not_nano(tmp_path):
    task = make_native_miniswe_task(tmp_path)

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "GREEN-quiet"
    assert audit.stop_reason == "submitted"
    assert audit.iterations == 1
    assert audit.in_tokens == 10
    assert audit.out_tokens == 3
    assert audit.cache_read == 2
    assert audit.tool_results == 1
    assert set(audit.feature_attribution) == set(
        gt_audit.summarize_features({})
    )
    assert "missing agent/nano.txt" not in " ".join(audit.verdict_reasons)


def test_native_miniswe_246_terminal_is_read_from_info(tmp_path):
    task = make_native_miniswe_task(tmp_path)
    path = task / "agent" / "miniswe_trajectory.json"
    trajectory = json.loads(path.read_text())
    trajectory["info"]["exit_status"] = trajectory.pop("exit_status")
    path.write_text(json.dumps(trajectory))
    audit = gt_audit.audit_task(task)
    assert audit.verdict == "GREEN-quiet"
    assert audit.stop_reason == "submitted"


def test_native_miniswe_audit_reports_timeout_cause_not_missing_nano(tmp_path):
    task = make_native_miniswe_task(
        tmp_path,
        exception_info={
            "exception_type": "AgentTimeoutError",
            "exception_message": "Agent execution timed out after 1800 seconds",
        },
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert audit.stop_reason == "external_timeout"
    reasons = " ".join(audit.verdict_reasons)
    assert "AgentTimeoutError" in reasons
    assert "missing agent/nano.txt" not in reasons


def test_native_miniswe_audit_rejects_tampered_provider_blob(tmp_path):
    task = make_native_miniswe_task(tmp_path, tamper_request=True)

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert any("provider request blob hash mismatch" in reason
               for reason in audit.verdict_reasons)


def test_native_delivery_requires_exact_bytes_in_immediate_request(tmp_path):
    task = make_native_miniswe_task(
        tmp_path, delivery_text="inspect sibling.py", expose_delivery=False
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert audit.feature_attribution["cochange_prior"]["status"] == (
        "DELIVERED_UNEXPOSED"
    )
    assert any("delivery bytes absent" in issue for issue in audit.attribution_issues)


def test_native_delivery_exact_bytes_are_independently_witnessed(tmp_path):
    task = make_native_miniswe_task(
        tmp_path, delivery_text="inspect sibling.py", expose_delivery=True
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "GREEN-delivered"
    assert audit.feature_attribution["cochange_prior"]["status"] == "WITNESSED"


def test_native_delivery_must_be_on_the_immediate_provider_boundary(tmp_path):
    task = make_native_miniswe_task(tmp_path, delivery_text="inspect sibling.py")
    rewrite_native_events(
        task,
        lambda rows: [row.update(iteration=2) for row in rows
                      if row["event"].startswith("provider_")],
    )
    trajectory_path = task / "agent" / "miniswe_trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    trajectory["info"]["model_stats"]["api_calls"] = 1
    trajectory_path.write_text(json.dumps(trajectory))

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert any("not joined to its immediate boundary" in issue
               for issue in audit.attribution_issues)


def test_native_malformed_delivery_ids_fail_closed_without_crashing(tmp_path):
    task = make_native_miniswe_task(tmp_path, delivery_text="inspect sibling.py")
    rewrite_native_events(
        task,
        lambda rows: next(row for row in rows
                          if row["event"] == "provider_delivery").update(
                              delivery_ids=1
                          ),
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert any("delivery_ids is not a list" in issue
               for issue in audit.attribution_issues)


@pytest.mark.parametrize(
    "exit_status",
    ["TimeExceeded", "budget_exhausted", "internal_error", "timeout", "unknown"],
)
def test_native_failure_terminal_is_red_without_outer_exception(tmp_path, exit_status):
    task = make_native_miniswe_task(tmp_path)
    path = task / "agent" / "miniswe_trajectory.json"
    trajectory = json.loads(path.read_text())
    trajectory["exit_status"] = exit_status
    path.write_text(json.dumps(trajectory))

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert f"Mini-SWE failure terminal: {exit_status}" in audit.verdict_reasons


def test_native_non_string_terminal_is_red(tmp_path):
    task = make_native_miniswe_task(tmp_path)
    path = task / "agent" / "miniswe_trajectory.json"
    trajectory = json.loads(path.read_text())
    trajectory["exit_status"] = 17
    path.write_text(json.dumps(trajectory))

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert any("exit_status is not a string" in issue
               for issue in audit.attribution_issues)


def test_native_response_blob_must_be_a_json_object(tmp_path):
    task = make_native_miniswe_task(tmp_path)
    state = next((task / "agent" / "gt-state").glob("*/provider_responses/*.json"))
    malformed = b"not JSON"
    digest = hashlib.sha256(malformed).hexdigest()
    replacement = state.with_name(f"{digest}.json")
    replacement.write_bytes(malformed)
    rewrite_native_events(
        task,
        lambda rows: next(row for row in rows
                          if row["event"] == "provider_response").update(
                              response_sha256=digest,
                              response_blob=f"provider_responses/{digest}.json",
                          ),
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert any("unreadable JSON" in issue for issue in audit.attribution_issues)


@pytest.mark.parametrize("cached_tokens", [True, -1, 11])
def test_native_cached_usage_must_be_bounded_integer(tmp_path, cached_tokens):
    task = make_native_miniswe_task(tmp_path)
    rewrite_native_events(
        task,
        lambda rows: next(row for row in rows
                          if row["event"] == "provider_response")["usage"]
        ["prompt_tokens_details"].update(cached_tokens=cached_tokens),
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert any("cached_tokens is invalid" in issue
               for issue in audit.attribution_issues)


def test_native_missing_usage_is_unknown_and_red(tmp_path):
    task = make_native_miniswe_task(tmp_path)
    rewrite_native_events(
        task,
        lambda rows: next(row for row in rows
                          if row["event"] == "provider_response").pop("usage"),
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert audit.in_tokens is None and audit.out_tokens is None
    assert any("usage is not an object" in issue for issue in audit.attribution_issues)


def test_native_feature_projection_requires_request_and_response_identity_join():
    identity = "a" * 64
    delivery = {
        "event": "evidence_delivery",
        "evidence_type": "cochange_partner",
        "delivery_identity": identity,
    }
    unjoined = gt_audit._native_feature_projection([delivery])
    assert unjoined["cochange_prior"]["status"] == "DELIVERED_UNEXPOSED"

    joined = gt_audit._native_feature_projection([
        delivery,
        {"event": "provider_delivery", "iteration": 2,
         "delivery_ids": [identity],
         "matches": [{"delivery_id": identity, "rendered_sha256": identity}]},
        {"event": "provider_response", "iteration": 2,
         "delivery_ids": [identity]},
    ])
    assert joined["cochange_prior"]["status"] == "WITNESSED"
    assert joined["cochange_prior"]["exposed"] is True
    assert joined["cochange_prior"]["response_observed"] is True


def test_native_fact_delivery_does_not_invent_capability_execution():
    identity = "b" * 64
    projected = gt_audit._native_feature_projection([
        {"event": "evidence_delivery", "evidence_type": "submit_refusal",
         "delivery_identity": identity},
        {"event": "provider_delivery", "iteration": 1,
         "delivery_ids": [identity],
         "matches": [{"delivery_id": identity, "rendered_sha256": identity}]},
        {"event": "provider_response", "iteration": 1,
         "delivery_ids": [identity]},
    ])

    assert projected["submit_refusal"]["status"] == "WITNESSED"
    assert projected["GT_CERT_DELIVERY"]["status"] == "INELIGIBLE"
    assert projected["GT_SS_SUBMIT_RED"]["status"] == "INELIGIBLE"


def test_native_miniswe_audit_rejects_malformed_nested_schemas(tmp_path):
    task = make_native_miniswe_task(tmp_path)
    trajectory_path = task / "agent" / "miniswe_trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    trajectory["info"] = None
    trajectory_path.write_text(json.dumps(trajectory))
    state = next((task / "agent" / "gt-state").glob("*/provider_requests/*.json"))
    malformed = b"[]"
    old_digest = state.stem
    new_digest = hashlib.sha256(malformed).hexdigest()
    new_state = state.with_name(f"{new_digest}.json")
    new_state.write_bytes(malformed)
    events_path = next((task / "agent" / "gt-state").glob("*/events.jsonl"))
    rows = [json.loads(line) for line in events_path.read_text().splitlines()]
    rows[0]["payload_sha256"] = new_digest
    rows[0]["request_blob"] = rows[0]["request_blob"].replace(old_digest, new_digest)
    parent = GENESIS_HASH
    for row in rows:
        row["parent_hash"] = parent
        row["event_hash"] = event_hash(row)
        parent = row["event_hash"]
    events_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":"))
                  for row in rows) + "\n"
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert any("trajectory info" in issue for issue in audit.attribution_issues)
    assert any("provider request JSON object" in issue
               for issue in audit.attribution_issues)


HEALTHY_NONCODE = "\n".join([
    panel("assistant", "Let me check the date."),
    panel("tool_call", "bash(command='date')"),
    "iter=1 in=500 out=50",
    panel("tool_result", "Tue Jul 29 2026"),
    panel("assistant", "Done."),
    "iter=2 in=600 out=60",
    stop_line(iters=2, in_t=1100, out_t=110),
    panel("final", "The date is printed."),
    "",
])


# --------------------------------------------------------------------------- #
# 1. crash detection against the REAL artifact
# --------------------------------------------------------------------------- #
def test_crashed_run_is_red():
    audits = gt_audit.audit_run(CRASHED_RUN)
    assert len(audits) == 1
    a = audits[0]
    assert a.task_name == "break-filter-js-from-html"
    assert a.verdict == "RED"
    assert a.stop_reason == "error"
    assert a.iterations == 1
    assert a.in_tokens == 0 and a.out_tokens == 0
    assert a.agent_error and "UnicodeEncodeError" in a.agent_error
    assert a.reward == 0.0
    assert a.gt_deliveries == 0
    # the real crashed transcript must parse fully - nothing unexplained
    assert a.unparsed_lines == 0
    assert not a.unparsed_structures


def test_audit_counts_task_agent_harness_path_attempts(tmp_path):
    task = make_task_dir(
        tmp_path,
        "code-task__isolation",
        "code-task",
        "\n".join([
            panel("assistant", "I will inspect the implementation."),
            panel(
                "tool_call",
                "bash(command='find /installed-agent/nano-harness -type f')",
            ),
            panel("tool_result", "find: no such file or directory"),
            stop_line(iters=1, in_t=10, out_t=2),
            "",
        ]),
    )

    audit = gt_audit.audit_task(task)

    assert audit.forbidden_harness_path_attempt_count == 1
    assert "/installed-agent/nano-harness" in (
        audit.forbidden_harness_path_samples[0]
    )


def test_audit_distinguishes_rejected_harness_access_from_executed_access(
        tmp_path):
    from gt_engine.attribution import AttributionTrace

    task = make_task_dir(
        tmp_path,
        "code-task__blocked-isolation",
        "code-task",
        "\n".join([
            panel("tool_call", "bash(command='ls -la .gt && find .gt -type f')"),
            panel(
                "tool_result",
                "ERROR: GroundTruth and harness state is outside the task "
                "filesystem contract; this access was not executed.",
            ),
            stop_line(iters=1, in_t=10, out_t=2),
            "",
        ]),
    )
    trace = AttributionTrace(
        lambda: task / "agent" / "gt_attribution.jsonl",
        trace_id="f" * 32,
    )
    trace.record(
        "tool.control_decision",
        action_index=0,
        boundary="pre_dispatch",
        payload={
            "decision": "REJECTED",
            "reason_code": "harness_isolation",
            "tool_name": "bash",
        },
    )

    audit = gt_audit.audit_task(task)

    assert audit.harness_access_rejected_count == 1
    assert audit.tool_control_rejected_count == 1
    assert audit.forbidden_harness_path_attempt_count == 0


def test_audit_does_not_count_explicit_gt_exclusions_as_access(tmp_path):
    task = make_task_dir(
        tmp_path,
        "code-task__isolation-exclusion",
        "code-task",
        "\n".join([
            panel(
                "tool_call",
                "bash(command=\"find . -not -path './.gt/*' "
                "--exclude-dir='.gt'\")",
            ),
            panel(
                "tool_call",
                "bash(command='find . -path ./.git -prune -o "
                "-path ./.gt -prune -o -type f -print')",
            ),
            panel(
                "tool_call",
                "bash(command=\"echo 'scan excluding .git and .gt' && "
                "grep -r token --exclude-dir=.git --exclude-dir=.gt .\")",
            ),
            stop_line(iters=1, in_t=10, out_t=2),
            "",
        ]),
    )

    audit = gt_audit.audit_task(task)

    assert audit.forbidden_harness_path_attempt_count == 0


def test_attribution_trace_is_loaded_and_projects_all_19_features(tmp_path):
    from gt_engine.attribution import AttributionTrace

    task = make_task_dir(
        tmp_path,
        "code-task__trace",
        "code-task",
        "\n".join([
            panel("tool_call", "bash(command='pytest')"),
            "iter=1 in=10 out=2",
            panel("tool_result", "ok"),
            stop_line(iters=1, in_t=10, out_t=2),
            "",
        ]),
    )
    trace = AttributionTrace(lambda: task / "agent" / "gt_attribution.jsonl",
                             trace_id="d" * 32)
    trace.record(
        "feature.evaluated",
        action_index=1,
        boundary="recovery",
        payload={
            "feature_id": "recovery",
            "eligible": True,
            "outcome": "producer_abstained",
        },
    )

    audit = gt_audit.audit_task(task)

    assert audit.attribution_present is True
    assert audit.attribution_issues == []
    assert len(audit.feature_attribution) == 19
    assert audit.feature_attribution["recovery"]["status"] == "TRIGGERED_DARK"


def test_audit_reports_provider_and_profile_activation_receipts(tmp_path):
    from gt_engine.attribution import AttributionTrace

    task = make_task_dir(
        tmp_path,
        "code-task__temperature",
        "code-task",
        "\n".join([stop_line(iters=1, in_t=10, out_t=2), ""]),
    )
    trace = AttributionTrace(
        lambda: task / "agent" / "gt_attribution.jsonl",
        trace_id="a" * 32,
    )
    trace.record(
        "run.started",
        action_index=0,
        boundary="task_start",
        payload={
            "expected_profile_controls": ["GT_GATEWAY", "GT_CS_EDIT_TRIGGER"],
            "active_profile_controls": ["GT_GATEWAY", "GT_CS_EDIT_TRIGGER"],
            "missing_profile_controls": [],
            "active_behavior_flags": ["GT_CS_EDIT_TRIGGER"],
            "profile_receipt_fault": "",
        },
    )
    trace.record(
        "provider.request",
        action_index=0,
        boundary="provider",
        payload={
            "iteration": 1,
            "provider": "openai.chat.completions",
            "model": "deepseek-v4-flash",
            "temperature": 1.0,
            "delivery_ids": [],
            "matches": [],
        },
    )

    audit = gt_audit.audit_task(task)

    assert audit.attribution_issues == []
    assert audit.provider_temperatures == [1.0]
    assert audit.expected_profile_controls == [
        "GT_CS_EDIT_TRIGGER", "GT_GATEWAY",
    ]
    assert audit.active_profile_controls == [
        "GT_CS_EDIT_TRIGGER", "GT_GATEWAY",
    ]
    assert audit.missing_profile_controls == []
    assert audit.profile_behavior_flags == ["GT_CS_EDIT_TRIGGER"]
    assert audit.profile_receipt_fault == ""


def test_audit_projects_contract_graph_router_and_verification_receipts(
    tmp_path,
):
    from gt_engine.attribution import AttributionTrace

    task = make_task_dir(
        tmp_path,
        "code-task__graph-receipts",
        "code-task",
        "\n".join([stop_line(iters=1, in_t=10, out_t=2), ""]),
    )
    trace = AttributionTrace(
        lambda: task / "agent" / "gt_attribution.jsonl",
        trace_id="b" * 32,
    )
    trace.record(
        "graph.surface_receipt",
        action_index=0,
        boundary="task_start",
        payload={
            "available": True,
            "task_role": "code_behavior",
            "obligation_count": 4,
            "shipped_obligation_count": 4,
            "surface_counts": {"nodes": 9, "edges": 7},
        },
    )
    trace.record(
        "graph.task_projection",
        action_index=0,
        boundary="task_start",
        payload={
            "file_count": 2,
            "symbol_count": 3,
            "node_count": 4,
            "surface_hits": {"nodes_fts": 2, "closure": 1},
            "revision": "graph-r1",
            "router_revision": "graph-r1",
            "semantic_fact_count": 8,
        },
    )
    trace.record(
        "role_pack.selected",
        action_index=0,
        boundary="task_start",
        payload={"pack_id": "code-build", "version": "1"},
    )
    for predicate_id in ("pred-1", "pred-2", "pred-3", "pred-4"):
        trace.record(
            "contract.predicate_compiled",
            action_index=0,
            boundary="task_start",
            payload={"predicate_id": predicate_id, "kind": "behavior"},
        )
    trace.record(
        "contract.predicate_observed",
        action_index=3,
        boundary="test",
        payload={
            "predicate_id": "pred-1",
            "kind": "behavior",
            "outcome": "pass",
            "action_index": 3,
            "latest_edit_action": 2,
            "command_sha256": "c" * 64,
            "output_sha256": "d" * 64,
        },
    )
    trace.record(
        "graph.evidence_need",
        action_index=0,
        boundary="task_start",
        payload={"revision": "graph-r1", "ranked_count": 1},
    )
    trace.record(
        "graph.evidence_ranked",
        action_index=0,
        boundary="task_start",
        payload={
            "revision": "graph-r1",
            "obligation_ids": ["obl-1"],
            "active_target_linked": False,
        },
    )
    trace.record(
        "graph.context_refreshed",
        action_index=2,
        boundary="post_edit",
        payload={"revision": "graph-r2"},
    )
    trace.record(
        "provider.request",
        action_index=1,
        boundary="provider",
        payload={
            "matches": [{"delivery_id": "delivery-1"}],
            "temperature": 1.0,
        },
    )
    trace.record(
        "capsule.expired",
        action_index=2,
        boundary="provider",
        payload={"delivery_id": "delivery-1"},
    )
    trace.record(
        "utility.scored",
        action_index=2,
        boundary="gateway",
        payload={"selected": True},
    )
    trace.record(
        "progress.transition",
        action_index=2,
        boundary="tool_result",
        payload={"current": "STALLED"},
    )
    trace.record(
        "tool.outcome_classified",
        action_index=2,
        boundary="tool_result",
        payload={
            "classification": "useful_red",
            "harmful": False,
            "information_gain": True,
            "new_delivery_ids": ["delivery-1"],
        },
    )
    trace.record(
        "tool.outcome_classified",
        action_index=3,
        boundary="tool_result",
        payload={
            "tool_name": "bash",
            "classification": "shell_lifecycle",
            "harmful": True,
        },
    )
    trace.record(
        "tool.outcome_classified",
        action_index=4,
        boundary="tool_result",
        payload={
            "tool_name": "bash",
            "classification": "success",
            "harmful": False,
        },
    )
    trace.record(
        "control.decision",
        action_index=1,
        boundary="gateway",
        payload={
            "feature_id": "GT_ROLE_DRIVEN_COALITION",
            "decision": "SUPPRESSED",
            "reason": "role_irrelevant",
        },
    )
    trace.record(
        "control.decision",
        action_index=2,
        boundary="gateway",
        payload={
            "feature_id": "GT_VERIFICATION_PLAN",
            "decision": "APPLIED",
        },
    )
    trace.record(
        "lifecycle.checkpoint",
        action_index=3,
        boundary="verify",
        payload={
            "phase": "verify",
            "outcome": "requirements_verified",
            "obligation_total": 4,
            "obligation_met": 4,
        },
    )

    audit = gt_audit.audit_task(task)

    assert audit.task_role == "code_behavior"
    assert audit.obligation_count == 4
    assert audit.shipped_obligation_count == 4
    assert audit.graph_surface_counts == {"edges": 7, "nodes": 9}
    assert audit.graph_projection_file_count == 2
    assert audit.graph_projection_surface_hits == {
        "closure": 1, "nodes_fts": 2,
    }
    assert audit.evidence_router_suppressed == 1
    assert audit.evidence_router_reasons == {"role_irrelevant": 1}
    assert audit.verification_plan_applied is True
    assert audit.verify_obligation_total == 4
    assert audit.verify_obligation_met == 4
    assert audit.role_pack_id == "code-build"
    assert audit.predicate_compiled_count == 4
    assert audit.predicate_observed_kinds == {"behavior": 1}
    assert audit.predicate_invalid_receipt_count == 0
    assert audit.graph_evidence_need_count == 1
    assert audit.graph_evidence_ranked_count == 1
    assert audit.graph_evidence_unlinked_count == 0
    assert audit.graph_evidence_revision_mismatch_count == 0
    assert audit.graph_projection_revision == "graph-r1"
    assert audit.graph_router_revision == "graph-r1"
    assert audit.graph_semantic_fact_count == 8
    assert audit.graph_refresh_count == 1
    assert audit.capsule_expired_count == 1
    assert audit.capsule_unique_exposed_count == 1
    assert audit.capsule_repeated_exposure_count == 0
    assert audit.utility_selected_count == 1
    assert audit.progress_states == {"STALLED": 1}
    assert audit.tool_outcome_counts == {
        "shell_lifecycle": 1,
        "success": 1,
        "useful_red": 1,
    }
    assert audit.tool_outcome_new_capsule_count == 1
    assert audit.shell_lifecycle_recovered_count == 1
    assert audit.shell_lifecycle_unrecovered_count == 0


def test_audit_censuses_typed_progress_controls(tmp_path):
    from gt_engine.attribution import AttributionTrace

    task_dir = make_task_dir(
        tmp_path,
        "code-task__progress-controls",
        "code-task",
        "\n".join([stop_line(iters=80, in_t=10, out_t=2), ""]),
    )
    trace = AttributionTrace(
        lambda: task_dir / "agent" / "gt_attribution.jsonl",
        trace_id="e" * 32,
    )
    for action_index, (mode, iteration) in enumerate((
        ("artifact_completion", 50),
        ("verified_completion", 62),
        ("finalization", 80),
    ), 1):
        trace.record(
            "progress.control_issued",
            action_index=action_index,
            boundary="provider",
            payload={
                "mode": mode,
                "iteration": iteration,
                "iteration_limit": 100,
            },
        )

    audit = gt_audit.audit_task(task_dir)

    assert audit.progress_control_count == 3
    assert audit.progress_control_modes == {
        "artifact_completion": 1,
        "finalization": 1,
        "verified_completion": 1,
    }
    assert audit.progress_control_iterations["finalization"] == [80]


def test_attribution_integrity_failure_is_red(tmp_path):
    from gt_engine.attribution import AttributionTrace

    task = make_task_dir(
        tmp_path,
        "code-task__badtrace",
        "code-task",
        "\n".join([stop_line(iters=1, in_t=10, out_t=2), ""]),
    )
    path = task / "agent" / "gt_attribution.jsonl"
    trace = AttributionTrace(lambda: path, trace_id="e" * 32)
    trace.record(
        "decision.committed",
        action_index=1,
        boundary="gateway",
        payload={"decision": "no_delivery", "reason": "no_candidate"},
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    row["payload"]["reason"] = "tampered"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert audit.attribution_issues == ["row 1: row_hash mismatch"]


def test_bridge_fault_event_is_not_silently_green(tmp_path):
    from gt_engine.attribution import AttributionTrace

    task = make_task_dir(
        tmp_path,
        "code-task__faulttrace",
        "code-task",
        "\n".join([stop_line(iters=1, in_t=10, out_t=2), ""]),
    )
    trace = AttributionTrace(
        lambda: task / "agent" / "gt_attribution.jsonl",
        trace_id="f" * 32,
    )
    trace.record(
        "decision.committed",
        action_index=1,
        boundary="gateway",
        payload={
            "decision": "telemetry_fault",
            "reason": "bridge_exception",
            "fault_type": "RuntimeError",
        },
    )

    audit = gt_audit.audit_task(task)

    assert audit.verdict == "RED"
    assert audit.attribution_issues == [
        "trace event 1: bridge_exception (RuntimeError)"
    ]


def test_cli_end_to_end_on_crashed_fixture(tmp_path):
    out_json = tmp_path / "audit.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(CRASHED_RUN), "--json", str(out_json)],
        capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 1  # RED present
    assert "RED" in proc.stdout
    assert "agent error at iteration 1" in proc.stdout
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["tasks"][0]["verdict"] == "RED"


# --------------------------------------------------------------------------- #
# 2. dormancy classification (healthy non-code run, zero GT activity)
# --------------------------------------------------------------------------- #
def test_dormant_noncode_task_is_green_dormant(tmp_path):
    make_task_dir(tmp_path, "datecheck__abc", "datecheck", HEALTHY_NONCODE, reward=1.0)
    audits = gt_audit.audit_run(tmp_path)
    a = audits[0]
    assert a.verdict == "GREEN-dormant"
    assert a.gt_deliveries == 0
    assert a.code_task is False
    assert a.stop_reason == "end_turn"
    assert a.iterations == 2
    assert a.unparsed_lines == 0


def test_quiet_code_task_is_green_quiet(tmp_path):
    transcript = "\n".join([
        panel("assistant", "Editing."),
        panel("tool_call", "edit_file(path='src/main.py', old='a', new='b')"),
        panel("tool_result", "edited src/main.py"),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "fix__x", "fix", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.verdict == "GREEN-quiet"
    assert a.code_task is True
    assert a.gt_deliveries == 0


# --------------------------------------------------------------------------- #
# 3. dose counting on a synthetic multi-dose observation
# --------------------------------------------------------------------------- #
def test_single_dose_is_green_and_counted(tmp_path):
    obs = ("$ apply_patch src/api.py\n"
           "src/api.py: error: handler() signature changed; 3 caller(s) in 2 "
           "file(s) must update the call sites")
    transcript = "\n".join([
        panel("tool_call", "edit_file(path='src/api.py', old='x', new='y')"),
        panel("tool_result", obs, width=120),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.verdict == "GREEN"
    assert a.gt_deliveries == 1
    assert a.gt_delivery_kinds == {"caller_contract": 1}
    assert not a.dose_violations
    assert a.gt_overhead_chars > 0


def test_multi_dose_observation_is_flagged(tmp_path):
    obs = ("ok\n"
           "src/api.py: error: handler() signature changed; 3 caller(s) in 2 "
           "file(s) must update the call sites\n"
           "some interleaved tool output\n"
           "src/config.py: note: your change must also update this file")
    transcript = "\n".join([
        panel("tool_call", "bash(command='sed -i s/a/b/ src/api.py')"),
        panel("tool_result", obs, width=120),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.gt_deliveries == 2
    assert len(a.dose_violations) == 1
    assert "2 GT blocks" in a.dose_violations[0]
    assert a.verdict == "YELLOW"


def test_submit_refusal_block_counts_once(tmp_path):
    obs = ("pre-commit hook failed:\n"
           "syntax_error\n"
           "invalid syntax (api.py, line 10)\n"
           "commit aborted (exit 1)")
    transcript = "\n".join([
        panel("tool_call", "bash(command='python src/api.py')"),
        panel("tool_result", obs, width=100),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.gt_deliveries == 1
    assert a.gt_delivery_kinds == {"submit_refusal": 1}
    assert not a.dose_violations


# --------------------------------------------------------------------------- #
# 4. leak law
# --------------------------------------------------------------------------- #
def test_gt_tag_leak_is_red(tmp_path):
    transcript = "\n".join([
        panel("tool_call", "bash(command='ls')"),
        panel("tool_result", "file1\n<gt-fact>secret evidence</gt-fact>", width=100),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.verdict == "RED"
    # counted per matching line; the opening tag sits on one transcript line
    assert a.leak_tag_count == 1
    assert any("<gt-fact>" in c for c in a.leak_tag_context)


def test_test_identity_in_gt_block_is_review_flag_not_red(tmp_path):
    obs = ("tests/test_api.py: error: handler() signature changed; 1 caller(s) "
           "in 1 file(s) must update the call sites")
    transcript = "\n".join([
        panel("tool_call", "edit_file(path='src/api.py', old='x', new='y')"),
        panel("tool_result", obs, width=120),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.verdict == "YELLOW"  # human review, never a hard RED on heuristic
    assert len(a.review_flags) == 1
    assert "human review" in a.review_flags[0]


# --------------------------------------------------------------------------- #
# 5. error rate + 6. tokens
# --------------------------------------------------------------------------- #
def test_error_rate_and_tokens(tmp_path):
    transcript = "\n".join([
        panel("tool_call", "bash(command='date')"),
        panel("tool_result", "ok"),
        panel("tool_call", "bash(command='nope')"),
        panel("tool_result (error)", "ERROR: nope: command not found [exit code 127]"),
        stop_line(reason="end_turn", iters=4, in_t=4321, out_t=765, cache=99),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.tool_results == 2
    assert a.tool_errors == 1
    assert a.error_rate == 0.5
    assert (a.in_tokens, a.out_tokens, a.cache_read) == (4321, 765, 99)


# --------------------------------------------------------------------------- #
# UNPARSED honesty: unknown shapes are surfaced, never silently skipped
# --------------------------------------------------------------------------- #
def test_unrecognized_lines_are_reported_unparsed(tmp_path):
    transcript = "\n".join([
        "some junk the parser has never seen",
        panel("tool_call", "bash(command='date')"),
        panel("tool_result", "ok"),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.unparsed_lines == 1
    assert "junk" in a.unparsed_samples[0]
    assert a.verdict == "YELLOW"  # cannot be trusted GREEN


def test_unknown_panel_title_is_flagged(tmp_path):
    transcript = "\n".join([
        panel("mystery", "??"),
        panel("tool_result", "ok"),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert any("mystery" in s for s in a.unparsed_structures)
    assert a.verdict == "YELLOW"


def test_missing_transcript_is_red(tmp_path):
    d = make_task_dir(tmp_path, "t__1", "t", "x")
    (d / "agent" / "nano.txt").unlink()
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.verdict == "RED"
    assert "missing agent/nano.txt" in a.verdict_reasons[0]


# --------------------------------------------------------------------------- #
# 7. pairing
# --------------------------------------------------------------------------- #
def test_paired_table_aligns_by_task_name(tmp_path):
    base = tmp_path / "base"
    gt = tmp_path / "gt"
    make_task_dir(base, "alpha__b1", "alpha", HEALTHY_NONCODE, reward=1.0)
    make_task_dir(base, "beta__b2", "beta", HEALTHY_NONCODE, reward=1.0)
    make_task_dir(gt, "alpha__g1", "alpha", HEALTHY_NONCODE, reward=1.0)
    make_task_dir(gt, "beta__g2", "beta", HEALTHY_NONCODE, reward=0.0)  # harm
    out = gt_audit.render_paired(gt_audit.audit_run(gt), gt_audit.audit_run(base))
    lines = out.splitlines()
    alpha = next(ln for ln in lines if ln.startswith("alpha"))
    beta = next(ln for ln in lines if ln.startswith("beta"))
    assert "HARM?" not in alpha
    assert "HARM?" in beta
    assert lines.index(alpha) < lines.index(beta)  # deterministic name order


def test_paired_unpaired_task_is_flagged(tmp_path):
    base = tmp_path / "base"
    gt = tmp_path / "gt"
    make_task_dir(base, "alpha__b1", "alpha", HEALTHY_NONCODE, reward=1.0)
    make_task_dir(gt, "gamma__g1", "gamma", HEALTHY_NONCODE, reward=1.0)
    out = gt_audit.render_paired(gt_audit.audit_run(gt), gt_audit.audit_run(base))
    assert out.count("UNPAIRED") == 2


# --------------------------------------------------------------------------- #
# determinism: two runs over the same tree produce identical bytes
# --------------------------------------------------------------------------- #
def test_report_is_deterministic():
    a1 = gt_audit.render_report(gt_audit.audit_run(CRASHED_RUN), CRASHED_RUN)
    a2 = gt_audit.render_report(gt_audit.audit_run(CRASHED_RUN), CRASHED_RUN)
    assert a1 == a2


def test_nested_run_dir_is_found(tmp_path):
    nested = tmp_path / "wrapper"
    make_task_dir(nested, "t__1", "t", HEALTHY_NONCODE)
    audits = gt_audit.audit_run(tmp_path)  # one level above the task dirs' parent
    assert len(audits) == 1 and audits[0].task_name == "t"


def test_merged_artifact_wrappers_discover_every_task(tmp_path):
    expected = [f"task-{index:02d}" for index in range(1, 21)]
    for task_name in expected:
        wrapper = tmp_path / f"deepswe-run-{task_name}"
        make_task_dir(
            wrapper,
            f"{task_name}__trial",
            task_name,
            HEALTHY_NONCODE,
        )

    audits = gt_audit.audit_run(tmp_path)

    assert [audit.task_name for audit in audits] == expected


def test_empty_run_dir_fails_loud(tmp_path):
    with pytest.raises(SystemExit):
        gt_audit.audit_run(tmp_path)


# --------------------------------------------------------------------------- #
# 8. LEDGER-JOIN - fixtures derived from the REAL smoke artifact
#    (tb2-gt-30501483446 / llm-inference-batching-scheduler: real nano.txt
#    excerpt through tool_result #2 + real trailing stop/final panel, plus the
#    real 3-row gt_ledger.jsonl).
# --------------------------------------------------------------------------- #
# the REAL shipped bytes of ev1 (verified: sha256 == the ledger row hash)
EV1_TEXT = ("task_file/scripts/cost_model.py:28:align\n"
            "task_file/scripts/baseline_packer.py:37:load_requests\n")
EV1_HASH = "16b158360fab054f58554d7762ec60da87765f853d688e5b348ca21c1ea63cbf"


def copy_smoke(tmp_path: Path) -> Path:
    dst = tmp_path / "run"
    shutil.copytree(SMOKE_RUN, dst)
    return dst


def smoke_task_agent(run_dir: Path) -> Path:
    return run_dir / "llm-inference-batching-scheduler__kbuaa8w" / "agent"


def test_smoke_fixture_hash_contract_is_real():
    # pin the hash contract itself: shipped bytes = rows + trailing '\n'
    assert hashlib.sha256(EV1_TEXT.encode()).hexdigest() == EV1_HASH
    ledger = (smoke_task_agent(SMOKE_RUN) / "gt_ledger.jsonl").read_text()
    assert EV1_HASH in ledger


def test_smoke_fixture_reconciles_green_delivered():
    audits = gt_audit.audit_run(SMOKE_RUN)
    assert len(audits) == 1
    a = audits[0]
    assert a.verdict == "GREEN-delivered"
    assert a.ledger_present is True
    assert a.gt_deliveries == 3  # ledger truth, NOT the heuristic (which sees 0)
    assert a.gt_blocks_observed == 0
    assert a.gt_overhead_chars == 537 + 95 + 178
    assert a.gt_delivery_kinds == {
        "caller_contract_view": 1, "localization": 1, "obligations": 1}
    by_ev = {r.event_id: r for r in a.ledger_rows}
    # ev0: task_start capsule -> MODEL-ONLY by construction
    assert by_ev["0"].status == "MODEL-ONLY"
    assert "initial user message" in by_ev["0"].status_reason
    # ev1: the real localization delivery, hash-located in tool_result #1
    assert by_ev["1"].status == "TRANSCRIPT-CONFIRMED"
    assert "tool_result #1" in by_ev["1"].status_reason
    assert by_ev["1"].quote == "task_file/scripts/cost_model.py:28:align"
    # ev2: caller_contract_view suffix past the CLI [:2000] display cap
    assert by_ev["2"].status == "MODEL-ONLY"
    assert "display-cap" in by_ev["2"].status_reason
    # real transcript parses fully ([GT L1] telemetry is a known shape now)
    assert a.unparsed_lines == 0
    assert not a.unparsed_structures
    assert not a.ledger_issues and not a.dose_violations


def test_smoke_fixture_cli_json_and_exit_code(tmp_path):
    out_json = tmp_path / "audit.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(SMOKE_RUN), "--json", str(out_json)],
        capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0
    assert "GREEN-delivered" in proc.stdout
    assert "3L" in proc.stdout  # gt column marks ledger truth
    data = json.loads(out_json.read_text(encoding="utf-8"))
    task = data["tasks"][0]
    assert task["verdict"] == "GREEN-delivered"
    assert task["ledger_present"] is True
    assert [r["status"] for r in task["ledger_rows"]] == [
        "MODEL-ONLY", "TRANSCRIPT-CONFIRMED", "MODEL-ONLY"]


def test_smoke_report_is_deterministic():
    r1 = gt_audit.render_report(gt_audit.audit_run(SMOKE_RUN), SMOKE_RUN)
    r2 = gt_audit.render_report(gt_audit.audit_run(SMOKE_RUN), SMOKE_RUN)
    assert r1 == r2


def test_tampered_ledger_row_is_unreconciled_red(tmp_path):
    run = copy_smoke(tmp_path)
    lp = smoke_task_agent(run) / "gt_ledger.jsonl"
    rows = [json.loads(x) for x in lp.read_text().splitlines()]
    rows[1]["rendered_bytes_hash"] = "0" * 64  # tamper the sealed byte-proof
    lp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    a = gt_audit.audit_run(run)[0]
    assert a.verdict == "RED"
    ev1 = next(r for r in a.ledger_rows if r.event_id == "1")
    assert ev1.status == "UNRECONCILED"
    assert any("UNRECONCILED ledger row ev1" in r for r in a.verdict_reasons)


def test_provider_receipt_is_delivery_witness_not_transcript(monkeypatch, tmp_path):
    run = copy_smoke(tmp_path)
    agent_dir = smoke_task_agent(run)
    (agent_dir / "gt_attribution.jsonl").write_text("", encoding="utf-8")
    ledger = [
        json.loads(line)
        for line in (agent_dir / "gt_ledger.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    delivery_ids = [row["event_id"] for row in ledger]
    rows = [{
        "event_type": "run.started",
        "payload": {"provider_final_receipts_required": True},
    }]
    rows.extend({
        "event_type": "decision.committed",
        "payload": {
            "decision": "delivered",
            "delivery_id": row["event_id"],
            "evidence_type": row["evidence_type"],
            "rendered_bytes_hash": row["rendered_bytes_hash"],
        },
    } for row in ledger)
    rows.extend([
        {
            "event_type": "provider.request",
            "payload": {
                "iteration": 2,
                "delivery_ids": delivery_ids,
                "matches": [
                    {
                        "delivery_id": row["event_id"],
                        "rendered_sha256": row["rendered_bytes_hash"],
                    }
                    for row in ledger
                ],
            },
        },
        {
            "event_type": "model.response",
            "payload": {
                "iteration": 2,
                "delivery_ids": delivery_ids,
            },
        },
    ])
    monkeypatch.setattr(
        gt_audit, "load_attribution", lambda _path: (rows, [])
    )
    original_reconcile = gt_audit.reconcile_ledger

    def force_transcript_miss(*args, **kwargs):
        original_reconcile(*args, **kwargs)
        args[0][1].status = "UNRECONCILED"
        args[0][1].status_reason = "synthetic transcript omission"

    monkeypatch.setattr(gt_audit, "reconcile_ledger", force_transcript_miss)

    audit = gt_audit.audit_run(run)[0]

    assert audit.verdict == "GREEN-delivered"
    missed = audit.ledger_rows[1]
    assert missed.status == "UNRECONCILED"
    assert missed.provider_confirmed is True
    assert not any(
        "UNRECONCILED ledger row" in reason
        for reason in audit.verdict_reasons
    )


def test_chain_head_duplicate_is_flagged(tmp_path):
    run = copy_smoke(tmp_path)
    lp = smoke_task_agent(run) / "gt_ledger.jsonl"
    rows = [json.loads(x) for x in lp.read_text().splitlines()]
    rows[2]["chain_head"] = rows[0]["chain_head"]  # chain must strictly advance
    lp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    a = gt_audit.audit_run(run)[0]
    assert a.verdict == "YELLOW"  # integrity flag, not the F1 delivery-lie class
    assert any("chain_head duplicates" in s for s in a.ledger_issues)


def test_event_id_regression_is_flagged(tmp_path):
    run = copy_smoke(tmp_path)
    lp = smoke_task_agent(run) / "gt_ledger.jsonl"
    rows = [json.loads(x) for x in lp.read_text().splitlines()]
    rows.append(dict(rows[0]))  # re-seal event 0 AFTER event 2
    rows[-1]["chain_head"] = "f" * 64
    rows[-1]["dedup_key"] = "ffffffffffffffff"
    lp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    a = gt_audit.audit_run(run)[0]
    assert any("event_id regressed" in s for s in a.ledger_issues)
    assert a.verdict == "YELLOW"


def test_duplicate_dedup_key_is_flagged(tmp_path):
    run = copy_smoke(tmp_path)
    lp = smoke_task_agent(run) / "gt_ledger.jsonl"
    rows = [json.loads(x) for x in lp.read_text().splitlines()]
    rows[2]["dedup_key"] = rows[1]["dedup_key"]  # one fact sealed twice
    lp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    a = gt_audit.audit_run(run)[0]
    assert any("dedup_key" in s and "already" in s for s in a.ledger_issues)
    assert a.verdict == "YELLOW"


def test_ledger_dose_law_two_gateway_rows_one_event(tmp_path):
    run = copy_smoke(tmp_path)
    lp = smoke_task_agent(run) / "gt_ledger.jsonl"
    rows = [json.loads(x) for x in lp.read_text().splitlines()]
    dup = dict(rows[1])  # second gateway seal on the SAME observation
    dup["chain_head"] = "e" * 64
    dup["dedup_key"] = "eeeeeeeeeeeeeeee"
    rows.insert(2, dup)
    lp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    a = gt_audit.audit_run(run)[0]
    assert any("dose-law violation" in s and "event 1" in s
               for s in a.dose_violations)
    assert a.verdict == "YELLOW"


def test_malformed_ledger_line_fails_loud(tmp_path):
    run = copy_smoke(tmp_path)
    lp = smoke_task_agent(run) / "gt_ledger.jsonl"
    lp.write_text(lp.read_text() + "{not json\n", encoding="utf-8")
    a = gt_audit.audit_run(run)[0]
    assert any("unparseable" in s for s in a.ledger_issues)
    assert a.verdict == "YELLOW"


# --------------------------------------------------------------------------- #
# 9. gt_deliveries.txt - optional byte-source, sha256 contract
# --------------------------------------------------------------------------- #
def write_deliveries(run: Path, blocks: list[tuple[str, str, str, str, str]]) -> None:
    """blocks: (event_id, boundary, evidence_type, hash, text)."""
    out: list[str] = []
    for eid, boundary, ev_type, h, text in blocks:
        out.append(f"event_id={eid} boundary={boundary} evidence_type={ev_type} "
                   f"rendered_bytes_hash={h}")
        out.extend(text.strip("\n").splitlines())
        out.append("")
    (smoke_task_agent(run) / "gt_deliveries.txt").write_text(
        "\n".join(out) + "\n", encoding="utf-8")


def test_deliveries_file_verified_and_used_for_join(tmp_path):
    run = copy_smoke(tmp_path)
    write_deliveries(run, [("1", "gateway", "localization", EV1_HASH, EV1_TEXT)])
    a = gt_audit.audit_run(run)[0]
    assert a.deliveries_file_present is True
    ev1 = next(r for r in a.ledger_rows if r.event_id == "1")
    assert ev1.status == "TRANSCRIPT-CONFIRMED"
    assert "deliveries-file bytes located" in ev1.status_reason
    assert ev1.quote == "task_file/scripts/cost_model.py:28:align"
    assert a.verdict == "GREEN-delivered"
    assert not a.ledger_issues


def test_deliveries_file_bridge_exact_format(tmp_path):
    # pin the EXACT gt_engine/bridge.py writer frame: '--- ... ---' header,
    # then shipped bytes verbatim, then b'\n\n' (shipped may START with '\n').
    run = copy_smoke(tmp_path)
    lead_text = "\n" + EV1_TEXT  # leading-newline join variant
    lead_hash = hashlib.sha256(lead_text.encode()).hexdigest()
    raw = (f"--- event_id=1 boundary=gateway evidence_type=localization "
           f"rendered_bytes_hash={lead_hash} ---\n"
           + lead_text + "\n\n")
    (smoke_task_agent(run) / "gt_deliveries.txt").write_bytes(raw.encode())
    texts, issues = gt_audit.load_deliveries(
        smoke_task_agent(run) / "gt_deliveries.txt")
    assert issues == []
    assert texts == {"1": lead_text}
    # the run still reconciles: header hash differs from the ledger seal
    # (ledger sealed the no-leading-newline variant), which must be REPORTED,
    # not silently accepted.
    a = gt_audit.audit_run(run)[0]
    ev1 = next(r for r in a.ledger_rows if r.event_id == "1")
    assert ev1.status == "UNRECONCILED"
    assert "disagree" in ev1.status_reason


def test_deliveries_file_bridge_format_matching_ledger(tmp_path):
    run = copy_smoke(tmp_path)
    raw = (f"--- event_id=1 boundary=gateway evidence_type=localization "
           f"rendered_bytes_hash={EV1_HASH} ---\n"
           + EV1_TEXT + "\n\n")
    (smoke_task_agent(run) / "gt_deliveries.txt").write_bytes(raw.encode())
    a = gt_audit.audit_run(run)[0]
    ev1 = next(r for r in a.ledger_rows if r.event_id == "1")
    assert ev1.status == "TRANSCRIPT-CONFIRMED"
    assert "deliveries-file bytes located" in ev1.status_reason
    assert a.verdict == "GREEN-delivered"


def test_deliveries_block_hash_mismatch_is_red(tmp_path):
    run = copy_smoke(tmp_path)
    write_deliveries(run, [
        ("1", "gateway", "localization", EV1_HASH,
         EV1_TEXT.replace("28", "99"))])  # tampered shipped text
    a = gt_audit.audit_run(run)[0]
    assert any("fails sha256" in s for s in a.ledger_issues)
    ev1 = next(r for r in a.ledger_rows if r.event_id == "1")
    assert ev1.status == "UNRECONCILED"
    assert a.verdict == "RED"


def test_deliveries_header_ledger_disagreement_is_red(tmp_path):
    # block verifies against ITS OWN header hash, but that hash is not the
    # ledger row's seal -> the two GT-side records disagree -> F1 class.
    run = copy_smoke(tmp_path)
    fake_text = "task_file/scripts/other.py:1:nope\n"
    fake_hash = hashlib.sha256(fake_text.encode()).hexdigest()
    write_deliveries(run, [("1", "gateway", "localization", fake_hash, fake_text)])
    a = gt_audit.audit_run(run)[0]
    ev1 = next(r for r in a.ledger_rows if r.event_id == "1")
    assert ev1.status == "UNRECONCILED"
    assert "disagree" in ev1.status_reason
    assert a.verdict == "RED"


def test_absent_deliveries_file_degrades_to_hash_locate():
    a = gt_audit.audit_run(SMOKE_RUN)[0]
    assert a.deliveries_file_present is False
    ev1 = next(r for r in a.ledger_rows if r.event_id == "1")
    assert ev1.status == "TRANSCRIPT-CONFIRMED"
    assert "sha256 of a panel slice" in ev1.status_reason


# --------------------------------------------------------------------------- #
# 10. ledgerless behavior unchanged + [GT L1] telemetry handling
# --------------------------------------------------------------------------- #
def test_ledgerless_run_keeps_heuristic_behavior(tmp_path):
    make_task_dir(tmp_path, "datecheck__abc", "datecheck", HEALTHY_NONCODE,
                  reward=1.0)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.ledger_present is False
    assert a.verdict == "GREEN-dormant"  # never GREEN-delivered without a ledger
    assert a.ledger_rows == []


def test_gt_l1_lines_outside_panels_are_known_shape(tmp_path):
    transcript = "\n".join([
        panel("tool_call", "bash(command='grep -rn foo /app')"),
        "[GT L1] grep-to-seed: searching 3 tokens in /app",
        "[GT L1] FTS5: query returned 2 candidates",
        panel("tool_result", "ok"),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert a.unparsed_lines == 0
    assert a.verdict in ("GREEN-dormant", "GREEN-quiet")


def test_gt_l1_inside_a_panel_is_flagged(tmp_path):
    transcript = "\n".join([
        panel("tool_call", "bash(command='ls')"),
        panel("tool_result", "ok\n[GT L1] FTS5: query returned 2 candidates",
              width=100),
        stop_line(),
        panel("final", "done"),
    ])
    make_task_dir(tmp_path, "t__1", "t", transcript)
    a = gt_audit.audit_run(tmp_path)[0]
    assert any("[GT L1] telemetry INSIDE" in f for f in a.review_flags)
    assert a.verdict == "YELLOW"
