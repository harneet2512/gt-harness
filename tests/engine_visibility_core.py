"""ENGINE visibility harness (PROVE IT Part 2) — shared engine-loop scenarios.

Forces every non-REMOVE DIRECT feature's trigger THROUGH the real
``engine_execute_actions`` seam (not producer-direct), then asserts the fact
appears in the SAME canonical observation as its trigger, before the next model
call. This is the correct-time proof: each scenario returns the list of rendered
observations so a caller can assert ``<fact owner="X">`` is present in the
observation bound to the triggering action.

Scenarios are shared by tests/test_engine_visibility.py and
scripts/engine_visibility.py so the matrix and the gates measure the same thing.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from groundtruth.runtime.gateway import GatewayState
from gt_engine.engine.runner import engine_execute_actions
from gt_engine.gt_session import GTMode


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")


def _mk_graph(db: Path, rows: list[tuple], edges: list[tuple] = ()) -> None:
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, label TEXT, name TEXT, file_path TEXT, start_line INTEGER, is_test INTEGER, signature TEXT)")
    con.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, type TEXT, confidence REAL, resolution_method TEXT, source_line INTEGER)")
    con.executemany(
        "INSERT INTO nodes (id,label,name,file_path,start_line,is_test,signature) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    con.executemany(
        "INSERT INTO edges (id,source_id,target_id,type,confidence,resolution_method,source_line) VALUES (?,?,?,?,?,?,?)",
        edges,
    )
    con.commit()
    try:
        con.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(name, file_path)")
        con.execute("INSERT INTO nodes_fts(rowid,name,file_path) SELECT id,name,file_path FROM nodes")
        con.commit()
    except sqlite3.Error:
        pass
    con.close()


class Env:
    """Fake execution environment: routes commands to a real temp repo so the
    git-backed producers (changed files, edit before/after, untracked) see the
    real on-disk state their triggers depend on."""

    def __init__(self, repo: Path):
        self.repo = Path(repo)
        self.ran: list[str] = []

    def execute(self, action):
        cmd = str(action.get("command") or action.get("cmd") or "")
        self.ran.append(cmd)
        if "grep -r parse" in cmd:
            return {"output": "a.py:1: def parse\nb.py:1: def parse", "returncode": 0}
        if "grep -r missing" in cmd:
            return {"output": "", "returncode": 1}
        if "cat app.py" in cmd:
            return {"output": "def vulnerable():\n    pass\n", "returncode": 0}
        if "pytest" in cmd or cmd.startswith("python manage.py test"):
            return {"output": "tests/test_a.py:4: in test_x\n    app_function()\n"
                              "src/app.py:12: in app_function\n"
                              "    assert x == 1\nE   AssertionError\n1 failed",
                    "returncode": 1}
        if "run_tests.sh" in cmd or "test.sh" in cmd:
            return {"output": "Traceback (most recent call last):\n"
                              "  File \"src/app.py\", line 12, in app_function\n"
                              "    assert x == 1\nE   AssertionError\nFAILED\n1 failed",
                    "returncode": 1}
        if "edit_signature" in cmd:
            # real on-disk mutation: mod.py f(x) -> f(x, y)
            (self.repo / "mod.py").write_text("def f(x, y):\n    return x + y\n", encoding="utf-8")
            return {"output": "", "returncode": 0}
        if "create_azure" in cmd:
            (self.repo / "providers" / "azure.py").write_text("class Azure:\n    pass\n", encoding="utf-8")
            return {"output": "", "returncode": 0}
        if "create_module" in cmd:
            (self.repo / "new_module.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            return {"output": "", "returncode": 0}
        return {"output": "ok", "returncode": 0}


class Store:
    def __init__(self):
        self.events = []

    def append(self, event, **payload):
        self.events.append({"event": event, **payload})


class Adapter:
    contract = None
    global_action = 0
    iteration = 0
    blocking_reasons: tuple[str, ...] = ()

    def __init__(self, repo: Path, graph: str | None = None, issue_text: str = "",
                 contract=None, blocking_reasons=()):
        self.repo_root = str(repo)
        self.repository_revision = "r1"
        self.graph_db = graph
        self.graph_fresh = bool(graph)
        self.issue_text = issue_text
        self.contract = contract
        self.blocking_reasons = blocking_reasons
        self.store = Store()
        self._dedup_chain: set[str] = set()
        self._delivered_evidence_types: set[str] = set()
        self._engine_failure_history: dict[str, int] = {}
        self._engine_search_history: dict[str, int] = {}

    def gateway_state(self):
        return GatewayState(
            repo_root=self.repo_root,
            graph_db=self.graph_db,
            issue_text=self.issue_text,
        )

    def evaluate_observation(self, *a, **k):
        return None

    def evaluate_failing_observation(self, *a, **k):
        return None

    def blocking_obligation_texts(self):
        return tuple(self.blocking_reasons)

    def next_contract_delta(self, max_chars=1200):
        return ""

    # --- lifecycle surface the engine loop calls (real MiniSweAdapter has a
    # GroundtruthController behind these; the harness must mirror them so the
    # regression tests exercise the real wiring, not fail-open AttributeError).
    phase = "IMPLEMENT"

    def before_action(self, *a, **k):
        return ""

    def note_edit(self, *a, **k):
        return None

    def begin_implement(self, *a, **k):
        return None

    def begin_verify(self, *a, **k):
        return None

    def after_observation(self, *a, **k):
        return None


class Session:
    mode = GTMode.ENGINE
    disabled = False
    model_visible = True

    def can_enforce(self):
        return True

    def capability_active(self, name):
        return True

    def capability_model_visible(self, name):
        return True

    def allows_live_probes(self):
        return False

    def degrade(self, stage, error):
        self.disabled = True


class Model:
    def __init__(self):
        self.observations = []

    def format_observation_messages(self, message, outputs, template_vars):
        formatted = [
            {"role": "tool", "content": str(o.get("output") or "")} for o in outputs
        ]
        self.observations = formatted
        return formatted


class Agent:
    def __init__(self):
        self.sent = []

    def get_template_vars(self):
        return {}

    def add_messages(self, *messages):
        self.sent = list(messages)
        return self.sent


def run_engine(repo: Path, actions: list[dict], *, graph: str | None = None,
               issue_text: str = "", contract=None, blocking_reasons=(),
               deny_submit: bool = False, _original_submit_gate=None) -> dict:
    """Run the actions through engine_execute_actions; return the harness state.

    ``_original_submit_gate`` is an optional restore hook: when passed, the
    current ``_run_submit_gate`` is swapped to the deny-gate for this call and
    RESTORED afterwards, so the monkeypatch never leaks to later tests.
    """
    import gt_engine.miniswe_runtime as rt

    adapter = Adapter(repo, graph=graph, issue_text=issue_text,
                      contract=contract, blocking_reasons=blocking_reasons)
    model, agent, env = Model(), Agent(), Env(repo)
    session = Session()
    original = _original_submit_gate
    if deny_submit:
        if original is None:
            original = rt._run_submit_gate
        rt._run_submit_gate = lambda s, c: False
    try:
        engine_execute_actions(
            agent,
            {"extra": {"actions": actions}},
            session=session,
            adapter=adapter,
            model=model,
            environment=env,
            original_execute=None,
        )
    finally:
        if deny_submit:
            rt._run_submit_gate = original
    return {
        "observations": model.observations,
        "agent_sent": agent.sent,
        "adapter": adapter,
        "env": env,
        "session": session,
    }


def has_fact(observation: str, owner: str) -> bool:
    return f'<fact owner="{owner}"' in observation
