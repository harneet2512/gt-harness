"""Per-family name extraction, on both sides of the comparison.

Every sample below is the shape the runner actually prints, including the
detail that defeats the certified wheel: cargo's summary-only tail, go's
INDENTED subtest rows, jest's ``●`` failure header, vitest's ``FAIL file >
suite > name`` line, mocha's numbered failure block.

The contract under test is narrow on purpose - ``parse_test_names`` delegates
to the pinned wheel and, for a non-pytest family, either fills a side the
wheel left empty or appends what its column-0 anchors missed (go and cargo
only, where both sources spell an identity the same way). So the tests check
both halves: that the fallback fires where the wheel is blind, and that it
never displaces or re-spells a wheel answer.
"""
from __future__ import annotations

import pytest

from gt_engine.test_names import family_for_command, parse_test_names

# --- realistic runner output --------------------------------------------

PYTEST_VERBOSE = """\
============================= test session starts ==============================
tests/test_base.py::test_get_item PASSED                                 [ 33%]
tests/test_base.py::test_set_item FAILED                                 [ 66%]
tests/test_utils.py::test_merge PASSED                                   [100%]

=========================== short test summary info ============================
FAILED tests/test_base.py::test_set_item - AssertionError: assert 1 == 2
========================= 2 passed, 1 failed in 0.42s ==========================
"""

# `cargo test 2>&1 | tail -40` - the per-test rows scrolled off, the roll-call
# survived. This is the single most common Rust capture in the corpus.
CARGO_SUMMARY_TAIL = """\
running 4 tests

failures:

---- parser::tests::parses_nested stdout ----
thread 'parser::tests::parses_nested' panicked at src/parser.rs:88:9:
assertion `left == right` failed

failures:
    lexer::tests::handles_escape
    parser::tests::parses_nested

test result: FAILED. 2 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out

error: test failed, to rerun pass `-p pest_meta --lib`
"""

CARGO_VERBOSE = """\
running 3 tests
test sorting_test ... ok
test merge_test ... ok
test dedup_test ... FAILED

test result: FAILED. 2 passed; 1 failed; 0 ignored
"""

# `go test -v ./evaluator`: subtests are indented under their parent, which is
# where the wheel's column-0 anchor loses them.
GO_VERBOSE_SUBTESTS = """\
=== RUN   TestEnv
=== RUN   TestEnv/reads_var
=== RUN   TestEnv/defaults
    --- FAIL: TestEnv/reads_var (0.00s)
        env_test.go:31: want "abs", got ""
    --- PASS: TestEnv/defaults (0.00s)
--- FAIL: TestEnv (0.01s)
--- PASS: TestCommand (0.00s)
FAIL
FAIL\tgithub.com/abs-lang/abs/evaluator\t0.204s
ok  \tgithub.com/abs-lang/abs/util\t0.011s
"""

# A go package that does not COMPILE: a FAIL line, and no test anywhere.
GO_BUILD_FAILURE = """\
# github.com/abs-lang/abs/evaluator [github.com/abs-lang/abs/evaluator.test]
./module.go:12:2: undefined: fmt
FAIL\tgithub.com/abs-lang/abs/evaluator [build failed]
"""

JEST_DEFAULT_REPORTER = """\
FAIL tests/initialize.test.ts
  Awilix initialize
    ✓ initializes by dependency levels (4 ms)
    ✕ registers initializer via register map (7 ms)

  ● Awilix initialize › registers initializer via register map

    expect(received).toBe(expected)

  ● Console

    console.log
      wiring up

Tests:       1 failed, 1 passed, 2 total
"""

VITEST_DEFAULT_REPORTER = """\
 RUN  v1.6.0 /app/backend

 ✓ tests/handlers/chat.test.ts > chat > streams tokens
 FAIL  tests/handlers/delegateTask.test.ts > delegateTask > aborts on signal
AssertionError: expected 1 to be 2

 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 1 passed (2)
"""

MOCHA_SPEC_REPORTER = """\

  lexer
    ✓ handles escapes
    ✓ parses shorthand

  2 passing (11ms)
  1 failing

  1) lexer
       parses nested:
     AssertionError: expected 'a' to equal 'b'
      at Context.<anonymous> (test/lexer.js:42:20)
"""

MOCHA_SINGLE_LINE_FAILURE = """\
  3 passing (8ms)
  1 failing

  1) reporter writes a report file:
     Error: ENOENT
"""


# --- the wheel keeps pytest ---------------------------------------------

def test_pytest_names_come_from_the_wheel_untouched():
    passing, failing, counts = parse_test_names(
        PYTEST_VERBOSE, family="pytest", command=("pytest", "-v")
    )
    assert passing == ["tests/test_base.py::test_get_item",
                       "tests/test_utils.py::test_merge"]
    assert failing == ["tests/test_base.py::test_set_item"]
    assert (counts["passed"], counts["failed"]) == (2, 1)


def test_pytest_family_never_reaches_a_fallback():
    """mocha's numbered failure block is read by the fallback and nothing else.

    Fed through the pytest family it must yield nothing, which is how we know
    no extractor ran. (The wheel's own check-mark patterns are family-blind
    and DO fire on any output; that is the wheel's behaviour, unchanged here.)
    """
    passing, failing, _counts = parse_test_names(
        MOCHA_SINGLE_LINE_FAILURE, family="pytest", command=("pytest",)
    )
    assert (passing, failing) == ([], [])


# --- cargo ----------------------------------------------------------------

def test_cargo_summary_tail_yields_the_failures_roll_call():
    passing, failing, counts = parse_test_names(
        CARGO_SUMMARY_TAIL, family="cargo", command=("cargo", "test")
    )
    assert sorted(failing) == ["lexer::tests::handles_escape",
                               "parser::tests::parses_nested"]
    assert passing == []          # the `... ok` rows scrolled off; abstain
    assert (counts["passed"], counts["failed"]) == (2, 2)


def test_cargo_per_test_rows_still_come_from_the_wheel():
    passing, failing, _counts = parse_test_names(
        CARGO_VERBOSE, family="cargo", command=("cargo", "test")
    )
    assert passing == ["sorting_test", "merge_test"]
    assert failing == ["dedup_test"]


# --- go -------------------------------------------------------------------

def test_go_indented_subtests_are_recovered():
    passing, failing, _counts = parse_test_names(
        GO_VERBOSE_SUBTESTS, family="go", command=("go", "test", "-v", "./...")
    )
    # go is unionable: both sources spell an identity `TestX`/`TestX/sub`, so
    # the wheel's column-0 answers come first and the indented subtests its
    # anchor could never reach are appended. Fill-only-if-empty would lose
    # every subtest, because `go test -v` always prints a column-0 parent.
    assert failing == ["TestEnv", "TestEnv/reads_var"]
    assert passing == ["TestCommand", "TestEnv/defaults"]


def test_go_package_lines_are_never_test_names():
    """`FAIL\\tpkg [build failed]` names a package. Naming it would let the
    next green run "conserve" a test that never existed."""
    passing, failing, _counts = parse_test_names(
        GO_BUILD_FAILURE, family="go", command=("go", "test", "./...")
    )
    assert passing == []
    assert failing == []


# --- jest / vitest --------------------------------------------------------

def test_jest_bullet_header_is_read_and_console_noise_is_not():
    passing, failing, _counts = parse_test_names(
        JEST_DEFAULT_REPORTER, family="jest", command=("npx", "jest")
    )
    assert passing == ["initializes by dependency levels"]
    # The wheel's own `✕` row wins; the `●` header only fills an empty side.
    assert failing == ["registers initializer via register map"]
    assert not any("Console" in name for name in failing)


def test_jest_timing_suffix_is_stripped_on_both_sides():
    """The wheel strips `(7 ms)` from a passing name and not from a failing
    one, so one test could carry two identities. One name space, or the
    conservation check between baseline and observation is meaningless."""
    _passing, failing, _counts = parse_test_names(
        JEST_DEFAULT_REPORTER, family="jest", command=("npx", "jest")
    )
    assert all("ms)" not in name for name in failing)


def test_vitest_fail_line_names_the_test_not_the_file():
    passing, failing, _counts = parse_test_names(
        VITEST_DEFAULT_REPORTER, family="vitest",
        command=("npx", "vitest", "run"),
    )
    assert passing == ["tests/handlers/chat.test.ts > chat > streams tokens"]
    assert failing == [
        "tests/handlers/delegateTask.test.ts > delegateTask > aborts on signal"
    ]


# --- mocha ----------------------------------------------------------------

def test_mocha_numbered_failure_block_is_read():
    passing, failing, counts = parse_test_names(
        MOCHA_SPEC_REPORTER, family="mocha", command=("npx", "mocha")
    )
    assert passing == ["handles escapes", "parses shorthand"]
    assert failing == ["lexer parses nested"]
    assert (counts["passed"], counts["failed"]) == (2, 1)


def test_mocha_single_line_failure_block_is_read():
    _passing, failing, _counts = parse_test_names(
        MOCHA_SINGLE_LINE_FAILURE, family="mocha", command=("npx", "mocha")
    )
    assert failing == ["reporter writes a report file"]


# --- the delegation contract ---------------------------------------------

@pytest.mark.parametrize(
    ("command", "family"),
    [
        ("uvx pytest tests/ -q", "pytest"),
        ("cd /app && cargo test -p pest", "cargo"),
        ("go test ./evaluator", "go"),
        ("npx vitest run tests/a.test.ts", "vitest"),
        ("cd /app && npx mocha t/", "mocha"),
        ("cd /app && npm test", "node"),
        ("cat pytest.ini", ""),
    ],
)
def test_family_for_command_matches_the_shape_parser(command, family):
    assert family_for_command(command) == family


def test_unknown_family_falls_back_to_nothing_at_all():
    """No family, no extractor: the wheel's answer is the whole answer."""
    passing, failing, _counts = parse_test_names(
        MOCHA_SPEC_REPORTER, family="", command=()
    )
    assert passing == ["handles escapes", "parses shorthand"]
    assert failing == []


def test_names_do_not_need_the_command_but_counts_do():
    """The wheel keys its per-runner SUMMARY patterns on the argv, so counts
    degrade to zero without it while the name extractors are unaffected. Both
    call sites pass the argv; this pins what happens when one cannot."""
    passing, failing, counts = parse_test_names(
        CARGO_VERBOSE, family="cargo", command=()
    )
    assert passing == ["sorting_test", "merge_test"]
    assert failing == ["dedup_test"]
    assert counts == {"passed": 0, "failed": 0, "errored": 0}


def test_bytes_output_is_decoded_not_rejected():
    passing, _failing, _counts = parse_test_names(
        CARGO_VERBOSE.encode("utf-8"), family="cargo", command=("cargo", "test")
    )
    assert passing == ["sorting_test", "merge_test"]
