"""Mini-SWE + GT integration smoke: the real DefaultAgent loop with GT hooks.

Deterministic and provider-free (scripted model + scripted env). Validates the
full seam end-to-end:
  contract in iteration-1 payload, localization evidence at search_result,
  RED receipt on a failing test, receipt invalidation on edit (epoch bump),
  GREEN on a passing test, submit gate accept, provider-response binding, exit.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# minisweagent's __init__ prints a rich banner; on Windows cp1252 stdout that
# raises before pytest can capture it. Force UTF-8 before the import.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 - stream reconfigure is best-effort
            pass

import pytest  # noqa: E402
from minisweagent.agents.default import AgentConfig, DefaultAgent  # noqa: E402
from minisweagent.exceptions import Submitted  # noqa: E402
from minisweagent.models.utils.actions_toolcall import (  # noqa: E402
    format_toolcall_observation_messages,
)

from gt_engine.bridge import apply_profile_env  # noqa: E402
from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig  # noqa: E402
from gt_engine.indexer import ensure_index  # noqa: E402
from gt_engine.miniswe_controller import Predicate  # noqa: E402
from gt_engine.miniswe_integration import MiniSweAdapter  # noqa: E402
from gt_engine.miniswe_runtime import install_runtime_hooks  # noqa: E402
from gt_engine.runtime_observation import capture_workspace  # noqa: E402
from gt_engine.task_contract import extract_task_contract  # noqa: E402
from gt_engine.verification_contract import compile_obligation_predicates  # noqa: E402

TASK = "compute() must pass the pytest suite."
SRC = "def compute(values):\n    total = sum(values)\n    return total / len(values)\n"
FIXED = "def compute(values):\n    return (total / len(values)) if values else 0.0\n"
EDIT = (
    "printf \"def compute(values):\\n    return (total / len(values)) "
    "if values else 0.0\\n\" > src/mod.py"
)


class ScriptedEnv:
    def __init__(self, script, writes=None):
        self.script = script
        self.writes = writes or {}
        self.executed: list[str] = []

    def execute(self, action):
        command = action.get("command", "")
        self.executed.append(command)
        for marker, (path, content) in self.writes.items():
            if marker in command:
                Path(path).write_text(content, encoding="utf-8")
                break
        result = self.script.get(command, {"output": "", "returncode": 0})
        output = result["output"]
        if output.lstrip().startswith("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"):
            raise Submitted({
                "role": "exit", "content": output,
                "extra": {"exit_status": "Submitted", "submission": output},
            })
        return {"output": output, "returncode": result.get("returncode", 0),
                "exception_info": ""}

    def get_template_vars(self, **kwargs):
        return {"cwd": ".", "timeout": 30}

    def serialize(self):
        return {}


class ScriptedModel:
    def __init__(self, outputs):
        self.outputs = outputs
        self.index = -1

    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in item.items() if k != "extra"} for item in messages]

    def query(self, messages, **kwargs):
        self._prepare_messages_for_api(messages)
        self.index += 1
        return self.outputs[self.index]

    def format_message(self, **kwargs):
        return dict(kwargs)

    def format_observation_messages(self, message, outputs, template_vars=None):
        return format_toolcall_observation_messages(
            actions=message.get("extra", {}).get("actions", []),
            outputs=outputs,
            observation_template=(
                "<returncode>{{output.returncode}}</returncode>\n<output>\n"
                "{{output.output}}</output>"
            ),
            template_vars=template_vars,
        )

    def get_template_vars(self, **kwargs):
        return {"model_name": "scripted"}

    def serialize(self):
        return {}


def _build_agent(tmp_path):
    apply_profile_env()
    root = Path(tempfile.mkdtemp(prefix="miniswe-gt-smoke-"))
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "mod.py").write_text(SRC, encoding="utf-8")
    (root / "tests" / "test_mod.py").write_text(
        "from mod import compute\n\n\ndef test_empty():\n    assert compute([]) == 0.0\n",
        encoding="utf-8",
    )
    graph_db = ensure_index(str(root))

    contract = extract_task_contract(TASK)
    compiled = compile_obligation_predicates(contract)
    predicates = tuple(
        Predicate(compiled[o.obligation_id].predicate_id, o.text)
        for o in contract.obligations
    )
    adapter = MiniSweAdapter(
        task_id="smoke-1", state_dir=root / ".gt-state", predicates=predicates,
        contract=contract, repo_root=str(root), graph_db=graph_db, issue_text=TASK,
    )
    adapter.record_repository_snapshot(
        capture_workspace(root, excluded_roots=(root / ".gt-state",)),
        boundary="repository_start",
    )
    session = GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=str(root),
            state_dir=str(root / ".gt-state"),
            graph_db=graph_db,
            issue_text=TASK,
            mode=GTMode.ADVISORY,
        ),
        engine=adapter,
    )

    def _msg(content, command, tool_id, tokens=4):
        return {"role": "assistant", "content": content,
                "extra": {"actions": [{"command": command, "tool_call_id": tool_id}],
                          "response": {"model": "deepseek-v4-flash",
                                       "usage": {"prompt_tokens": 10,
                                                 "completion_tokens": tokens}},
                          "cost": 0.0}}

    outputs = [
        _msg("search", 'grep -rn "def compute" src/', "c1"),
        _msg("view", "cat src/mod.py", "c2"),
        _msg("red test", "python -m pytest tests/test_mod.py -q", "c3"),
        _msg("edit", EDIT, "c4"),
        _msg("green test", "python -m pytest tests/ -q", "c5"),
        _msg("submit", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "c6"),
    ]
    script = {
        'grep -rn "def compute" src/': {
            "output": "src/mod.py:1:def compute(values):\n", "returncode": 0,
        },
        "cat src/mod.py": {"output": SRC, "returncode": 0},
        "python -m pytest tests/test_mod.py -q": {
            "output": (
                "tests/test_mod.py::test_compute FAILED - compute([]) "
                "raised ZeroDivisionError\n1 failed\n"
            ),
            "returncode": 1,
        },
        EDIT: {"output": "", "returncode": 0},
        "python -m pytest tests/ -q": {
            "output": (
                "tests/test_mod.py::test_compute PASSED - compute([]) == 0.0\n"
                "1 passed\n"
            ),
            "returncode": 0,
        },
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT": {
            "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfixed\n", "returncode": 0,
        },
    }
    env = ScriptedEnv(script, writes={" > src/mod.py": (str(root / "src" / "mod.py"), FIXED)})
    model = ScriptedModel(outputs)
    # Production default: the compiled delivery path owns first-request
    # contract/localization evidence. The legacy system-template path is only
    # an explicit rollback mode and must not be duplicated here.
    system_template = "You are a coding agent."
    agent = DefaultAgent(
        model, env,
        config_class=AgentConfig,
        system_template=system_template,
        instance_template="Task: {{ task }}",
        step_limit=10,
        output_path=None,
    )
    install_runtime_hooks(agent, session)
    return agent, adapter, graph_db


def test_miniswe_gt_smoke_runs_to_submitted(tmp_path):
    agent, adapter, graph_db = _build_agent(tmp_path)
    result = agent.run(TASK)

    assert result.get("exit_status") == "Submitted"
    assert adapter.phase == "FINISHED"
    assert adapter.unmet_predicates == ()
    assert adapter.contract_shipped is True
    assert adapter.iteration == 6
    assert len(adapter.deliveries) == 6
    assert all(adapter.terminal_confirmed(d.request_id) for d in adapter.deliveries)
    # the edit bumped the workspace epoch -> stale receipts were invalidated
    assert adapter.workspace_epoch == 1


def test_miniswe_gt_smoke_delivers_evidence_and_receipts(tmp_path):
    agent, adapter, graph_db = _build_agent(tmp_path)
    agent.run(TASK)

    import json

    rows = [
        json.loads(line)
        for line in (adapter.store.path.read_text(encoding="utf-8")).splitlines()
        if line.strip()
    ]
    execution = [row for row in rows if row["event"] == "execution_evidence"]
    assert execution, "no structured action-bound evidence was delivered"
    assert execution[0]["action_id"] == 3
    assert execution[0]["outcome"] == "fail"
    assert any(row["event"] == "semantic_red" for row in rows)
    assert any(row["event"] == "submit_decision" and row["accepted"] for row in rows)


def test_task_start_localization_delivered_with_graph(tmp_path):
    import os

    agent, adapter, graph_db = _build_agent(tmp_path)
    if graph_db is None:
        import pytest

        pytest.skip("no graph binary available")
    os.environ["GT_GATEWAY_NATIVE"] = "1"
    agent.run(TASK)
    # The complete contract is bound to the first request exactly once. Ranked
    # localization is correct-or-quiet when its producer is not certified.
    import json

    rows = [
        json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    first = next(row for row in rows if row.get("event") == "provider_delivery")
    request = json.loads((adapter.store.root / first["request_blob"]).read_text(
        encoding="utf-8"
    ))
    rendered = json.dumps(request["messages"], ensure_ascii=False)
    assert rendered.count("Task contract") == 1
    # no harness framing (round-9: the model audits GT_* markers on disk)
    assert "GT_TASK_CONTRACT" not in rendered
    assert "GT_" not in rendered
    loc_rows = [r for r in rows if r.get("event") == "evidence_delivery"
                and r.get("evidence_type") == "localization"]
    assert len(loc_rows) <= 1
    lifecycle_rows = adapter.feature_lifecycle_receipts()
    assert lifecycle_rows
    assert lifecycle_rows[0]["repository_revision"] == adapter.initial_repository_revision
    assert all(row["repository_revision"] for row in lifecycle_rows)


def test_miniswe_gt_smoke_localization_requires_graph(tmp_path):
    # Without a graph the pipeline still runs, but the graph-backed
    # localization feature is dormant (correct-or-quiet) - the run must not
    # crash and the submit gate must still work.
    agent, adapter, graph_db = _build_agent(tmp_path)
    if graph_db is not None:
        pytest.skip("graph available; covered by the full-smoke test")
    result = agent.run(TASK)
    assert result.get("exit_status") == "Submitted"
