from __future__ import annotations

import gt_engine.miniswe_runtime as rt
from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_evidence import EvidenceResult
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks
from gt_engine.task_contract import extract_task_contract


class FakeModel:
    def __init__(self):
        self.calls = []

    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in item.items() if k != "extra"} for item in messages]

    def query(self, messages, **kwargs):
        self.calls.append(messages)
        return {
            "role": "assistant",
            "content": "ok",
            "extra": {
                "actions": [],
                "response": {"model": "deepseek-v4-flash", "usage": {"prompt_tokens": 5}},
            },
        }

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [
            {
                "role": "tool",
                "content": f"<returncode>{out.get('returncode')}</returncode>\n"
                           f"<output>{out.get('output')}</output>",
                "tool_call_id": f"call-{index}",
            }
            for index, out in enumerate(outputs)
        ]


class FakeEnv:
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, action):
        self.executed.append(action.get("command", ""))
        return {"output": "ok", "returncode": 0}


class FakeAgent:
    def __init__(self):
        self.model = FakeModel()
        self.env = FakeEnv()
        self.messages: list[dict] = []

    def execute_actions(self, message):
        return []

    def add_messages(self, *messages):
        self.messages.extend(messages)
        return list(messages)

    def get_template_vars(self):
        return {}


class AlwaysSuppressBoundary:
    def authorize_submit_suppression(self, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(
            schema="gt.submit_suppression_receipt.v1",
            repository_revision=kwargs["current_revision"],
            action_sha256=__import__("hashlib").sha256(
                kwargs["action_bytes"]
            ).hexdigest(),
            provider_payload_sha256=__import__("hashlib").sha256(b"").hexdigest(),
            blocker_ids=("closed-red",),
            provider_dispatched=False,
            chars_delivered=0,
        )


def _session(adapter, mode=GTMode.ADVISORY):
    return GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=adapter.repo_root,
            state_dir=str(adapter.store.root.parent),
            mode=mode,
        ),
        engine=adapter,
    )


def test_runtime_hooks_capture_provider_payload_and_action(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    handle = install_runtime_hooks(agent, adapter)
    prepared = agent.model._prepare_messages_for_api([
        {"role": "user", "content": "task"},
    ])
    assert prepared[0]["content"].startswith("task")
    assert adapter.deliveries
    agent.execute_actions({"extra": {"actions": [{"cmd": "printf ok"}]}})
    assert handle.installed is True
    assert adapter.iteration == 1


def test_native_groundtruth_action_is_routed_without_shell_execution(tmp_path):
    (tmp_path / "mod.py").write_text("needle = 1\n", encoding="utf-8")
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t",
        state_dir=tmp_path / "state",
        predicates=[Predicate("p", "p")],
        repo_root=str(tmp_path),
    )
    install_runtime_hooks(agent, adapter)
    messages = agent.execute_actions(
        {
            "extra": {
                "actions": [
                    {
                        "tool_name": "groundtruth",
                        "tool_call_id": "gt-1",
                            "gt_action": {
                                "kind": "exact_literal_search",
                                "arguments": {"literal": "needle", "paths": ["."]},
                        },
                    }
                ]
            }
        }
    )
    assert agent.env.executed == []
    assert len(messages) == 1
    raw_payload = messages[0]["content"].split("<output>", 1)[1]
    payload = __import__("json").loads(raw_payload.rsplit("</output>", 1)[0])
    assert payload["decision"]["mode"] == "REPLACE"
    answer = payload["direct_answer"]
    matches = answer["matches"] if isinstance(answer, dict) else answer
    assert matches[0]["path"] == "mod.py"
    assert adapter._pending_typed_observations
    agent.model._prepare_messages_for_api([*agent.messages, *messages])
    assert adapter._pending_typed_observations == []
    rows = [
        __import__("json").loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    joined = [row for row in rows if row["event"] == "typed_observation_provider_join"]
    assert len(joined) == 1
    assert joined[0]["final_observation_sha256"]
    assert joined[0]["provider_payload_sha256"] == adapter.deliveries[-1].payload_sha256


def test_typed_query_lazily_refreshes_a_stale_graph(monkeypatch, tmp_path):
    (tmp_path / "mod.py").write_text("needle = 1\n", encoding="utf-8")
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t",
        state_dir=tmp_path / "state",
        predicates=[Predicate("p", "p")],
        repo_root=str(tmp_path),
        graph_db=str(tmp_path / "graph.db"),
    )
    adapter.graph_fresh = False
    phases = []

    def refresh_graph(*, phase="graph_query"):
        phases.append(phase)
        adapter.graph_fresh = True
        return True

    monkeypatch.setattr(adapter, "refresh_graph", refresh_graph)
    install_runtime_hooks(agent, adapter)
    agent.execute_actions(
        {
            "extra": {
                "actions": [
                    {
                        "tool_name": "groundtruth",
                        "tool_call_id": "gt-refresh",
                        "gt_action": {
                            "kind": "exact_literal_search",
                            "arguments": {"literal": "needle", "paths": ["mod.py"]},
                        },
                    }
                ]
            }
        }
    )

    assert phases == ["graph_query"]
    assert adapter.graph_fresh is True


def test_malformed_groundtruth_action_fails_open_without_becoming_shell(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state", predicates=[], repo_root=str(tmp_path)
    )
    install_runtime_hooks(agent, adapter)
    messages = agent.execute_actions(
        {"extra": {"actions": [{"tool_name": "groundtruth", "tool_call_id": "gt-bad"}]}}
    )
    assert agent.env.executed == []
    assert "incomplete" in messages[0]["content"]


def test_runtime_hooks_are_idempotent(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path, predicates=[])
    first = install_runtime_hooks(agent, adapter)
    second = install_runtime_hooks(agent, adapter)
    assert first is second


def test_real_miniswe_entrypoint_builds_pinned_adapter(tmp_path):
    from scripts.miniswe_gt_run import build_agent

    agent, adapter, session = build_agent(
        task="Create output.py and run pytest.",
        model="deepseek-v4-flash",
        cwd=str(tmp_path),
        state_dir=str(tmp_path / "state"),
        output=None,
        temperature=1.0,
        gt_off=False,
    )
    assert agent._gt_runtime_hook_handle.installed is True
    assert adapter.contract is not None
    assert adapter.task_id
    assert session is not None
    assert agent._gt_runtime_hook_handle.session is session
    assert session.mode is GTMode.ADVISORY


def test_provider_response_is_bound_to_delivery(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    install_runtime_hooks(agent, _session(adapter, GTMode.ENFORCED))
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    request_id = adapter.deliveries[-1].request_id
    assert not adapter.terminal_confirmed(request_id)
    agent.model.query([{"role": "user", "content": "task"}])
    assert adapter.terminal_confirmed(request_id)


def test_submit_magic_string_executes_when_no_red_evidence(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    install_runtime_hooks(agent, adapter)
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    msgs = agent.execute_actions({"extra": {"actions": [
        {"cmd": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
    ]}})
    # D3-G: with no RED receipt, UNKNOWN predicates no longer block submission.
    assert adapter.phase == "FINISHED"
    assert agent.env.executed
    assert not any(m.get("role") == "user" and "GT REQUIRES" in str(m.get("content"))
                   for m in msgs)


def test_advisory_mode_never_blocks_submit_on_red_evidence(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    install_runtime_hooks(agent, _session(adapter, GTMode.ADVISORY))
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    adapter.record_receipt("p", "pytest", 1, "1 failed", epoch=0, status="RED",
                           semantic=True)
    msgs = agent.execute_actions({"extra": {"actions": [
        {"cmd": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
    ]}})
    assert adapter.phase == "FINISHED"
    assert agent.env.executed
    assert not any(m.get("role") == "user" and "GT ADVISORY" in str(m.get("content"))
                   for m in msgs)


def test_enforced_mode_refuses_only_current_red_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    adapter.provider_boundary = AlwaysSuppressBoundary()
    install_runtime_hooks(agent, _session(adapter, GTMode.ENFORCED))
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    adapter.record_receipt("p", "pytest", 1, "1 failed", epoch=0, status="RED",
                           semantic=True)
    msgs = agent.execute_actions({"extra": {"actions": [
        {"cmd": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
    ]}})
    assert adapter.phase == "IMPLEMENT"
    assert not agent.env.executed
    assert any(m.get("role") == "user" and "GT ENFORCED" in str(m.get("content"))
               for m in msgs)


def test_submit_magic_string_executes_when_contract_proven(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    install_runtime_hooks(agent, adapter)
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    adapter.record_receipt("p", "check", 0, "ok", epoch=0, semantic=True)
    agent.execute_actions({"extra": {"actions": [
        {"cmd": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
    ]}})
    assert adapter.phase == "FINISHED"
    assert agent.env.executed


def test_git_based_edit_detection_catches_heredoc_write(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "mod.py").write_text("def compute(values):\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    class WriteEnv:
        def __init__(self):
            self.executed = []

        def execute(self, action):
            cmd = action.get("command", "")
            self.executed.append(cmd)
            if "WRITE_NOW" in cmd:
                (repo / "src" / "mod.py").write_text(
                    "def compute(values):\n    return 0.0\n", encoding="utf-8"
                )
            return {"output": "ok", "returncode": 0}

    agent = FakeAgent()
    agent.env = WriteEnv()
    adapter = MiniSweAdapter(
        task_id="t",
        state_dir=tmp_path / "state",
        predicates=[Predicate("p", "p")],
        repo_root=str(repo),
    )
    install_runtime_hooks(agent, adapter)
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    # A heredoc-shaped write (delimiter WRITE_NOW) that mutates the tracked
    # file is detected as an edit_result via the git workspace fingerprint,
    # even though no edit tool name appears in the command.
    agent.execute_actions({"extra": {"actions": [
        {"command": "python - <<'WRITE_NOW'\nopen('src/mod.py','w').write('x')\nWRITE_NOW",
         "tool_call_id": "c1"},
    ]}})
    assert adapter.workspace_epoch == 1


def test_result_level_submit_interception_refuses_bypass(monkeypatch, tmp_path):
    from minisweagent.exceptions import Submitted

    class BypassEnv:
        def __init__(self):
            self.executed = []

        def execute(self, action):
            self.executed.append(action.get("command", ""))
            # The command text has no marker, but its OUTPUT begins with the
            # magic string - Mini-SWE's _check_finished would raise Submitted.
            raise Submitted({
                "role": "exit",
                "content": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfake",
                "extra": {"exit_status": "Submitted", "submission": "fake"},
            })

    agent = FakeAgent()
    agent.env = BypassEnv()
    monkeypatch.setenv("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    adapter.provider_boundary = AlwaysSuppressBoundary()
    install_runtime_hooks(agent, _session(adapter, GTMode.ENFORCED))
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    adapter.record_receipt("p", "pytest", 1, "1 failed", epoch=0, status="RED",
                           semantic=True)
    msgs = agent.execute_actions({"extra": {"actions": [
        {"command": "python -c \"print('COMPLETE_' 'TASK_AND_SUBMIT_FINAL_OUTPUT')\"",
         "tool_call_id": "c1"},
    ]}})
    # The gate refused at the RESULT level: the run continues (no Submitted
    # propagates) and the model sees an explicit, nonterminal GT advisory.
    assert adapter.phase == "IMPLEMENT"
    assert any(m.get("role") == "user" and "GT ENFORCED" in str(m.get("content"))
               for m in msgs)
    assert agent.env.executed


def test_result_level_submit_interception_accepts_when_proven(tmp_path):
    from minisweagent.exceptions import Submitted

    class BypassEnv:
        def execute(self, action):
            raise Submitted({
                "role": "exit",
                "content": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfinal",
                "extra": {"exit_status": "Submitted", "submission": "final"},
            })

    agent = FakeAgent()
    agent.env = BypassEnv()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    install_runtime_hooks(agent, adapter)
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    adapter.record_receipt("p", "check", 0, "ok", epoch=0, semantic=True)
    import pytest

    with pytest.raises(Submitted):
        agent.execute_actions({"extra": {"actions": [
            {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "tool_call_id": "c1"},
        ]}})
    assert adapter.phase == "FINISHED"


def test_failing_test_attributed_to_edited_surface(monkeypatch, tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "mod.py").write_text("def compute(values):\n    return 0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    class ScriptedEnv:
        def __init__(self):
            self.executed = []

        def execute(self, action):
            cmd = action.get("command", "")
            self.executed.append(cmd)
            if "WRITE_NOW" in cmd:
                (repo / "src" / "mod.py").write_text("def compute(values):\n    return 0.0\n")
            elif "pytest" in cmd:
                return {"output": (
                    "tests/test_mod.py::test_compute FAILED - "
                    "compute([]) broke src/mod.py\n1 failed\n"), "returncode": 1}
            return {"output": "ok", "returncode": 0}

    agent = FakeAgent()
    agent.env = ScriptedEnv()
    contract = extract_task_contract("compute() must pass the pytest suite.")
    from gt_engine.verification_contract import compile_obligation_predicates

    compiled = compile_obligation_predicates(contract)
    predicates = tuple(
        Predicate(compiled[o.obligation_id].predicate_id, o.text)
        for o in contract.obligations
    )
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state",
        predicates=predicates,
        repo_root=str(repo),
        contract=contract,
    )
    install_runtime_hooks(agent, adapter)
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])

    # edit first (records the edited file), then a failing test that names it
    agent.execute_actions({"extra": {"actions": [
        {"command": "python - <<'WRITE_NOW'\nopen('src/mod.py','w').write('x')\nWRITE_NOW",
         "tool_call_id": "c1"},
    ]}})
    assert adapter.workspace_epoch == 1
    assert "src/mod.py" in adapter._edited_files

    captured = {}
    import gt_engine.miniswe_runtime as rt
    from gt_engine.miniswe_evidence import EvidenceResult

    def spy(state, event, **kw):
        captured["covering"] = event.covering
        return EvidenceResult(rendered="", sealed=False)

    monkeypatch.setattr(rt, "run_evidence_pipeline", spy)
    agent.execute_actions({"extra": {"actions": [
        {"command": "python -m pytest tests/ -q", "tool_call_id": "c2"},
    ]}})
    cov = captured.get("covering")
    assert cov is not None
    assert cov.verdict == "fail"
    assert cov.target == "src/mod.py"
    assert cov.test_files


def test_syntax_probe_catches_broken_edit(monkeypatch, tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "mod.py").write_text("def compute(values):\n    return 0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    class BrokenWriteEnv:
        def execute(self, action):
            cmd = action.get("command", "")
            if "WRITE_BROKEN" in cmd:
                (repo / "src" / "mod.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
            return {"output": "ok", "returncode": 0}

    contract = extract_task_contract("compute() must pass the pytest suite.")
    from gt_engine.verification_contract import compile_obligation_predicates

    compiled = compile_obligation_predicates(contract)
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    monkeypatch.setenv("GT_ALLOW_LIVE_PROBES", "1")
    agent = FakeAgent()
    agent.env = BrokenWriteEnv()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state",
        predicates=[Predicate(compiled[o.obligation_id].predicate_id, o.text)
                    for o in contract.obligations],
        repo_root=str(repo), contract=contract,
    )
    install_runtime_hooks(agent, _session(adapter, GTMode.ASSISTIVE))
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    msgs = agent.execute_actions({"extra": {"actions": [
        {"command": "python - <<'WRITE_BROKEN'\nopen('src/mod.py','w').write('x')\nWRITE_BROKEN",
         "tool_call_id": "c1"},
    ]}})
    joined = "\n".join(str(m.get("content")) for m in msgs)
    assert "[GT_EVIDENCE:syntax_result]" in joined
    assert "syntax error" in joined


def test_evidence_capsule_splices_into_observation(monkeypatch, tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t",
        state_dir=tmp_path,
        predicates=[Predicate("p", "p")],
        contract=extract_task_contract("Fix compute() to handle empty lists."),
        repo_root=str(tmp_path),
    )
    monkeypatch.setattr(
        rt, "run_evidence_pipeline",
        lambda *a, **k: EvidenceResult(rendered="[GT] evidence", sealed=True),
    )
    install_runtime_hooks(agent, adapter)
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    msgs = agent.execute_actions({"extra": {"actions": [
        {"cmd": "pytest tests/ -q", "tool_call_id": "call-1"},
    ]}})
    # FRONT placement: GT facts LEAD the observation (before <returncode>)
    spliced = [str(m.get("content")) for m in msgs if "<gt-facts>" in str(m.get("content"))]
    assert spliced
    assert spliced[0].startswith("<gt-facts>")
    assert "<returncode>" in spliced[0]


def test_sealed_evidence_is_refused_after_shared_prompt_lane_storm_backstop(
    monkeypatch, tmp_path
):
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t",
        state_dir=tmp_path,
        predicates=[Predicate("p", "p")],
        contract=extract_task_contract("Fix compute() to handle empty lists."),
        repo_root=str(tmp_path),
    )
    monkeypatch.setattr(
        rt, "run_evidence_pipeline",
        lambda *a, **k: EvidenceResult(rendered="[GT] twenty-fifth evidence", sealed=True),
    )
    install_runtime_hooks(agent, adapter)
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    for ordinal in range(2, 25):
        assert adapter.admit_model_visible_delivery(
            lane="prompt",
            kind="context_delta",
            rendered=f"delta-{ordinal}",
            action_index=0,
            iteration=ordinal,
            dedup_key=f"delta-{ordinal}",
        )

    msgs = agent.execute_actions({"extra": {"actions": [
        {"cmd": "pytest tests/ -q", "tool_call_id": "call-1"},
    ]}})

    assert not any("<gt-facts>" in str(message.get("content")) for message in msgs)
    rows = [
        __import__("json").loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    refused = [row for row in rows if row["event"] == "delivery_refused"]
    assert refused[-1]["lane"] == "sealed"
    assert refused[-1]["candidate_ordinal"] == 25
    assert refused[-1]["reason"] == "task_delivery_storm_backstop"


def test_newfile_precedent_delivered_on_file_create(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    class CreateEnv:
        def execute(self, action):
            cmd = action.get("command", "")
            if "CREATE_NOW" in cmd:
                target = repo / "src" / "new_util.py"
                target.write_text("def new():\n    return 2\n", encoding="utf-8")
            return {"output": "ok", "returncode": 0}

    contract = extract_task_contract("compute() must pass the pytest suite.")
    from gt_engine.verification_contract import compile_obligation_predicates

    compiled = compile_obligation_predicates(contract)
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")
    monkeypatch.setenv("GT_ALLOW_LIVE_PROBES", "1")
    agent = FakeAgent()
    agent.env = CreateEnv()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state",
        predicates=[Predicate(compiled[o.obligation_id].predicate_id, o.text)
                    for o in contract.obligations],
        repo_root=str(repo), contract=contract,
    )
    install_runtime_hooks(agent, _session(adapter, GTMode.ASSISTIVE))
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    msgs = agent.execute_actions({"extra": {"actions": [
        {"command": (
            "python - <<'CREATE_NOW'\nopen('src/new_util.py','w').write('x')\n"
            "CREATE_NOW"
         ), "tool_call_id": "c1"},
    ]}})
    joined = "\n".join(str(m.get("content")) for m in msgs)
    assert "[GT_EVIDENCE:new_file_destination]" in joined
    assert "advisory precedent" in joined
    assert "reason=same_directory,same_extension" in joined
    assert "inspect=src/util.py" in joined
    assert "<output>ok</output>" in joined
    rows = [
        __import__("json").loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    delivery = next(
        row
        for row in rows
        if row["event"] == "evidence_delivery"
        and row["evidence_type"] == "new_file_destination"
    )
    receipt = next(
        row
        for row in rows
        if row["event"] == "receipt"
        and row["evidence_type"] == "new_file_destination"
    )
    marker = "[GT_EVIDENCE:new_file_destination]\n"
    shipped = marker + joined.split(marker, 1)[1].split("\n</gt-facts>", 1)[0]
    assert delivery["target"] == "src/new_util.py"
    assert delivery["rendered_bytes"] == len(shipped.encode("utf-8"))
    assert delivery["payload_sha256"] == receipt["payload_hash"]
    assert receipt["payload_hash"] == __import__("hashlib").sha256(
        shipped.encode("utf-8")
    ).hexdigest()


def test_newfile_precedent_is_quiet_without_inspectable_sibling(tmp_path):
    from gt_engine.miniswe_covering import run_newfile_precedent

    repo = tmp_path / "repo"
    (repo / "isolated").mkdir(parents=True)
    (repo / "isolated" / "first.py").write_text("value = 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state",
        predicates=[], repo_root=str(repo),
    )
    adapter.repository_revision = "revision-1"
    assert run_newfile_precedent(adapter, ("isolated/first.py",)) == ""


def test_byte_identical_rename_is_not_treated_as_new_file(tmp_path):
    from gt_engine.miniswe_runtime import _created_files_excluding_exact_renames
    from gt_engine.runtime_observation import capture_workspace, diff_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    old = repo / "old.py"
    old.write_text("value = 1\n", encoding="utf-8")
    before = capture_workspace(repo)
    old.rename(repo / "new.py")
    after = capture_workspace(repo)
    transaction = diff_workspace(before, after, action_id=1, command="rename")
    assert _created_files_excluding_exact_renames(transaction) == ()


def test_advisory_mode_never_runs_hidden_covering_or_syntax_commands(
    monkeypatch, tmp_path
):
    import subprocess

    import gt_engine.miniswe_covering as covering

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    class EditEnv(FakeEnv):
        def execute(self, action):
            self.executed.append(action.get("command", ""))
            (repo / "mod.py").write_text("value = 2\n", encoding="utf-8")
            return {"output": "edited", "returncode": 0}

    def forbidden(*args, **kwargs):
        raise AssertionError("advisory GT executed an implicit workspace command")

    monkeypatch.setattr(covering, "run_covering_lane", forbidden)
    monkeypatch.setattr(covering, "run_syntax_probe", forbidden)
    monkeypatch.setattr(covering, "run_newfile_precedent", forbidden)
    monkeypatch.setattr(
        rt, "run_evidence_pipeline",
        lambda *a, **k: EvidenceResult(rendered="", sealed=False),
    )
    agent = FakeAgent()
    agent.env = EditEnv()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state",
        predicates=[Predicate("p", "p")],
        contract=extract_task_contract("Change mod.py."), repo_root=str(repo),
    )
    install_runtime_hooks(agent, _session(adapter, GTMode.ADVISORY))
    agent.execute_actions({"extra": {"actions": [
        {"command": "python edit.py", "tool_call_id": "c1"},
    ]}})
    assert agent.env.executed == ["python edit.py"]


def test_evidence_failure_degrades_and_preserves_original_observation(
    monkeypatch, tmp_path
):
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path,
        predicates=[Predicate("p", "p")],
        contract=extract_task_contract("Inspect the result."),
        repo_root=str(tmp_path),
    )
    session = _session(adapter)

    def explode(*args, **kwargs):
        raise RuntimeError("injected evidence failure")

    monkeypatch.setattr(rt, "run_evidence_pipeline", explode)
    install_runtime_hooks(agent, session)
    msgs = agent.execute_actions({"extra": {"actions": [
        {"command": "printf ok", "tool_call_id": "c1"},
    ]}})
    assert agent.env.executed == ["printf ok"]
    assert any("<output>ok</output>" in str(m.get("content")) for m in msgs)
    assert session.disabled is True
    assert session.assurance_state.value == "DEGRADED"


def test_before_action_failure_degrades_and_still_executes(monkeypatch, tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    session = _session(adapter)

    def explode(*args, **kwargs):
        raise RuntimeError("injected pre-action failure")

    monkeypatch.setattr(adapter, "before_action", explode)
    install_runtime_hooks(agent, session)
    msgs = agent.execute_actions({"extra": {"actions": [
        {"command": "printf ok", "tool_call_id": "c1"},
    ]}})
    assert agent.env.executed == ["printf ok"]
    assert any("<output>ok</output>" in str(m.get("content")) for m in msgs)
    assert session.disabled is True


def test_shadow_mode_does_not_change_model_visible_messages(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path,
        predicates=[Predicate("p", "p")],
        contract=extract_task_contract("Fix compute() for empty input."),
    )
    install_runtime_hooks(agent, _session(adapter, GTMode.SHADOW))
    original = [{"role": "user", "content": "task", "extra": {"private": True}}]
    expected = agent.model._gt_original_prepare_messages_for_api(original)
    assert agent.model._prepare_messages_for_api(original) == expected


def test_hook_handle_can_restore_stock_miniswe_methods(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path, predicates=[])
    original_execute = agent.execute_actions
    original_prepare = agent.model._prepare_messages_for_api
    original_query = agent.model.query
    handle = install_runtime_hooks(agent, _session(adapter))
    handle.restore()
    assert agent.execute_actions == original_execute
    assert agent.model._prepare_messages_for_api == original_prepare
    assert agent.model.query == original_query
    assert handle.installed is False


def test_runtime_captures_one_multifile_transaction_and_invalidates_graph(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")

    class MultiEditEnv(FakeEnv):
        def execute(self, action):
            self.executed.append(action.get("command", ""))
            (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
            (repo / "b.py").write_text("b = 2\n", encoding="utf-8")
            return {"output": "edited", "returncode": 0}

    monkeypatch.setattr(
        rt, "run_evidence_pipeline",
        lambda *a, **k: EvidenceResult(rendered="", sealed=False),
    )
    agent = FakeAgent()
    agent.env = MultiEditEnv()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(repo), graph_db=str(tmp_path / "graph.db"),
    )
    install_runtime_hooks(agent, _session(adapter))
    agent.execute_actions({"extra": {"actions": [
        {"command": "python edit_many.py", "tool_call_id": "c1"},
    ]}})

    rows = [
        __import__("json").loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    transactions = [row for row in rows if row["event"] == "edit_transaction"]
    assert len(transactions) == 1
    assert transactions[0]["changed_paths"] == ["a.py", "b.py"]
    artifacts = next(row for row in rows if row["event"] == "transaction_artifacts")
    assert artifacts["transaction_sha256"] == transactions[0]["transaction_sha256"]
    assert artifacts["patch_count"] == 2
    assert artifacts["syntax_count"] == 2
    assert rows.index(artifacts) < next(
        index for index, row in enumerate(rows) if row["event"] == "graph_invalidated"
    )
    assert adapter.workspace_epoch == 1
    assert adapter.graph_fresh is False
    assert any(row["event"] == "graph_invalidated" for row in rows)


def test_runtime_augments_test_result_but_keeps_raw_output_byte_for_byte(
    monkeypatch, tmp_path
):
    raw = "tests/test_x.py::test_x FAILED\r\n1 failed\r\n"

    class TestEnv(FakeEnv):
        def execute(self, action):
            self.executed.append(action.get("command", ""))
            return {"output": raw, "returncode": 1, "exception_info": "failed"}

    monkeypatch.setattr(
        rt, "run_evidence_pipeline",
        lambda *a, **k: EvidenceResult(rendered="", sealed=False),
    )
    agent = FakeAgent()
    agent.env = TestEnv()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(tmp_path),
    )
    install_runtime_hooks(agent, _session(adapter))
    messages = agent.execute_actions({"extra": {"actions": [
        {"command": "python -m pytest tests/test_x.py -q", "tool_call_id": "c1"},
    ]}})

    content = messages[0]["content"]
    assert "[GT_EXECUTION_EVIDENCE]" in content
    assert raw in content
    rows = [
        __import__("json").loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    evidence = next(row for row in rows if row["event"] == "execution_evidence")
    blob = adapter.store.root / evidence["raw_blob"]
    assert blob.read_bytes() == raw.encode("utf-8")


def test_disabled_typed_capability_never_reaches_shell(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(tmp_path),
    )
    session = GTSession(
        GTSessionConfig(
            task_id="t", repo_root=str(tmp_path),
            disabled_capabilities=("typed_actions",),
        ),
        engine=adapter,
    )
    install_runtime_hooks(agent, session)
    messages = agent.execute_actions({"extra": {"actions": [{
        "tool_name": "groundtruth",
        "tool_call_id": "c1",
        "gt_action": {
            "kind": "exact_literal_search",
            "arguments": {"literal": "x", "paths": ["."]},
            "requested_fidelity": "exact",
        },
    }]}})
    assert agent.env.executed == []
    assert "capability_disabled" in messages[0]["content"]


def test_gt_on_binds_terminal_failure_and_authorizes_zero_delivery_suppression(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    monkeypatch.setenv("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")
    agent = FakeAgent()

    class FailingEnv(FakeEnv):
        def execute(self, action):
            command = action.get("command", "")
            self.executed.append(command)
            if "pytest" in command:
                return {"output": "FAILED deterministic", "returncode": 1,
                        "exception_info": "failed"}
            return {"output": "ok", "returncode": 0}

    agent.env = FailingEnv()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state",
        predicates=[Predicate("p", "must pass")],
        repo_root=str(tmp_path), issue_text="Fix the deterministic failure.",
    )
    calls = []

    class Boundary:
        def authorize_submit_suppression(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                schema="gt.submit_suppression_receipt.v1",
                repository_revision=kwargs["current_revision"],
                action_sha256=__import__("hashlib").sha256(
                    kwargs["action_bytes"]
                ).hexdigest(),
                provider_payload_sha256=__import__("hashlib").sha256(b"").hexdigest(),
                blocker_ids=("closed-red",),
                provider_dispatched=False,
                chars_delivered=0,
            )

    adapter.provider_boundary = Boundary()
    session = _session(adapter, GTMode.ENFORCED)
    install_runtime_hooks(agent, session)
    assert adapter.terminal_evidence_session is not None

    agent.execute_actions({"extra": {"actions": [
        {"command": "python -m pytest failing.py", "tool_call_id": "c1"},
    ]}})
    adapter.record_receipt(
        "p", "python -m pytest failing.py", 1, "failed",
        epoch=adapter.workspace_epoch, status="RED", semantic=True,
    )
    agent.execute_actions({"extra": {"actions": [{
        "command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        "tool_call_id": "c2",
    }]}})

    assert len(calls) == 1
    assert calls[0]["provider_payload_bytes"] == b""
    assert "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in agent.env.executed
    rows = [
        __import__("json").loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(row["event"] == "terminal_evidence_bound" for row in rows)
    assert any(row["event"] == "episode_failure_recorded" for row in rows)
    zero = next(row for row in rows if row["event"] == "submit_suppression_zero_delivery")
    assert zero["provider_dispatched"] is False
    assert zero["chars_delivered"] == 0


def test_gt_off_never_attaches_terminal_or_provider_authorities(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path, predicates=[])
    session = _session(adapter, GTMode.OFF)
    install_runtime_hooks(agent, session)
    action = {"command": "printf baseline", "tool_call_id": "c1"}
    agent.execute_actions({"extra": {"actions": [action]}})
    assert agent.env.executed == ["printf baseline"]
    assert adapter.provider_boundary is None
    assert adapter.terminal_evidence_session is None


def test_submit_suppression_kill_switch_off_fails_open_to_native_action(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GT_SUBMIT_SUPPRESSION_ENFORCE", "0")
    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path,
        predicates=[Predicate("p", "p")], issue_text="Fix it.",
    )
    adapter.provider_boundary = AlwaysSuppressBoundary()
    install_runtime_hooks(agent, _session(adapter, GTMode.ENFORCED))
    adapter.record_receipt(
        "p", "pytest", 1, "failed", epoch=0, status="RED", semantic=True
    )
    command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    agent.execute_actions({"extra": {"actions": [{"command": command}]}})
    assert command in agent.env.executed
    assert not any(
        "submit_suppression_zero_delivery" in line
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    )


def test_submit_suppression_missing_receipt_fails_open_to_native_action(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")

    class NoReceiptBoundary:
        def authorize_submit_suppression(self, **_kwargs):
            return None

    agent = FakeAgent()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path,
        predicates=[Predicate("p", "p")], issue_text="Fix it.",
    )
    adapter.provider_boundary = NoReceiptBoundary()
    install_runtime_hooks(agent, _session(adapter, GTMode.ENFORCED))
    adapter.record_receipt(
        "p", "pytest", 1, "failed", epoch=0, status="RED", semantic=True
    )
    command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    agent.execute_actions({"extra": {"actions": [{"command": command}]}})
    assert command in agent.env.executed


def test_gt_on_attaches_and_restores_canonical_provider_boundary(tmp_path):
    from groundtruth.runtime.miniswe_provider_boundary import MiniSweProviderBoundary

    class BoundaryModel(FakeModel):
        def _query(self, messages, **kwargs):
            return {"id": "resp", "status": "completed", "model": "m"}

    agent = FakeAgent()
    agent.model = BoundaryModel()
    native_prepare = agent.model._prepare_messages_for_api
    native_query = agent.model.query
    native_transport = agent.model._query
    native_add_messages = agent.add_messages
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path, predicates=[], issue_text="Fix it."
    )
    handle = install_runtime_hooks(agent, _session(adapter))
    assert isinstance(adapter.provider_boundary, MiniSweProviderBoundary)
    handle.restore()
    assert agent.model._prepare_messages_for_api == native_prepare
    assert agent.model.query == native_query
    assert agent.model._query == native_transport
    assert agent.add_messages == native_add_messages


def test_gt_on_real_boundary_suppresses_fresh_recorded_failure_end_to_end(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")
    monkeypatch.setenv("GT_RUNTIME_LEDGER", str(tmp_path / "runtime-ledger.jsonl"))

    class BoundaryModel(FakeModel):
        def _query(self, messages, **kwargs):
            return {"id": "resp", "status": "completed", "model": "m"}

    class FailingEnv(FakeEnv):
        def execute(self, action):
            command = action.get("command", "")
            self.executed.append(command)
            if "pytest" in command:
                return {"output": "FAILED exact", "returncode": 1,
                        "exception_info": "failed"}
            return {"output": "ok", "returncode": 0}

    agent = FakeAgent()
    agent.model = BoundaryModel()
    agent.env = FailingEnv()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path / "state", predicates=[],
        repo_root=str(tmp_path), issue_text="Fix exact failure.",
    )
    install_runtime_hooks(agent, _session(adapter, GTMode.ENFORCED))
    agent.execute_actions({"extra": {"actions": [{
        "command": "python -m pytest failing.py", "tool_call_id": "c1",
    }]}})
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    agent.execute_actions({"extra": {"actions": [{
        "command": submit, "tool_call_id": "c2",
    }]}})

    assert submit not in agent.env.executed
    assert len(adapter.provider_boundary.submit_suppression_receipts) == 1
    receipt = adapter.provider_boundary.submit_suppression_receipts[0]
    assert receipt.provider_dispatched is False
    assert receipt.chars_delivered == 0
    assert receipt.provider_payload_sha256 == __import__("hashlib").sha256(b"").hexdigest()
    rows = [
        __import__("json").loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    bound = next(row for row in rows if row["event"] == "terminal_evidence_bound")
    task_bytes = b"Fix exact failure."
    assert bound["task_bytes"] == len(task_bytes)
    assert bound["task_bytes_sha256"] == __import__("hashlib").sha256(task_bytes).hexdigest()
