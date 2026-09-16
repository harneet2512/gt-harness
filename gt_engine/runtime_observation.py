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
from dataclasses import dataclass, field, replace
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
#
# E1: measured over the 790 recorded trajectories in this harness's own
# command corpus (tests/fixtures/command_corpus/model_test_commands.json -
# 1282 recorded test commands, 964 once heredoc bodies are excluded), the
# single-family successor to D3 still read `unknown` for 622 of them: 169 of
# 511 pytest commands (33.1%) and 100% of the 453 non-pytest ones -
# jest/vitest/mocha 157, cargo 116, npm/pnpm/yarn 85, go 78, make/tox 16,
# unittest 1 - because it recognised no runner but pytest. Downstream,
# `_classify_execution_vs_baseline` classifies only when the scope is known,
# so the classifier, the suite ledger and the submit-window advisory were
# inert on every Rust/Go/JS/TS task (16 of the 20 DeepSWE smoke tasks) and on
# a third of the Python ones. The pytest misses were not exotic: `&&` chains
# with benign preludes, `;` chains, `| grep -E "passed|failed"` (grep IS an
# output filter), `timeout 180 python -m pytest`, `FORCE_COLOR=1` env
# prefixes, `PY=... && $PY -m pytest`, `git worktree add ... && cd ... &&
# pytest`, and `> /tmp/log 2>&1; tail -6 /tmp/log`.
#
# The parser below is therefore two layers:
#   1. a shell-segment tokenizer that splits on `&&`, `||`, `;`, `|` and
#      newline while treating quotes, `$(...)`, `${...}` and backticks as
#      opaque - ``go test `go list ./... | grep -v /js` `` is ONE segment;
#   2. a per-family runner reader. A command is a runner command when exactly
#      ONE segment invokes a runner and every other segment is benign. Two
#      runner segments are ambiguous (which one produced these bytes?) and a
#      non-benign segment can change what the runner sees, so both read
#      `unknown`.
# The docstring's conservatism is unchanged: an ambiguous suite may read
# `scoped` and lose only a ledger-promotion opportunity; a narrowed run must
# NEVER read `suite`.

_REDIRECT_RE = re.compile(r"^(?:\d*|&)(?:>>?|<)")
_BARE_REDIRECT = frozenset({">", ">>", "<", "2>", "2>>", "&>", "&>>", ">&"})
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A heredoc body is arbitrary text - `cat > tests/new_test.py <<EOF` writes a
# whole test file - so nothing about the run can be read from the argv alone.
# 318 of the corpus's 1282 recorded test commands carry one.
_HEREDOC_RE = re.compile(r"<<-?\s*[\"'\\]?\w")
# A path that is really a glob or a command substitution: its extent is not
# knowable here, so it narrows the run rather than describing its coverage.
_OPAQUE_PATH_RE = re.compile(r"[*?`]|\$\(|\$\{")
# A trailing segment that only reshapes bytes already produced cannot change
# what the run covered, so it does not make the command compound.
_OUTPUT_FILTERS = frozenset({
    "tail", "head", "sed", "grep", "egrep", "fgrep", "rg", "ag", "tee", "cat",
    "wc", "less", "more", "sort", "uniq", "cut", "tr", "awk", "column",
    "strings", "nl", "fold", "rev", "expand",
})
# Segments that read or print without changing what the runner will collect.
# `git diff` and `cat` DO stream text into the same captured bytes the name
# extractors read, so a file that literally contains `FAILED x::y` can still
# donate a name (the H5 hazard); what bounds that is the `kind == "test"` gate
# in `_classify_execution_vs_baseline`, and admitting these prefixes is what
# recovers ~30% of the pytest corpus. Anything that writes, installs, builds
# or runs another program is deliberately NOT here.
_BENIGN_COMMANDS = frozenset({
    "cd", "echo", "printf", "true", ":", "pwd", "export", "ls", "cat", "nl",
    "head", "tail", "wc", "sed", "grep", "egrep", "fgrep", "rg", "test", "[",
    "sort", "uniq", "cut", "tr", "awk", "column", "date", "basename",
    "dirname", "readlink", "file", "stat", "md5sum", "sha256sum", "cmp",
    "tee", "less", "more", "rev", "fold", "expand", "strings", "which",
    "type", "hostname", "whoami", "env",
})
# `git worktree add` builds a second checkout the agent then `cd`s into; the
# runner still runs once, against a tree this command names.
# `git add` and `git commit` move bytes between the index and history; neither
# changes a tracked file's CONTENT, so the runner beside them reads exactly
# the tree it would have read anyway, and neither prints a runner's name
# grammar. `git worktree remove` (which deletes a checkout) is deliberately
# NOT admitted - see `_is_benign_git`.
_BENIGN_GIT = frozenset({
    "status", "rev-parse", "branch", "log", "diff", "show", "merge-base",
    "add", "commit", "rev-list", "ls-files", "describe", "shortlog",
    "remote", "tag", "blame", "cat-file", "symbolic-ref",
})
# A static check reads the tree and reports; it neither builds anything nor
# selects a single test, and none of these print a runner's name grammar
# (`ruff` prints `path:line:col: CODE`, `tsc` prints `path(l,c): error TSnnnn`).
# The wheel's own taxonomy draws the same line - ValidationKind.STATIC_CHECK
# here, COMPILER_CHECK deliberately NOT: a build is the `npm run build` class
# and stays non-benign.
_STATIC_CHECKS = frozenset({
    "ruff", "mypy", "flake8", "pylint", "pyright", "black", "isort",
    "eslint", "prettier", "tsc", "biome", "rubocop", "phpstan", "shellcheck",
})
_STATIC_CHECK_SUBCOMMANDS = {
    "cargo": frozenset({"clippy", "fmt"}),
    "go": frozenset({"vet", "fmt"}),
    # An empty set means the tool IS the check - it has no subcommand.
    "gofmt": frozenset(),
}
# A check that REWRITES the sources is not benign: the runner that follows it
# imports different bytes than the runner before it would have. `black src/`,
# `gofmt -w x.go`, `eslint --fix`, `ruff format` (no `--check`) all write.
_CHECK_WRITE_FLAGS = frozenset({
    "-w", "--write", "--fix", "--fix-only", "--in-place", "-i", "--apply",
    "--apply-unsafe", "--unsafe-fixes", "-a", "-A", "--autocorrect",
    "--autocorrect-all",
})
# Tools whose DEFAULT is to rewrite; benign only with an explicit check flag.
# Short forms (`-l`, `-c`, `-n`) are deliberately absent: `black -l 100` is a
# line length, not a check, and reading it as one would admit a writer.
_REWRITING_CHECKS = frozenset({"black", "isort", "prettier", "gofmt"})
_REWRITING_SUBCOMMANDS = {("ruff", "format"), ("cargo", "fmt"), ("go", "fmt")}
_CHECK_ONLY_FLAGS = frozenset({
    "--check", "--check-only", "--diff", "--list-different",
    "--verify-no-changes", "--dry-run",
})
# Flags after which a program prints a banner and exits without doing its job.
# Applied to a RUNNER's argv: `pytest --version` collected nothing, so calling
# it a `suite` run would let a version banner promote a whole collection.
_NO_RUN_FLAGS = frozenset({
    "--version", "-V", "--help", "-h", "--usage",
    "--collect-only", "--collectonly", "--co", "--fixtures", "--markers",
    "--setup-only", "--setup-plan", "--list-fixtures",
    "--no-run", "--list", "--list-tests", "--listTests", "--showConfig",
    "--show-config", "--show-only", "--dry-run", "--debug-only",
})
# Package-manager scripts that are a lint/type gate by near-universal
# convention. Narrow on purpose: `build` compiles, `check` runs tests in
# plenty of repositories, and neither is here. The script body is arbitrary,
# so this is the one entry in the benign set that trusts a NAME - what bounds
# it is that such a script can only ADD output to a run that already covers
# the whole package, never narrow one.
_STATIC_CHECK_SCRIPTS = frozenset({
    "lint", "lint:check", "lint:ci", "typecheck", "type-check", "types",
    "tsc", "format:check", "fmt:check", "prettier:check", "style",
})
# Read-only queries of tools that are not otherwise benign.
_READ_ONLY_SUBCOMMANDS = {
    "pip": frozenset({"list", "show", "freeze", "check", "config"}),
    "pip3": frozenset({"list", "show", "freeze", "check", "config"}),
    "conda": frozenset({"list", "info"}),
    "poetry": frozenset({"show", "check"}),
}
# Wrappers that may precede the runner token inside its own segment. Without
# an allowlist `grep pytest out.log` reads as a pytest invocation.
_PLAIN_WRAPPERS = frozenset({
    "nohup", "stdbuf", "xvfb-run", "sudo", "command", "exec", "time",
    "setsid", "ionice",
})
# `<tool> run <runner>` forms. `pnpm test` is a runner, `pnpm exec vitest` is
# a wrapper, so the subcommand - not the tool - decides.
#
# E2 (Terminal-Bench 2.0): 82 of the 89 TB2 tasks invoke their tests as
# `uvx pytest ...` or `uv run [--project <dir>] pytest ...`. The pinned wheel
# recognises `uv run pytest` but NOT `uvx pytest` (`uvx` is absent from its
# wrapper alternatives), so those runs produced no `execution_evidence
# kind=test` row at all and covering_red / recovery / submit_refusal were dead
# on them. Both forms are peeled here, and `wrapper_stripped_command` re-
# exposes the runner to the kind decision in `compile_execution_evidence`.
_RUN_WRAPPERS = frozenset({"uv", "poetry", "pdm", "hatch", "pipenv", "rye"})
_EXEC_WRAPPERS = frozenset({"npx", "bunx", "uvx"})
# Options `uv run` consumes before the program it runs. `--project <dir>` also
# names the directory the run happened in, which is that run's coverage.
_UV_RUN_VALUE_OPTIONS = frozenset({
    "--project", "--directory", "--with", "--with-requirements", "--python",
    "-p", "--package", "--extra", "--group", "--index", "--color",
    "--config-file", "--cache-dir", "--python-preference",
})
_INTERPRETER_RE = re.compile(r"^(?:python[\d.]*|py|pypy[\d.]*)$")
# CPython flags that take no value and leave `-m` reachable. `-c`, `-`,
# `--help`-class options and a bare script path are NOT here on purpose:
# they replace the module run rather than precede it.
_INTERPRETER_NOARG_FLAGS = frozenset({
    "-B", "-b", "-E", "-I", "-O", "-OO", "-P", "-R", "-s", "-S", "-u",
    "-q", "-v", "-V", "-VV", "-x",
})
# Interpreter flags that consume exactly one value.
_INTERPRETER_VALUE_FLAGS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
_SCRIPT_RUNNERS = frozenset({"npm", "pnpm", "yarn", "bun", "deno"})
_DIRECT_RUNNERS = {
    "pytest": "pytest", "py.test": "pytest",
    "jest": "jest", "vitest": "vitest", "mocha": "mocha",
    "ctest": "ctest", "tox": "tox", "nox": "nox",
}

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


@dataclass(frozen=True)
class CommandShape:
    """What one test command actually attempted, and by which runner.

    ``scope``           - ``suite`` | ``scoped`` | ``unknown``
    ``family``          - the runner that produced the bytes, so the name
                          extractors can be pointed at the right grammar
    ``coverage``        - positional path/nodeid/package arguments. For a
                          ``suite`` run in a per-package family (npm, jest,
                          vitest, mocha) it is instead the package DIRECTORY
                          the run happened in: ``cd ark/json-schema && pnpm
                          test`` is the whole suite OF THAT PACKAGE, and where
                          it ran is the only coverage statement it makes.
    ``excluded``        - --ignore / --ignore-glob / --deselect targets
    ``narrowed``        - -k / -m / -run / -t / a ::nodeid / a filter after
                          `--`: the run skipped part of its own coverage, so
                          it may fold names but never promote them
    ``runner_segment``  - the runner's own argv, wrappers and redirections
                          stripped, suitable to hand to a counts parser
    """

    scope: str
    family: str = "unknown"
    coverage: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    narrowed: bool = False
    runner_segment: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        """Compatibility alias for the pytest-era ``CommandCoverage.paths``."""
        return self.coverage


# The pre-E1 name. Callers that only ever saw pytest keep working unchanged.
CommandCoverage = CommandShape

_UNKNOWN_SHAPE = CommandShape("unknown")


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


_EXECUTABLE_SUFFIX_RE = re.compile(r"(?i)\.(?:exe|bat|cmd|com)$")


def _runner_basename(word: str) -> str:
    """Executable basename a runner identity can match.

    A Windows-shaped invocation still names the runner:
    ``"C:\\...\\python.exe" -m unittest`` is a unittest run on any host, so
    runner resolution must see ``python`` the way the shell does. Applied
    only where a word is resolved AS a program name - never to payload args.
    """
    return _EXECUTABLE_SUFFIX_RE.sub("", _basename(word))


def _closing_quote(text: str, start: int) -> int | None:
    """Index of the quote that closes the one at ``start``, or None."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if quote == '"' and text[i] == "\\" and i + 1 < len(text):
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    return None


def _closing_bracket(text: str, start: int) -> int | None:
    """Index of the `)`/`}` closing the bracket at ``start``, or None."""
    opening = text[start]
    closing = ")" if opening == "(" else "}"
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            i += 2
            continue
        if char in "'\"":
            end = _closing_quote(text, i)
            if end is None:
                return None
            i = end + 1
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _split_segments(command: str) -> list[tuple[str, str]] | None:
    """``[(preceding_operator, raw_text)]`` split at TOP-LEVEL operators.

    Quotes, ``$(...)``, ``${...}`` and backticks are opaque, so the `|` inside
    ``go test `go list ./... | grep -v /js` `` does not split the command and
    the `;` inside ``echo "a;b"`` does not either. Returns None when quoting
    never closes - an unreadable command must read ``unknown`` rather than
    half a command.
    """
    text = command or ""
    segments: list[tuple[str, str]] = []
    operator = ""
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "\\" and i + 1 < n:
            buf.append(text[i:i + 2])
            i += 2
            continue
        if char in "'\"":
            end = _closing_quote(text, i)
            if end is None:
                return None
            buf.append(text[i:end + 1])
            i = end + 1
            continue
        if char == "`":
            end = text.find("`", i + 1)
            if end == -1:
                return None
            buf.append(text[i:end + 1])
            i = end + 1
            continue
        if text.startswith("$(", i) or text.startswith("${", i):
            end = _closing_bracket(text, i + 1)
            if end is None:
                return None
            buf.append(text[i:end + 1])
            i = end + 1
            continue
        if text.startswith("&&", i) or text.startswith("||", i):
            segments.append((operator, "".join(buf)))
            operator = text[i:i + 2]
            buf = []
            i += 2
            continue
        if char == "&" and (
            (buf and "".join(buf).rstrip()[-1:] == ">")
            or text[i + 1:i + 2] == ">"
        ):
            # `2>&1`, `&>out`, `>&2`: a descriptor, not the background
            # operator. Splitting here is what made every `... 2>&1 | tail`
            # command in the corpus unreadable.
            buf.append(char)
            i += 1
            continue
        if char in ";|&\n":
            segments.append((operator, "".join(buf)))
            operator = "\n" if char == "\n" else char
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    segments.append((operator, "".join(buf)))
    return [(op, raw) for op, raw in segments if raw.strip()]


def _mask_substitutions(text: str) -> tuple[str, dict[str, str]]:
    """Replace ``$(...)``/``${...}``/backtick spans with space-free tokens.

    ``shlex`` knows nothing about substitution, so ``go test `go list ./... |
    grep -v /js``` came back as five words and the package argument was lost.
    Masking keeps each substitution as ONE word, which is what it is.
    """
    table: dict[str, str] = {}
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if char in "'\"":
            end = _closing_quote(text, i)
            if end is None:
                return text, {}
            out.append(text[i:end + 1])
            i = end + 1
            continue
        end = None
        if char == "`":
            found = text.find("`", i + 1)
            end = None if found == -1 else found
        elif text.startswith("$(", i) or text.startswith("${", i):
            end = _closing_bracket(text, i + 1)
        if end is None:
            out.append(char)
            i += 1
            continue
        key = f"\x00sub{len(table)}\x00"
        table[key] = text[i:end + 1]
        out.append(key)
        i = end + 1
    return "".join(out), table


def _shell_words(command: str) -> list[str] | None:
    """Split one segment into words. None when the quoting is unreadable."""
    masked, table = _mask_substitutions(command or "")
    try:
        words = shlex.split(masked, posix=True)
    except ValueError:
        return None
    if not table:
        return words
    restored: list[str] = []
    for word in words:
        for key, value in table.items():
            if key in word:
                word = word.replace(key, value)
        restored.append(word)
    return restored


def _strip_redirections(words: list[str]) -> tuple[list[str], tuple[str, ...]]:
    """``(argv, files_written)``. `2>&1` dups a descriptor, it writes nothing."""
    argv: list[str] = []
    written: list[str] = []
    expect_target = False
    writing = False
    for word in words:
        if expect_target:
            expect_target = False
            if writing and not word.startswith("&"):
                written.append(word)
            continue
        if word in _BARE_REDIRECT:
            expect_target = True
            writing = ">" in word
            continue
        if _REDIRECT_RE.match(word):
            rest = _REDIRECT_RE.sub("", word, count=1)
            if not rest:
                expect_target = True
                writing = ">" in word
            elif ">" in word and not rest.startswith("&"):
                written.append(rest)
            continue
        argv.append(word)
    return argv, tuple(written)


def _strip_wrappers(words: list[str]) -> tuple[list[str], tuple[str, ...]]:
    """Peel env assignments and exec wrappers off a segment's head.

    `FORCE_COLOR=1 timeout 200 cargo test ...` and `PYTHONPATH=... timeout 900
    env CONTEXT=abs go test ...` are the two shapes the corpus uses most;
    neither changes which tests the run attempted.
    """
    assignments: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        base = _runner_basename(word)
        if _ENV_ASSIGN_RE.match(word):
            assignments.append(word)
            i += 1
            continue
        if base in _PLAIN_WRAPPERS:
            i += 1
            continue
        if base == "timeout":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                if words[i] in ("-k", "-s", "--signal", "--kill-after"):
                    i += 1
                i += 1
            i += 1  # the duration
            continue
        if base == "env":
            i += 1
            while i < len(words):
                if _ENV_ASSIGN_RE.match(words[i]):
                    assignments.append(words[i])
                    i += 1
                elif words[i] == "-u" and i + 1 < len(words):
                    i += 2
                elif words[i] in ("-i", "--ignore-environment"):
                    i += 1
                else:
                    break
            continue
        if base == "nice":
            i += 1
            if i < len(words) and words[i] == "-n":
                i += 2
            elif i < len(words) and re.fullmatch(r"-\d+", words[i]):
                i += 1
            continue
        break
    return words[i:], tuple(assignments)


def _strip_run_options(words: list[str], hint: str) -> tuple[list[str], str]:
    """Peel `uv run`'s own options off the program it is about to run.

    `uv run --project /app pytest -q` (E2: the TB2 house style) hides the
    runner behind two words that are not the runner's. `--project <dir>` is
    also the only statement such a command makes about WHERE it ran.
    """
    i = 0
    while i < len(words):
        word = words[i]
        if word == "--":
            i += 1
            break
        if not word.startswith("-"):
            break
        option, equals, inline = word.partition("=")
        if option in ("--project", "--directory"):
            if equals:
                hint = hint or inline
            elif i + 1 < len(words):
                hint = hint or words[i + 1]
        if option in _UV_RUN_VALUE_OPTIONS and not equals:
            i += 1
        i += 1
    return words[i:], hint


def _peel_exec_wrappers(
    words: list[str], assigns: dict[str, str], hint: str = ""
) -> tuple[list[str], str] | None:
    """Peel `uvx` / `uv run [--project X]` / `npx` / `pnpm exec` off a program.

    These forms say WHERE a program comes from, never what it does, so the
    program underneath is what both runner resolution and benignity have to
    look at. None when a `$VAR` head cannot be resolved from an earlier
    segment's assignments - the program is genuinely unknown then.
    """
    for _ in range(6):
        if not words:
            return words, hint
        head = words[0]
        if head.startswith("$"):
            # `PY=/root/.../python && $PY -m pytest` - the interpreter is
            # named in an earlier segment, so resolve it before giving up.
            name = head.lstrip("$").strip("{}")
            if name not in assigns:
                return None
            words = [assigns[name], *words[1:]]
            continue
        base = _runner_basename(head)
        if base in _RUN_WRAPPERS and words[1:2] == ["run"]:
            words, hint = _strip_run_options(words[2:], hint)
            continue
        if base in _EXEC_WRAPPERS:
            words = words[1:]
            continue
        if base == "pnpm" and words[1:2] in (["exec"], ["dlx"]):
            words = words[2:]
            continue
        if base == "yarn" and words[1:2] == ["dlx"]:
            words = words[2:]
            continue
        # `yarn jest`, `pnpm vitest run`: the package manager is running a
        # binary directly, not the package's `test` script.
        if base in ("yarn", "pnpm", "bun") and _runner_basename(
            words[1] if len(words) > 1 else ""
        ) in _DIRECT_RUNNERS:
            words = words[1:]
            continue
        break
    return words, hint


def _runner_call(
    argv: list[str], assigns: dict[str, str]
) -> tuple[str, list[str], list[str], str] | None:
    """``(family, args_after_the_runner, runner_argv, cwd_hint)`` or None."""
    peeled = _peel_exec_wrappers(list(argv), assigns)
    if peeled is None:
        return None
    words, hint = peeled
    if not words:
        return None
    base = _runner_basename(words[0])
    rest = words[1:]
    if base in _DIRECT_RUNNERS:
        return _DIRECT_RUNNERS[base], rest, words, hint
    if _INTERPRETER_RE.match(base):
        # Flags the interpreter consumes before `-m`. `-c` and a bare script
        # path END module resolution (python executes them instead), so they
        # are deliberately absent - only flags that leave `-m` reachable skip.
        i = 0
        while i < len(rest):
            if rest[i] in _INTERPRETER_NOARG_FLAGS:
                i += 1
            elif rest[i] in _INTERPRETER_VALUE_FLAGS and i + 1 < len(rest):
                i += 2
            else:
                break
        if rest[i:i + 1] == ["-m"] and len(rest) > i + 1:
            module = rest[i + 1]
            if module in ("pytest", "py.test"):
                return "pytest", rest[i + 2:], words, hint
            if module in ("unittest", "tox", "nox"):
                return module, rest[i + 2:], words, hint
        return None
    if base == "cargo":
        if rest[:1] == ["test"]:
            return "cargo", rest[1:], words, hint
        if rest[:2] == ["nextest", "run"]:
            return "cargo", rest[2:], words, hint
        return None
    if base == "go":
        return ("go", rest[1:], words, hint) if rest[:1] == ["test"] else None
    if base in _SCRIPT_RUNNERS:
        args = rest[1:] if rest[:1] == ["run"] else rest
        if not args:
            return None
        script = args[0]
        if script == "test" or script.startswith("test:"):
            return "node", args[1:], words, hint
        return None
    if base in ("make", "gmake"):
        skip_next = False
        for i, arg in enumerate(rest):
            if skip_next:
                skip_next = False
                continue
            if arg.startswith("-"):
                if arg in ("-f", "-C", "-j", "--file", "--directory", "--jobs"):
                    skip_next = True
                continue
            if arg in ("test", "tests", "check"):
                return "make", rest[i + 1:], words, hint
            return None
        return None
    if base in ("mvn", "mvnw"):
        return ("maven", rest, words, hint) if "test" in rest else None
    if base in ("gradle", "gradlew"):
        selects = any(arg == "test" or arg.endswith(":test") for arg in rest)
        return ("gradle", rest, words, hint) if selects else None
    if base == "dotnet":
        return ("dotnet", rest[1:], words, hint) if rest[:1] == ["test"] else None
    return None


def _read_pytest(args: list[str], cwd: str) -> CommandShape:
    del cwd
    paths: list[str] = []
    excluded: list[str] = []
    narrowed = False
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
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
    if any(_OPAQUE_PATH_RE.search(path) for path in paths):
        narrowed = True
    scope = "scoped" if (paths or excluded or narrowed) else "suite"
    return CommandShape(scope, "pytest", tuple(paths), tuple(excluded), narrowed)


def _read_unittest(args: list[str], cwd: str) -> CommandShape:
    """`python -m unittest` is the whole suite unless a module or -k narrows it."""
    del cwd
    targets: list[str] = []
    narrowed = False
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            if arg == "-k":
                narrowed = True
                skip_next = True
            elif arg in ("-s", "--start-directory", "-t",
                         "--top-level-directory", "-p", "--pattern"):
                skip_next = True
            continue
        if arg == "discover":
            continue
        targets.append(arg)
    scope = "scoped" if (targets or narrowed) else "suite"
    return CommandShape(scope, "unittest", tuple(targets), (), narrowed)


_CARGO_TARGET_OPTIONS = frozenset({
    "--test", "--bin", "--example", "--bench", "--lib", "--bins", "--tests",
    "--doc", "--benches", "--examples",
})
_CARGO_VALUE_OPTIONS = frozenset({
    "--features", "--target", "--manifest-path", "--profile", "-j", "--jobs",
    "--target-dir", "--message-format", "--config", "-Z",
})
_LIBTEST_VALUE_OPTIONS = frozenset({
    "--test-threads", "--skip", "--format", "--logfile", "-Z", "--color",
})


def _read_cargo(args: list[str], cwd: str) -> CommandShape:
    """`-p pkg` is the coverage statement; anything positional is a filter.

    `cargo test -p pest_meta -- --test coalesce_is_final_top_down_pass` - the
    single most common Rust shape in the corpus - is a NARROWED run of one
    package: it may fold `coalesce_is_final_top_down_pass` but must never
    promote the rest of pest_meta. `cargo test` and `cargo test --workspace`
    are the suite.
    """
    del cwd
    packages: list[str] = []
    narrowed = False
    targeted = False
    after_dashdash = False
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            after_dashdash = True
            continue
        if arg.startswith("-") and arg != "-":
            option, equals, inline = arg.partition("=")
            if after_dashdash:
                if option == "--skip":
                    narrowed = True
                if option in _LIBTEST_VALUE_OPTIONS and not equals:
                    skip_next = True
                continue
            if option in ("-p", "--package", "--exclude"):
                value = inline if equals else (
                    args[i + 1] if i + 1 < len(args) else ""
                )
                if not equals:
                    skip_next = True
                if value:
                    packages.append(value)
                continue
            if option in _CARGO_TARGET_OPTIONS:
                targeted = True
                if not equals and option in (
                    "--test", "--bin", "--example", "--bench"
                ):
                    skip_next = True
                continue
            if option in ("--workspace", "--all"):
                continue
            if option in _CARGO_VALUE_OPTIONS and not equals:
                skip_next = True
            continue
        # A bare word is a libtest name filter on either side of `--`.
        narrowed = True
    scope = "scoped" if (packages or targeted or narrowed) else "suite"
    return CommandShape(scope, "cargo", tuple(packages), (), narrowed)


_GO_VALUE_OPTIONS = frozenset({
    "-count", "-timeout", "-tags", "-parallel", "-cpu", "-coverprofile",
    "-covermode", "-o", "-ldflags", "-gcflags", "-exec", "-fuzztime",
    "-shuffle", "-benchtime", "-blockprofile", "-cpuprofile", "-memprofile",
    "-outputdir", "-coverpkg",
})
_GO_NARROWING_OPTIONS = frozenset({
    "-run", "-bench", "-skip", "-fuzz", "-test.run",
})


def _read_go(args: list[str], cwd: str) -> CommandShape:
    """`./...` is the suite; anything else is a package prefix.

    A bare `go test` tests only the package in the working directory, so it
    reads `scoped` on `.` - calling it `suite` is the one direction this
    module is not allowed to be wrong in.
    """
    del cwd
    paths: list[str] = []
    narrowed = False
    whole = False
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            option, equals, _inline = arg.partition("=")
            if option in _GO_NARROWING_OPTIONS:
                narrowed = True
                if not equals:
                    skip_next = True
                continue
            if option in _GO_VALUE_OPTIONS and not equals:
                skip_next = True
            continue
        if arg == "./...":
            whole = True
            continue
        if _OPAQUE_PATH_RE.search(arg):
            # ``go test `go list ./... | grep -v /js` `` - the package list is
            # computed at run time, so its extent is not knowable here.
            narrowed = True
            paths.append(arg)
            continue
        trimmed = arg[2:] if arg.startswith("./") else arg
        if trimmed.endswith("/..."):
            trimmed = trimmed[:-4]
        paths.append(trimmed or ".")
    if paths or narrowed:
        scope = "scoped"
    elif whole:
        scope = "suite"
    else:
        scope = "scoped"
        paths = ["."]
    return CommandShape(scope, "go", tuple(paths), (), narrowed)


_NODE_NARROWING_OPTIONS = frozenset({
    "-t", "--testNamePattern", "--test-name-pattern", "--grep", "-g",
    "--fgrep", "-f", "--testPathPattern", "--testPathPatterns",
})
_NODE_VALUE_OPTIONS = frozenset({
    "--reporter", "--reporters", "--config", "-c", "--maxWorkers", "--shard",
    "--coverageDirectory", "--outputFile", "--timeout", "--retries",
    "--require", "-r", "--ui", "--project", "--projects", "--environment",
    "--root", "--dir", "--maxConcurrency", "--pool", "--testTimeout",
})


def _read_node(args: list[str], cwd: str, family: str) -> CommandShape:
    """jest/vitest/mocha argv, reached directly or through an npm script.

    `cd ark/json-schema && pnpm test` is the WHOLE suite of that package, so
    the scope is `suite` and the package directory is its coverage statement.
    """
    paths: list[str] = []
    narrowed = False
    skip_next = False
    if family == "vitest" and args[:1] in (["run"], ["watch"], ["related"]):
        args = args[1:]
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            continue
        if arg.startswith("-") and arg != "-":
            option, equals, _inline = arg.partition("=")
            if option in _NODE_NARROWING_OPTIONS:
                narrowed = True
                if not equals:
                    skip_next = True
                continue
            if option in _NODE_VALUE_OPTIONS and not equals:
                skip_next = True
            continue
        if _OPAQUE_PATH_RE.search(arg):
            narrowed = True
        paths.append(arg)
    if paths or narrowed:
        return CommandShape("scoped", family, tuple(paths), (), narrowed)
    package = (cwd,) if cwd and not _OPAQUE_PATH_RE.search(cwd) else ()
    return CommandShape("suite", family, package, (), False)


def _read_make(args: list[str], cwd: str) -> CommandShape:
    """`make test` is the suite; extra targets narrow it to whatever they name."""
    del cwd
    targets = [arg for arg in args if not arg.startswith("-")]
    if targets:
        return CommandShape("scoped", "make", tuple(targets), (), True)
    return CommandShape("suite", "make", (), (), False)


def _read_tox(args: list[str], cwd: str, family: str) -> CommandShape:
    """`-e py311` selects an ENVIRONMENT, not a subset of tests."""
    del cwd
    positionals: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            continue
        if arg.startswith("-"):
            option, equals, _inline = arg.partition("=")
            if option in ("-e", "-c", "-s", "--result-json", "-f", "-k") \
                    and not equals:
                skip_next = True
            continue
        positionals.append(arg)
    if positionals:
        return CommandShape("scoped", family, tuple(positionals), (), True)
    return CommandShape("suite", family, (), (), False)


def _read_filtered(
    args: list[str], cwd: str, family: str, filters: frozenset[str]
) -> CommandShape:
    """ctest / dotnet / maven / gradle: suite unless an obvious filter narrows."""
    del cwd
    narrowed = False
    selected: list[str] = []
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        option, equals, inline = arg.partition("=")
        if option in filters:
            narrowed = True
            if equals:
                if inline:
                    selected.append(inline)
            elif i + 1 < len(args):
                selected.append(args[i + 1])
                skip_next = True
            continue
        if arg.startswith("-"):
            continue
        if arg == "test" or arg.endswith(":test"):
            continue
        selected.append(arg)
    if narrowed or selected:
        return CommandShape("scoped", family, tuple(selected), (), narrowed)
    return CommandShape("suite", family, (), (), False)


_CTEST_FILTERS = frozenset({"-R", "--tests-regex", "-L", "--label-regex"})
_DOTNET_FILTERS = frozenset({"--filter"})
_MAVEN_FILTERS = frozenset({"-Dtest", "-Dit.test"})
_GRADLE_FILTERS = frozenset({"--tests"})

_FAMILY_READERS = {
    "pytest": _read_pytest,
    "unittest": _read_unittest,
    "cargo": _read_cargo,
    "go": _read_go,
    "node": lambda args, cwd: _read_node(args, cwd, "node"),
    "jest": lambda args, cwd: _read_node(args, cwd, "jest"),
    "vitest": lambda args, cwd: _read_node(args, cwd, "vitest"),
    "mocha": lambda args, cwd: _read_node(args, cwd, "mocha"),
    "make": _read_make,
    "tox": lambda args, cwd: _read_tox(args, cwd, "tox"),
    "nox": lambda args, cwd: _read_tox(args, cwd, "nox"),
    "ctest": lambda args, cwd: _read_filtered(args, cwd, "ctest", _CTEST_FILTERS),
    "dotnet": lambda args, cwd: _read_filtered(args, cwd, "dotnet", _DOTNET_FILTERS),
    "maven": lambda args, cwd: _read_filtered(args, cwd, "maven", _MAVEN_FILTERS),
    "gradle": lambda args, cwd: _read_filtered(args, cwd, "gradle", _GRADLE_FILTERS),
}


@dataclass(frozen=True)
class _Segment:
    operator: str
    argv: tuple[str, ...]
    written: tuple[str, ...]


def _parse_segments(command: str, depth: int = 0) -> list[_Segment] | None:
    """Tokenize, strip redirections, and inline one level of `bash -c`."""
    raw_segments = _split_segments(command)
    if not raw_segments:
        return None
    out: list[_Segment] = []
    for operator, raw in raw_segments:
        words = _shell_words(raw)
        if words is None:
            return None
        argv, written = _strip_redirections(words)
        stripped, _env = _strip_wrappers(argv)
        if (
            depth < 2
            and len(stripped) == 3
            and _basename(stripped[0]) in ("bash", "sh", "zsh")
            and stripped[1] == "-c"
        ):
            inner = _parse_segments(stripped[2], depth + 1)
            if inner is None:
                return None
            for i, segment in enumerate(inner):
                out.append(_Segment(
                    operator if i == 0 else segment.operator,
                    segment.argv,
                    segment.written if i else segment.written + written,
                ))
            continue
        out.append(_Segment(operator, tuple(argv), written))
    return out


def _is_benign_git(args: tuple[str, ...]) -> bool:
    if not args:
        return False
    if args[0] in _BENIGN_GIT:
        return True
    if args[:2] == ("worktree", "add"):
        return True
    # `worktree list` / `stash list` report; `worktree remove` deletes a tree.
    if args[:2] in (("worktree", "list"), ("stash", "list")):
        return True
    # `git checkout <path>` restores one file; `git checkout <branch>` moves
    # the whole tree under the runner, so only a path form is benign.
    if args[0] in ("checkout", "restore") and len(args) > 1:
        return any(
            "/" in arg or arg.startswith(".") or "." in _basename(arg)
            for arg in args[1:] if not arg.startswith("-")
        )
    return False


def _option_names(args: list[str]) -> set[str]:
    """Option words with any ``=value`` tail removed."""
    return {arg.partition("=")[0] for arg in args if arg.startswith("-")}


def _is_static_check(argv: list[str]) -> bool:
    """A lint/type/format CHECK: it reads the tree and reports, nothing else.

    `_STATIC_CHECKS` and `_STATIC_CHECK_SUBCOMMANDS` were declared for this
    and never consulted, so `pytest -q && ruff check src && mypy src` - the
    single most common "and now the lint gate" shape in the corpus - read
    `unknown` and every gate keyed on a test boundary was dead on it. None of
    these print a runner's name grammar (ruff prints `path:line:col: CODE`,
    tsc prints `path(l,c): error TSnnnn`), so admitting them cannot donate a
    test name. A check that WRITES is refused: the runner after `black src/`
    imports different bytes than the runner before it would have.
    """
    if not argv:
        return False
    base = _runner_basename(argv[0])
    rest = list(argv[1:])
    # `python -m ruff check src` / `python -m mypy src`.
    if _INTERPRETER_RE.match(base) and rest[:1] == ["-m"] and len(rest) > 1:
        base, rest = _runner_basename(rest[1]), rest[2:]
    positional = [arg for arg in rest if not arg.startswith("-")]
    subcommand = positional[0] if positional else ""
    if base in _SCRIPT_RUNNERS:
        # `npm run lint`: the package's own lint gate, never `npm test`.
        return (
            positional[:1] == ["run"]
            and len(positional) > 1
            and positional[1] in _STATIC_CHECK_SCRIPTS
        )
    if base in _STATIC_CHECK_SUBCOMMANDS:
        allowed = _STATIC_CHECK_SUBCOMMANDS[base]
        if allowed and subcommand not in allowed:
            return False
    elif base not in _STATIC_CHECKS:
        return False
    options = _option_names(rest)
    if options & _CHECK_WRITE_FLAGS:
        return False
    rewrites = (
        base in _REWRITING_CHECKS
        or (base, subcommand) in _REWRITING_SUBCOMMANDS
    )
    return not rewrites or bool(options & _CHECK_ONLY_FLAGS)


def _is_version_probe(argv: list[str]) -> bool:
    """`python --version`, `yarn --version`: a banner, then exit."""
    return (
        len(argv) > 1
        and all(arg.startswith("-") for arg in argv[1:])
        and _option_names(argv[1:]) <= _NO_RUN_FLAGS
    )


def _is_read_only_query(argv: list[str]) -> bool:
    """`pip list`, `pip show x`: reports what is installed, installs nothing."""
    if not argv:
        return False
    allowed = _READ_ONLY_SUBCOMMANDS.get(_runner_basename(argv[0]))
    if allowed is None:
        return False
    positional = [arg for arg in argv[1:] if not arg.startswith("-")]
    return bool(positional) and positional[0] in allowed


def _is_benign(segment: _Segment, *, allow_stash: bool) -> bool:
    argv = list(segment.argv)
    if not argv:
        return True
    head = argv[0]
    if head.startswith("#"):
        return True  # a comment line inside a multi-line command
    if _ENV_ASSIGN_RE.match(head):
        if all(_ENV_ASSIGN_RE.match(word) for word in argv):
            return True
    # A non-runner segment that WRITES a file can change what the runner
    # collects; only the runner's own `> /tmp/log` is harmless.
    if any(target != "/dev/null" for target in segment.written):
        return False
    # `timeout 60 python -m ruff check src` and `npx tsc --noEmit` are the
    # same statements as `ruff check src` and `tsc --noEmit`: the wrappers
    # say where the program comes from and how long it may take, never what
    # it does. Runner resolution has always peeled them; benignity did not,
    # so the identical segment read benign or hostile by spelling alone.
    stripped, _assignments = _strip_wrappers(argv)
    peeled = _peel_exec_wrappers(stripped, {})
    if peeled is None:
        return False
    argv = peeled[0]
    if not argv:
        return True
    head = argv[0]
    base = _basename(head)
    if base == "export":
        return all(_ENV_ASSIGN_RE.match(word) for word in argv[1:])
    if base == "git":
        if allow_stash and argv[1:2] == ["stash"]:
            return True
        return _is_benign_git(tuple(argv[1:]))
    if base == "cd":
        return len(argv) <= 2
    if base == "rm":
        flags = [word for word in argv[1:] if word.startswith("-")]
        operands = [word for word in argv[1:] if not word.startswith("-")]
        return bool(operands) and all(set(flag[1:]) <= {"f"} for flag in flags)
    if base == "find":
        # `find . -name conftest.py` lists; `-delete`/`-exec` runs or removes.
        return not ({arg for arg in argv[1:] if arg.startswith("-")} & {
            "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
            "-fprintf", "-fls",
        })
    if _is_static_check(argv) or _is_version_probe(argv) or _is_read_only_query(argv):
        return True
    if _is_probe_invocation(argv):
        return True
    return base in _BENIGN_COMMANDS or base in _OUTPUT_FILTERS


def _is_probe_invocation(argv: list[str]) -> bool:
    """A runner asked to describe itself rather than to run anything.

    ``pytest --collect-only -q | grep -c "::"`` beside a real run is the
    corpus's standard "did my paths select what I think" check. It reports
    which tests EXIST; it produces no pass/fail row for any of them, so it
    can neither donate a name nor make the byte stream ambiguous about which
    run produced it.
    """
    call = _runner_call(list(argv), {})
    return call is not None and bool(_option_names(call[1]) & _NO_RUN_FLAGS)


def _is_stash(argv: tuple[str, ...]) -> bool:
    return argv[:2] == ("git", "stash") and argv[2:3] != ("pop",)


def _is_stash_pop(argv: tuple[str, ...]) -> bool:
    return argv[:3] == ("git", "stash", "pop")


def _locate_runner(
    segments: list[_Segment],
) -> tuple[int, tuple[str, list[str], list[str], str]] | None:
    """The single runner segment, or None when there are none or several."""
    assigns: dict[str, str] = {}
    for segment in segments:
        for word in segment.argv:
            if _ENV_ASSIGN_RE.match(word):
                key, _eq, value = word.partition("=")
                assigns.setdefault(key, value)
    found: tuple[int, tuple[str, list[str], list[str], str]] | None = None
    for index, segment in enumerate(segments):
        stripped, _env = _strip_wrappers(list(segment.argv))
        call = _runner_call(stripped, assigns)
        if call is None:
            continue
        if _option_names(call[1]) & _NO_RUN_FLAGS:
            # A `--collect-only` / `--version` probe is not a second run; it
            # is judged as an ordinary (benign) neighbour instead.
            continue
        if found is not None:
            # `pytest a | tail -2 && pytest b`: two runs, one byte stream, and
            # no way to say which run the parsed names came from.
            return None
        found = (index, call)
    return found


def _probe_indices(
    segments: list[_Segment], runner_at: int | None = None
) -> tuple[int, int, int] | None:
    """``(stash, runner, pop)`` when the command is a pristine-tree probe."""
    stash_at = pop_at = found_runner = None
    for index, segment in enumerate(segments):
        if _is_stash_pop(segment.argv):
            if pop_at is None:
                pop_at = index
            continue
        if _is_stash(segment.argv):
            if stash_at is None:
                stash_at = index
            continue
        if found_runner is None:
            stripped, _env = _strip_wrappers(list(segment.argv))
            if _runner_call(stripped, {}) is not None:
                found_runner = index
    if runner_at is not None:
        found_runner = runner_at
    if stash_at is None or pop_at is None or found_runner is None:
        return None
    if not stash_at < found_runner < pop_at:
        return None
    return stash_at, found_runner, pop_at


def test_command_shape(command: str) -> CommandShape:
    """Read one shell command as at most one test-runner invocation.

    See the E1 note above for the corpus this is measured against. The scope
    is ``unknown`` whenever the command cannot be read as exactly one runner
    run surrounded by benign segments - an abstention, never a guess.
    """
    text = command or ""
    if _HEREDOC_RE.search(text):
        return _UNKNOWN_SHAPE
    segments = _parse_segments(text)
    if not segments:
        return _UNKNOWN_SHAPE
    if any(segment.operator == "&" for segment in segments):
        # A backgrounded runner's output never lands in this command's bytes.
        return _UNKNOWN_SHAPE
    located = _locate_runner(segments)
    if located is None:
        return _UNKNOWN_SHAPE
    runner_at, (family, args, runner_argv, cwd_hint) = located
    allow_stash = _probe_indices(segments, runner_at) is not None
    # `uv run --project <dir>` names the package directory itself; a leading
    # `cd <dir>` is the fallback statement of where the run happened.
    cwd = cwd_hint
    for index, segment in enumerate(segments):
        if index == runner_at:
            continue
        if (
            index < runner_at
            and segment.argv[:1] == ("cd",)
            and len(segment.argv) == 2
        ):
            cwd = cwd_hint or segment.argv[1]
        if not _is_benign(segment, allow_stash=allow_stash):
            return _UNKNOWN_SHAPE
    reader = _FAMILY_READERS.get(family)
    if reader is None:
        return _UNKNOWN_SHAPE
    # `which pytest && pytest --version`, `go test --help`, `cargo test
    # --no-run`: the runner printed a banner or compiled and stopped. It
    # collected NOTHING, so `suite` here would let a version string promote a
    # whole collection - the one direction this module may not be wrong in.
    if _option_names(list(args)) & _NO_RUN_FLAGS:
        return _UNKNOWN_SHAPE
    shape = reader(list(args), cwd)
    return replace(shape, runner_segment=tuple(runner_argv))


def test_command_coverage(command: str) -> CommandShape:
    """Classify a test invocation's coverage shape and extent.

    ``suite``   - no positional selection; coverage is the whole collection
    ``scoped``  - positional path/package args or filters narrow coverage
    ``unknown`` - anything else (compound, non-runner, unparseable)

    Conservative direction: an ambiguous suite reads ``scoped`` and loses only
    a ledger-promotion opportunity; a scoped run must never read ``suite``.
    """
    return test_command_shape(command)


def test_command_scope(command: str) -> str:
    """The coverage shape alone - ``suite`` | ``scoped`` | ``unknown``."""
    return test_command_shape(command).scope


def wrapper_stripped_command(command: str) -> str:
    """The command with env prefixes and exec wrappers peeled off the runner.

    E2: the pinned wheel's runner detection never learned `uvx`, so
    `uvx pytest -q` produced no `kind == "test"` evidence row on 82 of the 89
    TB2 tasks and every downstream gate that keys on a test boundary was dead
    there. The wheel is certified and pinned, so the stripping happens HERE
    and the wheel is handed a command it already understands. Returns the
    input unchanged when no runner segment can be found, so a caller can
    always use the result.
    """
    text = command or ""
    if _HEREDOC_RE.search(text):
        return text
    segments = _parse_segments(text)
    if not segments:
        return text
    located = _locate_runner(segments)
    if located is None:
        return text
    _runner_at, (_family, _args, runner_argv, _hint) = located
    return shlex.join(runner_argv)


_PROGRAM_PREFIXES = frozenset({
    # Words after which the NEXT word is still the program the segment runs:
    # exec wrappers, shell keywords and negation. `time`/`nice`/`sudo`-class
    # flags keep the position open too (handled by the flag/assignment rules
    # in the scanner, not by this set).
    "builtin", "command", "do", "elif", "else", "env", "exec", "if", "nice",
    "nohup", "stdbuf", "sudo", "then", "time", "until", "while", "xargs", "!",
    "{",
})
_ASSIGN_WORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ASSIGN_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=$")
# The characters a program word may keep on the surface. Everything else -
# every shell metacharacter, quote and whitespace - flattens to ``_`` so a
# quoted name can never mint a boundary the shell did not parse.
_PROGRAM_SAFE_RE = re.compile(r"[^A-Za-z0-9_./:@%+-]")


def _program_word_surface(content: str) -> str:
    """A quoted program word as readable surface text.

    The program word is invocation surface, not payload: ``"pytest" -q`` and
    ``"C:\\tools\\python.exe" -m unittest`` still run the runner, and masking
    the name makes a real test execution read as no evidence at all. Shell
    metacharacters inside a quoted program name are literal filename bytes
    the shell never parsed, so they flatten to ``_`` - ``"x;nox"`` names one
    weird executable, it does not open a segment - and backslashes normalize
    to ``/`` so a quoted Windows path keeps a parseable basename.
    """
    return _PROGRAM_SAFE_RE.sub("_", content.replace("\\", "/"))


def _unquoted_command_surface(command: str) -> str:
    """The command with quoted payloads and backslash escapes made opaque.

    Shell quoting protects a boundary the way nothing else can:
    ``git commit -m 'x;pytest tests/'`` never split at that ``;`` and
    ``echo \\;pytest`` never separated at it. The canonical runner regex
    reads raw text, so the ``;`` inside a message still matches a boundary
    the shell never created - a commit message minted ``kind == "test"``
    execution evidence, and the churn governor read the same raw text and
    reset its verification-failure streak on it.

    Quoted ARGUMENT spans survive here as ``Q<digest>`` placeholders:
    distinct payloads stay distinct for repeat detection, but no quoted
    ``;``, ``|``, ``&`` or runner name can reach a classifier. Command
    substitution, real operators and heredoc markers are left intact -
    ``$(...)`` outside quotes really does run.

    A quoted span in PROGRAM position is the exception: it is the
    executable the segment runs, not payload. CI templates and Windows
    interpreters are routinely quoted (``"C:\\...\\python.exe" -m
    unittest``); masking the name blinded both the runner parser and the
    wheel's fallback grammar, so a real suite produced no test evidence.
    Program words are emitted as metacharacter-free literals instead -
    readable basename, no boundary syntax - while argument payloads keep
    the opaque ``Q<digest>`` treatment.
    """
    text = command or ""
    out: list[str] = []
    i, n = 0, len(text)
    expecting_program = True
    wrapper_args = False          # env/nice/sudo flags still precede the program
    in_backtick = False
    word: list[str] = []          # the word in progress, quoted spans as 'Q'

    def end_word() -> None:
        nonlocal expecting_program, wrapper_args
        w = "".join(word)
        word.clear()
        if not expecting_program or not w:
            return
        if _ASSIGN_WORD_RE.match(w):
            return                     # VAR=x keeps program position open
        if wrapper_args and w.startswith("-"):
            return                     # a wrapper flag, not the program yet
        if w[:1] in "><" or re.match(r"^\d+[><]", w):
            return                     # a redirection word, not the program
        if _runner_basename(w) in _PROGRAM_PREFIXES:
            wrapper_args = True        # env/sudo/if/... wrap the real program
            return
        expecting_program = False
        wrapper_args = False

    while i < n:
        char = text[i]
        if char == "\\" and i + 1 < n:
            # An escaped character is a literal inside the word - never
            # syntax. `\;pytest` is one word, not a separator plus a runner.
            out.append("  ")
            word.append("x")           # literal word char for shape tracking
            i += 2
            continue
        if char in "'\"":
            end = _closing_quote(text, i)
            stop = n if end is None else end
            content = text[i + 1:stop]
            if expecting_program and not _ASSIGN_PREFIX_RE.match("".join(word)):
                # Program position: the executable name stays readable, and
                # the tracker keeps it so a quoted `env`/`sudo` still wraps
                # the program word that follows it.
                safe = _program_word_surface(content)
                out.append(safe if safe else "Q")
                word.append(safe or "Q")
            else:
                # Payload (incl. a VAR="..." assignment value): opaque.
                digest = hashlib.sha256(
                    content.encode("utf-8", "replace")
                ).hexdigest()[:8]
                out.append(f"{char}Q{digest}{char if end is not None else ''}")
                word.append("Q")
            i = (stop + 1) if end is not None else n
            continue
        if char == "`":
            end_word()
            in_backtick = not in_backtick
            expecting_program = in_backtick   # a substitution's first word runs
            out.append(char)
            i += 1
            continue
        if text.startswith("$(", i) or text.startswith("${", i):
            end_word()
            out.append(text[i:i + 2])
            expecting_program = True    # $( begins a nested command line
            i += 2
            continue
        if char == "&" and (
            (word and "".join(word).rstrip()[-1:] == ">")
            or text[i + 1:i + 2] == ">"
        ):
            # `2>&1` / `&>out`: a descriptor operation, not a boundary.
            out.append(char)
            word.append(char)
            i += 1
            continue
        if char in ";|&\n":
            end_word()
            out.append(char)
            expecting_program = True
            wrapper_args = False
            i += 1
            continue
        if char == "(":
            end_word()
            out.append(char)
            expecting_program = True    # subshell opens on a program word
            i += 1
            continue
        if char in ")}":
            end_word()
            out.append(char)
            expecting_program = False
            i += 1
            continue
        if char.isspace():
            end_word()
            out.append(char)
            i += 1
            continue
        out.append(char)
        word.append(char)
        i += 1
    return "".join(out)


def _classification_command(command: str) -> str:
    """The invocation the shell actually parsed, for outcome classification.

    Two directions the raw command string lies about:

    * quoted payload - ``git commit -m 'x;pytest'`` mints a runner boundary
      that never existed (the wheel regex reads raw text), and
    * exec wrappers - ``uv run --project foo pytest`` hides a real boundary
      behind option words the wheel never learned (E2's TB2 house style).

    Heredoc data bodies are dropped first - ``cat <<EOF\\npytest\\nEOF``
    writes bytes, it does not run them - then quoted spans go opaque, then
    the segment parser's own canonical runner argv is what the classifier
    sees. When no runner is located the masked surface is returned, which
    is still the command the shell ran.
    """
    text = command or ""
    if _HEREDOC_RE.search(text):
        from .bridge import _without_heredoc_bodies

        text = _without_heredoc_bodies(text)
    return wrapper_stripped_command(_unquoted_command_surface(text))


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
    if _HEREDOC_RE.search(command or ""):
        return False
    segments = _parse_segments(command or "")
    if not segments:
        return False
    return _probe_indices(segments) is not None


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


# Directories that are never the repository's own suite: VCS, caches, build
# output, installed dependencies. Dependency test files (vendor/, node_modules)
# do not count toward suite completeness - the agent's suite is the repo's own.
_INVENTORY_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", ".nuxt", "dist", "build", "target", "out", "coverage",
    "site-packages", "vendor", "third_party", "deps", "external",
    ".idea", ".vscode", ".terraform", "bower_components",
})

_INVENTORY_MAX = 50_000


def _is_test_filename(name: str, under_tests_dir: bool) -> bool:
    """Filename-level test conventions for the cohort languages."""
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py") or name.endswith("_test.go"):
        return True
    if re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", name):
        return True
    if name.endswith("Test.java") or name.endswith("Tests.java"):
        return True
    # Rust integration tests live as tests/*.rs; unit tests are inline
    # #[cfg(test)] and invisible to a filename scan, which is conservative.
    if under_tests_dir and name.endswith(".rs"):
        return True
    return False


def repo_test_inventory(root: str | Path) -> frozenset[str] | None:
    """Repo-relative paths of the repository's own test files.

    This is the suite-extent ground truth ``covers_known_suite`` was missing:
    the observed-name universe only contains names a run happened to print,
    so ``pytest tests/unit/`` covering every known name claimed whole-suite
    truth while ``tests/integration/`` sat unobserved. A directory-scoped run
    is suite-equivalent only when its coverage contains every inventoried
    file - ``pytest tests/`` still qualifies on a tests/-rooted layout.

    Returns ``None`` when the root is missing or unenumerable (unknown never
    authorises a whole-suite claim); an empty frozenset is a completed scan
    that found no test files.
    """
    base = Path(root or "")
    if not base.is_dir():
        return None
    found: set[str] = set()
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in _INVENTORY_SKIP_DIRS
            ]
            rel_dir = os.path.relpath(dirpath, base)
            under_tests = any(
                part in ("tests", "test") for part in Path(rel_dir).parts
            )
            for name in filenames:
                if not _is_test_filename(name, under_tests):
                    continue
                rel = name if rel_dir == "." else f"{rel_dir.replace(os.sep, '/')}/{name}"
                found.add(rel)
                if len(found) >= _INVENTORY_MAX:
                    return frozenset(found)
    except OSError:
        return None
    return frozenset(found)


def _matches_any(name: str, prefixes: tuple[str, ...]) -> bool:
    file_part = name.split("::", 1)[0]
    for prefix in prefixes:
        if "::" in prefix:
            if name == prefix:
                return True
        elif file_part == prefix or file_part.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def _dir_covered(path: str, directories: tuple[str, ...]) -> bool:
    """Whether a repo-relative path sits under a covered directory."""
    for prefix in directories:
        d = prefix.rstrip("/")
        if d in (".", "") or path == d or path.startswith(d + "/"):
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
    # The baseline capture command's own declared scope: "suite" when the
    # baseline ran unrestricted, "scoped" with ``baseline_scope_paths`` when
    # it named directories, "unknown" when its shape is unreadable.
    baseline_scope: str = "unknown"
    baseline_scope_paths: tuple[str, ...] = ()

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
        self._repo_inventory: frozenset[str] | None = None

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

    def note_repo_test_inventory(self, paths: Iterable[str]) -> None:
        """Feed the suite-extent ground truth used by ``covers_known_suite``."""
        self._repo_inventory = frozenset(paths)

    def covers_known_suite(
        self, prefixes: Iterable[str], excluded: Iterable[str] = ()
    ) -> bool:
        """Whether a scoped run's positional paths cover the WHOLE suite.

        `pytest tests/` IS the full suite when every test file the repo
        actually has sits under a covered directory - the observed-name
        universe cannot prove that, because it only contains names some run
        already printed: ``pytest tests/unit/`` covering every known name
        still claims whole-suite truth while ``tests/integration/`` sits
        unobserved (partial-inventory overclaim).

        Ground truth sources, in order: the repository test inventory, then
        the baseline command's own declared scope. Directory prefixes only -
        a file or ::nodeid path is narrower than a suite by construction,
        exclusions (--ignore/--deselect) defeat completeness, and with
        neither source the suite's extent is unknown, which never authorises
        a whole-suite claim.
        """
        directories = tuple(
            p for p in prefixes
            if "::" not in p and not Path(p).suffix
        )
        if not directories or tuple(excluded):
            return False
        inventory = self._repo_inventory
        if inventory is not None:
            if not inventory:
                return False
            return all(
                _dir_covered(path.rstrip("/"), directories) for path in inventory
            )
        if self.baseline_scope == "suite":
            # The baseline itself ran unrestricted: any directory-scoped run
            # is narrower than the suite by construction.
            return False
        if self.baseline_scope == "scoped" and self.baseline_scope_paths:
            return all(
                _dir_covered(declared.rstrip("/"), directories)
                for declared in self.baseline_scope_paths
            )
        return False

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
        # "Unrestricted" covers two shapes: the bare `pytest` run and a
        # scoped-looking command whose directory args cover every known test
        # (`pytest tests/` on a tests/-rooted layout).
        unrestricted = not prefixes or self.covers_known_suite(prefixes, excluded)
        if not named_all:
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
            # A green whole-suite run supersedes every verdict taken before
            # the last edit, including the ones it did not name - verbose or
            # not. `named_all` only bounds name promotion; it is not a
            # reason to withhold the run's own completeness.
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
    a ``suite_observed`` basis (D2). Names come from
    ``gt_engine.test_names.parse_test_names`` - the certified wheel first,
    then a per-family extractor for what the wheel's column-0 anchors cannot
    reach - which is the same function the baseline capture uses, so both
    sides parse identically.
    """
    if ledger is None:
        return None
    text = (
        output.decode("utf-8", errors="replace")
        if isinstance(output, (bytes, bytearray))
        else output
    )
    from .test_names import parse_test_names

    shape = test_command_shape(command)
    failing = parse_test_names(
        text or "", family=shape.family, command=command
    ).failing
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
        scope=shape.scope,
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
    # Kind, protocol and exit-code attribution all read the invocation
    # surface: a `;nox`/`| make` inside a quoted payload is message text,
    # not a runner segment, and a quoted `;` is not a compound command.
    surface = _unquoted_command_surface(command)
    # `_ADDITIONAL_TEST_RE` names the runners the pinned wheel does not, and
    # it anchors on a segment boundary - so `uvx nox -s tests` missed it for
    # the same reason `uvx pytest` missed the wheel: the runner is not at a
    # boundary until the wrapper is peeled. The wheel is CERTIFIED AND PINNED
    # and is never edited, so the peeling happens here (see
    # `_classification_command`) and both deciders are handed the invocation
    # the shell actually parsed. `surface` keeps the compound structure,
    # because exit-code attribution below must still see `&&` and `|`.
    runner_surface = _classification_command(command)
    kind = (
        "test" if test_protocol or _ADDITIONAL_TEST_RE.search(runner_surface)
        else "build" if _BUILD_RE.search(surface) else ""
    )
    if not kind:
        return None
    outcome = "timeout" if timed_out else (
        _execution_outcome_guard(surface, returncode, kind=kind) or test_outcome or "unknown"
    )
    return ExecutionEvidence(
        action_id=action_id,
        kind=kind,
        protocol="native" if test_protocol == "native" else _protocol(surface),
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
    # The certified classifier matches the runner grammar against raw text:
    # `;pytest` inside a quoted commit message is a boundary it cannot know
    # the shell never made, and `uv run --project foo pytest` is a real
    # boundary its wrapper list never learned. Hand it the invocation the
    # shell actually parsed - quoted spans opaque, wrappers peeled to the
    # runner argv - which the segment parser above computes either way.
    classify_as = _classification_command(command)
    try:
        if output_artifact is None:
            from groundtruth.runtime.patterns import classify_test_observation

            return classify_test_observation(classify_as, output, returncode)

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
            classify_as, chunks, returncode, encoding="utf-8", errors="replace"
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
