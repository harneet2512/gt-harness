"""Existing versus proposed: separating what the repository has from what it needs.

A plan row names two kinds of thing and they carry different warranties. A
SYMBOL that resolved in the graph is a fact about the code at the captured
revision -- the node id was validated against the published database before it
was allowed into the plan. A CHECK is a command, and the files it names may not
exist yet: ``pytest tests/test_strict_mode.py`` is a perfectly good plan and a
completely absent file.

Nothing distinguished them. The planning call may return
``verification_kind: existing_test`` for a path the repository does not contain,
and the block then told the agent an acceptance test existed. An agent that runs
it gets a collection error rather than a red test, and cannot tell from the plan
which of the two it was looking at.

Everything here is decided ONCE, against the repository as it stood at capture,
and stored on the row. Re-deciding later would be worse than useless: after the
agent writes the file, the same command classifies differently, so a plan
replayed from its journal would not reproduce its own digest.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import replace
from pathlib import Path

# A check whose command names nothing in particular. `pytest`, `npm test` and
# `go test ./...` all run whatever the repository already has, so neither
# "existing" nor "proposed" is a true statement about them.
BASIS_UNNAMED = "unnamed"
# Every path the command names is present at capture.
BASIS_EXISTING = "existing"
# At least one named path is absent: the agent must write it before the command
# can do anything but fail to collect.
BASIS_PROPOSED = "proposed"
# There is no command at all, or no repository to check it against.
BASIS_NONE = "none"
BASIS_UNKNOWN = "unknown"

SYMBOL_EXISTING = "existing"
SYMBOL_NAME_GUESS = "name_guess"
SYMBOL_UNMAPPED = "unmapped"

# Source-file suffixes across the producer's languages. A bare token with one of
# these is a path even without a separator (`test_widget.py`).
_SUFFIX_RE = re.compile(
    r"\.(py|pyi|js|jsx|mjs|cjs|ts|tsx|go|rs|java|rb|php|c|cc|cpp|cxx|h|hpp|cs|kt|kts|swift|scala|ex|exs|feature)$"
)
_PATH_SHAPED_RE = re.compile(r"^[A-Za-z0-9_.@+\-/\\]+$")
# `./...`, `pkg/*_test.go`, `tests/**` name a set, not a file. Asking whether
# such a token exists on disk answers a different question than the one asked.
_WILDCARD_RE = re.compile(r"[*?\[\]]|\.\.\.")


def command_path_tokens(command: str) -> tuple[str, ...]:
    """The repository paths a command names, in order, deduplicated.

    The program itself (argv[0]) is a tool, not a target, and options are not
    paths. A pytest node id is split at ``::`` because the file is the part the
    repository either has or does not.
    """
    text = (command or "").strip()
    if not text:
        return ()
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()
    found: list[str] = []
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        # A pytest node id names a file and a case inside it. Split first: the
        # file is the part the repository either has or does not, and the case
        # after ``::`` is legitimately allowed to be one nobody has written.
        candidate = token.split("::", 1)[0].replace("\\", "/")
        if not candidate or _WILDCARD_RE.search(candidate):
            continue
        if not _PATH_SHAPED_RE.match(candidate):
            continue
        if "/" not in candidate and not _SUFFIX_RE.search(candidate):
            continue
        if candidate not in found:
            found.append(candidate)
    return tuple(found)


def classify_check(command: str, repo_root: str) -> tuple[str, tuple[str, ...]]:
    """What the repository has of what this command needs, at capture."""
    if not (command or "").strip():
        return BASIS_NONE, ()
    if not repo_root:
        return BASIS_UNKNOWN, ()
    named = command_path_tokens(command)
    if not named:
        return BASIS_UNNAMED, ()
    root = Path(repo_root)
    missing = tuple(path for path in named if not (root / path).exists())
    return (BASIS_PROPOSED if missing else BASIS_EXISTING), missing


def classify_symbols(row, inputs) -> str:
    """Whether this row is tied to code the graph actually resolved.

    An exact-name anchor is the identifier the request wrote, found in the
    graph. A lexical anchor is a word match against prose and is routinely
    wrong, so a row carrying only those has not been mapped to anything -- it
    has been guessed at, and the count must say so.
    """
    if not row.anchors:
        return SYMBOL_UNMAPPED
    lookup = {
        anchor.node_id: anchor
        for anchors in getattr(inputs.anchors, "anchors", {}).values()
        for anchor in anchors
    }
    bases = {lookup[node_id].basis for node_id in row.anchors if node_id in lookup}
    if not bases:
        return SYMBOL_UNMAPPED
    return SYMBOL_EXISTING if "exact_name" in bases else SYMBOL_NAME_GUESS


def annotate_row(row, inputs, repo_root: str):
    """Record on the row what it names and whether the repository has it."""
    basis, missing = classify_check(row.verification_command, repo_root)
    kind = row.verification_kind
    # One-directional correction. A plan may not report an existing test over a
    # file the repository does not contain. The reverse is legitimate and must
    # survive: a new case written into an existing file is a new test whose
    # file is already there.
    if basis == BASIS_PROPOSED and kind == "existing_test":
        kind = "new_test"
    return replace(
        row,
        verification_kind=kind,
        check_basis=basis,
        check_missing_paths=missing,
        symbol_basis=classify_symbols(row, inputs),
    )


def annotate_plan(plan, repo_root: str):
    """Annotate every row in place on the artifact, before it is digested."""
    plan.rows = tuple(annotate_row(row, plan.inputs, repo_root) for row in plan.rows)
    return plan


def cleared_check_provenance(value: dict) -> dict:
    """A revision that rewrites the command drops the capture-time reading.

    The classification describes the repository as it stood before the first
    edit. A command written afterwards was never measured against it, and
    measuring it against the edited workspace would make the replay of a
    recovered revision depend on when it was replayed. Saying nothing is the
    only answer that is both true and deterministic.
    """
    if "verification_command" not in value:
        return {}
    return {"check_basis": "", "check_missing_paths": ()}
