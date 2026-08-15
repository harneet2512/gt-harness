"""Typed, conservative pre-execution action contract for the central engine."""

from __future__ import annotations

import hashlib
import posixpath
import re
import shlex
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ActionOperation(StrEnum):
    READ = "read"
    SEARCH = "search"
    EDIT = "edit"
    CREATE = "create"
    DELETE = "delete"
    VALIDATE = "validate"
    SUBMIT = "submit"
    INSTALL = "install"
    OTHER = "other"


class SegmentRole(StrEnum):
    """Structural shell role used for coverage, not inferred intent."""

    ACTION = "action"
    SHELL_CONTEXT = "shell_context"
    OUTPUT_ONLY = "output_only"
    OPAQUE_PROGRAM = "opaque_program"
    UNKNOWN = "unknown"


class MutationCertainty(StrEnum):
    """What shell parsing can prove about workspace mutation."""

    PROVEN_READ_ONLY = "proven_read_only"
    MAY_MUTATE = "may_mutate"
    PROVEN_MUTATING = "proven_mutating"


class WorkspaceImpact(StrEnum):
    """Whether dispatch can affect the task workspace tree."""

    PROVEN_NO_WORKSPACE_CHANGE = "proven_no_workspace_change"
    MAY_CHANGE_WORKSPACE = "may_change_workspace"
    PROVEN_WORKSPACE_CHANGE = "proven_workspace_change"


class ActionDisposition(StrEnum):
    PASS = "pass"
    AUGMENT = "augment"
    RETURN_TO_MODEL = "return_to_model"
    REWRITE = "rewrite"
    SUPPRESS = "suppress"


class PreflightMode(StrEnum):
    """Host policy for a candidate preflight decision.

    OFF preserves the old dispatch path.  SHADOW evaluates and records but
    cannot alter execution.  ASSISTIVE_SAFE may return grounded evidence for
    fresh model reasoning, but cannot rewrite or suppress shell commands.
    """

    OFF = "off"
    SHADOW = "shadow"
    ASSISTIVE_SAFE = "assistive_safe"

    @classmethod
    def parse(cls, value: str | PreflightMode) -> PreflightMode:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"unknown preflight mode {value!r}; expected {choices}") from exc


class EvidenceGrade(StrEnum):
    DIRECT = "direct"
    STRUCTURAL = "structural"
    DERIVED = "derived"
    HEURISTIC = "heuristic"


@dataclass(frozen=True, slots=True)
class ActionTarget:
    path: str
    role: str = "operand"


@dataclass(frozen=True, slots=True)
class ShellRedirection:
    """One top-level shell redirection, separate from executable argv."""

    segment_index: int
    file_descriptor: int | None
    operator: str
    target: str
    duplicates_descriptor: bool = False
    reads_filesystem: bool = False
    mutates_filesystem: bool = False


@dataclass(frozen=True, slots=True)
class ParsedShellSegment:
    """Executable argv plus redirections parsed from one shell segment."""

    argv: tuple[str, ...]
    redirections: tuple[ShellRedirection, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutableInvocation:
    """One wrapper-normalized executable invocation derived from shell words.

    This is mechanical shell structure, not inferred model intent.  Dynamic
    timeout expressions and malformed wrappers abstain so callers can PASS.
    """

    executable: str | None
    arguments: tuple[str, ...] = ()
    environment_assignments: tuple[tuple[str, str], ...] = ()
    wrappers: tuple[str, ...] = ()
    requested_timeout_sec: float | None = None
    confidence: float = 0.0

    @property
    def words(self) -> tuple[str, ...]:
        if self.executable is None:
            return ()
        return (self.executable, *self.arguments)


@dataclass(frozen=True, slots=True)
class ReadSpan:
    """A mechanically observed source range requested by one shell segment."""

    path: str
    start_line: int | None = None
    end_line: int | None = None
    whole_file: bool = False


@dataclass(frozen=True, slots=True)
class ObservedOperation:
    """One mechanically classified operation inside a proposed Bash action.

    This is shell structure, not inferred model intent.  Compound actions can
    carry several operations while unsupported segments remain OTHER.
    """

    segment_index: int
    executable: str
    operation: ActionOperation
    targets: tuple[ActionTarget, ...] = ()
    read_spans: tuple[ReadSpan, ...] = ()
    mutates_workspace: bool = False
    confidence: float = 0.0
    parser_evidence: tuple[str, ...] = ()
    segment_role: SegmentRole = SegmentRole.ACTION


@dataclass(frozen=True, slots=True)
class ProposedAction:
    action_id: str
    raw_command: str
    operation: ActionOperation
    targets: tuple[ActionTarget, ...]
    mutates_workspace: bool
    validation_kind: str | None
    source_revision: str
    workspace_revision: str
    model_call: int
    batch_index: int
    batch_size: int
    parser_confidence: float
    mutation_certainty: MutationCertainty = MutationCertainty.MAY_MUTATE
    parse_coverage: float = 0.0
    has_unknown_segments: bool = False
    has_opaque_segments: bool = False
    requested_timeout_sec: float | None = None
    operations: tuple[ObservedOperation, ...] = ()
    target_must_be_absent: bool = False
    shell_segments: tuple[tuple[str, ...], ...] = ()
    shell_connectors: tuple[str, ...] = ()
    shell_redirections: tuple[ShellRedirection, ...] = ()
    parser_evidence: tuple[str, ...] = ()

    @property
    def cycle_id(self) -> str:
        return f"call-{self.model_call}:batch-{self.batch_index}:{self.action_id}"

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["operation"] = self.operation.value
        row["mutation_certainty"] = self.mutation_certainty.value
        for operation in row["operations"]:
            operation["operation"] = str(operation["operation"])
        row["cycle_id"] = self.cycle_id
        return row


def classify_workspace_impact(
    proposed: ProposedAction,
    *,
    cwd: str,
) -> WorkspaceImpact:
    """Classify sensor necessity without trusting an opaque program body."""

    if proposed.mutation_certainty is MutationCertainty.PROVEN_READ_ONLY:
        return WorkspaceImpact.PROVEN_NO_WORKSPACE_CHANGE
    if proposed.has_unknown_segments or proposed.has_opaque_segments:
        return WorkspaceImpact.MAY_CHANGE_WORKSPACE
    mutating = tuple(operation for operation in proposed.operations if operation.mutates_workspace)
    if not mutating:
        return WorkspaceImpact.MAY_CHANGE_WORKSPACE
    targets = tuple(
        target.path.replace("\\", "/")
        for operation in mutating
        for target in operation.targets
        if target.path
    )
    if not targets:
        return WorkspaceImpact.MAY_CHANGE_WORKSPACE
    normalized_cwd = posixpath.normpath(str(cwd or "/").replace("\\", "/"))

    def inside_workspace(path: str) -> bool:
        if not path.startswith("/"):
            return True
        normalized = posixpath.normpath(path)
        return normalized == normalized_cwd or normalized.startswith(
            normalized_cwd.rstrip("/") + "/"
        )

    if all(path.startswith("/") and not inside_workspace(path) for path in targets):
        return WorkspaceImpact.PROVEN_NO_WORKSPACE_CHANGE
    if proposed.mutation_certainty is MutationCertainty.PROVEN_MUTATING and any(
        inside_workspace(path) for path in targets
    ):
        return WorkspaceImpact.PROVEN_WORKSPACE_CHANGE
    return WorkspaceImpact.MAY_CHANGE_WORKSPACE


@dataclass(frozen=True, slots=True)
class PreflightDecision:
    disposition: ActionDisposition
    command: str
    evidence: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    confidence: float = 0.0
    latency_ms: float = 0.0
    source_revision: str = ""
    evidence_grade: EvidenceGrade = EvidenceGrade.DIRECT
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["disposition"] = self.disposition.value
        row["evidence_grade"] = self.evidence_grade.value
        return row


@dataclass(slots=True)
class ActionCycleReceipt:
    """Replayable join across proposal, decision, dispatch, and postflight."""

    proposed: ProposedAction
    mode: PreflightMode
    candidate_decision: PreflightDecision
    applied_disposition: ActionDisposition
    applied_reason_codes: tuple[str, ...]
    dispatch_command: str
    executed: bool = False
    postflight: dict[str, Any] = field(default_factory=dict)
    reconsideration: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.proposed.cycle_id,
            "action_id": self.proposed.action_id,
            "proposed": self.proposed.as_dict(),
            "mode": self.mode.value,
            "candidate_decision": self.candidate_decision.as_dict(),
            "applied_disposition": self.applied_disposition.value,
            "applied_reason_codes": list(self.applied_reason_codes),
            "dispatch_command": self.dispatch_command,
            "executed": self.executed,
            "postflight": dict(self.postflight),
            "reconsideration": dict(self.reconsideration),
        }


_SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
_MUTATING_EXECUTABLES = frozenset(
    {
        "apply_patch",
        "cp",
        "install",
        "ln",
        "make",
        "mkdir",
        "mv",
        "ninja",
        "patch",
        "rm",
        "rmdir",
        "tee",
        "touch",
        "truncate",
    }
)
_MUTATING_GIT_SUBCOMMANDS = frozenset(
    {
        "add",
        "am",
        "apply",
        "checkout",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "filter-branch",
        "filter-repo",
        "gc",
        "init",
        "merge",
        "mv",
        "rebase",
        "reset",
        "restore",
        "reflog",
        "revert",
        "rm",
        "stash",
        "switch",
        "update-ref",
    }
)
_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"blame", "diff", "grep", "log", "ls-files", "rev-parse", "show", "status"}
)


def _without_heredoc_bodies(command: str) -> str:
    kept: list[str] = []
    terminator = ""
    for line in command.splitlines():
        if terminator:
            if line.strip() == terminator:
                terminator = ""
            continue
        kept.append(line)
        match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if match:
            terminator = match.group(1)
    return "\n".join(kept)


def _strip_shell_comments(command: str) -> str:
    """Remove top-level shell comments while preserving command newlines.

    ``shlex`` normally consumes the newline that terminates a comment.  That
    would merge the next command into the current segment, so comments are
    removed before lexing and their newline is retained as a real list
    separator.  Quoted ``#`` characters are ordinary data.
    """

    kept: list[str] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            if char != "\n":
                kept.extend(("\\", char))
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            kept.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            kept.append(char)
            index += 1
            continue
        if char == "#" and (index == 0 or command[index - 1].isspace()):
            newline = command.find("\n", index)
            if newline < 0:
                break
            kept.append("\n")
            index = newline + 1
            continue
        kept.append(char)
        index += 1
    if escaped:
        kept.append("\\")
    return "".join(kept)


_REDIRECTION_OPERATORS = ("<<-", "<<<", ">>", "<<", "<>", ">|", ">&", "<&", ">", "<")
_REDIRECTION_MARKER = "__GT_SHELL_REDIRECTION_"


def _mark_shell_redirections(command: str) -> tuple[str, dict[str, tuple[int | None, str]]]:
    """Replace unquoted redirection operators with shlex-safe markers.

    Marking before lexing preserves the shell grammar distinction between
    ``2>&1`` and ``2 >&1``.  The former owns fd 2; in the latter ``2`` remains
    an executable argument and stdout is redirected separately.
    """

    output: list[str] = []
    metadata: dict[str, tuple[int | None, str]] = {}
    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            output.extend(("\\", char))
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            output.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            output.append(char)
            index += 1
            continue

        fd_start = index
        fd_value: int | None = None
        operator_index = index
        if char.isdigit() and (index == 0 or not command[index - 1].isalnum()):
            while operator_index < len(command) and command[operator_index].isdigit():
                operator_index += 1
            if operator_index == index or operator_index >= len(command):
                operator_index = index
            elif command[operator_index] not in {"<", ">"}:
                operator_index = index
            else:
                fd_value = int(command[index:operator_index])
        operator = next(
            (
                candidate
                for candidate in _REDIRECTION_OPERATORS
                if command.startswith(candidate, operator_index)
            ),
            "",
        )
        if operator:
            marker = f"{_REDIRECTION_MARKER}{len(metadata):04d}__"
            metadata[marker] = (fd_value, operator)
            output.extend((" ", marker, " "))
            index = operator_index + len(operator)
            continue
        output.append(char)
        index = fd_start + 1
    if escaped:
        output.append("\\")
    return "".join(output), metadata


def _parsed_shell_parts(
    command: str,
) -> tuple[tuple[ParsedShellSegment, ...], tuple[str, ...]]:
    """Parse executable argv, redirections, and top-level connectors once."""

    try:
        marked, redirection_metadata = _mark_shell_redirections(
            _strip_shell_comments(_without_heredoc_bodies(command))
        )
        lexer = shlex.shlex(
            marked,
            posix=True,
            punctuation_chars=";|&\n",
        )
        # Newlines are Bash list separators, not generic whitespace.  Keeping
        # them as punctuation prevents two commands from being fused.
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return (), ()
    token_segments: list[tuple[str, ...]] = []
    connectors: list[str] = []
    current: list[str] = []
    for token in tokens:
        normalized_connector = token.replace("\n", ";")
        if normalized_connector and set(normalized_connector) <= {";", "|", "&"}:
            if current:
                token_segments.append(tuple(current))
                current = []
                connectors.append(normalized_connector)
            continue
        current.append(token)
    if current:
        token_segments.append(tuple(current))

    segments: list[ParsedShellSegment] = []
    for segment_index, tokens in enumerate(token_segments):
        argv: list[str] = []
        redirections: list[ShellRedirection] = []
        cursor = 0
        while cursor < len(tokens):
            token = tokens[cursor]
            details = redirection_metadata.get(token)
            if details is None:
                argv.append(token)
                cursor += 1
                continue
            fd_value, operator = details
            if cursor + 1 >= len(tokens):
                # Incomplete redirection is unsupported shell syntax.  Keep a
                # sentinel operand so downstream classifiers abstain.
                argv.append(token)
                cursor += 1
                continue
            target = tokens[cursor + 1]
            duplicates = operator in {">&", "<&"} and (
                target.isdigit() or target == "-"
            )
            reads = operator in {"<", "<>"} and not duplicates
            mutates = operator in {">", ">>", ">|", "<>"} and not duplicates
            redirections.append(
                ShellRedirection(
                    segment_index=segment_index,
                    file_descriptor=fd_value,
                    operator=operator,
                    target=target,
                    duplicates_descriptor=duplicates,
                    reads_filesystem=reads,
                    mutates_filesystem=mutates,
                )
            )
            cursor += 2
        segments.append(ParsedShellSegment(tuple(argv), tuple(redirections)))
    return tuple(segments), tuple(connectors)


def _shell_parts(
    command: str,
) -> tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]:
    parsed, connectors = _parsed_shell_parts(command)
    return tuple(segment.argv for segment in parsed), connectors


def shell_segments(command: str) -> tuple[tuple[str, ...], ...]:
    """Parse top-level executable segments once for proposal and validation."""

    return _shell_parts(command)[0]


def shell_structure(
    command: str,
) -> tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]:
    """Return parsed shell segments and list/pipeline connectors.

    Connectors may contain one terminal operator (notably ``&``) after the
    last segment.  Consumers must therefore not assume ``len(connectors)`` is
    exactly ``len(segments) - 1``.
    """

    return _shell_parts(command)


_ENV_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)
_TIMEOUT_DURATION = re.compile(r"(?P<value>(?:\d+(?:\.\d*)?|\.\d+))(?P<unit>[smhd]?)", re.I)


def _literal_timeout_seconds(value: str) -> float | None:
    match = _TIMEOUT_DURATION.fullmatch(value.strip())
    if match is None:
        return None
    amount = float(match.group("value"))
    multiplier = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[
        match.group("unit").lower()
    ]
    seconds = amount * multiplier
    return seconds if seconds > 0 else None


def normalize_executable_invocation(words: tuple[str, ...]) -> ExecutableInvocation:
    """Unwrap literal shell launchers without guessing through dynamic syntax."""

    if not words:
        return ExecutableInvocation(None)
    index = 0
    assignments: list[tuple[str, str]] = []
    wrappers: list[str] = []
    requested_timeout: float | None = None

    def consume_assignments() -> None:
        nonlocal index
        while index < len(words):
            match = _ENV_ASSIGNMENT.fullmatch(words[index])
            if match is None:
                break
            assignments.append((match.group(1), match.group(2)))
            index += 1

    consume_assignments()
    while index < len(words):
        wrapper = words[index].rsplit("/", 1)[-1].lower()
        if wrapper not in {"env", "command", "timeout", "sudo"}:
            break
        wrappers.append(wrapper)
        index += 1
        if wrapper == "env":
            while index < len(words) and words[index].startswith("-"):
                option = words[index]
                index += 1
                if option in {"-u", "--unset"}:
                    if index >= len(words):
                        return ExecutableInvocation(None, confidence=0.0)
                    index += 1
            consume_assignments()
            continue
        if wrapper == "command":
            while index < len(words) and words[index].startswith("-"):
                index += 1
            continue
        if wrapper == "sudo":
            while index < len(words) and words[index].startswith("-"):
                option = words[index]
                index += 1
                if option in {"-C", "-D", "-g", "-h", "-p", "-r", "-t", "-u"}:
                    if index >= len(words):
                        return ExecutableInvocation(None, confidence=0.0)
                    index += 1
            consume_assignments()
            continue
        while index < len(words) and words[index].startswith("-"):
            option = words[index]
            index += 1
            if option in {"-k", "--kill-after", "-s", "--signal"}:
                if index >= len(words):
                    return ExecutableInvocation(None, confidence=0.0)
                index += 1
        if index >= len(words):
            return ExecutableInvocation(None, confidence=0.0)
        requested_timeout = _literal_timeout_seconds(words[index])
        if requested_timeout is None:
            return ExecutableInvocation(None, confidence=0.0)
        index += 1
        consume_assignments()

    if index >= len(words):
        return ExecutableInvocation(None, confidence=0.0)
    executable = words[index].rsplit("/", 1)[-1]
    if not executable or any(character in executable for character in "$`(){}"):
        return ExecutableInvocation(None, confidence=0.0)
    return ExecutableInvocation(
        executable=executable,
        arguments=tuple(words[index + 1 :]),
        environment_assignments=tuple(assignments),
        wrappers=tuple(wrappers),
        requested_timeout_sec=requested_timeout,
        confidence=1.0,
    )


def _mutation_signals(segments: tuple[ParsedShellSegment, ...]) -> tuple[str, ...]:
    signals: list[str] = []
    for segment in segments:
        words = segment.argv
        if not words:
            continue
        invocation = normalize_executable_invocation(words)
        semantic_words = invocation.words
        if not semantic_words:
            continue
        head = semantic_words[0].lower()
        if head in _MUTATING_EXECUTABLES:
            signals.append(f"executable:{head}")
        if head in {"sed", "perl"} and any(flag in semantic_words for flag in ("-i", "-pi")):
            signals.append(f"in_place:{head}")
        if (
            head == "git"
            and len(semantic_words) > 1
            and semantic_words[1] in _MUTATING_GIT_SUBCOMMANDS
        ):
            signals.append(f"git:{semantic_words[1]}")
        if any(
            redirection.mutates_filesystem
            and redirection.target.strip("'\"") != "/dev/null"
            for redirection in segment.redirections
        ):
            signals.append("shell_redirection")
    return tuple(dict.fromkeys(signals))


_READ_EXECUTABLES = frozenset(
    {
        "cat",
        "cmp",
        "diff",
        "du",
        "file",
        "head",
        "less",
        "ls",
        "more",
        "nl",
        "od",
        "pwd",
        "readlink",
        "realpath",
        "stat",
        "strings",
        "tail",
        "wc",
        "xxd",
        "hexdump",
    }
)
_SEARCH_EXECUTABLES = frozenset({"rg", "grep", "find", "ack", "ag"})
_VALIDATE_EXECUTABLES = frozenset({"pytest", "ctest"})
_NON_TARGET_TOKENS = frozenset({"/dev/null", "-", "."})
_SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
_SHELL_CONTROL_PREFIXES = frozenset({"if", "elif", "then", "else", "while", "until", "do"})
_SHELL_CONTROL_ONLY = frozenset({"fi", "done", "esac", "{", "}"})
_SHELL_COMPLEX_CONTROL = frozenset({"for", "select", "case", "function"})


def _resolve_segment_path(value: str, cwd: str) -> str:
    cleaned = value.strip("'\"").replace("\\", "/")
    if not cleaned or cleaned in _NON_TARGET_TOKENS:
        return ""
    if cleaned.startswith("/"):
        return posixpath.normpath(cleaned)
    if cwd:
        return posixpath.normpath(posixpath.join(cwd, cleaned))
    return posixpath.normpath(cleaned)


def _looks_like_path(value: str) -> bool:
    cleaned = value.strip("'\"")
    if not cleaned or cleaned.startswith("-") or cleaned in _NON_TARGET_TOKENS:
        return False
    # Code strings, diagnostics, and shell expressions are not path operands.
    # Abstaining here is safer than presenting source text as a concrete file.
    if any(char.isspace() for char in cleaned):
        return False
    if any(char in cleaned for char in ";|&<>(){}[]=,"):
        return False
    if cleaned.isdigit() or _SED_RANGE.match(cleaned):
        return False
    return bool("/" in cleaned or "." in posixpath.basename(cleaned))


def _segment_operand_paths(words: tuple[str, ...], cwd: str) -> tuple[str, ...]:
    values: list[str] = []
    skip_next = False
    head = words[0].rsplit("/", 1)[-1] if words else ""
    program_indices: set[int] = set()
    opaque_indices: set[int] = set()
    if head in {"python", "python3", "py", "node", "ruby", "perl", "bash", "sh"}:
        for index, token in enumerate(words[1:], start=1):
            if token in {"-c", "-e"} and index + 1 < len(words):
                opaque_indices.add(index + 1)
    if head in {"sed", "awk", "perl", "rg", "grep", "ack", "ag"}:
        expression_option = False
        for index, token in enumerate(words[1:], start=1):
            if expression_option:
                program_indices.add(index)
                expression_option = False
                break
            if token in {"-e", "--expression"}:
                expression_option = True
                continue
            if token.startswith("-"):
                continue
            program_indices.add(index)
            break
    for index, token in enumerate(words[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if token in {">", ">>"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if index in program_indices:
            continue
        if index in opaque_indices:
            continue
        # A sed program or search query is not a path merely because it
        # contains punctuation.
        if index == 1 and head in {"sed", "awk", "rg", "grep", "ack", "ag"}:
            if _SED_RANGE.match(token.strip("'\"")) or not _looks_like_path(token):
                continue
        if not _looks_like_path(token):
            continue
        resolved = _resolve_segment_path(token, cwd)
        if resolved and resolved not in values:
            values.append(resolved)
    return tuple(values)


def _redirection_targets(
    redirections: tuple[ShellRedirection, ...], cwd: str
) -> tuple[str, ...]:
    targets: list[str] = []
    for redirection in redirections:
        if not redirection.mutates_filesystem:
            continue
        resolved = _resolve_segment_path(redirection.target, cwd)
        if resolved and resolved not in targets and resolved != "/dev/null":
            targets.append(resolved)
    return tuple(targets)


def _input_redirection_targets(
    redirections: tuple[ShellRedirection, ...], cwd: str
) -> tuple[str, ...]:
    targets: list[str] = []
    for redirection in redirections:
        if not redirection.reads_filesystem:
            continue
        resolved = _resolve_segment_path(redirection.target, cwd)
        if resolved and resolved not in targets and resolved != "/dev/null":
            targets.append(resolved)
    return tuple(targets)


def _sed_range(words: tuple[str, ...]) -> tuple[int | None, int | None]:
    for token in words[1:]:
        match = _SED_RANGE.match(token.strip("'\""))
        if match:
            start = int(match.group(1))
            return start, int(match.group(2) or start)
    return None, None


def _segment_is_validation(words: tuple[str, ...], validation: Any | None) -> bool:
    """Bind a whole-command validation result only to its actual runner.

    The immutable classifier is authoritative for the action, but a compound
    command can also contain setup and reporting segments.  Those segments do
    not become validation merely because a later runner is a declared check.
    """

    if not words:
        return False
    head = words[0].rsplit("/", 1)[-1].lower()
    lowered = tuple(word.lower() for word in words[1:])
    if head in {"python", "python3", "py"}:
        if any(word in {"pytest", "unittest"} for word in lowered):
            return True
        script = next((word for word in lowered if not word.startswith("-")), "")
        return any(marker in posixpath.basename(script) for marker in ("test", "check", "verify"))
    if head in {"node", "ruby", "bash", "sh"}:
        script = next((word for word in lowered if not word.startswith("-")), "")
        return any(marker in posixpath.basename(script) for marker in ("test", "check", "verify"))
    if head in {"npm", "pnpm", "yarn", "npx", "gradle", "gradlew", "mvn"}:
        return any(word in {"test", "check", "verify"} for word in lowered)
    return any(marker in head for marker in ("test", "check", "verify"))


def _segment_role(
    words: tuple[str, ...],
    head: str,
    operation: ActionOperation,
    redirections: tuple[str, ...],
) -> SegmentRole:
    """Classify shell mechanics separately from action semantics."""

    if head == "cd":
        return SegmentRole.SHELL_CONTEXT
    if head in {"echo", "printf"} and not redirections:
        return SegmentRole.OUTPUT_ONLY
    if head in {"python", "python3", "py", "node", "ruby", "bash", "sh"} and any(
        token in {"-c", "--command", "-e", "--eval"} for token in words[1:]
    ):
        return SegmentRole.OPAQUE_PROGRAM
    if operation is ActionOperation.OTHER:
        return SegmentRole.UNKNOWN
    return SegmentRole.ACTION


def _classify_operations(
    command: str,
    segments: tuple[ParsedShellSegment, ...],
    *,
    connectors: tuple[str, ...] = (),
    validation: Any | None,
) -> tuple[ObservedOperation, ...]:
    operations: list[ObservedOperation] = []
    current_cwd = ""
    pending_pipeline_read_indices: list[int] = []
    for segment_index, parsed_segment in enumerate(segments):
        words = parsed_segment.argv
        if not words:
            continue
        if segment_index and (
            segment_index - 1 >= len(connectors) or connectors[segment_index - 1] != "|"
        ):
            pending_pipeline_read_indices.clear()
        original_head = words[0].lower()
        if original_head in _SHELL_CONTROL_ONLY or original_head in _SHELL_COMPLEX_CONTROL:
            operations.append(
                ObservedOperation(
                    segment_index,
                    "",
                    ActionOperation.OTHER,
                    confidence=1.0,
                    parser_evidence=(
                        f"shell_control:{original_head}",
                        f"segment:{segment_index}",
                    ),
                    segment_role=SegmentRole.SHELL_CONTEXT,
                )
            )
            continue
        if original_head in _SHELL_CONTROL_PREFIXES:
            words = words[1:]
            if not words:
                operations.append(
                    ObservedOperation(
                        segment_index,
                        "",
                        ActionOperation.OTHER,
                        confidence=1.0,
                        parser_evidence=(
                            f"shell_control:{original_head}",
                            f"segment:{segment_index}",
                        ),
                        segment_role=SegmentRole.SHELL_CONTEXT,
                    )
                )
                continue
        invocation = normalize_executable_invocation(words)
        semantic_words = invocation.words
        head = (invocation.executable or "").lower()
        if not semantic_words:
            operations.append(
                ObservedOperation(
                    segment_index,
                    "",
                    ActionOperation.OTHER,
                    confidence=0.0,
                    parser_evidence=("wrapper_normalization_abstained", f"segment:{segment_index}"),
                    segment_role=SegmentRole.UNKNOWN,
                )
            )
            continue
        if head == "cd" and len(semantic_words) > 1:
            current_cwd = _resolve_segment_path(semantic_words[1], current_cwd)
            operations.append(
                ObservedOperation(
                    segment_index,
                    head,
                    ActionOperation.OTHER,
                    confidence=1.0,
                    parser_evidence=("shell_context:cwd", f"cwd:{current_cwd}"),
                    segment_role=SegmentRole.SHELL_CONTEXT,
                )
            )
            continue

        operands = _segment_operand_paths(semantic_words, current_cwd)
        redirections = _redirection_targets(parsed_segment.redirections, current_cwd)
        input_redirections = _input_redirection_targets(
            parsed_segment.redirections, current_cwd
        )
        base_targets = tuple(ActionTarget(path) for path in operands)
        evidence = (
            f"head:{head or 'unknown'}",
            f"segment:{segment_index}",
            *(
                (f"shell_control:{original_head}",)
                if original_head in _SHELL_CONTROL_PREFIXES
                else ()
            ),
        )

        if _SUBMIT_MARKER in command and _SUBMIT_MARKER in " ".join(words):
            operation, confidence = ActionOperation.SUBMIT, 1.0
        elif (
            validation is not None
            and bool(getattr(validation, "is_validation", False))
            and getattr(validation, "validator_segment_index", None) == segment_index
        ):
            operation, confidence = ActionOperation.VALIDATE, 1.0
        elif head in _READ_EXECUTABLES:
            operation, confidence = ActionOperation.READ, 0.98
        elif (
            head == "git"
            and len(semantic_words) > 1
            and semantic_words[1] in _READ_ONLY_GIT_SUBCOMMANDS
        ):
            operation, confidence = ActionOperation.READ, 0.98
        elif head == "sed" and "-n" in semantic_words and "-i" not in semantic_words:
            operation, confidence = ActionOperation.READ, 0.95
        elif head == "awk" and not redirections:
            operation, confidence = ActionOperation.READ, 0.85
        elif head in _SEARCH_EXECUTABLES:
            operation, confidence = ActionOperation.SEARCH, 0.95
        elif head in _VALIDATE_EXECUTABLES or (
            head in {"go", "cargo"} and len(semantic_words) > 1 and semantic_words[1] == "test"
        ):
            operation, confidence = ActionOperation.VALIDATE, 0.95
        elif _segment_is_validation(semantic_words, validation):
            operation, confidence = ActionOperation.VALIDATE, 1.0
        elif head in {"sed", "perl"} and any(flag in semantic_words for flag in ("-i", "-pi")):
            operation, confidence = ActionOperation.EDIT, 0.9
        elif head in {
            "apply_patch",
            "cp",
            "install",
            "ln",
            "mv",
            "patch",
            "tee",
            "truncate",
        }:
            operation, confidence = ActionOperation.EDIT, 0.95
        elif (
            head == "git"
            and len(semantic_words) > 1
            and semantic_words[1] in _MUTATING_GIT_SUBCOMMANDS
        ):
            operation, confidence = ActionOperation.EDIT, 0.9
        elif head in {"touch", "mkdir"}:
            operation, confidence = ActionOperation.CREATE, 0.95
        elif head in {"rm", "rmdir"}:
            operation, confidence = ActionOperation.DELETE, 0.95
        elif head in {"pip", "pip3", "npm", "yarn", "pnpm", "apt", "apt-get"} and any(
            word in {"install", "add"} for word in words[1:3]
        ):
            operation, confidence = ActionOperation.INSTALL, 0.95
        else:
            operation, confidence = ActionOperation.OTHER, 0.2

        read_spans: tuple[ReadSpan, ...] = ()
        if operation == ActionOperation.READ and operands:
            start, end = _sed_range(semantic_words) if head == "sed" else (None, None)
            read_spans = tuple(
                ReadSpan(
                    path=path,
                    start_line=start,
                    end_line=end,
                    whole_file=start is None and end is None,
                )
                for path in operands
            )
        mutates = operation in {
            ActionOperation.EDIT,
            ActionOperation.CREATE,
            ActionOperation.DELETE,
            ActionOperation.INSTALL,
        }
        operations.append(
            ObservedOperation(
                segment_index,
                head,
                operation,
                base_targets,
                read_spans,
                mutates,
                confidence,
                evidence,
                _segment_role(words, head, operation, redirections),
            )
        )
        base_operation_index = len(operations) - 1
        if input_redirections:
            operations.append(
                ObservedOperation(
                    segment_index,
                    head,
                    ActionOperation.READ,
                    tuple(
                        ActionTarget(path, "input_redirection")
                        for path in input_redirections
                    ),
                    tuple(ReadSpan(path=path, whole_file=True) for path in input_redirections),
                    confidence=0.98,
                    parser_evidence=(*evidence, "input_redirection"),
                )
            )
        if redirections:
            operations.append(
                ObservedOperation(
                    segment_index,
                    head,
                    ActionOperation.EDIT,
                    tuple(ActionTarget(path, "redirection") for path in redirections),
                    mutates_workspace=True,
                    confidence=0.98,
                    parser_evidence=(*evidence, "shell_redirection"),
                )
            )
        if operation == ActionOperation.READ and read_spans:
            pending_pipeline_read_indices.append(base_operation_index)
        elif head == "sed" and operation == ActionOperation.READ and not operands:
            start, end = _sed_range(semantic_words)
            if start is not None and pending_pipeline_read_indices:
                for read_index in pending_pipeline_read_indices:
                    previous = operations[read_index]
                    operations[read_index] = ObservedOperation(
                        previous.segment_index,
                        previous.executable,
                        previous.operation,
                        previous.targets,
                        tuple(
                            ReadSpan(span.path, start, end, False) for span in previous.read_spans
                        ),
                        previous.mutates_workspace,
                        previous.confidence,
                        (*previous.parser_evidence, "range_from_pipeline_filter"),
                    )
        else:
            pending_pipeline_read_indices.clear()
    return tuple(operations)


_PRIMARY_OPERATION_PRIORITY = {
    ActionOperation.SUBMIT: 0,
    ActionOperation.DELETE: 1,
    ActionOperation.EDIT: 2,
    ActionOperation.CREATE: 3,
    ActionOperation.INSTALL: 4,
    ActionOperation.VALIDATE: 5,
    ActionOperation.SEARCH: 6,
    ActionOperation.READ: 7,
    ActionOperation.OTHER: 8,
}


def adapt_proposed_action(
    action: Mapping[str, Any],
    *,
    source_revision: str,
    workspace_revision: str,
    model_call: int,
    batch_index: int,
    batch_size: int,
    validation: Any | None = None,
) -> ProposedAction:
    command = str(action.get("command") or "")
    action_id = (
        str(action.get("tool_call_id") or "")
        or "action-"
        + hashlib.sha256(f"{model_call}:{batch_index}:{command}".encode()).hexdigest()[:12]
    )
    stripped = command.strip()
    parsed_segments, connectors = _parsed_shell_parts(stripped)
    segments = tuple(segment.argv for segment in parsed_segments)
    invocation = (
        normalize_executable_invocation(segments[0])
        if len(segments) == 1
        else ExecutableInvocation(None)
    )
    words = list(invocation.words)
    head = invocation.executable or ""
    mutation_signals = _mutation_signals(parsed_segments)
    operations = _classify_operations(
        command,
        parsed_segments,
        connectors=connectors,
        validation=validation,
    )
    meaningful = tuple(item for item in operations if item.operation != ActionOperation.OTHER)
    validation_index = getattr(validation, "validator_segment_index", None)
    primary = next(
        (
            item
            for item in operations
            if item.segment_index == validation_index
            and item.operation is ActionOperation.VALIDATE
        ),
        None,
    )
    if primary is None:
        primary = min(
            meaningful or operations,
            key=lambda item: (_PRIMARY_OPERATION_PRIORITY[item.operation], item.segment_index),
            default=None,
        )
    operation = primary.operation if primary is not None else ActionOperation.OTHER
    action_operations = tuple(
        item
        for item in operations
        if item.segment_role not in {SegmentRole.SHELL_CONTEXT, SegmentRole.OUTPUT_ONLY}
    )
    known_operations = tuple(
        item for item in action_operations if item.operation is not ActionOperation.OTHER
    )
    heredoc_interpreter = bool(
        re.search(r"<<-?\s*['\"]?[A-Za-z_][A-Za-z0-9_]*['\"]?", command)
        and any(
            item.executable in {"python", "python3", "py", "node", "ruby", "perl", "bash", "sh"}
            for item in operations
        )
    )
    has_opaque_segments = heredoc_interpreter or any(
        item.segment_role is SegmentRole.OPAQUE_PROGRAM for item in action_operations
    )
    has_unknown_segments = any(
        item.segment_role is SegmentRole.UNKNOWN for item in action_operations
    )
    parse_coverage = len(known_operations) / len(action_operations) if action_operations else 0.0
    confidence = (
        min(item.confidence for item in action_operations)
        if action_operations
        else (0.2 if stripped else 0.0)
    )
    validation_kind: str | None = None
    if validation is not None and bool(getattr(validation, "is_validation", False)):
        validation_kind = str(getattr(validation, "command_class", "validation"))
    elif operation == ActionOperation.VALIDATE:
        validation_kind = head
    # Targets come only from mechanically parsed executable operands and
    # redirections.  Regex-scanning the raw command leaks heredoc bodies,
    # interpreter source strings, and diagnostics into typed intent.
    parsed_targets: list[ActionTarget] = []
    for observed in operations:
        for target in observed.targets:
            if target not in parsed_targets:
                parsed_targets.append(target)
    proven_mutating = bool(mutation_signals) or any(item.mutates_workspace for item in operations)
    proven_read_only = (
        bool(action_operations)
        and all(
            item.operation in {ActionOperation.READ, ActionOperation.SEARCH}
            for item in action_operations
        )
        and not has_opaque_segments
        and not has_unknown_segments
    )
    mutation_certainty = (
        MutationCertainty.PROVEN_MUTATING
        if proven_mutating
        else MutationCertainty.PROVEN_READ_ONLY
        if proven_read_only
        else MutationCertainty.MAY_MUTATE
    )
    return ProposedAction(
        action_id=action_id,
        raw_command=command,
        operation=operation,
        targets=tuple(parsed_targets),
        mutates_workspace=(
            operation
            in {
                ActionOperation.EDIT,
                ActionOperation.CREATE,
                ActionOperation.DELETE,
                ActionOperation.INSTALL,
            }
            or bool(mutation_signals)
        ),
        validation_kind=validation_kind,
        source_revision=source_revision,
        workspace_revision=workspace_revision,
        model_call=max(1, int(model_call)),
        batch_index=max(0, int(batch_index)),
        batch_size=max(1, int(batch_size)),
        parser_confidence=confidence,
        mutation_certainty=mutation_certainty,
        parse_coverage=round(parse_coverage, 6),
        has_unknown_segments=has_unknown_segments,
        has_opaque_segments=has_opaque_segments,
        requested_timeout_sec=next(
            (
                item.requested_timeout_sec
                for item in (
                    normalize_executable_invocation(segment.argv)
                    for segment in parsed_segments
                )
                if item.requested_timeout_sec is not None
            ),
            None,
        ),
        operations=operations,
        target_must_be_absent=(
            operation == ActionOperation.CREATE
            and head == "mkdir"
            and "-p" not in words
            and "--parents" not in words
        ),
        shell_segments=segments,
        shell_connectors=connectors,
        shell_redirections=tuple(
            redirection
            for segment in parsed_segments
            for redirection in segment.redirections
        ),
        parser_evidence=(
            f"head:{head or 'unknown'}",
            f"segments:{len(segments)}",
            f"operation:{operation.value}",
            f"mutation_certainty:{mutation_certainty.value}",
            f"parse_coverage:{parse_coverage:.6f}",
            *(f"mutation:{item}" for item in mutation_signals),
        ),
    )


def pass_decision(proposed: ProposedAction, *reasons: str) -> PreflightDecision:
    return PreflightDecision(
        ActionDisposition.PASS,
        proposed.raw_command,
        reason_codes=tuple(reasons) or ("default_pass",),
        confidence=proposed.parser_confidence,
        source_revision=proposed.source_revision,
    )


@dataclass(frozen=True, slots=True)
class FeatureLifecyclePlacement:
    feature_id: str
    current_trigger: str
    preflight_operations: tuple[ActionOperation, ...]
    postflight_only: bool
    required_inputs: tuple[str, ...]
    evidence_grade: EvidenceGrade
    decision: str


def _placement(
    feature_id: str,
    trigger: str,
    operations: tuple[ActionOperation, ...],
    inputs: tuple[str, ...],
    grade: EvidenceGrade,
    decision: str,
    *,
    postflight_only: bool = False,
) -> FeatureLifecyclePlacement:
    return FeatureLifecyclePlacement(
        feature_id,
        trigger,
        operations,
        postflight_only,
        inputs,
        grade,
        decision,
    )


PREFLIGHT_FEATURE_PLACEMENT = {
    item.feature_id: item
    for item in (
        _placement(
            "obligations",
            "task_start",
            (ActionOperation.SUBMIT,),
            ("task_contract", "current_obligations"),
            EvidenceGrade.DIRECT,
            "read current contract before submit",
        ),
        _placement(
            "localization",
            "task_start/search_result",
            (ActionOperation.EDIT, ActionOperation.CREATE),
            ("source_bound_graph", "typed_targets"),
            EvidenceGrade.STRUCTURAL,
            "shadow until an exact target contradiction is proven",
        ),
        _placement(
            "GT_LOC_RESLOT",
            "task_start/search_result",
            (ActionOperation.EDIT, ActionOperation.CREATE),
            ("ranked_source_anchors", "typed_targets"),
            EvidenceGrade.STRUCTURAL,
            "shadow ranking; never guess a file",
        ),
        _placement(
            "def_partition",
            "task_start/search_result",
            (ActionOperation.EDIT,),
            ("definition_anchors", "reference_anchors"),
            EvidenceGrade.STRUCTURAL,
            "preflight only with graph-proven partitions",
        ),
        _placement(
            "caller_contract",
            "task_start/file_view/search_result/edit_result",
            (ActionOperation.EDIT,),
            ("directed_caller_edges", "target_symbol"),
            EvidenceGrade.STRUCTURAL,
            "preflight only with directed caller evidence",
        ),
        _placement(
            "newfile_precedent",
            "search_result/edit_result",
            (ActionOperation.CREATE,),
            ("exact_create_target", "source_sibling"),
            EvidenceGrade.STRUCTURAL,
            "return only for exact duplicates; precedents start shadow",
        ),
        _placement(
            "GT_CHANGE_SURFACE",
            "edit_result",
            (),
            ("workspace_diff",),
            EvidenceGrade.DIRECT,
            "requires executed diff",
            postflight_only=True,
        ),
        _placement(
            "signature_delta",
            "edit_result",
            (),
            ("before_contents", "after_contents"),
            EvidenceGrade.DIRECT,
            "requires executed source delta",
            postflight_only=True,
        ),
        _placement(
            "GT_PATCH_DELTA",
            "edit_result",
            (),
            ("workspace_diff", "signature_delta"),
            EvidenceGrade.DERIVED,
            "requires executed patch",
            postflight_only=True,
        ),
        _placement(
            "GT_EDIT_CHECK",
            "edit_result",
            (ActionOperation.EDIT, ActionOperation.VALIDATE),
            ("source_revision", "validation_debt"),
            EvidenceGrade.DERIVED,
            "may select a grounded existing check; no speculative test",
        ),
        _placement(
            "syntax_result",
            "edit_result",
            (),
            ("generated_source", "syntax_command_result"),
            EvidenceGrade.DIRECT,
            "requires generated code and command result",
            postflight_only=True,
        ),
        _placement(
            "covering_red",
            "test_result",
            (),
            ("validation_result", "diagnostic"),
            EvidenceGrade.DIRECT,
            "requires executed validator output",
            postflight_only=True,
        ),
        _placement(
            "GT_HYPOTHESIS",
            "test_result",
            (ActionOperation.VALIDATE,),
            ("unchanged_source_revision", "failure_fingerprint"),
            EvidenceGrade.DERIVED,
            "shadow repeated-failure hypothesis before validation",
        ),
        _placement(
            "recovery",
            "test_result",
            (ActionOperation.VALIDATE,),
            ("failure_history", "concrete_alternative"),
            EvidenceGrade.DIRECT,
            "return only on exact unchanged repeated failure with an alternative",
        ),
        _placement(
            "submit_refusal",
            "test_result/submit",
            (ActionOperation.SUBMIT,),
            ("fresh_grounded_failures",),
            EvidenceGrade.DIRECT,
            "return on a fresh explicit failing check",
        ),
        _placement(
            "GT_SS_SUBMIT_RED",
            "test_result/submit",
            (ActionOperation.SUBMIT,),
            ("fresh_grounded_failures", "source_revision"),
            EvidenceGrade.DIRECT,
            "same submit blocker, no duplicate message",
        ),
        _placement(
            "GT_CERT_DELIVERY",
            "test_result/submit",
            (ActionOperation.SUBMIT,),
            ("current_checks", "source_revision"),
            EvidenceGrade.DIRECT,
            "certificate state remains private unless submission needs it",
        ),
    )
}
