"""ENGINE-mode real-seam end-to-end smoke (provider-free, scripted).

Drives the REAL DefaultAgent loop with the REAL MiniSweAdapter + real
install_runtime_hooks in GTMode.ENGINE — the exact production code path the
paid smoke runs. Scripted model + scripted env produce a trajectory that
crosses every feature trigger, and the assertions verify:
  * every FACT fires with a NON-EMPTY, usable payload (the review's bug-1 guard)
  * raw output is preserved on bash actions (the review's bug-2 guard)
  * the fact appears in the SAME observation as its trigger (correct time)
  * lifecycle advances (global_action, note_edit RED invalidation) (bug-3 guard)

Run:
    python scripts/engine_smoke_e2e.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# minisweagent prints a rich banner; on Windows cp1252 stdout that raises.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

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
from gt_engine.task_contract import extract_task_contract  # noqa: E402
from gt_engine.verification_contract import compile_obligation_predicates  # noqa: E402

TASK = "compute() must pass the pytest suite and handle empty input."
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
                target = Path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
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

    def _next(self):
        self.index += 1
        if self.index < len(self.outputs):
            return self.outputs[self.index]
        # Out of scripted turns: emit a terminal submit so the run ends
        # cleanly (the provider boundary double-consumes query/_query, so the
        # audit supplies a few extra turns per scenario).
        return {
            "role": "assistant",
            "content": "done",
            "extra": {
                "actions": [
                    {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                     "tool_call_id": "end"},
                ],
                "response": {"model": "m", "usage": {}},
            },
        }

    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in item.items() if k != "extra"} for item in messages]

    def query(self, messages, **kwargs):
        self._prepare_messages_for_api(messages)
        return self._next()

    def _query(self, messages, **kwargs):
        # mirror LitellmModel's private query surface the provider boundary
        # wraps; ScriptedModel routes through the same scripted outputs.
        return self._next()

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


def build_engine_run(tmp_root: str | None = None):
    apply_profile_env()
    root = Path(tmp_root or tempfile.mkdtemp(prefix="miniswe-gt-engine-"))
    (root / "src").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "src" / "mod.py").write_text(SRC, encoding="utf-8")
    (root / "tests" / "test_mod.py").write_text(
        "from mod import compute\n\n\ndef test_empty():\n    assert compute([]) == 0.0\n",
        encoding="utf-8",
    )
    # terminal-bench workspaces are git repos; git-backed producers
    # (_git_changed_py / note_edit / edit before-after) require it.
    import subprocess

    for cmd in (["git", "-C", str(root), "init", "-q"],
                ["git", "-C", str(root), "config", "user.email", "t@t.t"],
                ["git", "-C", str(root), "config", "user.name", "t"],
                ["git", "-C", str(root), "add", "."],
                ["git", "-C", str(root), "commit", "-qm", "init"]):
        subprocess.run(cmd, capture_output=True, check=False)
    graph_db = ensure_index(str(root))

    contract = extract_task_contract(TASK)
    compiled = compile_obligation_predicates(contract)
    predicates = tuple(
        Predicate(compiled[o.obligation_id].predicate_id, o.text)
        for o in contract.obligations
    )
    adapter = MiniSweAdapter(
        task_id="engine-smoke-1", state_dir=root / ".gt-state", predicates=predicates,
        contract=contract, repo_root=str(root), graph_db=graph_db, issue_text=TASK,
    )
    session = GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=str(root),
            state_dir=str(root / ".gt-state"),
            graph_db=graph_db,
            issue_text=TASK,
            mode=GTMode.ENGINE,
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

    def _script():
        return {
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

    outputs = [
        _msg("search", 'grep -rn "def compute" src/', "c1"),
        _msg("view", "cat src/mod.py", "c2"),
        _msg("red test", "python -m pytest tests/test_mod.py -q", "c3"),
        _msg("edit", EDIT, "c4"),
        _msg("green test", "python -m pytest tests/ -q", "c5"),
        _msg("submit", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "c6"),
    ]
    script = _script()
    env = ScriptedEnv(script, writes={" > src/mod.py": (str(root / "src" / "mod.py"), FIXED)})
    model = ScriptedModel(outputs)
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
    return agent, adapter, graph_db, root


def build_engine_run_submit_red(tmp_root: str | None = None):
    """Trajectory: search, failing test, then submit WHILE RED is fresh. The
    submit gate must SUPPRESS + refuse; the model then edits, re-tests, submits
    again -> accepted. Proves submit_refusal end-to-end through the real seam."""
    apply_profile_env()
    root = Path(tmp_root or tempfile.mkdtemp(prefix="miniswe-gt-engine-red-"))
    (root / "src").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "src" / "mod.py").write_text(SRC, encoding="utf-8")
    (root / "tests" / "test_mod.py").write_text(
        "from mod import compute\n\n\ndef test_empty():\n    assert compute([]) == 0.0\n",
        encoding="utf-8",
    )
    import subprocess

    for cmd in (["git", "-C", str(root), "init", "-q"],
                ["git", "-C", str(root), "config", "user.email", "t@t.t"],
                ["git", "-C", str(root), "config", "user.name", "t"],
                ["git", "-C", str(root), "add", "."],
                ["git", "-C", str(root), "commit", "-qm", "init"]):
        subprocess.run(cmd, capture_output=True, check=False)
    graph_db = ensure_index(str(root))
    contract = extract_task_contract(TASK)
    compiled = compile_obligation_predicates(contract)
    predicates = tuple(
        Predicate(compiled[o.obligation_id].predicate_id, o.text)
        for o in contract.obligations
    )
    adapter = MiniSweAdapter(
        task_id="engine-smoke-red", state_dir=root / ".gt-state", predicates=predicates,
        contract=contract, repo_root=str(root), graph_db=graph_db, issue_text=TASK,
    )
    session = GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=str(root),
            state_dir=str(root / ".gt-state"),
            graph_db=graph_db,
            issue_text=TASK,
            mode=GTMode.ENGINE,
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

    script = {
        'grep -rn "def compute" src/': {
            "output": "src/mod.py:1:def compute(values):\n", "returncode": 0,
        },
        "python -m pytest tests/test_mod.py -q": {
            "output": (
                "tests/test_mod.py::test_compute FAILED - compute([]) "
                "raised ZeroDivisionError\n1 failed\n"
            ),
            "returncode": 1,
        },
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT": {
            "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nfixed\n", "returncode": 0,
        },
        EDIT: {"output": "", "returncode": 0},
        "cat src/mod.py": {"output": SRC, "returncode": 0},
        "python -m pytest tests/ -q": {
            "output": (
                "tests/test_mod.py::test_compute PASSED - compute([]) == 0.0\n"
                "1 passed\n"
            ),
            "returncode": 0,
        },
    }
    outputs = [
        _msg("search", 'grep -rn "def compute" src/', "c1"),
        _msg("red test", "python -m pytest tests/test_mod.py -q", "c3"),
        _msg("submit while red", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "c6"),
        _msg("after refusal 1", "cat src/mod.py", "c8"),
        _msg("after refusal 2", "cat src/mod.py", "c9"),
        _msg("after refusal 3", "cat src/mod.py", "c10"),
        _msg("edit", EDIT, "c4"),
        _msg("green test", "python -m pytest tests/ -q", "c5"),
        _msg("submit again 1", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "c7"),
        _msg("submit again 2", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "c11"),
    ]
    env = ScriptedEnv(script, writes={" > src/mod.py": (str(root / "src" / "mod.py"), FIXED)})
    model = ScriptedModel(outputs)
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
    return agent, adapter, graph_db, root


def build_scenario_engine(
    *,
    tmp_root: str | None = None,
    files: dict[str, str] | None = None,
    graph: tuple[list[tuple], list[tuple]] | None = None,
    script: dict | None = None,
    trajectory: list[dict] | None = None,
    writes: dict | None = None,
    task: str = TASK,
    issue_text: str | None = None,
    task_id: str = "engine-scenario",
):
    """Generic REAL-seam scenario runner (P1 readiness audit).

    Builds a git workspace from ``files``, optionally injects a synthetic graph
    (``graph`` = (nodes, edges) using the real gateway schema — deterministic,
    no gt-index binary needed), runs the provided scripted trajectory through
    the REAL DefaultAgent + MiniSweAdapter + install_runtime_hooks, and returns
    (agent, adapter, graph_db, root). The real MiniSweProviderBoundary attaches
    via the production path.
    """
    apply_profile_env()
    # The ENGINE is the canonical runtime: the submit gate's zero-delivery
    # SUPPRESS is enforced (same flags the paid smoke workflow sets).
    os.environ.setdefault("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")
    root = Path(tmp_root or tempfile.mkdtemp(prefix="miniswe-gt-scenario-"))
    for rel, content in (files or {}).items():
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    import subprocess

    for cmd in (["git", "-C", str(root), "init", "-q"],
                ["git", "-C", str(root), "config", "user.email", "t@t.t"],
                ["git", "-C", str(root), "config", "user.name", "t"],
                ["git", "-C", str(root), "add", "."],
                ["git", "-C", str(root), "commit", "-qm", "init"]):
        subprocess.run(cmd, capture_output=True, check=False)

    graph_db = None
    if graph is not None:
        from tests.engine_visibility_core import _mk_graph

        graph_db = str(root.parent / f"{root.name}-graph.db")
        _mk_graph(Path(graph_db), graph[0], graph[1])

    contract = extract_task_contract(task)
    compiled = compile_obligation_predicates(contract)
    predicates = tuple(
        Predicate(compiled[o.obligation_id].predicate_id, o.text)
        for o in contract.obligations
    )
    resolved_issue = issue_text if issue_text is not None else task
    # State lives OUTSIDE the repo root (mirrors production: /logs/agent/gt-state
    # vs /app). If state or graph.db sat inside repo_root, the gateway's
    # change_surface repo walk would scan them and leak internal paths into
    # model-visible newfile_precedent evidence (found by the readiness audit).
    state_dir = root.parent / f"{root.name}-state"
    adapter = MiniSweAdapter(
        task_id=task_id, state_dir=str(state_dir), predicates=predicates,
        contract=contract, repo_root=str(root), graph_db=graph_db,
        issue_text=resolved_issue,
    )
    session = GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=str(root),
            state_dir=str(state_dir),
            graph_db=graph_db,
            issue_text=resolved_issue,
            mode=GTMode.ENGINE,
        ),
        engine=adapter,
    )
    # resolve relative write targets against the scenario root (ScriptedEnv
    # writes via Path() relative to the process CWD otherwise)
    resolved_writes = {}
    for marker, (path, content) in (writes or {}).items():
        target = path if Path(path).is_absolute() else str(root / path)
        resolved_writes[marker] = (target, content)
    env = ScriptedEnv(script or {}, writes=resolved_writes)
    model = ScriptedModel(trajectory or [])
    agent = DefaultAgent(
        model, env,
        config_class=AgentConfig,
        system_template="You are a coding agent.",
        instance_template="Task: {{ task }}",
        step_limit=20,
        output_path=None,
    )
    # The audit runs the SAME issue the adapter's contract was built from, so
    # delivered obligation facts can be verified against the real task.
    agent._gt_scenario_task = resolved_issue
    install_runtime_hooks(agent, session)
    return agent, adapter, graph_db, root


def run_and_audit() -> dict:
    agent, adapter, graph_db, root = build_engine_run()
    agent.run(TASK)

    import json as _json

    rows = [
        _json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    deliveries = [r for r in rows if r.get("event") == "engine_delivery"]
    engine_init = next((r for r in rows if r.get("event") == "engine_init"), None)
    return {
        "exit": "Submitted",
        "phase": adapter.phase,
        "unmet": list(adapter.unmet_predicates),
        "iteration": adapter.iteration,
        "global_action": adapter.global_action,
        "workspace_epoch": adapter.workspace_epoch,
        "deliveries": len(deliveries),
        "engine_init": engine_init,
        "graph_db_present": bool(graph_db),
        "repository_revision_set": bool(getattr(adapter, "repository_revision", "")),
        "red_invalidated_by_edit": any(r.get("event") == "red_invalidated_by_edit" for r in rows),
        "semantic_red": [r for r in rows if r.get("event") == "semantic_red"],
        "semantic_observation": [r for r in rows if r.get("event") == "semantic_observation"],
        "submit_decision": [r for r in rows if r.get("event") == "submit_decision"],
        "episode_failure_recorded": [r for r in rows if r.get("event") == "episode_failure_recorded"],
        "engine_delivery_events": deliveries,
        "rows_kept": len(rows),
    }


def run_and_audit_submit_red() -> dict:
    """Submit-while-RED scenario: the first submit must be SUPPRESSED with a
    GT ENFORCED refusal (fresh RED closed blocker), then after the fix + green
    test the second submit is accepted. Attaches the REAL MiniSweProviderBoundary
    (production attaches it to GroundTruthLitellmModel; a ScriptedModel cannot)."""
    import os

    os.environ["GT_SUBMIT_SUPPRESSION_ENFORCE"] = "1"
    agent, adapter, graph_db, root = build_engine_run_submit_red()
    from groundtruth.runtime.miniswe_provider_boundary import (
        MiniSweProviderBoundary,
    )

    adapter.provider_boundary = MiniSweProviderBoundary(
        model=agent.model,
        agent=agent,
        fault_handler=lambda stage, exc: None,
    )
    agent.run(TASK)

    import json as _json

    rows = [
        _json.loads(line)
        for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    suppress = [r for r in rows if r.get("event") == "engine_delivery"
                and r.get("decision") == "suppress"]
    refusals = [r for r in rows if r.get("event") == "submit_refusal"]
    decisions = [r for r in rows if r.get("event") == "submit_decision"]
    return {
        "phase": adapter.phase,
        "unmet": list(adapter.unmet_predicates),
        "iteration": adapter.iteration,
        "suppress_deliveries": len(suppress),
        "submit_refusal_events": refusals,
        "submit_decisions": decisions,
        "closed_blocker_registered": any(
            bool(r.get("blocker_id")) for r in rows
            if r.get("event") == "episode_failure_recorded"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--submit-red", action="store_true",
                        help="run the submit-while-RED (submit_refusal) scenario")
    args = parser.parse_args()
    if args.submit_red:
        audit = run_and_audit_submit_red()
    else:
        audit = run_and_audit()
    if args.json:
        print(json.dumps(audit, indent=2, default=str))
    elif args.submit_red:
        print(f"phase={audit['phase']} unmet={audit['unmet']} iter={audit['iteration']}")
        print(f"suppress_deliveries={audit['suppress_deliveries']}")
        print(f"submit_refusal_events={len(audit['submit_refusal_events'])}")
        print(f"submit_decisions={len(audit['submit_decisions'])} "
              f"accepted={sum(1 for d in audit['submit_decisions'] if d.get('accepted'))}")
        print(f"closed_blocker_registered={audit['closed_blocker_registered']}")
    else:
        print(f"phase={audit['phase']} unmet={audit['unmet']} iter={audit['iteration']}")
        print(f"global_action={audit['global_action']} workspace_epoch={audit['workspace_epoch']}")
        print(f"deliveries={audit['deliveries']} graph_db={audit['graph_db_present']} "
              f"repository_revision_set={audit['repository_revision_set']}")
        print(f"red_invalidated_by_edit={audit['red_invalidated_by_edit']}")
        print(f"semantic_red={len(audit['semantic_red'])} semantic_observation={len(audit['semantic_observation'])}")
        print(f"submit_decision={len(audit['submit_decision'])} episode_failure_recorded={len(audit['episode_failure_recorded'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
