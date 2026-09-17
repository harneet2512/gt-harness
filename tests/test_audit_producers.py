"""Layer-4 audit of the shipping Mini-SWE producer -> admission -> wire boundary.

Every test here is provider-free: the env executes real commands through Git
Bash inside a real temporary repository, the graph is built by the real
``gt-index`` producer binary and sealed like a benchmark-bound build, and the
provider is a recording fake. Assertions join the run journal
(``producer_invocation`` / ``evidence_delivery`` / ``provider_delivery`` /
``provider_response`` / ``delivery_refusal``) to the exact model-visible bytes
the provider transport received.

Audited abilities: localization + GT_LOC_RESLOT, caller_contract,
cochange_prior, def_partition, newfile_precedent + GT_CHANGE_SURFACE,
signature_delta + GT_PATCH_DELTA, select_catalog, plus scenarios D1
(localization -> view -> caller -> signature edit -> affected-caller evidence)
and D5 (simultaneous candidates -> budget refusal -> retry -> one exposure).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine import miniswe_runtime as rt
from gt_engine.engine_state import RuntimeLayout
from gt_engine.gt_session import (
    GTDecisionCandidate,
    GTMode,
    GTSession,
    GTSessionConfig,
)
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.task_contract import extract_task_contract

# --------------------------------------------------------------------------
# Harness fakes (self-contained copies of the test-miniswe-runtime shapes)
# --------------------------------------------------------------------------


class _FakeModel:
    def __init__(self):
        self.calls: list[list[dict]] = []

    def _prepare_messages_for_api(self, messages):
        return [{k: v for k, v in item.items() if k != "extra"} for item in messages]

    def query(self, messages, **kwargs):
        self.calls.append(messages)
        return {
            "role": "assistant",
            "content": "ok",
            "extra": {
                "actions": [],
                "response": {"model": "deepseek-v4-flash",
                             "usage": {"prompt_tokens": 5}},
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


class _TransportFakeModel(_FakeModel):
    """Fake provider transport: ``query`` flows through the real prepare +
    query_transport wrappers, so admission and payload binding are exercised."""

    model_name = "fixture/model"
    model_kwargs: dict = {}
    tools: list = []

    def _query(self, messages, **kwargs):
        self.calls.append(messages)
        return {"id": "response", "model": self.model_name, "usage": {}}

    def query(self, messages, **kwargs):
        prepared = self._prepare_messages_for_api(messages)
        response = self._query(prepared, **kwargs)
        return {"role": "assistant", "content": "ok",
                "extra": {"actions": [], "response": response}}


class _FakeAgent:
    def __init__(self):
        self.model = _FakeModel()
        self.env = None
        self.messages: list[dict] = []

    def execute_actions(self, message):
        return []

    def add_messages(self, *messages):
        self.messages.extend(messages)
        return list(messages)

    def get_template_vars(self):
        return {}


def _git_bash() -> str:
    """Git Bash on Windows; ``bash`` on POSIX. Skip rather than fake."""
    candidate = shutil.which("bash")
    if candidate and "System32" not in candidate:  # WSL bash cannot see D:\\ paths
        return candidate
    git_bash = r"C:\Program Files\Git\usr\bin\bash.exe"
    if os.path.isfile(git_bash):
        return git_bash
    pytest.skip("a real POSIX-ish shell is required to execute actions")


class _ShellEnv:
    """A Mini-SWE-shaped environment backed by a real shell.

    ``execute`` runs the command in the repository root exactly like the
    shipping LocalEnvironment: stdout+stderr, real returncode, real file
    effects that ``capture_workspace``/``diff_workspace`` then observe.
    """

    def __init__(self, cwd):
        self.cwd = str(cwd)
        self.executed: list[str] = []

    def execute(self, action):
        command = action.get("command", "")
        self.executed.append(command)
        proc = subprocess.run(
            [_git_bash(), "-c", command],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {"output": proc.stdout + proc.stderr, "returncode": proc.returncode}


# --------------------------------------------------------------------------
# Fixture: production env fan-out + real producer graph + full hook stack
# --------------------------------------------------------------------------


_PRODUCER_CANDIDATES = (
    r"C:\Users\Lenovo\.groundtruth\bin\gt-index.exe",
    r"D:\Groundtruth\gt-index\gt-index.exe",
)


def _producer_binary() -> str:
    env = os.environ.get("GT_INDEX_BINARY", "").strip()
    if env and os.path.isfile(env):
        return env
    which = shutil.which("gt-index")
    if which:
        return which
    for candidate in _PRODUCER_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    pytest.skip("gt-index producer binary unavailable")


@pytest.fixture()
def production_env(monkeypatch):
    """Apply the same flag surface ``miniswe_gt_run`` installs on dispatch."""
    for key in list(os.environ):
        if key.startswith("GT_"):
            monkeypatch.delenv(key, raising=False)
    binary = _producer_binary()
    monkeypatch.setenv("GT_INDEX_BINARY", binary)
    from gt_engine.bridge import apply_profile_env

    apply_profile_env()
    # The three setdefault()s the shipping runner applies on top of the
    # profile fan-out (scripts/miniswe_gt_run.py:664-682).
    os.environ.setdefault("GT_SUBMIT_SUPPRESSION_ENFORCE", "1")
    os.environ.setdefault("GT_VERIFY_EXECUTE", "1")
    os.environ.setdefault("GT_PERSISTENT_PLAN", "1")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", "100000")
    monkeypatch.setenv("GT_PROVIDER_RESERVED_OUTPUT_TOKENS", "1000")
    monkeypatch.setenv("GT_PROVIDER_CONTEXT_WINDOW_SOURCE", "fixture")
    monkeypatch.setattr(rt, "provider_request_tokens", lambda _: 1)
    return binary


def _seal_graph(graph_dir: Path, graph_db: Path, task_id: str) -> None:
    """Seal the producer-built graph like a benchmark-bound index build.

    Mirrors tests/conftest.py ``write_certifiable_graph`` but over a graph the
    REAL producer emitted: the three sidecars bind to actual graph bytes.
    """
    from gt_engine.indexer import _graph_phase_metadata, _sealed_json

    producer_sha = hashlib.sha256(
        Path(os.environ["GT_INDEX_BINARY"]).read_bytes()
    ).hexdigest()
    binding = {
        "repository_root_sha256": "b" * 64,
        "source_manifest_sha256": "c" * 64,
        "task_id": task_id,
        "product_source_sha": "d" * 40,
        "identity_scope": "benchmark_bound",
    }
    resource_path = graph_dir / "index-resource.json"
    _sealed_json(
        resource_path,
        {
            "schema": "gt.index_resource.v1", **binding,
            "status": "completed", "exit_code": 0, "error_code": "",
            "memory_evidence": False, "producer_binary_sha256": producer_sha,
        },
        "evidence_sha256",
    )
    manifest = {
        "schema": "gt.graph_certification.v1",
        **binding,
        **_graph_phase_metadata(graph_db),
        "binary_certified": True,
        "binary_sha256": producer_sha,
        "graph_sha256": hashlib.sha256(graph_db.read_bytes()).hexdigest(),
        "graph_bytes": graph_db.stat().st_size,
        "index_resource_sha256": hashlib.sha256(
            resource_path.read_bytes()
        ).hexdigest(),
        "sqlite_quick_check": "ok",
        "cochange_rows": 0,
    }
    (graph_dir / "graph.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    _sealed_json(
        graph_dir / "lsp-promotion.json",
        {"schema": "gt.lsp_promotion.v1", **binding,
         "graph_sha256": manifest["graph_sha256"],
         "status": "promotion_not_scheduled",
         "servers_detected": ["pyright-langserver"], "server_count": 1},
        "promotion_sha256",
    )


def _producer_graph(repo: Path, graph_dir: Path, binary: str) -> Path:
    """Run the real producer binary over ``repo`` and seal the output."""
    graph_dir.mkdir(parents=True, exist_ok=True)
    graph_db = graph_dir / "graph.db"
    proc = subprocess.run(
        [binary, "-root", str(repo), "-output", str(graph_db)],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"producer failed: {proc.stderr[-400:]}"
    assert graph_db.is_file()
    return graph_db


def _insert_cochanges(graph_db: Path, rows: list[tuple[str, str, int]]) -> None:
    """Populate ``cochanges`` as the real producer would after a rebuild."""
    with sqlite3.connect(graph_db) as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(cochanges)")]
        if {"file_a", "file_b", "count"}.issubset(cols):
            con.executemany(
                "INSERT INTO cochanges(file_a,file_b,count) VALUES (?,?,?)",
                rows,
            )
        else:
            con.executemany("INSERT INTO cochanges VALUES (?)",
                            [(i,) for i, _ in enumerate(rows)])


SHIPPING_CAPABILITIES = (
    "exact_provider_payload", "provider_response_ids", "structured_actions",
    "structured_results", "workspace_deltas", "filesystem_snapshots",
    "tool_call_deferral", "parsed_test_results",
)


class _Stack(SimpleNamespace):
    pass


def _build_stack(
    tmp_path: Path,
    issue: str,
    files: dict[str, str],
    *,
    task_id: str = "audit",
    cochanges: list[tuple[str, str, int]] | None = None,
    disabled_capabilities: tuple[str, ...] = (),
    model=None,
) -> _Stack:
    """Assemble the real shipping stack: producer graph -> adapter -> session
    -> installed runtime hooks -> recording transport fake + real shell env."""
    repo = tmp_path / "repo"
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    layout = RuntimeLayout.resolve(
        workspace=repo, state_root=state, task_id=task_id
    )
    graph_dir = layout.graph_root
    graph_db = _producer_graph(repo, graph_dir, os.environ["GT_INDEX_BINARY"])
    if cochanges:
        _insert_cochanges(graph_db, cochanges)
    _seal_graph(graph_dir, graph_db, task_id)

    adapter = MiniSweAdapter(
        task_id=task_id,
        state_dir=state,
        predicates=[],
        contract=extract_task_contract(issue),
        repo_root=repo,
        graph_db=str(graph_db),
        issue_text=issue,
        requested_model="fixture/model",
        resolved_model="fixture/model",
        layout=layout,
    )
    adapter.record_repository_snapshot(
        rt.capture_workspace(repo), boundary="task_start"
    )
    session = GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            repo_root=str(repo),
            state_dir=str(state),
            capabilities=SHIPPING_CAPABILITIES,
            issue_text=issue,
            mode=GTMode.ADVISORY,
            disabled_capabilities=disabled_capabilities,
        ),
        engine=adapter,
    )
    agent = _FakeAgent()
    agent.model = model or _TransportFakeModel()
    agent.env = _ShellEnv(repo)
    handle = rt.install_runtime_hooks(agent, session)
    return _Stack(
        repo=repo, state=state, layout=layout, graph_db=graph_db,
        adapter=adapter, session=session, agent=agent, handle=handle,
    )


def _rows(stack: _Stack) -> list[dict]:
    return [
        json.loads(line)
        for line in stack.adapter.store.path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def _by_event(rows: list[dict], event: str) -> list[dict]:
    return [row for row in rows if row.get("event") == event]


def _deliveries(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("event") in {"evidence_delivery", "context_addition_delivery"}
    ]


def _provider_deliveries(rows: list[dict]) -> list[dict]:
    return _by_event(rows, "provider_delivery")


def _delivery_for(rows: list[dict], kind: str) -> dict | None:
    return next(
        (
            row for row in _deliveries(rows)
            if row.get("kind") == kind or row.get("evidence_type") == kind
        ),
        None,
    )


def _request_rows_for(rows: list[dict], delivery_identity: str) -> list[dict]:
    return [
        row for row in _provider_deliveries(rows)
        if delivery_identity in (row.get("delivery_ids") or [])
    ]


def _turn(stack: _Stack, command: str, tool_call_id: str = "t"):
    """Execute one action through the shipping seam, then run one model turn.

    Returns ``(observation_messages, prepared_request_messages)``: the tool
    observation GT may have spliced, and the exact messages the provider
    transport received on the following ``query``.
    """
    obs = stack.agent.execute_actions(
        {"extra": {"actions": [{"command": command,
                                "tool_call_id": tool_call_id}]}}
    )
    history = [{"role": "user", "content": stack.adapter.issue_text}]
    history.extend(obs if isinstance(obs, list) else [])
    stack.agent.model.query(history)
    return obs, (stack.agent.model.calls[-1] if stack.agent.model.calls else [])


def _last_request_text(prepared: list[dict]) -> str:
    return "\n".join(
        str(message.get("content") or "") for message in prepared
    )


FIXTURE_FILES = {
    "src/service.py": (
        "def tokenize(text, limit=0):\n"
        "    return text.split()[:limit or None]\n"
    ),
    "src/util.py": (
        "from .service import tokenize\n\n"
        "def helper(s):\n    return tokenize(s, 3)\n"
    ),
    "src/other.py": (
        "from .service import tokenize\n\n"
        "def another(s):\n    return tokenize(s)\n"
    ),
    "tests/test_service.py": (
        "from src.service import tokenize\n\n"
        "def test_t():\n    assert tokenize('a b')\n"
    ),
}

ISSUE = (
    "The `tokenize` helper in `src/service.py` drops the trailing element "
    "when a limit is passed: `tokenize('a b c', 2)` must return three items."
)


# --------------------------------------------------------------------------
# Environment / profile surface
# --------------------------------------------------------------------------


def test_profile_env_fans_out_production_producer_flags(production_env):
    """The dispatch-time env must carry the Profile-2 producer gates."""
    for flag in (
        "GT_GATEWAY", "GT_GATEWAY_EDIT_BRIDGES", "GT_LOC_RESLOT",
        "GT_PATCH_DELTA", "GT_CHANGE_SURFACE", "GT_CS_EDIT_TRIGGER",
        "GT_REGISTRY_ENFORCE", "GT_EDIT_OVERLAY", "GT_VERIFY_EXECUTE",
        "GT_PERSISTENT_PLAN", "GT_SUBMIT_SUPPRESSION_ENFORCE",
    ):
        assert os.environ.get(flag) == "1", flag


def test_profile_env_explicit_zero_wins(monkeypatch):
    for key in list(os.environ):
        if key.startswith("GT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GT_LOC_RESLOT", "0")
    from gt_engine.bridge import apply_profile_env

    apply_profile_env()
    assert os.environ["GT_LOC_RESLOT"] == "0"


# --------------------------------------------------------------------------
# localization + GT_LOC_RESLOT
# --------------------------------------------------------------------------


def test_localization_without_catalog_ships_and_binds(
    production_env, tmp_path
):
    """Control: with select_catalog off, task-start localization must ship."""
    stack = _build_stack(
        tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-loc",
        disabled_capabilities=("select_catalog",),
    )
    stack.agent.model.query([{"role": "user", "content": ISSUE}])
    rows = _rows(stack)
    delivery = _delivery_for(rows, "localization")
    assert delivery is not None, (
        "task-start localization never delivered: "
        + json.dumps([r.get("event") for r in rows])
    )
    assert delivery["evidence_type"] == "localization"
    # The run journal is a verified hash chain end to end, not just rows.
    from gt_engine.event_journal import verify_event_journal

    verification = verify_event_journal(stack.adapter.store.path)
    assert not verification.issues, list(verification.issues)[:5]
    requests = _request_rows_for(rows, delivery["delivery_identity"])
    assert len(requests) == 1, "localization delivery must join exactly one request"
    prepared = stack.agent.model.calls[-1]
    text = _last_request_text(prepared)
    assert "[GT_EVIDENCE:localization]" in text
    assert "src/service.py" in text
    # The provider response must terminally confirm the same request.
    responses = [
        r for r in _by_event(rows, "provider_response")
        if r.get("request_id") == requests[0]["request_id"]
    ]
    assert responses and delivery["delivery_identity"] in responses[0][
        "delivery_ids"
    ]


def test_localization_survives_catalog_bootstrap(
    production_env, tmp_path
):
    """REGRESSION: the select_catalog bootstrap peeked at the localization
    resolution and consumed ``_localization_render_revision`` /
    ``_localization_drift_at_render`` -- afterwards ``resolution_pending``
    reported False forever and ``_task_start_shipped`` could never latch, so
    the ranked localization AND every later GT_LOC_RESLOT drift re-rank were
    silently dead on any run whose catalog was merely prepared. The peek must
    not consume the pending markers."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-loc-cat")
    stack.agent.model.query([{"role": "user", "content": ISSUE}])
    rows = _rows(stack)
    delivery = _delivery_for(rows, "localization")
    assert delivery is not None, (
        "localization suppressed after select_catalog bootstrap consumed "
        "its resolution; deliveries=" +
        json.dumps(
            [(r.get("kind"), r.get("delivery_identity")) for r in _deliveries(rows)]
        )
    )
    prepared = stack.agent.model.calls[-1]
    assert "[GT_EVIDENCE:localization]" in _last_request_text(prepared)


def test_loc_reslot_rerenders_on_search_drift(production_env, tmp_path):
    """GT_LOC_RESLOT: after the task-start localization ships, a new agent
    search term re-queues the recipe and re-resolves at admission. Whatever
    the resolver returns, the admission outcome must be journaled."""
    stack = _build_stack(
        tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-reslot",
        disabled_capabilities=("select_catalog",),
    )
    stack.agent.model.query([{"role": "user", "content": ISSUE}])
    assert _delivery_for(_rows(stack), "localization") is not None
    before = _rows(stack)

    _turn(stack, "grep -rn \"helper\" src/", "d1")
    rows = _rows(stack)
    new_rows = rows[len(before):]
    reslot = [
        r for r in new_rows
        if r.get("event") in {
            "evidence_delivery", "context_addition_delivery",
            "delivery_refusal", "delivery_prepared", "delivery_recipe_unresolved",
        }
        and (r.get("kind") == "localization"
             or "localization" in str(r.get("dedup_key") or ""))
    ]
    assert reslot, (
        "search drift did not re-queue the localization recipe: "
        + json.dumps([r.get("event") for r in new_rows])
    )


# --------------------------------------------------------------------------
# caller_contract
# --------------------------------------------------------------------------


def _pipeline(repo, graph_db, command, *, viewed=(), changed=(), eba=None,
              issue="x", action_index=1):
    """Run the certified pipeline directly with Linux-seam-shaped inputs."""
    from groundtruth.runtime.adapters.miniswe import normalize_event
    from groundtruth.runtime.episode_state import EpisodeState
    from groundtruth.runtime.gateway import GatewayState

    from gt_engine.miniswe_evidence import run_evidence_pipeline

    invocations: list[dict] = []
    state = GatewayState(
        graph_db=str(graph_db), repo_root=str(repo), issue_text=issue,
        episode=EpisodeState(episode_id="audit"),
        producer_recorder=invocations.append,
    )
    result = run_evidence_pipeline(
        state,
        normalize_event(
            command, "", 0, action_index, cwd=str(repo),
            changed_files=changed, viewed_files=viewed,
            edit_before_after=eba,
        ),
        dedup_chain=set(), chain_head="",
        episode_id="audit", event_id=f"audit:0:{action_index}",
        model_prefix=True,
    )
    return result, invocations


def test_caller_contract_view_producer_direct(production_env, tmp_path):
    """The view producer with repository-relative ``viewed_files`` (the shape
    the POSIX seam emits) must attach the FACT-tier callers of every
    definition in the viewed file."""
    repo = tmp_path / "repo"
    for rel, content in FIXTURE_FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content)
    graph = _producer_graph(
        repo, tmp_path / "g", os.environ["GT_INDEX_BINARY"]
    )
    result, invocations = _pipeline(
        repo, graph, "cat src/service.py", viewed=("src/service.py",)
    )
    kinds = [d.envelope.evidence_type for d in result.doses]
    assert kinds == ["caller_contract_view"], invocations
    rendered = result.doses[0].rendered
    assert "src/util.py" in rendered and "src/other.py" in rendered
    assert "tokenize" in rendered
    entered = [
        i for i in invocations
        if i.get("producer") == "caller_contract"
        and i.get("outcome") == "returned_fact"
    ]
    assert entered


@pytest.mark.skipif(
    os.name == "posix",
    reason="POSIX seam relativizes correctly; covered by the POSIX twin below",
)
def test_caller_contract_view_windows_seam_abstains_closed(
    production_env, tmp_path
):
    """Documents a dev-surface defect: ``_viewed_files`` resolves to OS-absolute
    paths; the certified wheel's ``_to_repo_rel`` relativizes only
    ``/``-prefixed (POSIX) paths, so on Windows the producer's
    ``confined_repo_file`` returns the absolute string, every definition match
    misses, and the producer abstains ``no_verified_caller_contract`` --
    correct-or-quiet, never a fabricated caller set. On the Linux task image
    the same seam emits ``/testbed/...`` and relativizes correctly; this test
    pins the Windows behavior so the gap is visible and fails closed."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-ccv")
    _turn(stack, "cat src/service.py", "v1")
    rows = _rows(stack)
    invocation = next(
        (
            r for r in _by_event(rows, "producer_invocation")
            if r.get("producer") == "caller_contract"
            and r.get("invocation_site") == "gateway.view.caller_contract_view"
            and r.get("outcome") == "returned_nothing"
        ),
        None,
    )
    assert invocation is not None, "view producer never evaluated"
    reasons = json.dumps(invocation.get("abstention_reasons") or [])
    assert "no_verified_caller_contract" in reasons
    # No caller_contract_view delivery may exist after a correct abstention.
    assert _delivery_for(rows, "caller_contract_view") is None


@pytest.mark.skipif(
    os.name != "posix",
    reason="the seam defect is Windows-only; on POSIX _to_repo_rel relativizes",
)
def test_caller_contract_view_posix_seam_returns_fact(
    production_env, tmp_path
):
    """POSIX twin of the Windows-seam pin: the same ``cat src/service.py``
    command resolves through ``_viewed_files`` to an OS-absolute POSIX path,
    which the certified wheel's ``_to_repo_rel`` relativizes under the repo
    root -- so the producer must return caller facts and a
    ``caller_contract_view`` delivery must exist. This is the installed-Linux
    recheck the audit ledger still owed."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-ccv")
    _turn(stack, "cat src/service.py", "v1")
    rows = _rows(stack)
    invocation = next(
        (
            r for r in _by_event(rows, "producer_invocation")
            if r.get("producer") == "caller_contract"
            and r.get("invocation_site") == "gateway.view.caller_contract_view"
            and r.get("outcome") == "returned_fact"
        ),
        None,
    )
    assert invocation is not None, "POSIX seam must deliver caller facts"
    assert _delivery_for(rows, "caller_contract_view") is not None


# --------------------------------------------------------------------------
# signature_delta + GT_PATCH_DELTA + caller_break (edit boundary)
# --------------------------------------------------------------------------

_EDIT = (
    "python - <<'PYEOF'\n"
    "from pathlib import Path\n"
    "p = Path('src/service.py')\n"
    "p.write_text('def tokenize(text):\\n    return text.split()\\n')\n"
    "PYEOF"
)


def test_signature_edit_producers_direct_pipeline(production_env, tmp_path):
    """CONTROL for D-beta: when ``edit_before_after`` reaches the gateway, the
    same repo+graph produces BOTH the patch-delta signature advisory and the
    caller-break fact from the pre-edit graph's CALLS edges."""
    repo = tmp_path / "repo"
    for rel, content in FIXTURE_FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content)
    graph = _producer_graph(
        repo, tmp_path / "g", os.environ["GT_INDEX_BINARY"]
    )
    before = (repo / "src/service.py").read_text()
    after = "def tokenize(text):\n    return text.split()\n"
    (repo / "src/service.py").write_text(after)
    result, invocations = _pipeline(
        repo, graph, _EDIT,
        changed=("src/service.py",),
        eba={"src/service.py": (before, after)},
    )
    kinds = [d.envelope.evidence_type for d in result.doses]
    assert "signature_mismatch" in kinds, (
        f"patch_delta produced no signature_mismatch when fed before/after: "
        f"{invocations}"
    )
    assert "caller_break" in kinds, (
        f"caller_contract produced no caller_break when fed before/after: "
        f"{invocations}"
    )
    rendered = "\n".join(d.rendered for d in result.doses)
    assert "src/util.py" in rendered
    # The test file is a leaky path: it must not be listed as a caller.
    assert "test_service.py" not in rendered


def test_signature_edit_seam_carries_edit_before_after(
    production_env, tmp_path
):
    """FIXED DEFECT D-beta (was cross-boundary, ``miniswe_runtime.py``):
    the shipping edit turn computes ``edit_before_after`` from the workspace
    transaction (``_capture_edit_after`` / ``diff_workspace``) and passes it
    into ``_run_evidence`` -- whose ``classify_event`` call used to drop it,
    so every edit_result event reached the gateway with
    ``edit_before_after=None`` and patch_delta / caller_contract abstained
    ``no_edit_before_after`` on EVERY shipping edit turn.

    Post-fix contract: the seam carries the pair, the edit producers return
    facts, and no ``no_edit_before_after`` abstention remains."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-edit")
    _turn(stack, _EDIT, "e1")
    rows = _rows(stack)

    edit_invocations = [
        r for r in _by_event(rows, "producer_invocation")
        if str(r.get("invocation_site") or "").startswith("gateway.edit")
    ]
    assert edit_invocations, (
        "the edit turn never reached the gateway edit producers: "
        + json.dumps(
            [r.get("invocation_site")
             for r in _by_event(rows, "producer_invocation")]
        )
    )
    sites = {r.get("invocation_site") for r in edit_invocations}
    assert "gateway.edit.patch_delta" in sites
    assert not any(
        "no_edit_before_after" in json.dumps(r.get("abstention_reasons") or [])
        or "no_edited_before_after_pair" in json.dumps(r.get("abstention_reasons") or [])
        for r in edit_invocations
    ), "edit_before_after still missing at the seam: " + json.dumps(
        edit_invocations
    )
    assert any(
        r.get("outcome") == "returned_fact" for r in edit_invocations
    ), "the edit producers produced nothing on a real signature edit: " + json.dumps(
        edit_invocations
    )


def test_body_only_edit_abstains_signature_producers(production_env, tmp_path):
    """A body-only edit must produce typed abstentions, never a false break."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-body")
    body_edit = (
        "python - <<'PYEOF'\n"
        "from pathlib import Path\n"
        "p = Path('src/service.py')\n"
        "p.write_text('def tokenize(text, limit=0):\\n"
        "    parts = text.split()\\n    return parts[:limit or None]\\n')\n"
        "PYEOF"
    )
    _turn(stack, body_edit, "e1")
    rows = _rows(stack)
    abstained = {
        r["producer"]
        for r in _by_event(rows, "producer_invocation")
        if r.get("outcome") == "returned_nothing"
    }
    assert "caller_contract" in abstained or "patch_delta" in abstained
    assert _delivery_for(rows, "caller_break") is None
    assert _delivery_for(rows, "signature_mismatch") is None


# --------------------------------------------------------------------------
# newfile_precedent + GT_CHANGE_SURFACE
# --------------------------------------------------------------------------


def test_file_creation_delivers_newfile_precedent(production_env, tmp_path):
    """Creating ``src/fmt.py`` must deliver exactly one new_file_destination
    dose naming the same-directory precedents, bound to the next request."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-nf")
    create = (
        "cat > src/fmt.py <<'PYEOF'\n"
        "def fmt(s):\n    return s.strip()\n"
        "PYEOF"
    )
    _turn(stack, create, "c1")
    rows = _rows(stack)
    deliveries = [
        r for r in _deliveries(rows)
        if r.get("kind") == "new_file_destination"
        or r.get("evidence_type") == "new_file_destination"
    ]
    assert len(deliveries) == 1, (
        "expected exactly one new_file_destination exposure, got "
        + json.dumps(deliveries)
    )
    prepared = stack.agent.model.calls[-1]
    text = _last_request_text(prepared)
    assert "new_file_destination" in text
    assert "src/fmt.py" in text
    assert "inspect=" in text  # sibling precedent rows


def test_modify_existing_file_no_newfile_delivery(production_env, tmp_path):
    """A modify transaction must not mint a new-file precedent."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-nf2")
    _turn(stack, _EDIT, "e1")
    rows = _rows(stack)
    assert not [
        r for r in _deliveries(rows)
        if r.get("evidence_type") == "new_file_destination"
        or r.get("kind") == "new_file_destination"
    ]


# --------------------------------------------------------------------------
# cochange_prior
# --------------------------------------------------------------------------


def test_cochange_history_unavailable_is_typed_not_negative(
    production_env, tmp_path
):
    """An empty cochanges table is 'history unavailable', never 'no partners'."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-cc")
    _turn(stack, "cat src/util.py", "v1")
    rows = _rows(stack)
    unresolved = _by_event(rows, "delivery_recipe_unresolved")
    assert any(
        r.get("kind") == "cochange"
        and r.get("reason") == "cochange_history_unavailable"
        for r in unresolved
    ), json.dumps(unresolved)
    assert _delivery_for(rows, "cochange_partner") is None


def test_cochange_prior_delivers_when_history_exists(
    production_env, tmp_path
):
    """With a populated cochanges table, the recipe resolves at admission and
    the advisory dose binds to the next provider request."""
    stack = _build_stack(
        tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-cc2",
        cochanges=[("src/util.py", "src/service.py", 4),
                   ("src/other.py", "src/service.py", 2)],
    )
    _turn(stack, "cat src/util.py", "v1")
    rows = _rows(stack)
    delivery = _delivery_for(rows, "cochange_partner")
    assert delivery is not None, (
        "cochange recipe never delivered: "
        + json.dumps(
            [r for r in rows
             if r.get("event") in {"delivery_recipe_unresolved",
                                   "evidence_delivery",
                                   "context_addition_delivery",
                                   "delivery_refusal"}]
        )
    )
    requests = _request_rows_for(rows, delivery["delivery_identity"])
    assert len(requests) == 1
    prepared = stack.agent.model.calls[-1]
    text = _last_request_text(prepared)
    assert "co-change prior" in text
    assert "src/service.py" in text
    assert "prior_not_resolution" in text


# --------------------------------------------------------------------------
# def_partition
# --------------------------------------------------------------------------


def test_def_partition_on_ambiguous_hit(production_env, tmp_path):
    """A symbol defined in TWO production files is AMBIGUOUS_HIT; the search
    must surface the definition/reference partition, delivered and bound."""
    files = dict(FIXTURE_FILES)
    files["src/dup.py"] = (
        "def tokenize(x):\n    return list(x)\n"
    )
    stack = _build_stack(tmp_path, ISSUE, files, task_id="audit-def")
    _turn(
        stack,
        'grep -rn "tokenize" src/ ; echo done', "s1",
    )
    rows = _rows(stack)
    invocation = next(
        (
            r for r in _by_event(rows, "producer_invocation")
            if r.get("producer") == "def_ref_partition"
        ),
        None,
    )
    assert invocation is not None, (
        "def_ref_partition never evaluated for a two-definition search: "
        + json.dumps(
            [r.get("invocation_site") for r in _by_event(rows, "producer_invocation")]
        )
    )
    if invocation["outcome"] == "returned_fact":
        delivery = _delivery_for(rows, "def_ref_partition")
        assert delivery is not None
        assert _request_rows_for(rows, delivery["delivery_identity"])
        text = _last_request_text(stack.agent.model.calls[-1])
        assert "src/service.py" in text or "src/dup.py" in text


def test_def_partition_not_reached_on_exact_hit(production_env, tmp_path):
    """A search whose single definition is already in the hits is EXACT_HIT:
    the partition producer must stay silent and the journal must show it was
    not dispatched."""
    stack = _build_stack(tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-def2")
    _turn(stack, 'grep -rn "tokenize" src/service.py', "s1")
    rows = _rows(stack)
    invocations = [
        r for r in _by_event(rows, "producer_invocation")
        if r.get("producer") == "def_ref_partition"
    ]
    assert not invocations or all(
        r.get("outcome") in {"not_entered"} or r.get("skip_reason")
        for r in invocations
    )
    assert _delivery_for(rows, "def_ref_partition") is None


# --------------------------------------------------------------------------
# select_catalog
# --------------------------------------------------------------------------


class _LiteMessage:
    """The message shape LitellmModel.query persists (needs model_dump)."""

    def __init__(self, tool_calls):
        self.content, self.tool_calls = "", tool_calls

    def model_dump(self, mode=None):
        return {"role": "assistant", "content": "",
                "tool_calls": self.tool_calls}


class _LiteResponse:
    """The response shape LitellmModel.query persists (needs model_dump)."""

    def __init__(self, message, identity):
        self.id, self.model = identity, "fixture/model"
        self.usage = {"prompt_tokens": 3, "completion_tokens": 1}
        self.choices = [SimpleNamespace(
            message=message, finish_reason="tool_calls")]

    def model_dump(self, mode=None):
        return {"id": self.id, "model": self.model, "usage": self.usage,
                "choices": [{"message": self.choices[0].message.model_dump()}]}


def test_select_catalog_valid_selection_litellm(
    production_env, tmp_path, monkeypatch
):
    """Same contract as above but the catalog model is LitellmModel from the
    start so the real ``_parse_actions`` seam runs."""
    import litellm
    from minisweagent.models.litellm_model import LitellmModel

    calls: list[dict] = []
    Message, Response = _LiteMessage, _LiteResponse

    def completion(*, model, messages, tools, **kwargs):
        calls.append({"messages": messages, "tools": tools})
        tool_name = tools[0]["function"]["name"]
        if tool_name == "select_catalog":
            request = json.loads(messages[-1]["content"].splitlines()[0])
            item_id = request["items"][0]["item_id"]
            function = SimpleNamespace(
                name="select_catalog",
                arguments=json.dumps({"ids": [item_id]}),
            )
            return Response(Message([SimpleNamespace(
                id="catalog-call", function=function)]), "bootstrap")
        function = SimpleNamespace(
            name="bash", arguments='{"command":"cat src/service.py"}')
        return Response(Message([SimpleNamespace(
            id="bash-call", function=function)]), "executor")

    model = LitellmModel(
        model_name="fixture/model", model_kwargs={}, cost_tracking="ignore_errors"
    )
    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(model, "_calculate_cost", lambda _: {"cost": 0.0})
    stack = _build_stack(
        tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-sc2", model=model
    )
    stack.agent.model.query([{"role": "user", "content": ISSUE}])
    rows = _rows(stack)
    lifecycle = _by_event(rows, "select_catalog_lifecycle")
    assert any(r.get("reason") == "selection_accepted" for r in lifecycle), (
        json.dumps(lifecycle)
    )
    results = [
        r for r in _deliveries(rows)
        if "select_catalog_result" in str(r.get("dedup_key") or "")
        or (r.get("kind") == "select_catalog" and r.get("action_index"))
    ]
    assert results, "selection result was never delivered to the agent request"
    prepared = calls[-1]["messages"]
    assert "GT_SELECT_CATALOG_RESULT" in _last_request_text(prepared)


def test_select_catalog_malformed_selection_abstains(
    production_env, tmp_path, monkeypatch
):
    """A malformed/obsolete selection records attempted-vs-selected honestly
    and never ships a fabricated result."""
    import litellm
    from minisweagent.models.litellm_model import LitellmModel

    Message, Response = _LiteMessage, _LiteResponse

    def completion(*, model, messages, tools, **kwargs):
        tool_name = tools[0]["function"]["name"]
        if tool_name == "select_catalog":
            function = SimpleNamespace(
                name="select_catalog",
                arguments=json.dumps({"ids": ["obsolete-id-zzz"]}),
            )
            return Response(Message([SimpleNamespace(
                id="catalog-call", function=function)]), "bootstrap")
        function = SimpleNamespace(
            name="bash", arguments='{"command":"echo hi"}')
        return Response(Message([SimpleNamespace(
            id="bash-call", function=function)]), "executor")

    model = LitellmModel(
        model_name="fixture/model", model_kwargs={}, cost_tracking="ignore_errors"
    )
    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(model, "_calculate_cost", lambda _: {"cost": 0.0})
    stack = _build_stack(
        tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-sc3", model=model
    )
    stack.agent.model.query([{"role": "user", "content": ISSUE}])
    rows = _rows(stack)
    lifecycle = _by_event(rows, "select_catalog_lifecycle")
    # An obsolete id is attempted but selects nothing: the run must record the
    # selection attempt and must not deliver a catalog result.
    assert lifecycle, "the selection attempt was never journaled"
    assert not any(
        r.get("reason") == "selection_accepted" for r in lifecycle
    )
    assert not [
        r for r in _deliveries(rows)
        if "select_catalog_result" in str(r.get("dedup_key") or "")
    ]


# --------------------------------------------------------------------------
# Scenario D5: simultaneous candidates -> refusal -> retry -> one exposure
# --------------------------------------------------------------------------


def test_d5_simultaneous_candidates_budget_refusal_retry(
    production_env, tmp_path
):
    """Two candidates at one decision: the oversized one is refused with a
    typed reason, re-queuing it does not consume state, and exactly one
    exposure is bound to the provider request."""
    stack = _build_stack(
        tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-d5",
        disabled_capabilities=("select_catalog",),
    )
    stack.agent.model.query([{"role": "user", "content": ISSUE}])
    base_rows = len(_rows(stack))

    fits = GTDecisionCandidate(
        rendered="[GT_EVIDENCE:execution_evidence]\nsmall fact",
        kind="execution_evidence", dedup_key="d5:small", lane="sealed",
        target="src/util.py",
        source_revision=str(stack.adapter.repository_revision),
    )
    oversized = GTDecisionCandidate(
        rendered="[GT_EVIDENCE:execution_evidence]\n" + ("x" * 4000),
        kind="execution_evidence", dedup_key="d5:big", lane="sealed",
        target="src/util.py",
        source_revision=str(stack.adapter.repository_revision),
    )
    stack.session.queue_decision_candidates([fits, oversized])
    stack.agent.model.query([{"role": "user", "content": "next"}])
    rows = _rows(stack)
    new = rows[base_rows:]
    refusals = _by_event(new, "delivery_refused")
    big_refusals = [
        r for r in refusals if r.get("dedup_key") == "d5:big"
    ]
    assert big_refusals and all(
        r.get("reason") == "delivery_byte_ceiling" for r in big_refusals
    ), "oversized candidate must be refused typed: " + json.dumps(refusals)
    # Exactly one exposure from this decision: the small candidate is admitted
    # and bound to the exact next provider request; the big one never was.
    request = _provider_deliveries(new)[-1]
    small = next(
        r for r in _deliveries(new) if r.get("dedup_key") == "d5:small"
    )
    assert small["delivery_identity"] in request["delivery_ids"]
    big_deliveries = [
        r for r in _deliveries(new) if r.get("dedup_key") == "d5:big"
    ]
    assert not big_deliveries, "refused candidate must never be delivered"
    # Retry: a refusal consumed no dedup key or chain state, so re-queuing the
    # oversized candidate refuses again with the same typed reason -- and it
    # still never reaches a provider request.
    stack.session.queue_decision_candidates([oversized])
    base2 = len(rows)
    stack.agent.model.query([{"role": "user", "content": "again"}])
    rows2 = _rows(stack)[base2:]
    retry_refusals = [
        r for r in _by_event(rows2, "delivery_refused")
        if r.get("dedup_key") == "d5:big"
    ]
    assert retry_refusals and all(
        r.get("reason") == "delivery_byte_ceiling" for r in retry_refusals
    ), "refused candidate must refuse typed again on retry"
    assert not [
        r for r in _deliveries(_rows(stack)) if r.get("dedup_key") == "d5:big"
    ]
    # And no request ever carried both candidates: each request's delivery_ids
    # is the exposure boundary.
    all_requests = _provider_deliveries(_rows(stack))
    for req in all_requests:
        ids = req.get("delivery_ids") or []
        assert not (
            small["delivery_identity"] in ids
            and any(
                r.get("delivery_identity") in ids for r in big_deliveries
            )
        )


# --------------------------------------------------------------------------
# Layer 4: corrupt one boundary per test; the audit evidence must go red
# --------------------------------------------------------------------------


class _FlakyTransportModel(_TransportFakeModel):
    """The recording transport that fails once on the wire."""

    def __init__(self):
        super().__init__()
        self.attempts = 0

    def _query(self, messages, **kwargs):
        self.attempts += 1
        if self.attempts == 1:
            raise TimeoutError("synthetic transport timeout")
        return super()._query(messages, **kwargs)


class TestLayer4Mutations:
    """Each test corrupts exactly one boundary named in the audit spec and
    asserts the journal reflects the breakage: delivery absent, refusal or
    abstention typed. A green ability verdict under these mutations would be
    a proof defect in this audit, not a pass."""

    def test_producer_disconnected_is_visible(
        self, production_env, tmp_path, monkeypatch
    ):
        """Mutation: the resolver returns nothing despite an eligible input.
        The localization delivery must disappear AND a typed
        ``delivery_recipe_unresolved`` row must name the dead lane."""
        stack = _build_stack(
            tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-m1",
            disabled_capabilities=("select_catalog",),
        )
        native = stack.adapter.resolve_delivery_recipe

        def dead_resolver(recipe):
            if (recipe or {}).get("kind") == "localization":
                return None
            return native(recipe)

        monkeypatch.setattr(
            stack.adapter, "resolve_delivery_recipe", dead_resolver
        )
        stack.agent.model.query([{"role": "user", "content": ISSUE}])
        rows = _rows(stack)
        assert _delivery_for(rows, "localization") is None
        assert any(
            r.get("kind") == "localization"
            for r in _by_event(rows, "delivery_recipe_unresolved")
        )
        assert "[GT_EVIDENCE:localization]" not in _last_request_text(
            stack.agent.model.calls[-1]
        )

    def test_dispatch_disconnected_is_visible(
        self, production_env, tmp_path, monkeypatch
    ):
        """Mutation: the gateway flag off disconnects shipping dispatch. No
        gateway producer may be invoked and no gateway evidence may ship."""
        monkeypatch.setenv("GT_GATEWAY", "0")
        stack = _build_stack(
            tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-m2",
            disabled_capabilities=("select_catalog",),
        )
        stack.agent.model.query([{"role": "user", "content": ISSUE}])
        _turn(stack, "cat src/service.py", "v1")
        rows = _rows(stack)
        gateway_sites = [
            r for r in _by_event(rows, "producer_invocation")
            if str(r.get("invocation_site") or "").startswith("gateway.")
        ]
        assert not gateway_sites, (
            "gateway producers ran with GT_GATEWAY=0: "
            + json.dumps(gateway_sites)
        )
        assert _delivery_for(rows, "caller_contract_view") is None

    def test_missing_graph_keeps_pipeline_typed(
        self, production_env, tmp_path
    ):
        """Mutation: the required graph ancestor is absent. The direct
        pipeline must abstain typed rather than fabricate caller facts."""
        repo = tmp_path / "repo"
        for rel, content in FIXTURE_FILES.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content)
        missing = tmp_path / "does-not-exist.db"
        result, invocations = _pipeline(
            repo, missing, "cat src/service.py", viewed=("src/service.py",)
        )
        assert not result.doses
        assert invocations, "producer was never evaluated"
        assert all(
            i.get("outcome") != "returned_fact" for i in invocations
        )

    def test_admission_drop_never_reaches_request(
        self, production_env, tmp_path, monkeypatch
    ):
        """Mutation: admission silently drops every candidate. Prepared
        bytes must not appear in the provider request and no delivery row
        may be journaled."""
        stack = _build_stack(
            tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-m4",
            disabled_capabilities=("select_catalog",),
        )
        monkeypatch.setattr(
            stack.adapter, "admit_model_visible_delivery",
            lambda **_kwargs: False,
        )
        stack.agent.model.query([{"role": "user", "content": ISSUE}])
        rows = _rows(stack)
        assert not _deliveries(rows)
        assert not _by_event(rows, "delivery_prepared")
        assert "[GT_EVIDENCE:localization]" not in _last_request_text(
            stack.agent.model.calls[-1]
        )

    def test_transport_failure_prepared_is_not_exposed(
        self, production_env, tmp_path
    ):
        """Mutation: response binding is absent on the first attempt. A
        ``delivery_prepared`` row is admission, not exposure: no
        ``provider_delivery``/``evidence_delivery`` may bind it. On retry the
        SAME delivery identity binds to the next request and its response."""
        model = _FlakyTransportModel()
        stack = _build_stack(
            tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-m5",
            disabled_capabilities=("select_catalog",), model=model,
        )
        messages = [{"role": "user", "content": ISSUE}]
        with pytest.raises(TimeoutError):
            stack.agent.model.query(messages)
        rows = _rows(stack)
        prepared = [
            r for r in _by_event(rows, "delivery_prepared")
            if r.get("kind") == "localization"
        ]
        assert prepared, "localization was never admitted"
        identity = prepared[0]["delivery_identity"]
        # Prepared is not exposed: the failed wire attempt must not journal
        # a delivery binding or a response.
        assert _request_rows_for(rows, identity) == []
        assert _delivery_for(rows, "localization") is None
        assert _by_event(rows, "provider_attempt_failed")

        stack.agent.model.query(messages)
        rows = _rows(stack)
        requests = _request_rows_for(rows, identity)
        assert len(requests) == 1, (
            "pending delivery did not bind the retry request"
        )
        responses = [
            r for r in _by_event(rows, "provider_response")
            if r.get("request_id") == requests[0]["request_id"]
        ]
        assert responses and identity in responses[0]["delivery_ids"]

    def test_cochange_ghost_partner_is_labeled_prior_not_existence(
        self, production_env, tmp_path
    ):
        """Mutation: co-change history names a partner the current checkout
        does not contain (deleted/renamed file). A prior may still ship --
        it claims historical coupling, not current existence -- but it must
        name its exact table row and carry ``status=prior_not_resolution``
        so nothing reads it as a resolution or a file-existence claim."""
        stack = _build_stack(
            tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-m6",
            cochanges=[("src/util.py", "vendor/ghost.py", 9)],
        )
        _turn(stack, "cat src/util.py", "v1")
        rows = _rows(stack)
        delivery = _delivery_for(rows, "cochange_partner")
        if delivery is None:
            # Also honest: the lane may abstain typed when no deliverable
            # partner survives; the skip must be journaled either way.
            assert any(
                r.get("kind") == "cochange"
                for r in _by_event(rows, "delivery_recipe_unresolved")
            )
            return
        text = _last_request_text(stack.agent.model.calls[-1])
        assert "prior_not_resolution" in text
        assert "provenance=cochanges(file_a=src/util.py,file_b=vendor/ghost.py)" \
            in text
        # A renamed/deleted partner is reported as history, never dressed up
        # as a file the agent could open.
        assert "inspect=vendor/ghost.py" not in text

    def test_unchanged_signature_produces_no_mismatch(
        self, production_env, tmp_path
    ):
        """Mutation: the edit event carries identical before/after. The
        producer must not claim a signature change that never happened."""
        repo = tmp_path / "repo"
        for rel, content in FIXTURE_FILES.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content)
        graph = _producer_graph(
            repo, tmp_path / "g", os.environ["GT_INDEX_BINARY"]
        )
        same = (repo / "src/service.py").read_text()
        result, invocations = _pipeline(
            repo, graph, _EDIT,
            changed=("src/service.py",),
            eba={"src/service.py": (same, same)},
        )
        kinds = [d.envelope.evidence_type for d in result.doses]
        assert "signature_mismatch" not in kinds
        assert "caller_break" not in kinds

    def test_catalog_transport_failure_abstains(
        self, production_env, tmp_path, monkeypatch
    ):
        """Mutation: the internal catalog request fails on the wire. The
        lifecycle must abstain typed; no fabricated selection result may
        reach the agent request."""
        import litellm
        from minisweagent.models.litellm_model import LitellmModel

        # One attempt only: the failure path is the audit target, not the
        # provider's real retry budget.
        monkeypatch.setenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "1")
        Message, Response = _LiteMessage, _LiteResponse

        def completion(*, model, messages, tools, **kwargs):
            tool_name = tools[0]["function"]["name"]
            if tool_name == "select_catalog":
                raise TimeoutError("synthetic catalog transport failure")
            function = SimpleNamespace(
                name="bash", arguments='{"command":"echo hi"}')
            return Response(Message([SimpleNamespace(
                id="bash-call", function=function)]), "executor")

        model = LitellmModel(
            model_name="fixture/model", model_kwargs={},
            cost_tracking="ignore_errors",
        )
        monkeypatch.setattr(litellm, "completion", completion)
        monkeypatch.setattr(model, "_calculate_cost", lambda _: {"cost": 0.0})
        stack = _build_stack(
            tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-m8", model=model
        )
        stack.agent.model.query([{"role": "user", "content": ISSUE}])
        rows = _rows(stack)
        assert not [
            r for r in _deliveries(rows)
            if "select_catalog_result" in str(r.get("dedup_key") or "")
        ], "a failed catalog call must never mint a selection result"


# --------------------------------------------------------------------------
# Scenario D1: localization -> view -> caller -> signature edit -> evidence
# --------------------------------------------------------------------------


def test_d1_localization_to_caller_evidence(production_env, tmp_path):
    """The D1 chain on the shipping boundary: task-start localization ships;
    the view reaches the caller-contract producer with a typed outcome; the
    signature edit reaches the edit producers and ``caller_break`` delivers
    and binds. Defect D-beta (the seam dropping ``edit_before_after``) is
    fixed; a ``None`` delivery here is now a hard regression, and a typed
    ``no_edit_before_after`` abstention must never reappear."""
    stack = _build_stack(
        tmp_path, ISSUE, FIXTURE_FILES, task_id="audit-d1",
        disabled_capabilities=("select_catalog",),
    )
    stack.agent.model.query([{"role": "user", "content": ISSUE}])
    rows = _rows(stack)
    assert _delivery_for(rows, "localization") is not None

    _turn(stack, "cat src/service.py", "v1")
    rows = _rows(stack)
    assert any(
        r.get("invocation_site") == "gateway.view.caller_contract_view"
        for r in _by_event(rows, "producer_invocation")
    ), "view did not reach the caller-contract producer"

    _turn(stack, _EDIT, "e1")
    rows = _rows(stack)
    edit_invocations = [
        r for r in _by_event(rows, "producer_invocation")
        if str(r.get("invocation_site") or "").startswith("gateway.edit")
    ]
    assert edit_invocations, "edit turn did not reach the edit producers"
    caller = _delivery_for(rows, "caller_break")
    assert caller is not None, (
        "caller_break did not deliver on a real signature edit -- "
        "if abstention_reasons show no_edit_before_after, D-beta regressed: "
        + json.dumps(
            [r.get("abstention_reasons") for r in edit_invocations]
        )
    )
    requests = _request_rows_for(rows, caller["delivery_identity"])
    assert len(requests) == 1
    text = _last_request_text(stack.agent.model.calls[-1])
    assert "caller(s)" in text or "signature changed" in text
    # Subsequent state: the edit was journaled as an edit transaction and the
    # graph went stale-and-typed rather than silently fresh.
    assert _by_event(rows, "edit_transaction") or _by_event(
        rows, "repository_snapshot")
