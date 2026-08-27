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

from gt_engine.language_registry import is_validation_source

_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_DIRECTIVE_RE = re.compile(
    r"(?i)\b(?:must|should|required|ensure|add|implement|create|write|install|support|"
    r"supports|has support|keep|do not|don't|never|be careful|has to|need to|"
    r"make sure|call your|put it in|produce|generate|replace|remove|reconstruct|"
    r"source the|mimics?|fix|fixes|fixed|fixing|update|updates|updated|updating|"
    r"patch|patches|patched|patching|refactor|expose|exposes|normalize|normalizes|"
    r"improve|improves|improved|improving|"
    r"harden|hardens|hardened|hardening|bug|bugs|bugged)\b"
)

_HARNESS_SCAFFOLD_HEADINGS = frozenset(
    {
        "recommended workflow",
        "command execution rules",
        "useful command examples",
    }
)
_HARNESS_SCAFFOLD_START_RE = re.compile(
    r"(?i)^you can execute bash commands and edit files to implement the necessary changes\.?$"
)
_VERSION_CONTROL_SCAFFOLD_RE = re.compile(
    r"(?i)^important:\s*please\s+work\s+on\s+this\s+in\s+a\s+new\s+branch\b"
)


def _task_issue_core(issue_text: str) -> str:
    """Remove host-supplied agent instructions from the task's normative text.

    Terminal-Bench appends a generic workflow, tool protocol, submit marker,
    and command examples to the actual task.  Those rows govern the host loop;
    they are not task completion predicates.  The boundary is structural and
    deterministic rather than benchmark-task-specific.
    """

    kept: list[str] = []
    for raw in (issue_text or "").splitlines():
        stripped = raw.strip()
        if _VERSION_CONTROL_SCAFFOLD_RE.match(stripped):
            break
        if _HARNESS_SCAFFOLD_START_RE.fullmatch(stripped):
            break
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            if heading in _HARNESS_SCAFFOLD_HEADINGS:
                break
        kept.append(raw)
    return "\n".join(kept).strip()
_CONTENT_SCAN_RE = re.compile(
    r"(?i)\b(?:saniti[sz]e|api keys?|credentials?|secrets?|sensitive values?|"
    r"remove all|replace the actual value|repository after)\b"
)
_DATA_TRANSFORM_RE = re.compile(
    r"(?i)\b(?:dataset|jsonl|batch(?:ing)?|reshard|compress|decompress|"
    r"input_data|output_data|plan_b\d|transform)\b"
)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{2,}")
_CODE_MENTION_RE = re.compile(
    r"(?:`|'|\")(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:(?:::|[.#])[A-Za-z_][A-Za-z0-9_]*)*)(?:`|'|\")"
)
_BACKTICK_CALLABLE_RE = re.compile(
    r"`\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:(?:::|[.#])[A-Za-z_][A-Za-z0-9_]*)*)"
    r"\s*\([^`\r\n]*\)\s*`"
)
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


class DirectiveKind(StrEnum):
    """Normative operation requested by one task clause."""

    MODIFY = "MODIFY"
    ADD = "ADD"
    REMOVE = "REMOVE"
    PRESERVE = "PRESERVE"
    FORBID_EDIT = "FORBID_EDIT"
    INSPECT = "INSPECT"
    VALIDATE = "VALIDATE"


class MentionParticipation(StrEnum):
    """Whether a named artifact is an edit target, constraint, or context."""

    TARGET = "TARGET"
    CONSTRAINT = "CONSTRAINT"
    CONTEXT = "CONTEXT"


class TextAuthority(StrEnum):
    """Syntactic strength of an artifact mention in the task text."""

    PLAIN_PROSE = "PLAIN_PROSE"
    CODE_CITATION = "CODE_CITATION"
    QUALIFIED_CITATION = "QUALIFIED_CITATION"
    LITERAL_PATH = "LITERAL_PATH"


@dataclass(frozen=True)
class TaskMention:
    text: str
    participation: MentionParticipation
    authority: TextAuthority


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    text: str
    source: str
    subjects: tuple[str, ...] = ()
    directive_kind: DirectiveKind = DirectiveKind.MODIFY
    mentions: tuple[TaskMention, ...] = ()


class TaskResourceRole(StrEnum):
    """The mechanically supported relationship between a task and one path."""

    INPUT = "input"
    OUTPUT = "output"
    REFERENCE = "reference"
    EXECUTABLE = "executable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TaskResource:
    path: str
    role: TaskResourceRole
    mutable: bool
    source_span: str
    confidence: float


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
    resources: tuple[TaskResource, ...] = ()


_RESOURCE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:/app/)?(?:[A-Za-z0-9_.-]+/)*"
    r"[A-Za-z0-9_.-]+\.(?:jsonl?|csv|tsv|txt|md|out|comp|py|c|cc|cpp|cxx|h|hpp|"
    r"js|jsx|mjs|ts|tsx|java|rs|go|rb|php|sh|scm|toml|ya?ml|ini|cfg|conf|log|xml|"
    r"html?|css|sql|db|cbl|cob|cpy|dat|ckpt|bpe|red|fasta|fa|npy|npz|bmp|png|"
    r"jpe?g|gif|svg|ico|mp4|mov|webm|avi|mkv|mp3|wav|flac|ogg|gcode|nc|gz|gzip|"
    r"tar|zip|tgz|bz2|xz|h5|hdf5|pkl|pt|onnx|safetensors|so|dll|exe|class|jar|"
    r"wasm|lib|obj|stl|gltf|glb|ply|wrl|step|stp|vhd|vhdl|sv|asm|f90|f95|cs|fs|"
    r"fsx|vb|mm|pas|ada|adb|hs|lua|pl|pm|tcl|tex|bib|sty|cls|nix|tf|hcl|cu|cuh)\b"
)
_ABSOLUTE_RESOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/app/[A-Za-z0-9_./-]+"
)
_EXTERNAL_RESOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:(?:etc/nginx)|(?:var/log/nginx))/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*"
)
_SHEBANG_RESOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:(?:/app/)|(?:[A-Za-z0-9_.-]+/))+"
    r"[A-Za-z0-9_-]+(?!\.[A-Za-z0-9_-]+)"
)
_OUTPUT_CUE_RE = re.compile(
    r"(?i)\b(?:write|create|produce|generate|save|emit|deliver|output)\b"
)
_INPUT_CUE_RE = re.compile(
    r"(?i)\b(?:read|input|incoming|source|provided|existing|unchanged|examine|"
    r"inspect|reference|do not modify)\b"
)
# Executable tokens whose following path is a shell operand (INPUT), not the
# deliverable named by a prose output verb earlier in the same clause.
_SHELL_OPERAND_EXEC_RE = re.compile(
    r"(?i)\b(?:node|npm|npx|python|python3|gcc|g\+\+|cc|clang|ruby|bash|sh|"
    r"perl|php|java|go|rustc)\b"
)
# A path that is the target of a ``>`` redirection is the OUTPUT of a shell
# command, distinct from an operand that precedes the redirection.
_REDIRECT_TARGET_RE = re.compile(r">\s*(?:/app/)?(?:[\w.-]+/)*[\w.+-]+")


def _resource_path(raw: str) -> str:
    path = str(raw or "").strip("`'\".,:;()[]{} ").replace("\\", "/")
    if path.startswith("/app/"):
        return path[5:]
    if path.startswith("./"):
        return path[2:]
    return path


def _resource_occurrences(line: str) -> list[tuple[int, int, str]]:
    found: dict[tuple[int, int, str], None] = {}
    for pattern in (_RESOURCE_PATH_RE, _ABSOLUTE_RESOURCE_RE, _EXTERNAL_RESOURCE_RE):
        for match in pattern.finditer(line or ""):
            cleaned = _resource_path(match.group(0))
            if cleaned and " " not in cleaned:
                found[(match.start(), match.end(), cleaned)] = None
    return sorted(found, key=lambda item: item[0])


def task_external_paths(issue_text: str) -> tuple[str, ...]:
    """Return explicitly named, allowlisted paths outside ``/app``.

    Only the known service roots are eligible.  A missing or malformed path
    remains absent rather than turning the sensor into a broad filesystem
    crawler.
    """

    core = _task_issue_core(issue_text)
    paths: list[str] = []
    seen: set[str] = set()
    for match in _EXTERNAL_RESOURCE_RE.finditer(core):
        path = match.group(0).rstrip(".,:;()[]{}'\"")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return tuple(paths)


def task_shebang_paths(issue_text: str) -> tuple[str, ...]:
    """Return explicitly described extensionless script paths only."""

    paths: list[str] = []
    seen: set[str] = set()
    for line in _task_issue_core(issue_text).splitlines():
        if not re.search(r"(?i)\b(?:script|shebang|interpreter|python|ruby|bash)\b", line):
            continue
        for match in _SHEBANG_RESOURCE_RE.finditer(line):
            path = _resource_path(match.group(0))
            if path and path not in seen and "." not in path.rsplit("/", 1)[-1]:
                seen.add(path)
                paths.append(path)
    return tuple(paths)


def _direct_output_score(prefix: str, path: str) -> int:
    escaped = re.escape(path.rsplit("/", 1)[-1])
    if re.search(
        rf"(?is)\b(?:write|create|produce|generate|save|emit|deliver)\b"
        rf"(?:\s+\S+){{0,8}}\s+(?:/app/)?(?:[\w.-]+/)*{escaped}\b",
        prefix[-220:],
    ):
        return 90
    tail = prefix[-180:]
    cue = list(_OUTPUT_CUE_RE.finditer(tail))
    if cue and not re.search(r"[.!?]\s", tail[cue[-1].end() :]):
        return 90
    return 0


def _to_file_output_score(prefix: str, path: str) -> int:
    """Score the ``... to the file <path>`` deliverable construction.

    Sentence-boundary splitting cuts a parenthetical example (``(e.g. ...)``)
    between an output verb and its path, so the clause-local direct-verb rule
    cannot see the pairing.  ``write ... to the file /app/answer.txt`` is
    unambiguous deliverable language and names the path immediately after
    ``the file``, so it is scored directly from the line prefix.
    """

    escaped = re.escape(path.rsplit("/", 1)[-1])
    if re.search(
        rf"(?is)\b(?:to|into)\s+the\s+(?:output\s+)?file\s+"
        rf"(?:/app/)?(?:[\w.-]+/)*{escaped}\b",
        prefix[-200:],
    ):
        return 90
    return 0


def _strong_direct_output(prefix: str, path: str) -> bool:
    """True when an output verb directly names ``path`` within a few words.

    This is the strong signal (``Write me a program extract.js``).  A shell
    operand must not override it, because the operand (``a.out``) is distinct
    from the deliverable the verb names (``extract.js``).
    """

    escaped = re.escape(path.rsplit("/", 1)[-1])
    return bool(
        re.search(
            rf"(?is)\b(?:write|create|produce|generate|save|emit|deliver)\b"
            rf"(?:\s+\S+){{0,8}}\s+(?:/app/)?(?:[\w.-]+/)*{escaped}\b",
            prefix[-220:],
        )
    )


def _shell_position_role(clause: str, offset: int, path: str) -> TaskResourceRole | None:
    """Return a role from the shell command position of one path occurrence.

    A path that is the target of ``>`` is the command's OUTPUT.  A path that is
    an operand of an executable token (``node extract.js /app/a.out``) is the
    command's INPUT, and the prose output verb earlier in the clause binds to
    the program being written, not to this operand.
    """

    head = clause[:offset]
    tail = clause[offset + len(path) :]
    if _REDIRECT_TARGET_RE.match(tail):
        return TaskResourceRole.OUTPUT
    # The operand appears after the most recent redirection or command start.
    head_segment = head.rsplit(">", 1)[-1]
    if _SHELL_OPERAND_EXEC_RE.search(head_segment):
        return TaskResourceRole.INPUT
    return None


def _resource_clause(line: str, offset: int) -> tuple[str, int]:
    """Return the punctuation-bounded clause containing one path occurrence."""

    boundaries = [
        match.end()
        for match in re.finditer(r"[.!?;](?:\s+|$)", line or "")
    ]
    start = max((boundary for boundary in boundaries if boundary <= offset), default=0)
    end = min((boundary for boundary in boundaries if boundary > offset), default=len(line))
    return line[start:end].strip(), max(0, offset - start)


def extract_task_resources(issue_text: str) -> tuple[TaskResource, ...]:
    """Extract typed path roles without guessing across ambiguous task prose.

    Markdown frequently wraps the verb and its paths onto separate lines.  A
    small flow state carries an explicit input/output cue across that block;
    structural ``input_data``/``output_data`` paths and direct verbs override
    the flow.  Conflicting low-confidence evidence abstains to UNKNOWN.
    """

    core = _task_issue_core(issue_text)
    scores: dict[str, dict[TaskResourceRole, int]] = {}
    spans: dict[str, str] = {}
    order: list[str] = []
    section_role = TaskResourceRole.UNKNOWN
    flow_role = TaskResourceRole.UNKNOWN

    # A path that a direct output verb names anywhere (``Write ... main.py.c``)
    # is a deliverable; a later shell-operand occurrence (``python3 main.py.c``)
    # must not reclassify it as INPUT.  Collect the per-path strong-direct flag
    # before scoring so the shell-position rule can consult the whole task text,
    # not only the current clause.
    strong_direct_paths: set[str] = set()
    for _raw in core.splitlines():
        for _offset, _end, path in _resource_occurrences(_raw):
            _clause, _clause_offset = _resource_clause(_raw, _offset)
            _prefix = _clause[: _clause_offset + (_end - _offset)]
            if _strong_direct_output(_prefix, path):
                strong_direct_paths.add(path)

    for raw in core.splitlines():
        stripped = raw.strip()
        if not stripped:
            flow_role = TaskResourceRole.UNKNOWN
            continue
        heading = re.match(r"^#{1,6}\s+(?P<name>.+?)\s*$", stripped)
        if heading:
            name = heading.group("name").strip().lower()
            section_role = (
                TaskResourceRole.OUTPUT
                if any(word in name for word in ("deliverable", "output", "result"))
                else TaskResourceRole.INPUT
                if any(word in name for word in ("input", "source data"))
                else TaskResourceRole.UNKNOWN
            )
            flow_role = section_role
            continue

        output_cue = bool(_OUTPUT_CUE_RE.search(stripped))
        input_cue = bool(_INPUT_CUE_RE.search(stripped))
        if output_cue:
            flow_role = TaskResourceRole.OUTPUT
        elif input_cue:
            flow_role = TaskResourceRole.INPUT

        for offset, end, path in _resource_occurrences(raw):
            if path not in scores:
                scores[path] = {}
                spans[path] = stripped[:500]
                order.append(path)
            row = scores[path]
            normalized = path.lower()
            clause, clause_offset = _resource_clause(raw, offset)
            raw_span = end - offset
            prefix = clause[: clause_offset + raw_span]
            basename = re.escape(path.rsplit("/", 1)[-1])
            clause_output = bool(_OUTPUT_CUE_RE.search(clause))
            clause_input = bool(_INPUT_CUE_RE.search(clause))

            def add(
                role: TaskResourceRole,
                score: int,
                target: dict[TaskResourceRole, int] = row,
            ) -> None:
                target[role] = max(score, target.get(role, 0))

            if "/input_data/" in f"/{normalized}" or normalized.startswith("input_data/"):
                add(TaskResourceRole.INPUT, 100)
            if "/output_data/" in f"/{normalized}" or normalized.startswith("output_data/"):
                add(TaskResourceRole.OUTPUT, 100)
            if re.search(rf"(?i)\bgives\s+exactly\s+(?:/app/)?(?:[\w.-]+/)*{basename}\b", clause):
                add(TaskResourceRole.INPUT, 95)
            if re.search(
                rf"(?i)\bkeep\b[^.\n]{{0,100}}{basename}"
                rf"[^.\n]{{0,60}}\bunchanged\b",
                clause,
            ):
                add(TaskResourceRole.INPUT, 100)
            direct_output = _direct_output_score(prefix, path)
            if direct_output:
                add(TaskResourceRole.OUTPUT, direct_output)
            if _to_file_output_score(raw[: end], path):
                add(TaskResourceRole.OUTPUT, 90)
            shell_role = _shell_position_role(clause, clause_offset, path)
            if shell_role is not None and path not in strong_direct_paths:
                # Shell position is mechanically stronger than a prose output
                # verb *earlier in the same clause* that only bleeds onto this
                # path.  For example in ``Write extract.js ... node extract.js
                # /app/a.out > out.json`` the operand ``a.out`` is INPUT and the
                # redirection target ``out.json`` is OUTPUT; only ``extract.js``
                # (named directly by ``Write``) is the deliverable.  A path that
                # a direct output verb names *anywhere* stays a deliverable even
                # when it also appears as a shell operand (``Write ... main.py.c
                # ... python3 main.py.c``).
                add(shell_role, 100)
            if re.search(
                rf"(?i)\b(?:read|have|provided|existing)\b(?:\s+\S+){{0,8}}\s+"
                rf"(?:/app/)?(?:[\w.-]+/)*{basename}\b",
                prefix[-220:],
            ):
                add(TaskResourceRole.INPUT, 80)
            if is_validation_source(path) and re.search(
                rf"(?i)\b(?:have|given|provided|existing|located)\b[^.\n]{{0,100}}"
                rf"(?:/app/)?(?:[\w.-]+/)*{basename}\b",
                clause,
            ):
                add(TaskResourceRole.REFERENCE, 90)
            if re.search(
                rf"(?i)\bcompile\b[^.\n]{{0,80}}\b(?:to|as)\s+"
                rf"(?:/app/)?(?:[\w.-]+/)*{basename}\b",
                clause,
            ):
                add(TaskResourceRole.EXECUTABLE, 95)
            if re.search(
                rf"(?i)\b(?:executable|decompressor|compiler|interpreter)\b"
                rf"(?:\s+\S+){{0,8}}\s+(?:/app/)?(?:[\w.-]+/)*{basename}\b",
                prefix[-220:],
            ):
                add(
                    TaskResourceRole.REFERENCE
                    if is_validation_source(path)
                    else TaskResourceRole.EXECUTABLE,
                    85,
                )
            if not re.search(r"\.[A-Za-z0-9]+$", path) and re.search(
                rf"(?i)(?:\brun(?:ning)?\b|\|)\s+(?:/app/)?(?:[\w.-]+/)*{basename}\b",
                clause,
            ):
                # Shell position is mechanically stronger than a prose cue
                # earlier in the same clause (for example ``Write data.comp
                # ... | /app/decomp``).  It must win instead of tying the
                # output score and degrading to UNKNOWN.
                add(TaskResourceRole.EXECUTABLE, 100)
            if section_role is not TaskResourceRole.UNKNOWN:
                add(section_role, 60)
            local_flow = (
                TaskResourceRole.OUTPUT
                if clause_output
                else TaskResourceRole.INPUT
                if clause_input
                else flow_role
            )
            if local_flow is not TaskResourceRole.UNKNOWN:
                add(local_flow, 50)
            if not row:
                add(TaskResourceRole.UNKNOWN, 1)

    resources: list[TaskResource] = []
    for path in order:
        ranked = sorted(scores[path].items(), key=lambda item: (-item[1], item[0].value))
        role, score = ranked[0]
        if len(ranked) > 1 and ranked[1][1] == score and ranked[1][0] is not role:
            role = TaskResourceRole.UNKNOWN
            score = 0
        resources.append(
            TaskResource(
                path=path,
                role=role,
                mutable=role is TaskResourceRole.OUTPUT,
                source_span=spans[path],
                confidence=min(1.0, score / 100.0),
            )
        )
    return tuple(resources)


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
        # Repository paths and task prose must use the same identifier
        # vocabulary.  Keeping only the raw ``multi-agent`` token while paths
        # expose ``multi`` and ``agent`` made a strong compound artifact lose
        # to an unrelated dense result containing only ``chat``.  Preserve the
        # original token for exact lookup and add deterministic component
        # terms for camel, snake, kebab, and dotted spellings.
        expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", token)
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", expanded)
        for component in re.findall(r"[A-Za-z0-9]+", expanded):
            normalized = component.casefold()
            if (
                len(normalized) >= 4
                and normalized not in _STOPWORDS
                and not normalized.isdigit()
            ):
                tokens.add(normalized)
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
    return bool(_WORKFLOW_STEP_RE.match(low))


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
            "expected feature:",
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


def _engine_candidates(issue_text: str) -> list[tuple[str, str]]:
    # The former source was the pre-v5 GroundTruth package. Its useful grammar
    # has been superseded by ``_markdown_candidates`` below, which preserves
    # complete normative clauses and short directive bullets. Keeping a second
    # extractor produced duplicate obligation identities and made an excluded
    # legacy package part of the benchmark runtime.
    return []


_PROSE_TEST_NAME_RE = re.compile(
    r"(?i:\btest_\w+\b)|(?i:\b\w+_test\b)|\bTest[A-Z]\w*\b|\btest[A-Z]\w*\b"
    r"|\b\w[\w.\-]*\.(?:test|spec)\.(?:ts|tsx|js|jsx|mjs|cjs)\b"
    r"|[\w/\\+\-]+\.[A-Za-z0-9_]+::[\w.\[\]\-]+|\b\w+::tests?::\w"
    r"|\b(?:test|tests|__tests__|__test__|spec|specs|e2e)[\\/]"
    r"|\#\[\s*tests?\b|\b(?:it|describe|context)\(\s*['\"]"
)
_PROSE_ASSERT_RE = re.compile(r"\bassert(?:_\w+|[A-Z]\w*|!|\s*\()")
_TEST_SOURCE_PATH_RE = re.compile(
    r"[\w./\\+\-]+\.(?:py|go|rs|js|jsx|ts|tsx|rb|java)\b", re.IGNORECASE
)
_TEST_FILE_RE = re.compile(
    r"(?:^|/)(?:test|tests|__tests__|__test__|spec|specs|e2e)(?:/|$)"
    r"|(?:^test_.*|.*_test\.(?:py|go|rb)|.*\.(?:test|spec)\."
    r"(?:js|jsx|ts|tsx|mjs|cjs)|.*Test\.java|.*_spec\.rb|conftest\.py)$",
    re.IGNORECASE,
)


def _leaks_test_identity(text: str) -> bool:
    value = text or ""
    if _PROSE_TEST_NAME_RE.search(value) or _PROSE_ASSERT_RE.search(value):
        return True
    return any(
        _TEST_FILE_RE.search(path.replace("\\", "/"))
        for path in _TEST_SOURCE_PATH_RE.findall(value)
    )


def _role(issue_text: str) -> str:
    if _CONTENT_SCAN_RE.search(issue_text or ""):
        return "content_scan"
    if _DATA_TRANSFORM_RE.search(issue_text or ""):
        return "data_transform"
    return "code_behavior"


def classify_directive_kind(text: str) -> DirectiveKind:
    value = str(text or "")
    positive_change = re.search(
        r"(?i)\b(?:add|change|create|expose|extend|fix|harden|implement|improve|modify|"
        r"normalize|patch|refactor|remove|replace|support|update|wire)\b",
        value,
    )
    # Negative and preservation clauses take precedence over a nearby positive
    # modal such as ``must``. They constrain a change; they never authorize one.
    if re.search(r"(?i)\b(?:do not|don't|never)\s+(?:edit|modify|change|remove|touch)\b", value):
        return DirectiveKind.FORBID_EDIT
    if not positive_change and re.search(
        r"(?i)\b(?:remain(?:s|ed)?\s+(?:unchanged|unaffected|compatible)|"
        r"unaffected by|preserve|keep\s+(?:the\s+)?(?:existing|current|unchanged)|"
        r"without\s+(?:changing|modifying|editing|breaking))\b",
        value,
    ):
        return DirectiveKind.PRESERVE
    if re.search(r"(?i)\b(?:remove|delete|drop|eliminate)\b", value):
        return DirectiveKind.REMOVE
    if re.search(r"(?i)\b(?:add|create|introduce|new|generate|write)\b", value):
        return DirectiveKind.ADD
    if re.search(
        r"(?i)\b(?:verify|validate|confirm|run|execute)\s+(?:the\s+)?(?:tests?|checks?)?\b",
        value,
    ):
        return DirectiveKind.VALIDATE
    if re.search(r"(?i)\b(?:inspect|find|locate|identify|determine|explain|read)\b", value):
        return DirectiveKind.INSPECT
    return DirectiveKind.MODIFY


def _typed_mentions(text: str, kind: DirectiveKind) -> tuple[TaskMention, ...]:
    participation = (
        MentionParticipation.CONSTRAINT
        if kind in {DirectiveKind.PRESERVE, DirectiveKind.FORBID_EDIT}
        else MentionParticipation.CONTEXT
        if kind in {DirectiveKind.INSPECT, DirectiveKind.VALIDATE}
        else MentionParticipation.TARGET
    )
    mentions: list[TaskMention] = []
    seen: set[str] = set()
    code_matches = (
        *(match.group("name") for match in _CODE_MENTION_RE.finditer(text or "")),
        *(match.group("name") for match in _BACKTICK_CALLABLE_RE.finditer(text or "")),
    )
    for value in code_matches:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        authority = (
            TextAuthority.QUALIFIED_CITATION
            if "::" in value or "." in value
            else TextAuthority.CODE_CITATION
        )
        mentions.append(TaskMention(value, participation, authority))
    # Literal source/configuration paths have explicit identity even when they
    # are not wrapped in Markdown code formatting.
    for _start, _end, path in _resource_occurrences(text or ""):
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        mentions.append(TaskMention(path, participation, TextAuthority.LITERAL_PATH))
    return tuple(mentions)


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
    normative_text = _task_issue_core(issue_text)
    combined = _engine_candidates(normative_text) + _markdown_candidates(normative_text)
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
        ):
            continue
        # Do not add nested copies of a row already retained — exact key only
        # (substring match dropped "Create foo.txt.bak with retries" when
        # "Create foo.txt" already existed, losing a distinct obligation).
        if key in seen:
            continue
        seen.add(key)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        obligations.append(
            Obligation(
                obligation_id=f"obl-{digest}",
                text=text[:500],
                source=source,
                subjects=_subjects(text),
                directive_kind=(kind := classify_directive_kind(text)),
                mentions=_typed_mentions(text, kind),
            )
        )
    frozen = tuple(obligations)
    mode = _task_mode(normative_text)
    return TaskContract(
        role=_role(normative_text),
        obligations=frozen,
        task_mode=mode,
        predicates=_typed_predicates(frozen, mode),
        resources=extract_task_resources(normative_text),
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
    if not shipped:
        return "", ()
    remaining = len(contract.obligations) - len(shipped)
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
    header = "Remaining task requirements:"
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
