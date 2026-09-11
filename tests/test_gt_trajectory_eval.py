"""Tests for scripts/gt_trajectory_eval.py - offline trajectory scorer.

All fixtures are synthetic trial dirs built in tmp_path mirroring the real
artifact layout:

    <trial>__XXXXX/
      config.json                     task.path -> tasks/<task_id>
      agent/miniswe_trajectory.json   {info, messages, trajectory_format}
      agent/gt-state/<id>/events.jsonl
      agent/gt-state/<id>/deliveries/*.json   (rendered GT_CONTEXT_UNIT text)
      agent/gt-state/<id>/graph.db            (nodes/edges sqlite)
      artifacts/model.patch
      verifier/reward.json

plus a tasks_root holding tasks/<task_id>/solution/solution.patch.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import gt_trajectory_eval as gte

TASK_ID = "demo-task"


# --------------------------------------------------------------------------
# fixture builders
# --------------------------------------------------------------------------
def _assistant_msg(command: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_x", "type": "function",
            "function": {"name": "bash",
                         "arguments": json.dumps({"command": command})},
        }],
        "extra": {"actions": [], "response": {}, "cost": 0.0,
                  "timestamp": 0.0},
    }


def _tool_msg(rc: int = 0, output: str = "ok") -> dict:
    return {
        "role": "tool",
        "content": f"<returncode>{rc}</returncode>\n<output>\n{output}\n</output>",
        "extra": {"raw_output": output, "returncode": rc, "timestamp": 0.0},
    }


def make_trajectory(commands: list[tuple[str, int] | tuple[str, int, str]]
                    ) -> dict:
    """commands: (command, returncode) or (command, returncode, tool_extra_text)."""
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "task"}]
    for item in commands:
        cmd, rc = item[0], item[1]
        extra_text = item[2] if len(item) > 2 else "ok"
        msgs.append(_assistant_msg(cmd))
        msgs.append(_tool_msg(rc, extra_text))
    return {"info": {}, "messages": msgs,
            "trajectory_format": "mini-swe-agent-1.1"}


def make_trial(tmp_path: Path, commands, *, events=(), deliveries=(),
               task_id: str = TASK_ID, reward: float = 1.0,
               with_graph: bool = False,
               model_patch: str | None = None) -> Path:
    trial = tmp_path / f"{task_id}__tRiaL1"
    (trial / "agent" / "gt-state" / "statedir").mkdir(parents=True)
    (trial / "artifacts").mkdir(parents=True)
    (trial / "verifier").mkdir(parents=True)
    (trial / "config.json").write_text(json.dumps(
        {"task": {"path": f"deepswe-bench/tasks/{task_id}"}}),
        encoding="utf-8")
    (trial / "agent" / "miniswe_trajectory.json").write_text(
        json.dumps(make_trajectory(commands)), encoding="utf-8")
    state = trial / "agent" / "gt-state" / "statedir"
    if events:
        (state / "events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8")
    if deliveries:
        ddir = state / "deliveries"
        ddir.mkdir(exist_ok=True)
        for did, body in deliveries:
            (ddir / f"{did}.json").write_text(body, encoding="utf-8")
    if with_graph:
        _make_graph(state / "graph.db")
    (trial / "verifier" / "reward.json").write_text(
        json.dumps({"reward": reward, "partial": reward}),
        encoding="utf-8")
    if model_patch is not None:
        (trial / "artifacts" / "model.patch").write_text(
            model_patch, encoding="utf-8")
    return trial


def _make_graph(path: Path) -> None:
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, file_path TEXT)")
    cur.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY, "
                "source_id INTEGER, target_id INTEGER)")
    # gold: src/core.py ; 1-hop neighbors: src/helper.py, tests/test_core.py
    cur.executemany("INSERT INTO nodes (id, file_path) VALUES (?, ?)",
                    [(1, "src/core.py"), (2, "src/helper.py"),
                     (3, "tests/test_core.py"), (4, "docs/readme.md")])
    cur.executemany("INSERT INTO edges (source_id, target_id) VALUES (?, ?)",
                    [(1, 2), (3, 1)])
    con.commit()
    con.close()


def make_tasks_root(tmp_path: Path) -> Path:
    root = tmp_path / "tasks"
    sol = root / TASK_ID / "solution"
    sol.mkdir(parents=True)
    (sol / "solution.patch").write_text(
        "diff --git a/src/core.py b/src/core.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/core.py\n"
        "+++ b/src/core.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8")
    return root


def delivery_blob(unit: str, action_index: int, kind: str,
                  text: str) -> str:
    hdr = json.dumps({"action_index": action_index, "historical": False,
                      "unit_id": unit})
    return f"[GT_CONTEXT_UNIT] {hdr}\n[GT_EVIDENCE:{kind}]\n{text}\n"


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
def test_ttfc_preedit_revisit_first_edit(tmp_path):
    tasks = make_tasks_root(tmp_path)
    commands = [
        ("cat docs/readme.md", 0),                    # non-core view
        ("cat src/helper.py", 0),                     # core view (1-hop)
        ("cat src/helper.py", 0),                     # revisit
        ("grep -n foo src/core.py", 0),               # view gold
        ("cat > src/core.py <<'EOF'\nnew\nEOF", 0),   # first edit (gold)
        ("pytest tests/test_core.py", 1),             # failing check
        ("pytest tests/test_core.py", 0),             # recovery
    ]
    trial = make_trial(tmp_path, commands, with_graph=True,
                       model_patch="diff --git a/src/core.py b/src/core.py\n"
                                   "--- a/src/core.py\n+++ b/src/core.py\n"
                                   "@@ -1,1 +1,1 @@\n-a\n+b\n")
    res = gte.evaluate_trial(trial, tasks)
    assert res["task_id"] == TASK_ID
    assert res["gold_files"] == ["src/core.py"]
    assert res["graph_neighborhood_available"] is True
    assert set(res["graph_neighborhood"]) == {"src/helper.py",
                                             "tests/test_core.py"}
    assert set(res["G_i"]) == {"src/core.py", "src/helper.py",
                              "tests/test_core.py"}
    assert res["ttfc"] == 2                      # src/helper.py view
    assert res["first_core_file"] == "src/helper.py"
    assert res["first_edit_step"] == 5
    assert res["first_edit_in_gold"] is True
    assert res["first_edit_in_G"] is True
    # viewed before edit: readme, helper, core = 3 files, 2 in G
    assert res["preedit_viewed"] == 3
    assert res["preedit_recall"] == pytest.approx(2 / 3)
    assert res["preedit_precision"] == pytest.approx(2 / 3)
    # 4 view targets total (readme, helper, helper, core); helper revisited
    assert res["revisit_rate"] == pytest.approx(1 / 4)
    assert res["revisits"] == 1
    assert res["view_file_events"] == 4
    assert res["steps_total"] == 7
    assert res["verifier_reward"] == 1.0
    assert res["model_patch_files"] == ["src/core.py"]
    assert res["timeline"]["solved"] is True
    rec = res["recovery"]
    assert rec["failed_targets"] == 1
    assert rec["recovered_targets"] == 1
    assert rec["recovery_rate"] == 1.0
    assert rec["steps_to_recovery_median"] == 1
    nc = res["non_core_exploration"]
    assert nc["non_core"] == 1                  # only readme.md view
    assert nc["exploration_actions_before_core"] == 1
    assert nc["ratio"] == 1.0


def test_led_vs_confirmed_split(tmp_path):
    """Deliveries that led somewhere new vs confirmed already-touched files."""
    tasks = make_tasks_root(tmp_path)
    # action 1 views src/seen.py (delivery A confirms it later);
    # delivery B points at src/fresh.py -> agent views it within K.
    commands = [
        ("cat src/seen.py", 0),
        ("ls -la", 0),
        ("cat src/fresh.py", 0),
        ("sed -n '1,5p' src/seen.py", 0),
    ]
    events = [
        {"event": "evidence_delivery", "delivery_identity": "d1",
         "kind": "caller_contract_view", "target": "src/seen.py",
         "action_index": 2, "iteration": 2, "delivery_ordinal": 1},
        {"event": "evidence_delivery", "delivery_identity": "d2",
         "kind": "caller_contract_view", "target": "src/fresh.py",
         "action_index": 1, "iteration": 1, "delivery_ordinal": 2},
        {"event": "evidence_delivery", "delivery_identity": "d3",
         "kind": "context_contract", "target": "provider_prompt",
         "action_index": 0, "iteration": 0, "delivery_ordinal": 3},
        {"event": "delivery_refused", "delivery_identity": "dX",
         "kind": "localization"},
    ]
    trial = make_trial(tmp_path, commands, events=events)
    res = gte.evaluate_trial(trial, tasks)
    u = res["gt_utilization"]
    assert u["deliveries_total"] == 3
    assert u["file_targeted"] == 2
    assert u["refused"] == 1
    # d2 anchored at action 2 (action_index 1 + 1): src/fresh.py not touched
    # before, viewed at action 3 -> led_new
    # d1 anchored at action 3: src/seen.py already touched at action 1
    # -> confirmed_only
    assert u["led_new"] == 1
    assert u["confirmed_only"] == 1
    assert u["acted_within_5"] == 2
    assert u["no_file_target"] == 1
    by_id = {d["delivery_id"]: d for d in u["deliveries"]}
    assert by_id["d2"]["bucket"] == "led_new"
    assert by_id["d2"]["previously_touched"] is False
    assert by_id["d2"]["acted_within_5"] is True
    assert by_id["d1"]["bucket"] == "confirmed_only"
    assert by_id["d1"]["previously_touched"] is True
    assert by_id["d3"]["bucket"] == "no_file_target"


def test_blob_only_deliveries_and_degenerate_action_index(tmp_path):
    """deliveries/*.json blobs without matching events; stubbed action_index
    falls back to locating [GT_EVIDENCE] markers inside tool observations."""
    tasks = make_tasks_root(tmp_path)
    blob_text = ("src/fresh.py:12: note: caller here\n"
                 "partner=src/alt.py count=2")
    commands = [
        ("cat src/seen.py", 0),
        ("ls", 0, "<gt-facts>\n[GT_EVIDENCE:cochange_partner]\n"
                  "src/seen.py: co-change partner=src/helper.py\n</gt-facts>"),
        ("cat src/helper.py", 0),
    ]
    events = [
        # stubbed action_index=1 on every delivery; iteration varies
        {"event": "evidence_delivery", "delivery_identity": "aa",
         "kind": "cochange_partner", "target": "src/seen.py",
         "action_index": 1, "iteration": 2, "delivery_ordinal": 1,
         "delivery_blob": "deliveries/aa.json"},
        {"event": "evidence_delivery", "delivery_identity": "bb",
         "kind": "select_catalog", "target": "src/missing.py",
         "action_index": 1, "iteration": 3, "delivery_ordinal": 2},
    ]
    trial = make_trial(tmp_path, commands, events=events,
                       deliveries=[("aa", delivery_blob(
                           "aa", 2, "cochange_partner", blob_text))])
    res = gte.evaluate_trial(trial, tasks)
    u = res["gt_utilization"]
    by_id = {d["delivery_id"]: d for d in u["deliveries"]}
    # blob header says delivered after action 2 -> anchor 3; seen.py and
    # helper.py (from blob text) both touched earlier/later
    assert by_id["aa"]["anchor"] == 3
    assert by_id["aa"]["anchor_source"] == "blob_header"
    assert by_id["aa"]["previously_touched"] is True   # seen.py at action 1
    # missing.py never touched -> not led
    assert by_id["bb"]["bucket"] in ("not_used", "confirmed_only")


def test_graceful_degradation_no_graph_no_deliveries(tmp_path):
    tasks = make_tasks_root(tmp_path)
    commands = [("cat src/core.py", 0), ("echo hi", 0)]
    trial = make_trial(tmp_path, commands)  # no events.jsonl, no graph.db
    res = gte.evaluate_trial(trial, tasks)
    assert res["graph_neighborhood_available"] is False
    assert res["graph_neighborhood"] is None
    assert "graph_db" in res["missing_inputs"]
    assert "events_jsonl" in res["missing_inputs"]
    assert res["G_i"] == ["src/core.py"]        # degrades to gold only
    assert res["ttfc"] == 1
    u = res["gt_utilization"]
    assert u["deliveries_total"] == 0
    assert u["led_new"] == 0
    assert res["recovery"]["check_executions"] == 0


def test_typed_events_preferred(tmp_path):
    """edit_transaction / execution_evidence typed events anchor metrics."""
    tasks = make_tasks_root(tmp_path)
    commands = [
        ("cat src/unrelated.py", 0),
        ("cat src/core.py", 0),
        ("python -c 'print(1)'", 0),     # no shell-visible write
        ("go test ./...", 1),
        ("go test ./...", 0),
    ]
    events = [
        {"event": "edit_transaction", "action_index": 3,
         "changed_paths": ["src/core.py"]},
        {"event": "execution_evidence", "action_id": 4, "kind": "test",
         "outcome": "fail", "returncode": 1, "command_sha256": "abc"},
        {"event": "execution_evidence", "action_id": 5, "kind": "test",
         "outcome": "pass", "returncode": 0, "command_sha256": "abc"},
    ]
    trial = make_trial(tmp_path, commands, events=events)
    res = gte.evaluate_trial(trial, tasks)
    assert res["first_edit_step"] == 3
    assert res["first_edit_source"] == "edit_transaction"
    assert res["first_edit_in_gold"] is True
    # typed check events: fail@4 -> pass@5 = recovery, no double count
    rec = res["recovery"]
    assert rec["check_executions"] == 2
    assert rec["recovered_targets"] == 1


def test_batch_mode(tmp_path):
    tasks = make_tasks_root(tmp_path)
    root = tmp_path / "run_root"
    for i, cmds in enumerate([
            [("cat src/core.py", 0)],
            [("cat src/other.py", 0), ("cat src/core.py", 0)]], 1):
        td = root / f"gt-harness-deepswe20-task-{i}-999"
        make_trial(td, cmds)
    res = gte.run_batch(root, tasks)
    assert res["schema"].endswith(".batch")
    assert len(res["tasks"]) == 2
    assert res["aggregate"]["n_tasks"] == 2
    assert res["aggregate"]["metrics"]["ttfc"]["n"] == 2
    assert "| task |" in res["markdown"]
    # deterministic ordering by task_id
    ids = [t["task_id"] for t in res["tasks"]]
    assert ids == sorted(ids)


def test_missing_gold_still_scores(tmp_path):
    root = tmp_path / "tasks"          # empty tasks root - no gold dir
    root.mkdir()
    trial = make_trial(tmp_path, [("cat a.py", 0)], task_id="ghost-task")
    res = gte.evaluate_trial(trial, root)
    assert res["gold_files"] == []
    assert "gold_solution_patch" in res["missing_inputs"]
    assert res["G_i"] == []
    assert res["ttfc"] is None           # no G_i to reach
    assert res["preedit_recall"] is None


def test_sed_i_and_apply_patch_edits(tmp_path):
    tasks = make_tasks_root(tmp_path)
    commands = [
        ("sed -i 's/a/b/' src/core.py", 0),
        ("apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: "
         "src/helper.py\n@@\nPATCH", 0),
    ]
    trial = make_trial(tmp_path, commands, with_graph=True)
    res = gte.evaluate_trial(trial, tasks)
    assert res["first_edit_step"] == 1
    assert "src/core.py" in res["first_edit_files"]
    assert res["first_edit_in_gold"] is True


# --------------------------------------------------------------------------
# funnel: SENT -> VISIBLE -> SERVED -> AGENT-DID -> CONSUMED
# --------------------------------------------------------------------------
def _assistant_msg_with_resp(command: str, resp_id: str) -> dict:
    m = _assistant_msg(command)
    m["extra"]["response"] = {"id": resp_id}
    return m


def make_traj_with_responses(commands: list[tuple[str, int]]) -> dict:
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "task"}]
    for i, (cmd, rc) in enumerate(commands, 1):
        msgs.append(_assistant_msg_with_resp(cmd, f"gen-{i}"))
        msgs.append(_tool_msg(rc, "ok"))
    return {"info": {}, "messages": msgs,
            "trajectory_format": "mini-swe-agent-1.1"}


def make_trial_with_provider(tmp_path: Path, commands, *, events, requests,
                             task_id: str = TASK_ID) -> Path:
    """requests: {iteration: messages_list} written to
    gt-state/statedir/provider_requests/req<it>.json and referenced by
    provider_delivery rows in events."""
    trial = tmp_path / f"{task_id}__tRiaL1"
    (trial / "agent" / "gt-state" / "statedir" / "provider_requests") \
        .mkdir(parents=True)
    (trial / "artifacts").mkdir(parents=True)
    (trial / "verifier").mkdir(parents=True)
    (trial / "config.json").write_text(json.dumps(
        {"task": {"path": f"deepswe-bench/tasks/{task_id}"}}),
        encoding="utf-8")
    (trial / "agent" / "miniswe_trajectory.json").write_text(
        json.dumps(make_traj_with_responses(commands)), encoding="utf-8")
    state = trial / "agent" / "gt-state" / "statedir"
    ev = list(events)
    for it, msgs in requests.items():
        (state / "provider_requests" / f"req{it}.json").write_text(
            json.dumps({"messages": msgs}), encoding="utf-8")
        ev.append({"event": "provider_delivery", "iteration": it,
                   "request_id": f"req-{it}",
                   "request_blob": f"provider_requests/req{it}.json"})
        ev.append({"event": "provider_response", "request_id": f"req-{it}",
                   "provider_response_id": f"gen-{it}", "iteration": it})
    (state / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in ev) + "\n", encoding="utf-8")
    (trial / "verifier" / "reward.json").write_text(
        json.dumps({"reward": 1.0}), encoding="utf-8")
    return trial


def _req_msgs(n_tool_msgs: int, gt_block: str | None = None,
              contract: bool = False) -> list:
    """Minimal request messages: system+user(+contract), assistant/tool
    pairs; last tool message carries gt_block when given."""
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user",
             "content": "task" + ("\n[GT_TASK_CONTRACT]\n- [ ] outcome"
                                 if contract else "")}]
    for i in range(n_tool_msgs):
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"function": {"arguments": "{}"}}]})
        body = f"obs{i}"
        if gt_block and i == n_tool_msgs - 1:
            body = f"<gt-facts>\n{gt_block}\n</gt-facts>\n{body}"
        msgs.append({"role": "tool", "content": body})
    return msgs


def test_funnel_led_and_confirmed(tmp_path):
    tasks = make_tasks_root(tmp_path)
    commands = [
        ("cat src/seen.py", 0),        # action 1 - pre-touches seen.py
        ("cat src/fresh.py", 0),       # action 2 - consumes d1 (led)
        ("sed -n '1,3p' src/seen.py", 0),  # action 3 - consumes d2 (confirmed)
        ("ls", 0),                     # action 4
    ]
    events = [
        # prompt-lane contract on the first request
        {"event": "context_addition_delivery", "delivery_identity": "dc",
         "kind": "context_contract", "target": "provider_prompt",
         "lane": "prompt", "action_index": 0, "iteration": 0,
         "delivery_ordinal": 1},
        # sealed delivery riding obs of action 1 -> visible to action 2
        {"event": "evidence_delivery", "delivery_identity": "d1",
         "kind": "caller_contract_view", "target": "src/fresh.py",
         "lane": "sealed", "action_index": 1, "iteration": 1,
         "delivery_ordinal": 2},
        # sealed delivery riding obs of action 2 -> visible to action 3
        {"event": "evidence_delivery", "delivery_identity": "d2",
         "kind": "caller_contract_view", "target": "src/seen.py",
         "lane": "sealed", "action_index": 1, "iteration": 2,
         "delivery_ordinal": 3},
    ]
    requests = {
        1: _req_msgs(1, contract=True),
        2: _req_msgs(2, "[GT_EVIDENCE:caller_contract_view]\n"
                        "src/fresh.py:9: note: callsite here"),
        3: _req_msgs(3, "[GT_EVIDENCE:caller_contract_view]\n"
                        "src/seen.py:4: note: caller here"),
        4: _req_msgs(4, "[GT_EVIDENCE:caller_contract_view]\n"
                        "src/seen.py:4: note: caller here"),
    }
    trial = make_trial_with_provider(tmp_path, commands, events=events,
                                     requests=requests)
    res = gte.evaluate_trial(trial, tasks)
    u = res["gt_utilization"]
    by_id = {d["delivery_id"]: d for d in u["deliveries"]}
    # d1: sent at anchor 2, marker absent from req1 -> marker_new,
    # response gen-2 produced action 2, command hits src/fresh.py,
    # never touched before -> led
    f1 = by_id["d1"]["funnel"]
    assert f1["sent"] and f1["visible"] and f1["served"] and f1["agent_did"]
    assert f1["visible_evidence"] == "marker_new"
    assert f1["response_action"] == 2
    assert f1["consumed"] and f1["prior_touches"] == 0
    assert by_id["d1"]["consumption_class"] == "led"
    # d2: same chain but src/seen.py was touched at action 1 -> confirmed
    f2 = by_id["d2"]["funnel"]
    assert f2["consumed"] and f2["prior_touches"] == 1
    assert by_id["d2"]["consumption_class"] == "confirmed"
    # three deliveries; all reach the agent's action boundary
    assert u["sent"] == 3 and u["served"] == 3 and u["agent_did"] == 3
    assert u["consumed_fair"] >= 1 and u["confirmed_consumed"] >= 1
    # prompt contract: consumed via contract token match at action 1
    fc = by_id["dc"]["funnel"]
    assert fc["visible"] and fc["visible_evidence"] in ("marker",
                                                      "marker_new")


def test_funnel_marker_any_and_not_visible(tmp_path):
    tasks = make_tasks_root(tmp_path)
    commands = [("cat a.py", 0), ("cat b.py", 0), ("cat c.py", 0)]
    events = [
        # right kind present but block never names the target -> marker_any
        {"event": "evidence_delivery", "delivery_identity": "dA",
         "kind": "caller_contract_view", "target": "src/other.py",
         "action_index": 1, "iteration": 1, "delivery_ordinal": 1},
        # request at anchor has no GT marker at all -> sent_not_visible
        {"event": "evidence_delivery", "delivery_identity": "dB",
         "kind": "trace_frame", "target": "src/t.py",
         "action_index": 1, "iteration": 2, "delivery_ordinal": 2},
    ]
    requests = {
        1: _req_msgs(1),
        2: _req_msgs(2, "[GT_EVIDENCE:caller_contract_view]\n"
                        "src/unrelated.py:1: note"),
        3: _req_msgs(3),
    }
    trial = make_trial_with_provider(tmp_path, commands, events=events,
                                     requests=requests)
    res = gte.evaluate_trial(trial, tasks)
    by_id = {d["delivery_id"]: d for d in res["gt_utilization"]["deliveries"]}
    assert by_id["dA"]["funnel"]["visible_evidence"] == "marker_any"
    assert by_id["dA"]["funnel"]["visible"] is True
    assert by_id["dB"]["funnel"]["verdict"] == "sent_not_visible"
    assert by_id["dB"]["funnel"]["visible"] is False


# --------------------------------------------------------------------------
# symbol-anchored gold closure
# --------------------------------------------------------------------------
def _make_symbol_graph(path: Path) -> None:
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, file_path TEXT, "
                "name TEXT, qualified_name TEXT, label TEXT, "
                "start_line INTEGER, end_line INTEGER, is_test BOOLEAN)")
    cur.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY, "
                "source_id INTEGER, target_id INTEGER, type TEXT)")
    # gold file src/core.py: function core_fn at lines 10-30 (patch hits
    # hunk +12,4 -> overlaps); other_fn at 40-60 untouched by patch but in
    # same file.  CALLS: core_fn -> helper_fn (src/helper.py);
    # test_core (is_test, tests/test_core.py) -> core_fn.
    # src/unrelated.py has nodes but no edges -> must NOT enter closure.
    cur.executemany(
        "INSERT INTO nodes (id, file_path, name, qualified_name, label, "
        "start_line, end_line, is_test) VALUES (?,?,?,?,?,?,?,?)",
        [(1, "src/core.py", "core_fn", "src.core.core_fn", "Function",
          10, 30, 0),
         (2, "src/core.py", "other_fn", "src.core.other_fn", "Function",
          40, 60, 0),
         (3, "src/helper.py", "helper_fn", "src.helper.helper_fn",
          "Function", 5, 15, 0),
         (4, "tests/test_core.py", "test_core", "tests.test_core",
          "Function", 1, 20, 1),
         (5, "src/unrelated.py", "unrelated", "u", "Function", 1, 5, 0)])
    cur.executemany(
        "INSERT INTO edges (source_id, target_id, type) VALUES (?,?,?)",
        [(1, 3, "CALLS"), (4, 1, "CALLS"), (2, 5, "CANDIDATE")])
    con.commit()
    con.close()


def make_symbol_trial(tmp_path: Path, commands, *, task_id: str = TASK_ID
                      ) -> Path:
    trial = tmp_path / f"{task_id}__tRiaL1"
    state = trial / "agent" / "gt-state" / "statedir"
    state.mkdir(parents=True)
    (trial / "artifacts").mkdir(parents=True)
    (trial / "verifier").mkdir(parents=True)
    (trial / "config.json").write_text(json.dumps(
        {"task": {"path": f"deepswe-bench/tasks/{task_id}"}}),
        encoding="utf-8")
    (trial / "agent" / "miniswe_trajectory.json").write_text(
        json.dumps(make_trajectory(commands)), encoding="utf-8")
    _make_symbol_graph(state / "graph.db")
    (trial / "verifier" / "reward.json").write_text(
        json.dumps({"reward": 1.0}), encoding="utf-8")
    return trial


def test_symbol_anchored_closure(tmp_path):
    # patch changes only lines 12-15 of src/core.py -> symbol core_fn is the
    # anchor; closure = callers/callees/tests of core_fn only.  other_fn's
    # CANDIDATE edge to unrelated.py is a bookkeeping edge - excluded.
    root = tmp_path / "tasks"
    sol = root / TASK_ID / "solution"
    sol.mkdir(parents=True)
    (sol / "solution.patch").write_text(
        "diff --git a/src/core.py b/src/core.py\n"
        "--- a/src/core.py\n+++ b/src/core.py\n"
        "@@ -10,6 +10,6 @@ def core_fn():\n"
        " ctx\n-old\n+new\n ctx2\n ctx3\n ctx4\n",
        encoding="utf-8")
    trial = make_symbol_trial(tmp_path, [("cat src/core.py", 0)])
    res = gte.evaluate_trial(trial, root)
    cl = res["gold_closure"]
    assert cl["symbol_level"] is True
    assert cl["changed_symbols"]["src/core.py"] == ["src.core.core_fn"]
    assert cl["files_detail"]["src/core.py"]["mode"] == "symbol"
    assert set(res["graph_neighborhood"]) == {"src/helper.py",
                                             "tests/test_core.py"}
    assert "src/unrelated.py" not in res["G_i"]
    assert set(res["G_i"]) == {"src/core.py", "src/helper.py",
                             "tests/test_core.py"}


def test_closure_file_fallback_no_symbol_match(tmp_path):
    # patch touches lines 200-210 - no symbol there -> file-level fallback
    # anchors every symbol in the file, so other_fn's CANDIDATE edge is
    # still filtered (bookkeeping type), but core_fn's CALLS reach helper.
    root = tmp_path / "tasks"
    sol = root / TASK_ID / "solution"
    sol.mkdir(parents=True)
    (sol / "solution.patch").write_text(
        "diff --git a/src/core.py b/src/core.py\n"
        "--- a/src/core.py\n+++ b/src/core.py\n"
        "@@ -200,4 +200,4 @@ tail\n-a\n+b\n c\n d\n",
        encoding="utf-8")
    trial = make_symbol_trial(tmp_path, [("cat src/core.py", 0)])
    res = gte.evaluate_trial(trial, root)
    assert res["gold_closure"]["files_detail"]["src/core.py"]["mode"] \
        == "file"
    assert "src/helper.py" in res["G_i"]
    assert "src/unrelated.py" not in res["G_i"]


# --------------------------------------------------------------------------
# splits, baseline, timeline
# --------------------------------------------------------------------------
def test_split_assignment_and_aggregate(tmp_path):
    tasks = make_tasks_root(tmp_path)
    root = tmp_path / "run_root"
    td = root / "gt-harness-deepswe20-task-1-999"
    make_trial(td, [("cat src/core.py", 0)])
    res = gte.run_batch(root, tasks, splits={
        "tuning": set(), "evaluation": {TASK_ID}})
    t = res["tasks"][0]
    assert t["split"] == "evaluation"
    assert res["aggregate"]["splits"]["evaluation"]["n_tasks"] == 1
    assert res["split_counts"]["evaluation"] == 1
    assert "held_out_note" in res


def test_timeline_steps_and_gt_attribution(tmp_path):
    tasks = make_tasks_root(tmp_path)
    commands = [
        ("cat docs/readme.md", 0),
        ("cat src/fresh.py", 0),
        ("cat src/core.py", 0),
    ]
    events = [
        {"event": "evidence_delivery", "delivery_identity": "d1",
         "kind": "caller_contract_view", "target": "src/fresh.py",
         "lane": "sealed", "action_index": 1, "iteration": 1,
         "delivery_ordinal": 1},
    ]
    requests = {
        1: _req_msgs(1),
        2: _req_msgs(2, "[GT_EVIDENCE:caller_contract_view]\n"
                        "src/fresh.py:9: note: callsite here"),
        3: _req_msgs(3),
    }
    trial = make_trial_with_provider(tmp_path, commands, events=events,
                                     requests=requests)
    res = gte.evaluate_trial(trial, tasks)
    steps = res["steps"]
    assert len(steps) == 3
    assert steps[0]["relevance"] == "non_core"   # readme not in G_i
    assert steps[1]["gt_delivered"]            # d1 anchored at action 2
    assert steps[1]["gt_consumed"]             # cat src/fresh.py consumes d1
    assert steps[1]["gt_consumed"][0]["class"] == "led"
    assert steps[2]["relevance"] == "gold"     # src/core.py is gold


def test_typical_path_similarity_distinct_label(tmp_path):
    tasks = make_tasks_root(tmp_path)
    trial = make_trial(tmp_path, [("cat src/core.py", 0),
                                 ("cat src/other.py", 0)])
    res = gte.evaluate_trial(
        trial, tasks,
        baseline_viewed={"src/core.py", "src/elsewhere.py"})
    sim = res["typical_path_similarity"]
    assert sim is not None and "_label" in sim
    assert "typical_path_similarity" in sim["_label"]
    # overlap: src/core.py ; union: core, other, elsewhere
    assert sim["overlap_files"] == 1
    assert sim["jaccard"] == pytest.approx(1 / 3)


def test_baseline_outcomes_smoke20_shape(tmp_path):
    tasks = make_tasks_root(tmp_path)
    base = tmp_path / "smoke20-x.json"
    base.write_text(json.dumps({
        "baseline": {"pass_rate": 0.5, "task_count": 2},
        "per_task": [{"task": TASK_ID, "baseline_passes": 3,
                      "baseline_trials": 4,
                      "baseline_four_trial_pass_rate": 0.75}]}),
        encoding="utf-8")
    per_task, meta, err = gte.load_baseline_outcomes(base)
    assert err is None
    assert per_task[TASK_ID]["gt_off_passes"] == 3
    assert per_task[TASK_ID]["gt_off_rate"] == 0.75
    assert meta["baseline_summary"]["pass_rate"] == 0.5
    # absent path degrades to error string, never a crash
    _, _, err2 = gte.load_baseline_outcomes(tmp_path / "nope.json")
    assert err2 and "not found" in err2


def test_load_splits_conflict_is_reported(tmp_path):
    spec = tmp_path / "splits.json"
    spec.write_text(json.dumps({"tuning": ["a"], "evaluation": ["a", "b"]}),
                    encoding="utf-8")
    splits, issues = gte.load_splits(str(spec), None, None)
    assert "a" in splits["tuning"] and "a" in splits["evaluation"]
    assert any("BOTH" in i for i in issues)
    assert gte.split_for("a", splits) == "conflict"
    assert gte.split_for("b", splits) == "evaluation"
    assert gte.split_for("c", splits) == "unassigned"


# --------------------------------------------------------------------------
# real-bundle smoke (skips cleanly when artifacts are absent)
# --------------------------------------------------------------------------
SMOKE_ROOT = Path(r"D:\gt_runs\33646776586")
SMOKE_TASKS = Path(r"D:\deepswe-bench-435ee89\tasks")


@pytest.mark.skipif(not SMOKE_ROOT.is_dir(),
                    reason="GT-on smoke bundle not present")
def test_real_bundle_ingests_one_task():
    task_dirs = sorted(p for p in SMOKE_ROOT.iterdir()
                       if p.is_dir() and "-task-" in p.name)
    assert task_dirs, "bundle has no task dirs"
    trials = []
    for td in task_dirs:
        trials = gte.find_trial_dirs(td)
        if trials:
            break
    assert trials, "no trial dir found in first task dir"
    res = gte.evaluate_trial(trials[0], SMOKE_TASKS)
    assert res["schema"] == gte.SCHEMA
    assert res["task_id"]
    assert res["steps_total"] and res["steps_total"] > 0
    assert isinstance(res["steps"], list) and res["steps"]
    assert {"step", "kinds", "relevance"} <= set(res["steps"][0])
    u = res["gt_utilization"]
    for k in ("deliveries_total", "sent", "visible", "served",
              "agent_did", "consumed", "consumed_fair", "led_new",
              "confirmed_only"):
        assert k in u
    assert res["gold_files"], "gold patch should resolve for a real task"
