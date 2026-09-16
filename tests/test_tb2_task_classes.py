"""TB2 host route: one fixture workspace per equivalence class A-F.

The 89 Terminal-Bench 2.0 tasks fall into six shapes (counts and
representatives from the TB2 inventory):

A (5, break-filter-js-from-html) grading tests COPYd into /app; B (3,
fix-code-vulnerability) the workspace owns a runnable suite; C (6, fix-git) a
cloned repo with history and nothing to run; D (46, prove-plus-comm) 1-3 seed
files producing an artifact; E (13, nginx-request-logging) success is a
listening port, not a file; F (16, polyglot-c-py) zero payload files.

Each fixture is built in ``tmp_path`` from the representative's shape - the
corpus itself is not vendored - and driven through ``MiniSweCentralAgent.run``
with a scripted model performing that class's canonical sequence: search,
edit, run the class's verification command, submit.  The environment replays
the real tmp_path tree over ``pwd -P``, the ``find -printf`` manifest,
``sha256sum`` and the base64 capture probe, and applies each edit to the tree,
so ``WorkspaceSensor`` observes genuine transitions.

Declared limitation, stated rather than papered over.  There is no
``gt-index`` binary on this host, so ``RepositorySession.refresh`` cannot
build a graph and every source-bearing class reports
``repository_evidence.status == "index_unavailable"``.  The trigger sets
pinned below are therefore the **graph-less** trigger sets.  Graph-backed
rows (``caller_contract``, ``def_partition`` with real definitions/references,
``register_structural_evidence``) remain UNPROVEN here, not absent - per
``D:\\gt-context-plan\\docs\\AUDIT-ABILITY-SPEC.md`` section 4 layer 3, a
missing asset yields UNPROVEN, never a skipped pass.

Nothing under ``eval/`` or ``gt_engine/`` is modified.  No provider call, no
benchmark dispatch, no Docker.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext

from eval.gt_central_agent import MiniSweCentralAgent
from gt_engine.central_runtime import syntax_probe_command
from gt_engine.repository_intelligence import (
    RepositoryApplicability,
    RepositoryEvidence,
    RepositoryIntelligenceStatus,
    RepositorySubstrateStatus,
    classify_repository_applicability,
)

SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

# The directory names _MANIFEST_COMMAND prunes (gt_engine/central_runtime.py:63).
PRUNED = frozenset(
    ".git .hg .svn .gt .groundtruth node_modules .venv venv __pycache__ "
    ".tox .mypy_cache .ruff_cache dist build target".split()
)

# The three abilities whose only boundary is ``test_result``, plus the red leg
# of submission evidence.  The inventory calls them BROKEN on the 81 tasks
# where the agent cannot run the grading suite; the per-class runs below show
# they stay silent even on class A and B, where a test_result boundary does
# occur.
TEST_RESULT_ONLY_FEATURES = ("covering_red", "recovery", "GT_HYPOTHESIS", "GT_SS_SUBMIT_RED")


def _manifest(root: Path) -> str:
    rows: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        parts = relative.split("/")
        if any(part in PRUNED for part in parts):
            continue
        if path.is_dir():
            rows.append(f"d\t4096\t1.0\t1.0\t{relative}\t")
        else:
            rows.append(f"f\t{path.stat().st_size}\t1.0\t1.0\t{relative}\t")
    rows.sort()
    return "".join(row + "\n" for row in rows)


class _WorkspaceEnvironment:
    """Fake Harbor environment replaying one real tmp_path tree.

    Shape follows the stub at tests/test_gt_central_agent.py:880; the
    ``download_dir_with_exclusions`` seam is the provider-free transfer path
    ``eval/gt_central_agent.py:3084`` documents.
    """

    default_user = "root"

    def __init__(self, root: Path, handlers: dict, *, lint_fault: Exception | None = None):
        self.root = root
        self.handlers = handlers
        self.lint_fault = lint_fault
        self.commands: list[str] = []

    async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        excluded = set(exclude)
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root)
            if any(part in excluded for part in relative.parts):
                continue
            if path.is_dir():
                (target / relative).mkdir(parents=True, exist_ok=True)
            else:
                (target / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target / relative)

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.commands.append(command)
        if command == "pwd -P":
            return ExecResult(stdout="/app\n", return_code=0)
        if command.startswith("uname "):
            return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
        if "-printf" in command and "find ." in command:
            return ExecResult(stdout=_manifest(self.root), return_code=0)
        if command.startswith("sha256sum -- "):
            return self._sha256sum(command)
        if command.startswith("python3 -c "):
            return self._capture(command)
        if "py_compile" in command:
            if self.lint_fault is not None:
                raise self.lint_fault
            return ExecResult(stdout="", return_code=0)
        if command == SUBMIT:
            return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
        handler = self.handlers.get(command)
        if handler is not None:
            return handler(self.root)
        return ExecResult(stdout="", return_code=0)

    def _sha256sum(self, command: str) -> ExecResult:
        lines = []
        for relative in shlex.split(command)[2:]:
            target = self.root / relative
            blob = target.read_bytes() if target.is_file() else b""
            lines.append(f"{hashlib.sha256(blob).hexdigest()}  {relative}")
        return ExecResult(stdout="\n".join(lines) + "\n", return_code=0)

    def _capture(self, command: str) -> ExecResult:
        payload: dict[str, str] = {}
        for relative in shlex.split(command)[3:]:
            target = self.root / relative
            if target.is_file():
                payload[relative] = base64.b64encode(target.read_bytes()).decode("ascii")
        return ExecResult(stdout=json.dumps(payload) + "\n", return_code=0)


class _ScriptedModel:
    """Mirror of the injected model at tests/test_gt_central_agent.py:1341."""

    config = type("Config", (), {"model_name": "test"})()
    tools = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]

    def __init__(self, commands):
        self.script = list(commands)
        self.index = 0
        self.observed: list[str] = []
        self.observed_history: list[list[str]] = []

    def format_message(self, **kwargs):
        return kwargs

    def get_template_vars(self):
        return {"observation_template": "{{ output.output }}", "format_error_template": "error"}

    def query(self, messages):
        self.observed = [str(item.get("content") or "") for item in messages]
        self.observed_history.append(list(self.observed))
        command = self.script[min(self.index, len(self.script) - 1)]
        self.index += 1
        return {
            "role": "assistant",
            "content": "act",
            "extra": {
                "actions": [{"command": command, "tool_call_id": "call-1"}],
                "response": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
                },
                "cost": 0.0,
            },
        }

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [{"role": "tool", "content": outputs[0]["output"]}]


# ---------------------------------------------------------------------------
# One fixture workspace per class.
# ---------------------------------------------------------------------------


def _ok(stdout: str = "", returncode: int = 0):
    return lambda _root: ExecResult(stdout=stdout, return_code=returncode)


def _rewrite(relative: str, old: str, new: str):
    def apply(root: Path) -> ExecResult:
        path = root / relative
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        return ExecResult(stdout="", return_code=0)

    return apply


def _build_class_a(root: Path):
    """break-filter-js-from-html: grading tests are COPYd into /app."""
    (root / "tests").mkdir(parents=True)
    (root / "filter.py").write_text(
        "import re\n\n\ndef filter_js(html):\n"
        "    return re.sub('<script.*?</script>', '', html)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_outputs.py").write_text(
        "from filter import filter_js\n\n\ndef test_filter():\n"
        "    assert filter_js('<script>x</script>a') == 'a'\n",
        encoding="utf-8",
    )
    search = "rg --line-number filter_js ."
    edit = "sed -i 's/re.sub/re.sub-with-dotall/' filter.py"
    verify = "pytest tests/test_outputs.py -q"
    handlers = {
        search: _ok(
            "filter.py:4:def filter_js(html):\n"
            "tests/test_outputs.py:1:from filter import filter_js\n"
        ),
        edit: _rewrite(
            "filter.py",
            "return re.sub('<script.*?</script>', '', html)",
            "return re.sub(r'<script.*?</script>', '', html, flags=re.S)",
        ),
        verify: _ok("1 passed in 0.01s\n"),
    }
    instruction = "Repair filter_js in filter.py so tests/test_outputs.py passes."
    return instruction, [search, edit, verify, SUBMIT, SUBMIT], handlers


def _build_class_b(root: Path):
    """fix-code-vulnerability: the workspace ships its own runnable suite."""
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        "import os\n\n\ndef verify(token, expected):\n    return token == expected\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_auth.py").write_text(
        "from src.auth import verify\n\n\ndef test_verify():\n    assert verify('a', 'a')\n",
        encoding="utf-8",
    )
    search = "rg --line-number verify src"
    edit = "sed -i 's/token == expected/compare_digest/' src/auth.py"
    verify = "pytest -q"
    handlers = {
        search: _ok("src/auth.py:4:def verify(token, expected):\n"),
        edit: _rewrite("src/auth.py", "import os", "import hmac\nimport os"),
        verify: _ok("2 passed in 0.02s\n"),
    }
    instruction = (
        "Fix the timing vulnerability in src/auth.py verify and keep tests/test_auth.py green."
    )
    return instruction, [search, edit, verify, SUBMIT, SUBMIT], handlers


def _build_class_c(root: Path):
    """fix-git: a real .git with two commits and nothing runnable."""
    (root / "content").mkdir(parents=True)
    (root / "content" / "about.md").write_text("# About\n", encoding="utf-8")
    (root / "default.html").write_text("<html></html>\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    (root / "content" / "about.md").write_text("# About\n\nSecond.\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "second")

    search = "grep -rn Second content"
    edit = "sed -i 's/Second/Restored/' content/about.md"
    verify = "git log --oneline"
    handlers = {
        search: _ok("content/about.md:3:Second.\n"),
        edit: _rewrite("content/about.md", "Second", "Restored"),
        verify: _ok("aaaaaaa second\nbbbbbbb initial\n"),
    }
    instruction = "Restore the missing content so the repository history is intact."
    return instruction, [search, edit, verify, SUBMIT, SUBMIT], handlers


def _git(root: Path, *args: str) -> None:
    command = ["git"]
    if args[0] != "init":
        command += ["-C", str(root)]
    command += list(args)
    subprocess.run(command, check=True, capture_output=True)


def _build_class_d(root: Path):
    """prove-plus-comm: one seed file, produce an artifact at a named path."""
    (root / "partial_proof.v").write_text(
        "Theorem plus_comm : forall n m : nat, n + m = m + n.\nProof.\nAdmitted.\n",
        encoding="utf-8",
    )
    search = "grep -n plus_comm partial_proof.v"
    edit = "sed -i 's/Admitted./Qed./' partial_proof.v"
    verify = "coqc partial_proof.v"
    handlers = {
        search: _ok("1:Theorem plus_comm : forall n m : nat, n + m = m + n.\n"),
        edit: _rewrite("partial_proof.v", "Admitted.", "  induction n; auto.\nQed."),
        verify: _ok(""),
    }
    instruction = "Complete the proof in partial_proof.v and write the compiled proof."
    return instruction, [search, edit, verify, SUBMIT, SUBMIT], handlers


def _build_class_e(root: Path):
    """nginx-request-logging: success is a listening service, not a file."""
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "nginx.conf").write_text(
        "http {\n    server {\n        listen 80;\n    }\n}\n", encoding="utf-8"
    )
    search = "grep -n listen etc/nginx.conf"
    edit = "sed -i 's/listen 80;/listen 80; access_log/' etc/nginx.conf"
    verify = "curl -s -o /dev/null -w '%{http_code}' http://localhost/"
    handlers = {
        search: _ok("3:        listen 80;\n"),
        edit: _rewrite(
            "etc/nginx.conf",
            "listen 80;",
            "listen 80;\n        access_log /var/log/nginx/access.log;",
        ),
        verify: _ok("200"),
    }
    instruction = (
        "Configure nginx to log every request to /var/log/nginx/access.log and reload it."
    )
    return instruction, [search, edit, verify, SUBMIT, SUBMIT], handlers


def _build_class_f(root: Path):
    """polyglot-c-py: zero payload files; the workspace is whatever RUN built."""
    root.mkdir(parents=True, exist_ok=True)
    search = "find . -maxdepth 1 -type f"
    edit = "tee poly.c > /dev/null"
    verify = "gcc -o poly poly.c && ./poly"

    def create(target: Path) -> ExecResult:
        (target / "poly.c").write_text('#if 0\n"""\n#endif\n', encoding="utf-8")
        return ExecResult(stdout="", return_code=0)

    handlers = {search: _ok(""), edit: create, verify: _ok("hello\n")}
    instruction = "Create /app/poly.c that is valid C and valid Python and prints the same output."
    return instruction, [search, edit, verify, SUBMIT, SUBMIT], handlers


BUILDERS = {
    "A": _build_class_a,
    "B": _build_class_b,
    "C": _build_class_c,
    "D": _build_class_d,
    "E": _build_class_e,
    "F": _build_class_f,
}


class _ClassRun:
    def __init__(self, logs_dir: Path, receipt: dict, terminal: str, environment):
        self.logs_dir = logs_dir
        self.receipt = receipt
        self.terminal = terminal
        self.environment = environment

    @property
    def features(self) -> dict:
        return self.receipt["features"]

    @property
    def fired(self) -> set[str]:
        return {row["feature_id"] for row in self.features["receipts"]}

    @property
    def boundaries(self) -> set[tuple[str, str]]:
        return {(row["feature_id"], row["boundary"]) for row in self.features["receipts"]}

    @property
    def persistent_state(self) -> dict:
        return self.receipt["persistent_execution_state"]


async def _run_class(name: str, tmp_path: Path, *, lint_fault: Exception | None = None):
    root = tmp_path / "app"
    root.mkdir(parents=True, exist_ok=True)
    instruction, script, handlers = BUILDERS[name](root)
    logs_dir = tmp_path / "logs"
    model = _ScriptedModel(script)
    agent = MiniSweCentralAgent(
        logs_dir=logs_dir,
        model_name="test",
        integration_mode="active",
        policy_mode="certified_active",
        enable_persistent_execution_state=True,
        enable_preemptive_retrieval=False,
        enable_feature_guidance=False,
        step_limit=8,
    )
    agent._model_factory = lambda: model
    environment = _WorkspaceEnvironment(root, handlers, lint_fault=lint_fault)
    await agent.run(instruction, environment, AgentContext())
    receipt = json.loads((logs_dir / "central_receipt.json").read_text(encoding="utf-8"))
    trajectory = json.loads((logs_dir / "miniswe_trajectory.json").read_text(encoding="utf-8"))
    return _ClassRun(
        logs_dir, receipt, str((trajectory.get("info") or {}).get("exit_status") or ""), environment
    )


# ---------------------------------------------------------------------------
# Per-class trigger sets.
# ---------------------------------------------------------------------------

EXPECTED_TRIGGERS = {
    # Classes A and B reach an edit with a lintable Python file and a passing
    # verifier, so they are the only classes where the edit-check and
    # test_result rows exist at all.
    "A": {
        "obligations",
        "localization",
        "GT_LOC_RESLOT",
        "syntax_result",
        "GT_EDIT_CHECK",
        "GT_CHANGE_SURFACE",
        "GT_PATCH_DELTA",
        "GT_CERT_DELIVERY",
    },
    "B": {
        "obligations",
        "localization",
        "GT_LOC_RESLOT",
        "syntax_result",
        "GT_EDIT_CHECK",
        "GT_CHANGE_SURFACE",
        "GT_PATCH_DELTA",
        "GT_CERT_DELIVERY",
    },
    # C: a doc-only search output carries no anchors, so localization never
    # fires even though the _SEARCH trigger matched.
    "C": {"obligations", "GT_CHANGE_SURFACE", "GT_PATCH_DELTA", "GT_CERT_DELIVERY"},
    # D and E: anchors exist, but .v and .conf have no syntax probe, so the
    # edit-check family is genuinely NOT APPLICABLE rather than missing.
    "D": {
        "obligations",
        "localization",
        "GT_LOC_RESLOT",
        "GT_CHANGE_SURFACE",
        "GT_PATCH_DELTA",
        "GT_CERT_DELIVERY",
    },
    "E": {
        "obligations",
        "localization",
        "GT_LOC_RESLOT",
        "GT_CHANGE_SURFACE",
        "GT_PATCH_DELTA",
        "GT_CERT_DELIVERY",
    },
    # F: nothing to search, so only the task_start and edit/submit rows exist.
    "F": {"obligations", "GT_CHANGE_SURFACE", "GT_PATCH_DELTA", "GT_CERT_DELIVERY"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(BUILDERS), ids=sorted(BUILDERS))
async def test_class_fixture_reaches_submitted(tmp_path, name):
    run = await _run_class(name, tmp_path)
    assert run.terminal == "Submitted"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(BUILDERS), ids=sorted(BUILDERS))
async def test_class_trigger_set(tmp_path, name):
    run = await _run_class(name, tmp_path)
    assert run.fired == EXPECTED_TRIGGERS[name]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(BUILDERS), ids=sorted(BUILDERS))
async def test_test_result_only_abilities_never_fire_on_any_class(tmp_path, name):
    """Boundary starvation, proven in its stronger form.

    ``covering_red``, ``recovery`` and the red leg of ``submit_refusal`` are
    enabled on every class.  On C/D/E/F the ``test_result`` boundary cannot
    occur at all; on A and B it does occur - GT_CERT_DELIVERY carries a
    ``test_result`` row - and the three abilities still produce nothing.  The
    receipt records no difference between "never triggered" and "triggered and
    produced nothing".
    """
    run = await _run_class(name, tmp_path)
    assert not run.fired & set(TEST_RESULT_ONLY_FEATURES)
    assert "submit_refusal" not in run.fired

    test_result_rows = {
        feature for feature, boundary in run.boundaries if boundary == "test_result"
    }
    if name in {"A", "B"}:
        assert test_result_rows == {"GT_CERT_DELIVERY"}
    else:
        assert test_result_rows == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["A", "B"], ids=["A", "B"])
async def test_edit_check_family_fires_only_where_a_syntax_probe_exists(tmp_path, name):
    run = await _run_class(name, tmp_path)
    assert {"syntax_result", "GT_EDIT_CHECK"} <= run.fired
    assert run.features["action_metrics"]["lint_checks"] >= 1
    assert run.features["action_metrics"]["lint_failures"] == 0


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("filter.py", True),
        ("src/auth.py", True),
        ("content/about.md", False),
        ("partial_proof.v", False),
        ("etc/nginx.conf", False),
        ("poly.c", False),
    ],
)
def test_syntax_probe_availability_is_the_edit_check_applicability_boundary(relative, expected):
    """Independent semantic expectation for the row above.

    ``syntax_result``/``GT_EDIT_CHECK`` are genuinely NOT APPLICABLE on the
    .v/.conf/.gcode/.red/.scm tasks because no certified post-image parser
    exists for them - not because a producer failed.
    """
    assert (syntax_probe_command(relative) is not None) is expected


# ---------------------------------------------------------------------------
# persistent_execution_state on D and F.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_class_f_persistent_state_is_not_applicable_no_supported_source(tmp_path):
    """Class F is the source-less shape, and the host route says so correctly."""
    run = await _run_class("F", tmp_path)
    state = run.persistent_state

    assert state["initialization"]["status"] == "not_applicable"
    assert state["initialization"]["reason_codes"] == ["not_applicable_no_supported_source"]
    assert state["bootstrap"]["status"] == "not_applicable"
    assert state["bootstrap"]["reason_codes"] == ["not_applicable_no_supported_source"]
    assert state["activation"]["initial_applicability"] == "not_applicable_no_supported_source"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_class_d_reads_as_substrate_failure: a class-D task holds one seed file "
        "that gt_engine/indexer.py:inspect_source_coverage classifies as supported "
        "source, so it is not 'no supported source'.  When the graph substrate then "
        "fails to materialise, classify_repository_applicability "
        "(gt_engine/repository_intelligence.py:146-166) has only SUBSTRATE_FAILURE "
        "left, and persistent_execution_state reports graph_unavailable / "
        "repository_substrate_unavailable.  The 46 class-D tasks - the bulk of TB2 - "
        "therefore read as broken infrastructure rather than as an inapplicable "
        "ability."
    ),
)
@pytest.mark.asyncio
async def test_class_d_persistent_state_is_not_applicable_no_supported_source(tmp_path):
    run = await _run_class("D", tmp_path)
    state = run.persistent_state

    assert state["initialization"]["status"] == "not_applicable"
    assert state["initialization"]["reason_codes"] == ["not_applicable_no_supported_source"]


@pytest.mark.asyncio
async def test_class_d_current_truth_is_substrate_failure(tmp_path):
    """GREEN companion: pin today's verdict so a fix has to move this row."""
    run = await _run_class("D", tmp_path)
    state = run.persistent_state

    assert state["initialization"]["status"] == "graph_unavailable"
    assert state["initialization"]["reason_codes"] == ["repository_substrate_unavailable"]
    assert state["activation"]["initial_applicability"] == "substrate_failure"
    assert run.receipt["repository_intelligence"]["applicability"] == "substrate_failure"


def test_coq_seed_file_counts_as_supported_source():
    """The host-independent half of the mechanism above.

    This holds with or without a gt-index binary: a lone ``.v`` file is
    ``IndexBuildStatus.AVAILABLE`` source coverage, which is what closes the
    NOT_APPLICABLE door for class D.
    """
    import tempfile

    from gt_engine.indexer import IndexBuildStatus, inspect_source_coverage

    root = Path(tempfile.mkdtemp())
    (root / "partial_proof.v").write_text("Theorem t : True.\nProof. Qed.\n", encoding="utf-8")
    coverage = inspect_source_coverage(root)
    assert coverage.status is IndexBuildStatus.AVAILABLE
    assert coverage.source_files == 1

    empty = Path(tempfile.mkdtemp())
    assert inspect_source_coverage(empty).status is IndexBuildStatus.NO_SUPPORTED_SOURCE


# ---------------------------------------------------------------------------
# The deliberate off-switch must not read as infrastructure failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_disabled_reads_as_substrate_failure: with "
        "enable_repository_intelligence=False, eval/gt_central_agent.py:3047-3061 "
        "writes a receipt row with status='disabled' but returns "
        "RepositoryEvidence(status='environment_transfer_unavailable'), which "
        "classify_repository_applicability (gt_engine/repository_intelligence.py:"
        "146-166) can only map to SUBSTRATE_FAILURE.  A deliberate off-switch and a "
        "broken substrate produce the same verdict, and the receipt's own two "
        "halves disagree."
    ),
)
def test_disabled_repository_intelligence_is_not_a_substrate_failure():
    evidence = RepositoryEvidence(status="environment_transfer_unavailable")
    assert (
        classify_repository_applicability(evidence)
        != RepositoryApplicability.SUBSTRATE_FAILURE.value
    )


def test_disabled_repository_intelligence_current_truth_is_substrate_failure():
    """GREEN companion, plus the two verdicts the classifier does get right."""
    disabled = RepositoryEvidence(status="environment_transfer_unavailable")
    assert (
        classify_repository_applicability(disabled)
        == RepositoryApplicability.SUBSTRATE_FAILURE.value
    )

    source_less = RepositoryEvidence(
        status=RepositoryIntelligenceStatus.NO_SUPPORTED_SOURCE.value,
        substrate_status=RepositorySubstrateStatus.NOT_APPLICABLE.value,
    )
    assert (
        classify_repository_applicability(source_less)
        == RepositoryApplicability.NOT_APPLICABLE_NO_SUPPORTED_SOURCE.value
    )

    healthy = RepositoryEvidence(
        status=RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        substrate_ready=True,
        index_current=True,
        intelligence_valid=True,
    )
    assert (
        classify_repository_applicability(healthy) == RepositoryApplicability.SOURCE_BACKED.value
    )


@pytest.mark.asyncio
async def test_disabled_repository_intelligence_receipt_disagrees_with_its_evidence(tmp_path):
    """The two halves of :3047-3061, read straight off one agent."""
    root = tmp_path / "app"
    root.mkdir(parents=True)
    instruction, _script, handlers = _build_class_d(root)
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path / "logs",
        model_name="test",
        integration_mode="active",
        policy_mode="certified_active",
        enable_repository_intelligence=False,
    )
    environment = _WorkspaceEnvironment(root, handlers)
    evidence, session = await agent._start_repository_session(
        environment, instruction, snapshot=None, source_revision="revision-1"
    )

    assert session is None
    assert evidence.status == "environment_transfer_unavailable"
    work = [row for row in agent._repository_work_receipts if row["kind"] == "mirror_transfer"]
    assert work and work[-1]["status"] == "disabled"
    # The receipt says "disabled"; the evidence it returns says the transfer
    # was unavailable, and that is what the classifier sees.
    assert work[-1]["status"] != evidence.status


# ---------------------------------------------------------------------------
# _run_lint's silent `except Exception: continue`.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_lint_silent: eval/gt_central_agent.py:3438 swallows every syntax-probe "
        "exception with `except Exception: continue`.  A linter whose transport dies "
        "leaves no syntax_result row of any kind - no FeatureReceipt, no feature "
        "opportunity, lint_checks stays 0 - so a broken or missing linter is "
        "indistinguishable from a file that had no syntax probe to run.  The "
        "advisory GT_EDIT_CHECK row still fires from the declared-check path, which "
        "makes the receipt read as though edit validation was covered.  The only "
        "surviving trace is the host-exec ledger, which is not a GT evidence surface."
    ),
)
@pytest.mark.asyncio
async def test_failed_lint_probe_leaves_a_receipt_row(tmp_path):
    run = await _run_class("A", tmp_path, lint_fault=RuntimeError("docker compose exec failed"))
    metrics = run.features["action_metrics"]
    syntax_opportunities = [
        row for row in run.features["feature_opportunities"] if row["feature_id"] == "syntax_result"
    ]
    assert (
        metrics["lint_checks"] >= 1 or "syntax_result" in run.fired or syntax_opportunities
    ), "a failed syntax probe produced no syntax_result evidence row"


@pytest.mark.asyncio
async def test_failed_lint_probe_current_truth_is_silence(tmp_path):
    """GREEN companion: pin the silence, and show the only surviving trace."""
    clean = await _run_class("A", tmp_path / "clean")
    faulted = await _run_class(
        "A", tmp_path / "faulted", lint_fault=RuntimeError("docker compose exec failed")
    )

    assert "syntax_result" in clean.fired
    assert clean.features["action_metrics"]["lint_checks"] == 1

    metrics = faulted.features["action_metrics"]
    assert metrics["lint_checks"] == 0
    assert metrics["lint_failures"] == 0
    assert "syntax_result" not in faulted.fired
    assert not [
        row
        for row in faulted.features["feature_opportunities"]
        if row["feature_id"] == "syntax_result"
    ]
    # The advisory row survives, so the receipt still looks as though edit
    # validation happened.
    assert "GT_EDIT_CHECK" in faulted.fired
    assert faulted.fired == clean.fired - {"syntax_result"}

    # The host-exec ledger is the one place the fault survives, and it is not
    # a GroundTruth evidence surface.
    syntax_rows = [
        row
        for row in faulted.receipt["host_execution"]["receipts"]
        if row["category"] == "syntax_probe"
    ]
    assert syntax_rows
    assert syntax_rows[-1]["return_code"] is None
    assert syntax_rows[-1]["exception_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# The declared limitation, asserted rather than assumed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(BUILDERS), ids=sorted(BUILDERS))
async def test_graph_substrate_is_unavailable_offline_on_every_class(tmp_path, name):
    """UNPROVEN, stated as an assertion.

    Without a gt-index binary no class can reach SOURCE_BACKED, so every
    graph-derived row below ``localization``/``def_partition`` is untested
    here.  This test exists so that the limitation is a recorded property of
    this reproduction rather than an unexamined gap: if a CI tier ever
    provides GT_INDEX_BINARY, this row turns RED and the class trigger sets
    above must be re-measured.
    """
    run = await _run_class(name, tmp_path)
    assert run.receipt["repository_evidence"]["substrate_ready"] is False
    assert run.receipt["repository_intelligence"]["applicability"] in {
        "substrate_failure",
        "not_applicable_no_supported_source",
    }
    assert not run.fired & {"caller_contract", "def_partition"}
