"""Pre-submit requirement review for attached delivery.

Trajectory analysis of the DeepSWE tasks GT-on lost (run 36359464192, 7 of 7)
found one mechanism: the agent wrote its own tests to match its own reading
of the task, saw them pass, and submitted - with a sentence of the task
unimplemented or misread (cliffy looked for ``test.rc`` where the task says
``.namerc``; httpx evicted the newest where it says oldest; fastapi never
tested an include_router value against a router default). Every run ended
green on tests that encoded the misreading. Showing the requirements at the
START (the task plan) did not help; the check has to happen at submit.

So the first submit is held once and answered with the task's requirement
lines set against the agent's actual changes:

* every literal the task names (backticked code, quoted strings, --flags,
  dotted / snake / camel identifiers) that appears nowhere in the added code
  or tests is flagged under its requirement line;
* requirement lines with no such literal are listed for the agent to check
  by reading - they cannot be verified mechanically and are not claimed to be;
* the real tests reaching the changed files are named to run.

The second submit always goes through: this is a review, never a gate
(the push arm's plan gate refused every submission on unverifiable rows and
cost TB2 whole runs).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gt_engine.gt_session import GTSession

# Shown once per task, so it may be long; the rows a run misses are the
# limiting ones (kysely, koota, fastapi in run 36450157395), and the old 6000-byte
# cut dropped them from the tail.
MAX_REVIEW_BYTES = 12000
MAX_PROSE_ROWS = 12
MAX_TESTS_SHOWN = 6

# Sentences that exclude or bound something. A misread one passes the agent's
# own tests. Kept to negations and limits: "must"/"default"/"order" appear in
# most sentences of a spec and would tag every row (bandit: 14 of 17).
_CONSTRAINT = re.compile(
    r"\b(?:not|never|only|without|instead of|at least|at most|exactly|no longer|"
    r"nearest|precedence|unless|except|rather than)\b|n't\b", re.I)
# A call signature the task names: `tap(task, fn)`, `lag(column, offset=None)`.
_SIGNATURE = re.compile(r"^([A-Za-z_][\w.]*)\s*\(([^()]*)\)$")
_PARAM_NAME = re.compile(r"^\s*(?:\.\.\.|\*{1,2})?([A-Za-z_]\w*)")

_BACKTICK = re.compile(r"`([^`\n]{2,80})`")
_QUOTED = re.compile(r"""(?<![\w'"])(['"])([^'"\n]{2,60})\1(?![\w'"])""")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]{1,40})")
# Bare dot-prefixed names the task spells out: ".namerc", ".json".
_DOTNAME = re.compile(r"(?<![\w.])(\.[a-z][a-z0-9_]{1,30})\b")
_ATOM = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# Placeholders in a task's templates: N, M, <url>, <RFC 7231 date>.
_PLACEHOLDER = re.compile(r"<[^<>]{1,40}>|\b[A-Z]\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`(\"'])")
# Identifiers that are clearly code: dotted, snake_case, camelCase, PascalCase
# with an inner capital. Plain English words never match.
_IDENT = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9]*(?:[._][A-Za-z0-9_]+)+|[a-z]+[A-Z][A-Za-z0-9]*|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*)(?![\w])")
# Lines that are code or process, not requirements.
_CODE_LINE = re.compile(r"^\s*(?:[{}()\[\];]|(?:const|let|var|def|class|import|from|return|function|public|private|func|fn)\b)|[;{]\s*$|=>")
_PROCESS = re.compile(
    r"\b(?:new branch|from main|pull request|commit (?:everything|your|all)|push (?:your|to)|"
    r"you can execute|bash commands|edit files to implement|IMPORTANT:)", re.I)
_FENCED = re.compile(r"```.*?```", re.S)
# Issue-template placeholders (aiogram smoke 36477831875 listed "_No response_").
_PLACEHOLDER_LINE = re.compile(r"^_?(?:no response|n/?a|none|tbd|-+)_?\.?$", re.I)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


@dataclass
class ReviewRow:
    text: str
    literals: tuple[str, ...]
    missing: tuple[str, ...] = ()
    # "task: tap(task, fn); yours: tap(fn, task)" for each named signature the
    # change defines with a different parameter order.
    shapes: tuple[str, ...] = ()

    @property
    def checkable(self) -> bool:
        return bool(self.literals)

    @property
    def constraint(self) -> bool:
        return bool(_CONSTRAINT.search(self.text))

    def line(self, index: int) -> str:
        tag = "[constraint] " if self.constraint else ""
        notes = ""
        if self.missing:
            notes += f"   [not in your changes: {', '.join(self.missing)}]"
        for shape in self.shapes:
            notes += f"   [{shape}]"
        return f"{index}. {tag}{self.text}{notes}"


@dataclass
class SubmitReview:
    rows: list[ReviewRow] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> list[ReviewRow]:
        return [row for row in self.rows if row.missing]

    @property
    def prose(self) -> list[ReviewRow]:
        return [row for row in self.rows if not row.checkable]

    def render(self, lean: bool = False) -> str:
        """Every requirement sentence, numbered, once. Shown in full because
        the requirement a run misses is not predictable: all 7 misses in run
        36359464192 were ordinary sentences, 3 of them inside one paragraph."""
        head = ("[GT] before you submit: the task's requirements, one per line. For EACH, "
                "confirm your change implements it and a test asserts it with the values the "
                "task states (names, order, messages, precedence) - not values copied from your "
                "own output. Fix what is missing, then submit again; this review appears once.")
        if lean:
            head += (" Lines marked [not in your changes] or [task: ...; yours: ...] are the ones "
                     "to check first; if every line is already implemented and tested, submit again now.")
        tail = []
        if self.tests:
            tail = ["", "Existing tests that reach the files you changed (run them): "
                    + ", ".join(self.tests[:MAX_TESTS_SHOWN])]
        rendered = {index: row.line(index) for index, row in enumerate(self.rows, start=1)}
        # Over the cap, plain rows go first (last to first), never a flagged
        # or limiting one, and the dropped numbers are named.
        droppable = [index for index, row in enumerate(self.rows, start=1)
                     if not (row.missing or row.shapes or row.constraint)]
        dropped: list[int] = []

        def compose() -> str:
            notice = ([f"... requirements {_ranges(dropped)} not shown "
                       "(plain descriptions; re-read them in the task)"] if dropped else [])
            return "\n".join([head, "", *rendered.values(), *notice, *tail])

        text = compose()
        while droppable and len(text.encode("utf-8")) > MAX_REVIEW_BYTES:
            index = droppable.pop()
            dropped.append(index)
            del rendered[index]
            text = compose()
        encoded = text.encode("utf-8")
        if len(encoded) <= MAX_REVIEW_BYTES:
            return text
        return encoded[:MAX_REVIEW_BYTES].decode("utf-8", "ignore").rsplit("\n", 1)[0] + \
            "\n... (more requirements in the task text; re-read it)"


def _ranges(numbers: list[int]) -> str:
    """[3, 4, 5, 9] -> '3-5, 9'."""
    spans: list[tuple[int, int]] = []
    for number in sorted(numbers):
        if spans and spans[-1][1] == number - 1:
            spans[-1] = (spans[-1][0], number)
        else:
            spans.append((number, number))
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in spans)


def literals_of(text: str) -> tuple[str, ...]:
    """The code literals a requirement line names, in order, de-duplicated."""
    found: list[str] = []
    for match in _BACKTICK.finditer(text):
        found.append(match.group(1).strip())
    stripped = _BACKTICK.sub(" ", text)
    for match in _QUOTED.finditer(stripped):
        found.append(match.group(2).strip())
    for match in _FLAG.finditer(stripped):
        found.append(match.group(1))
    for match in _DOTNAME.finditer(stripped):
        found.append(match.group(1))
    for match in _IDENT.finditer(_QUOTED.sub(" ", stripped)):
        found.append(match.group(1))
    out: list[str] = []
    for item in found:
        if len(item) >= 2 and item not in out:
            out.append(item)
    return tuple(out)


def requirement_lines(issue_text: str) -> list[str]:
    """Every requirement sentence of the task, in order.

    Not the plan's ledger: its workflow-noise filter dropped real
    requirements (bandit-taint's safe-call list and two sink rules, run
    36359464192) while keeping "work on this in a new branch". Here fenced
    code is removed, each line is split into sentences, and only process
    sentences (branches, commits, how to use the shell) are dropped."""
    text = _FENCED.sub("\n", issue_text or "")
    rows: list[str] = []
    for raw in text.splitlines():
        line = _BULLET.sub("", raw).strip()
        if not line or line.startswith("#"):
            continue
        for sentence in _SENTENCE.split(" ".join(line.split())):
            sentence = sentence.strip()
            if sentence.lower().startswith("please solve this issue:"):
                sentence = sentence.split(":", 1)[1].strip()
            # Code quoted in backticks is part of a requirement ("a curried form
            # `(task) => result`"), not a sign the line is code: true-myth's tap
            # sentence was dropped whole for its `=>` (run 36450157395).
            prose = _BACKTICK.sub("`x`", sentence)
            if (_PLACEHOLDER_LINE.match(sentence) or len(sentence) < 12 or _CODE_LINE.search(prose)
                    or _PROCESS.search(sentence) or sentence in rows):
                continue
            rows.append(sentence)
    return rows


def _present(literal: str, haystack: str) -> bool:
    """A literal is present when it appears verbatim, or when every word of it
    does once template placeholders are removed (a signature or message
    template is never written verbatim in code)."""
    if literal in haystack:
        return True
    tail = literal.rsplit(".", 1)[-1]
    if len(tail) >= 4 and tail != literal and tail in haystack:
        return True  # a dotted name used as an attribute
    atoms = _ATOM.findall(_PLACEHOLDER.sub(" ", literal))
    return bool(atoms) and all(atom in haystack for atom in atoms)


def _param_names(params: str) -> tuple[str, ...]:
    """Parameter names of a signature, types/defaults dropped. Brackets inside
    types (``fn: (v: T) => void``) are skipped when splitting on commas."""
    names: list[str] = []
    depth, current = 0, ""
    for char in params + ",":
        if char in "([{<":
            depth += 1
        elif char in ")]}>" and depth:
            depth -= 1
        if char == "," and depth == 0:
            match = _PARAM_NAME.match(current)
            if match and match.group(1) not in {"self", "cls", "this"}:
                names.append(match.group(1))
            current = ""
        else:
            current += char
    return tuple(names)


def _definitions(name: str, added_text: str) -> list[tuple[str, ...]]:
    """Parameter lists of every definition of ``name`` in the added code
    (def / function / fn / func / method / arrow assignment)."""
    short = re.escape(name.rsplit(".", 1)[-1])
    head = re.compile(
        rf"(?:\b(?:def|function|fn|func)\s+{short}|(?:^|[\s;]){short}\s*[:=]\s*(?:async\s*)?(?:function\s*)?"
        rf"|^\s*(?:(?:public|private|protected|static|async|export|override)\s+)*{short})\s*(?:<[^()]*?>)?\s*\(",
        re.M)
    found: list[tuple[str, ...]] = []
    for match in head.finditer(added_text):
        depth, end = 1, match.end()
        while end < len(added_text) and depth:
            depth += {"(": 1, ")": -1}.get(added_text[end], 0)
            end += 1
        found.append(_param_names(added_text[match.end():end - 1]))
    return found


def _shape_notes(literals: tuple[str, ...], added_text: str) -> tuple[str, ...]:
    notes: list[str] = []
    for literal in literals:
        match = _SIGNATURE.match(literal)
        if not match:
            continue
        wanted = _param_names(match.group(2))
        defined = [params for params in _definitions(match.group(1), added_text) if params]
        if len(wanted) < 2 or not defined or wanted in defined:
            continue
        short = match.group(1).rsplit(".", 1)[-1]
        # Of several overloads, the one with the task's arity is the contrast that matters.
        closest = next((params for params in defined if len(params) == len(wanted)), defined[0])
        notes.append(f"task: {short}({', '.join(wanted)}); yours: {short}({', '.join(closest)})")
    return tuple(notes)


def build_review(issue_text: str, added_text: str, tests: list[str] | None = None) -> SubmitReview:
    review = SubmitReview(tests=list(tests or []))
    for text in requirement_lines(issue_text):
        literals = literals_of(text)
        missing = tuple(lit for lit in literals if not _present(lit, added_text))
        review.rows.append(ReviewRow(text=text, literals=literals, missing=missing,
                                     shapes=_shape_notes(literals, added_text)))
    return review


def added_text_since(repo_root: str, baseline: str) -> str:
    """Every line the agent added since ``baseline`` (committed or not), plus
    the full text of new untracked files."""
    root = Path(repo_root)
    parts: list[str] = []
    try:
        ref = baseline or "HEAD"
        diff = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(root), "diff", "--no-color", "-U0", ref],
                              capture_output=True, text=True, timeout=30, errors="replace").stdout
        parts += [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
        untracked = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
                                   capture_output=True, text=True, timeout=30, errors="replace").stdout
        for rel in untracked.splitlines()[:200]:
            path = root / rel
            try:
                if path.is_file() and path.stat().st_size < 400_000:
                    parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    except (OSError, subprocess.SubprocessError):
        return ""
    return "\n".join(parts)


def changed_files_since(repo_root: str, baseline: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "-C", repo_root, "diff", "--name-only", baseline or "HEAD"],
                             capture_output=True, text=True, timeout=30, errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def session_review(session: "GTSession", baseline: str) -> str:
    """The rendered review for this session, or '' when there is nothing to say."""
    engine = getattr(session, "_engine", None)
    issue = str(getattr(engine, "issue_text", "") or "")
    root = str(getattr(engine, "repo_root", "") or "")
    if not issue.strip() or not root:
        return ""
    from gt_engine.workspace_gate import has_start_commit

    # The review diffs against the task's starting commit. Without one (no
    # repository, no git, a refused repository, an empty `git init`) the diff is
    # empty and every task literal would read "[not in your changes]"
    # (qemu-startup, campaign 2026-09-29), so there is no review. ``baseline`` is
    # the commit taken at session start; an empty one means the same.
    if not baseline or not has_start_commit(root):
        return ""
    tests: list[str] = []
    try:
        from gt_engine.tool_server import _tests_by_reachability

        changed = changed_files_since(root, baseline)
        tests = [f"{row.get('file_path')}::{row.get('name')}" for row in
                 _tests_by_reachability(session, changed)[:MAX_TESTS_SHOWN]] if changed else []
    except Exception:  # noqa: BLE001 - tests are advisory
        tests = []
    review = build_review(issue, added_text_since(root, baseline), tests)
    if not review.rows:
        return ""
    if review_mode() == "flagged":
        # Lean review: the post-review tail was 15.7% of GT's tokens on
        # SWE-Live Lite (run 36512659501/36512661736) while the solve lift was
        # the same whether or not the agent edited after it. Hold the submit
        # only when the review found something concrete to fix.
        if not any(row.missing or row.shapes for row in review.rows):
            return ""
        return review.render(lean=True)
    return review.render()


REVIEW_MODE_ENV = "GT_SUBMIT_REVIEW_MODE"


def review_mode() -> str:
    """``always`` (hold the first submit with the full review) or ``flagged``
    (hold it only when a requirement literal or signature is missing)."""
    import os

    mode = os.environ.get(REVIEW_MODE_ENV, "always").strip().lower()
    return mode if mode in ("always", "flagged") else "always"


__all__ = ["SubmitReview", "build_review", "literals_of", "requirement_lines", "session_review"]
