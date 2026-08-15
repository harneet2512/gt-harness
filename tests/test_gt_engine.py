"""gt_engine unit tests: bridge sequence, exit-code parsing, GT-off byte
identity of truncation, indexer detection, profile-2 default fan-out, bash
edit bridges, the gate-kernel submit probe, the task-start capsule, and a
GT-off agent-loop smoke with a stubbed provider (no API key required)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from gt_engine.bridge import (
    DeliveredSpan,
    apply_profile_env,
    bash_edit_target,
    bash_edit_targets,
    failure_fingerprint,
    gateway_observation_output,
    parse_exit_code,
)
from gt_engine.context import smart_truncate
from gt_engine.indexer import ensure_index, is_code_repo
from nano.agent import Agent
from nano.providers import StepResult, ToolCall, Usage


@pytest.fixture(autouse=True)
def _gt_env_isolation():
    """Strip GT_* env before each test and undo anything a test (or
    apply_profile_env's direct os.environ writes) added - no cross-test leak."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("GT_")}
    for k in saved:
        del os.environ[k]
    yield
    for k in [k for k in os.environ if k.startswith("GT_")]:
        del os.environ[k]
    os.environ.update(saved)

try:
    import groundtruth  # noqa: F401
    HAVE_GT = True
except ImportError:
    HAVE_GT = False

requires_gt = pytest.mark.skipif(not HAVE_GT, reason="groundtruth not installed")


# --------------------------------------------------------------------------- #
# exit-code parsing (decision C: tools.py:172 flattens the code into a string)
# --------------------------------------------------------------------------- #
def test_parse_exit_code_success_is_zero():
    assert parse_exit_code("any output", is_error=False) == 0


def test_parse_exit_code_from_toolerror_text():
    out = "ERROR: FAILED tests/x.py::test_a\n[exit code 1]"
    assert parse_exit_code(out, is_error=True) == 1


def test_parse_exit_code_multiline_and_negative():
    assert parse_exit_code("ERROR: boom\nmore\n[exit code 137]", True) == 137
    assert parse_exit_code("ERROR: killed\n[exit code -9]", True) == -9


def test_parse_exit_code_unparsable_is_none():
    # Timeout / dead shell / dispatch errors carry no "[exit code N]" suffix.
    assert parse_exit_code("ERROR: Command exceeded timeout of 60s", True) is None
    assert parse_exit_code("", True) is None
    # The marker must be terminal, not mid-text.
    assert parse_exit_code("[exit code 3] and then more text", True) is None


def test_empty_failed_search_marker_is_restored_only_for_gateway_semantics():
    marker = "\n[exit code 1]"
    assert gateway_observation_output("grep -R azure providers", marker, 1) == ""
    assert gateway_observation_output("pytest -q", marker, 1) == marker
    diagnostic = "grep: providers: No such file\n[exit code 1]"
    assert (
        gateway_observation_output("grep -R azure providers", diagnostic, 1)
        == diagnostic
    )


# --------------------------------------------------------------------------- #
# GT-off byte identity: smart_truncate with no deliveries == stock truncation
# --------------------------------------------------------------------------- #
def _make_messages(n_results: int = 4, result_len: int = 500,
                   big_input: int = 0) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "task"}]
    for i in range(n_results):
        inp: dict[str, Any] = {"command": "echo hi"}
        if big_input:
            inp["new"] = "y" * big_input
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "bash", "input": inp}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}",
             "content": f"out{i}:" + ("x" * result_len), "is_error": False}]})
    return msgs


@pytest.mark.parametrize("budget,big_input", [
    (10_000_000, 0),   # under budget: no-op
    (1200, 0),         # phase 1 only
    (300, 5000),       # phase 1 exhausts, phase 2 shrinks tool_use inputs
])
def test_smart_truncate_byte_identical_when_no_deliveries(budget, big_input):
    msgs = _make_messages(big_input=big_input)
    agent = Agent.__new__(Agent)  # no provider needed for _truncate_if_needed
    agent.truncation_char_budget = budget

    stock_msgs, stock_tr = copy.deepcopy(msgs), []
    Agent._truncate_if_needed(agent, stock_msgs, stock_tr)

    gt_msgs, gt_tr = copy.deepcopy(msgs), []
    smart_truncate(gt_msgs, gt_tr, char_budget=budget, delivered_spans=[])

    assert gt_msgs == stock_msgs
    assert gt_tr == stock_tr


def test_smart_truncate_exempts_evidence_blocks_entirely():
    """FIX E (sealed => seen, structural): a tool_result carrying a delivered
    span - ANY tier - is never phase-1 truncated; only evidence-free blocks
    are reclaimed, in the stock oldest-first order."""
    msgs = _make_messages(n_results=3, result_len=500)
    evidence = "pkg/a.py:1:helper\n"
    warn = "note: check callers\n"
    msgs[2]["content"][0]["content"] += "\n" + evidence   # block t0: VERIFIED
    msgs[4]["content"][0]["content"] += "\n" + warn       # block t1: WARNING
    spans = [DeliveredSpan(text=evidence, tier="VERIFIED",
                           evidence_type="def_ref_partition", dedup_key="k1"),
             DeliveredSpan(text=warn, tier="WARNING",
                           evidence_type="cochange_partner", dedup_key="k2")]
    tr: list[dict[str, Any]] = []
    smart_truncate(msgs, tr, char_budget=100, delivered_spans=spans)
    # Only the evidence-free block (t2) was reclaimed by phase 1.
    order = [t["tool_use_id"] for t in tr if t["type"] == "truncation"]
    assert order == ["t2"]
    assert str(msgs[6]["content"][0]["content"]).startswith("[truncated")
    # Both evidence-bearing blocks survive INTACT (bytes, not just presence).
    assert evidence in str(msgs[2]["content"][0]["content"])
    assert warn in str(msgs[4]["content"][0]["content"])


def test_smart_truncate_evidence_only_overflow_falls_to_phase_two():
    """FIX E: when the ONLY reclaimable phase-1 blocks carry evidence, phase 1
    reclaims nothing - the evidence survives intact and phase 2 (tool_use
    input shrinking) fires instead."""
    msgs = _make_messages(n_results=2, result_len=300, big_input=5000)
    ev0 = "pkg/a.py:1:helper\n"
    ev1 = "note: check callers\n"
    msgs[2]["content"][0]["content"] += "\n" + ev0
    msgs[4]["content"][0]["content"] += "\n" + ev1
    spans = [DeliveredSpan(text=ev0, tier="VERIFIED",
                           evidence_type="def_ref_partition", dedup_key="k1"),
             DeliveredSpan(text=ev1, tier="HYPOTHESIS",
                           evidence_type="recovery", dedup_key="k2")]
    tr: list[dict[str, Any]] = []
    smart_truncate(msgs, tr, char_budget=800, delivered_spans=spans)
    # No tool_result was truncated; every truncation event is a phase-2
    # tool_use input shrink.
    assert tr and all(t["type"] == "truncation" for t in tr)
    for i in (2, 4):
        assert not str(msgs[i]["content"][0]["content"]).startswith("[truncated")
    assert ev0 in str(msgs[2]["content"][0]["content"])
    assert ev1 in str(msgs[4]["content"][0]["content"])
    # Phase 2 shrank the oversized input string(s).
    assert any(str(v).startswith("[truncated")
               for m in msgs if isinstance(m.get("content"), list)
               for b in m["content"] if b.get("type") == "tool_use"
               for v in (b.get("input") or {}).values())


def test_smart_truncate_keeps_phase_two():
    # Only oversized tool_use inputs can free the space: phase 2 must run.
    msgs = _make_messages(n_results=1, result_len=10, big_input=8000)
    tr: list[dict[str, Any]] = []
    smart_truncate(msgs, tr, char_budget=500, delivered_spans=[])
    assert str(msgs[1]["content"][0]["input"]["new"]).startswith("[truncated")


def test_compact_provider_view_keeps_task_checkpoint_and_two_recent_turns():
    from gt_engine.context import compact_provider_view

    messages = [{"role": "user", "content": "ORIGINAL TASK"}]
    for index in range(5):
        messages.extend([
            {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": f"t{index}",
                    "name": "bash",
                    "input": {"command": f"echo {index}"},
                }],
            },
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": f"t{index}",
                    "content": str(index) * 200,
                    "is_error": False,
                }],
            },
        ])

    view, receipt = compact_provider_view(
        messages,
        checkpoint="state-v1",
        char_budget=900,
        tail_turns=2,
        tool_output_chars=100,
    )

    assert "ORIGINAL TASK" in str(view[0]["content"])
    assert "state-v1" in str(view[0]["content"])
    rendered = str(view)
    assert "'id': 't0'" not in rendered
    assert "'id': 't4'" in rendered
    assert receipt["compacted"] is True
    assert receipt["omitted_message_count"] >= 2
    assert receipt["active_message_chars"] < receipt["raw_message_chars"]


def test_compact_provider_view_does_not_mutate_durable_messages():
    from gt_engine.context import compact_provider_view

    messages = _make_messages(n_results=1, result_len=1000, big_input=1000)
    original = copy.deepcopy(messages)

    compact_provider_view(
        messages,
        checkpoint="state",
        char_budget=200,
        tail_turns=1,
        tool_output_chars=50,
    )

    assert messages == original


def test_compact_provider_view_uses_budget_for_more_than_minimum_tail():
    from gt_engine.context import compact_provider_view

    messages = _make_messages(n_results=6, result_len=1_500)
    messages[2]["content"][0]["content"] = "old" * 5000

    view, receipt = compact_provider_view(
        messages,
        checkpoint="state",
        char_budget=5_000,
        tail_turns=2,
        max_tail_turns=4,
        tool_output_chars=1_000,
    )

    rendered = str(view)
    assert "'id': 't5'" in rendered
    assert "'id': 't2'" in rendered
    assert "'id': 't0'" not in rendered
    assert 2 < receipt["tail_turns"] <= 4
    assert receipt["active_message_chars"] <= 5_000


def test_compact_provider_view_default_never_rehydrates_irrelevant_old_turns():
    """Typed state, not spare budget, owns durable context by default."""
    from gt_engine.context import compact_provider_view

    messages = _make_messages(n_results=6, result_len=1_500)

    view, receipt = compact_provider_view(
        messages,
        checkpoint='{"unresolved":["obl-1"],"changed_paths":["pkg/a.py"]}',
        char_budget=20_000,
        tail_turns=2,
        tool_output_chars=1_000,
    )

    rendered = str(view)
    assert "'id': 't5'" in rendered
    assert "'id': 't4'" in rendered
    assert "'id': 't3'" not in rendered
    assert receipt["tail_turns"] == 2


def test_compact_provider_view_retains_one_older_semantically_active_group():
    from gt_engine.context import compact_provider_view

    messages = _make_messages(n_results=6, result_len=1_500)
    messages[3]["content"][0]["input"]["command"] = "sed -n 1,80p pkg/active.py"
    messages[4]["content"][0]["content"] = "pkg/active.py exact source"

    view, receipt = compact_provider_view(
        messages,
        checkpoint='{"changed_paths":["pkg/active.py"]}',
        char_budget=10_000,
        target_char_budget=6_000,
        tail_turns=2,
        semantic_needles=("pkg/active.py",),
        tool_output_chars=1_000,
    )

    rendered = str(view)
    assert "'id': 't1'" in rendered
    assert "'id': 't4'" in rendered
    assert "'id': 't5'" in rendered
    assert "'id': 't2'" not in rendered
    assert receipt["semantic_tail_turns"] == 1
    assert receipt["tail_turns"] == 3
    assert receipt["omitted_group_hashes"]


def test_bridge_records_gateway_no_candidate_reason(tmp_path):
    """A quiet gateway observation must be explainable, not absent from telemetry."""
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(tmp_path), graph_db=None)

    assert bridge.enrich("bash", {"command": "echo ok"}, "ok", False) == "ok"

    trace_path = tmp_path / ".gt" / "gt_attribution.jsonl"
    rows = [json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any(row["event_type"] == "observation.received" for row in rows)
    terminal = [row for row in rows if row["event_type"] == "decision.committed"]
    assert terminal[-1]["payload"]["decision"] == "no_delivery"
    assert terminal[-1]["payload"]["reason"] == "no_candidate"


def test_bridge_proves_exact_delivery_exposure(tmp_path):
    from types import SimpleNamespace

    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(tmp_path), graph_db=None)
    sealed = SimpleNamespace(
        event_id="7",
        evidence_type="localization",
        tier="VERIFIED",
        dedup_key="dedup-7",
        rendered_bytes_hash=hashlib.sha256(b"EVIDENCE").hexdigest(),
    )
    bridge._ledger_record(sealed, "EVIDENCE", "gateway")

    ids = bridge.trace_model_request(
        2, [{"role": "user", "content": "tool output\nEVIDENCE"}]
    )

    assert ids == ("7",)


@requires_gt
def test_model_action_is_bound_to_gt_exposure_without_persisting_arguments(
        tmp_path):
    """The next action is attributable to an exposed GT delivery, while raw
    provider/tool payload values remain outside the durable trace."""
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(tmp_path), graph_db=None)
    bridge._delivery_texts["7"] = "\nverified evidence"
    delivery_ids = bridge.trace_model_request(2, [
        {"role": "user", "content": [
            {"type": "tool_result", "content": "output\nverified evidence"},
        ]},
    ])
    secret_path = "src/provider-secret-value.py"
    result = StepResult(
        text="I will inspect the evidence target",
        tool_calls=[ToolCall(
            id="next-1", name="read_file", arguments={"path": secret_path},
        )],
        stop_reason="tool_use", usage=_usage(),
    )
    bridge.trace_model_response(2, result, delivery_ids)

    row = next(
        item for item in bridge._attribution.rows
        if item["event_type"] == "model.response"
    )
    assert delivery_ids == ("7",)
    assert row["event_type"] == "model.response"
    assert row["payload"]["delivery_ids"] == ["7"]
    assert row["payload"]["tool_calls"] == [
        {"id": "next-1", "name": "read_file"},
    ]
    action = bridge._attribution.rows[-1]
    assert action["event_type"] == "response.action"
    assert action["payload"]["delivery_id"] == "7"
    raw = (tmp_path / ".gt" / "gt_attribution.jsonl").read_text(
        encoding="utf-8")
    assert secret_path not in raw
    assert "I will inspect the evidence target" not in raw


def test_provider_receipt_binds_block_list_delivery_without_persisting_payload(
        tmp_path):
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(tmp_path), graph_db=None)
    secret = "\nprovider-final GT capsule"
    bridge._delivery_texts["8"] = secret
    bridge._delivery_metadata["8"] = {
        "evidence_type": "localization",
        "producer": "ranked_localization",
        "target": "pkg/alpha.py",
    }

    delivery_ids = bridge.trace_provider_request(
        3,
        "openai.chat.completions",
        {
            "model": "deepseek-v4-flash",
            "messages": [{
                "role": "tool",
                "tool_call_id": "call-2",
                "content": "ordinary output" + secret,
            }],
        },
    )

    assert delivery_ids == ("8",)
    row = bridge._attribution.rows[-1]
    assert row["event_type"] == "provider.request"
    assert row["payload"]["matches"][0]["locations"] == ["0.content"]
    assert row["payload"]["matches"][0]["rendered_sha256"] == hashlib.sha256(
        secret.encode()
    ).hexdigest()
    raw = (tmp_path / ".gt" / "gt_attribution.jsonl").read_text(
        encoding="utf-8"
    )
    assert secret not in raw
    assert "ordinary output" not in raw


def test_provider_receipt_records_explicit_temperature(tmp_path):
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(tmp_path), graph_db=None)
    bridge.trace_provider_request(
        1,
        "openai.chat.completions",
        {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "task"}],
            "temperature": 1.0,
        },
    )

    row = bridge._attribution.rows[-1]
    assert row["event_type"] == "provider.request"
    assert row["payload"]["temperature"] == 1.0


def test_provider_message_view_exposes_capsule_once_without_mutating_history(
    tmp_path,
):
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(tmp_path), graph_db=None)
    capsule = "\nprovider-final GT capsule"
    bridge._delivery_texts["8"] = capsule
    bridge._delivery_metadata["8"] = {
        "evidence_type": "localization",
        "producer": "ranked_localization",
        "target": "pkg/alpha.py",
        "issued_action": "4",
    }
    messages = [{
        "role": "user",
        "content": [{
            "type": "tool_result",
            "content": "ordinary output" + capsule,
        }],
    }]

    bridge.action_index = 4
    active = bridge.provider_message_view(messages)
    assert capsule in bridge._message_text(active)
    bridge.trace_provider_request(
        1,
        "openai.chat.completions",
        {"model": "deepseek-v4-flash", "messages": active},
    )

    bridge.action_index = 5
    expired = bridge.provider_message_view(messages)
    assert capsule not in bridge._message_text(expired)
    assert "ordinary output" in bridge._message_text(expired)
    assert capsule in bridge._message_text(
        messages
    ), "forensic history must remain unchanged"
    assert bridge._delivery_exposures["8"] == 1
    expiry = [
        row for row in bridge._attribution.rows
        if row["event_type"] == "capsule.expired"
    ]
    assert len(expiry) == 1
    assert expiry[0]["payload"]["exposure_count"] == 1


def test_unexposed_capsule_survives_parallel_sibling_action_indices(tmp_path):
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(tmp_path), graph_db=None)
    capsule = "\nGT evidence produced by the first parallel tool result"
    bridge._delivery_texts["34"] = capsule
    bridge._delivery_metadata["34"] = {
        "evidence_type": "caller_contract_view",
        "producer": "caller_contract",
        "target": "pkg/alpha.py",
        "issued_action": "34",
    }
    messages = [{
        "role": "user",
        "content": [{
            "type": "tool_result",
            "content": "ordinary output" + capsule,
        }],
    }]

    # Five sibling tool results completed before the next provider decision.
    # Action age is not exposure: the capsule must survive until that request.
    bridge.action_index = 39
    active = bridge.provider_message_view(messages)
    assert capsule in bridge._message_text(active)
    assert not any(
        row["event_type"] == "capsule.expired"
        for row in bridge._attribution.rows
    )

    bridge.trace_provider_request(
        1,
        "openai.chat.completions",
        {"model": "deepseek-v4-flash", "messages": active},
    )
    bridge.action_index = 40
    expired = bridge.provider_message_view(messages)
    assert capsule not in bridge._message_text(expired)


def test_expired_capsule_cannot_corrupt_overlapping_unexposed_capsule(
    tmp_path,
):
    """An old localization may be a strict substring of a fresher result."""
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(repo_root=str(tmp_path), graph_db=None)
    old = "\nbase_terminal.py:4:BaseTerminal\n"
    fresh = (
        "\nheadless_terminal.py:13:HeadlessTerminal"
        "\nbase_terminal.py:4:BaseTerminal\n"
    )
    bridge._delivery_texts.update({"1": old, "34": fresh})
    bridge._delivery_metadata.update({
        "1": {
            "evidence_type": "localization",
            "producer": "ranked_localization",
            "target": "base_terminal.py",
            "issued_action": "1",
        },
        "34": {
            "evidence_type": "localization",
            "producer": "ranked_localization",
            "target": "headless_terminal.py",
            "issued_action": "34",
        },
    })
    bridge._delivery_exposures["1"] = 1
    messages = [
        {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "content": "first search" + old,
            }],
        },
        {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "content": "fresh search" + fresh,
            }],
        },
    ]

    request = bridge.provider_message_view(messages)

    assert old not in bridge._message_text(request[0])
    assert fresh in bridge._message_text(request[1])
    assert bridge.trace_model_request(2, request) == ("34",)
    assert bridge.trace_provider_request(
        2,
        "openai.chat.completions",
        {"model": "deepseek-v4-flash", "messages": request},
    ) == ("34",)


# --------------------------------------------------------------------------- #
# indexer: code-repo detection (GT dormant on non-code roots)
# --------------------------------------------------------------------------- #
def test_is_code_repo_detection(tmp_path):
    (tmp_path / "notes.txt").write_text("just text", encoding="utf-8")
    assert not is_code_repo(str(tmp_path))
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert is_code_repo(str(tmp_path))


def test_is_code_repo_skips_vendored_dirs(tmp_path):
    nm = tmp_path / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("//", encoding="utf-8")
    assert not is_code_repo(str(tmp_path))


def test_ensure_index_non_code_root_returns_none(tmp_path):
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    assert ensure_index(str(tmp_path)) is None
    assert ensure_index(str(tmp_path / "missing")) is None
    assert ensure_index(None) is None


@requires_gt
def test_ensure_index_can_keep_graph_state_outside_repository(
        tmp_path, monkeypatch):
    import groundtruth._binary

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    state = tmp_path / "private-state"
    monkeypatch.setenv("GT_STATE_DIR", str(state))

    def fake_run_index(_root, output):
        import sqlite3

        connection = sqlite3.connect(output)
        try:
            connection.execute("CREATE TABLE nodes(id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE VIRTUAL TABLE nodes_fts USING fts5(name,file_path)"
            )
            connection.commit()
        finally:
            connection.close()
        return True

    monkeypatch.setattr(groundtruth._binary, "run_index", fake_run_index)

    db = ensure_index(str(repo))

    assert db is not None
    assert Path(db).is_relative_to(state)
    assert Path(db).name == "graph.db"
    manifest = Path(db).with_suffix(".manifest.json")
    certification = json.loads(manifest.read_text(encoding="utf-8"))
    assert certification["sqlite_quick_check"] == "ok"
    assert certification["graph_sha256"] == hashlib.sha256(Path(db).read_bytes()).hexdigest()
    assert not (repo / ".gt").exists()


@requires_gt
def test_failed_index_build_preserves_previous_database(tmp_path, monkeypatch):
    import groundtruth._binary

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    state = tmp_path / "state"
    root_key = hashlib.sha256(
        os.path.realpath(repo).encode("utf-8", "surrogatepass")
    ).hexdigest()[:16]
    target = state / root_key / "graph.db"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"known-good")
    monkeypatch.setenv("GT_STATE_DIR", str(state))
    monkeypatch.setattr(groundtruth._binary, "run_index", lambda *_args: False)

    assert ensure_index(str(repo)) is None
    assert target.read_bytes() == b"known-good"


@requires_gt
def test_manifest_publication_failure_rolls_back_database(tmp_path, monkeypatch):
    import sqlite3

    import groundtruth._binary

    import gt_engine.indexer as indexer

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    state = tmp_path / "state"
    root_key = hashlib.sha256(
        os.path.realpath(repo).encode("utf-8", "surrogatepass")
    ).hexdigest()[:16]
    target = state / root_key / "graph.db"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"known-good")
    monkeypatch.setenv("GT_STATE_DIR", str(state))

    def valid_index(_root, output):
        connection = sqlite3.connect(output)
        connection.execute("CREATE TABLE nodes(id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()
        return True

    monkeypatch.setattr(groundtruth._binary, "run_index", valid_index)
    monkeypatch.setattr(
        indexer, "_atomic_write",
        lambda *_args: (_ for _ in ()).throw(OSError("manifest fault")),
    )
    assert ensure_index(str(repo)) is None
    assert target.read_bytes() == b"known-good"


# --------------------------------------------------------------------------- #
# bridge: the full production sequence against a real graph.db
# --------------------------------------------------------------------------- #
@pytest.fixture
def indexed_repo(tmp_path, monkeypatch):
    if not HAVE_GT:
        pytest.skip("groundtruth not installed")
    monkeypatch.setenv("GT_GATEWAY", "1")
    monkeypatch.setenv("GT_GATEWAY_NATIVE", "1")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "alpha.py").write_text(
        "def helper(x, y):\n    return x + y\n\n\n"
        "def caller_a(v):\n    return helper(v, 1)\n", encoding="utf-8")
    (pkg / "beta.py").write_text(
        "def helper(a, b, c):\n    return a * b * c\n\n\n"
        "def caller_b(v):\n    return helper(v, 2, 3)\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from pkg.alpha import caller_a\n\n\ndef run():\n"
        "    return caller_a(1)\n", encoding="utf-8")
    db = ensure_index(str(tmp_path))
    if db is None:
        pytest.skip("gt-index binary unavailable")
    from gt_engine.bridge import GTBridge
    return GTBridge(repo_root=str(tmp_path), graph_db=db)


_AMBIGUOUS_GREP = "grep -rn helper ."
_GREP_OUT = ("pkg/alpha.py:1:def helper(x, y):\n"
             "pkg/beta.py:1:def helper(a, b, c):\n")


@requires_gt
@pytest.mark.gt_all17
def test_bridge_delivers_sealed_pure_suffix(indexed_repo):
    b = indexed_repo
    out = b.enrich("bash", {"command": _AMBIGUOUS_GREP}, _GREP_OUT, False)
    # Pure suffix (TITO law 1): the original observation bytes are untouched.
    assert out.startswith(_GREP_OUT)
    assert len(out) > len(_GREP_OUT)
    # Sealed delivery: envelope stamped, chain advanced, dedup key recorded.
    assert len(b.deliveries) == 1
    sealed = b.deliveries[0]
    assert sealed.evidence_type == "def_ref_partition"
    assert sealed.receipt_state == "delivered"
    assert sealed.rendered_bytes_hash
    assert b.chain_head
    assert sealed.dedup_key in b.episode.delivered_dedup
    # Native render: no GT tag, no test identity, within budget.
    delta = out[len(_GREP_OUT):]
    assert "<gt-" not in delta.lower()
    assert len(delta) <= 4001  # delta + at most one inserted newline
    # Span tracked for evidence-aware truncation.
    assert b.delivered_spans[0].evidence_type == "def_ref_partition"


@requires_gt
@pytest.mark.gt_all17
def test_file_view_fires_verified_caller_contract(indexed_repo, tmp_path):
    bridge = indexed_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    source = alpha.read_text(encoding="utf-8")

    output = bridge.enrich(
        "read_file", {"path": str(alpha)}, source, False
    )

    assert output.startswith(source)
    assert [item.evidence_type for item in bridge.deliveries] == [
        "caller_contract_view"
    ]


@requires_gt
@pytest.mark.gt_all17
def test_search_fires_ranked_localization_and_loc_reslot(
    indexed_repo, monkeypatch,
):
    apply_profile_env()
    bridge = indexed_repo
    bridge.issue_text = "Repair the helper implementation."
    from groundtruth.runtime import gateway

    monkeypatch.setattr(
        gateway,
        "_compute_ranked_localization_rows",
        lambda _state, _audit=None: [("pkg/alpha.py", 1, "helper")],
    )
    output = bridge.enrich(
        "bash", {"command": "grep -rn helper ."}, _GREP_OUT, False
    )

    assert [item.evidence_type for item in bridge.deliveries] == [
        "localization"
    ]
    delivery_id = next(iter(bridge._delivery_texts))
    exposure = bridge.trace_provider_request(
        1,
        "openai.chat.completions",
        {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": output}],
        },
    )
    bridge.trace_model_response(
        1,
        StepResult(
            text="",
            tool_calls=[
                ToolCall(
                    id="next-1",
                    name="read_file",
                    arguments={"path": "pkg/alpha.py"},
                )
            ],
            stop_reason="tool_use",
            usage=_usage(),
        ),
        exposure,
    )
    from gt_engine.attribution import summarize_features

    summary = summarize_features(bridge._attribution.rows)
    assert exposure == (delivery_id,)
    assert summary["localization"]["status"] == "WITNESSED"
    assert summary["GT_LOC_RESLOT"]["status"] == "WITNESSED"


@requires_gt
def test_bridge_dedup_suppresses_repeat(indexed_repo):
    b = indexed_repo
    first = b.enrich("bash", {"command": _AMBIGUOUS_GREP}, _GREP_OUT, False)
    assert first != _GREP_OUT
    second = b.enrich("bash", {"command": _AMBIGUOUS_GREP}, _GREP_OUT, False)
    assert second == _GREP_OUT  # same fact never delivered twice
    assert len(b.deliveries) == 1


@requires_gt
def test_bridge_error_observation_never_breaks(indexed_repo):
    b = indexed_repo
    out = b.enrich("bash", {"command": "false"},
                   "ERROR: \n[exit code 1]", True)
    assert out.startswith("ERROR: ")  # raw output survives whatever GT does


@requires_gt
def test_bridge_internal_fault_returns_raw_output(indexed_repo):
    b = indexed_repo
    b.graph_db = 12345  # type: ignore[assignment] - poison the state
    out = b.enrich("bash", {"command": _AMBIGUOUS_GREP}, _GREP_OUT, False)
    assert out == _GREP_OUT  # correct-or-quiet: fault -> unchanged


@requires_gt
def test_bridge_repo_rel_paths(indexed_repo):
    b = indexed_repo
    rel = b._repo_rel(b.repo_root.replace("/", "\\") + "\\pkg\\alpha.py")
    assert rel == "pkg/alpha.py"  # decision J: repo-relative, forward slashes


# --------------------------------------------------------------------------- #
# FIX 1: profile-2 default fan-out (production parity, AGENTS.md §C / W8)
# --------------------------------------------------------------------------- #
@requires_gt
def test_profile_default_fans_out_profile_2():
    apply_profile_env()
    # Profile-1 core + Super-Mode members the edit-turn producers need.
    for flag in ("GT_GATEWAY", "GT_GATEWAY_NATIVE", "GT_GATEWAY_EDIT_BRIDGES",
                 "GT_PATCH_DELTA", "GT_CHANGE_SURFACE", "GT_LOC_RESLOT"):
        assert os.environ.get(flag) == "1", flag
    # Determinism: durable cross-session memory never set by the bridge.
    assert not any(k.startswith("GT_XSESSION") for k in os.environ)


@requires_gt
def test_profile_explicit_member_value_wins(monkeypatch):
    monkeypatch.setenv("GT_PATCH_DELTA", "0")  # user kill-switch
    apply_profile_env()
    assert os.environ["GT_PATCH_DELTA"] == "0"
    assert os.environ.get("GT_GATEWAY") == "1"


@requires_gt
def test_profile_explicit_legacy_token_is_minimal_pair(monkeypatch):
    monkeypatch.setenv("GT_RL_PROFILE", "off")
    apply_profile_env()
    assert os.environ.get("GT_GATEWAY") == "1"
    assert os.environ.get("GT_GATEWAY_NATIVE") == "1"
    assert "GT_PATCH_DELTA" not in os.environ  # no profile fan-out


@requires_gt
def test_profile_explicit_token_resolves_that_profile(monkeypatch):
    monkeypatch.setenv("GT_RL_PROFILE", "1")
    apply_profile_env()
    assert os.environ.get("GT_GATEWAY_EDIT_BRIDGES") == "1"  # profile-1 member
    assert "GT_PATCH_DELTA" not in os.environ  # super-mode only


@requires_gt
@pytest.mark.gt_all17
def test_profile_explicit_two_fans_out_behavior_flags(monkeypatch):
    monkeypatch.setenv("GT_RL_PROFILE", "2")

    apply_profile_env()

    for flag in (
        "GT_CS_EDIT_TRIGGER",
        "GT_SS_EDIT_PREVENTIVE",
        "GT_INFRA_NOISE_GUARD",
        "GT_HYP_CONTRA_GUARD",
        "GT_RECOVERY_ESCALATE",
        "GT_OBLIG_STEER_GUARD",
        "GT_ROLE_DRIVEN_COALITION",
    ):
        assert os.environ.get(flag) == "1", flag


@requires_gt
def test_task_start_receipts_complete_profile_two_control_activation(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("GT_RL_PROFILE", "2")
    apply_profile_env()
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(
        repo_root=str(tmp_path),
        graph_db=None,
        issue_text="Inspect the repository.",
    )
    bridge.task_start()

    started = next(
        row for row in bridge._attribution.rows
        if row["event_type"] == "run.started"
    )
    payload = started["payload"]
    assert payload["profile"] == "2"
    assert payload["missing_profile_controls"] == []
    assert payload["expected_profile_control_count"] == (
        payload["active_profile_control_count"]
    )
    assert set(payload["active_behavior_flags"]) == {
        "GT_CS_EDIT_TRIGGER",
        "GT_SS_EDIT_PREVENTIVE",
        "GT_INFRA_NOISE_GUARD",
        "GT_HYP_CONTRA_GUARD",
        "GT_RECOVERY_ESCALATE",
        "GT_OBLIG_STEER_GUARD",
        "GT_ROLE_DRIVEN_COALITION",
    }


@requires_gt
def test_profile_unknown_token_never_dark(monkeypatch):
    monkeypatch.setenv("GT_RL_PROFILE", "99")
    apply_profile_env()
    assert os.environ.get("GT_GATEWAY") == "1"  # minimal pair fallback


# --------------------------------------------------------------------------- #
# FIX 1 (live proof): an edit with edit_before_after fires an edit-family
# envelope under the profile-2 defaults (patch_delta was dark before).
# --------------------------------------------------------------------------- #
@requires_gt
@pytest.mark.gt_all17
def test_edit_fires_signature_mismatch_under_profile_2(indexed_repo, tmp_path):
    apply_profile_env()  # profile-2 defaults on top of the fixture's pair
    b = indexed_repo
    b.issue_text = "helper returns the wrong sum"
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    after = before.replace("def helper(x, y):", "def helper(x):").replace(
        "return x + y", "return x")
    alpha.write_text(after, encoding="utf-8")
    out = b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
                   edit_before=before, edit_after=after)
    assert [d.evidence_type for d in b.deliveries] == ["signature_mismatch"]
    assert out.startswith("edited")           # pure suffix
    assert "helper()" in out                  # the arity diagnostic delivered
    assert "<gt-" not in out.lower()
    producer_rows = [
        row for row in b._attribution.rows
        if row["event_type"] == "producer.invocation"
        and row["payload"].get("producer") == "caller_contract"
    ]
    entered = {
        row["payload"]["invocation_id"] for row in producer_rows
        if row["payload"]["outcome"] == "entered"
    }
    terminal = {
        row["payload"]["invocation_id"] for row in producer_rows
        if row["payload"]["outcome"] != "entered"
    }
    assert entered
    assert entered == terminal


@requires_gt
@pytest.mark.gt_all17
def test_repeated_failed_search_fires_newfile_precedent_and_change_surface(
    tmp_path, monkeypatch,
):
    apply_profile_env()
    monkeypatch.setenv("GT_LOC_RESLOT", "0")
    providers = tmp_path / "providers"
    providers.mkdir()
    (providers / "__init__.py").write_text(
        "from .aws import AwsProvider\n"
        "from .gcp import GcpProvider\n"
        "REGISTRY = {'aws': AwsProvider, 'gcp': GcpProvider}\n",
        encoding="utf-8",
    )
    (providers / "aws.py").write_text(
        "class AwsProvider:\n    pass\n", encoding="utf-8"
    )
    (providers / "gcp.py").write_text(
        "class GcpProvider:\n    pass\n", encoding="utf-8"
    )
    db = ensure_index(str(tmp_path))
    if db is None:
        pytest.skip("gt-index binary unavailable")
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(
        repo_root=str(tmp_path),
        graph_db=db,
        issue_text="Add an azure provider like the aws and gcp providers.",
    )
    args = {"command": "grep -R azure providers"}
    failed = "[exit code 1]"

    first = bridge.enrich("bash", args, failed, True)
    assert first == failed
    second = bridge.enrich("bash", args, failed, True)

    assert second.startswith(failed)
    assert [item.evidence_type for item in bridge.deliveries] == [
        "missing_role:registration"
    ]
    delivery_id = next(iter(bridge._delivery_texts))
    exposure = bridge.trace_model_request(
        1,
        [{"role": "user", "content": [{"type": "text", "text": second}]}],
    )
    assert exposure == (delivery_id,)
    bridge.trace_model_response(
        1,
        StepResult(
            text="",
            tool_calls=[
                ToolCall(
                    id="next-1",
                    name="edit_file",
                    arguments={"path": "providers/azure.py"},
                )
            ],
            stop_reason="tool_use",
            usage=_usage(),
        ),
        exposure,
    )
    from gt_engine.attribution import summarize_features

    summary = summarize_features(bridge._attribution.rows)
    assert summary["newfile_precedent"]["status"] == "WITNESSED"
    assert summary["GT_CHANGE_SURFACE"]["status"] == "WITNESSED"


@requires_gt
@pytest.mark.gt_all17
def test_explicit_profile_two_new_file_fires_change_surface(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("GT_RL_PROFILE", "2")
    apply_profile_env()
    monkeypatch.setenv("GT_LOC_RESLOT", "0")
    providers = tmp_path / "providers"
    providers.mkdir()
    (providers / "__init__.py").write_text(
        "from .aws import AwsProvider\n"
        "from .gcp import GcpProvider\n"
        "REGISTRY = {'aws': AwsProvider, 'gcp': GcpProvider}\n",
        encoding="utf-8",
    )
    (providers / "aws.py").write_text(
        "class AwsProvider:\n    pass\n", encoding="utf-8"
    )
    (providers / "gcp.py").write_text(
        "class GcpProvider:\n    pass\n", encoding="utf-8"
    )
    db = ensure_index(str(tmp_path))
    if db is None:
        pytest.skip("gt-index binary unavailable")
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(
        repo_root=str(tmp_path),
        graph_db=db,
        issue_text="Add an azure provider like the aws and gcp providers.",
    )
    azure = providers / "azure.py"
    after = "class AzureProvider:\n    pass\n"
    azure.write_text(after, encoding="utf-8")

    output = bridge.enrich(
        "edit_file",
        {"path": str(azure)},
        "created",
        False,
        edit_before=None,
        edit_after=after,
    )

    assert output.startswith("created")
    assert [
        item.evidence_type for item in bridge.deliveries
    ] == ["missing_role_postcreate:registration"]
    from gt_engine.attribution import summarize_features

    summary = summarize_features(bridge._attribution.rows)
    assert summary["newfile_precedent"]["status"] == "DELIVERED_UNEXPOSED"
    assert summary["GT_CHANGE_SURFACE"]["status"] == "DELIVERED_UNEXPOSED"


@requires_gt
@pytest.mark.gt_all17
def test_new_file_change_surface_records_correct_quiet_execution(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("GT_RL_PROFILE", "2")
    apply_profile_env()
    monkeypatch.setenv("GT_LOC_RESLOT", "0")
    (tmp_path / "base_terminal.py").write_text(
        "class BaseTerminal:\n    pass\n", encoding="utf-8"
    )
    db = ensure_index(str(tmp_path))
    if db is None:
        pytest.skip("gt-index binary unavailable")
    from gt_engine.bridge import GTBridge

    bridge = GTBridge(
        repo_root=str(tmp_path),
        graph_db=db,
        issue_text="Create headless_terminal.py with HeadlessTerminal.",
    )
    created = tmp_path / "headless_terminal.py"
    after = (
        "from base_terminal import BaseTerminal\n\n"
        "class HeadlessTerminal(BaseTerminal):\n    pass\n"
    )
    created.write_text(after, encoding="utf-8")

    output = bridge.enrich(
        "edit_file",
        {"path": str(created)},
        "created",
        False,
        edit_before=None,
        edit_after=after,
    )

    assert output == "created"
    from gt_engine.attribution import summarize_features

    summary = summarize_features(bridge._attribution.rows)
    for feature_id in ("newfile_precedent", "GT_CHANGE_SURFACE"):
        assert summary[feature_id]["status"] == "INELIGIBLE"
        assert summary[feature_id]["reasons"] == [
            "producer_abstained_correct_quiet"
        ]


# --------------------------------------------------------------------------- #
# FIX 3: bash-mediated edit bridges (production _gateway_edit_bridges port)
# --------------------------------------------------------------------------- #
def test_bash_edit_target_shapes():
    assert bash_edit_target("sed -i 's/a/b/' pkg/alpha.py") == "pkg/alpha.py"
    assert bash_edit_target("cat > src/x.js <<'EOF'\ncode\nEOF") == "src/x.js"
    assert bash_edit_target("echo hi >> pkg/mod.py") == "pkg/mod.py"
    assert bash_edit_target(
        "python -c \"open('pkg/z.py','w').write('x')\"") == "pkg/z.py"
    assert bash_edit_target(
        "git apply <<'EOF'\n--- a/pkg/y.py\n+++ b/pkg/y.py\n@@\nEOF") == "pkg/y.py"
    assert bash_edit_target("grep -rn helper .") is None
    assert bash_edit_target("cat pkg/alpha.py") is None
    # A heredoc BODY line must not read as a redirect target.
    assert bash_edit_target(
        "cat > /tmp/t.txt <<'EOF'\n> fake.py\nEOF") is None


def test_bash_edit_targets_include_multiple_structured_artifacts():
    cmd = (
        "python3 <<'PY'\n"
        "open('output/plan_a.jsonl', 'w').write('a')\n"
        "open('output/plan_b.jsonl', 'w').write('b')\n"
        "PY"
    )

    assert bash_edit_targets(cmd) == (
        "output/plan_a.jsonl",
        "output/plan_b.jsonl",
    )
    assert bash_edit_target(cmd) == "output/plan_a.jsonl"


@requires_gt
def test_bash_edit_bridges_round_trip(indexed_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GT_GATEWAY_EDIT_BRIDGES", "1")
    b = indexed_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    pre_content = alpha.read_text(encoding="utf-8")
    args = {"command": "sed -i 's/return x + y/return x+y+0/' pkg/alpha.py"}
    b.capture_bash_preimage(args)
    assert b._bash_preimages == {"pkg/alpha.py": pre_content}
    # simulate the dispatched edit
    alpha.write_text(pre_content.replace("return x + y", "return x+y+0"),
                     encoding="utf-8")
    changed, eba = b._bash_bridges(args["command"])
    assert changed == ("pkg/alpha.py",)
    assert eba == {"pkg/alpha.py": (pre_content,
                                    alpha.read_text(encoding="utf-8"))}


@requires_gt
def test_bash_edit_bridges_creation_before_is_none(indexed_repo, tmp_path,
                                                   monkeypatch):
    monkeypatch.setenv("GT_GATEWAY_EDIT_BRIDGES", "1")
    b = indexed_repo
    args = {"command": "cat > pkg/newmod.py <<'EOF'\nX = 1\nEOF"}
    b.capture_bash_preimage(args)
    assert b._bash_preimages == {"pkg/newmod.py": None}  # positive creation
    (tmp_path / "pkg" / "newmod.py").write_text("X = 1\n", encoding="utf-8")
    changed, eba = b._bash_bridges(args["command"])
    assert changed == ("pkg/newmod.py",)
    assert eba == {"pkg/newmod.py": (None, "X = 1\n")}


@requires_gt
def test_structured_artifact_edit_can_drive_unresolved_red_submit_gate(
    indexed_repo,
    tmp_path,
    monkeypatch,
):
    import groundtruth.runtime.patterns as runtime_patterns

    monkeypatch.setenv("GT_GATEWAY_EDIT_BRIDGES", "1")
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    # The formal-runner classifier may label an ad-hoc Python assertion as an
    # environment failure. Its explicit AssertionError/exit status still gives
    # the bridge an unambiguous executed RED.
    monkeypatch.setattr(
        runtime_patterns,
        "classify_test_observation",
        lambda *_args: ("env_fail", None),
    )
    b = indexed_repo
    cmd = (
        "python3 <<'PY'\n"
        "open('output/plan_a.jsonl', 'w').write('a')\n"
        "open('output/plan_b.jsonl', 'w').write('b')\n"
        "PY"
    )
    args = {"command": cmd}
    b.capture_bash_preimage(args)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "plan_a.jsonl").write_text("a", encoding="utf-8")
    (output_dir / "plan_b.jsonl").write_text("b", encoding="utf-8")

    b.enrich("bash", args, "", False)

    assert b.edited_files == [
        "output/plan_a.jsonl",
        "output/plan_b.jsonl",
    ]
    b.enrich(
        "bash",
        {
            "command": (
                "python3 <<'PY'\n"
                "assert metric <= limit, 'performance threshold'\n"
                "PY"
            )
        },
        (
            "AssertionError: performance threshold for "
            "output/plan_b.jsonl\n[exit code 1]"
        ),
        True,
    )
    assert b._observed_red is not None
    refusal = b.submit_probe()
    assert refusal is not None
    assert "never re-run green" in refusal


@requires_gt
def test_explicit_true_self_check_clears_red_but_passive_view_does_not(
    indexed_repo,
    monkeypatch,
):
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    b = indexed_repo
    b.edited_files = ["output/plan.jsonl"]

    b.enrich(
        "bash",
        {"command": "python3 scripts/verify_plan.py"},
        "OVERALL PASS=False\noutput/plan.jsonl\n",
        False,
    )
    assert b._observed_red is not None

    b.enrich(
        "bash",
        {"command": "cat verification.log"},
        "OVERALL PASS=True\noutput/plan.jsonl\n",
        False,
    )
    assert b._observed_red is not None

    b.enrich(
        "bash",
        {"command": "python3 scripts/verify_plan.py"},
        (
            "candidate OK=False\ncandidate OK=True\n"
            "OVERALL PASS=True\noutput/plan.jsonl\n"
        ),
        False,
    )
    assert b._observed_red is None


@requires_gt
def test_zero_exit_explicit_false_self_check_drives_submit_gate(
    indexed_repo,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    b = indexed_repo
    _edit_alpha(b, tmp_path, "candidate")

    b.enrich(
        "bash",
        {"command": "cd /app && python3 pkg/alpha.py"},
        (
            "PASS: Cost=True, Pad=True, P95=True, Seq=False\n"
            "Status: Cost=PASS, Pad=PASS, P95=PASS, Seq=FAIL\n"
            "artifact generated by pkg/alpha.py"
        ),
        False,
    )

    assert b._observed_red is not None
    refusal = b.submit_probe()
    assert refusal is not None
    assert "never re-run green" in refusal


@requires_gt
def test_bash_edit_bridges_off_by_flag(indexed_repo):
    b = indexed_repo  # fixture sets only GT_GATEWAY/NATIVE - bridges flag off
    b.capture_bash_preimage({"command": "sed -i 's/a/b/' pkg/alpha.py"})
    assert b._bash_preimages == {}
    assert b._bash_bridges("sed -i 's/a/b/' pkg/alpha.py") == ((), None)


@requires_gt
def test_bash_edit_fires_edit_producer_end_to_end(indexed_repo, tmp_path):
    """A sed edit (no edit_file tool) reaches the edit-turn producers: the
    bridges reconstruct changed_files + before/after and patch_delta fires."""
    apply_profile_env()  # profile-2: GT_GATEWAY_EDIT_BRIDGES + GT_PATCH_DELTA
    b = indexed_repo
    b.issue_text = "helper returns the wrong sum"
    cmd = ("sed -i 's/def helper(x, y):/def helper(x):/; "
           "s/return x + y/return x/' pkg/alpha.py")
    b.capture_bash_preimage({"command": cmd})
    alpha = tmp_path / "pkg" / "alpha.py"
    src = alpha.read_text(encoding="utf-8")
    alpha.write_text(src.replace("def helper(x, y):", "def helper(x):")
                     .replace("return x + y", "return x"), encoding="utf-8")
    out = b.enrich("bash", {"command": cmd}, "", False)
    assert [d.evidence_type for d in b.deliveries] == ["signature_mismatch"]
    assert "helper()" in out  # the arity diagnostic reached the observation


@requires_gt
def test_bash_edit_enrich_records_edited_file(indexed_repo, monkeypatch):
    monkeypatch.setenv("GT_GATEWAY_EDIT_BRIDGES", "1")
    b = indexed_repo
    args = {"command": "sed -i 's/x/y/' pkg/alpha.py"}
    b.capture_bash_preimage(args)
    b.enrich("bash", args, "", False)
    assert b.edited_files == ["pkg/alpha.py"]
    assert b._bash_preimages == {}  # consumed by the observation


# --------------------------------------------------------------------------- #
# FIX 2: gate-kernel submit probe (positive executed evidence only)
# --------------------------------------------------------------------------- #
@requires_gt
@pytest.mark.gt_all17
def test_submit_probe_blocks_on_real_syntax_error(indexed_repo, tmp_path):
    (tmp_path / "pkg" / "broken.py").write_text("def oops(:\n    pass\n",
                                                encoding="utf-8")
    b = indexed_repo
    b.edited_files.append("pkg/broken.py")
    nudge = b.submit_probe()
    assert nudge is not None
    assert nudge.startswith("pre-commit hook failed:")  # native refusal form
    assert "SyntaxError" in nudge
    assert "pkg/broken.py" in nudge          # repo-relative, the agent's own file
    assert "<gt-" not in nudge.lower()
    # Sealed as a delivery so the audit sees it.
    sealed = b.deliveries[-1]
    assert sealed.evidence_type == "syntax_result"
    assert sealed.receipt_state == "delivered"
    assert sealed.rendered_bytes_hash
    assert b.chain_head


@requires_gt
def test_submit_probe_quiet_on_clean_or_unedited(indexed_repo):
    b = indexed_repo
    assert b.submit_probe() is None          # nothing edited: clean allow
    census = [
        row for row in b._attribution.rows
        if row["event_type"] == "run.feature_census"
    ]
    assert len(census[-1]["payload"]["features"]) == 17
    assert {
        item["feature_id"] for item in census[-1]["payload"]["features"]
    } == {
        "caller_contract", "covering_red", "def_partition", "localization",
        "newfile_precedent", "obligations", "recovery", "signature_delta",
        "submit_refusal", "syntax_result", "GT_CERT_DELIVERY",
        "GT_CHANGE_SURFACE", "GT_EDIT_CHECK", "GT_HYPOTHESIS",
        "GT_LOC_RESLOT", "GT_PATCH_DELTA", "GT_SS_SUBMIT_RED",
    }
    b.edited_files.append("pkg/alpha.py")    # syntactically fine
    assert b.submit_probe() is None


@requires_gt
def test_submit_probe_remains_authoritative_for_nano_pushback_budget(
        indexed_repo, tmp_path):
    (tmp_path / "pkg" / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    b = indexed_repo
    b.edited_files.append("pkg/broken.py")
    assert b.submit_probe() is not None
    assert b.submit_probe() is not None
    assert b.submit_probe() is not None
    refusal_hashes = [
        item.rendered_bytes_hash
        for item in b.deliveries
        if item.evidence_type in {"submit_refusal", "syntax_result"}
    ]
    assert len(refusal_hashes) == 3
    assert len(set(refusal_hashes)) == 3
    assert b.submit_probe() is None  # bounded by nano's three-pushback policy


@requires_gt
def test_submit_blocks_positive_numpy2_removed_alias(indexed_repo, tmp_path):
    source = tmp_path / "pkg" / "compat.py"
    source.write_text(
        "import numpy as np\nDTYPE = np.int\n",
        encoding="utf-8",
    )
    b = indexed_repo
    b.issue_text = "Make this package compatible with NumPy >=2.0."
    b.edited_files.append("pkg/compat.py")

    refusal = b.submit_probe()

    assert refusal is not None
    assert "NumPy >=2 incompatible aliases remain" in refusal
    assert "pkg/compat.py: np.int" in refusal
    assert b.deliveries[-1].evidence_type == "submit_refusal"


# --------------------------------------------------------------------------- #
# FIX 4: task-start capsule (v1r brief) against the real fixture graph.db
# --------------------------------------------------------------------------- #
@requires_gt
def test_task_start_capsule_fires_and_seals(indexed_repo):
    pytest.importorskip("numpy")  # v1r brief hard-requires numpy upstream
    apply_profile_env()  # profile-2: native/minimal brief form
    b = indexed_repo
    b.issue_text = ("helper in pkg/alpha.py returns the wrong sum; "
                    "caller_a breaks when helper drops an argument")
    cap = b.task_start()
    assert cap is not None
    assert cap.startswith("Requirements to satisfy (from the issue):")
    assert "helper" in cap
    assert "<gt-" not in cap.lower()         # frame unwrapped, no tag leaks
    assert len(cap) <= 4000                  # law 8 budget
    sealed = b.deliveries[-1]
    assert sealed.evidence_type == "obligations"
    assert sealed.receipt_state == "delivered"
    assert b.chain_head


@requires_gt
def test_task_start_delivers_one_compound_orientation_with_localization_receipts(
        indexed_repo):
    """Step 0 must expose obligations and ranked graph locations as one block."""
    from types import SimpleNamespace

    from gt_engine.attribution import summarize_features

    apply_profile_env()
    bridge = indexed_repo
    bridge.issue_text = (
        "Fix helper in pkg/alpha.py while keeping caller_a compatible."
    )

    capsule = bridge.task_start()

    assert capsule is not None
    assert "Ranked work surface" in capsule
    assert "pkg/alpha.py" in capsule
    assert "helper" in capsule
    assert len(bridge.deliveries) == 1
    assert bridge.deliveries[0].evidence_type == "obligations"

    messages = [{"role": "user", "content": "task\n\n" + capsule}]
    view = bridge.provider_message_view(messages, char_budget=8_000)
    delivery_ids = bridge.trace_provider_request(
        1,
        "openai.chat.completions",
        {"model": "deepseek-v4-flash", "messages": view},
    )
    bridge.trace_model_response(
        1,
        SimpleNamespace(
            text="I will inspect the ranked helper first.",
            tool_calls=(),
            stop_reason="tool_use",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                cache_read_tokens=0,
            ),
        ),
        delivery_ids,
    )

    summary = summarize_features(bridge._attribution.rows)
    assert summary["obligations"]["status"] == "WITNESSED"
    assert summary["localization"]["status"] == "WITNESSED"
    assert summary["GT_LOC_RESLOT"]["status"] == "WITNESSED"
    assert summary["obligations"]["deliveries"] == ["0"]
    assert summary["localization"]["deliveries"] == ["0"]
    assert summary["GT_LOC_RESLOT"]["deliveries"] == ["0"]


@requires_gt
def test_graph_projection_uses_task_symbols_and_relationship_surfaces(indexed_repo):
    from gt_engine.graph_context import (
        build_graph_projection,
        graph_surface_receipt,
    )
    from gt_engine.task_contract import extract_task_contract

    contract = extract_task_contract(
        "Implement helper so caller_a remains compatible with the result."
    )
    projection = build_graph_projection(indexed_repo.graph_db, contract)
    receipt = graph_surface_receipt(indexed_repo.graph_db)

    assert receipt["available"] is True
    assert receipt["surfaces"]["nodes"] > 0
    assert projection.node_ids
    assert "pkg/alpha.py" in projection.files
    assert "helper" in projection.symbols
    assert any(
        fact.surface in {
            "nodes_fts", "symbol_content_fts", "content_passages_fts"
        }
        and fact.revision == projection.revision
        and ("helper" in fact.value.lower() or fact.symbol == "helper")
        for fact in projection.semantic_facts
    )


@requires_gt
def test_provider_view_renders_jit_graph_evidence_and_receipts_checkpoint(
        indexed_repo):
    bridge = indexed_repo
    bridge.issue_text = "Update helper while keeping caller compatible."
    task_capsule = bridge.task_start()
    assert task_capsule
    messages = [{"role": "user", "content": "task\n\n" + task_capsule}]

    view = bridge.provider_message_view(messages, char_budget=8_000)
    rendered = bridge._message_text(view)

    assert "[jit graph evidence]" in rendered
    assert "pkg/alpha.py" in rendered
    assert "for=obl-" in rendered
    bridge.trace_provider_request(
        1,
        "openai.chat.completions",
        {"model": "deepseek-v4-flash", "messages": view},
    )
    request = [
        row for row in bridge._attribution.rows
        if row["event_type"] == "provider.request"
    ][-1]["payload"]
    assert request["checkpoint_sha256"]
    assert request["active_boundary"] == "task_start"
    assert request["context_policy"] == "gt.compact.v1"


@requires_gt
def test_checkpoint_names_unresolved_requirements_and_missing_artifacts(
        indexed_repo):
    from gt_engine.task_contract import Obligation, TaskContract
    from gt_engine.verification_contract import compile_obligation_predicates

    bridge = indexed_repo
    missing_scope = "/app/task_file/output_data/gt_test_missing_plan_b1.jsonl"
    obligation = Obligation(
        "obl-artifact",
        f"Generate the required file {missing_scope}.",
        "task",
        ("plan_b1.jsonl",),
    )
    bridge._task_contract = TaskContract("data_transform", (obligation,))
    bridge._obligation_predicates = compile_obligation_predicates(
        bridge._task_contract
    )
    bridge.iteration_budget = 100
    bridge._last_model_iteration = 49

    checkpoint = bridge._render_context_checkpoint()
    control = bridge.progress_control(50)

    assert missing_scope in checkpoint
    assert '"iterations_remaining":51' in checkpoint
    assert '"priority_unresolved"' in checkpoint
    assert control is not None
    assert "Required output artifacts are still absent" in control
    assert missing_scope in control
    assert bridge.progress_control(51) is None


@requires_gt
def test_progress_control_enters_finalization_once(indexed_repo):
    bridge = indexed_repo
    bridge.iteration_budget = 100

    first = bridge.progress_control(80)
    second = bridge.progress_control(81)

    assert first is not None
    assert "Finalization mode: 21 model request(s) remain" in first
    assert "Do not repeat searches" in first
    assert second is None
    rows = [
        row for row in bridge._attribution.rows
        if row["event_type"] == "progress.control_issued"
    ]
    assert rows[-1]["payload"]["mode"] == "finalization"


@requires_gt
def test_finalization_prioritizes_install_end_state_over_descriptive_clauses(
        indexed_repo):
    from gt_engine.task_contract import Obligation, TaskContract
    from gt_engine.verification_contract import compile_obligation_predicates

    bridge = indexed_repo
    obligations = tuple(
        Obligation(f"obl-{index}", f"Descriptive compatibility clause {index}.", "task")
        for index in range(6)
    ) + (
        Obligation(
            "obl-install",
            "Install the package from source to the system global Python environment.",
            "task",
        ),
    )
    bridge._task_contract = TaskContract("code_behavior", obligations)
    bridge._obligation_predicates = compile_obligation_predicates(
        bridge._task_contract
    )
    bridge.iteration_budget = 100

    control = bridge.progress_control(80)
    checkpoint = bridge._render_context_checkpoint()

    assert control is not None
    assert "Install the package" in control
    priority = json.loads(
        checkpoint.split("\n[jit graph evidence]", 1)[0]
    )["obligations"]["priority_unresolved"]
    assert priority[0]["id"] == "obl-install"


@requires_gt
def test_finalization_control_rejects_broad_views_but_allows_checks(
        indexed_repo):
    bridge = indexed_repo
    bridge.iteration_budget = 100
    assert bridge.progress_control(80)

    assert bridge.tool_control_reason(
        "read_file", {"path": "pkg/alpha.py"}
    )
    assert bridge.tool_control_reason(
        "bash", {"command": "grep -rn helper ."}
    )
    assert bridge.tool_control_reason(
        "bash", {"command": "python -m pytest -q"}
    ) is None
    assert bridge.tool_control_reason(
        "edit_file", {"path": "pkg/alpha.py", "old": "x", "new": "y"}
    ) is None
    bridge._recent_failure_paths = ("/repo/pkg/failing.py",)
    assert bridge.tool_control_reason(
        "read_file", {"path": "/repo/pkg/failing.py"}
    ) is None


@requires_gt
def test_task_start_abstains_without_issue_text(indexed_repo):
    b = indexed_repo
    b.issue_text = ""
    assert b.task_start() is None
    assert b.deliveries == []


@requires_gt
def test_submit_certificate_receives_obligation_coverage(
        indexed_repo, monkeypatch):
    import groundtruth.runtime.submit_gate as submit_gate

    captured: dict[str, object] = {}
    real = submit_gate.safe_build_certificate

    def capture(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    monkeypatch.setattr(submit_gate, "safe_build_certificate", capture)
    b = indexed_repo
    b.issue_text = "Implement helper. The result must remain compatible."
    assert b.task_start() is not None
    b.edited_files.append("pkg/alpha.py")

    b.submit_probe()

    obligations = captured.get("obligations")
    assert isinstance(obligations, dict)
    assert obligations["total"] >= 1
    assert obligations["unmet"]


@requires_gt
def test_submit_uses_graph_verification_plan(indexed_repo, monkeypatch):
    import groundtruth.runtime.verification_plan as verification_plan

    calls: list[tuple[str, ...]] = []
    real = verification_plan.build_verification_plan

    def capture(graph_db, repo_root, changed_entities, obligations=(), **kwargs):
        calls.append(tuple(obligations))
        return real(
            graph_db,
            repo_root,
            changed_entities,
            obligations,
            **kwargs,
        )

    monkeypatch.setenv("GT_VERIFICATION_PLAN", "1")
    monkeypatch.setattr(verification_plan, "build_verification_plan", capture)
    b = indexed_repo
    b.issue_text = "Implement helper. The result must remain compatible."
    assert b.task_start() is not None
    b.edited_files.append("pkg/alpha.py")

    b.submit_probe()

    assert calls
    assert calls[-1]


@requires_gt
def test_task_start_empty_brief_is_named_correct_quiet(indexed_repo, monkeypatch):
    from types import SimpleNamespace

    import groundtruth.pretask.v1r_brief as v1r_brief

    b = indexed_repo
    b.issue_text = "change behavior without any rankable repository surface"
    monkeypatch.setattr(
        v1r_brief,
        "generate_v1r_brief",
        lambda *_args, **_kwargs: SimpleNamespace(brief_text=""),
    )

    assert b.task_start() is None
    events = [
        row["payload"]
        for row in b._attribution.rows
        if row["event_type"] == "feature.evaluated"
        and row["payload"].get("feature_id") == "obligations"
    ]
    assert events[-1] == {
        "feature_id": "obligations",
        "eligible": False,
        "outcome": "brief_empty",
    }


def test_agent_prepends_task_start_capsule():
    class _StubBridge:
        issue_text = ""
        delivered_spans: list[Any] = []

        def task_start(self):
            return "CAPSULE"

    steps = [StepResult(text="hi", tool_calls=[], stop_reason="end_turn",
                        usage=_usage())]
    agent = Agent(provider=_ScriptedProvider(steps), system="s")
    agent._gt = _StubBridge()
    result = agent.run("the task")
    assert result.transcript[0]["content"] == "the task\n\nCAPSULE"


# --------------------------------------------------------------------------- #
# GT-off agent-loop smoke: stubbed provider, no API key, gt_root=None
# --------------------------------------------------------------------------- #
class _ScriptedProvider:
    """Provider stub: yields a scripted sequence of StepResults."""

    def __init__(self, steps):
        self._steps = iter(steps)

    def step(self, messages, tools, system):
        return next(self._steps)


class _ReceiptScriptedProvider(_ScriptedProvider):
    request_observer = None
    model = "deepseek-v4-flash"

    def step(self, messages, tools, system):
        if self.request_observer is not None:
            self.request_observer(
                "openai.chat.completions",
                {
                    "model": self.model,
                    "messages": [{"role": "system", "content": system}]
                    + list(messages),
                },
            )
        return super().step(messages, tools, system)


def _usage():
    return Usage(input_tokens=10, output_tokens=5, cache_read_tokens=0)


def test_agent_rechecks_gt_submit_after_clean_probe_and_later_tool_work():
    """A clean probe must not disarm GT after later repository work."""
    class _LateBlockBridge:
        issue_text = ""
        delivered_spans: list[Any] = []

        def __init__(self):
            self.probes = 0

        def task_start(self):
            return None

        def capture_bash_preimage(self, _args):
            return None

        def enrich(self, _name, _args, output, _is_error, **_kwargs):
            return output

        def submit_probe(self):
            self.probes += 1
            return "LATE BLOCK" if self.probes == 2 else None

    steps = [
        StepResult(text=None, tool_calls=[ToolCall(
            id="c1", name="bash", arguments={"command": "echo initial"})],
            stop_reason="tool_use", usage=_usage()),
        StepResult(text="done once", tool_calls=[], stop_reason="end_turn",
                   usage=_usage()),
        StepResult(text=None, tool_calls=[ToolCall(
            id="c2", name="bash", arguments={"command": "echo later-work"})],
            stop_reason="tool_use", usage=_usage()),
        StepResult(text="done twice", tool_calls=[], stop_reason="end_turn",
                   usage=_usage()),
        StepResult(text=None, tool_calls=[ToolCall(
            id="c3", name="bash", arguments={"command": "echo repaired"})],
            stop_reason="tool_use", usage=_usage()),
        StepResult(text="verified", tool_calls=[], stop_reason="end_turn",
                   usage=_usage()),
    ]
    bridge = _LateBlockBridge()
    agent = Agent(provider=_ScriptedProvider(steps), system="s")
    agent._gt = bridge

    result = agent.run("fix it")

    assert bridge.probes == 3
    assert any(m.get("content") == "LATE BLOCK" for m in result.transcript
               if m.get("type") == "user")
    assert result.stop_reason == "end_turn"


def test_agent_gt_off_smoke_end_to_end():
    steps = [
        StepResult(text=None, tool_calls=[ToolCall(
            id="c1", name="bash", arguments={"command": "echo nano-smoke"})],
            stop_reason="tool_use", usage=_usage()),
        StepResult(text="done", tool_calls=[], stop_reason="end_turn",
                   usage=_usage()),  # first done: challenged once
        StepResult(text=None, tool_calls=[ToolCall(
            id="c2", name="bash", arguments={"command": "echo verified"})],
            stop_reason="tool_use", usage=_usage()),
        StepResult(text="all done", tool_calls=[], stop_reason="end_turn",
                   usage=_usage()),
    ]
    agent = Agent(provider=_ScriptedProvider(steps), system="s")
    assert agent._gt is None  # gt_root defaulted: GT fully dormant
    result = agent.run("say hi")
    assert result.stop_reason == "end_turn"
    assert result.final_text == "all done"
    # GT-off byte identity: the initial user message is exactly the task
    # (no task-start capsule path touched it).
    assert result.transcript[0]["content"] == "say hi"
    tool_outputs = [t for t in result.transcript if t["type"] == "tool_result"]
    assert any("nano-smoke" in t["output"] for t in tool_outputs)


def test_agent_gt_root_on_non_code_dir_gets_dormant_bridge(tmp_path):
    """Phase-3 contract change: a non-code gt_root gets a DORMANT bridge
    (graph_db None, producers abstain) that can WAKE when the agent writes
    source files - it is no longer None-forever."""
    if not HAVE_GT:
        pytest.skip("groundtruth not installed")
    (tmp_path / "data.txt").write_text("no code here", encoding="utf-8")
    steps = [StepResult(text="hi", tool_calls=[], stop_reason="end_turn",
                        usage=_usage())]
    agent = Agent(provider=_ScriptedProvider(steps), system="s",
                  gt_root=str(tmp_path))
    assert agent._gt is not None
    assert agent._gt.graph_db is None      # dormant: no graph substrate
    result = agent.run("t")
    assert result.stop_reason == "end_turn"
    # Dormant bridge delivers nothing: the initial message is the raw task
    # (task_start abstains without a graph) and no evidence was sealed.
    assert result.transcript[0]["content"] == "t"
    assert agent._gt.deliveries == []
    trace_rows = [
        json.loads(line)
        for line in (tmp_path / ".gt" / "gt_attribution.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    assert any(row["event_type"] == "run.started" for row in trace_rows)
    assert any(row["event_type"] == "run.completed" for row in trace_rows)
    assert any(row["event_type"] == "run.feature_census" for row in trace_rows)


@requires_gt
@pytest.mark.gt_all17
def test_real_indexed_agent_proves_task_start_exposure(indexed_repo):
    from gt_engine.attribution import summarize_features, verify_trace_rows

    pytest.importorskip("numpy")
    steps = [StepResult(text="I will inspect helper.", tool_calls=[],
                        stop_reason="end_turn", usage=_usage())]
    agent = Agent(
        provider=_ReceiptScriptedProvider(steps),
        system="s",
        gt_root=indexed_repo.repo_root,
        verify=False,
    )

    result = agent.run(
        "helper in pkg/alpha.py returns the wrong sum; preserve its callers"
    )

    assert result.stop_reason == "end_turn"
    rows = agent._gt._attribution.rows
    assert verify_trace_rows(rows) == []
    features = summarize_features(rows)
    assert features["obligations"]["status"] == "WITNESSED"
    assert features["obligations"]["exposed"] is True
    assert features["obligations"]["response_observed"] is True


# --------------------------------------------------------------------------- #
# WIRE 1: L6 freshness - wake-from-dormant + reindex-after-edit (GT_L6_FRESH)
# --------------------------------------------------------------------------- #
def _node_count(db: str) -> int:
    import sqlite3
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    finally:
        con.close()


@requires_gt
def test_l6_wake_from_dormant_on_source_edit(tmp_path, monkeypatch):
    """A task that STARTS non-code becomes code: the dormant bridge wakes on
    the first source-file edit and the new module's symbols are in the graph."""
    monkeypatch.setenv("GT_GATEWAY", "1")
    monkeypatch.setenv("GT_GATEWAY_NATIVE", "1")
    monkeypatch.setenv("GT_L6_FRESH", "1")
    (tmp_path / "notes.txt").write_text("non-code at start", encoding="utf-8")
    from gt_engine import create_bridge
    b = create_bridge(str(tmp_path))
    if b is None:
        pytest.skip("gt-index binary unavailable")
    assert b.graph_db is None                       # dormant
    content = "def fresh_fn(a):\n    return a * 2\n"
    (tmp_path / "newmod.py").write_text(content, encoding="utf-8")
    out = b.enrich("edit_file", {"path": str(tmp_path / "newmod.py")},
                   "edited", False, edit_before=None, edit_after=content)
    assert out.startswith("edited")                 # never breaks the turn
    assert b.graph_db is not None                   # WOKE
    import sqlite3
    con = sqlite3.connect(b.graph_db)
    rows = con.execute(
        "SELECT name FROM nodes WHERE name='fresh_fn'").fetchall()
    con.close()
    assert rows == [("fresh_fn",)]                  # the agent's new code


@requires_gt
def test_l6_wake_rebuilds_task_projection_and_router(tmp_path, monkeypatch):
    """A graph wake publishes its db, projection, and router as one context."""
    monkeypatch.setenv("GT_GATEWAY", "1")
    monkeypatch.setenv("GT_GATEWAY_NATIVE", "1")
    monkeypatch.setenv("GT_L6_FRESH", "1")
    (tmp_path / "notes.txt").write_text("non-code at start", encoding="utf-8")
    from gt_engine import create_bridge

    b = create_bridge(str(tmp_path))
    if b is None:
        pytest.skip("gt-index binary unavailable")
    b.issue_text = (
        "Create newmod.py and implement fresh_fn so it returns twice its input."
    )
    assert b.task_start()
    assert b.graph_db is None
    assert b._graph_projection is not None
    assert not b._graph_projection.files

    content = "def fresh_fn(a):\n    return a * 2\n"
    path = tmp_path / "newmod.py"
    path.write_text(content, encoding="utf-8")
    b.enrich(
        "edit_file",
        {"path": str(path)},
        "edited",
        False,
        edit_before=None,
        edit_after=content,
    )

    assert b.graph_db is not None
    assert b._graph_projection is not None
    assert "newmod.py" in b._graph_projection.files
    assert b._evidence_router is not None
    assert "newmod.py" in b._evidence_router.graph_files


@requires_gt
def test_l6_reindex_after_edit_grows_graph(tmp_path, monkeypatch):
    """A second new module (calling the first) re-indexes: node count grows
    and the cross-file CALLS edge to the NEW symbol exists - the evidence the
    full-reindex decision buys (gt-index -file cannot mint new incoming edges)."""
    monkeypatch.setenv("GT_GATEWAY", "1")
    monkeypatch.setenv("GT_GATEWAY_NATIVE", "1")
    monkeypatch.setenv("GT_L6_FRESH", "1")
    from gt_engine import create_bridge
    (tmp_path / "newmod.py").write_text(
        "def fresh_fn(a):\n    return a * 2\n", encoding="utf-8")
    b = create_bridge(str(tmp_path))
    if b is None or b.graph_db is None:
        pytest.skip("gt-index binary unavailable")
    n0 = _node_count(b.graph_db)
    c2 = "from newmod import fresh_fn\n\n\ndef consumer(v):\n    return fresh_fn(v)\n"
    (tmp_path / "second.py").write_text(c2, encoding="utf-8")
    b.enrich("edit_file", {"path": str(tmp_path / "second.py")}, "edited",
             False, edit_before=None, edit_after=c2)
    n1 = _node_count(b.graph_db)
    assert n1 > n0                                  # graph re-indexed
    import sqlite3
    con = sqlite3.connect(b.graph_db)
    edges = con.execute(
        "SELECT s.name, t.name, e.type FROM edges e "
        "JOIN nodes s ON s.id=e.source_id JOIN nodes t ON t.id=e.target_id "
        "WHERE e.type='CALLS'").fetchall()
    con.close()
    assert ("consumer", "fresh_fn", "CALLS") in edges


@requires_gt
def test_l6_flag_off_never_wakes(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_GATEWAY", "1")
    monkeypatch.setenv("GT_GATEWAY_NATIVE", "1")
    # Explicit kill-switch (an unset flag would be fanned to "1" by the
    # Profile-2 defaults create_bridge applies; explicit user value wins).
    monkeypatch.setenv("GT_L6_FRESH", "0")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    from gt_engine import create_bridge
    b = create_bridge(str(tmp_path))
    if b is None:
        pytest.skip("groundtruth unavailable")
    content = "def f():\n    return 1\n"
    (tmp_path / "m.py").write_text(content, encoding="utf-8")
    b.enrich("edit_file", {"path": str(tmp_path / "m.py")}, "edited", False,
             edit_before=None, edit_after=content)
    assert b.graph_db is None                       # gated off: stays dormant


# --------------------------------------------------------------------------- #
# WIRE 2: executed covering-RED at post-edit + submit covering head
# --------------------------------------------------------------------------- #
@pytest.fixture
def covering_repo(tmp_path, monkeypatch):
    """An indexed repo whose OWN test file covers helper() through a FACT-tier
    import-resolved CALLS edge - the covering lane's selection substrate."""
    if not HAVE_GT:
        pytest.skip("groundtruth not installed")
    monkeypatch.setenv("GT_GATEWAY", "1")
    monkeypatch.setenv("GT_GATEWAY_NATIVE", "1")
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "alpha.py").write_text(
        "def helper(x, y):\n    return x + y\n\n\n"
        "def caller_a(v):\n    return helper(v, 1)\n", encoding="utf-8")
    (tmp_path / "test_alpha.py").write_text(
        "from pkg.alpha import helper\n\n\n"
        "def test_helper_sum():\n    assert helper(1, 2) == 3\n",
        encoding="utf-8")
    db = ensure_index(str(tmp_path))
    if db is None:
        pytest.skip("gt-index binary unavailable")
    from gt_engine.bridge import GTBridge
    return GTBridge(repo_root=str(tmp_path), graph_db=db)


def _break_helper(root, crash=True):
    alpha = root / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    repl = ("return x + y + undefined_name" if crash else "return x + y")
    after = before.replace("return x + y", repl)
    alpha.write_text(after, encoding="utf-8")
    return alpha, before, after


@requires_gt
@pytest.mark.gt_all17
def test_covering_red_fires_at_post_edit(covering_repo, tmp_path):
    """The TB-critical live fire: a real edit that breaks a covering test
    delivers the Format-D RED into the SAME post-edit observation, sealed,
    with ZERO test identity in the delivered bytes."""
    b = covering_repo
    alpha, before, after = _break_helper(tmp_path)
    out = b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
                   edit_before=before, edit_after=after)
    assert out.startswith("edited")                       # pure suffix
    delta = out[len("edited"):]
    assert "A covering test fails:" in delta              # Format-D head
    assert "undefined_name" in delta                      # the real signal
    assert "pkg/alpha.py" in delta                        # the where-to-fix
    # Leak law: no test identity, no GT tag, in the delivered bytes.
    from groundtruth.runtime.native_render import contains_gt_tag, contains_test_identity
    assert not contains_gt_tag(delta)
    assert not contains_test_identity(delta)
    assert "test_alpha" not in delta and "test_helper" not in delta
    # Sealed as THIS observation's one dose.
    assert [d.evidence_type for d in b.deliveries] == ["covering_verdict"]
    assert b.deliveries[0].receipt_state == "delivered"
    assert b.chain_head
    # The executed verdict is cached for the submit gate's covering head.
    assert b._last_covering is not None
    assert b._last_covering.get("verdict") == "fail"


@requires_gt
def test_covering_green_stays_quiet_and_rechecks_after_later_edit(
        covering_repo, tmp_path):
    """A green run stays quiet, but a later edit revalidates the file."""
    b = covering_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    after = before.replace("return helper(v, 1)", "return helper(v, 2)")
    alpha.write_text(after, encoding="utf-8")
    out = b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
                   edit_before=before, edit_after=after)
    assert "A covering test fails:" not in out
    assert b._last_covering is not None
    assert b._last_covering.get("verdict") == "pass"
    assert "pkg/alpha.py" in b._covering_fired
    # A second edit changes repository state and must execute again.
    calls: list[int] = []
    b._run_covering = (  # type: ignore[method-assign]
        lambda changed: (calls.append(1), (None, []))[1])
    out2 = b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
                    edit_before=after, edit_after=after)
    assert out2.startswith("edited")
    assert calls == [1]


@requires_gt
def test_covering_rechecks_each_edit_after_unavailable_or_pass(
        covering_repo, tmp_path):
    """Every edit is a new covering opportunity, whatever the prior verdict."""
    b = covering_repo
    alpha = tmp_path / "pkg" / "alpha.py"

    def _edit(n: int) -> None:
        before = alpha.read_text(encoding="utf-8")
        after = before + f"\n# attempt {n}\n"
        alpha.write_text(after, encoding="utf-8")
        b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
                 edit_before=before, edit_after=after)

    calls: list[str] = []
    b._run_covering = (  # type: ignore[method-assign]
        lambda changed: (calls.append("unavail"),
                         ({"verdict": "unavailable", "ran": []},
                          ["test_alpha.py"]))[1])
    _edit(1)
    assert calls == ["unavail"]
    assert "pkg/alpha.py" not in b._covering_fired   # fault: latch NOT set
    _edit(2)
    assert calls == ["unavail", "unavail"]           # bounded retry happened
    # An executed pass is cached for submit but cannot mute a later edit.
    b._run_covering = (  # type: ignore[method-assign]
        lambda changed: (calls.append("pass"),
                         ({"verdict": "pass", "ran": ["test_alpha.py"]},
                          ["test_alpha.py"]))[1])
    _edit(3)
    assert calls == ["unavail", "unavail", "pass"]
    assert "pkg/alpha.py" in b._covering_fired
    _edit(4)
    assert calls == ["unavail", "unavail", "pass", "pass"]


@requires_gt
def test_covering_off_by_flag(covering_repo, tmp_path, monkeypatch):
    monkeypatch.delenv("GT_VERIFY_EXECUTE", raising=False)
    b = covering_repo
    alpha, before, after = _break_helper(tmp_path)
    out = b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
                   edit_before=before, edit_after=after)
    assert "A covering test fails:" not in out
    assert b._last_covering is None                 # gate off: no execution


# --------------------------------------------------------------------------- #
# WIRE 3: CompletionCertificate delivery at the submit boundary
# --------------------------------------------------------------------------- #
@requires_gt
def test_submit_cert_block_on_covering_red(covering_repo, tmp_path, monkeypatch):
    """Broken edit -> the submit probe re-runs the covering head fresh (G-2:
    a cached FAIL is stale at submit) and delivers the NOT-CLEAN cert as the
    native per-head pre-commit block."""
    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    b = covering_repo
    alpha, before, after = _break_helper(tmp_path)
    b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
             edit_before=before, edit_after=after)
    nudge = b.submit_probe()
    assert nudge is not None
    assert nudge.startswith("pre-commit hook failed:")
    assert "run covering tests" in nudge            # the cert's covering head
    assert nudge.rstrip().endswith("commit aborted (exit 1)")
    assert "test_alpha" not in nudge                # leak law on the cert too
    assert "<gt-" not in nudge.lower()
    sealed = b.deliveries[-1]
    assert sealed.evidence_type == "submit_refusal"
    # The same positive covering RED remains authoritative within nano's
    # bounded pushback budget.
    second = b.submit_probe()
    assert second is not None
    assert "still unresolved after 1 prior refusal" in second


@requires_gt
def test_submit_cert_block_on_syntax_error(indexed_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    (tmp_path / "pkg" / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    b = indexed_repo
    b.edited_files.append("pkg/broken.py")
    nudge = b.submit_probe()
    assert nudge is not None
    assert "check syntax" in nudge                  # per-head cert line
    assert "Failed" in nudge
    assert b.deliveries[-1].evidence_type == "submit_refusal"


@requires_gt
def test_submit_cert_allow_never_blocks(indexed_repo, monkeypatch):
    """Clean episode + GT_CERT_DELIVERY on: the cert is head-derived and a
    clean head ALWAYS returns None - the cert can never invent a block."""
    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    b = indexed_repo
    b.edited_files.append("pkg/alpha.py")           # syntactically fine
    assert b.submit_probe() is None
    assert b.deliveries == []


@requires_gt
def test_sdlc_submit_refuses_edit_without_post_edit_verification(
        indexed_repo, tmp_path, monkeypatch):
    """Profile-enabled SDLC verification spends one advisory bounce when
    source was edited but no later behavioral check passed."""
    monkeypatch.setenv("GT_SDLC_VERIFY", "1")
    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    b = indexed_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    after = before + "\n# changed without a test\n"
    alpha.write_text(after, encoding="utf-8")
    b.enrich(
        "edit_file",
        {"path": str(alpha)},
        "edited",
        False,
        edit_before=before,
        edit_after=after,
    )

    nudge = b.submit_probe()

    assert nudge is not None
    assert "passing post-edit" in nudge
    assert b.deliveries[-1].evidence_type == "submit_refusal"
    rows = b._attribution.rows
    assert any(
        row["event_type"] == "lifecycle.checkpoint"
        and row["payload"].get("phase") == "verify"
        and row["payload"].get("outcome") == "missing_post_edit_verification"
        for row in rows
    )


@requires_gt
@pytest.mark.gt_all17
def test_post_edit_syntax_failure_delivers_immediately(
        indexed_repo, tmp_path):
    """A positive parser failure is delivered on the edit result itself; the
    model need not wait until its attempted final submission."""
    b = indexed_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    after = "def broken(:\n"
    alpha.write_text(after, encoding="utf-8")

    out = b.enrich(
        "edit_file",
        {"path": str(alpha)},
        "edited",
        False,
        edit_before=before,
        edit_after=after,
    )

    assert out.startswith("edited")
    assert "SyntaxError" in out
    assert b.deliveries[-1].evidence_type == "syntax_result"
    exposure = b.trace_provider_request(
        1,
        "openai.chat.completions",
        {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": out}],
        },
    )
    b.trace_model_response(
        1,
        StepResult(
            text="repairing syntax",
            tool_calls=[ToolCall(
                id="repair-1",
                name="edit_file",
                arguments={"path": str(alpha)},
            )],
            stop_reason="tool_use",
            usage=_usage(),
        ),
        exposure,
    )
    from gt_engine.attribution import summarize_features

    summary = summarize_features(b._attribution.rows)
    assert summary["syntax_result"]["status"] == "WITNESSED"
    assert summary["GT_EDIT_CHECK"]["status"] == "WITNESSED"


@requires_gt
def test_post_edit_syntax_suppression_does_not_mute_remaining_lanes(
        indexed_repo, tmp_path, monkeypatch):
    """A guarded syntax candidate spends no dose and must fall through."""
    b = indexed_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    after = before + "\n# changed\n"
    alpha.write_text(after, encoding="utf-8")
    monkeypatch.setattr(
        b,
        "_post_edit_syntax",
        lambda _changed: (
            "pkg/alpha.py",
            {"verdict": "syntax_error", "rendered": "candidate"},
        ),
    )
    monkeypatch.setattr(
        b, "_deliver_post_edit_syntax", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(b, "_covering_lane", lambda _changed: None)
    monkeypatch.setattr(
        b,
        "_deliver",
        lambda _cmd, output, _rc, **_kwargs: output + "\nFALLTHROUGH",
    )

    enriched = b.enrich(
        "edit_file",
        {"path": str(alpha)},
        "edited",
        False,
        edit_before=before,
        edit_after=after,
    )

    assert enriched.endswith("FALLTHROUGH")


@requires_gt
def test_sdlc_submit_allows_after_green_post_edit_test(
        indexed_repo, tmp_path, monkeypatch):
    """A formal green test after the latest edit satisfies the behavioral
    verification checkpoint; syntax still runs independently at submit."""
    monkeypatch.setenv("GT_SDLC_VERIFY", "1")
    b = indexed_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    after = before + "\n# changed then tested\n"
    alpha.write_text(after, encoding="utf-8")
    b.enrich(
        "edit_file",
        {"path": str(alpha)},
        "edited",
        False,
        edit_before=before,
        edit_after=after,
    )
    b.enrich(
        "bash",
        {"command": "python -m pytest -q"},
        "3 passed in 0.08s\n",
        False,
    )

    assert b.submit_probe() is None
    assert any(
        row["event_type"] == "lifecycle.checkpoint"
        and row["payload"].get("phase") == "verify"
        and row["payload"].get("outcome") == "post_edit_verification_green"
        for row in b._attribution.rows
    )


@requires_gt
def test_sdlc_lifecycle_records_research_pre_edit_post_edit_and_test(
        indexed_repo, tmp_path):
    """The lifecycle trace proves checkpoint execution even when canonical
    evidence producers correctly remain quiet."""
    b = indexed_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    b.pre_edit_checkpoint(
        "edit_file",
        {"path": str(alpha)},
        edit_before=before,
    )
    after = before + "\n# lifecycle edit\n"
    alpha.write_text(after, encoding="utf-8")
    b.enrich(
        "edit_file",
        {"path": str(alpha)},
        "edited",
        False,
        edit_before=before,
        edit_after=after,
    )
    b.enrich(
        "bash",
        {"command": "python -m pytest -q"},
        "3 passed in 0.08s\n",
        False,
    )

    checkpoints = {
        row["payload"].get("phase")
        for row in b._attribution.rows
        if row["event_type"] == "lifecycle.checkpoint"
    }
    assert {"pre_edit", "post_edit", "test"} <= checkpoints


@requires_gt
def test_lifecycle_research_only_records_repository_observation(indexed_repo):
    """A generic or test bash command must not counterfeit research."""
    b = indexed_repo

    b.enrich(
        "bash",
        {"command": "python -c \"print('hello')\""},
        "hello\n",
        False,
    )

    assert not any(
        row["event_type"] == "lifecycle.checkpoint"
        and row["payload"].get("phase") == "research"
        for row in b._attribution.rows
    )

    b.enrich(
        "bash",
        {"command": "rg -n \"helper\" pkg"},
        "pkg/alpha.py:1:def helper():\n",
        False,
    )

    assert any(
        row["event_type"] == "lifecycle.checkpoint"
        and row["payload"].get("phase") == "research"
        for row in b._attribution.rows
    )


@requires_gt
def test_bash_edit_records_pre_edit_checkpoint_before_dispatch(
        indexed_repo, tmp_path, monkeypatch):
    """The bash preimage seam is also the real pre-dispatch SDLC boundary."""
    monkeypatch.setenv("GT_GATEWAY_EDIT_BRIDGES", "1")
    b = indexed_repo
    alpha = tmp_path / "pkg" / "alpha.py"
    command = "sed -i 's/return x + y/return x-y/' pkg/alpha.py"

    b.capture_bash_preimage({"command": command})

    checkpoints = [
        row for row in b._attribution.rows
        if row["event_type"] == "lifecycle.checkpoint"
        and row["payload"].get("phase") == "pre_edit"
    ]
    assert len(checkpoints) == 1
    assert checkpoints[0]["action_index"] == 0
    assert checkpoints[0]["payload"]["tool_name"] == "bash"
    assert checkpoints[0]["payload"]["target_count"] == 1
    assert checkpoints[0]["payload"]["before_available"] is True
    assert alpha.read_text(encoding="utf-8") != "changed"


@requires_gt
def test_real_agent_sdlc_gate_requires_and_accepts_green_verification(
        indexed_repo, tmp_path, monkeypatch):
    """Actual Agent/provider/tool loop: read -> edit -> premature submit is
    refused once -> real pytest passes -> final submit is accepted."""
    monkeypatch.setenv("GT_SDLC_VERIFY", "1")
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    after = before + "\n# verified lifecycle\n"
    steps = [
        StepResult(
            text="inspect",
            tool_calls=[ToolCall(
                id="read-1",
                name="read_file",
                arguments={"path": str(alpha)},
            )],
            stop_reason="tool_use",
            usage=_usage(),
        ),
        StepResult(
            text="edit",
            tool_calls=[ToolCall(
                id="edit-1",
                name="edit_file",
                arguments={
                    "path": str(alpha),
                    "old": before,
                    "new": after,
                },
            )],
            stop_reason="tool_use",
            usage=_usage(),
        ),
        StepResult(
            text="done too early",
            tool_calls=[],
            stop_reason="end_turn",
            usage=_usage(),
        ),
        StepResult(
            text="verify",
            tool_calls=[ToolCall(
                id="test-1",
                name="bash",
                arguments={
                    "command": (
                        "python -c \"print('OVERALL PASS: True')\""
                    )
                },
            )],
            stop_reason="tool_use",
            usage=_usage(),
        ),
        StepResult(
            text="verified",
            tool_calls=[],
            stop_reason="end_turn",
            usage=_usage(),
        ),
    ]
    agent = Agent(
        provider=_ReceiptScriptedProvider(steps),
        system="s",
        gt_root=indexed_repo.repo_root,
    )

    result = agent.run("change helper without breaking its callers")

    assert result.stop_reason == "end_turn"
    assert any(
        item.get("type") == "user"
        and item.get("gt") == "submit_evidence"
        and "passing post-edit" in str(item.get("content"))
        for item in result.transcript
    )
    checkpoints = {
        row["payload"].get("phase")
        for row in agent._gt._attribution.rows
        if row["event_type"] == "lifecycle.checkpoint"
    }
    assert {
        "task_start", "research", "pre_edit", "post_edit",
        "test", "verify", "submit",
    } <= checkpoints
    from gt_engine.attribution import verify_lifecycle_rows

    assert verify_lifecycle_rows(agent._gt._attribution.rows) == []


@requires_gt
def test_observed_red_does_not_attribute_dependency_basename(indexed_repo):
    """A dependency frame sharing a basename with an edit is not edit proof."""
    b = indexed_repo
    b.edited_files.append("src/utils.py")

    b._track_observed_red(
        "pytest -q",
        "FAILED vendor/utils.py::test_other - AssertionError\n1 failed in 0.10s\n",
        1,
    )

    assert b._observed_red is None


@requires_gt
def test_submit_cert_fault_falls_back_to_plain_refusal(indexed_repo, tmp_path,
                                                       monkeypatch):
    """A poisoned cert renderer degrades to the existing native refusal -
    a real block is never silenced by a cert fault (correct-or-quiet)."""
    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    import groundtruth.runtime.native_render as nr
    monkeypatch.setattr(nr, "render_completion_cert_native",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    (tmp_path / "pkg" / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    b = indexed_repo
    b.edited_files.append("pkg/broken.py")
    nudge = b.submit_probe()
    assert nudge is not None
    assert "syntax_error" in nudge                  # the plain refusal form
    assert b.deliveries[-1].evidence_type == "syntax_result"


# --------------------------------------------------------------------------- #
# WIRE 6: on-disk delivery ledger (both-sides observability)
# --------------------------------------------------------------------------- #
def _read_ledger(root) -> list[dict[str, Any]]:
    import json
    p = root / ".gt" / "gt_ledger.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in
            p.read_text(encoding="utf-8").splitlines() if ln.strip()]


@requires_gt
def test_ledger_one_line_per_sealed_delivery(covering_repo, tmp_path,
                                             monkeypatch):
    """Ledger line count == len(bridge.deliveries) across a multi-delivery run
    (gateway dose + covering dose + submit refusal), each line joinable to its
    sealed envelope by rendered_bytes_hash; no wall clock, no payload bytes."""
    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    b = covering_repo
    b.enrich("bash", {"command": _AMBIGUOUS_GREP}, _GREP_OUT, False)  # gateway
    alpha, before, after = _break_helper(tmp_path)
    b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
             edit_before=before, edit_after=after)                   # covering
    b.submit_probe()                                                 # submit
    lines = _read_ledger(tmp_path)
    assert len(lines) == len(b.deliveries) >= 2
    by_hash = {ln["rendered_bytes_hash"]: ln for ln in lines}
    for sealed in b.deliveries:
        ln = by_hash[sealed.rendered_bytes_hash]     # 1:1 join (dose law)
        assert ln["evidence_type"] == sealed.evidence_type
        assert ln["dedup_key"] == sealed.dedup_key
        assert ln["len_shipped_chars"] > 0
        assert "timestamp" not in ln and "time" not in ln
        # Leak law applies to the ledger too: no payload, no test identity.
        assert "test_alpha" not in str(ln)


@requires_gt
def test_ledger_write_failure_never_unseals(indexed_repo, monkeypatch):
    b = indexed_repo
    monkeypatch.setattr(type(b), "_ledger_path",
                        lambda self: (_ for _ in ()).throw(RuntimeError()))
    out = b.enrich("bash", {"command": _AMBIGUOUS_GREP}, _GREP_OUT, False)
    assert len(out) > len(_GREP_OUT)                # delivery still happened
    assert len(b.deliveries) == 1                   # and stayed sealed


def test_ledger_absent_when_gt_off(tmp_path):
    steps = [StepResult(text="hi", tool_calls=[], stop_reason="end_turn",
                        usage=_usage())]
    agent = Agent(provider=_ScriptedProvider(steps), system="s")  # gt_root=None
    assert agent._gt is None
    agent.run("t")
    assert not (tmp_path / ".gt").exists()


# --------------------------------------------------------------------------- #
# FIX A: gt_deliveries.txt - verbatim shipped bytes alongside the ledger
# --------------------------------------------------------------------------- #
import re as _re  # noqa: E402

_DELIV_HDR = _re.compile(
    rb"--- event_id=(\S*) boundary=(\S+) evidence_type=(\S+) "
    rb"rendered_bytes_hash=([0-9a-f]{64}) ---\n")


def _read_deliveries(root) -> dict[str, dict[str, Any]]:
    """Parse gt_deliveries.txt into {rendered_bytes_hash: block}. Body = the
    exact bytes between the header newline and the trailing blank-line
    delimiter the writer appends (b'\\n\\n')."""
    p = root / ".gt" / "gt_deliveries.txt"
    if not p.exists():
        return {}
    data = p.read_bytes()
    hits = list(_DELIV_HDR.finditer(data))
    out: dict[str, dict[str, Any]] = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(data)
        body = data[m.end():end]
        assert body.endswith(b"\n\n")  # the framing blank line
        out[m.group(4).decode()] = {
            "event_id": m.group(1).decode(),
            "boundary": m.group(2).decode(),
            "evidence_type": m.group(3).decode(),
            "body": body[:-2],
        }
    return out


@requires_gt
def test_deliveries_file_joins_ledger_one_to_one(covering_repo, tmp_path,
                                                 monkeypatch):
    """Every ledger row has exactly one framed block whose body bytes hash to
    rendered_bytes_hash (law 6: the shipped bytes ARE the sealed bytes), with
    matching boundary/evidence_type - across gateway, covering and submit
    deliveries in one episode."""
    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    b = covering_repo
    b.enrich("bash", {"command": _AMBIGUOUS_GREP}, _GREP_OUT, False)  # gateway
    alpha, before, after = _break_helper(tmp_path)
    b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
             edit_before=before, edit_after=after)                   # covering
    b.submit_probe()                                                 # submit
    rows = _read_ledger(tmp_path)
    blocks = _read_deliveries(tmp_path)
    assert len(rows) == len(blocks) == len(b.deliveries) >= 2
    for ln in rows:
        blk = blocks[ln["rendered_bytes_hash"]]      # 1:1 join by hash
        assert hashlib.sha256(blk["body"]).hexdigest() == ln["rendered_bytes_hash"]
        assert blk["boundary"] == ln["boundary"]
        assert blk["evidence_type"] == ln["evidence_type"]
        assert blk["event_id"] == ln["event_id"]
    # The verbatim bytes are the EXACT shipped suffixes the bridge appended.
    shipped = {hashlib.sha256(
        s.text.encode("utf-8", "surrogatepass")).hexdigest(): s.text
        for s in b.delivered_spans}
    for h, txt in shipped.items():
        assert blocks[h]["body"].decode("utf-8") == txt


@requires_gt
def test_deliveries_file_write_failure_never_unseals(indexed_repo, monkeypatch,
                                                     tmp_path):
    """An unwritable ledger home (missing dir) silences BOTH files but the
    delivery still ships sealed - correct-or-quiet on the record path."""
    b = indexed_repo
    bad = str(tmp_path / "nodir" / "gt_ledger.jsonl")  # parent never created
    monkeypatch.setattr(type(b), "_ledger_path", lambda self: bad)
    out = b.enrich("bash", {"command": _AMBIGUOUS_GREP}, _GREP_OUT, False)
    assert len(out) > len(_GREP_OUT)                 # delivery still happened
    assert len(b.deliveries) == 1                    # and stayed sealed
    assert not (tmp_path / "nodir").exists()


def test_deliveries_file_absent_when_gt_off(tmp_path):
    steps = [StepResult(text="hi", tool_calls=[], stop_reason="end_turn",
                        usage=_usage())]
    agent = Agent(provider=_ScriptedProvider(steps), system="s")  # gt_root=None
    assert agent._gt is None
    agent.run("t")
    assert not (tmp_path / ".gt" / "gt_deliveries.txt").exists()


# --------------------------------------------------------------------------- #
# FIX B: recovery / GT_HYPOTHESIS lane (falsification recurrence -> one steer)
# --------------------------------------------------------------------------- #
_PYTEST_CMD = "python -m pytest -x"
_FAIL_X = ("=================== test session starts ===================\n"
           "FAILED test_math.py::test_helper_sum - AssertionError: assert 4 == 3\n"
           "1 failed in 0.12s\n"
           "[exit code 1]")
_FAIL_Y = ("=================== test session starts ===================\n"
           "FAILED test_math.py::test_caller - TypeError: caller_a() missing arg\n"
           "1 failed in 0.10s\n"
           "[exit code 1]")
_RECOVERY_STEER = ("The last edit did not change the failing result — form a "
                   "new hypothesis before editing again.")


def test_failure_fingerprint_normalization():
    """The scrub keeps genuinely-volatile numerics out of the key: path
    tokens, hex addresses, durations; a passing observation has no
    signature."""
    a = failure_fingerprint("FAILED pkg/x.py::t - AssertionError: assert 4 == 3")
    b = failure_fingerprint("FAILED src/y.py::t - AssertionError: assert 4 == 3")
    assert a and a == b                       # same failure, path drift only
    assert failure_fingerprint("5 passed in 0.01s") == ""
    assert failure_fingerprint("") == ""
    assert failure_fingerprint(_FAIL_X) != failure_fingerprint(_FAIL_Y)


def test_failure_fingerprint_discriminates_assertion_values():
    """W2-R6 fix: two DIFFERENT failing values must fingerprint DIFFERENTLY —
    `expected 5 got 3` -> edit -> `expected 5 got 4` is numeric PROGRESS, and
    a false-same key would deliver a false 'the edit changed nothing' steer.
    Volatile numerics (file:line locators, hex addresses, durations, `line N`
    traceback refs) stay scrubbed — flaky drift there must NOT read as
    progress (the failure mode the scrub existed for)."""
    a = failure_fingerprint(
        "FAILED test_m.py::t - AssertionError: expected 5 got 3\n[exit code 1]")
    b = failure_fingerprint(
        "FAILED test_m.py::t - AssertionError: expected 5 got 4\n[exit code 1]")
    assert a and b and a != b                 # value drift IS a state change
    # Guards on the scrub's original purpose — each pair must stay SAME:
    assert (failure_fingerprint("Error: boom at pkg/foo.py:12")
            == failure_fingerprint("Error: boom at pkg/foo.py:13"))
    assert (failure_fingerprint("Error: boom at foo.py:12:1")
            == failure_fingerprint("Error: boom at foo.py:13:7"))
    assert (failure_fingerprint('AssertionError at line 42 in helper')
            == failure_fingerprint('AssertionError at line 43 in helper'))
    assert (failure_fingerprint("fatal: segfault at 0xdeadbeef")
            == failure_fingerprint("fatal: segfault at 0xcafe12"))
    assert (failure_fingerprint("AssertionError: got 3\n1 failed in 0.12s")
            == failure_fingerprint("AssertionError: got 3\n1 failed in 0.87s"))


def _edit_alpha(b, tmp_path, marker: str):
    """A real intervening source edit through the bridge (records the edit
    index for the ledger's edit-between predicate)."""
    alpha = tmp_path / "pkg" / "alpha.py"
    before = alpha.read_text(encoding="utf-8")
    after = before + f"\n# {marker}\n"
    alpha.write_text(after, encoding="utf-8")
    b.enrich("edit_file", {"path": str(alpha)}, "edited", False,
             edit_before=before, edit_after=after)
    return alpha


@requires_gt
@pytest.mark.gt_all17
def test_recovery_fires_on_same_failure_recurring_across_edit(
        indexed_repo, tmp_path, monkeypatch):
    """LIVE FIRE: fail(X) -> edit -> fail(X) delivers ONE HYPOTHESIS-tier
    native steer as a sealed pure suffix on the recurrence observation."""
    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    b = indexed_repo
    out1 = b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    assert "form a new hypothesis" not in out1     # a FRESH failure never steers
    _edit_alpha(b, tmp_path, "attempt 1")
    out2 = b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    assert out2.startswith(_FAIL_X)                # pure suffix (TITO law 1)
    delta = out2[len(_FAIL_X):]
    assert _RECOVERY_STEER in delta                # the production imperative
    assert "<gt-" not in delta.lower()             # native, leak-guarded
    assert "test_math" not in delta                # no test identity
    sealed = b.deliveries[-1]
    assert sealed.evidence_type == "recovery"
    assert sealed.tier == "HYPOTHESIS"             # CAP: never [VERIFIED]
    assert sealed.receipt_state == "delivered"
    # Dose law: exactly ONE delivery rode this observation.
    assert [d for d in b.deliveries if d.event_id == sealed.event_id] == [sealed]
    # Ledger + deliveries-file record the steer (boundary "recovery").
    rows = [ln for ln in _read_ledger(tmp_path) if ln["boundary"] == "recovery"]
    assert len(rows) == 1
    assert rows[0]["rendered_bytes_hash"] == sealed.rendered_bytes_hash
    blk = _read_deliveries(tmp_path)[sealed.rendered_bytes_hash]
    assert _RECOVERY_STEER.encode("utf-8") in blk["body"]


@requires_gt
def test_recovery_quiet_on_different_failure(indexed_repo, tmp_path, monkeypatch):
    """fail(X) -> edit -> fail(Y): a DIFFERENT failure is progress evidence,
    never a falsification steer (quiet)."""
    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    b = indexed_repo
    b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    _edit_alpha(b, tmp_path, "attempt 1")
    out = b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_Y, True)
    assert out == _FAIL_Y
    assert not [d for d in b.deliveries if d.evidence_type == "recovery"]


@requires_gt
def test_recovery_quiet_without_intervening_edit(indexed_repo, monkeypatch):
    """fail(X) -> fail(X) with NO edit between is the unchanged-patch class
    (D_REQUEST_NEW_HYPOTHESIS) - deliberately NOT wired: quiet."""
    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    b = indexed_repo
    b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    out = b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    assert out == _FAIL_X
    assert not [d for d in b.deliveries if d.evidence_type == "recovery"]


@requires_gt
def test_repeated_no_gain_repository_action_gets_one_bounded_steer(
        indexed_repo, monkeypatch):
    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    bridge = indexed_repo
    first = bridge.enrich(
        "bash", {"command": "grep -rn missing_symbol ."}, "", False
    )
    second = bridge.enrich(
        "bash", {"command": "grep -rn missing_symbol ."}, "", False
    )
    third = bridge.enrich(
        "bash", {"command": "grep -rn missing_symbol ."}, "", False
    )

    assert "repeated without new information" not in first
    assert "repeated without new information" in second
    assert "repeated without new information" not in third
    controls = [
        item for item in bridge.deliveries
        if item.evidence_type == "recovery"
    ]
    assert len(controls) == 1


@requires_gt
def test_progress_interventions_are_globally_bounded_per_task(
        indexed_repo, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    bridge = indexed_repo
    outputs = []
    for index in range(4):
        candidate = bridge._progress_intervention(
            SimpleNamespace(
                current="STALLED",
                streak=2,
                signature=f"distinct-stall-{index}",
            ),
            classification="expected_negative_probe",
        )
        if candidate is not None:
            outputs.append(bridge._deliver_recovery("", *candidate))

    assert len(outputs) == 2
    assert all("repeated without new information" in item for item in outputs)
    assert len([
        item for item in bridge.deliveries
        if item.evidence_type == "recovery"
    ]) == 2


@requires_gt
def test_terminal_tool_result_never_seals_unexposable_delivery(
        indexed_repo, monkeypatch):
    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    bridge = indexed_repo
    command = "grep -rn terminal_missing ."
    bridge.enrich("bash", {"command": command}, "", False)

    output = bridge.enrich(
        "bash",
        {"command": command},
        "",
        False,
        can_request_follow=False,
    )

    assert output == ""
    assert not [
        item for item in bridge.deliveries
        if item.evidence_type == "recovery"
    ]
    assert any(
        row["event_type"] == "decision.committed"
        and row["payload"].get("reason") == "no_following_provider_budget"
        for row in bridge._attribution.rows
    )


@requires_gt
def test_recovery_fires_on_fresh_attributable_red_near_budget(
        indexed_repo, tmp_path, monkeypatch):
    """A first useful RED may steer near exhaustion without claiming that an
    intervening edit has already been falsified."""
    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    b = indexed_repo
    b.issue_text = "Implement helper and keep callers compatible."
    assert b.task_start()
    b.iteration_budget = 10
    b._last_model_iteration = 8
    _edit_alpha(b, tmp_path, "near-budget attempt")

    out = b.enrich("bash", {"command": _PYTEST_CMD}, _RED_TOUCH, True)

    assert "still failing near the iteration limit" in out
    assert b.deliveries[-1].evidence_type == "recovery"


@requires_gt
def test_recovery_quiet_on_nontest_failure_recurrence(indexed_repo, tmp_path,
                                                      monkeypatch):
    """The stall gate's genuine-test half: a recurring NON-test failure (a
    plain command error, no runner protocol) never steers."""
    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    b = indexed_repo
    err = "ERROR: cannot open config: parse failure\n[exit code 1]"
    b.enrich("bash", {"command": "./tool --check"}, err, True)
    _edit_alpha(b, tmp_path, "attempt 1")
    out = b.enrich("bash", {"command": "./tool --check"}, err, True)
    assert out == err
    assert not [d for d in b.deliveries if d.evidence_type == "recovery"]


@requires_gt
def test_recovery_once_per_signature_per_episode(indexed_repo, tmp_path,
                                                 monkeypatch):
    """After the steer fires for signature X, a THIRD recurrence of X (after
    yet another edit) stays quiet - the latch is per signature per episode."""
    monkeypatch.setenv("GT_HYPOTHESIS", "1")
    b = indexed_repo
    b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    _edit_alpha(b, tmp_path, "attempt 1")
    out2 = b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    assert _RECOVERY_STEER in out2                   # fired once
    _edit_alpha(b, tmp_path, "attempt 2")
    out3 = b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    assert out3 == _FAIL_X                           # latched: quiet
    assert len([d for d in b.deliveries
                if d.evidence_type == "recovery"]) == 1


@requires_gt
def test_recovery_flag_off_touches_no_state(indexed_repo, tmp_path):
    """GT_HYPOTHESIS unset: no failure memory fed, no episode edited_files
    mirror, no delivery - flag-off state identity."""
    b = indexed_repo
    b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    _edit_alpha(b, tmp_path, "attempt 1")
    out = b.enrich("bash", {"command": _PYTEST_CMD}, _FAIL_X, True)
    assert out == _FAIL_X
    assert b.episode.failure_fingerprints == set()
    assert b.episode.edited_files == {}
    assert not [d for d in b.deliveries if d.evidence_type == "recovery"]


@requires_gt
def test_recovery_is_profile_2_member():
    """GT_HYPOTHESIS is fanned out by the Profile-2 defaults (rl_profile:106),
    so the production GT arm runs this lane without extra knobs."""
    apply_profile_env()
    assert os.environ.get("GT_HYPOTHESIS") == "1"
    # FIX D's gate flag is a Profile-2 member too (rl_profile:260).
    assert os.environ.get("GT_SS_SUBMIT_RED") == "1"


# --------------------------------------------------------------------------- #
# FIX D: SS-2 observed-RED broad fallback at the submit boundary
# --------------------------------------------------------------------------- #
# A failing run whose traceback frame TOUCHES the edited surface (pkg/alpha.py)
# - production's relatedness rule reads only the agent's own strings.
_RED_TOUCH = ("FAILED test_math.py::test_helper_sum - AssertionError\n"
              '  File "pkg/alpha.py", line 2, in helper\n'
              "1 failed in 0.12s\n[exit code 1]")
_GREEN_TOUCH = "3 passed in 0.08s\n"


def _edit_and_fail(b, tmp_path, cmd=_PYTEST_CMD):
    _edit_alpha(b, tmp_path, "fix attempt")
    b.enrich("bash", {"command": cmd}, _RED_TOUCH, True)


@requires_gt
@pytest.mark.gt_all17
def test_submit_red_blocks_on_unresolved_observed_fail(indexed_repo, tmp_path,
                                                       monkeypatch):
    """LIVE FIRE: edit -> observed test FAIL on the edited surface -> submit
    is refused ONCE (native pre-commit form, agent's own command quoted),
    sealed as submit_refusal and remains authoritative within nano's bounded
    pushback budget."""
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    monkeypatch.setenv("GT_CERT_DELIVERY", "1")
    b = indexed_repo
    _edit_and_fail(b, tmp_path)
    nudge = b.submit_probe()
    assert nudge is not None
    assert nudge.startswith("pre-commit hook failed:")
    assert "never re-run green" in nudge
    assert "python -m pytest -x" in nudge      # the agent's OWN command echoed
    assert "test_math" not in nudge            # leak law on the rendered bytes
    assert "<gt-" not in nudge.lower()
    sealed = b.deliveries[-1]
    assert sealed.evidence_type == "submit_refusal"
    assert sealed.receipt_state == "delivered"
    from gt_engine.attribution import summarize_features
    summary = summarize_features(b._attribution.rows)
    assert summary["submit_refusal"]["status"] == "DELIVERED_UNEXPOSED"
    assert summary["GT_SS_SUBMIT_RED"]["status"] == "DELIVERED_UNEXPOSED"
    assert summary["GT_CERT_DELIVERY"]["status"] == "DELIVERED_UNEXPOSED"
    second = b.submit_probe()
    assert second is not None
    assert "still unresolved after 1 prior refusal" in second


@requires_gt
def test_submit_red_detail_degrades_when_command_names_a_test(
        indexed_repo, tmp_path, monkeypatch):
    """A test command that NAMES a test file still blocks, but the quoted
    detail degrades to the generic form - no test identity ships."""
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    b = indexed_repo
    _edit_and_fail(b, tmp_path, cmd="python -m pytest tests/test_math.py -x")
    nudge = b.submit_probe()
    assert nudge is not None
    assert "never re-run green" in nudge
    assert "test_math" not in nudge            # degraded, never leaked
    from groundtruth.runtime.native_render import contains_test_identity
    assert not contains_test_identity(nudge)


@requires_gt
def test_submit_red_quiet_when_no_test_ever_observed(indexed_repo, tmp_path,
                                                     monkeypatch):
    """Hidden verifier tests are invisible by design: an edit with NO
    agent-observed test run must never fire the fallback (GT only knows what
    the agent observed)."""
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    b = indexed_repo
    _edit_alpha(b, tmp_path, "fix attempt")
    assert b.submit_probe() is None
    assert b.deliveries == [] or all(
        d.evidence_type != "submit_refusal" for d in b.deliveries)


@requires_gt
def test_submit_red_quiet_after_rerun_green(indexed_repo, tmp_path, monkeypatch):
    """fail -> pass on the same edited surface clears the latch: quiet."""
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    b = indexed_repo
    _edit_and_fail(b, tmp_path)
    b.enrich("bash", {"command": "python -m pytest pkg/alpha.py"},
             _GREEN_TOUCH, False)              # touching PASS clears
    assert b.submit_probe() is None


@requires_gt
def test_submit_red_ignores_failure_on_unedited_surface(indexed_repo,
                                                        monkeypatch):
    """A pre-existing failure on an UNEDITED tree is not the agent's
    unresolved RED - the latch never sets (production's touch rule)."""
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    b = indexed_repo
    b.enrich("bash", {"command": _PYTEST_CMD}, _RED_TOUCH, True)  # no edits yet
    assert b._observed_red is None
    assert b.submit_probe() is None


@requires_gt
def test_submit_bounce_not_burned_by_suppressed_refusal(indexed_repo, tmp_path,
                                                        monkeypatch):
    """W2-R4 fix: a refusal SUPPRESSED by the seam guards (leak-tripping
    render here) is a silent allow and must NOT spend the bounded refusal
    budget. The next submit with a real block still refuses, and only shipped
    refusal text consumes that budget."""
    monkeypatch.setenv("GT_SS_SUBMIT_RED", "1")
    b = indexed_repo
    _edit_and_fail(b, tmp_path)
    import groundtruth.runtime.native_render as nr
    real_render = nr.render_submit_rejection
    # Probe 1: the rendered refusal trips contains_test_identity -> suppressed.
    monkeypatch.setattr(
        nr, "render_submit_rejection",
        lambda *a, **k: "FAILED tests/test_math.py::test_helper_sum")
    assert b.submit_probe() is None            # guard-suppressed: silent allow
    assert b.submit_bounces == 0               # ...and the bounce is NOT spent
    assert all(d.evidence_type != "submit_refusal" for d in b.deliveries)
    # Probe 2: renderer healthy again — the real block must still ship.
    monkeypatch.setattr(nr, "render_submit_rejection", real_render)
    nudge = b.submit_probe()
    assert nudge is not None
    assert "never re-run green" in nudge
    assert b.submit_bounces == 1               # spent exactly at the ship
    # Probe 3: the unresolved blocker remains authoritative while nano still
    # has pushback budget.
    assert b.submit_probe() is not None


@requires_gt
def test_submit_red_flag_off_never_fires(indexed_repo, tmp_path):
    """Latch set but GT_SS_SUBMIT_RED unset: the submit boundary stays quiet
    (default-off byte identity for the gate consumption)."""
    b = indexed_repo
    _edit_and_fail(b, tmp_path)
    assert b._observed_red is not None         # host-side latch tracked
    assert b.submit_probe() is None            # consumption is flag-gated
