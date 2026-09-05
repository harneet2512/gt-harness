from __future__ import annotations

import json

import pytest

import gt_engine.miniswe_runtime as rt
from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_evidence import EvidenceResult
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks
from gt_engine.provider_limits import ProviderRequestTooLarge
from gt_engine.task_contract import extract_task_contract


@pytest.fixture(autouse=True)
def isolated_git_fixture_identity(monkeypatch):
    # These tests commit disposable repositories. Do not depend on a CI
    # account's global identity or mutate the machine's Git configuration.
    for name, value in {
        "GIT_AUTHOR_NAME": "GT Test", "GIT_COMMITTER_NAME": "GT Test",
        "GIT_AUTHOR_EMAIL": "gt-test@example.invalid",
        "GIT_COMMITTER_EMAIL": "gt-test@example.invalid",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("sed -n '1,20p' src/a.py", ("src/a.py",)),
        ("head -n 20 src/a.py", ("src/a.py",)),
        ("nl -ba src/a.py", ("src/a.py",)),
        ("cat src/a.py src/b.py", ("src/a.py", "src/b.py")),
        ("cat 'src/a file.py' | head -n 20", ("src/a file.py",)),
    ],
)
def test_viewed_files_parses_operands_not_options(command, expected):
    assert rt._viewed_files(command) == expected


def test_newfile_precedent_does_not_preempt_executed_syntax_failure(tmp_path, monkeypatch):
    from gt_engine import miniswe_covering as covering

    adapter = MiniSweAdapter(task_id="precedent", state_dir=tmp_path, predicates=[],
                             contract=extract_task_contract("Add a parser."))
    calls = []
    monkeypatch.setattr(covering, "run_newfile_precedent", lambda *_: "nearby example")
    monkeypatch.setattr(covering, "run_covering_lane", lambda *_: calls.append("covering"))
    monkeypatch.setattr(covering, "run_syntax_probe", lambda *_:
                        calls.append("syntax") or "new.py: syntax error")
    rendered = rt._run_evidence(adapter, "write", "", 0, 1, ("new.py",), {},
                                 ("new.py",), allow_live_probes=True)
    assert calls == ["covering", "syntax"]
    assert "[GT_EVIDENCE:syntax_result]" in rendered
    assert "nearby example" not in rendered


def test_runtime_collects_every_candidate_before_current_syntax_wins(
    tmp_path, monkeypatch
):
    from gt_engine import miniswe_covering as covering

    adapter = MiniSweAdapter(
        task_id="rank-all",
        state_dir=tmp_path,
        predicates=[],
        contract=extract_task_contract("Add a parser."),
    )
    calls = []
    monkeypatch.setattr(
        covering,
        "run_covering_lane",
        lambda *_: calls.append("covering") or None,
    )
    monkeypatch.setattr(
        covering,
        "run_syntax_probe",
        lambda *_: calls.append("syntax") or "new.py: syntax error",
    )
    monkeypatch.setattr(
        covering,
        "run_newfile_precedent",
        lambda *_: calls.append("newfile") or "nearby example",
    )

    def gateway(*args, **kwargs):
        calls.append("gateway")
        return EvidenceResult(rendered="", sealed=False)

    def cochange(owner, command, changed_files):
        calls.append("cochange")
        owner.stage_model_visible_delivery(
            kind="cochange_partner", dedup_key="weak-prior"
        )
        return "weak prior"

    monkeypatch.setattr(rt, "run_evidence_pipeline", gateway)
    monkeypatch.setattr(rt, "_cochange_prior", cochange)
    original_chain = adapter._chain_head
    rendered = rt._run_evidence(
        adapter,
        "write",
        "",
        0,
        1,
        ("new.py",),
        {},
        ("new.py",),
        allow_live_probes=True,
    )

    assert calls == ["covering", "syntax", "gateway", "newfile", "cochange"]
    assert "syntax error" in rendered
    assert adapter.consume_model_visible_delivery_metadata()["kind"] == "syntax_result"
    assert "weak-prior" not in adapter._dedup_chain
    assert adapter._chain_head == original_chain


def test_verification_candidate_outranks_weak_priors(tmp_path, monkeypatch):
    from gt_engine import miniswe_covering as covering

    adapter = MiniSweAdapter(
        task_id="verification-rank",
        state_dir=tmp_path,
        predicates=[],
        contract=extract_task_contract("Fix the parser."),
    )
    adapter._pending_verification_candidate = (
        "[GT_EVIDENCE:verification_plan]\npytest tests/test_parser.py"
    )
    adapter._pending_verification_metadata = {
        "kind": "verification_plan",
        "dedup_key": "verification:tx-1",
        "target": "tests/test_parser.py",
        "semantics": "advisory_pre_edit_dependency_graph",
    }
    monkeypatch.setattr(covering, "run_newfile_precedent", lambda *_: "example")
    monkeypatch.setattr(
        rt,
        "run_evidence_pipeline",
        lambda *args, **kwargs: EvidenceResult(rendered="", sealed=False),
    )

    def cochange(owner, command, changed_files):
        owner.stage_model_visible_delivery(
            kind="cochange_partner", dedup_key="weak"
        )
        return "weak prior"

    monkeypatch.setattr(rt, "_cochange_prior", cochange)
    rendered = rt._run_evidence(
        adapter,
        "edit",
        "",
        0,
        1,
        ("src/parser.py",),
        {},
        ("src/parser.py",),
    )
    assert "verification_plan" in rendered
    assert adapter.verification_candidate()[0] == rendered
    assert "verification:tx-1" not in adapter._dedup_chain
    assert (
        adapter.consume_model_visible_delivery_metadata()["kind"]
        == "verification_plan"
    )
    adapter.admit_model_visible_delivery(lane="sealed", kind="verification_plan",
        rendered=rendered, action_index=1, iteration=0, dedup_key="verification:tx-1")
    adapter.discard_pending_provider_deliveries(reason="fixture_provider_refusal")
    assert adapter.verification_candidate()[0] == rendered
    assert "verification:tx-1" not in adapter._dedup_chain
    rendered = rt._run_evidence(adapter, "edit", "", 0, 2, (), {})
    assert adapter.admit_model_visible_delivery(lane="sealed", kind="verification_plan",
        rendered=rendered, action_index=2, iteration=0, dedup_key="verification:tx-1")
    adapter.bind_provider_payload({"messages": [{"role": "user", "content": rendered}]})
    assert adapter.verification_candidate()[0] == ""
    assert "verification:tx-1" in adapter._dedup_chain


def test_selected_gateway_chain_commits_only_on_exact_exposure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    adapter = MiniSweAdapter(task_id="exposure", state_dir=tmp_path, predicates=[],
                             contract=extract_task_contract("Fix parser."))
    adapter._chain_head = "old-head"
    monkeypatch.setattr(rt, "_cochange_prior", lambda *args: "")
    monkeypatch.setattr(rt, "run_evidence_pipeline", lambda *args, **kwargs:
        EvidenceResult(rendered="[GT_EVIDENCE:caller_contract]\nexact proof", sealed=True,
                       chain_head="new-head", envelope=SimpleNamespace(
                           evidence_type="caller_contract", dedup_key="proof-key", target="a.py")))
    rendered = rt._run_evidence(adapter, "cat a.py", "x", 0, 1, (), {})
    assert adapter._chain_head == "old-head"
    assert "proof-key" not in adapter._dedup_chain
    assert adapter.admit_model_visible_delivery(lane="sealed", kind="caller_contract",
        rendered=rendered, action_index=1, iteration=0, dedup_key="proof-key")
    adapter.bind_provider_payload({"messages": [{"role": "user", "content": "formatter removed proof"}]})
    assert adapter._chain_head == "old-head"
    assert "proof-key" not in adapter._dedup_chain
    rendered = rt._run_evidence(adapter, "cat a.py", "x", 0, 2, (), {})
    assert adapter.admit_model_visible_delivery(lane="sealed", kind="caller_contract",
        rendered=rendered, action_index=2, iteration=1, dedup_key="proof-key")
    adapter.bind_provider_payload({"messages": [{"role": "user", "content": rendered}]})
    assert adapter._chain_head == "new-head"
    assert "proof-key" in adapter._dedup_chain


def test_covering_selection_does_not_open_stale_graph(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from gt_engine import miniswe_covering as covering

    adapter = SimpleNamespace(repo_root=str(tmp_path), graph_query_snapshot=lambda:
        SimpleNamespace(graph_current=False, graph_path=""))
    monkeypatch.setenv("GT_VERIFY_EXECUTE", "1")

    def query(*_args):
        pytest.fail("stale graph query")

    monkeypatch.setattr(covering, "_symbols_for_files", query)
    assert covering.run_covering_lane(adapter, ("source.py",)) is None


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


class TransportFakeModel(FakeModel):
    model_name = "fixture/model"
    model_kwargs = {}
    tools = []

    def _query(self, messages, **kwargs):
        self.calls.append(messages)
        return {"id": "response", "model": self.model_name, "usage": {}}

    def query(self, messages, **kwargs):
        prepared = self._prepare_messages_for_api(messages)
        response = self._query(prepared, **kwargs)
        return {"role": "assistant", "content": "ok",
                "extra": {"actions": [], "response": response}}


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


def test_native_action_batch_has_session_owned_execution_receipts(tmp_path, monkeypatch):
    import subprocess
    import sys

    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="execution-batch", state_dir=tmp_path, predicates=[])
    def execute(action):
        result = subprocess.run([sys.executable, "-c", action["command"]],
                                capture_output=True, text=True, check=False)
        return {"output": result.stdout + result.stderr, "returncode": result.returncode}
    agent.env.execute = execute
    monkeypatch.setattr(rt, "_run_evidence", lambda *args, **kwargs: "")
    install_runtime_hooks(agent, _session(adapter))
    outputs = agent.execute_actions({"extra": {"actions": [
        {"command": "print('real output')"}, {"command": "pass"},
        {"command": "raise RuntimeError('real failure')"},
    ]}})
    assert len(outputs) == 3
    rows = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    starts = [row for row in rows if row["event"] == "execution_started"]
    finishes = [row for row in rows if row["event"] == "execution_finished"]
    assert len(starts) == len(finishes) == 3
    assert [row["action_index"] for row in starts] == [1, 2, 3]
    assert [row["execution_id"] for row in starts] == [row["execution_id"] for row in finishes]
    assert all(row["result_sha256"] for row in finishes)


def test_fast_paths_have_distinct_native_action_indices(tmp_path):
    agent = FakeAgent()
    adapter = MiniSweAdapter(task_id="fast-paths", state_dir=tmp_path, predicates=[])
    install_runtime_hooks(agent, _session(adapter))
    actions = [{"command": "echo hello"}, {"command": ""},
               {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]
    agent.execute_actions({"extra": {"actions": actions}})
    rows = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    starts = [row for row in rows if row["event"] == "execution_started"]
    assert [row["action_index"] for row in starts] == [1, 2, 3]
    assert agent.env.executed == [action["command"] for action in actions]


def test_external_edit_cannot_dirty_repository(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "a.py").write_text("x = 1\n", encoding="utf-8")
    external = tmp_path / "scratch.py"
    external.write_text("x = 1\n", encoding="utf-8")
    adapter = MiniSweAdapter(task_id="external-edit", repo_root=str(repository),
                             state_dir=tmp_path / "state", predicates=[])
    agent = FakeAgent()
    edits = []
    monkeypatch.setattr(adapter, "note_edit", lambda paths: edits.append(paths))
    monkeypatch.setattr(rt, "_run_evidence", lambda *args, **kwargs: "")

    def execute(action):
        external.write_text("x = 2\n", encoding="utf-8")
        return {"output": "", "returncode": 0}

    agent.env.execute = execute
    install_runtime_hooks(agent, adapter)
    agent.execute_actions({"extra": {"actions": [
        {"command": f"sed -i 's/1/2/' {external.as_posix()}"}
    ]}})
    assert external.read_text(encoding="utf-8") == "x = 2\n"
    assert edits == []


def test_fallback_preimage_does_not_read_external_file(tmp_path):
    from types import SimpleNamespace
    repository = tmp_path / "repo"
    repository.mkdir()
    external = tmp_path / "scratch.py"
    external.write_text("outside", encoding="utf-8")
    adapter = SimpleNamespace(repo_root=str(repository))
    assert rt._capture_edit_preimage(
        adapter, f"sed -i 's/outside/changed/' {external.as_posix()}"
    ) is None


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


def _configure_fixture_provider(monkeypatch):
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "100000")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "1000")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "fixture")
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 1)


def test_runtime_hooks_capture_provider_payload_and_action(tmp_path, monkeypatch):
    _configure_fixture_provider(monkeypatch)
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    handle = install_runtime_hooks(agent, adapter)
    prepared = agent.model._prepare_messages_for_api([
        {"role": "user", "content": "task"},
    ])
    assert prepared[0]["content"].startswith("task")
    assert not adapter.deliveries
    agent.model.query([{"role": "user", "content": "task"}])
    assert adapter.deliveries
    agent.execute_actions({"extra": {"actions": [{"cmd": "printf ok"}]}})
    assert handle.installed is True
    assert adapter.iteration == 1


def test_native_groundtruth_action_is_routed_without_shell_execution(tmp_path, monkeypatch):
    _configure_fixture_provider(monkeypatch)
    (tmp_path / "mod.py").write_text("needle = 1\n", encoding="utf-8")
    agent = FakeAgent()
    agent.model = TransportFakeModel()
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
    agent.model.query([*agent.messages, *messages])
    assert adapter._pending_typed_observations == []
    rows = [
        __import__("json").loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    joined = [row for row in rows if row["event"] == "typed_observation_provider_join"]
    assert len(joined) == 1
    assert joined[0]["final_observation_sha256"]
    assert joined[0]["provider_payload_sha256"] == adapter.deliveries[-1].payload_sha256


def test_graph_independent_typed_query_does_not_refresh_stale_graph(
    monkeypatch, tmp_path,
):
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

    assert phases == []
    assert adapter.graph_fresh is False


def test_ordinary_provider_turn_does_not_rebuild_a_stale_graph(monkeypatch, tmp_path):
    agent = FakeAgent()
    agent.model = TransportFakeModel()
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
    agent.model._prepare_messages_for_api(
        [{"role": "user", "content": "continue after an edit"}]
    )

    assert phases == []
    assert adapter.graph_fresh is False


def test_groundtruth_reads_exact_raw_output_not_bounded_model_view():
    result = {
        "output": "bounded",
        "extra": {"raw_output": "exact diagnostic output"},
    }

    assert rt._observation_output(result) == "exact diagnostic output"


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
        wall_time_limit_seconds=123,
    )
    assert agent._gt_runtime_hook_handle.installed is True
    assert adapter.contract is not None
    assert adapter.task_id
    assert session is not None
    assert agent._gt_runtime_hook_handle.session is session
    assert session.mode is GTMode.ADVISORY
    assert session.assurance_state.value == "FULL"
    assert set(session.config.capabilities) == {
        "exact_provider_payload",
        "provider_response_ids",
        "structured_actions",
        "structured_results",
        "workspace_deltas",
        "filesystem_snapshots",
        "tool_call_deferral",
        "parsed_test_results",
    }
    assert agent.config.wall_time_limit_seconds == 123


def test_provider_response_is_bound_to_delivery(tmp_path, monkeypatch):
    _configure_fixture_provider(monkeypatch)
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    install_runtime_hooks(agent, _session(adapter, GTMode.ENFORCED))
    agent.model.query([{"role": "user", "content": "task"}])
    request_id = adapter.deliveries[-1].request_id
    assert adapter.terminal_confirmed(request_id)


def test_provider_admission_uses_prepared_payload_and_conserves_refusal(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "100")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "20")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "provider:/models")

    class PreparedBoundaryModel(FakeModel):
        model_name = "openai/meta/muse-spark-1.2-contributor"
        model_kwargs = {}
        tools = []

        def __init__(self):
            super().__init__()
            self.transport_calls = 0

        def _query(self, messages, **kwargs):
            self.transport_calls += 1
            return {"id": "resp", "status": "completed", "model": "m"}

        def query(self, messages, **kwargs):
            prepared = self._prepare_messages_for_api(messages)
            response = self._query(prepared, **kwargs)
            return {
                "role": "assistant",
                "content": "ok",
                "extra": {
                    "actions": [],
                    "response": {
                        "id": response["id"],
                        "model": "meta/muse-spark-1.2-contributor",
                        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                    },
                },
            }

    agent = FakeAgent()
    agent.model = PreparedBoundaryModel()
    adapter = MiniSweAdapter(
        task_id="t", state_dir=tmp_path, predicates=[], issue_text="Fix it."
    )
    install_runtime_hooks(agent, _session(adapter))

    # The raw history is huge only because ``extra`` duplicates content.  The
    # native prepare seam strips it before admission.
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _payload: 79)
    agent.model.query(
        [
            {
                "role": "tool",
                "content": "real evidence",
                "extra": {"duplicate": "x" * 1_000_000},
            }
        ]
    )
    assert agent.model.transport_calls == 1

    monkeypatch.setattr(rt, "provider_request_tokens", lambda _payload: 81)
    with pytest.raises(ProviderRequestTooLarge):
        agent.model.query([{"role": "user", "content": "genuinely too large"}])
    assert agent.model.transport_calls == 1
    assert len(adapter.deliveries) == 1
    assert adapter.terminal_confirmed(adapter.deliveries[-1].request_id)

    monkeypatch.delenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE")
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _payload: 12)
    with pytest.raises(rt.ProviderContextWindowUnavailable):
        agent.model.query([{"role": "user", "content": "metadata unavailable"}])
    assert agent.model.transport_calls == 1
    events = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    refusal = [row for row in events if row.get("event") == "provider_admission"][-1]
    assert refusal["reason"] == "GT_PROVIDER_CONTEXT_WINDOW_UNAVAILABLE"
    assert refusal["request_tokens"] == 12
    assert refusal["request_bytes"] > 0
    assert refusal["metadata_source"] == ""


def test_final_provider_refusal_does_not_consume_gt_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "100")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "20")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "fixture")
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path, predicates=[],
                             contract=extract_task_contract("Fix compute()."))
    install_runtime_hooks(agent, _session(adapter))
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 81)
    prepared = agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    assert "GT_TASK_CONTRACT" in prepared[-1]["content"]
    assert not adapter.contract_shipped
    assert not adapter.deliveries
    with pytest.raises(ProviderRequestTooLarge):
        agent.model._query(prepared)
    assert not adapter.contract_shipped
    assert not adapter.deliveries
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 79)
    retry = agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    assert "GT_TASK_CONTRACT" in retry[-1]["content"]
    agent.model._query(retry)
    assert adapter.contract_shipped
    assert len(adapter.deliveries) == 1


def test_recovery_retries_through_real_admission_and_transport_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "100")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "20")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "fixture")
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    adapter = MiniSweAdapter(task_id="recovery-wire", state_dir=tmp_path, predicates=[])
    adapter.start_task()
    adapter.note_failure_fingerprint("failure", epoch=0)
    adapter.note_edit(["module.py"])
    adapter.note_failure_fingerprint("failure", epoch=1)
    rendered = adapter.pending_transient
    install_runtime_hooks(agent, _session(adapter))
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 81)
    messages = [{"role": "user", "content": "task"}]
    with pytest.raises(ProviderRequestTooLarge):
        agent.model._query(messages)
    assert not agent.model.calls
    assert adapter._recovery_delivered == 0
    assert adapter.pending_transient == rendered
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 79)
    agent.model._query(messages)
    assert agent.model.calls[-1][-1]["content"] == rendered
    assert adapter._recovery_delivered == 1
    assert adapter.deliveries[-1].delivery_ids
    assert messages == [{"role": "user", "content": "task"}]
    agent.model._query(messages)
    assert all(rendered not in item["content"] for item in agent.model.calls[-1])
    assert adapter._recovery_delivered == 1


def test_chain_conflict_never_reaches_transport_and_can_be_reprepared(tmp_path, monkeypatch):
    from gt_engine.miniswe_integration import ExposureChainConflict

    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "100")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "20")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "fixture")
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 10)
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    adapter = MiniSweAdapter(task_id="chain-wire", state_dir=tmp_path, predicates=[])
    install_runtime_hooks(agent, _session(adapter))
    initial = adapter._chain_head
    rendered = "[GT_EVIDENCE:caller_contract] dependent proof"

    def stage(previous):
        adapter.stage_exposure(rendered=rendered, dedup_key="B",
                               previous_chain_head=previous, next_chain_head="head-b")
        assert adapter.admit_model_visible_delivery(
            lane="sealed", kind="caller_contract", rendered=rendered,
            action_index=1, iteration=0, dedup_key="B")

    stage("missing-head-a")
    messages = [{"role": "user", "content": rendered}]
    with pytest.raises(ExposureChainConflict):
        agent.model._query(messages)
    assert not agent.model.calls
    assert not adapter.deliveries
    assert adapter._chain_head == initial
    assert not adapter._pending_provider_deliveries
    stage(initial)
    agent.model._query(messages)
    assert agent.model.calls == [messages]
    assert adapter._chain_head == "head-b"
    assert adapter._model_visible_delivery_count == 1


def test_prepare_failure_discards_staged_delivery(tmp_path, monkeypatch):
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    adapter = MiniSweAdapter(
        task_id="t",
        state_dir=tmp_path,
        predicates=[],
        contract=extract_task_contract("Fix compute()."),
    )
    session = _session(adapter)
    install_runtime_hooks(agent, session)

    def fail_after_staging(messages, *, iteration):
        adapter.admit_model_visible_delivery(
            lane="prompt",
            kind="context_delta",
            rendered="[GT_FIXTURE] staged",
            dedup_key="fixture",
            action_index=0,
            iteration=iteration,
        )
        raise RuntimeError("fixture prepare failure")

    monkeypatch.setattr(session, "before_model", fail_after_staging)
    original = [{"role": "user", "content": "task"}]
    assert agent.model._prepare_messages_for_api(original) == original
    assert not adapter.deliveries
    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    discarded = [
        row for row in rows if row.get("event") == "prepared_deliveries_discarded"
    ]
    assert discarded[-1]["reason"] == "prepare_messages_error"


def test_disable_between_prepare_and_transport_discards_delivery(tmp_path, monkeypatch):
    _configure_fixture_provider(monkeypatch)
    agent = FakeAgent()
    agent.model = TransportFakeModel()
    adapter = MiniSweAdapter(
        task_id="t",
        state_dir=tmp_path,
        predicates=[],
        contract=extract_task_contract("Fix compute()."),
    )
    session = _session(adapter)
    install_runtime_hooks(agent, session)
    prepared = agent.model._prepare_messages_for_api(
        [{"role": "user", "content": "task"}]
    )
    assert "GT_TASK_CONTRACT" in prepared[-1]["content"]

    session.degrade("fixture", RuntimeError("disabled before transport"))
    agent.model._query(prepared)

    assert not adapter.contract_shipped
    assert not adapter.deliveries
    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]
    discarded = [
        row for row in rows if row.get("event") == "prepared_deliveries_discarded"
    ]
    assert discarded[-1]["reason"] == "gt_disabled_before_transport"


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
    # This environment returns ordinary "ok", not Submitted. Command text alone
    # must not declare completion before a native terminal result exists.
    assert adapter.phase == "IMPLEMENT"
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
    assert adapter.phase == "IMPLEMENT"  # FakeEnv returned ok, not Submitted.
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
    assert adapter.phase == "IMPLEMENT"  # FakeEnv returned ok, not Submitted.
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


def test_result_level_submit_cannot_reuse_preexecution_authority(monkeypatch, tmp_path):
    from minisweagent.exceptions import Submitted

    class BypassEnv:
        def __init__(self):
            self.executed = []

        def execute(self, action):
            self.executed.append(action.get("command", ""))
            # The command text has no marker, but its OUTPUT begins with the
            # magic string - Mini-SWE's _check_finished would raise Submitted.
            error = Submitted({
                "role": "exit",
                "content": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfake",
                "extra": {"exit_status": "Submitted", "submission": "fake"},
            })
            error.gt_execution_result = {"output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfake",
                                         "returncode": 0, "exception_info": ""}
            raise error

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
    with pytest.raises(Submitted):
        agent.execute_actions({"extra": {"actions": [
            {"command": "python -c \"print('COMPLETE_' 'TASK_AND_SUBMIT_FINAL_OUTPUT')\"",
             "tool_call_id": "c1"},
        ]}})
    assert agent.env.executed
    rows = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    assert not any(row["event"] in {"submit_refusal", "action_suppressed"} for row in rows)
    assert agent._gt_runtime_hook_handle.session.disabled_stage == "terminal_refusal_authority"


@pytest.mark.parametrize("edit", [False, True])
def test_real_submission_preserves_output_and_edit(monkeypatch, tmp_path, edit):
    import subprocess
    import sys

    from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "changed.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "changed.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "fixture"],
                   check=True, capture_output=True)
    agent = FakeAgent()
    agent.env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path))
    monkeypatch.setenv("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")
    adapter = MiniSweAdapter(task_id="real-refusal", state_dir=tmp_path / "state",
                             repo_root=str(tmp_path), predicates=[Predicate("p", "p")])
    adapter.provider_boundary = AlwaysSuppressBoundary()
    install_runtime_hooks(agent, _session(adapter, GTMode.ENFORCED))
    adapter.record_receipt("p", "pytest", 1, "1 failed", epoch=0, status="RED", semantic=True)
    gate_epochs = []
    native_gate = rt._run_submit_gate
    def observe_gate(session, command, **kwargs):
        gate_epochs.append(adapter.workspace_epoch)
        return native_gate(session, command, **kwargs)
    monkeypatch.setattr(rt, "_run_submit_gate", observe_gate)
    change = "Path('changed.py').write_text('x = 2'); " if edit else ""
    command = (f'"{sys.executable}" -c "from pathlib import Path; ' + change +
               "print('COMPLETE_' + 'TASK_AND_SUBMIT_FINAL_OUTPUT'); print('actual submission')\"")
    message = {"extra": {"actions": [{"command": command, "tool_call_id": "c1"}]}}
    from minisweagent.exceptions import Submitted
    with pytest.raises(Submitted) as caught:
        agent.execute_actions(message)
    assert "actual submission" in str(caught.value.messages)
    assert "actual submission" in caught.value.gt_execution_result["output"]
    assert gate_epochs == [int(edit)]
    assert adapter.workspace_epoch == int(edit)
    assert (tmp_path / "changed.py").read_text() == ("x = 2" if edit else "x = 1\n")

    # An accepted literal-marker action must also pass through observation.
    agent._gt_runtime_hook_handle.restore()
    session = _session(adapter, GTMode.ASSISTIVE)
    install_runtime_hooks(agent, session)
    accepted_command = (f'"{sys.executable}" -c "from pathlib import Path; '
                        "Path('changed.py').write_text('x = 3'); "
                        "print('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'); print('accepted')\"")
    with pytest.raises(Submitted) as caught:
        agent.execute_actions({"extra": {"actions": [{"command": accepted_command}]}})
    assert "accepted" in str(caught.value.messages)
    assert adapter.workspace_epoch == int(edit) + 1


@pytest.mark.parametrize("has_result", [False, True])
def test_result_level_submit_interception_accepts_when_proven(tmp_path, has_result):
    from minisweagent.exceptions import Submitted

    class BypassEnv:
        def execute(self, action):
            self.error = Submitted({
                "role": "exit",
                "content": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfinal",
                "extra": {"exit_status": "Submitted", "submission": "final"},
            })
            if has_result:
                self.error.gt_execution_result = {"output": "final", "returncode": 0}
            raise self.error

    agent = FakeAgent()
    agent.env = BypassEnv()
    adapter = MiniSweAdapter(task_id="t", state_dir=tmp_path,
                             predicates=[Predicate("p", "p")])
    install_runtime_hooks(agent, adapter)
    agent.model._prepare_messages_for_api([{"role": "user", "content": "task"}])
    adapter.record_receipt("p", "check", 0, "ok", epoch=0, semantic=True)
    import pytest

    with pytest.raises(Submitted) as caught:
        agent.execute_actions({"extra": {"actions": [
            {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "tool_call_id": "c1"},
        ]}})
    assert caught.value is agent.env.error
    session = agent._gt_runtime_hook_handle.session
    if has_result:
        assert adapter.phase == "FINISHED"
    else:
        assert session.disabled_stage == "submitted_result_missing"
        assert session.integrity_receipt()["valid"] is False


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
    for ordinal in range(3):
        assert adapter.admit_model_visible_delivery(
            lane="prompt",
            kind="context_delta",
            rendered=f"delta-{ordinal}",
            action_index=0,
            iteration=adapter.iteration,
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
    assert refused[-1]["candidate_ordinal"] == 5
    assert refused[-1]["reason"] == "boundary_claim_ceiling"


def test_newfile_precedent_delivered_on_file_create(tmp_path, monkeypatch):
    import subprocess

    _configure_fixture_provider(monkeypatch)

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
    agent.model = TransportFakeModel()
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
    agent.model.query(msgs)
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
    rows = [json.loads(line) for line in adapter.store.path.read_text().splitlines()]
    suppressed = [row for row in rows if row["event"] == "action_suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["reason"] == "capability_disabled"
    assert not any(row["event"] == "execution_started" for row in rows)


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
    suppressed = [row for row in rows if row["event"] == "action_suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["reason"] == "submit_refused"
    assert suppressed[0]["action_index"] == 2
