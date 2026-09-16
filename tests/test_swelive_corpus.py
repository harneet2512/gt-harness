"""SWE-bench-Live Lite corpus tests - audit-spec layer L1, items 1-3.

Provider-free: no image pull, no container, no network, no benchmark
dispatch.  Every case replays a recorded fact about the 300 SWE-bench-Live
Lite instances (`tests/fixtures/benchmarks/swelive_tasks.json`) through the
shipping producers.

L1.1 `test_swelive_discovery_*`  - `discover_test_command` over all 300
     rebuilt repository roots plus `name_emitting_argv`, covering S1-S7.
L1.2 `test_swelive_scope_*`      - `test_command_coverage` over the 90
     distinct dataset commands AND the 10 commands recovered from recorded
     run 34996816912.
L1.3 `test_swelive_grade_*`      - the benchmark grader's own
     `parse_log_pytest` against the GT name extractor on whitespace-bearing
     parametrized ids, including the adversarial same-prefix case.

Expectations are independent of the code under test: the discovery triple
is derived from the CONFIDENCE LAW in `discover_test_command`'s docstring
applied to the recorded config facts, and each command's extent is read off
the command text with a pytest option table taken from `pytest --help`.

Defect ids exercised here:

  swelive_cov_value_options     6 distinct verifier commands (13 of the 300
                                rows) read a `--cov*` option VALUE as a
                                positional test path
  swelive_failed_name_space_id  a FAILED parametrized id containing a space
                                yields no GT identity while the grader keys
                                on its truncation
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from groundtruth.runtime.verification_plan import discover_test_command

from gt_engine.persistent_plan.baseline import _parse, name_emitting_argv
from gt_engine.runtime_observation import _VALUE_OPTIONS
from gt_engine.runtime_observation import test_command_coverage as command_coverage

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "benchmarks"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_GRADE_PY = (_REPO_ROOT / "swelive-bench" / "tasks" / "cyclotruc__gitingest-94"
             / "tests" / "grade.py")

with open(_FIXTURES / "swelive_tasks.json", encoding="utf-8") as _handle:
    _FIXTURE = json.load(_handle)
TASKS = _FIXTURE["tasks"]
COMMAND_CORPUS = _FIXTURE["command_corpus"]


def _load_grader():
    spec = importlib.util.spec_from_file_location("swelive_grade", _GRADE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GRADER = _load_grader()

# The `--cov*` family: pytest-cov options whose value is a report spec, a
# package name or a config path - never a test path.  Only the SPACE-separated
# form can be mistaken for a positional: `--cov=pkg` is one word.
COV_VALUE_OPTIONS = ("--cov", "--cov-report", "--cov-config", "--cov-fail-under")
_COV_SPACE_FORM_RE = re.compile(
    r"(?:{})\s+(?!-)".format("|".join(COV_VALUE_OPTIONS))
)
COV_DEFECT_COMMANDS = {
    entry["command"] for entry in COMMAND_CORPUS
    if entry["origin"] == "dataset_test_cmds"
    and _COV_SPACE_FORM_RE.search(entry["command"])
}

_MANIFEST_BODIES = {
    "pytest.ini": "[pytest]\n",
    "tox.ini": "[tox]\nenvlist = py311\n",
    "setup.py": "from setuptools import setup\n\nsetup(name='pkg')\n",
    "package.json": '{"name": "pkg"}\n',
    "go.mod": "module example.com/pkg\n\ngo 1.21\n",
    "Cargo.toml": '[package]\nname = "pkg"\nversion = "0.1.0"\n',
}


def _materialise(spec: dict, root: Path) -> None:
    """Rebuild the recorded repository root's discovery surface.

    Each row records WHICH root manifests exist and which pytest config
    section the repository actually carries; the body written here is the
    minimum that carries that recorded fact.  Every instance is a Python
    repository, so a marker `.py` source is written - that is what makes the
    Python language profile match during the extension fallback.
    """
    for name in spec["root_manifests"]:
        if name == "pyproject.toml":
            body = ('[tool.pytest.ini_options]\naddopts = ""\n'
                    if spec["pyproject_has_ini_options"]
                    else '[project]\nname = "pkg"\n')
        elif name == "setup.cfg":
            body = ("[tool:pytest]\n" if spec["setup_cfg_has_tool_pytest"]
                    else "[metadata]\nname = pkg\n")
        else:
            body = _MANIFEST_BODIES.get(name, "")
        (root / name).write_text(body, encoding="utf-8")
    if spec["makefile_test_target"]:
        (root / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    for name in spec["test_roots"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "_pkg.py").write_text("x = 1\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# L1.1 - discovery corpus over the 300 rebuilt roots
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("instance_id", [
    pytest.param(row["instance_id"], id=row["instance_id"]) for row in TASKS
])
def test_swelive_discovery_corpus(instance_id, tmp_path):
    """Each root yields exactly the triple its own config section implies."""
    row = next(item for item in TASKS if item["instance_id"] == instance_id)
    _materialise(row["materialisation"], tmp_path)
    command, basis, confidence = discover_test_command(str(tmp_path))
    expected = row["expected_discovery"]
    assert [list(command) if command else None, basis, confidence] == [
        expected["command"], expected["basis"], expected["confidence"]
    ], "{}: expectation derived from {}".format(instance_id, expected["rule"])


@pytest.mark.parametrize("instance_id", [
    pytest.param(row["instance_id"], id=row["instance_id"]) for row in TASKS
])
def test_swelive_discovery_matches_the_recorded_call(instance_id, tmp_path):
    """The rebuilt root reproduces the triple recorded against real blobs.

    The recorded value came from a root rebuilt out of the repository's real
    config bytes at `base_commit`; this asserts the compact fixture did not
    lose the fact that decided it.
    """
    row = next(item for item in TASKS if item["instance_id"] == instance_id)
    _materialise(row["materialisation"], tmp_path)
    command, basis, confidence = discover_test_command(str(tmp_path))
    recorded = row["recorded_discovery"]
    assert [list(command) if command else None, basis, confidence] == [
        recorded["command"], recorded["basis"], recorded["confidence"]
    ]


@pytest.mark.parametrize("instance_id", [
    pytest.param(row["instance_id"], id=row["instance_id"]) for row in TASKS
])
def test_swelive_baseline_argv_emits_names(instance_id):
    """The baseline argv must be able to print per-test identities.

    A bare `pytest` prints progress dots: the capture records counts while
    `passing_names` stays empty, which is the recorded gitingest blind
    baseline (`passed=30`, `passing_names: []`).
    """
    row = next(item for item in TASKS if item["instance_id"] == instance_id)
    command = row["recorded_discovery"]["command"]
    argv = list(name_emitting_argv(tuple(command or ())))
    assert argv == row["expected_baseline_argv"]
    if command and Path(command[0]).name == "pytest":
        assert any(token.startswith("-v") or token == "--verbose" for token in argv), argv


def test_swelive_discovery_covers_every_equivalence_class():
    classes = {row["equivalence_class"] for row in TASKS}
    assert len(classes) == 7, sorted(classes)
    assert len(TASKS) == 300


def test_swelive_non_pytest_rows_discover_their_own_runner():
    """S7: the 8 make/tox rows must not be reported as `pytest`."""
    rows = [row for row in TASKS
            if row["expected_discovery"]["command"] not in (["pytest"], None)]
    assert {tuple(row["expected_discovery"]["command"]) for row in rows} == {
        ("make", "test"), ("tox",)
    }
    assert len(rows) == 8


# ---------------------------------------------------------------------------
# L1.2 - scope corpus over dataset + recovered agent commands
# ---------------------------------------------------------------------------
def _scope_params():
    for index, entry in enumerate(COMMAND_CORPUS):
        marks = []
        if entry["command"] in COV_DEFECT_COMMANDS:
            marks.append(pytest.mark.xfail(
                strict=True,
                reason="swelive_cov_value_options: _VALUE_OPTIONS omits "
                       "--cov/--cov-report/--cov-config, so pytest-cov option "
                       "VALUES (`xml:coverage.xml`, `jupyter_ai`, "
                       "`.coveragerc`) are read as positional test paths - a "
                       "whole-suite run reads `scoped` over a path that does "
                       "not exist, and the suite ledger loses the promotion",
            ))
        yield pytest.param(
            entry["command"], entry["expected_scope"], entry["expected_paths"],
            entry["rationale"],
            id=f"{entry['origin']}-{index}", marks=marks,
        )


@pytest.mark.parametrize("command,expected_scope,expected_paths,rationale",
                         list(_scope_params()))
def test_swelive_scope_corpus(command, expected_scope, expected_paths, rationale):
    """Read extent versus classified extent, over every recorded command."""
    shape = command_coverage(command)
    assert shape.scope == expected_scope, f"{rationale}\n{command!r}"
    assert list(shape.paths) == expected_paths, f"{rationale}\n{command!r}"


def test_swelive_scope_corpus_shape():
    dataset = [e for e in COMMAND_CORPUS if e["origin"] == "dataset_test_cmds"]
    recovered = [e for e in COMMAND_CORPUS if e["origin"] == "run_34996816912"]
    assert len(dataset) == 90
    assert len(recovered) == 10
    assert sum(e["rows"] for e in dataset) == sum(len(r["test_cmds"]) for r in TASKS)


@pytest.mark.parametrize("entry", [
    pytest.param(e, id=f"action-{e['action_id']}")
    for e in COMMAND_CORPUS if e["origin"] == "run_34996816912"
])
def test_swelive_recovered_agent_command_keeps_its_boundary(entry):
    """Positive control: `cd X && python -m pytest ... | tail` is readable.

    The pre-`7f62b891` reading of this run was that all ten read `unknown`
    because the leading `cd ... &&` ended the scan.  On this HEAD all ten are
    scoped, and this pins that so the regression cannot come back silently.
    """
    shape = command_coverage(entry["command"])
    assert shape.scope == "scoped"
    assert list(shape.paths) == entry["expected_paths"]


def test_swelive_ignore_flags_are_recorded_as_exclusions():
    """Action 45 `--ignore`d four files; those names must never be promoted."""
    entry = next(e for e in COMMAND_CORPUS
                 if e.get("action_id") == 45)
    shape = command_coverage(entry["command"])
    assert shape.scope == "scoped"
    assert list(shape.excluded) == [
        "tests/test_base.py", "tests/test_cli.py",
        "tests/test_vault.py", "tests/test_redis.py",
    ]


def test_swelive_unparseable_shapes_abstain():
    """S4: a task-runner recipe is an abstention, never a guessed extent."""
    for command in ("hatch run test:unit -rA", "poe test -rA", "pdm run test -rA",
                    "python3 devscripts/run_tests.py --pytest-args -rA",
                    "cat ./scripts/test.sh"):
        assert command_coverage(command).scope == "unknown", command


def test_swelive_cov_options_are_absent_from_value_options():
    """The fix target, stated as a fact so the corpus test notices the fix."""
    assert not (set(COV_VALUE_OPTIONS) & set(_VALUE_OPTIONS))
    assert len(COV_DEFECT_COMMANDS) == 6
    affected = sum(entry["rows"] for entry in COMMAND_CORPUS
                   if entry["command"] in COV_DEFECT_COMMANDS)
    assert affected == 13


# ---------------------------------------------------------------------------
# L1.3 - grade parity on truncated parametrized ids
# ---------------------------------------------------------------------------
PLAIN_LOG = """============================= test session starts ==============================
collected 3 items

tests/test_p.py .F.                                                      [100%]

=========================== short test summary info ============================
PASSED tests/test_p.py::test_alpha
PASSED tests/test_p.py::test_gamma
FAILED tests/test_p.py::test_beta - assert 1 == 2
========================= 2 passed, 1 failed in 0.05s ==========================
"""

SPACE_ID_LOG = """=========================== short test summary info ============================
PASSED tests/test_p.py::test_param[plain]
PASSED tests/test_p.py::test_param[with space]
FAILED tests/test_p.py::test_other[a b] - assert 1 == 2
FAILED tests/test_p.py::test_simple - assert 0
"""

ADVERSARIAL_LOG = """=========================== short test summary info ============================
PASSED tests/test_p.py::test_param[alpha]
FAILED tests/test_p.py::test_param[alpha beta] - assert 0
"""


def _grader_sets(log: str):
    mapping = GRADER.default_pytest_parser(log)
    passing = {name for name, state in mapping.items() if state == "pass"}
    failing = {name for name, state in mapping.items() if state == "fail"}
    return passing, failing


def test_swelive_grade_parity_plain_ids():
    """Positive control: identities with no whitespace agree exactly."""
    grade_pass, grade_fail = _grader_sets(PLAIN_LOG)
    _counts, gt_pass, gt_fail = _parse(PLAIN_LOG, ("pytest", "-rA"))
    assert set(gt_pass) == grade_pass
    assert set(gt_fail) == grade_fail


def test_swelive_grade_parity_passing_space_ids():
    """PASSED rows truncate identically on both sides - the safe half."""
    grade_pass, _grade_fail = _grader_sets(SPACE_ID_LOG)
    _counts, gt_pass, _gt_fail = _parse(SPACE_ID_LOG, ("pytest", "-rA"))
    assert set(gt_pass) == grade_pass
    assert "tests/test_p.py::test_param[with" in grade_pass


@pytest.mark.xfail(
    strict=True,
    reason="swelive_failed_name_space_id: the grader truncates a FAILED row at "
           "the first space and keys on it, while the GT failing-name pattern "
           "requires the id to be followed by ` -` or end-of-line, so a "
           "space-bearing parametrized failure yields NO GT identity at all. "
           "254 of the 300 instances carry whitespace-truncated ids, so a "
           "regression the grader counts is invisible to covering_red, "
           "recovery and the baseline comparison.",
)
def test_swelive_grade_parity_failing_space_ids():
    _grade_pass, grade_fail = _grader_sets(SPACE_ID_LOG)
    _counts, _gt_pass, gt_fail = _parse(SPACE_ID_LOG, ("pytest", "-rA"))
    assert set(gt_fail) == grade_fail


def test_swelive_grade_failing_space_id_is_lost_today():
    """The same fact, asserted positively, so the mechanism is reproducible."""
    _grade_pass, grade_fail = _grader_sets(SPACE_ID_LOG)
    _counts, _gt_pass, gt_fail = _parse(SPACE_ID_LOG, ("pytest", "-rA"))
    assert "tests/test_p.py::test_other[a" in grade_fail
    assert set(gt_fail) == {"tests/test_p.py::test_simple"}
    assert not [name for name in gt_fail if "test_other" in name]


def test_swelive_grade_adversarial_same_prefix():
    """A failing id whose truncation collides with a passing id's full name.

    `test_param[alpha beta]` truncates to `tests/test_p.py::test_param[alpha`.
    If an F2P entry were recorded under that key, the grader would mark the
    task unresolved on a test that is in neither list - and GT would never
    see the failure at all, so nothing offline could explain the loss.
    """
    grade_pass, grade_fail = _grader_sets(ADVERSARIAL_LOG)
    assert grade_pass == {"tests/test_p.py::test_param[alpha]"}
    assert grade_fail == {"tests/test_p.py::test_param[alpha"}
    _counts, gt_pass, gt_fail = _parse(ADVERSARIAL_LOG, ("pytest", "-rA"))
    assert set(gt_pass) == {"tests/test_p.py::test_param[alpha]"}
    assert gt_fail == []


def test_swelive_truncated_id_population_is_recorded():
    """The blast radius of the parser-parity invariant, from the corpus."""
    truncated = [row for row in TASKS
                 if row["f2p_truncated_ids"] or row["p2p_truncated_ids"]]
    assert len(truncated) == 254
    non_python = [row for row in TASKS if row["p2p_non_python_items"]]
    assert len(non_python) == 8


def test_swelive_f2p_new_file_rows_are_named():
    """S6: the covering mapping is structurally unreachable on these rows."""
    rows = [row for row in TASKS if row["f2p_files_new_in_test_patch"]]
    assert len(rows) == 16
    classes = {row["equivalence_class"] for row in rows}
    # 15 land in the dedicated class; `run-llama__llama_deploy-438` is claimed
    # first by S7 because its own discovery is non-pytest.
    assert classes == {"S6_f2p_new_file", "S7_discover_non_pytest"}
    assert sum(1 for row in rows
               if row["equivalence_class"] == "S6_f2p_new_file") == 15


def test_swelive_basename_collision_rows_are_named():
    """The four confirmed import-file-mismatch rows, all dynaconf."""
    rows = [row for row in TASKS if row["import_basename_collisions"]]
    assert len(rows) == 4
    assert {row["repo"] for row in rows} == {"dynaconf/dynaconf"}
