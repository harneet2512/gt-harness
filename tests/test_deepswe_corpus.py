"""DeepSWE v1.1 corpus tests - audit-spec layer L1 (T0.1 - T0.5).

Provider-free by construction: no benchmark dispatch, no container, no
network, no GT-off rerun.  Every case is a recorded fact about the 113
DeepSWE tasks (`tests/fixtures/benchmarks/`) replayed through the shipping
producers - `groundtruth.runtime.verification_plan.discover_test_command`,
`gt_engine.runtime_observation`, `groundtruth.runtime.patterns`,
`gt_engine.persistent_plan.baseline` and `gt_engine.persistent_plan.checks`.

Expectations are derived independently of the code under test.  The
discovery triple comes from the CONFIDENCE LAW written in
`discover_test_command`'s own docstring, re-applied to the recorded config
bytes; the verifier-command labels come from the documented
`test_command_shape` contract ("at most one test-runner invocation
surrounded by benign segments").  Where the product disagrees with the
expectation the row is marked `xfail(strict=True)` with a defect id - the
expectation is never weakened to match the product.

Defect ids exercised here:

  deepswe_npm_on_pnpm             17 pnpm-lockfile repos are told to run `npm test`
  deepswe_npm_on_yarn              2 yarn-lockfile repos are told to run `npm test`
  deepswe_subshell_scope_unknown   3 `( cd x && runner )` loses the test boundary
  deepswe_cmdsubst_phantom_path    1 a backtick package set becomes a literal path
  protocol_unknown_js_runners     39 jest/vitest/mocha/deno argvs bind no protocol
  vitest_name_duration_suffix      vitest passing ids keep their bare `3ms` tail
  mocha_failing_name_duplicated    one mocha failure lands under two identities
  checkspec_admits_env_assignment  a bare `VAR=/path/jest-reporter` is admitted
  covering_py_only                 non-Python failing test files are never named
  covering_trailing_colon_frame    `path:LINE:` frames never link to an edited file
"""
from __future__ import annotations

import json
import re
import shlex
import types
from pathlib import Path

import pytest
from groundtruth.runtime.patterns import TEST_RUNNER_RE, classify_test_observation
from groundtruth.runtime.verification_plan import discover_test_command

from gt_engine.miniswe_covering import (
    _TEST_FAILURE_FILE_RE,
    _failing_test_files,
    attribute_test_failure,
)
from gt_engine.persistent_plan.baseline import _parse
from gt_engine.persistent_plan.checks import CheckSpec, decompose_check_command
from gt_engine.runtime_observation import _protocol, wrapper_stripped_command
from gt_engine.runtime_observation import test_command_scope as command_scope
from gt_engine.runtime_observation import test_command_shape as command_shape

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "benchmarks"


def _load(name: str):
    with open(_FIXTURES / name, encoding="utf-8") as handle:
        return json.load(handle)


_COMMAND_CORPUS = (
    Path(__file__).resolve().parent
    / "fixtures" / "command_corpus" / "model_test_commands.json"
)

TASKS = _load("deepswe_tasks.json")
CONFIGS = _load("deepswe_test_configs.json")
LABELS = _load("model_command_labels.json")
MODEL_COMMANDS = json.loads(_COMMAND_CORPUS.read_text(encoding="utf-8"))

# Rows whose discovery outcome is decided by a sub-package manifest the
# recorded root tree does not carry.  Their real-repository answer is
# UNPROVEN under a root-only materialisation and is never guessed.
UNPROVEN_DISCOVERY = {
    "arcane-drift-detection-baselines",
    "claude-code-by-agents-recursive-delegation",
    "optique-conditional-option-dependencies",
    "quill-shared-toolbar-focus",
}

# `( cd pkg && runner )` - one runner, benign prefix, wrapped in a subshell.
SUBSHELL_SCOPE_ROWS = {
    "( cd /app/backend && bunx vitest run --reporter=junit "
    "--outputFile=/logs/verifier/new.xml tests/handlers/recursiveDelegation.test.ts )",
    "(cd test && go test -json -count=1 -skip 'TestContextCancellation' "
    './misc/... 2>>"$RUN_LOG")',
    '(cd test && go test -json -count=1 ./compare/... 2>>"$RUN_LOG")',
}


def _materialise(spec: dict, root: Path) -> None:
    """Write the recorded repository-root discovery surface into `root`.

    Only the root entries the discovery probes read are written: the real
    bytes for every config file whose CONTENT decides a probe, empty files
    for the manifests whose PRESENCE decides one, the root directory names,
    and a marker `.py` source when GitHub's language census says the
    repository contains Python (which is what makes the Python language
    profile match during the extension fallback).
    """
    for name, text in spec["files"].items():
        (root / name).write_text(text, encoding="utf-8")
    for name in spec["present"]:
        (root / name).write_text("", encoding="utf-8")
    if spec.get("package_json") is not None:
        (root / "package.json").write_text(
            json.dumps(spec["package_json"]), encoding="utf-8"
        )
    for name in spec["dirs"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    if spec.get("has_python_sources"):
        (root / "_pkg.py").write_text("x = 1\n", encoding="utf-8")


def _discovery_params():
    for row in TASKS:
        task_id = row["task_id"]
        if task_id in UNPROVEN_DISCOVERY:
            continue
        yield pytest.param(task_id, id=task_id)


# ---------------------------------------------------------------------------
# T0.1 - discover_command corpus
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("task_id", list(_discovery_params()))
def test_t01_discovery_corpus(task_id, tmp_path):
    """Every DeepSWE root yields the exact triple its config files imply."""
    row = next(item for item in TASKS if item["task_id"] == task_id)
    _materialise(CONFIGS[task_id], tmp_path)
    command, basis, confidence = discover_test_command(str(tmp_path))
    expected = row["expected_discovery"]
    assert [list(command) if command else None, basis, confidence] == [
        expected["command"], expected["basis"], expected["confidence"]
    ], "{}: expectation derived from {}".format(task_id, expected["rule"])


def test_t01_unproven_rows_are_named_and_bounded():
    """The rows whose answer needs sub-package data stay explicitly UNPROVEN.

    A silent skip would let the set grow; this pins it to the four rows whose
    repository root declares a JS workspace but no usable root test script,
    so discovery depends on manifests below the recorded root tree.
    """
    unproven = {row["task_id"] for row in TASKS
                if row["expected_discovery"]["basis"] == "UNPROVEN"}
    assert unproven == UNPROVEN_DISCOVERY
    for task_id in sorted(unproven):
        row = next(item for item in TASKS if item["task_id"] == task_id)
        assert "sub-package" in row["expected_discovery"]["rule"]


def test_t01_basis_distribution_is_locked():
    """The corpus keeps its shape: a silent collapse to one basis is a defect."""
    counts: dict[str, int] = {}
    for row in TASKS:
        counts[row["expected_discovery"]["basis"]] = (
            counts.get(row["expected_discovery"]["basis"], 0) + 1
        )
    assert counts == {
        "config:package_json": 36,
        "config:go_mod": 18,
        "config:makefile": 16,
        "config:pyproject.pytest": 15,
        "extension_fallback": 10,
        "config:setup_cfg": 4,
        "config:cargo": 4,
        "UNPROVEN": 4,
        "config:pytest_ini": 3,
        "unknown": 3,
    }


def _lockfile_rows(lockfile: str) -> list[str]:
    out = []
    for row in TASKS:
        spec = CONFIGS[row["task_id"]]
        present = set(spec["present"]) | set(spec["files"])
        command = row["expected_discovery"]["command"]
        if command and command[0] == "npm" and lockfile in present:
            out.append(row["task_id"])
    return out


PNPM_ROWS = _lockfile_rows("pnpm-lock.yaml")
YARN_ROWS = [t for t in _lockfile_rows("yarn.lock") if t not in PNPM_ROWS]


@pytest.mark.parametrize("task_id", [
    pytest.param(task_id, id=task_id, marks=pytest.mark.xfail(
        strict=True,
        reason="deepswe_npm_on_pnpm: _cfg_package_json emits ('npm','test') "
               "before repo_adapters' lockfile-aware manager is consulted",
    ))
    for task_id in PNPM_ROWS
])
def test_t01_pnpm_lockfile_repo_runs_pnpm(task_id, tmp_path):
    """A pnpm-lockfile repository must be told to run `pnpm test`.

    `npm test` in a pnpm workspace does not resolve the workspace protocol
    links, so the discovered baseline command cannot run at all - which is
    what `spawn_failed`/`no_tests_observed` looked like on this cohort.
    """
    _materialise(CONFIGS[task_id], tmp_path)
    command, _basis, _confidence = discover_test_command(str(tmp_path))
    assert list(command or []) == ["pnpm", "test"]


@pytest.mark.parametrize("task_id", [
    pytest.param(task_id, id=task_id, marks=pytest.mark.xfail(
        strict=True,
        reason="deepswe_npm_on_yarn: _cfg_package_json emits ('npm','test') "
               "before repo_adapters' lockfile-aware manager is consulted",
    ))
    for task_id in YARN_ROWS
])
def test_t01_yarn_lockfile_repo_runs_yarn(task_id, tmp_path):
    """A yarn-lockfile repository must be told to run `yarn test`."""
    _materialise(CONFIGS[task_id], tmp_path)
    command, _basis, _confidence = discover_test_command(str(tmp_path))
    assert list(command or []) == ["yarn", "test"]


@pytest.mark.parametrize("marker,expected", [
    pytest.param("Cargo.toml", (["cargo", "test"], "config:cargo", "low"), id="cargo"),
    pytest.param("go.mod", (["go", "test", "./..."], "config:go_mod", "low"), id="go_mod"),
])
def test_t01_manifest_only_roots(marker, expected, tmp_path):
    """Rust and Go roots discover their own runner - neither had any test."""
    (tmp_path / marker).write_text("", encoding="utf-8")
    command, basis, confidence = discover_test_command(str(tmp_path))
    assert [list(command or []), basis, confidence] == [list(expected[0]), expected[1], expected[2]]


def test_t01_makefile_only_root(tmp_path):
    """A column-0 `test:` target is the only Makefile shape that discovers."""
    (tmp_path / "Makefile").write_text("build:\n\tgo build ./...\n\ntest:\n\tgo test ./...\n",
                                       encoding="utf-8")
    assert discover_test_command(str(tmp_path)) == (("make", "test"), "config:makefile", "low")


def test_t01_makefile_variable_assignment_is_not_a_target(tmp_path):
    """`test := ./deploy.sh` is an assignment; discovering it ran a deploy."""
    (tmp_path / "Makefile").write_text("test := ./deploy.sh\n", encoding="utf-8")
    assert discover_test_command(str(tmp_path)) == (None, "unknown", "unknown")


def test_t01_empty_root_discovers_nothing(tmp_path):
    """No config and no profile command is UNKNOWN, never a fabricated command."""
    assert discover_test_command(str(tmp_path)) == (None, "unknown", "unknown")


@pytest.mark.parametrize("task_id", [
    pytest.param(row["task_id"], id=row["task_id"])
    for row in TASKS if row["expected_discovery"]["command"] is None
    and row["expected_discovery"]["basis"] != "UNPROVEN"
])
def test_t01_no_command_rows_stay_none(task_id, tmp_path):
    """The three DeepSWE roots with no discoverable suite must stay None.

    A fabricated command here becomes a baseline that cannot run, and the
    plan gate then reads `baseline_status: unknown` as "nothing blocking".
    """
    _materialise(CONFIGS[task_id], tmp_path)
    command, _basis, _confidence = discover_test_command(str(tmp_path))
    assert command is None


# ---------------------------------------------------------------------------
# T0.2 - verifier-command corpus
# ---------------------------------------------------------------------------
def _verifier_rows(kind: str):
    seen = set()
    for row in TASKS:
        for entry in row["verifier_invocations"]:
            if entry["kind"] != kind or entry["command"] in seen:
                continue
            seen.add(entry["command"])
            marks = []
            if kind == "plain" and entry["command"] in SUBSHELL_SCOPE_ROWS:
                marks.append(pytest.mark.xfail(
                    strict=True,
                    reason="deepswe_subshell_scope_unknown: test_command_shape "
                           "does not unwrap a subshell that encloses the whole "
                           "command, so `( cd pkg && runner )` reads unknown",
                ))
            yield pytest.param(
                row["task_id"], entry["command"], entry["reason"],
                id=f"{row['task_id']}-{len(seen)}", marks=marks,
            )


@pytest.mark.parametrize("task_id,command,reason", list(_verifier_rows("plain")))
def test_t02_plain_verifier_invocation_has_a_scope(task_id, command, reason):
    """One runner surrounded by benign segments must yield a coverage shape.

    An `unknown` here is a silent loss of the whole suite ledger for that
    language: nothing downstream can tell a whole-suite run from a one-file
    run, so no advisory and no regression comparison is possible.
    """
    shape = command_shape(command)
    assert shape.scope in ("suite", "scoped"), f"{task_id} ({reason}): {command!r}"
    assert shape.runner_segment, f"no runner argv recovered from {command!r}"


@pytest.mark.parametrize("task_id,command,reason", list(_verifier_rows("compound")))
def test_t02_compound_verifier_invocation_abstains(task_id, command, reason):
    """Anything that is not one readable runner run must abstain, not guess."""
    assert command_scope(command) == "unknown", (
        f"{task_id} ({reason}): {command!r}")


def _recovered_runner_argvs():
    seen = set()
    for row in TASKS:
        for entry in row["verifier_invocations"]:
            if entry["kind"] != "plain":
                continue
            shape = command_shape(entry["command"])
            if not shape.runner_segment:
                continue  # covered by deepswe_subshell_scope_unknown above
            argv = shlex.join(shape.runner_segment)
            if argv in seen:
                continue
            seen.add(argv)
            yield row["task_id"], argv


RUNNER_ARGVS = list(_recovered_runner_argvs())
# `_protocol`'s vocabulary is build-tool shaped (npm/pnpm/yarn/cargo/go/make).
# A JS-native runner invoked directly is not in it.
JS_NATIVE_HEADS = ("jest", "vitest", "mocha", "deno", "ava", "bun")


@pytest.mark.parametrize("task_id,argv", [
    pytest.param(task_id, argv, id=f"argv-{index}")
    for index, (task_id, argv) in enumerate(RUNNER_ARGVS)
])
def test_t02_recovered_runner_argv_is_canonical(task_id, argv):
    """The certified runner regex sees the same invocation the shape parser did."""
    assert TEST_RUNNER_RE.search(argv), f"{task_id}: {argv!r}"


@pytest.mark.parametrize("task_id,argv", [
    pytest.param(
        task_id, argv, id=f"argv-{index}",
        marks=([pytest.mark.xfail(
            strict=True,
            reason="protocol_unknown_js_runners: _protocol's vocabulary has no "
                   "jest/vitest/mocha/deno entry, so a JS-native runner leaves "
                   "ExecutionEvidence.protocol 'unknown'; miniswe_integration "
                   "then records the check binding as protocol 'unbound' and "
                   "classify_bound_check can never match spec.protocol",
        )] if shlex.split(argv)[0].rsplit("/", 1)[-1].startswith(JS_NATIVE_HEADS)
            else []),
    )
    for index, (task_id, argv) in enumerate(RUNNER_ARGVS)
])
def test_t02_recovered_runner_argv_binds_a_protocol(task_id, argv):
    """Every recovered runner argv must name the protocol it ran under."""
    assert _protocol(argv) != "unknown", f"{task_id}: {argv!r}"


def _plain_commands():
    seen = []
    for row in TASKS:
        for entry in row["verifier_invocations"]:
            if entry["kind"] == "plain" and entry["command"] not in seen:
                seen.append(entry["command"])
    return seen


PLAIN_COMMANDS = _plain_commands()


@pytest.mark.parametrize("command", [
    pytest.param(command, id=f"plain-{index}")
    for index, command in enumerate(PLAIN_COMMANDS)
])
def test_t02_certified_classifier_sees_every_plain_invocation(command):
    """The wheel's classifier must recognise the command production hands it.

    Production never passes the raw surface: `_classify_test_output` peels
    env prefixes and exec wrappers first (the E2 repair, which exists because
    `uvx pytest` produced no `kind == "test"` row on 82 of 89 TB2 tasks).
    This is the positive control for that seam across all four languages.
    """
    stripped = wrapper_stripped_command(command)
    _outcome, protocol = classify_test_observation(stripped, "", None)
    assert protocol == "command", f"{command!r} -> {stripped!r}"


def test_t02_raw_surface_gap_is_bounded_and_covered_by_the_seam():
    """How much of the corpus the pinned wheel cannot read unaided.

    Fourteen recorded verifier invocations - a bare `VAR=value` prefix and
    `pnpm exec <runner>` - are invisible to `TEST_RUNNER_RE` on the raw text.
    They are all recovered by the wrapper-stripping seam above, so this is a
    wheel limitation the engine covers, not a live blind spot; it is pinned
    so a seam regression shows up as a count change rather than as silence.
    """
    unseen = [command for command in PLAIN_COMMANDS
              if classify_test_observation(command, "", None)[1] != "command"]
    assert len(unseen) == 14
    for command in unseen:
        assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", command) or " exec " in command
        assert classify_test_observation(
            wrapper_stripped_command(command), "", None)[1] == "command"


def test_t02_verifier_corpus_covers_every_language():
    """The corpus is not silently a pytest corpus."""
    languages: dict[str, int] = {}
    for row in TASKS:
        plain = [e for e in row["verifier_invocations"] if e["kind"] == "plain"]
        if plain:
            languages[row["language"]] = languages.get(row["language"], 0) + len(plain)
    assert set(languages) >= {"go", "python", "typescript", "rust"}
    assert sum(languages.values()) >= 150


@pytest.mark.parametrize("command,output,returncode", [
    pytest.param(
        "git log --oneline -3",
        "a4431c1a fix: pytest tests/test_a.py now passes\n"
        "b1089ad8 chore: go test ./... in CI\n",
        0,
        id="commit_message_text",
    ),
    pytest.param(
        "echo 'go test ./...'",
        "go test ./...\n",
        0,
        id="echoed_command_text",
    ),
    pytest.param(
        "git commit -m 'run pytest tests/ before merging'",
        "[main 1234567] run pytest tests/ before merging\n 1 file changed\n",
        0,
        id="commit_subject_names_a_runner",
    ),
])
def test_t02_red_commit_text_is_not_a_verification(command, output, returncode):
    """RED: log and quoted command text that resembles verification is not one.

    Neither a coverage shape nor a certified test observation may come out of
    text that merely NAMES a runner; a `fail`/`pass` minted here resets the
    churn governor's streak and can close a plan row on a commit message.
    """
    assert command_scope(command) == "unknown"
    outcome, protocol = classify_test_observation(command, output, returncode)
    assert (outcome, protocol) == ("", ""), f"{command!r} -> {outcome!r}/{protocol!r}"


@pytest.mark.parametrize("command,expected", [
    pytest.param("uv run --project . pytest -q", "suite", id="uv_project_suite"),
    pytest.param("uv run --project packages/core pytest tests/test_a.py", "scoped",
                 id="uv_project_scoped"),
    pytest.param("uv run pytest -q", "suite", id="uv_run_suite"),
    pytest.param("uvx pytest -q", "suite", id="uvx_suite"),
])
def test_t02_uv_wrapper_keeps_the_test_boundary(command, expected):
    """RED case kept as a positive control: `uv --project` must not lose it."""
    assert command_scope(command) == expected


@pytest.mark.parametrize("command,expected_scope", [
    pytest.param("go test ./...", "suite", id="go_suite"),
    pytest.param("cargo test", "suite", id="cargo_suite"),
    pytest.param("cargo nextest run", "suite", id="nextest_suite"),
    pytest.param("npm test", "suite", id="npm_suite"),
    pytest.param("deno test -A", "suite", id="deno_suite"),
    pytest.param("make test", "suite", id="make_suite"),
    pytest.param("go test ./evaluator/...", "scoped", id="go_scoped"),
    pytest.param("cargo test -p pest_meta", "scoped", id="cargo_scoped"),
])
def test_t02_non_pytest_runners_reach_a_suite_verdict(command, expected_scope):
    """Non-pytest scope is NOT blind: the 83/113 claim is stale on this HEAD."""
    assert command_scope(command) == expected_scope


# ---------------------------------------------------------------------------
# T0.3 - improvised (recorded model) command corpus
# ---------------------------------------------------------------------------
# A family's abstention rate over the commands that really do run exactly one
# runner.  Above these floors GT is blind to what the agent actually ran.
UNKNOWN_RATE_FLOOR = {"pytest": 0.15}
DEFAULT_UNKNOWN_RATE_FLOOR = 0.25


def _plain_by_family() -> dict[str, list[str]]:
    families: dict[str, list[str]] = {}
    for label in LABELS["labels"]:
        if label["kind"] != "plain" or not label["family"]:
            continue
        families.setdefault(label["family"], []).append(MODEL_COMMANDS[label["index"]])
    return families


PLAIN_BY_FAMILY = _plain_by_family()


def test_t03_corpus_is_large_and_multi_family():
    assert sum(len(v) for v in PLAIN_BY_FAMILY.values()) >= 300
    assert set(PLAIN_BY_FAMILY) >= {"pytest", "go", "cargo", "jsrunner", "npm"}


@pytest.mark.parametrize("family", sorted(PLAIN_BY_FAMILY))
def test_t03_family_unknown_rate_under_floor(family):
    """What the agent actually typed must be classifiable, per family."""
    commands = PLAIN_BY_FAMILY[family]
    offenders = [c for c in commands if command_scope(c) == "unknown"]
    floor = UNKNOWN_RATE_FLOOR.get(family, DEFAULT_UNKNOWN_RATE_FLOOR)
    rate = len(offenders) / len(commands)
    listing = "\n".join("  " + repr(c[:200]) for c in offenders[:20])
    assert rate <= floor, (
        f"{family}: {len(offenders)}/{len(commands)} = {100 * rate:.1f}% unknown "
        f"(floor {100 * floor:.0f}%)\n{listing}"
    )


def _sample_params():
    for entry in LABELS["sample"]:
        marks = []
        if entry["index"] == 831:
            marks.append(pytest.mark.xfail(
                strict=True,
                reason="deepswe_cmdsubst_phantom_path: a backtick-computed "
                       "package set is read as a literal positional path, so a "
                       "run of unknown extent reads `scoped` with a path that "
                       "does not exist",
            ))
        yield pytest.param(entry["command"], entry["expected_scope"],
                           entry["rationale"], id=f"sample-{entry['index']}",
                           marks=marks)


def test_t03_labelled_sample_is_at_least_thirty():
    assert len(LABELS["sample"]) >= 30


@pytest.mark.parametrize("command,expected,rationale", list(_sample_params()))
def test_t03_labelled_sample_scope(command, expected, rationale):
    """Hand-read extent versus classified extent, on real agent commands."""
    assert command_scope(command) == expected, f"{rationale}\n{command!r}"


# ---------------------------------------------------------------------------
# T0.4 - output-name parsing per language
# ---------------------------------------------------------------------------
PYTEST_VERBOSE = """============================= test session starts ==============================
collected 3 items

tests/test_math.py::test_add PASSED                                      [ 33%]
tests/test_math.py::test_sub FAILED                                      [ 66%]
tests/test_math.py::test_mul PASSED                                      [100%]

=========================== short test summary info ============================
FAILED tests/test_math.py::test_sub - assert 1 == 2
========================= 2 passed, 1 failed in 0.05s ==========================
"""

PYTEST_RA = """============================= test session starts ==============================
collected 3 items

tests/test_math.py ..F                                                   [100%]

=========================== short test summary info ============================
PASSED tests/test_math.py::test_add
PASSED tests/test_math.py::test_mul
FAILED tests/test_math.py::test_sub - assert 1 == 2
========================= 2 passed, 1 failed in 0.05s ==========================
"""

VITEST_VERBOSE = """ RUN  v1.6.0 /app

 ✓ tests/math.test.ts > adds numbers 3ms
 ✗ tests/math.test.ts > subtracts numbers
 ✓ tests/math.test.ts > multiplies numbers 1ms

 Test Files  1 failed (1)
      Tests  1 failed | 2 passed (3)
"""

JEST_VERBOSE = """PASS tests/math.test.js
  math
    ✓ adds numbers (3 ms)
    ✗ subtracts numbers (1 ms)
    ✓ multiplies numbers

Tests:       1 failed, 2 passed, 3 total
"""

CARGO_TEST = """running 3 tests
test math::tests::add_works ... ok
test math::tests::sub_works ... FAILED
test math::tests::mul_works ... ok

failures:
    math::tests::sub_works

test result: FAILED. 2 passed; 1 failed; 0 ignored
"""

GO_TEST_VERBOSE = """=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestSub
--- FAIL: TestSub (0.00s)
    math_test.go:14: want 1 got 2
=== RUN   TestMul
--- PASS: TestMul (0.00s)
FAIL
FAIL\texample.com/math\t0.004s
"""

MOCHA_SPEC = """

  math
    ✓ adds numbers
    1) subtracts numbers
    ✓ multiplies numbers


  2 passing (12ms)
  1 failing

  1) math
       subtracts numbers:
     AssertionError: expected 1 to equal 2
"""


@pytest.mark.parametrize("argv,output,passing,failing", [
    pytest.param(("pytest", "-v"), PYTEST_VERBOSE,
                 ["tests/test_math.py::test_add", "tests/test_math.py::test_mul"],
                 ["tests/test_math.py::test_sub"], id="pytest-v"),
    pytest.param(("pytest", "-rA"), PYTEST_RA,
                 ["tests/test_math.py::test_add", "tests/test_math.py::test_mul"],
                 ["tests/test_math.py::test_sub"], id="pytest-rA"),
    pytest.param(("cargo", "test"), CARGO_TEST,
                 ["math::tests::add_works", "math::tests::mul_works"],
                 ["math::tests::sub_works"], id="cargo"),
    pytest.param(("go", "test", "-v", "./..."), GO_TEST_VERBOSE,
                 ["TestAdd", "TestMul"], ["TestSub"], id="go-v"),
    pytest.param(("npx", "vitest", "run", "--reporter=verbose"), VITEST_VERBOSE,
                 ["tests/math.test.ts > adds numbers",
                  "tests/math.test.ts > multiplies numbers"],
                 ["tests/math.test.ts > subtracts numbers"],
                 marks=pytest.mark.xfail(
                     strict=True,
                     reason="vitest_name_duration_suffix: the check-mark pattern "
                            "only strips a PARENTHESISED duration, so vitest's "
                            "bare `3ms` tail stays in the identity and the same "
                            "test gets a new name on every run",
                 ), id="vitest"),
    pytest.param(("npx", "jest", "--verbose"), JEST_VERBOSE,
                 ["adds numbers", "multiplies numbers"],
                 ["subtracts numbers"], id="jest"),
    pytest.param(("npx", "mocha", "--reporter", "spec"), MOCHA_SPEC,
                 ["adds numbers", "multiplies numbers"],
                 ["subtracts numbers"],
                 marks=pytest.mark.xfail(
                     strict=True,
                     reason="mocha_failing_name_duplicated: the spec reporter "
                            "prints one failure twice - inline as `1) name` and "
                            "again as the numbered `1) suite` / `name:` block - "
                            "and test_names._extract_mocha appends both, so one "
                            "failing test lands under two identities "
                            "(`subtracts numbers` and `math subtracts numbers`)",
                 ), id="mocha"),
])
def test_t04_output_name_parsing(argv, output, passing, failing):
    counts, observed_pass, observed_fail = _parse(output, argv)
    assert counts["passed"] == len(passing)
    assert counts["failed"] == len(failing)
    assert observed_pass == passing
    assert observed_fail == failing


@pytest.mark.parametrize("argv,output", [
    pytest.param(("pytest", "-v"), PYTEST_VERBOSE, id="pytest"),
    pytest.param(("cargo", "test"), CARGO_TEST, id="cargo"),
    pytest.param(("go", "test", "-v", "./..."), GO_TEST_VERBOSE, id="go"),
    pytest.param(("npx", "vitest", "run"), VITEST_VERBOSE, id="vitest"),
    pytest.param(("npx", "jest"), JEST_VERBOSE, id="jest"),
    pytest.param(("npx", "mocha"), MOCHA_SPEC, id="mocha"),
])
def test_t04_counts_are_recovered_for_every_runner(argv, output):
    """Counts alone must never be zero for a run that printed results."""
    counts, _passing, _failing = _parse(output, argv)
    assert counts["passed"] == 2 and counts["failed"] == 1, counts


# ---------------------------------------------------------------------------
# T0.5 - CheckSpec admission corpus + covering attribution
# ---------------------------------------------------------------------------
def _admission_params():
    seen = set()
    for row in TASKS:
        for entry in row["verifier_invocations"]:
            command = entry["command"]
            if command in seen:
                continue
            seen.add(command)
            yield pytest.param(command, entry["kind"], id=f"cmd-{len(seen)}")


@pytest.mark.parametrize("command,kind", list(_admission_params()))
def test_t05_decompose_never_invents_a_check(command, kind, tmp_path):
    """Decomposition either yields argv segments or names why it refused."""
    segments, _separators, _last_is_check, reason = decompose_check_command(command)
    assert (segments is None) == (reason is not None), command
    if segments is None:
        assert reason, command


_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _env_assignment_rows():
    """Recorded verifier lines that are a bare `NAME=value` and nothing else."""
    rows = []
    for row in TASKS:
        for entry in row["verifier_invocations"]:
            command = entry["command"].strip()
            if " " in command or not _ASSIGNMENT_RE.match(command):
                continue
            rows.append(command)
    return sorted(set(rows))


ENV_ASSIGNMENT_ROWS = _env_assignment_rows()


@pytest.mark.parametrize("command", [
    pytest.param(command, id=f"env-{index}", marks=pytest.mark.xfail(
        strict=True,
        reason="checkspec_admits_env_assignment: a bare `VAR=/path/<runner>` "
               "word is decomposed into a one-word argv whose BASENAME matches "
               "TEST_RUNNER_RE, so an assignment that runs no test at all is "
               "admitted as a canonical test-runner check and always exits 0",
    ))
    for index, command in enumerate(ENV_ASSIGNMENT_ROWS)
])
def test_t05_environment_assignment_is_not_an_admissible_check(command, tmp_path):
    """An assignment executes nothing; admitting it mints a free CHECK_PASSED."""
    segments, _separators, _last, _reason = decompose_check_command(command)
    assert segments, command
    with pytest.raises(ValueError):
        CheckSpec.from_dict(
            {"argv": segments[-1][0], "requirement_ids": ["R1"]}, str(tmp_path)
        )


@pytest.mark.parametrize("argv,admitted", [
    pytest.param(["pytest", "-q"], True, id="pytest"),
    pytest.param(["go", "test", "./..."], True, id="go"),
    pytest.param(["cargo", "nextest", "run"], True, id="nextest"),
    pytest.param(["npm", "test"], True, id="npm"),
    pytest.param(["make", "test"], True, id="make"),
    pytest.param(["bash", "-c", "pytest -q"], False, id="shell_wrapper"),
    pytest.param(["python", "-c", "import pytest; pytest.main()"], False, id="inline_program"),
    pytest.param(["git", "log", "--oneline"], False, id="git_log"),
    pytest.param(["echo", "go test ./..."], False, id="echo"),
    pytest.param(["ruff", "check", "src"], False, id="static_check"),
])
def test_t05_checkspec_admission(argv, admitted, tmp_path):
    """Only a canonical test-runner invocation becomes an automatic check."""
    payload = {"argv": argv, "requirement_ids": ["R1"]}
    if admitted:
        spec = CheckSpec.from_dict(payload, str(tmp_path))
        assert spec.argv == tuple(argv)
    else:
        with pytest.raises(ValueError):
            CheckSpec.from_dict(payload, str(tmp_path))


def test_t05_checkspec_requires_a_requirement_binding(tmp_path):
    with pytest.raises(ValueError, match="requirement binding"):
        CheckSpec.from_dict({"argv": ["pytest", "-q"], "requirement_ids": []}, str(tmp_path))


@pytest.mark.parametrize("command,reason", [
    pytest.param("pytest -q | tail -5", "unsupported_shell_operator:|", id="pipe"),
    pytest.param("pytest -q || true", "unsupported_shell_operator:||", id="or"),
    pytest.param("pytest -q > out.log", "unsupported_shell_operator:>", id="redirect"),
    pytest.param("pytest -q $(cat args)", "shell_expansion", id="substitution"),
    pytest.param("( cd pkg && pytest -q ) && ruff check .", "unsupported_shell_operator:(",
                 id="mid_command_subshell"),
])
def test_t05_decompose_refuses_unpreservable_operators(command, reason):
    segments, _separators, _last, why = decompose_check_command(command)
    assert segments is None
    assert why == reason


def test_t05_decompose_folds_cd_and_keeps_the_chain():
    segments, separators, last_is_check, reason = decompose_check_command(
        "cd packages/core && pytest -q && ruff check src"
    )
    assert reason is None
    assert separators == ("&&", "&&")
    assert last_is_check is True
    assert [argv for argv, _cwd in segments] == [["pytest", "-q"], ["ruff", "check", "src"]]
    assert {cwd for _argv, cwd in segments} == {"packages/core"}


def _covering_adapter(root, edited):
    return types.SimpleNamespace(repo_root=str(root), _edited_files=set(edited))


def test_t05_red_vendor_path_is_not_attributed_to_the_edited_file(tmp_path):
    """RED (repaired, kept as a positive control): `vendor/src/a.py` != `src/a.py`."""
    adapter = _covering_adapter(tmp_path, {"src/a.py"})
    output = 'E   File "vendor/src/a.py", line 12, in helper\nE   AssertionError\n'
    assert attribute_test_failure(adapter, "pytest -q", output, returncode=1) is None


def test_t05_covering_links_a_real_edited_path(tmp_path):
    adapter = _covering_adapter(tmp_path, {"src/a.py"})
    output = 'E   File "src/a.py", line 12, in helper\nE   AssertionError\n'
    result = attribute_test_failure(adapter, "pytest -q", output, returncode=1)
    assert result is not None and result.target == "src/a.py"


def test_t05_duplicate_basename_does_not_attribute(tmp_path):
    """`pkg/b/a.py` must not satisfy an edit to `pkg/a/a.py`."""
    adapter = _covering_adapter(tmp_path, {"pkg/a/a.py"})
    output = 'E   File "pkg/b/a.py", line 3, in helper\nE   AssertionError\n'
    assert attribute_test_failure(adapter, "pytest -q", output, returncode=1) is None


@pytest.mark.xfail(
    strict=True,
    reason="covering_trailing_colon_frame: _output_repo_paths strips `:12` and "
           "`:12:34` but not pytest's and go's own `path:12:` frame form, so "
           "the traceback that names the edited file never links to it",
)
@pytest.mark.parametrize("output", [
    "src/a.py:12: AssertionError\n",
    "src/a.py:12: in helper\n    assert 1 == 2\n",
])
def test_t05_covering_links_a_trailing_colon_frame(tmp_path, output):
    adapter = _covering_adapter(tmp_path, {"src/a.py"})
    result = attribute_test_failure(adapter, "pytest -q", output, returncode=1)
    assert result is not None and result.target == "src/a.py"


def test_t05_failing_test_file_regex_is_python_only():
    """The regex itself, stated as a fact so the fix has a target."""
    assert _TEST_FAILURE_FILE_RE.pattern == r"(?m)([A-Za-z0-9_./-]+\.py)(?::\d+|::)"


@pytest.mark.parametrize("output,expected", [
    pytest.param("FAILED tests/test_a.py::test_x - boom\n", ("tests/test_a.py",),
                 id="python"),
    pytest.param("    evaluator/module_test.go:14: want 1 got 2\n",
                 ("evaluator/module_test.go",),
                 marks=pytest.mark.xfail(
                     strict=True,
                     reason="covering_py_only: _TEST_FAILURE_FILE_RE matches "
                            "`.py` only, so every Go, Rust and TypeScript "
                            "covering result carries an empty test_files list "
                            "- 79 of the 113 DeepSWE tasks",
                 ), id="go"),
    pytest.param("thread 'x' panicked at tests/integration.rs:12:5\n",
                 ("tests/integration.rs",),
                 marks=pytest.mark.xfail(
                     strict=True, reason="covering_py_only: `.py`-only regex",
                 ), id="rust"),
    pytest.param("FAIL  tests/math.test.ts:12:3\n", ("tests/math.test.ts",),
                 marks=pytest.mark.xfail(
                     strict=True, reason="covering_py_only: `.py`-only regex",
                 ), id="typescript"),
])
def test_t05_failing_test_files_named_per_language(output, expected):
    assert _failing_test_files(output) == expected
