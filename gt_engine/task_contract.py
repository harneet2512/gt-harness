"""Deterministic, graph-independent task contract for the Mini-SWE seam.

The production brief extractor is deliberately precision-biased and then
filters obligations through localized graph anchors.  That is appropriate for
one evidence capsule, but it is not a complete SDLC contract: repository-wide
requirements and short Markdown bullets can disappear.  This module keeps the
full leak-screened normative set internally and renders a bounded native
checklist for the model.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_DIRECTIVE_RE = re.compile(
    r"(?i)\b(?:must|should|required|ensure|implement|create|install|support|"
    r"supports|has support|keep|do not|don't|never|be careful|has to|need to|"
    r"make sure|call your|put it in|produce|generate|replace|remove|reconstruct|"
    r"source the|mimics?)\b"
)
_CONTENT_SCAN_RE = re.compile(
    r"(?i)\b(?:saniti[sz]e|api keys?|credentials?|secrets?|sensitive values?|"
    r"remove all|replace the actual value|repository after)\b"
)
_DATA_TRANSFORM_RE = re.compile(
    r"(?i)\b(?:dataset|jsonl|batch(?:ing)?|reshard|compress|decompress|"
    r"input_data|output_data|plan_b\d|transform)\b"
)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{2,}")
_STOPWORDS = frozenset(
    {
        "about",
        "actual",
        "after",
        "also",
        "because",
        "before",
        "called",
        "careful",
        "common",
        "could",
        "every",
        "example",
        "following",
        "found",
        "functionality",
        "github",
        "implementation",
        "information",
        "install",
        "interface",
        "make",
        "present",
        "provided",
        "repository",
        "should",
        "supports",
        "system",
        "their",
        "there",
        "these",
        "those",
        "values",
        "where",
        "which",
        "with",
        "your",
    }
)


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    text: str
    source: str
    subjects: tuple[str, ...] = ()


class TaskMode(StrEnum):
    PATCH = "PATCH"
    BUILD_INSTALL = "BUILD_INSTALL"
    ARTIFACT = "ARTIFACT"
    SERVICE = "SERVICE"
    DATA_TRANSFORM = "DATA_TRANSFORM"
    MIXED = "MIXED"


@dataclass(frozen=True)
class TypedPredicate:
    predicate_id: str
    mode: TaskMode
    description: str
    phase: str
    dependencies: tuple[str, ...] = ()
    freshness_epoch: int = 0


@dataclass(frozen=True)
class TaskContract:
    role: str
    obligations: tuple[Obligation, ...]
    task_mode: TaskMode = TaskMode.PATCH
    predicates: tuple[TypedPredicate, ...] = ()


def _clean(text: str) -> str:
    return " ".join((text or "").strip().split())


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _subjects(text: str) -> tuple[str, ...]:
    found: set[str] = set()
    for token in _IDENT_RE.findall(text or ""):
        low = token.lower().strip(".-")
        if len(low) < 3 or low in _STOPWORDS:
            continue
        if "_" in token or "." in token or any(ch.isupper() for ch in token[1:]):
            found.add(token.strip("`'\".,:;()"))
    return tuple(sorted(found, key=str.lower))


def significant_tokens(text: str) -> tuple[str, ...]:
    """Stable lexical anchors safe for FTS and check-to-obligation mapping."""
    tokens: set[str] = set()
    for token in _IDENT_RE.findall(text or ""):
        low = token.lower().strip(".-")
        if len(low) >= 4 and low not in _STOPWORDS and not low.isdigit():
            tokens.add(low)
    return tuple(sorted(tokens))


_WORKFLOW_NOISE_RE = re.compile(
    r"(?i)^(?:read|learn|recall|study|review|analyze|explore|familiarize|"
    r"understand|make sure you|be sure to|take a look|look at|navigate|"
    r"inspect|run)\b.*\b(?:carefully|first|before|repository|code|knowledge|"
    r"issue)\b"
)
_CATALOG_NOISE_RE = re.compile(
    r"(?i)(?:cwe-[0-9]+|input validation|cross-site|script attacks|"
    r"injection|escape|sanitize|improper encoding|common weakness enumeration)"
)
_WORKFLOW_STEP_RE = re.compile(
    r"^\s*\d+[.)]\s*(?:read|learn|recall|identify|fix|create|run|verify|"
    r"check|learn or recall|find|locate|search|use|install|setup)\b"
)
# Harness boilerplate: the benchmark wrapper's own instructions about where the
# checkout lives and what to do in it. These minted plan rows on dynaconf-1241
# (run 34919574013) - "You are working in the `dynaconf/dynaconf` repository,
# checked out at `/testbed`." and "Investigate the issue described above and
# modify the code under `/testbed` to resolve it." both became requirements
# bound to checks, and a process instruction can never be proven by a test.
_HARNESS_PREAMBLE_RE = re.compile(
    r"(?i)(?:^you are working (?:in|on|inside)\b.*\brepositor|"
    r"^investigate the issue\b|"
    r"^please investigate\b|"
    r"\bchecked out at\b|"
    r"^modify the code under\b|"
    r"^resolve the issue\b)"
)
# Markers of an agent-process directive: instructions about the working and
# submission workflow (where to work, when to commit, what to open) rather than
# behaviour the code must have. One marker inside a longer technical sentence is
# not enough -- "the CLI must open a pull request" is a real requirement -- but
# a line built from two or more of them is never normative content. Measured:
# every DeepSWE task text ends with "IMPORTANT: Please work on this in a new
# branch from main and commit everything when you are done." (4 markers), which
# landed as an unprovable plan row and made `verified` unreachable on all 20.
_PROCESS_MARKERS = (
    r"\bwork on this\b",
    r"\bnew branch\b",
    r"\bwhen you are done\b",
    r"\bcommit everything\b",
    r"\bopen a pull request\b",
    r"\bsubmit (?:your|the)\s+(?:work|changes|patch|solution|assignment)\b",
    r"\bpush (?:your|the)\s+(?:work|changes|branch|commits?)\b",
)


def _is_process_directive(text: str) -> bool:
    low = text or ""
    markers = sum(
        1 for pattern in _PROCESS_MARKERS if re.search(pattern, low, re.IGNORECASE)
    )
    return markers >= 2 or bool(
        re.search(r"\bwork on this in a\b", low, re.IGNORECASE)
    )


def _is_workflow_noise(text: str) -> bool:
    """Exclude procedural/workflow bullets and CWE catalog rows from the contract.

    These are process guidance or reference material, not normative
    requirements. Keeping them pollutes the model-visible prompt (measured
    gton11 fix-code: 'read and analyze the repository carefully', a full CWE
    catalog) and makes the verifier ontology incoherent.
    """
    low = (text or "").strip()
    if _WORKFLOW_NOISE_RE.search(low):
        return True
    if _CATALOG_NOISE_RE.search(low):
        return True
    if _HARNESS_PREAMBLE_RE.search(low):
        return True
    return bool(_WORKFLOW_STEP_RE.match(low))


# A line that is only a section marker in issue prose. The heading patterns
# catch "## Expected" and "Expected:"; they do NOT catch a bare "Expected" or
# "Result" line, which then minted rows and obligations bound to unprovable
# checks. Measured twice on dynaconf-1241: run 34919574013 put "Expected" and
# "Result" into the persistent plan (bound to the whole suite and to three
# same-named app_test.py files); run 34925475946 then put "Expected" into the
# task CONTRACT via extract_spec_v2's normative region, where it compiled to
# a behavior predicate with no expected_relation - structurally
# undischargable, unmet on every tree, verified unreachable. The two
# extractors must share one definition of "not a requirement" or a line one
# rejects becomes an orphan predicate the other can never link.
_SECTION_MARKER_WORDS = frozenset({
    "expected", "actual", "result", "results", "output", "outcome",
    "reproduction", "repro", "reproducer", "description", "summary",
    "context", "problem", "issue", "solution", "note", "notes",
    "environment", "version", "versions", "log", "logs", "traceback",
    "error", "errors", "example", "examples", "motivation", "related",
    "references", "screenshot", "screenshots", "demo", "demonstration",
    "question", "answer", "goal", "setup", "dependency", "dependencies",
    "evidence", "observation", "impact", "severity", "workaround",
    "background", "details", "proposed", "rationale",
})
_SECTION_MARKER_PHRASE_RE = re.compile(
    r"(?i)^(?:expected|actual|current|desired|intended|observed)\s+"
    r"(?:behaviou?r|results?|output|response|error|issue|value)\.?$"
    r"|^(?:steps? to reproduce|how to reproduce|to reproduce|"
    r"minimal (?:reproducible )?example|"
    r"additional (?:context|information)|related issues?|"
    r"what (?:should|was expected to|actually)\s+\w+.*)\.?$"
)


def _section_marker_name(text: str) -> str | None:
    """The marker label when a cleaned line is only a section marker, else None."""
    low = text.strip().lower().rstrip(":.")
    if low in _SECTION_MARKER_WORDS:
        return low
    if _SECTION_MARKER_PHRASE_RE.match(low):
        return low
    return None


def _markdown_candidates(issue_text: str) -> list[tuple[str, str]]:
    """Return (source, text) candidates, excluding fenced examples."""
    candidates: list[tuple[str, str]] = []
    prose: list[str] = []
    fenced = False
    section = ""
    requirement_table = False

    def flush() -> None:
        if not prose:
            return
        paragraph = _clean(" ".join(prose))
        prose.clear()
        if not paragraph:
            return
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", paragraph):
            sentence = _clean(sentence)
            if not sentence or not _DIRECTIVE_RE.search(sentence):
                continue
            # Strip a trailing numbered workflow step glued to the paragraph
            # (e.g. "...according to CWE. 1. read and analyze the repository").
            sentence = _WORKFLOW_STEP_RE.sub("", sentence)
            sentence = _clean(sentence)
            if sentence:
                candidates.append(("directive", sentence.rstrip(".")))

    for raw in (issue_text or "").splitlines():
        if _FENCE_RE.match(raw):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _BULLET_RE.match(raw)
        if match:
            flush()
            text = _clean(match.group("text"))
            if section in {"background", "baseline", "cost model"}:
                continue
            if (
                text
                and not text.lower().startswith("example output")
                and "– an analytical cost" not in text.lower()
                and "– a slow baseline" not in text.lower()
                and not _is_workflow_noise(text)
            ):
                candidates.append(("markdown", text.rstrip(".")))
            continue
        stripped = raw.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("#") or (
            stripped.startswith("**") and stripped.endswith("**")
        ):
            flush()
            section = stripped.strip("#* ").lower()
            requirement_table = False
            continue
        if stripped.lower() in {
            "background",
            "goal",
            "baseline",
            "cost model",
            "deliverables",
            "example output format:",
        }:
            flush()
            section = stripped.strip(":").lower()
            requirement_table = False
            continue
        if "goal is to achieve metrics below the thresholds" in stripped.lower():
            flush()
            requirement_table = True
            candidates.append(("directive", stripped.rstrip(".")))
            continue
        if stripped.startswith("|"):
            flush()
            if (
                requirement_table
                and "---" not in stripped
                and "input file" not in stripped.lower()
            ):
                candidates.append(("table", _clean(stripped.strip("|"))))
            continue
        if section in {"background", "baseline", "cost model"}:
            continue
        prose.append(stripped)
    flush()
    return candidates


def _normative_issue_text(issue_text: str) -> str:
    """Remove explicitly non-normative Markdown sections before GT extraction."""
    kept: list[str] = []
    skip = False
    for raw in (issue_text or "").splitlines():
        stripped = raw.strip()
        heading = re.match(r"^\*\*(?P<name>[^*]+)\*\*(?P<tail>.*)$", stripped)
        if heading:
            name = heading.group("name").strip().lower()
            skip = name in {"background", "baseline", "cost model"}
            if not skip and heading.group("tail").strip():
                kept.append(heading.group("tail").strip())
            continue
        if not skip:
            kept.append(raw)
    return "\n".join(kept)


def _engine_candidates(issue_text: str) -> list[tuple[str, str]]:
    try:
        from groundtruth.pretask.spec import extract_spec_v2

        spec = extract_spec_v2(_normative_issue_text(issue_text))
        rows = spec.to_serializable(version=2)
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("region", "normative") != "normative":
            continue
        text = _clean(str(row.get("verbatim_text") or "")).rstrip(".")
        if text:
            out.append(("engine_v2", text))
    return out


def _leaks_test_identity(text: str) -> bool:
    try:
        from groundtruth.runtime.native_render import prose_leaks_test_identity

        return bool(prose_leaks_test_identity(text or ""))
    except Exception:
        return True


def _role(issue_text: str) -> str:
    if _CONTENT_SCAN_RE.search(issue_text or ""):
        return "content_scan"
    if _DATA_TRANSFORM_RE.search(issue_text or ""):
        return "data_transform"
    return "code_behavior"


def _task_mode(issue_text: str) -> TaskMode:
    text = (issue_text or "").lower()
    if re.search(r"\b(server|service|daemon|listen|endpoint|http)\b", text):
        return TaskMode.SERVICE
    if re.search(r"\b(install|build|compile|package|extension|import)\b", text):
        return TaskMode.BUILD_INSTALL
    if re.search(r"\b(output|artifact|file|schema|manifest|report)\b", text):
        return TaskMode.ARTIFACT
    if _DATA_TRANSFORM_RE.search(text):
        return TaskMode.DATA_TRANSFORM
    return TaskMode.PATCH


def _typed_predicates(
    obligations: tuple[Obligation, ...], mode: TaskMode
) -> tuple[TypedPredicate, ...]:
    return tuple(
        TypedPredicate(
            predicate_id=f"pred-{obligation.obligation_id}",
            mode=mode,
            description=obligation.text,
            phase="VERIFY",
        )
        for obligation in obligations
    )


def extract_task_contract(issue_text: str) -> TaskContract:
    """Extract the complete bounded task contract without requiring graph.db."""
    combined = _engine_candidates(issue_text) + _markdown_candidates(issue_text)
    seen: set[str] = set()
    obligations: list[Obligation] = []
    for source, raw in combined:
        text = _clean(raw)
        key = _key(text)
        low = text.lower().rstrip(":")
        if (
            not text
            or len(text) < 4
            or key in seen
            or low == "example output format"
            or low.endswith("following functionality")
            or low.endswith("pack these into batches so that")
            or low.endswith("replace it with placeholder values as follows")
            or _leaks_test_identity(text)
            or _is_workflow_noise(text)
            or _section_marker_name(text) is not None
        ):
            continue
        # Do not add nested copies of a row already retained.
        if any(key in existing or existing in key for existing in seen):
            continue
        seen.add(key)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        obligations.append(
            Obligation(
                obligation_id=f"obl-{digest}",
                text=text,
                source=source,
                subjects=_subjects(text),
            )
        )
    frozen = tuple(obligations)
    mode = _task_mode(issue_text)
    return TaskContract(
        role=_role(issue_text),
        obligations=frozen,
        task_mode=mode,
        predicates=_typed_predicates(frozen, mode),
    )


def render_task_contract(
    contract: TaskContract,
    *,
    max_chars: int,
) -> tuple[str, tuple[str, ...]]:
    """Render whole checklist rows until the hard byte surface is exhausted."""
    header = "Requirements to satisfy (complete GT task contract):"
    lines = [header]
    shipped: list[str] = []
    for item in contract.obligations:
        row = f"- [ ] {item.text}"
        candidate = "\n".join([*lines, row])
        if len(candidate) > max_chars:
            break
        lines.append(row)
        shipped.append(item.obligation_id)
    remaining = len(contract.obligations) - len(shipped)
    if not shipped:
        # Even a contract whose rows all exceed the surface is a fact the
        # model should see: requirements exist and gate submit.
        note = (
            f"- GT retained {remaining} requirement(s) for submit "
            "verification; none fit the byte surface."
        )
        if remaining and len("\n".join([*lines, note])) <= max_chars:
            return "\n".join([*lines, note]), ()
        return "", ()
    if remaining:
        note = f"- GT retained {remaining} additional requirement(s) for submit verification."
        if len("\n".join([*lines, note])) <= max_chars:
            lines.append(note)
    return "\n".join(lines), tuple(shipped)


def render_obligation_delta(
    contract: TaskContract,
    shipped_ids: Iterable[str],
    *,
    max_chars: int,
) -> tuple[str, tuple[str, ...]]:
    """Render missing obligations for a bounded corrective delivery.

    The full task contract remains authoritative outside the model-facing
    capsule. This delta exposes only rows not proven to have been shipped.
    """
    shipped = set(shipped_ids)
    remaining = [item for item in contract.obligations
                 if item.obligation_id not in shipped]
    header = "GT remaining contract obligations:"
    lines = [header]
    selected: list[str] = []
    for item in remaining:
        row = f"- [ ] {item.text}"
        candidate = "\n".join([*lines, row])
        if len(candidate) > max_chars:
            break
        lines.append(row)
        selected.append(item.obligation_id)
    if not selected:
        return "", ()
    lines.append("Check these obligations before submit; do not assume omitted rows are satisfied.")
    return "\n".join(lines)[:max_chars], tuple(selected)


def render_obligation_transitions(
    contract: TaskContract,
    transitions: Iterable[tuple[str, str]],
    *,
    max_chars: int,
) -> tuple[str, tuple[str, ...]]:
    """Render only obligation status changes since the last delivered delta.

    The full unmet checklist is delivered with the contract itself; a delta
    that re-lists every unmet row on each invalidation cycle re-sends the
    identical bytes the model already has. `transitions` is an iterable of
    ``(obligation_id, annotation)`` pairs in the order they should surface.
    """
    by_id = {item.obligation_id: item for item in contract.obligations}
    lines = ["GT contract obligation changes:"]
    selected: list[str] = []
    for obligation_id, annotation in transitions:
        item = by_id.get(obligation_id)
        if item is None:
            continue
        box = "[x]" if annotation == "satisfied" else "[ ]"
        row = f"- {box} {item.text} ({annotation})"
        candidate = "\n".join([*lines, row])
        if len(candidate) > max_chars:
            break
        lines.append(row)
        selected.append(obligation_id)
    if not selected:
        return "", ()
    lines.append("Unchanged obligations keep their last reported state.")
    return "\n".join(lines)[:max_chars], tuple(selected)


def matching_obligation_ids(
    contract: TaskContract,
    command: str,
    output: str,
) -> set[str]:
    """Conservative lexical map from an executed check to contract rows."""
    observed = set(significant_tokens(f"{command}\n{output}"))
    matched: set[str] = set()
    for item in contract.obligations:
        tokens = set(significant_tokens(item.text))
        threshold = 1 if len(tokens) <= 2 else 2
        if len(tokens & observed) >= threshold:
            matched.add(item.obligation_id)
    return matched
