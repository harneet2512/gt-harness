"""One name space for every runner, on both sides of the comparison.

The certified wheel (``groundtruth.runtime.test_runner``) is the canonical
name source and stays that way: it is what the baseline capture, the runtime
observation and the conservation check have always agreed on, and two sides
that parse differently compare two different name spaces. It is also pinned,
so it cannot be taught anything new.

What it does not know, measured against realistic runner output:

===============  ===========================================================
cargo            names only from ``test a::b ... FAILED``/``... ok`` rows at
                 column 0. A run piped through ``| tail -40`` keeps only the
                 ``failures:`` block, and that block yields nothing.
go               ``^--- FAIL:`` is anchored at column 0, so every SUBTEST
                 (``    --- FAIL: TestEnv/reads_var``) is invisible, and so
                 is every ``    --- PASS:`` under a verbose parent.
jest / vitest    the check-mark rows parse, but a failure printed as a
                 ``● Suite › name`` header or a ``FAIL file > suite > name``
                 line - the default reporters' actual output - does not.
mocha            ``✓`` rows parse; the numbered ``1) suite\\n   name:``
                 failure block does not.
===============  ===========================================================

So this module delegates first, then, for a non-pytest family only, either
fills a direction the wheel left EMPTY or - for go and cargo, whose fallback
spells an identity exactly as the wheel does - appends the names the wheel's
column-0 anchors could not reach. It never overrides, drops or reorders a
wheel answer, never runs for pytest or unittest (the wheel owns those
grammars outright), and never invents an identity that is not a test: a go ``FAIL\\tgithub.com/x/y`` line
names a PACKAGE, and emitting it as a test name would let a compile error be
conserved as a passing test.

Both callers - ``persistent_plan.baseline._parse`` (the captured baseline)
and ``miniswe_integration._parse_run_aggregate`` (every observed run) - go
through ``parse_test_names``, so the two sides cannot drift apart.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from typing import NamedTuple

__all__ = ["ParsedTestNames", "family_for_command", "parse_test_names"]

_EMPTY_COUNTS = {"passed": 0, "failed": 0, "errored": 0}

# The wheel owns these grammars; a fallback here could only disagree with it.
_WHEEL_OWNED = frozenset({"pytest", "unittest", "", "unknown"})

# Trailing `(5 ms)` / `(1.2s)` timing that a reporter appends to a name. The
# wheel strips it on the passing side and NOT on the failing side, so a name
# could pass as "x" and fail as "x (5 ms)" - stripping both ways here keeps
# one identity per test.
_TIMING_SUFFIX_RE = re.compile(r"\s*\(\s*[\d.]+\s*(?:ms|s|m|µs|us|ns)?\s*\)\s*$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class ParsedTestNames(NamedTuple):
    passing: list[str]
    failing: list[str]
    counts: dict[str, int]


def _clean(name: str) -> str:
    return _TIMING_SUFFIX_RE.sub("", name.strip()).strip()


def _dedup(names) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = _clean(raw)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _words(command: str | Sequence[str]) -> list[str]:
    """Argv for the wheel's count parser. Never raises on bad quoting.

    Callers hand this whatever they have, including a whole compound shell
    line with an unbalanced quote inside a commit message. ``shlex.split``
    raises on that, and a name parse must not be the thing that fails a run.
    """
    if not command:
        return []
    if not isinstance(command, str):
        return list(command)
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def family_for_command(command: str | Sequence[str]) -> str:
    """Which runner produced a command's bytes, or ``""`` when unreadable."""
    text = command if isinstance(command, str) else shlex.join(list(command))
    from .runtime_observation import test_command_shape

    family = test_command_shape(text).family
    return "" if family == "unknown" else family


# --- family extractors ---------------------------------------------------
#
# Each returns (passing, failing). None of them ever replaces a wheel answer
# (see `_merge`), so a miss costs an abstention and never a wrong name.

_CARGO_OK_RE = re.compile(r"(?m)^\s*test\s+(\S+)\s+\.\.\.\s+ok\b")
_CARGO_FAILED_RE = re.compile(r"(?m)^\s*test\s+(\S+)\s+\.\.\.\s+FAILED\b")
# `---- module::test stdout ----` heads each failing test's captured output.
_CARGO_STDOUT_RE = re.compile(r"(?m)^-{4}\s+(\S+)\s+stdout\s+-{4}")
_CARGO_FAILURES_HEAD_RE = re.compile(r"^\s*failures:\s*$")
# A name inside the `failures:` block: one indented path-ish token per line.
_CARGO_FAILURE_NAME_RE = re.compile(r"^\s{2,}([A-Za-z_][\w:]*(?:::[\w:]+)*)\s*$")


def _cargo_failures_block(text: str) -> list[str]:
    """The `failures:` roll-call cargo prints after the per-test rows.

    It is the only name-bearing part that survives `cargo test 2>&1 | tail
    -40`, which is how most of the corpus's Rust runs are captured.
    """
    names: list[str] = []
    inside = False
    for line in text.splitlines():
        if _CARGO_FAILURES_HEAD_RE.match(line):
            inside = True
            continue
        if not inside:
            continue
        if not line.strip():
            continue
        match = _CARGO_FAILURE_NAME_RE.match(line)
        if match:
            names.append(match.group(1))
            continue
        inside = False
    return names


def _extract_cargo(text: str) -> tuple[list[str], list[str]]:
    failing = [
        *_CARGO_FAILED_RE.findall(text),
        *_CARGO_STDOUT_RE.findall(text),
        *_cargo_failures_block(text),
    ]
    return _CARGO_OK_RE.findall(text), failing


# Indented because `go test -v` nests subtests under their parent.
_GO_PASS_RE = re.compile(r"(?m)^\s*--- PASS:\s+([^\s(]+)")
_GO_FAIL_RE = re.compile(r"(?m)^\s*--- FAIL:\s+([^\s(]+)")


def _extract_go(text: str) -> tuple[list[str], list[str]]:
    # `FAIL\tgithub.com/x/y\t0.2s` is deliberately NOT read: it names a
    # package. A package that fails to COMPILE prints exactly that and no
    # test names at all, and calling the package a test identity would let
    # the next green run "conserve" a test that never existed.
    return _GO_PASS_RE.findall(text), _GO_FAIL_RE.findall(text)


_TICK_RE = re.compile(r"(?m)^\s*[✓✔√]\s+(.+?)\s*$")
_CROSS_RE = re.compile(r"(?m)^\s*[✕✗×✖✘]\s+(.+?)\s*$")
# jest's failure header: `● Suite › test name`. `● Console` and
# `● Test suite failed to run` are the reporter talking about itself.
_BULLET_RE = re.compile(r"(?m)^\s*●\s+(.+?)\s*$")
_BULLET_NOISE = ("Console", "Test suite failed to run", "Deprecation Warning")
# vitest: ` FAIL  tests/x.test.ts > suite > name`. The whole `file > suite >
# name` path is captured, because that is exactly the spelling the same
# run's `×` rows use - capturing only the tail here would mint a second
# identity for one test. jest prints `FAIL path` with no `>`, which names a
# FILE, not a test, and is skipped.
_FAIL_PATH_RE = re.compile(r"(?m)^\s*FAIL\s+(\S+\s*>.+?)\s*$")


def _extract_node(text: str) -> tuple[list[str], list[str]]:
    failing = [
        *_CROSS_RE.findall(text),
        *_FAIL_PATH_RE.findall(text),
        *(
            name for name in _BULLET_RE.findall(text)
            if not name.startswith(_BULLET_NOISE)
        ),
    ]
    return _TICK_RE.findall(text), failing


_MOCHA_NUMBERED_RE = re.compile(r"^(\s*)(\d+)\)\s*(.*)$")


def _extract_mocha(text: str) -> tuple[list[str], list[str]]:
    """mocha's numbered failure block.

    ::

          1) lexer
               parses nested:
            AssertionError: ...

    The suite title sits on the ``N)`` line and the test title on the
    following, more-indented line ending in a colon. UNCERTAIN: mocha also
    prints the whole title on the ``N)`` line when it is short
    (``1) lexer parses nested:``), and a deeply nested suite spreads over
    three or more lines. Both shapes are handled below, but only the two-line
    and one-line forms are attested in the corpus.
    """
    passing, failing = _extract_node(text)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        head = _MOCHA_NUMBERED_RE.match(line)
        if not head:
            continue
        indent, _number, title = head.groups()
        if title.rstrip().endswith(":"):
            failing.append(title.rstrip()[:-1])
            continue
        parts = [title.strip()] if title.strip() else []
        for follow in lines[index + 1:index + 5]:
            stripped = follow.strip()
            if not stripped or len(follow) - len(follow.lstrip()) <= len(indent):
                break
            parts.append(stripped.rstrip(":"))
            if stripped.endswith(":"):
                break
        else:
            parts = []
        if parts:
            failing.append(" ".join(parts))
    return passing, failing


# (extractor, may_union). `may_union` says the extractor spells an identity
# EXACTLY as the wheel spells it, so adding a name the wheel missed cannot
# mint a second identity for one test:
#
# * go   - both read `--- PASS: TestX`; the fallback differs only by allowing
#          the leading indentation a subtest carries. Union is required here,
#          not merely allowed: `go test -v` prints a column-0 `--- PASS:` for
#          the parent, so the wheel is never empty and a fill-only-if-empty
#          rule would lose every subtest.
# * cargo - both read `test a::b ... ok`; the `failures:` roll-call prints the
#          same `module::test` path the per-test rows do.
#
# jest / vitest / mocha are NOT unionable: the same test is `✕ name` on one
# line and `● Suite › name` or `FAIL file > suite > name` on another, so
# merging the two sources would record one test under two names. There the
# fallback only fills a side the wheel left empty.
_EXTRACTORS = {
    "cargo": (_extract_cargo, True),
    "go": (_extract_go, True),
    "jest": (_extract_node, False),
    "vitest": (_extract_node, False),
    "node": (_extract_node, False),
    "mocha": (_extract_mocha, False),
}


def parse_test_names(
    output: str | bytes,
    *,
    family: str = "",
    command: str | Sequence[str] = (),
) -> ParsedTestNames:
    """``(passing, failing, counts)`` for one run's captured bytes.

    ``family`` is the runner that produced the bytes, as
    ``CommandShape.family`` names it; when it is omitted it is read off
    ``command``. ``command`` is also what the wheel's count parser keys its
    per-runner summary patterns on, so pass it whenever it is known.

    Raises ``RuntimeError("canonical_name_extractor_unavailable")`` when the
    wheel is missing - the same contract the call sites had before, because a
    silent fallback to fallback-only names would mean the baseline and the
    observation were parsed by different code.
    """
    text = (
        output.decode("utf-8", errors="replace")
        if isinstance(output, (bytes, bytearray))
        else (output or "")
    )
    words = _words(command)
    try:
        from groundtruth.runtime.test_runner import (
            _parse_failing_test_names,
            _parse_passing_test_names,
            _parse_test_output,
        )
    except ImportError as exc:  # pragma: no cover - environment defect
        raise RuntimeError("canonical_name_extractor_unavailable") from exc
    try:
        counts = dict(_parse_test_output(text, words))
    except Exception:  # noqa: BLE001 - counts are correct-or-quiet
        counts = dict(_EMPTY_COUNTS)
    passing = _dedup(_parse_passing_test_names(text))
    failing = _dedup(_parse_failing_test_names(text))

    if not family and words:
        family = family_for_command(words)
    entry = _EXTRACTORS.get(family) if family not in _WHEEL_OWNED else None
    if entry is None:
        return ParsedTestNames(passing, failing, counts)
    extractor, may_union = entry
    # A wheel answer is never overridden, dropped or reordered. It is either
    # left alone, or (for the two families that spell an identity the same
    # way the wheel does) extended with names the wheel's column-0 anchors
    # could not reach.
    extra_passing, extra_failing = extractor(_ANSI_RE.sub("", text))
    passing = _merge(passing, extra_passing, may_union)
    failing = _merge(failing, extra_failing, may_union)
    return ParsedTestNames(passing, failing, counts)


def _merge(wheel: list[str], fallback, may_union: bool) -> list[str]:
    if not wheel:
        return _dedup(fallback)
    if not may_union:
        return wheel
    return _dedup([*wheel, *fallback])
