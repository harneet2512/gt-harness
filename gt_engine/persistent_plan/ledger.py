"""Verbatim, line-level requirement ledger for the persistent task plan.

``task_contract.extract_task_contract`` is precision-biased: it merges prose
into sentences, drops lines that carry no directive verb, and truncates rows at
500 characters.  Measured on the awilix prompt it produced 13 obligations in
which the Assumptions block was glued into two run-on rows -- the requirement
the run actually missed (the parent container's singletons are not
reinitialized) sat mid-sentence inside one of them -- and the four ``Api:``
result-shape lines vanished entirely.  A ledger row is therefore ONE SOURCE
LINE, kept byte for byte, so a completeness miss is a row a reader can point at.

This module never reads tests, the fail-to-pass list, or anything under the
benchmark harness.  Its only input is the prompt the agent already receives.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from ..task_contract import (
    Obligation,
    TaskContract,
    TaskMode,
    _clean,
    _is_workflow_noise,
    _key,
    _leaks_test_identity,
    _subjects,
    _task_mode,
    _typed_predicates,
    significant_tokens,
)

_FENCE_RE = re.compile(r"^\s*(?P<marker>`{3,}|~{3,})(?P<tail>.*)$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(?P<text>.+?)\s*$")
_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*(?P<hash>.+?)|\*\*(?P<bold>[^*]+)\*\*)\s*:?\s*$"
)
_LABEL_RE = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z /_-]{2,40}):\s*$")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# Sentence boundary INSIDE one physical line. Splitting here is lossless: the
# pieces rejoin with a single space to reproduce the cleaned line, and the test
# suite pins that. Merging ACROSS lines is what produced the awilix miss and is
# never done. A period with no following space (result.metrics.database.level)
# is not a boundary.
# Any sentence boundary, not only one followed by a capital. Measured: a real
# prompt continued with "--clear-cache is no-op..." and "cache_expiration...",
# so a capital-letter lookahead swallowed two requirements into the sentence
# before them. A period with no following whitespace (1.5, result.metrics) is
# still not a boundary.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=\S)")
# An enumerated list packed into ONE line. Measured on a real prompt: eight
# method requirements, six CLI subcommands and five web endpoints arrived as
# three lines, so a line-level ledger tracked three things instead of
# nineteen. That is the same swallowing the sentence extractor does, one level
# up, and it hides requirements exactly as effectively.
_LIST_LABEL_RE = re.compile(r"^(?P<label>[^:]{3,200}):\s+(?P<items>.+)$")
# Phrases that introduce a multi-part SHAPE rather than a new list item, so the
# bare words after them belong to the item that declared the shape.
_SHAPE_INTRODUCER_RE = re.compile(
    r"(?:with|returns?|returning|containing|contains|including|of)\s+\S+$"
)
_IDENTIFIERISH_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\s*\(|[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+|/[A-Za-z_]"
)

# Sections whose content is context rather than requirement. The same three
# names ``task_contract._normative_issue_text`` drops, so the two views of the
# prompt cannot disagree about what is normative.
NON_NORMATIVE_SECTIONS = frozenset({"background", "baseline", "cost model"})

# A line that is only brackets, commas or operators carries no requirement even
# inside a normative region (the closing "})" of an API sketch).
_STRUCTURAL_ONLY_RE = re.compile(r"^[\s{}()\[\];,.:=<>|&+*/\\-]*$")

MAX_ROW_CHARS = 500


@dataclass(frozen=True)
class LedgerRow:
    """One normative source line, verbatim."""

    row_id: str
    text: str
    section: str
    line_no: int
    shape: str
    sentence_index: int = 0
    # The label an enumerated item was listed under, kept beside the verbatim
    # item so a row like "get_snapshot" still says what it is a member of.
    context: str = ""
    subjects: tuple[str, ...] = ()
    tokens: tuple[str, ...] = ()
    obligation_ids: tuple[str, ...] = ()
    source_text: str = ""
    examples: tuple[str, ...] = ()

    @property
    def is_ledger_only(self) -> bool:
        """True when no extracted obligation covers this line."""
        return not self.obligation_ids

    def as_dict(self) -> dict:
        return {
            "row_id": self.row_id,
            "text": self.text,
            "section": self.section,
            "line_no": self.line_no,
            "shape": self.shape,
            "sentence_index": self.sentence_index,
            "context": self.context,
            "subjects": list(self.subjects),
            "obligation_ids": list(self.obligation_ids),
            "source_text": self.source_text,
            "examples": list(self.examples),
        }


@dataclass(frozen=True)
class Ledger:
    rows: tuple[LedgerRow, ...] = ()
    skipped: tuple[tuple[int, str], ...] = ()
    fenced_lines: int = 0
    unclassified_spans: tuple[tuple[int, str], ...] = ()
    source_spans: tuple[tuple[int, str], ...] = ()

    def __iter__(self):
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def ledger_only(self) -> tuple[LedgerRow, ...]:
        return tuple(row for row in self.rows if row.is_ledger_only)

    def by_id(self, row_id: str) -> LedgerRow | None:
        for row in self.rows:
            if row.row_id == row_id:
                return row
        return None

    def counts(self) -> dict[str, int]:
        return {
            "rows": len(self.rows),
            "linked_rows": sum(1 for row in self.rows if row.obligation_ids),
            "ledger_only_rows": len(self.ledger_only),
            "skipped": len(self.skipped),
            "fenced_lines": self.fenced_lines,
        }


def row_id_for(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
    return "req-" + digest[:12]


def _section_name(line: str) -> str | None:
    """Return a normalised section label when the line is a heading, else None."""
    heading = _HEADING_RE.match(line)
    if heading:
        name = heading.group("hash") or heading.group("bold") or ""
        return _clean(name).strip("#*: ").lower()
    label = _LABEL_RE.match(line)
    if label:
        return _clean(label.group("label")).lower()
    return None


def _shape(raw: str) -> str:
    if _BULLET_RE.match(raw):
        return "bullet"
    if raw[:1].isspace():
        return "indented"
    return "prose"


def _split_top_level(text: str, separator: str = ",") -> tuple[str, ...]:
    """Split on a separator, ignoring anything inside brackets or backticks.

    ``save(--name, echoed in output), list(ls), show`` is three items, not five:
    a comma inside parentheses belongs to the item that owns it.
    """
    parts: list[str] = []
    depth = 0
    tick = False
    current: list[str] = []
    for char in text:
        if char == "`":
            tick = not tick
        elif not tick and char in "([{":
            depth += 1
        elif not tick and char in ")]}":
            depth = max(0, depth - 1)
        if char == separator and depth == 0 and not tick:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return tuple(part.strip() for part in parts if part.strip())


def _split_enumerated(text: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(label, items)`` when a line is really a list, else ``("", ())``.

    Fires only on a genuine enumeration: a label, three or more items, and at
    least half of them carrying something identifier-shaped. Ordinary prose with
    commas is left whole, because fragments of it would assert nothing.
    """
    match = _LIST_LABEL_RE.match(text)
    if not match:
        return "", ()
    items = _merge_trailing_clauses(_split_top_level(match.group("items")))
    if len(items) < 3:
        return "", ()
    identifierish = sum(1 for item in items if _IDENTIFIERISH_RE.search(item))
    if identifierish < max(2, len(items) // 2):
        return "", ()
    return _clean(match.group("label")), items


def _merge_trailing_clauses(items: tuple[str, ...]) -> tuple[str, ...]:
    """Reattach a fragment that names nothing to the item it belongs to.

    ``format_snapshot_diff(a, b) returning an object with added, removed,
    common lists`` is ONE requirement whose return shape happens to contain
    commas. Split naively it becomes "…with added", "removed", "common lists" --
    three rows that assert nothing on their own. Rejoining with ", "
    reconstructs the original substring exactly.
    """
    merged: list[str] = []
    in_shape = False
    for item in items:
        identifierish = bool(_IDENTIFIERISH_RE.search(item))
        if identifierish:
            merged.append(item)
            in_shape = bool(_SHAPE_INTRODUCER_RE.search(item))
            continue
        if merged and in_shape:
            merged[-1] = f"{merged[-1]}, {item}"
            continue
        # A bare word that continues no shape is its own requirement. Losing
        # "show, where, diff, delete" from a CLI list is precisely the
        # completeness failure this ledger exists to prevent, so the merge is
        # deliberately narrow: it only absorbs the tail of a declared shape.
        merged.append(item)
    return tuple(merged)


def _split_sentences(line_text: str) -> tuple[str, ...]:
    """Sentence pieces of ONE physical line, each kept verbatim.

    Lossless by construction: ``" ".join(_split_sentences(x)) == x`` for any
    cleaned line, which the suite pins. This is the only place a row is finer
    than a source line, and it never crosses a line boundary.
    """
    parts = tuple(part for part in _SENTENCE_SPLIT_RE.split(line_text) if part)
    return parts or (line_text,)


def _carries_requirement(text: str) -> bool:
    if _STRUCTURAL_ONLY_RE.match(text):
        return False
    return bool(_IDENTIFIER_RE.search(text))


def build_requirement_ledger(
    issue_text: str, contract: TaskContract | None = None
) -> Ledger:
    """Split the prompt into verbatim normative lines.

    Every non-blank line outside a fenced block and outside a non-normative
    section becomes a row unless it is a heading, a duplicate, workflow noise,
    structurally empty, or a line that leaks test identity.  Bullet markers are
    stripped from the stored text (they are syntax, not requirement) and runs of
    whitespace are collapsed; nothing else is rewritten -- no sentence
    splitting, no merging, no truncation.
    """
    rows: list[LedgerRow] = []
    skipped: list[tuple[int, str]] = []
    seen: set[str] = set()
    section = ""
    fence_marker = ""
    example_start = 0
    fenced_lines = 0
    example_lines: list[str] = []
    unclassified: list[tuple[int, str]] = []

    for line_no, raw in enumerate((issue_text or "").splitlines(), start=1):
        fence = _FENCE_RE.match(raw)
        closes = bool(fence and fence_marker and fence.group("marker")[0] == fence_marker[0]
                      and len(fence.group("marker")) >= len(fence_marker) and not fence.group("tail").strip())
        if fence and (not fence_marker or closes):
            example_lines.append(raw)
            if closes:
                example = "\n".join(example_lines)
                if rows:
                    rows[-1] = replace(rows[-1], examples=rows[-1].examples + (example,))
                else:
                    unclassified.append((example_start, example))
                example_lines = []
                fence_marker = ""
            else:
                fence_marker = fence.group("marker")
                example_start = line_no
            continue
        if fence_marker:
            fenced_lines += 1
            example_lines.append(raw)
            continue
        if not raw.strip():
            continue
        name = _section_name(raw)
        if name is not None:
            section = name
            continue
        if section in NON_NORMATIVE_SECTIONS:
            skipped.append((line_no, "non_normative_section"))
            continue

        bullet = _BULLET_RE.match(raw)
        line_text = _clean(bullet.group("text") if bullet else raw)
        if not line_text:
            continue
        shape = _shape(raw)
        pieces: list[tuple[str, str]] = []
        for sentence in _split_sentences(line_text):
            label, items = _split_enumerated(sentence)
            if not label:
                pieces.append((sentence, ""))
                continue
            pieces.extend((item, label) for item in items)
        for sentence_index, (text, context) in enumerate(pieces):
            if not _carries_requirement(text):
                skipped.append((line_no, "structural_only"))
                continue
            if _is_workflow_noise(text):
                skipped.append((line_no, "workflow_noise"))
                continue
            if _leaks_test_identity(text):
                skipped.append((line_no, "leaks_test_identity"))
                continue
            key = _key(text)
            if not key or key in seen:
                skipped.append((line_no, "duplicate"))
                continue
            seen.add(key)
            rows.append(
                LedgerRow(
                    row_id=row_id_for(text),
                    text=text,
                    section=section,
                    line_no=line_no,
                    shape=shape,
                    sentence_index=sentence_index,
                    context=context,
                    source_text=raw,
                    # The label is part of the requirement's identity: an item
                    # listed under "Monitor methods" is about Monitor even
                    # though the item text never repeats the word.
                    subjects=_subjects(f"{context} {text}" if context else text),
                    tokens=significant_tokens(f"{context} {text}" if context else text),
                )
            )

    if contract is not None:
        rows = _link_obligations(rows, contract)
    if example_lines:
        if rows:
            rows[-1] = replace(rows[-1], examples=rows[-1].examples + ("\n".join(example_lines),))
        else:
            unclassified.append((example_start, "\n".join(example_lines)))
    raw_lines = (issue_text or "").splitlines()
    unclassified.extend((line, raw_lines[line - 1]) for line, reason in skipped
                        if reason not in {"leaks_test_identity", "workflow_noise", "duplicate"})
    return Ledger(rows=tuple(rows), skipped=tuple(skipped), fenced_lines=fenced_lines,
                  unclassified_spans=tuple(unclassified),
                  source_spans=tuple((number, raw) for number, raw in enumerate(raw_lines, 1)
                                     if not _is_workflow_noise(raw) and not _leaks_test_identity(raw)))


def _link_obligations(
    rows: list[LedgerRow], contract: TaskContract
) -> list[LedgerRow]:
    """Attach the obligations each row is covered by, if any.

    An obligation covers a row when one normalised text contains the other.
    ``extract_task_contract`` merges lines, so one obligation routinely covers
    several rows -- that is exactly the merge this ledger undoes, and the link
    is what lets a merged obligation's evidence credit each line it swallowed.
    """
    obligation_keys = [
        (obligation.obligation_id, _key(obligation.text))
        for obligation in contract.obligations
    ]
    linked: list[LedgerRow] = []
    for row in rows:
        row_key = _key(row.text)
        matches = tuple(
            sorted(
                obligation_id
                for obligation_id, obligation_key in obligation_keys
                if obligation_key
                and (row_key in obligation_key or obligation_key in row_key)
            )
        )
        # ``replace`` rather than a fresh constructor: rebuilding the row field
        # by field silently dropped sentence_index the first time this was
        # written, and only the path that passes a contract showed it.
        linked.append(replace(row, obligation_ids=matches))
    return linked


def merged_plan_contract(
    contract: TaskContract, ledger: Ledger, issue_text: str = ""
) -> TaskContract:
    """The original contract plus the prompt lines nothing was tracking.

    Merged rather than kept alongside: ``evaluate_passing_observation`` walks
    ``contract.obligations``, so an obligation outside the contract can never be
    proven. Tracking a requirement that can never turn green would block the
    completion predicate permanently -- strictly worse than not tracking it.

    Returns the original object unchanged when there is nothing to add, so the
    caller can tell whether anything actually moved.
    """
    extra = ledger_only_contract(ledger, issue_text).obligations
    if not extra:
        return contract
    known = {obligation.obligation_id for obligation in contract.obligations}
    additions = tuple(item for item in extra if item.obligation_id not in known)
    if not additions:
        return contract
    obligations = contract.obligations + additions
    return TaskContract(
        role=contract.role,
        obligations=obligations,
        task_mode=contract.task_mode,
        predicates=_typed_predicates(obligations, contract.task_mode),
    )


def ledger_only_contract(ledger: Ledger, issue_text: str = "") -> TaskContract:
    """A synthetic contract over the rows no extracted obligation covers.

    These rows carry no predicate today, which is precisely how a requirement
    written verbatim in the prompt reaches submission with nothing tracking it.
    Compiling them through the normal obligation compiler gives them the same
    status, invalidation and re-verification machinery every other predicate
    already has.
    """
    obligations = tuple(
        Obligation(
            obligation_id="plan-" + row.row_id.removeprefix("req-"),
            text=row.text,
            source="persistent_plan",
            subjects=row.subjects,
        )
        for row in ledger.ledger_only
    )
    mode = _task_mode(issue_text) if issue_text else TaskMode.PATCH
    return TaskContract(
        role="code_behavior",
        obligations=obligations,
        task_mode=mode,
        predicates=_typed_predicates(obligations, mode),
    )
