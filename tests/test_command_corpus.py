"""What ``test_command_shape`` reads out of 964 real model test commands.

The fixture is the distinct ``test_commands`` slice of 790 recorded agent
trajectories (``tests/fixtures/command_corpus/model_test_commands.json``,
1282 entries).  318 of those carry a heredoc body - ``cat > t.py <<EOF`` is a
file WRITE, nothing about the run is readable from the argv - so the 964
non-heredoc entries are the population every rate below is measured over.

Three layers, deliberately:

* ``LABELLED`` - a hand-checked sample with the exact ``(scope, family,
  coverage)`` each command must read.  This is the layer that catches a
  silent semantic drift (a narrowed run starting to read ``suite``), which
  no aggregate rate can see.
* per-family ``unknown`` ceilings - the aggregate.  ``unknown`` is an
  abstention, and an abstention is not free: every gate that keys on a test
  boundary is dead on a command the parser will not read.  The ceilings are
  set just above the measured rate so a regression trips, and they print the
  offending commands when they trip.
* ``PLAIN_RUNNER_INVOCATIONS`` - commands taken verbatim out of the corpus's
  own unknown set that are a single runner run next to a static check, a
  ``git add``/``commit``, or a ``--version`` probe.  Nothing about those
  segments can change what the runner collected, so ``unknown`` on them is a
  parser gap, not conservatism.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import pytest

# Aliased: pytest would otherwise COLLECT the imported `test_command_shape`
# function as a test case.
from gt_engine.runtime_observation import test_command_shape as read_shape

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures" / "command_corpus" / "model_test_commands.json"
)
_HEREDOC_RE = re.compile(r"<<-?\s*[\"'\\]?\w")

# Which runner a command is ABOUT, read off the raw text rather than off the
# parser - a classifier that used the parser's own answer could never measure
# the parser's blind spots. Ordered: the first probe that matches wins, so
# `npm test` running jest under the hood counts once, against npm.
_FAMILY_PROBES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pytest", re.compile(r"\bpytest\b|\bpy\.test\b")),
    ("npm/pnpm/yarn", re.compile(r"\b(?:npm|pnpm|yarn)\b")),
    ("jest/vitest/mocha", re.compile(r"\b(?:jest|vitest|mocha)\b")),
    ("cargo", re.compile(r"\bcargo\b")),
    ("go", re.compile(r"\bgo\s+test\b")),
    ("unittest", re.compile(r"\bunittest\b")),
)

# Two denominators, because they answer two different questions.
#
# MENTIONS: every command whose text names the family, which is what the
# whole fixture reduces to. It includes `cat pytest.ini`, `ls tests/` and
# `pip install pytest` - commands that run no test at all and that the parser
# is RIGHT to read `unknown`. Ratchet only: these are today's measurement,
# and they exist so a regression trips, not as a quality target.
_MENTION_CEILINGS = {
    "pytest": 0.16,
    "npm/pnpm/yarn": 0.25,
    "jest/vitest/mocha": 0.25,
    "cargo": 0.25,
    "go": 0.25,
}
# INVOCATIONS: commands where the runner actually stands in command position
# (see `invokes_runner`). This is the population the parser is answerable
# for, and it carries the real targets: 15% for pytest, 25% elsewhere.
_INVOCATION_CEILINGS = {
    "pytest": 0.15,
    "npm/pnpm/yarn": 0.25,
    "jest/vitest/mocha": 0.25,
    "cargo": 0.25,
    # go sits at 25.4%: 4 of its 18 abstentions are `gofmt -w x.go && go test
    # ...`, where the formatter rewrote the sources the run then compiled, and
    # most of the rest run `go test` twice. Both are correct abstentions, so
    # the ceiling records the measurement rather than pretending otherwise.
    "go": 0.26,
}
# Why the pytest mention-ceiling is 16% and not 15%. Of the 81 residual
# abstentions, measured: 28 never invoke pytest (`cat pytest.ini`), 20 run
# pytest twice into one byte stream, 29 have a neighbour that writes or runs
# arbitrary code (`python -c ...`, `ruff format`, a `for` loop), and 4 are
# runner probes (`pytest --version`). Every one is an abstention the parser
# SHOULD make, so reaching 15% on THIS denominator would mean guessing at
# five of them. `test_mention_ceiling_is_not_met_by_guessing` pins the first
# of those groups so the ceiling can only be lowered by real parser work.
_MIN_NON_INVOKING_PYTEST_ABSTENTIONS = 24

# Wrappers that say where a program comes from, not what it does. Local to
# the test on purpose: measuring the parser with the parser's own wrapper
# table would hide a gap in that table.
_SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[;\n|]")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PROBE_WRAPPERS = frozenset({
    "sudo", "nohup", "time", "env", "xvfb-run", "stdbuf", "setsid", "exec",
    "command", "ionice", "uvx", "npx", "bunx",
})
_PROBE_RUN_PAIRS = frozenset({
    ("uv", "run"), ("poetry", "run"), ("pdm", "run"), ("hatch", "run"),
    ("pipenv", "run"), ("rye", "run"), ("pnpm", "exec"), ("pnpm", "dlx"),
    ("yarn", "dlx"),
})
_PROBE_INTERPRETER_RE = re.compile(r"python[\d.]*|py|pypy[\d.]*")
_PROBE_DURATION_RE = re.compile(r"[\d.]+[smhd]?")
_FAMILY_HEADS = {
    "pytest": frozenset({"pytest", "py.test"}),
    "npm/pnpm/yarn": frozenset({"npm", "pnpm", "yarn"}),
    "jest/vitest/mocha": frozenset({"jest", "vitest", "mocha"}),
    "cargo": frozenset({"cargo"}),
    "go": frozenset({"go"}),
    "unittest": frozenset({"unittest"}),
}


def corpus_family(command: str) -> str:
    for name, probe in _FAMILY_PROBES:
        if probe.search(command):
            return name
    return "other"


def load_corpus() -> list[str]:
    commands = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return [c for c in commands if not _HEREDOC_RE.search(c)]


def segment_heads(command: str) -> list[str]:
    """The program word each shell segment actually runs, wrappers peeled.

    Deliberately cruder than the parser under test and deliberately more
    INCLUSIVE than it: it applies no benignity and no ambiguity rule, so it
    can never hide a parser gap. All it removes is the difference between
    running a runner and merely naming one - `cat pytest.ini` has head `cat`.
    """
    heads: list[str] = []
    for raw in _SEGMENT_SPLIT_RE.split(command):
        words = raw.split()
        i = 0
        while i < len(words):
            word = words[i].strip("'\"()`")
            base = word.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            base = re.sub(r"(?i)\.(?:exe|bat|cmd)$", "", base)
            if base in ("timeout", "nice"):
                i += 1
                while i < len(words) and words[i].startswith("-"):
                    i += 1
                if i < len(words) and _PROBE_DURATION_RE.fullmatch(words[i]):
                    i += 1
                continue
            if _ASSIGNMENT_RE.match(word) or base in _PROBE_WRAPPERS:
                i += 1
                continue
            following = words[i + 1].strip("'\"") if i + 1 < len(words) else ""
            if (base, following) in _PROBE_RUN_PAIRS:
                i += 2
                continue
            if _PROBE_INTERPRETER_RE.fullmatch(base):
                j = i + 1
                while j < len(words) and words[j].startswith("-") and words[j] != "-m":
                    j += 1
                if words[j:j + 1] == ["-m"] and j + 1 < len(words):
                    heads.append(words[j + 1].strip("'\""))
                break
            heads.append(base)
            break
    return heads


def invokes_runner(command: str, family: str) -> bool:
    wanted = _FAMILY_HEADS[family]
    return any(head in wanted for head in segment_heads(command))


# --- layer 1: the hand-checked sample ------------------------------------
#
# (command, scope, family, coverage). Every row was read by hand against what
# the command actually runs; the comment on a group names the shape it pins.
LABELLED: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # -- pytest: plain invocations, paths, nodeids, -k/-m narrowing --------
    ("cd /app && pytest", "suite", "pytest", ()),
    ("cd /app && python -m pytest -q 2>&1 | tail -3", "suite", "pytest", ()),
    (
        "cd /testbed && python -m pytest -v 2>&1 | tail -38 && "
        'echo "=== git status ===" && git status --short',
        "suite", "pytest", (),
    ),
    (
        "cd /testbed && pytest -v tests/test_base.py tests/test_utils.py 2>&1 | tail -8",
        "scoped", "pytest", ("tests/test_base.py", "tests/test_utils.py"),
    ),
    (
        "cd /testbed/tests && python -m pytest test_base.py test_utils.py -q 2>&1 | tail -5",
        "scoped", "pytest", ("test_base.py", "test_utils.py"),
    ),
    (
        "cd /app && pytest -q tests/test_snapshots.py::test_format_snapshot_task_stack_shape"
        " 2>&1 | tail -3",
        "scoped", "pytest",
        ("tests/test_snapshots.py::test_format_snapshot_task_stack_shape",),
    ),
    (
        "cd /app && pytest -q tests/test_snapshot_cli.py -k where 2>&1 | tail -2",
        "scoped", "pytest", ("tests/test_snapshot_cli.py",),
    ),
    (
        'cd /testbed && python -m pytest tests/ -m "not integration" -q 2>&1 | tail -8',
        "scoped", "pytest", ("tests/",),
    ),
    (
        # `-p no:cacheprovider` is a VALUE option: `no:cacheprovider` is not a path.
        "pytest -p no:cacheprovider -q",
        "suite", "pytest", (),
    ),
    (
        # --ignore subtracts; the run still collected everything else.
        "pytest --ignore=tests/slow -q",
        "scoped", "pytest", (),
    ),
    (
        "cd /testbed && git stash && python -m pytest tests/test_base.py::test_get_item"
        " -q 2>&1 | tail -10; git stash pop",
        "scoped", "pytest", ("tests/test_base.py::test_get_item",),
    ),
    (
        # A Windows-shaped interpreter still names the runner.
        r'"C:\Python312\python.exe" -m pytest tests/test_x.py',
        "scoped", "pytest", ("tests/test_x.py",),
    ),
    # -- unittest ---------------------------------------------------------
    ("python -m unittest discover", "suite", "unittest", ()),
    (
        "python -m unittest tests.test_thing.Case.test_one",
        "scoped", "unittest", ("tests.test_thing.Case.test_one",),
    ),
    # -- cargo: -p is coverage, a bare word is a libtest filter ------------
    (
        'cd /app && cargo test 2>&1 | grep -E "test result:|FAILED"',
        "suite", "cargo", (),
    ),
    ("cd /app && cargo test --workspace 2>&1 | tail -20", "suite", "cargo", ()),
    (
        "cd /app && timeout 300 cargo test -p pest --lib 2>&1 | tail -15",
        "scoped", "cargo", ("pest",),
    ),
    (
        "cd /app && FORCE_COLOR=1 timeout 200 cargo test -p pest_meta -- "
        "--test coalesce_is_final_top_down_pass 2>&1",
        "scoped", "cargo", ("pest_meta",),
    ),
    (
        "cd /app && cargo nextest run -p oxvg_ast 2>&1 | tail -30",
        "scoped", "cargo", ("oxvg_ast",),
    ),
    (
        "cd /app && timeout 300 cargo test -p oxvg_ast 2>&1 | tail -30",
        "scoped", "cargo", ("oxvg_ast",),
    ),
    # -- go: ./... is the suite, a bare `go test` is only this package -----
    ("cd /app && go test ./... 2>&1 | head -60", "suite", "go", ()),
    (
        "cd /app && go test ./evaluator/... 2>&1 | tail -20",
        "scoped", "go", ("evaluator",),
    ),
    ("cd /app && go test -count=1 ./parser", "scoped", "go", ("parser",)),
    (
        "cd /app && timeout 300 env CONTEXT=abs go test "
        'github.com/abs-lang/abs/util 2>&1 | tail -20; echo "EXIT: $?"',
        "scoped", "go", ("github.com/abs-lang/abs/util",),
    ),
    (
        # -run narrows WITHIN ./..., so the run may fold names, never promote.
        "go test -run TestRequire ./...",
        "scoped", "go", (),
    ),
    (
        "cd /app && go test -v -count=1 ./evaluator 2>&1 | "
        'grep -cE "^--- PASS:"; echo "exit=${PIPESTATUS[0]}"',
        "scoped", "go", ("evaluator",),
    ),
    # -- jest / vitest / mocha --------------------------------------------
    (
        "cd $(pwd) && npx jest --no-coverage 2>&1 | tail -60",
        "suite", "jest", (),
    ),
    (
        'cd /app && npx jest tests/initialize.test.ts -t "register accepts object map"'
        " 2>&1 | tail -12",
        "scoped", "jest", ("tests/initialize.test.ts",),
    ),
    (
        "cd /app/backend && npx vitest run tests/handlers/delegateTask.test.ts 2>&1 | tail -60",
        "scoped", "vitest", ("tests/handlers/delegateTask.test.ts",),
    ),
    (
        "cd /app/backend && npx vitest run --reporter=verbose "
        'tests/handlers/multiAgentChat.test.ts -t "should manage abort controllers correctly"'
        " 2>&1 | tail -40",
        "scoped", "vitest", ("tests/handlers/multiAgentChat.test.ts",),
    ),
    (
        "cd /app && npx mocha tests/utils/report-file_tests.js 2>&1 | tail -30",
        "scoped", "mocha", ("tests/utils/report-file_tests.js",),
    ),
    (
        "cd /app && timeout 300 npx mocha lib/__tests/exports.js lib/__tests/lexer.js"
        " --require lib/__tests/helpers/setup.js --reporter dot 2>&1 | tail -8",
        "scoped", "mocha", ("lib/__tests/exports.js", "lib/__tests/lexer.js"),
    ),
    (
        # A whole-suite run of ONE package: where it ran is its coverage.
        "cd ark/json-schema && pnpm test",
        "suite", "node", ("ark/json-schema",),
    ),
    # -- npm / pnpm / yarn scripts ----------------------------------------
    ("cd /app && npm test 2>&1 | tail -25", "suite", "node", ("/app",)),
    (
        'cd /app; npm test 2>&1 | tail -25; echo "EXIT:${PIPESTATUS[0]}"',
        "suite", "node", ("/app",),
    ),
    ("cd /app && timeout 600 npm test 2>&1 | tail -6", "suite", "node", ("/app",)),
    (
        'cd /app; npm test -- tests/initialize.test.ts -t "works with asFunction and '
        'asClass resolvers" 2>&1 | grep -E "Tests:|PASS|FAIL"',
        "scoped", "node", ("tests/initialize.test.ts",),
    ),
    (
        'cd /app && yarn test:lint 2>&1 | tail -20; echo "real exit: ${PIPESTATUS[0]}"',
        "suite", "node", ("/app",),
    ),
    # -- the abstentions, one per cause -----------------------------------
    (
        # two runs, one byte stream: which run produced the names?
        "cd /app && python -m pytest tests/unit/core/test_taint.py 2>&1 | tail -2 && "
        "python -m pytest tests/functional/test_functional.py 2>&1 | tail -2",
        "unknown", "unknown", (),
    ),
    (
        # backgrounded: the runner's bytes never land in this command's output
        'cd /app && nohup cargo test > /tmp/build_full.log 2>&1 & echo started;'
        " sleep 5; tail -3 /tmp/build_full.log",
        "unknown", "unknown", (),
    ),
    (
        # a loop runs the runner N times
        "cd /app && for t in a b c; do timeout 40 python -m pytest "
        '"tests/test_snapshots.py::$t" -q 2>&1 | tail -3; done',
        "unknown", "unknown", (),
    ),
    (
        # a heredoc body is arbitrary text, including a whole test file
        "cat > tests/test_new.py <<'EOF'\nimport pytest\nEOF\npytest -q",
        "unknown", "unknown", (),
    ),
    (
        # no runner at all: reading a config file is not running it
        "cd /app && cat pytest.ini && echo '---' && cat aiomonitor/types.py",
        "unknown", "unknown", (),
    ),
    (
        # a non-benign neighbour can change what the runner then sees
        "cd $(pwd) && cp /tmp/edge.test.ts src/__tests__/initialization.edge.test.ts && "
        "npx jest src/__tests__/initialization.edge.test.ts --no-coverage 2>&1 | tail -60",
        "unknown", "unknown", (),
    ),
    (
        # `pytest --version` runs no test at all; calling it `suite` would let
        # a version banner promote a whole collection.
        "cd /testbed && which pytest && pytest --version",
        "unknown", "unknown", (),
    ),
    (
        "cd /app && go test --help 2>&1 | head -50",
        "unknown", "unknown", (),
    ),
    (
        # --no-run compiles the tests and stops.
        "cargo test -p oxvg_optimiser --no-run",
        "unknown", "unknown", (),
    ),
    (
        "pytest --collect-only -q tests/",
        "unknown", "unknown", (),
    ),
    # -- wrapper forms: uvx / uv run / poetry / npx / pnpm exec -----------
    (
        "uvx pytest tests/test_outputs.py -q",
        "scoped", "pytest", ("tests/test_outputs.py",),
    ),
    ("uv run --project /app pytest -q", "suite", "pytest", ()),
    ("cd /app && uv run pytest tests/ -x", "scoped", "pytest", ("tests/",)),
    ("uv run -- pytest -q", "suite", "pytest", ()),
    ("poetry run pytest", "suite", "pytest", ()),
    ("pipenv run pytest tests/test_a.py", "scoped", "pytest", ("tests/test_a.py",)),
    ("pdm run pytest tests/", "scoped", "pytest", ("tests/",)),
    ("hatch run pytest", "suite", "pytest", ()),
    ("npx jest --no-coverage 2>&1 | tail -30", "suite", "jest", ()),
    ("pnpm exec vitest run", "suite", "vitest", ()),
    ("yarn jest", "suite", "jest", ()),
    ("pnpm dlx mocha test/", "scoped", "mocha", ("test/",)),
)


@pytest.mark.parametrize(
    ("command", "scope", "family", "coverage"),
    LABELLED,
    ids=[f"{i:02d}-{row[2]}-{row[1]}" for i, row in enumerate(LABELLED)],
)
def test_labelled_corpus_sample(command, scope, family, coverage):
    shape = read_shape(command)
    assert (shape.scope, shape.family, shape.coverage) == (scope, family, coverage)


def test_labelled_sample_is_broad_enough():
    """A sample that drifts to one family stops being a cross-family guard."""
    assert len(LABELLED) >= 40
    families = {row[2] for row in LABELLED}
    assert {"pytest", "cargo", "go", "jest", "vitest", "mocha", "node",
            "unittest", "unknown"} <= families
    assert {row[1] for row in LABELLED} == {"suite", "scoped", "unknown"}


# --- layer 2: the aggregate ----------------------------------------------

def _unknown_rates(*, invoking_only: bool):
    totals: collections.Counter[str] = collections.Counter()
    unknown: collections.Counter[str] = collections.Counter()
    offenders: dict[str, list[str]] = collections.defaultdict(list)
    for command in load_corpus():
        family = corpus_family(command)
        if family not in _FAMILY_HEADS:
            continue
        if invoking_only and not invokes_runner(command, family):
            continue
        totals[family] += 1
        if read_shape(command).scope == "unknown":
            unknown[family] += 1
            offenders[family].append(command)
    return totals, unknown, offenders


def _assert_under(ceilings, totals, unknown, offenders, label):
    breaches = []
    for family, ceiling in ceilings.items():
        total = totals[family]
        assert total, f"no {family} commands in the corpus - probe broken?"
        rate = unknown[family] / total
        if rate > ceiling:
            sample = "\n".join(f"    {c[:160]}" for c in offenders[family][:25])
            breaches.append(
                f"{family}: {unknown[family]}/{total} = {rate:.2%} "
                f"> {ceiling:.0%}\n{sample}"
            )
    assert not breaches, f"{label} ceiling breached:\n" + "\n".join(breaches)


def test_corpus_population_is_the_one_the_ceilings_were_measured_on():
    assert len(load_corpus()) == 964, "fixture changed; re-measure the ceilings"


def test_unknown_rate_over_runner_invocations_stays_under_ceiling():
    """The real target: how often the parser abstains on an actual run."""
    totals, unknown, offenders = _unknown_rates(invoking_only=True)
    _assert_under(_INVOCATION_CEILINGS, totals, unknown, offenders, "invocation")


def test_unknown_rate_over_family_mentions_does_not_regress():
    """The ratchet, on the same denominator the wave was first measured on."""
    totals, unknown, offenders = _unknown_rates(invoking_only=False)
    _assert_under(_MENTION_CEILINGS, totals, unknown, offenders, "mention")


def test_mention_ceiling_is_not_met_by_guessing():
    """Most of what the mention-ceiling still counts is unguessable.

    If a future change ever drove the pytest mention rate down by reading
    `cat pytest.ini` or `pip install pytest` as a run, this trips: those
    commands have no pytest in command position, so `unknown` is the only
    honest answer and the count must stay high.
    """
    unfixable = [
        command for command in load_corpus()
        if corpus_family(command) == "pytest"
        and not invokes_runner(command, "pytest")
        and read_shape(command).scope == "unknown"
    ]
    assert len(unfixable) >= _MIN_NON_INVOKING_PYTEST_ABSTENTIONS, (
        "a command that never invokes pytest is now being read as a run:\n"
        + "\n".join(f"    {c[:160]}" for c in unfixable[:10])
    )


# --- layer 3: commands that must not abstain -----------------------------
#
# Every entry is verbatim from the corpus's unknown set. In each one the
# runner runs exactly once and every other segment is a static check, a
# read-only git/pip query, a version probe or a plain shell builtin - none of
# which can change which tests ran or add a runner name to the byte stream.
PLAIN_RUNNER_INVOCATIONS: tuple[str, ...] = (
    "cd /app && python -m pytest -q 2>&1 | tail -4 && "
    "python -m ruff check aiomonitor tests 2>&1 | tail -2",
    "cd /app && python -m ruff check aiomonitor/ tests/ 2>&1 | tail -2 && "
    "python -m pytest -q 2>&1 | tail -3",
    "cd /app && python -m ruff check aiomonitor/ tests/ 2>&1 | tail -3 && "
    "python -m pytest -q 2>&1 | tail -3",
    "cd /app && ruff check aiomonitor/ tests/ && python -m pytest -q 2>&1 | tail -4",
    "cd /app && ruff check aiomonitor/ tests/ && python -m pytest -q 2>&1 | tail -5",
    'cd /app && python -m pytest -q 2>&1 | grep -E "passed|failed" && '
    "python -m ruff check aiomonitor tests 2>&1 | tail -2 && "
    "python -m mypy aiomonitor 2>&1 | tail -2",
    "cd /app && python -m pytest tests/ -q 2>&1 | tail -5 && "
    "python -m mypy aiomonitor/ 2>&1 | tail -3",
    'cd /app && python -m pytest -q 2>&1 | tail -3 && echo "=== lint ===" && '
    "python -m ruff check aiomonitor tests 2>&1 | tail -2 && "
    'echo "=== mypy ===" && python -m mypy aiomonitor 2>&1 | tail -2 && '
    'echo "=== git ===" && git status --porcelain && echo "(clean)" && '
    "git log --oneline -3 && git rev-parse --abbrev-ref HEAD",
    'cd /app && python -m pytest -q 2>&1 | tail -3 && echo "===STATUS===" && '
    'git status --porcelain && echo "===BRANCH===" && git branch --show-current && '
    'echo "===RUFF===" && ruff check aiomonitor/ tests/ 2>&1 | tail -2',
    'cd /app && python -m pytest -q 2>&1 | tail -5 && echo "===== RUFF =====" && '
    "python -m ruff check aiomonitor tests 2>&1 | tail -5",
    "cd /app && python -m pytest tests/ -q 2>&1 | tail -6 && "
    'echo "===RUFF===" && python -m ruff check aiomonitor/ tests/ 2>&1 | tail -5 && '
    'echo "===FORMAT===" && python -m ruff format --check aiomonitor/ tests/ 2>&1 | tail -5 && '
    'echo "===MYPY===" && python -m mypy aiomonitor/ 2>&1 | tail -5',
    "cd /app && python -m ruff check aiomonitor/ 2>&1 | head -10 && "
    "python -m ruff format --check aiomonitor/ 2>&1 | head -10 && "
    "python -m pytest tests/ -q 2>&1 | tail -8",
    "cd /app && timeout 120 python -m pytest -q 2>&1 | tail -4 && "
    'echo "=== FINAL CHECKS ===" && timeout 60 python -m ruff check aiomonitor/ tests/ '
    "2>&1 | tail -2 && timeout 60 python -m ruff format --check aiomonitor/ tests/ "
    "2>&1 | tail -2 && timeout 120 python -m mypy aiomonitor/ 2>&1 | tail -2",
    "cd /app && git log --oneline -1 && git branch --show-current && "
    'echo "=== TESTS ===" && python -m pytest tests/test_monitor.py tests/test_snapshots.py'
    ' -q 2>&1 | tail -3 && echo "=== RUFF ===" && '
    "python -m ruff check aiomonitor tests 2>&1 | tail -1",
    "cd /app && git add tests/unit/core/test_config.py tests/unit/cli/test_cache.py && "
    'git commit -m "Expand cache tests: config settings and incompatible format_version '
    'import" && python3 -m pytest tests/unit -q 2>&1 | tail -3',
    "cd /app && git add tests/test_snapshots_branch.py && "
    'git commit -m "Add branch/clean-tree acceptance test for snapshot feature work" && '
    'git status --porcelain && echo "clean" && pytest -v tests/test_snapshots_branch.py'
    " 2>&1 | tail -5",
    "cd /app && python --version && python -m pytest tests/test_monitor.py -x -q 2>&1 | tail -20",
    "cd /app && python -m pytest -q 2>&1 | tail -20 && python --version && "
    'pip list 2>/dev/null | grep -E "aiohttp|pydantic|jinja|pytest|click|prompt"',
    "cd /app && npm test 2>&1 | tail -3 && npm run lint 2>&1 | tail -5",
    "cd /app && npx tsc --noEmit 2>&1 | tail -5 && npx jest --no-coverage 2>&1 | tail -30",
)


@pytest.mark.parametrize("command", PLAIN_RUNNER_INVOCATIONS)
def test_plain_runner_invocations_are_readable(command):
    shape = read_shape(command)
    assert shape.scope != "unknown", (
        "a single runner run beside read-only neighbours must be readable"
    )
    assert shape.family != "unknown"


def test_plain_runner_invocation_list_is_the_promised_size():
    assert len(PLAIN_RUNNER_INVOCATIONS) >= 20


def test_mutating_neighbours_still_abstain():
    """The other side of the same fix: a neighbour that WRITES stays unknown.

    `ruff format` (no --check) rewrites the sources the runner then imports,
    and `git worktree remove --force` deletes a tree - admitting either would
    trade a real hazard for a coverage statistic.
    """
    for command in (
        "cd /app && timeout 60 python -m ruff format aiomonitor/termui/commands.py "
        "2>&1 | tail -2 && timeout 120 python -m pytest -q 2>&1 | tail -5",
        "cd /app && black src/ && pytest -q",
        "cd /app && npx eslint --fix src/ && npx jest",
        "cd /app && gofmt -w evaluator/module.go && go test ./evaluator",
        "cd /app && git worktree remove /tmp/base --force && pytest -q",
    ):
        assert read_shape(command).scope == "unknown", command
