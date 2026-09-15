"""Snapshot-bound observation contracts for the Mini-SWE runtime seam.

Snapshot capture is independent of GroundTruth's installed wheel; test outcome
classification reuses its protocol classifier and abstains when unavailable.
This module records what the harness itself can observe: a repository snapshot at
an action boundary, one multi-file transaction for one selected action, and
the exact bytes returned by an executed build or test.  Semantic analyzers may
consume these records, but cannot weaken their byte identity.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import shlex
import sqlite3
import stat
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .repository_identity import (
    RepositoryHistory,
    canonical_repository_bytes,
    git_visible_paths,
    repository_history,
)

_SCHEMA = "gt.runtime_observation.v1"
_SKIP_DIRS = frozenset({
    ".git", ".gt", ".gt-state", ".groundtruth", ".hg", ".svn", ".venv", "venv",
    "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "build", "dist", "target", "vendor",
})
_MAX_CAPTURE_BYTES = 1_000_000
_ADDITIONAL_TEST_RE = re.compile(
    r"(?i)(?:^|[;&|]\s*)(?:nox|dotnet\s+test|mvnw\s+test|"
    r"bazel\s+test|meson\s+test)\b"
)
_BUILD_RE = re.compile(
    r"(?i)(?:^|[;&|]\s*)(?:npm\s+(?:run\s+)?build|pnpm\s+(?:run\s+)?build|"
    r"yarn\s+build|cargo\s+build|go\s+build|dotnet\s+build|"
    r"mvn(?:w)?\s+(?:package|compile)|gradle(?:w)?\s+(?:build|assemble)|"
    r"bazel\s+build|meson\s+compile|make(?:\s|$)|cmake\s+--build|"
    r"python\s+-m\s+build)\b"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@dataclass(frozen=True)
class FileState:
    path: str
    kind: str
    sha256: str
    size: int
    captured: bytes | None = None

    def mapping(self, *, include_content: bool = False) -> dict[str, object]:
        row: dict[str, object] = {
            "path": self.path,
            "kind": self.kind,
            "sha256": self.sha256,
            "size": self.size,
        }
        if include_content:
            row["content_hex"] = self.captured.hex() if self.captured is not None else None
        return row


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    revision: str
    files: tuple[FileState, ...]
    complete: bool
    omissions: tuple[str, ...]
    history: RepositoryHistory = RepositoryHistory()

    def canonical_bytes(self) -> bytes:
        return _canonical({
            "schema": _SCHEMA,
            "kind": "repository_snapshot",
            "root_sha256": hashlib.sha256(self.root.encode("utf-8")).hexdigest(),
            "revision": self.revision,
            "history": self.history.mapping(),
            "complete": self.complete,
            "omissions": list(self.omissions),
            "files": [item.mapping() for item in self.files],
        })


@dataclass(frozen=True)
class FileChange:
    path: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None
    before: bytes | None
    after: bytes | None

    def mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "operation": self.operation,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "before_content_hex": self.before.hex() if self.before is not None else None,
            "after_content_hex": self.after.hex() if self.after is not None else None,
        }


@dataclass(frozen=True)
class EditTransaction:
    action_id: int
    command_sha256: str
    pre_revision: str
    post_revision: str
    changes: tuple[FileChange, ...]
    complete: bool
    omissions: tuple[str, ...]
    transaction_sha256: str

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(change.path for change in self.changes)

    def canonical_bytes(self, *, include_transaction_hash: bool = True) -> bytes:
        row: dict[str, object] = {
            "schema": _SCHEMA,
            "kind": "edit_transaction",
            "action_id": self.action_id,
            "command_sha256": self.command_sha256,
            "pre_revision": self.pre_revision,
            "post_revision": self.post_revision,
            "complete": self.complete,
            "omissions": list(self.omissions),
            "changes": [change.mapping() for change in self.changes],
        }
        if include_transaction_hash:
            row["transaction_sha256"] = self.transaction_sha256
        return _canonical(row)


@dataclass(frozen=True)
class ExecutionEvidence:
    action_id: int
    kind: str
    protocol: str
    outcome: str
    command_sha256: str
    returncode: int | None
    repository_revision: str
    raw_output: bytes | None
    environment_sha256: str = ""
    timed_out: bool = False
    output_artifact_path: str = ""
    stored_output_sha256: str = ""
    stored_output_length: int = 0
    stored_output_encoding: str = "utf-8"
    observed_test_outcome: str = ""

    @property
    def raw_output_sha256(self) -> str:
        return (hashlib.sha256(self.raw_output).hexdigest() if self.raw_output is not None
                else self.stored_output_sha256)

    def canonical_bytes(self) -> bytes:
        return _canonical({
            "schema": _SCHEMA,
            "kind": self.kind,
            "protocol": self.protocol,
            "outcome": self.outcome,
            "observed_test_outcome": self.observed_test_outcome,
            "action_id": self.action_id,
            "command_sha256": self.command_sha256,
            "returncode": self.returncode,
            "repository_revision": self.repository_revision,
            "raw_output_sha256": self.raw_output_sha256,
            "raw_output_bytes": len(self.raw_output) if self.raw_output is not None else self.stored_output_length,
            "raw_preserved": True,
            "environment_sha256": self.environment_sha256,
            "timed_out": self.timed_out,
            "encoding": ("utf-8" if _is_utf8(self.raw_output) else "base64")
            if self.raw_output is not None else self.stored_output_encoding,
        })


def _is_utf8(payload: bytes) -> bool:
    try:
        payload.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _imperative(name: str, verdict: str, hint: str, basis: str) -> str:
    """One actionable sentence for one failing test name.

    H6: run 34996816912 shipped `baseline: 1 unverified-scope
    (tests/test_base.py::test_get_item)` and the agent kept chasing the test
    for another 1800 s. A count and a bucket label name the finding but never
    say what to DO with it; every string below is an instruction whose subject
    is the test and whose object is the agent's next action.
    """
    if hint == "stale_suite_pass":
        return (f"{name} passed in a full-suite run before your latest edit"
                " - re-run the full suite")
    if verdict == "regression_new":
        return (f"{name} passed before your edits and fails in the full suite"
                " - this one is yours")
    if verdict == "unverified_scope":
        if basis == "suite_observed":
            return (f"{name} failed here in a scoped run - not yet observed in"
                    " a full-suite run; run the full suite first")
        return (f"{name} passed at baseline; it failed here in a scoped run"
                " - run the full suite before investigating")
    if verdict == "scope_artifact":
        return (f"{name} passes in the full suite - this scoped failure is an"
                " isolation artifact, not a regression")
    if verdict == "baseline_noise":
        if hint == "pristine_probe":
            return (f"{name} already failed on the pristine tree (your own"
                    " git-stash probe) - not your change")
        return f"{name} already failed at baseline - not your change"
    if verdict == "suite_failing":
        return f"{name} fails in the full suite and was not in the baseline"
    return f"{name} is not in the baseline or any suite run"


# The clause rides in EVERY execution-evidence delivery, so it is bounded:
# 77 of smoke-20's 690 model tokens went to sha256s nobody could act on.
_CLAUSE_BUDGET = 200
_NAMES_PER_CLASS = 2
_VERDICT_ORDER = (
    "regression_new", "unverified_scope", "suite_failing",
    "scope_artifact", "baseline_noise", "untracked",
)


def _baseline_model_clause(classification: object) -> str:
    """Render the verdicts as instructions rather than as a labelled histogram.

    Accepts the dataclass or its ``as_dict`` form - the rehearsal auditor
    rebuilds the line from the stored journal payload, which carries the dict
    (now also carrying ``basis`` and ``hints``). Both sides must render through
    this one function or the delivered line and the audited line diverge.
    """
    if hasattr(classification, "verdicts"):
        verdicts = [(name, verdict) for name, verdict in classification.verdicts]
        basis = getattr(classification, "basis", "baseline")
        hints = dict(getattr(classification, "hints", {}) or {})
    else:
        verdicts = [
            (entry[0], entry[1])
            for entry in (classification.get("verdicts") or [])
        ]
        basis = str(classification.get("basis") or "baseline")
        hints = dict(classification.get("hints") or {})
    grouped: dict[str, list[str]] = {}
    for name, verdict in verdicts:
        grouped.setdefault(verdict, []).append(name)
    parts: list[str] = []
    dropped = 0
    used = 0
    for key in _VERDICT_ORDER:
        names = grouped.get(key) or []
        shown = names[:_NAMES_PER_CLASS]
        dropped += len(names) - len(shown)
        for name in shown:
            clause = _imperative(name, key, hints.get(name, ""), basis)
            if parts and used + len(clause) + 2 > _CLAUSE_BUDGET:
                dropped += 1
                continue
            parts.append(clause)
            used += len(clause) + 2
    if dropped:
        parts.append(f"+{dropped} more")
    return "; ".join(parts)


def execution_evidence_model_line(
    *,
    command: str,
    kind: str,
    outcome: str,
    returncode: int | None,
    observed_test_outcome: str = "",
    baseline_classification: object = None,
    submit_window: str = "",
) -> str:
    """The model-facing execution-evidence line - the only supported render.

    Shipping the canonical JSON gave the model sha256s it could not act on
    (77/690 consumed on smoke-20), so the delivery carries this prose line
    while the journal and blob keep the canonical artifact. Auditors rebuild
    the line from the stored payload rather than decoding JSON from the
    request; both sides must render through this one function.
    """
    outcome_word = {
        "pass": "passed", "fail": "failed", "timeout": "timed out",
        "interrupted": "interrupted", "env_fail": "could not run (environment)",
        "unknown": "result unclear",
    }.get(outcome, outcome)
    kind_word = {"test": "test run", "build": "build"}.get(kind, kind or "run")
    line = (
        f"{command or 'command'} — {kind_word} {outcome_word}"
        + (f" (exit {returncode})" if returncode is not None else "")
    )
    if observed_test_outcome:
        line += f"; tests: {observed_test_outcome}"
    if baseline_classification:
        clause = _baseline_model_clause(baseline_classification)
        if clause:
            line += f"; baseline: {clause}"
    if submit_window:
        line += f"; {submit_window}"
    return line


_BASELINE_CLASSIFICATION_LAYOUT = "gt.baseline_classification.v1"

# --- command shape ------------------------------------------------------
#
# D3: run 34996816912 issued ten test commands and the old parser read
# `unknown` for all ten, so nothing downstream ever ran. Every one of them was
# `cd /testbed && python -m pytest <paths> ... 2>&1 | tail -N`: the `cd`
# prefix, the `2>&1` word and the trailing pipe each defeated it on their own.

_REDIRECT_RE = re.compile(r"^(?:\d*|&)(?:>>?|<)")
_BARE_REDIRECT = frozenset({">", ">>", "<", "2>", "2>>", "&>", "&>>", ">&"})
# A trailing segment that only reshapes bytes already produced cannot change
# what the run covered, so it does not make the command compound.
_OUTPUT_FILTERS = frozenset({
    "tail", "head", "sed", "grep", "egrep", "fgrep", "rg", "ag", "tee", "cat",
    "wc", "less", "more", "sort", "uniq", "cut", "tr", "awk", "column",
    "strings", "nl", "fold", "rev", "expand",
})
# Wrappers that may precede the pytest token inside its own segment. Without
# this allowlist `grep pytest out.log` reads as a pytest invocation.
_RUNNER_WRAPPERS = frozenset({
    "python", "python2", "python3", "py", "uv", "uvx", "poetry", "pdm",
    "hatch", "pipenv", "timeout", "env", "nice", "stdbuf", "xvfb-run",
    "coverage", "nohup", "tox",
})
# Options that consume the following word. `-p no:cacheprovider` read that
# plugin name as a positional path and turned a whole-suite run into `scoped`.
_VALUE_OPTIONS = frozenset({
    "-p", "-c", "-o", "-W", "-n", "-k", "-m", "-P", "--rootdir", "--tb",
    "--maxfail", "--timeout", "--durations", "--junitxml", "--import-mode",
    "--basetemp", "--deselect", "--ignore", "--ignore-glob", "--override-ini",
    "--log-level", "--log-cli-level", "--color", "--capture", "--dist",
    "--numprocesses", "--confcutdir", "--assert",
})
# Options that narrow the run WITHIN its positional coverage: the run did not
# attempt everything it named, so it may fold names but never promote them.
_NARROWING_OPTIONS = frozenset({
    "-k", "-m", "--lf", "--ff", "--last-failed", "--failed-first", "--sw",
    "--stepwise",
})
# Options that subtract from coverage; their targets are recorded so a name
# inside an ignored file is never promoted by the run that skipped it.
_EXCLUDE_OPTIONS = frozenset({"--ignore", "--ignore-glob", "--deselect"})
_SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", "&"})


def _shell_words(command: str) -> list[str] | None:
    """Split a command into words, with `;` separated out.

    ``shlex`` does not treat `;` as a delimiter, so action 26's
    `... | tail -10; git stash pop` arrives as the word `-10;`.
    """
    try:
        words = shlex.split(command or "", posix=True)
    except ValueError:
        return None
    out: list[str] = []
    for word in words:
        if ";" in word:
            out.extend(part for part in re.split(r"(;)", word) if part)
        else:
            out.append(word)
    return out


def _shell_segments(words: list[str]) -> list[tuple[str, list[str]]]:
    """Split words into ``(preceding_operator, segment)`` pairs."""
    segments: list[tuple[str, list[str]]] = []
    current: list[str] = []
    operator = ""
    for word in words:
        if word in _SHELL_OPERATORS:
            if current:
                segments.append((operator, current))
            current = []
            operator = word
        else:
            current.append(word)
    if current:
        segments.append((operator, current))
    return segments


def _pytest_index(words: list[str]) -> int | None:
    """Index of the pytest token in a segment, or None.

    ``python -m pytest`` is the interpreter form: the `-m` sits BEFORE the
    token and must never be read as a marker expression.
    """
    for i, word in enumerate(words):
        base = word.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if base not in ("pytest", "py.test"):
            continue
        if i == 0 or words[i - 1] == "-m":
            return i
        first = words[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if first in _RUNNER_WRAPPERS or first.startswith("python"):
            return i
        return None
    return None


def _is_setup_prefix(words: list[str]) -> bool:
    """`cd <dir>` and `git stash` may precede the runner without hiding it."""
    return (len(words) == 2 and words[0] == "cd") or words == ["git", "stash"]


def _is_stash_pop(words: list[str]) -> bool:
    return words[:3] == ["git", "stash", "pop"]


def _is_output_filter(words: list[str]) -> bool:
    if not words:
        return False
    base = words[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base in _OUTPUT_FILTERS


@dataclass(frozen=True)
class CommandCoverage:
    """What one test command actually attempted.

    ``paths``    - positional path/nodeid arguments (the run's coverage)
    ``excluded`` - --ignore / --ignore-glob / --deselect targets, subtracted
    ``narrowed`` - -k / -m / a ::nodeid: the run skipped part of its own
                   coverage, so it may fold names but never promote them
    """

    scope: str
    paths: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    narrowed: bool = False


def _parse_pytest_segment(words: list[str]) -> CommandCoverage:
    index = _pytest_index(words)
    if index is None:
        return CommandCoverage("unknown")
    args = words[index + 1:]
    paths: list[str] = []
    excluded: list[str] = []
    narrowed = False
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in _BARE_REDIRECT:
            skip_next = True
            continue
        if _REDIRECT_RE.match(arg):
            continue
        if arg == "--":
            continue
        if arg.startswith("-") and arg != "-":
            option, equals, inline = arg.partition("=")
            if option in _EXCLUDE_OPTIONS:
                if equals:
                    excluded.append(inline)
                elif i + 1 < len(args):
                    excluded.append(args[i + 1])
                    skip_next = True
                continue
            if option in _NARROWING_OPTIONS:
                narrowed = True
            if option in _VALUE_OPTIONS and not equals:
                skip_next = True
            continue
        paths.append(arg)
    if any("::" in path for path in paths):
        narrowed = True
    scope = "scoped" if (paths or excluded or narrowed) else "suite"
    return CommandCoverage(scope, tuple(paths), tuple(excluded), narrowed)


def test_command_coverage(command: str) -> CommandCoverage:
    """Classify a pytest invocation's coverage shape and extent.

    ``suite``   - no positional selection; coverage is the whole collection
    ``scoped``  - positional path/nodeid args or filters narrow coverage
    ``unknown`` - anything else (compound, non-pytest, unparseable)

    Conservative direction: an ambiguous suite reads ``scoped`` and loses only
    a ledger-promotion opportunity; a scoped run must never read ``suite``.
    """
    words = _shell_words(command)
    if not words:
        return CommandCoverage("unknown")
    segments = _shell_segments(words)
    if not segments:
        return CommandCoverage("unknown")
    runner_at = None
    for i, (_operator, segment) in enumerate(segments):
        if _pytest_index(segment) is None:
            continue
        if runner_at is not None:
            return CommandCoverage("unknown")
        runner_at = i
    if runner_at is None:
        return CommandCoverage("unknown")
    for i, (operator, segment) in enumerate(segments):
        if i == runner_at:
            continue
        if i < runner_at:
            # Prefix segments must be setup joined by `&&`; anything else can
            # change what the runner sees.
            if not _is_setup_prefix(segment):
                return CommandCoverage("unknown")
            if segments[i + 1][0] != "&&":
                return CommandCoverage("unknown")
            continue
        if operator == "|" and _is_output_filter(segment):
            continue
        if operator in (";", "&&") and _is_stash_pop(segment):
            continue
        return CommandCoverage("unknown")
    return _parse_pytest_segment(segments[runner_at][1])


def test_command_scope(command: str) -> str:
    """The coverage shape alone - ``suite`` | ``scoped`` | ``unknown``."""
    return test_command_coverage(command).scope


def is_pristine_tree_probe(command: str) -> bool:
    """A `git stash` / run / `git stash pop` sandwich around a test command.

    Action 26 of run 34996816912, at +216 s:
    ``cd /testbed && git stash && python -m pytest
    tests/test_base.py::test_get_item -q 2>&1 | tail -10; git stash pop``
    - the test failed on the PRISTINE tree, which settled the question the
    agent then spent another 2643 s on. Deliberately narrow: both the stash
    and the pop must be present and the runner must sit between them, because
    a run whose stash never popped is not a probe, it is a lost workspace.
    """
    words = _shell_words(command)
    if not words:
        return False
    stash_at = pop_at = runner_at = None
    for i, (_operator, segment) in enumerate(_shell_segments(words)):
        if _is_stash_pop(segment):
            if pop_at is None:
                pop_at = i
        elif segment == ["git", "stash"]:
            if stash_at is None:
                stash_at = i
        elif _pytest_index(segment) is not None and runner_at is None:
            runner_at = i
    return (
        stash_at is not None and pop_at is not None and runner_at is not None
        and stash_at < runner_at < pop_at
    )


# --- run aggregates -----------------------------------------------------

# C1: a terminal summary line is the only affirmative evidence that a run
# finished. `| tail -20` of a verbose run shows PASSED rows with the summary
# cut off; without this marker a single parsed PASSED row promoted the entire
# baseline and opened the submit window.
_PYTEST_SUMMARY_RE = re.compile(
    r"(?m)^.*\b\d+\s+(?:passed|failed|errors?)\b.*\bin\s+[\d.]+\s*s"
)
_UNITTEST_SUMMARY_RE = re.compile(
    r"(?m)^Ran\s+\d+\s+tests?\b[\s\S]*?^(?:OK|FAILED)\b"
)


def has_terminal_summary(output: str) -> bool:
    """Whether the output carries a runner's own end-of-run aggregate."""
    text = output or ""
    return bool(
        _PYTEST_SUMMARY_RE.search(text) or _UNITTEST_SUMMARY_RE.search(text)
    )


def _name_covered(
    name: str,
    prefixes: tuple[str, ...],
    excluded: tuple[str, ...] = (),
) -> bool:
    """Whether a ``file::test`` name sits inside a run's declared coverage."""
    if excluded and _matches_any(name, excluded):
        return False
    if not prefixes:
        return True
    return _matches_any(name, prefixes)


def _matches_any(name: str, prefixes: tuple[str, ...]) -> bool:
    file_part = name.split("::", 1)[0]
    for prefix in prefixes:
        if "::" in prefix:
            if name == prefix:
                return True
        elif file_part == prefix or file_part.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


@dataclass
class SuiteVerdictLedger:
    """Current-tree suite truth joined against the task baseline.

    The baseline name sets are attribution only: a name passed at baseline is
    NOT suite-pass evidence under the agent's tree. Scoped runs never write
    suite FAILURES, because fixture ordering differs under isolation (run
    34996816912: test_get_item passed baseline and full suite, failed
    standalone) - they only fold names and, when green, promote inside their
    own coverage.

    ``basis`` records what the attribution rests on: ``baseline`` for a
    captured baseline, ``suite_observed`` when capture failed and the agent's
    own suite runs are the only evidence available (D2).
    """

    baseline_passing: tuple[str, ...] = ()
    baseline_failing: tuple[str, ...] = ()
    basis: str = "baseline"

    def __post_init__(self) -> None:
        self.baseline_passing = frozenset(self.baseline_passing)
        self.baseline_failing = frozenset(self.baseline_failing)
        self._suite: dict[str, str] = {}
        self._observed: set[str] = set()
        self._stale: set[str] = set()
        self._probe_passing: set[str] = set()
        self._probe_failing: set[str] = set()
        self._last_run_failing: frozenset[str] = frozenset()
        self._whole_suite_green = False

    def suite_verdict(self, name: str) -> str | None:
        return self._suite.get(name)

    def regression_count(self) -> int:
        """Baseline-passing names currently failing at suite scope."""
        return sum(
            1 for name, verdict in self._suite.items()
            if verdict == "fail" and self._passed_before(name)
        )

    def last_run_regressions(self) -> tuple[str, ...]:
        """Baseline-passing names failing in the MOST RECENT suite run.

        M9: a green run promotes every covered name to ``pass``, after which
        ``regression_count()`` can never be positive again. The latest run's
        own failing set is the reachable signal.
        """
        return tuple(sorted(
            name for name in self._last_run_failing if self._passed_before(name)
        ))

    def has_stale_verdicts(self) -> bool:
        return bool(self._stale)

    def has_whole_suite_green(self) -> bool:
        """Whether an unrestricted suite run has come back green since the
        last edit. The advisory may not describe a run that never happened."""
        return self._whole_suite_green

    def covers_known_suite(
        self, prefixes: Iterable[str], excluded: Iterable[str] = ()
    ) -> bool:
        """Whether a scoped run's positional paths cover the known suite.

        `pytest tests/` IS the full suite when every test file ever observed
        - baseline capture, pristine probe, or an earlier run - sits under a
        covered directory. Run 35016130850 ran exactly that on dynaconf and
        the scope classifier still read `scoped`, so no suite verdict was
        recorded and the submit-window advisory never fired. Directory
        prefixes only: a file or ::nodeid path is narrower than a suite by
        construction, exclusions (--ignore/--deselect) defeat completeness,
        and an empty known universe proves nothing about the suite's extent.
        """
        directories = tuple(
            p for p in prefixes if "::" not in p and not p.endswith(".py")
        )
        if not directories or tuple(excluded):
            return False
        known = (
            set(self._observed)
            | set(self.baseline_passing)
            | set(self.baseline_failing)
            | set(self._probe_passing)
            | set(self._probe_failing)
        )
        if not known:
            return False
        return all(_name_covered(name, directories) for name in known)

    def _passed_before(self, name: str) -> bool:
        return name in self.baseline_passing or name in self._probe_passing

    def note_failing(self, names: Iterable[str]) -> None:
        self._observed.update(names)

    def note_edit(self, names: Iterable[str] = ()) -> None:
        """M7: an edit invalidates every suite verdict taken before it.

        A green full-suite run says nothing about the tree the agent has since
        changed, and the advisory must not keep quoting it. ``names`` is
        accepted for symmetry with the adapter's per-path callers; staleness is
        whole-ledger because a suite run's verdicts are jointly observed.
        """
        del names
        self._stale = set(self._suite)
        self._whole_suite_green = False

    def note_baseline_probe(
        self, *, failing: Iterable[str] = (), passing: Iterable[str] = ()
    ) -> None:
        """Record the agent's own pristine-tree probe as baseline evidence.

        Action 26 of run 34996816912 stashed the edits, ran the test and
        popped: the test failed WITHOUT the agent's changes. That is baseline
        attribution the captured baseline never supplied, and it arrived
        2643 s before the agent stopped chasing the test.
        """
        self._probe_failing.update(failing)
        self._probe_passing.update(passing)

    def record_suite_run(
        self,
        *,
        passing: Iterable[str] = (),
        failing: Iterable[str] = (),
        observed_names: Iterable[str] = (),
        failed_count: int | None = None,
        passed_count: int | None = None,
        covered_prefixes: tuple[str, ...] = (),
        excluded_prefixes: tuple[str, ...] = (),
        suite_scope: bool = True,
    ) -> None:
        """Fold one run into suite truth; latest run wins.

        Promotion rule (M8/M9), in order:

        * a run that both passed and failed one identity established nothing -
          skip promotion entirely (H4);
        * a run whose summary count equals the number of names it printed
          observed exactly those names: promote them and nothing more;
        * otherwise the run printed dots (``-q`` yields no names at all -
          action 45 reported 375 passed and zero names), so promote the names
          already OBSERVED in this task, restricted to this run's coverage;
        * the BASELINE sets ride along only for a whole-suite run whose own
          count conserves them (``passed >= len(baseline_passing)``), the
          guard analogous to persistent_plan/baseline.py:718.

        ``suite_scope`` separates the two directions that are not symmetric: a
        whole-suite run's failures are suite truth, a scoped run's failures are
        not (that asymmetry IS the incident), while a green scoped run still
        promotes inside its own coverage.
        """
        passing = tuple(passing)
        failing = tuple(failing)
        prefixes = tuple(covered_prefixes)
        excluded = tuple(excluded_prefixes)
        # H4: the unittest FAIL pattern captures the CLASS, so one identity can
        # land in both parsed lists. Apply passing first and let fail win.
        conflicted = set(passing) & set(failing)
        promoting = failed_count == 0 and not conflicted
        for name in passing:
            if suite_scope or (promoting and _name_covered(name, prefixes, excluded)):
                self._write(name, "pass")
        if suite_scope:
            for name in failing:
                self._write(name, "fail")
            self._last_run_failing = frozenset(failing)
        self._observed.update(observed_names)
        if not promoting:
            return
        named_all = (
            bool(passing) and passed_count is not None
            and passed_count == len(passing)
        )
        if named_all:
            return
        # "Unrestricted" covers two shapes: the bare `pytest` run and a
        # scoped-looking command whose directory args cover every known test
        # (`pytest tests/` on a tests/-rooted layout).
        unrestricted = not prefixes or self.covers_known_suite(prefixes, excluded)
        candidates = set(self._observed)
        if (
            unrestricted
            and passed_count is not None
            and passed_count >= len(self.baseline_passing)
        ):
            candidates |= set(self.baseline_passing) | set(self.baseline_failing)
        for name in candidates:
            if _name_covered(name, prefixes, excluded):
                self._write(name, "pass")
        if unrestricted and not excluded and suite_scope:
            # A green whole-suite run supersedes every verdict taken before the
            # last edit, including the ones it did not name.
            self._stale.clear()
            self._whole_suite_green = True

    def _write(self, name: str, verdict: str) -> None:
        self._suite[name] = verdict
        self._stale.discard(name)

    def _verdict_for(self, name: str) -> str:
        suite = self._suite.get(name)
        if suite == "fail":
            if self._passed_before(name):
                return "regression_new"
            if name in self.baseline_failing or name in self._probe_failing:
                return "baseline_noise"
            return "suite_failing"
        if suite == "pass":
            # M7: a pass observed before the latest edit is not evidence about
            # the tree the agent just changed. A stale FAIL is left alone -
            # keeping a warning is the conservative direction.
            return "unverified_scope" if name in self._stale else "scope_artifact"
        if name in self.baseline_failing:
            return "baseline_noise"
        if name in self.baseline_passing:
            return "unverified_scope"
        # The captured baseline outranks the probe; the probe only governs
        # where the baseline has no opinion (which, on a `no_test_verdicts`
        # capture, is everywhere).
        if name in self._probe_failing:
            return "baseline_noise"
        if name in self._probe_passing:
            return "unverified_scope"
        return "untracked"

    def _hint_for(self, name: str, verdict: str) -> str:
        """Which wording variant the clause should use for this name."""
        if (
            verdict == "unverified_scope"
            and name in self._stale
            and self._suite.get(name) == "pass"
        ):
            return "stale_suite_pass"
        if (
            verdict == "baseline_noise"
            and name in self._probe_failing
            and name not in self.baseline_failing
        ):
            return "pristine_probe"
        return ""


@dataclass(frozen=True)
class BaselineClassification:
    scope: str
    verdicts: tuple[tuple[str, str], ...]
    summary: dict[str, int]
    basis: str = "baseline"
    hints: dict[str, str] = field(default_factory=dict)
    layout_schema: str = _BASELINE_CLASSIFICATION_LAYOUT

    def as_dict(self) -> dict:
        return {
            "layout_schema": self.layout_schema,
            "scope": self.scope,
            "basis": self.basis,
            "verdicts": [[name, verdict] for name, verdict in self.verdicts],
            "summary": dict(sorted(self.summary.items())),
            "hints": dict(sorted(self.hints.items())),
        }


def classify_failures_vs_baseline(
    *,
    command: str,
    output: str | bytes,
    ledger: SuiteVerdictLedger | None,
) -> BaselineClassification | None:
    """Classify observed failing test names against baseline + suite truth.

    Returns ``None`` when there is no ledger at all (no plan inputs) or the
    output parses to no failing names - absent evidence, never an error. An
    uncaptured baseline is NOT a reason to return None: the ledger then runs on
    a ``suite_observed`` basis (D2). The canonical producer's extractors are
    the only supported name source; both baseline and observation sides must
    parse identically.
    """
    if ledger is None:
        return None
    text = (
        output.decode("utf-8", errors="replace")
        if isinstance(output, (bytes, bytearray))
        else output
    )
    try:
        from groundtruth.runtime.test_runner import _parse_failing_test_names
    except ImportError as exc:
        raise RuntimeError("canonical_name_extractor_unavailable") from exc
    failing = _parse_failing_test_names(text or "")
    if not failing:
        return None
    ledger.note_failing(failing)
    verdicts = tuple((name, ledger._verdict_for(name)) for name in failing)
    summary: dict[str, int] = {}
    hints: dict[str, str] = {}
    for name, verdict in verdicts:
        summary[verdict] = summary.get(verdict, 0) + 1
        hint = ledger._hint_for(name, verdict)
        if hint:
            hints[name] = hint
    return BaselineClassification(
        scope=test_command_scope(command),
        verdicts=verdicts,
        summary=summary,
        basis=ledger.basis,
        hints=hints,
    )


def capture_workspace(
    root: str | Path, *, excluded_roots: tuple[str | Path, ...] = ()
) -> WorkspaceSnapshot:
    """Capture a deterministic, content-addressed repository snapshot.

    Every readable file contributes its complete hash. Files up to one MiB are
    retained as transaction witnesses; larger files remain hash-addressed and
    make a before/after transaction incomplete rather than silently truncated.
    """
    resolved = Path(root).resolve()
    history = repository_history(resolved)
    excluded = tuple(Path(path).resolve() for path in excluded_roots)
    files: list[FileState] = []
    omissions: list[str] = []
    if not resolved.is_dir():
        omissions.append("repository_root_missing")
    else:
        paths = git_visible_paths(resolved)
        if paths is None:
            candidates: list[Path] = []
            for dirpath, dirnames, filenames in os.walk(
                resolved, followlinks=False
            ):
                base = Path(dirpath)
                dirnames[:] = sorted(
                    name for name in dirnames
                    if name not in _SKIP_DIRS
                    and not any(
                        (base / name).resolve() == target
                        or target in (base / name).resolve().parents
                        for target in excluded
                    )
                )
                candidates.extend(base / name for name in sorted(filenames))
            paths = tuple(candidates)

        for path in paths:
            if any(path.resolve() == target or target in path.resolve().parents
                   for target in excluded):
                continue
            try:
                relative = path.relative_to(resolved).as_posix()
                if path.is_symlink():
                    payload = os.readlink(path).encode("utf-8", "surrogatepass")
                    kind = "symlink"
                elif path.is_dir():
                    # git ls-files lists submodule entries (mode 160000) as
                    # paths, but they are directories: read_bytes raises and
                    # the whole snapshot goes incomplete forever on an entry
                    # that has a perfectly good typed identity -- the gitlink's
                    # pinned commit. Read it from the index that enumerated the
                    # path, not HEAD, so unborn-HEAD and staged states work.
                    staged = subprocess.run(
                        ["git", "-C", str(resolved), "ls-files", "-s", "--",
                         relative],
                        check=True, capture_output=True, timeout=8,
                    ).stdout.decode("utf-8", "surrogateescape").split()
                    if len(staged) < 2 or staged[0] != "160000":
                        raise OSError(f"not_a_gitlink:{relative}")
                    payload = staged[1].encode("ascii")
                    kind = "gitlink"
                elif not path.is_file():
                    # Fifos, sockets and device nodes have no readable bytes;
                    # their identity is their file type.
                    payload = f"mode:{stat.S_IFMT(path.stat().st_mode):o}".encode()
                    kind = "special"
                else:
                    payload = path.read_bytes()
                    kind = "file"
                identity_payload = (
                    canonical_repository_bytes(payload)
                    if kind == "file"
                    else payload
                )
                files.append(FileState(
                    path=relative,
                    kind=kind,
                    sha256=hashlib.sha256(identity_payload).hexdigest(),
                    size=len(identity_payload),
                    captured=payload if len(payload) <= _MAX_CAPTURE_BYTES else None,
                ))
            except (OSError, subprocess.SubprocessError):
                # The relative path (not just the name) lets downstream
                # producer-input checks apply the same dir-pruning the
                # indexer's walk does; a basename hides the tree it sat in.
                omissions.append(f"unreadable:{relative}")
    if repository_history(resolved) != history:
        omissions.append("history_changed_during_snapshot")
    identity = _canonical({"files": [item.mapping() for item in files],
                           "history": history.mapping()})
    revision = hashlib.sha256(b"gt.workspace.v2\0" + identity).hexdigest()
    return WorkspaceSnapshot(
        root=str(resolved),
        revision=revision,
        files=tuple(files),
        complete=not omissions,
        omissions=tuple(sorted(omissions)),
        history=history,
    )


def diff_workspace(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
    *,
    action_id: int,
    command: str,
) -> EditTransaction:
    """Compile all changes caused within one selected action into one record."""
    old = {item.path: item for item in before.files}
    new = {item.path: item for item in after.files}
    changes: list[FileChange] = []
    omissions = [*before.omissions, *after.omissions]
    for path in sorted(set(old) | set(new)):
        left, right = old.get(path), new.get(path)
        if left is not None and right is not None and left.sha256 == right.sha256:
            continue
        operation = "create" if left is None else "delete" if right is None else "modify"
        if left is not None and left.captured is None:
            omissions.append(f"before_content_too_large:{path}")
        if right is not None and right.captured is None:
            omissions.append(f"after_content_too_large:{path}")
        changes.append(FileChange(
            path=path,
            operation=operation,
            before_sha256=left.sha256 if left else None,
            after_sha256=right.sha256 if right else None,
            before=left.captured if left else None,
            after=right.captured if right else None,
        ))
    base = EditTransaction(
        action_id=action_id,
        command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest(),
        pre_revision=before.revision,
        post_revision=after.revision,
        changes=tuple(changes),
        complete=before.complete and after.complete and not omissions,
        omissions=tuple(sorted(set(omissions))),
        transaction_sha256="",
    )
    digest = hashlib.sha256(base.canonical_bytes(include_transaction_hash=False)).hexdigest()
    return EditTransaction(**{**base.__dict__, "transaction_sha256": digest})


def _protocol(command: str) -> str:
    lowered = command.lower()
    protocols = (
        "pytest", "unittest", "tox", "nox", "npm", "pnpm", "yarn", "cargo", "go",
        "dotnet", "mvn", "gradle", "ctest", "rspec", "phpunit", "make",
        "cmake",
    )
    for name in protocols:
        if re.search(rf"(?:^|[\s;&|]){re.escape(name)}(?:[\s;&|]|$)", lowered):
            return name
    return "unknown"


def compile_execution_evidence(
    *,
    command: str,
    output: str,
    returncode: int | None,
    action_id: int,
    repository_revision: str,
    raw_output: bytes | None = None,
    timed_out: bool = False,
    environment_sha256: str = "",
    output_artifact_path: str = "",
    output_artifact: dict | None = None,
) -> ExecutionEvidence | None:
    test_outcome, test_protocol = _classify_test_output(
        command, output, returncode, output_artifact=output_artifact
    )
    kind = (
        "test" if test_protocol or _ADDITIONAL_TEST_RE.search(command)
        else "build" if _BUILD_RE.search(command) else ""
    )
    if not kind:
        return None
    outcome = "timeout" if timed_out else (
        _execution_outcome_guard(command, returncode, kind=kind) or test_outcome or "unknown"
    )
    return ExecutionEvidence(
        action_id=action_id,
        kind=kind,
        protocol="native" if test_protocol == "native" else _protocol(command),
        outcome=outcome,
        command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest(),
        returncode=returncode,
        repository_revision=repository_revision,
        raw_output=(None if output_artifact is not None else
                    raw_output if raw_output is not None else output.encode("utf-8")),
        environment_sha256=environment_sha256,
        timed_out=timed_out,
        output_artifact_path=output_artifact_path,
        stored_output_sha256=str((output_artifact or {}).get("sha256") or ""),
        stored_output_length=int((output_artifact or {}).get("total_length") or 0),
        stored_output_encoding=str((output_artifact or {}).get("encoding") or "utf-8"),
        observed_test_outcome=test_outcome,
    )


def program_execution_evidence(
    *,
    command: str,
    output: str,
    returncode: int | None,
    action_id: int,
    repository_revision: str,
    timed_out: bool = False,
    environment_sha256: str = "",
) -> ExecutionEvidence:
    """Evidence for a bound behavioral-program check.

    ``compile_execution_evidence`` returns None for commands that are neither
    test nor build kind - which is exactly what a program check is (``test
    "$(fd --sort)" = "a"``). The exit status is the assertion contract, so the
    outcome derives from it directly under the same timeout/interruption
    guards the test path applies.
    """
    if timed_out:
        outcome = "timeout"
    elif returncode is None:
        outcome = "unknown"
    elif returncode < 0:
        outcome = "interrupted"
    elif returncode == 0:
        outcome = "pass"
    else:
        outcome = "fail"
    return ExecutionEvidence(
        action_id=action_id,
        kind="program",
        protocol="shell",
        outcome=outcome,
        command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest(),
        returncode=returncode,
        repository_revision=repository_revision,
        raw_output=str(output or "").encode("utf-8"),
        environment_sha256=environment_sha256,
        timed_out=bool(timed_out),
    )


def _execution_outcome_guard(command: str, returncode: int | None, *, kind: str) -> str:
    if returncode is None:
        return "unknown"
    if returncode < 0:
        return "interrupted"
    if returncode == 124:
        return "timeout"
    if re.search(r"[;&|`\n]|\$\(", command):
        return "unknown"
    if kind == "build":
        return "pass" if returncode == 0 else "fail"
    return ""


def _classify_test_output(
    command: str,
    output: str,
    returncode: int | None,
    *,
    output_artifact: dict | None = None,
) -> tuple[str, str]:
    try:
        if output_artifact is None:
            from groundtruth.runtime.patterns import classify_test_observation

            return classify_test_observation(command, output, returncode)

        from groundtruth.runtime.patterns import classify_test_observation_stream

        from .output_evidence import EvidenceStore

        if output_artifact.get("schema") != "gt.output_artifact.v1":
            raise ValueError("invalid output artifact schema")
        root = output_artifact.get("root")
        digest = output_artifact.get("sha256")
        length = output_artifact.get("total_length")
        encoding = output_artifact.get("encoding")
        if not isinstance(root, str) or not root:
            raise ValueError("invalid output artifact root")
        if not isinstance(digest, str):
            raise ValueError("invalid output artifact digest")
        if type(length) is not int or length < 0:
            raise ValueError("invalid output artifact length")
        if encoding not in {"utf-8", "base64"}:
            raise ValueError("invalid output artifact encoding")
        chunks = EvidenceStore(root).iter_bytes(
            digest,
            expected_length=length,
            expected_encoding=encoding,
        )
        result = classify_test_observation_stream(
            command, chunks, returncode, encoding="utf-8", errors="replace"
        )
        # The canonical implementation consumes every chunk, but keep the
        # trust boundary explicit: no derived result leaves this function until
        # the artifact iterator has reached and validated its tail.
        for _ in chunks:
            pass
        return result
    except ImportError as exc:
        if output_artifact is not None:
            raise RuntimeError("canonical_streaming_classifier_unavailable") from exc
        # This branch fires ONLY when the certified producer's classifier
        # cannot be imported. A genuinely non-test command never reaches it -
        # it flows through the working classifier's ordinary return - so
        # ("", "") here does not mean "not a test", it means "nothing
        # classified it". Returning it degraded KIND as well as outcome, and
        # a missing kind makes compile_execution_evidence return None: not an
        # unknown outcome, which is a recorded fact, but no evidence at all.
        # A run against a stale producer then reports an agent that ran no
        # tests. Refuse instead, so the pinned-container requirement is
        # enforced by construction rather than by convention.
        raise RuntimeError("canonical_classifier_unavailable") from exc


def classify_execution_outcome(
    command: str,
    output: str,
    returncode: int | None,
    *,
    kind: str = "test",
    output_artifact: dict | None = None,
) -> str:
    """Conservative outcome shared by context and verification consumers.

    A shell's aggregate exit cannot attribute a pipeline/compound result to one
    check. Do not rewrite commands or guess segment status from their output.
    Protocol parsing uses the certified producer; missing parsing means unknown.
    """
    if output_artifact is not None:
        outcome, _ = _classify_test_output(
            command, output, returncode, output_artifact=output_artifact
        )
        guarded = _execution_outcome_guard(command, returncode, kind=kind)
        return guarded or outcome or "unknown"
    guarded = _execution_outcome_guard(command, returncode, kind=kind)
    if guarded:
        return guarded
    try:
        outcome, _ = _classify_test_output(command, output, returncode)
    except RuntimeError:
        # The docstring's conservatism is about OUTCOME, and it still holds
        # here: an unparsed outcome is "unknown", a recorded fact a context
        # consumer can act on. Evidence compilation is the caller that must
        # not degrade, and it does not - it lets the error through.
        return "unknown"
    return outcome or "unknown"


def compile_transaction_artifacts(
    transaction: EditTransaction,
    *,
    graph_db: str | Path | None = None,
) -> dict[str, object]:
    """Attach deterministic syntax, patch, and pre-edit caller facts.

    Caller rows are explicitly graph-recorded, not claimed complete. If the
    graph is unavailable or stale the caller artifact is omitted rather than
    approximated from text.
    """
    def python_signatures(data: bytes | None, path: str) -> dict[str, str] | None:
        if data is None:
            return {}
        try:
            tree = ast.parse(data.decode("utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            return None
        found: dict[str, str] = {}

        class SignatureVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.scope: list[str] = []

            def _visit_function(
                self, node: ast.FunctionDef | ast.AsyncFunctionDef
            ) -> None:
                qualified = ".".join((*self.scope, node.name))
                signature = "|".join((
                    type(node).__name__,
                    ast.dump(node.args, annotate_fields=True, include_attributes=False),
                    ast.dump(
                        node.returns,
                        annotate_fields=True,
                        include_attributes=False,
                    ) if node.returns is not None else "",
                    str(node.type_comment or ""),
                ))
                found[qualified] = signature
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._visit_function(node)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

        SignatureVisitor().visit(tree)
        return found

    patches: list[dict[str, object]] = []
    syntax: list[dict[str, object]] = []
    signatures: list[dict[str, object]] = []
    parser_rows: dict[str, dict] = {}
    try:
        from .parser_inspection import ParserInspectionRequest, inspect_sources

        inspection_requests = []
        for change in transaction.changes:
            for side, content in (("before", change.before), ("after", change.after)):
                if content is not None and Path(change.path).suffix.lower() in {
                    ".py", ".pyi", ".go", ".ts", ".tsx", ".js", ".jsx", ".rs",
                }:
                    inspection_requests.append(ParserInspectionRequest(
                        f"{transaction.transaction_sha256}:{side}:{change.path}",
                        change.path, content,
                    ))
        parser_rows = {
            str(row["request_id"]): row for row in inspect_sources(inspection_requests)
        }
    except (OSError, RuntimeError, subprocess.SubprocessError):
        parser_rows = {}
    for change in transaction.changes:
        before_text = (
            change.before.decode("utf-8", "replace").splitlines(keepends=True)
            if change.before is not None else []
        )
        after_text = (
            change.after.decode("utf-8", "replace").splitlines(keepends=True)
            if change.after is not None else []
        )
        text_roundtrip = (
            (change.before is None or "".join(before_text).encode("utf-8") == change.before)
            and (change.after is None or "".join(after_text).encode("utf-8") == change.after)
        )
        patch = (
            "".join(difflib.unified_diff(
                before_text, after_text,
                fromfile=f"a/{change.path}", tofile=f"b/{change.path}",
            ))
            if text_roundtrip else ""
        )
        patches.append({
            "path": change.path,
            "operation": change.operation,
            "representation": "unified_diff_utf8" if text_roundtrip else "full_postimage",
            "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            "patch": patch,
            "before_sha256": change.before_sha256,
            "after_sha256": change.after_sha256,
            # Exact reconstruction authority for text, binary, create, and delete.
            "postimage_hex": change.after.hex() if change.after is not None else None,
            "truncated": False,
        })
        if change.after is None:
            syntax.append({
                "path": change.path,
                "language": "unknown",
                "status": "not_applicable_deleted",
                "post_revision": transaction.post_revision,
            })
        else:
            after_key = f"{transaction.transaction_sha256}:after:{change.path}"
            parsed_after = parser_rows.get(after_key)
            if parsed_after is not None:
                syntax.append({
                    "path": change.path,
                    "language": str(parsed_after.get("language") or "unknown"),
                    "status": "exact" if parsed_after.get("complete") else "incomplete",
                    "valid": bool(parsed_after.get("complete")),
                    "diagnostics": list(parsed_after.get("diagnostics") or ()),
                    "post_revision": transaction.post_revision,
                    "producer": str(parsed_after.get("parser_identity") or ""),
                    "content_sha256": str(parsed_after.get("content_sha256") or ""),
                })
            elif change.path.endswith((".py", ".pyi")):
                try:
                    ast.parse(change.after.decode("utf-8"), filename=change.path)
                    syntax.append({
                        "path": change.path,
                        "language": "python",
                        "status": "exact",
                        "valid": True,
                        "post_revision": transaction.post_revision,
                        "producer": "python.ast.parse",
                    })
                except (SyntaxError, UnicodeDecodeError) as exc:
                    syntax.append({
                        "path": change.path,
                        "language": "python",
                        "status": "exact",
                        "valid": False,
                        "line": int(getattr(exc, "lineno", 0) or 0),
                        "column": int(getattr(exc, "offset", 0) or 0),
                        "error": type(exc).__name__,
                        "post_revision": transaction.post_revision,
                        "producer": "python.ast.parse",
                    })
            else:
                syntax.append({
                    "path": change.path,
                    "language": "unknown",
                    "status": "unsupported",
                    "reason": "no_harness_certified_postimage_parser",
                    "post_revision": transaction.post_revision,
                })
        before_key = f"{transaction.transaction_sha256}:before:{change.path}"
        after_key = f"{transaction.transaction_sha256}:after:{change.path}"
        parsed_before, parsed_after = parser_rows.get(before_key), parser_rows.get(after_key)
        parser_side_available = (
            (parsed_before is not None or change.before is None)
            and (parsed_after is not None or change.after is None)
            and (parsed_before is not None or parsed_after is not None)
        )
        if parser_side_available:
            parser_complete = (
                (parsed_before is None or bool(parsed_before.get("complete")))
                and (parsed_after is None or bool(parsed_after.get("complete")))
            )
            if not parser_complete:
                signatures.append({"path": change.path, "status": "unavailable_invalid_syntax",
                                   "post_revision": transaction.post_revision})
            else:
                def signature_map(row: dict | None) -> tuple[dict[str, str], bool]:
                    found: dict[str, str] = {}
                    ambiguous = False
                    for item in (row or {}).get("declarations") or ():
                        name = str(
                            item.get("qualified_name") or item.get("name") or ""
                        )
                        signature = str(item.get("signature") or "")
                        if not name or name in found:
                            ambiguous = True
                        else:
                            found[name] = signature
                    return found, ambiguous

                before_signatures, before_ambiguous = signature_map(parsed_before)
                after_signatures, after_ambiguous = signature_map(parsed_after)
                if before_ambiguous or after_ambiguous:
                    signatures.append({
                        "path": change.path,
                        "status": "unavailable_ambiguous_declaration_identity",
                        "post_revision": transaction.post_revision,
                    })
                    continue
                before_names, after_names = set(before_signatures), set(after_signatures)
                signatures.append({
                    "path": change.path, "status": "exact",
                    "added": sorted(after_names - before_names),
                    "removed": sorted(before_names - after_names),
                    "changed": sorted(name for name in before_names & after_names
                                      if before_signatures[name] != after_signatures[name]),
                    "post_revision": transaction.post_revision,
                    "producer": str(
                        (parsed_after or parsed_before or {}).get("parser_identity") or ""
                    ),
                })
        elif change.path.endswith((".py", ".pyi")):
            before_signatures = python_signatures(change.before, change.path)
            after_signatures = python_signatures(change.after, change.path)
            if before_signatures is None or after_signatures is None:
                signatures.append({
                    "path": change.path,
                    "status": "unavailable_invalid_syntax",
                    "post_revision": transaction.post_revision,
                })
            else:
                before_names = set(before_signatures)
                after_names = set(after_signatures)
                signatures.append({
                    "path": change.path,
                    "status": "exact",
                    "added": sorted(after_names - before_names),
                    "removed": sorted(before_names - after_names),
                    "changed": sorted(
                        name for name in before_names & after_names
                        if before_signatures[name] != after_signatures[name]
                    ),
                    "post_revision": transaction.post_revision,
                    "producer": "python.ast",
                })
        else:
            signatures.append({
                "path": change.path,
                "status": "unsupported",
                "reason": "no_harness_certified_signature_extractor",
                "post_revision": transaction.post_revision,
            })
    callers: list[dict[str, object]] = []
    graph = Path(graph_db) if graph_db else None
    if graph is not None and graph.is_file():
        try:
            paths = transaction.changed_paths
            placeholders = ",".join("?" for _ in paths)
            query = (
                "SELECT DISTINCT src.name,src.file_path,e.source_line,tgt.name,"
                "tgt.file_path FROM edges e "
                "JOIN nodes src ON src.id=e.source_id "
                "JOIN nodes tgt ON tgt.id=e.target_id "
                f"WHERE e.type='CALLS' AND tgt.file_path IN ({placeholders}) "
                "ORDER BY src.file_path,e.source_line,src.name LIMIT 200"
            )
            connection = sqlite3.connect(
                f"file:{graph.resolve().as_posix()}?mode=ro", uri=True
            )
            try:
                rows = connection.execute(query, paths).fetchall()
            finally:
                connection.close()
            callers = [{
                "caller": str(row[0] or ""),
                "caller_path": str(row[1] or ""),
                "caller_line": int(row[2] or 0),
                "target": str(row[3] or ""),
                "target_path": str(row[4] or ""),
                "semantics": "graph_recorded",
            } for row in rows]
        except sqlite3.Error:
            callers = []
    return {
        "schema": "gt.transaction_artifacts.v1",
        "transaction_sha256": transaction.transaction_sha256,
        "pre_revision": transaction.pre_revision,
        "post_revision": transaction.post_revision,
        "syntax": syntax,
        "signatures": signatures,
        "patches": patches,
        "callers": callers,
        "caller_coverage": "graph_recorded" if graph is not None else "unavailable",
    }


def certify_observation_equivalence(
    *, raw_output: bytes, final_observation: bytes, expected_observation: bytes,
    sentinel: bytes,
) -> dict[str, object]:
    """Certify narrow byte equivalence and prove a raw sentinel did not leak."""
    equivalent = final_observation == expected_observation
    sentinel_absent = bool(sentinel) and sentinel not in final_observation
    return {
        "schema": "gt.observation_equivalence.v1",
        "raw_output_sha256": hashlib.sha256(raw_output).hexdigest(),
        "final_observation_sha256": hashlib.sha256(final_observation).hexdigest(),
        "expected_observation_sha256": hashlib.sha256(expected_observation).hexdigest(),
        "byte_equivalent": equivalent,
        "sentinel_absent": sentinel_absent,
        "replacement_certified": equivalent and sentinel_absent,
    }


__all__ = [
    "EditTransaction", "ExecutionEvidence", "FileChange", "FileState",
    "WorkspaceSnapshot", "capture_workspace", "compile_execution_evidence",
    "certify_observation_equivalence", "compile_transaction_artifacts",
    "diff_workspace",
]
