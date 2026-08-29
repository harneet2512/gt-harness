"""Private state and policy for the host-owned coding-agent runtime.

Nothing in this module is installed in the task container.  The model sees the
stock Mini-SWE Bash interface; this module observes transitions through
Harbor's host-side ``BaseEnvironment`` boundary.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import shlex
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from gt_engine.central_controls import (
    FeatureEffect,
    consumer_spec_for,
)
from gt_engine.graph_inputs import is_graph_input
from gt_engine.host_execution import HostExecCategory, HostExecutionRecorder
from gt_engine.language_registry import (
    candidate_capabilities,
    is_indexable_source,
    is_validation_source,
    syntax_probe_command,
)
from gt_engine.preflight import (
    ActionCycleReceipt,
    ActionDisposition,
    ActionOperation,
    EvidenceGrade,
    ExecutableInvocation,
    PreflightDecision,
    PreflightMode,
    ProposedAction,
    adapt_proposed_action,
    normalize_executable_invocation,
    pass_decision,
    shell_segments,
    shell_structure,
)
from gt_engine.semantic_decisions import (
    DecisionNeedKind,
    SemanticClaimKind,
    SemanticDecisionEngine,
)
from gt_engine.task_contract import TaskResourceRole, extract_task_resources
from gt_engine.thin_compiler import PROVIDER_MATERIAL_FEATURES
from gt_engine.uplift_policy import (
    EvidenceAuthority,
    OpportunityKind,
    certify_opportunity,
)

_MANIFEST_COMMAND = (
    "set -o pipefail; LC_ALL=C find . -xdev "
    "\\( -type d \\( -name .git -o -name .hg -o -name .svn -o "
    "-name .gt -o -name .groundtruth -o -name node_modules -o "
    "-name .venv -o -name venv -o -name __pycache__ -o "
    "-name .tox -o -name .mypy_cache -o -name .ruff_cache -o "
    "-name dist -o -name build -o -name target \\) -prune \\) -o -mindepth 1 "
    "-printf '%y\\t%s\\t%T@\\t%C@\\t%P\\t%l\\n' 2>/dev/null "
    "| LC_ALL=C sort | LC_ALL=C awk 'NR <= 50001'"
)
_EXTERNAL_ROOTS = ("/etc/nginx/", "/var/log/nginx/")
_PRIVATE_TERMS = re.compile(r"groundtruth|gt_[a-z0-9_]*", re.IGNORECASE)
_MISSING_EXECUTABLE = re.compile(
    r"(?:command not found|not found|no such file or directory|cannot execute)", re.IGNORECASE
)
_FAILURE_LINE = re.compile(r"\b(?:fail(?:ed|ure)?|error|exception|traceback|red)\b", re.I)
_SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
_MODEL_ACTIONABLE_FEATURES = PROVIDER_MATERIAL_FEATURES
_NON_MATERIAL_PATH_PARTS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".git", ".hg", ".svn"}
)
_DERIVED_SUFFIXES = frozenset(
    {
        ".o",
        ".so",
        ".a",
        ".pyc",
        ".pyo",
        ".pyd",
        ".class",
        ".exe",
        ".dll",
        ".lib",
        ".obj",
        ".elf",
        ".out",
        ".bin",
        ".log",
        ".dylib",
        ".jar",
        ".whl",
        ".tar",
        ".gz",
        ".zip",
        # Serialized/generated data and model artifacts are not authored
        # source.  A model-selected benchmark may create them, but their
        # creation must not advance source revision or validation debt.
        ".pkl",
        ".pickle",
        ".npy",
        ".npz",
        ".pt",
        ".parquet",
        ".feather",
        ".arrow",
        ".h5",
        ".hdf5",
        ".onnx",
        ".pb",
        ".wasm",
    }
)
_DERIVED_PATH_PARTS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".hypothesis",
        ".ruff_cache",
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "build",
        "dist",
        "target",
        ".tox",
        "eggs",
    }
)
_BACKGROUND_ARTIFACT_NAMES = frozenset(
    {"benchmark_out.txt", "callback-test.txt", "a.out", "data.comp"}
)
_MAX_SOURCE_CAPTURE_BYTES = 250_000
_BINARY_HEAD_BYTES = 2048
_BINARY_HEAD_MAX_FILES = 32
_BINARY_HEAD_SKIP_DIRS = frozenset(
    {".extracted", ".pytest_cache", ".ruff_cache", "solution"}
)
_BINARY_HEAD_SKIP_FILES = frozenset(
    {
        "reward" + ".txt",
        "ctrf" + ".json",
        "test_outputs" + ".py",
        "solution",
    }
)


def _may_be_content_signature_source(path: str) -> bool:
    """Keep extensionless files eligible for bounded source capture.

    Language identity for shebang and other content-signature sources cannot
    be known from a manifest path.  Capturing the bounded prefix lets the
    shared resolver classify the file deterministically; non-source files are
    then excluded by ``classify_change`` and never advance source revision.
    """

    return not os.path.splitext(path.replace("\\", "/"))[1]


@dataclass(frozen=True, slots=True)
class FileState:
    kind: str
    size: int
    mtime: str
    ctime: str
    link_target: str
    digest: str = ""
    content: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    revision: str
    entries: dict[str, FileState]
    healthy: bool
    reason: str = ""
    elapsed_seconds: float = 0.0
    binary_heads: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RevisionEntry:
    """One canonical content-addressed repository input."""

    path: str
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SourceRevisionReceipt:
    """Content-addressed identity of the validation-relevant source mirror.

    Workspace metadata remains available through ``WorkspaceSnapshot.revision``
    for audit and change detection.  It is deliberately excluded here: a
    timestamp-only change must not stale graph evidence or validation state.
    ``complete`` is false when any admitted source lacks a mechanically
    available full-content digest.
    """

    revision: str
    complete: bool
    source_paths: tuple[str, ...]
    missing_digest_paths: tuple[str, ...]
    entries: tuple[RevisionEntry, ...] = ()


def revision_changed_paths(
    before: SourceRevisionReceipt,
    after: SourceRevisionReceipt,
) -> tuple[str, ...]:
    """Return paths whose presence or full-content identity changed."""

    before_entries = {entry.path: entry.content_sha256 for entry in before.entries}
    after_entries = {entry.path: entry.content_sha256 for entry in after.entries}
    return tuple(
        sorted(
            path
            for path in before_entries.keys() | after_entries.keys()
            if before_entries.get(path) != after_entries.get(path)
        )
    )


@dataclass(frozen=True, slots=True)
class WorkspaceTransition:
    action_id: int
    command: str
    before_revision: str
    after_revision: str
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    sensor_healthy: bool = True
    before_contents: dict[str, str] = field(default_factory=dict)
    after_contents: dict[str, str] = field(default_factory=dict)

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(sorted((*self.created, *self.modified, *self.deleted)))


class ChangeOrigin(StrEnum):
    """Why a path changed.  Only MODEL_AUTHORED advances source revision."""

    MODEL_AUTHORED = "model_authored"
    VALIDATOR_DERIVED = "validator_derived"
    BACKGROUND_DERIVED = "background_derived"
    TASK_DELIVERABLE = "task_deliverable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ClassifiedChange:
    path: str
    kind: str
    origin: ChangeOrigin
    validation_relevant: bool
    graph_indexable: bool = False


@dataclass(frozen=True, slots=True)
class RevisionState:
    """Workspace revision (raw audit) versus validation-relevant source state."""

    workspace_revision: str
    source_revision: str
    source_epoch: int = 0


def classify_change(
    path: str,
    *,
    kind: str = "f",
    task_deliverables: Iterable[str] = (),
    content: str | bytes | None = None,
) -> ClassifiedChange:
    """Classify one changed path against the source-revision model.

    Directories, caches, compiled objects, binaries, build products, logs,
    benchmark output, and background-process writes never advance source
    revision.  Task deliverables are tracked separately and satisfy
    obligations without pretending to be source edits.
    """
    parts = path.replace("\\", "/").split("/")
    if kind != "f" or any(part in _DERIVED_PATH_PARTS for part in parts):
        return ClassifiedChange(path, kind, ChangeOrigin.BACKGROUND_DERIVED, False)
    lower = path.lower()
    if any(lower.endswith(suffix) for suffix in _DERIVED_SUFFIXES):
        return ClassifiedChange(path, kind, ChangeOrigin.VALIDATOR_DERIVED, False)
    if any(name in path for name in _BACKGROUND_ARTIFACT_NAMES):
        return ClassifiedChange(path, kind, ChangeOrigin.BACKGROUND_DERIVED, False)
    validation_relevant = is_validation_source(path, content)
    graph_indexable = is_indexable_source(path, content)
    if path in set(task_deliverables):
        # Output is a task role, not a file type. Authored code can be both a
        # required output and validation/index source; data remains output-only.
        return ClassifiedChange(
            path,
            kind,
            ChangeOrigin.TASK_DELIVERABLE,
            validation_relevant,
            graph_indexable,
        )
    return ClassifiedChange(
        path,
        kind,
        ChangeOrigin.MODEL_AUTHORED,
        validation_relevant,
        graph_indexable,
    )


def source_revision_receipt(
    snapshot: WorkspaceSnapshot,
    task_deliverables: Iterable[str] = (),
) -> SourceRevisionReceipt:
    """Hash canonical source paths and content digests, never filesystem metadata."""

    deliverables = set(task_deliverables)
    digest = hashlib.sha256()
    source_paths: list[str] = []
    missing_digest_paths: list[str] = []
    entries: list[RevisionEntry] = []
    for path, item in sorted(snapshot.entries.items()):
        if item.kind != "f":
            continue
        if not classify_change(
            path,
            kind=item.kind,
            task_deliverables=deliverables,
            content=item.content,
        ).validation_relevant:
            continue
        canonical_path = _workspace_relative_path(path)
        source_paths.append(canonical_path)
        content_digest = str(item.digest or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", content_digest):
            if item.content is not None:
                content_digest = hashlib.sha256(
                    item.content.encode("utf-8", "surrogatepass")
                ).hexdigest()
            else:
                missing_digest_paths.append(canonical_path)
                content_digest = "missing"
        entries.append(
            RevisionEntry(
                path=canonical_path,
                content_sha256="" if content_digest == "missing" else content_digest,
                size_bytes=max(0, int(item.size)),
            )
        )
        digest.update(canonical_path.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(content_digest.encode("ascii"))
        digest.update(b"\0")
    return SourceRevisionReceipt(
        revision=digest.hexdigest(),
        complete=not missing_digest_paths,
        source_paths=tuple(source_paths),
        missing_digest_paths=tuple(missing_digest_paths),
        entries=tuple(entries),
    )


def graph_revision_receipt(
    snapshot: WorkspaceSnapshot,
    task_deliverables: Iterable[str] = (),
) -> SourceRevisionReceipt:
    """Hash structural sources and dependency/build metadata used by the graph."""

    del task_deliverables  # compatibility argument; graph identity is path-policy driven
    digest = hashlib.sha256()
    source_paths: list[str] = []
    missing_digest_paths: list[str] = []
    entries: list[RevisionEntry] = []
    for path, item in sorted(snapshot.entries.items()):
        if item.kind != "f":
            continue
        if not is_graph_input(path, item.content):
            continue
        canonical_path = _workspace_relative_path(path)
        source_paths.append(canonical_path)
        content_digest = str(item.digest or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", content_digest):
            if item.content is not None:
                content_digest = hashlib.sha256(
                    item.content.encode("utf-8", "surrogatepass")
                ).hexdigest()
            else:
                missing_digest_paths.append(canonical_path)
                content_digest = "missing"
        entries.append(
            RevisionEntry(
                path=canonical_path,
                content_sha256="" if content_digest == "missing" else content_digest,
                size_bytes=max(0, int(item.size)),
            )
        )
        digest.update(canonical_path.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(content_digest.encode("ascii"))
        digest.update(b"\0")
    return SourceRevisionReceipt(
        revision=digest.hexdigest(),
        complete=not missing_digest_paths,
        source_paths=tuple(source_paths),
        missing_digest_paths=tuple(missing_digest_paths),
        entries=tuple(entries),
    )


def source_revision_of(
    snapshot: WorkspaceSnapshot,
    task_deliverables: Iterable[str] = (),
) -> str:
    """Compatibility projection of :func:`source_revision_receipt`."""

    return source_revision_receipt(snapshot, task_deliverables).revision


def _workspace_relative_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    if normalized.startswith("/app/"):
        return normalized[5:]
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def _safe_external_path(path: str) -> str:
    """Accept only explicit service paths, never an arbitrary root scan."""

    normalized = str(path or "").strip().replace("\\", "/")
    if (
        not normalized.startswith(_EXTERNAL_ROOTS)
        or ".." in normalized.split("/")
        or any(ord(char) < 32 for char in normalized)
        or normalized.endswith("/")
    ):
        return ""
    return normalized


def _extensionless_candidate(path: str) -> bool:
    """Bounded discovery candidate; content still has to prove a shebang."""

    name = str(path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return bool(name and "." not in name and name not in {"", ".", ".."})


def _external_manifest_command(path: str) -> str:
    quoted = shlex.quote(path)
    return (
        "test ! -e "
        + quoted
        + " || find "
        + quoted
        + " -xdev -maxdepth 0 -type f -printf '%y\\t%s\\t%T@\\t%C@\\t%p\\t%l\\n' 2>/dev/null"
    )


def task_deliverable_paths(instruction: str) -> tuple[str, ...]:
    """Return OUTPUT paths from the typed task contract.

    The contract parser already assigns OUTPUT only through a mechanically
    grounded cue (direct output verb, redirection target, ``output_data``
    structure, or an output-cued flow role).  Trust that role rather than
    re-filtering against a frozen suffix allowlist: TB2 deliverables are often
    source or config files (``.py``, ``.c``, ``.toml``, ``.red``) that the
    historical ``_DELIVERABLE_SUFFIXES`` list silently discarded.  The
    source-revision model already classifies a code deliverable as both a task
    output and validation/index source.
    """

    return tuple(
        resource.path
        for resource in extract_task_resources(instruction)
        if resource.role is TaskResourceRole.OUTPUT
        and resource.confidence >= 0.5
        and not is_submit_command(resource.path)
    )


class InterventionDecision(StrEnum):
    PASS = "PASS"
    ADVISE = "ADVISE"
    HOLD_ONCE = "HOLD_ONCE"
    SHADOW = "SHADOW"


@dataclass(frozen=True, slots=True)
class SubmitDecision:
    decision: InterventionDecision
    blockers: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CheckEvidence:
    command: str
    returncode: int
    revision: str
    grounded: bool
    command_class: str = ""
    failure_kind: str = ""
    source_revision: str = ""
    authority: str = ""


# The direct inventory is 10 FACT identities plus 8 CAP identities. Keep the
# inventory in the host-owned runtime as data, rather
# than claiming that a legacy import or an environment flag is an active
# implementation.  A feature is only reported as delivered after its
# boundary-specific trigger is observed.
CENTRAL_FEATURES: tuple[dict[str, str], ...] = (
    {"id": "caller_contract", "kind": "FACT", "owner": "contract_map"},
    {"id": "covering_red", "kind": "FACT", "owner": "covering_runner"},
    {"id": "def_partition", "kind": "FACT", "owner": "post_search"},
    {"id": "localization", "kind": "FACT", "owner": "v1r_brief"},
    {"id": "newfile_precedent", "kind": "FACT", "owner": "change_surface"},
    {"id": "obligations", "kind": "FACT", "owner": "spec"},
    {"id": "recovery", "kind": "FACT", "owner": "governor"},
    {"id": "signature_delta", "kind": "FACT", "owner": "patch_delta"},
    {"id": "submit_refusal", "kind": "FACT", "owner": "submit_gate"},
    {"id": "syntax_result", "kind": "FACT", "owner": "edit_check"},
    {"id": "GT_CERT_DELIVERY", "kind": "CAP", "owner": "submit_refusal"},
    {"id": "GT_CHANGE_SURFACE", "kind": "CAP", "owner": "newfile_precedent"},
    {"id": "GT_EDIT_CHECK", "kind": "CAP", "owner": "syntax_result"},
    {"id": "GT_HYPOTHESIS", "kind": "CAP", "owner": "recovery"},
    {"id": "GT_LOC_RESLOT", "kind": "CAP", "owner": "localization"},
    {"id": "GT_PATCH_DELTA", "kind": "CAP", "owner": "signature_delta"},
    {"id": "GT_SS_SUBMIT_RED", "kind": "CAP", "owner": "submit_refusal"},
    {
        "id": "select_catalog",
        "kind": "CAP",
        "owner": "persistent_execution_state",
    },
)
CENTRAL_FEATURE_IDS = tuple(item["id"] for item in CENTRAL_FEATURES)
CENTRAL_CAP_OWNERS = {
    item["id"]: item["owner"] for item in CENTRAL_FEATURES if item["kind"] == "CAP"
}
CENTRAL_FEATURE_BOUNDARIES = {
    "caller_contract": ("task_start", "file_view", "search_result", "edit_result"),
    "covering_red": "test_result",
    "def_partition": ("task_start", "search_result"),
    "localization": ("task_start", "search_result"),
    "newfile_precedent": ("search_result", "edit_result"),
    "obligations": "task_start",
    "recovery": "test_result",
    "signature_delta": "edit_result",
    "submit_refusal": ("test_result", "submit"),
    "syntax_result": "edit_result",
    "GT_CERT_DELIVERY": ("test_result", "submit"),
    "GT_CHANGE_SURFACE": "edit_result",
    "GT_EDIT_CHECK": "edit_result",
    "GT_HYPOTHESIS": "test_result",
    "GT_LOC_RESLOT": ("task_start", "search_result"),
    "GT_PATCH_DELTA": "edit_result",
    "GT_SS_SUBMIT_RED": ("test_result", "submit"),
    "select_catalog": "task_start",
}


def feature_payload_valid(
    feature_id: str,
    payload: dict[str, Any],
    *,
    boundary: str,
    revision: str,
    fresh: bool,
) -> bool:
    """Validate the minimum non-opaque payload contract for one delivery."""
    if feature_id not in CENTRAL_FEATURE_IDS or not revision or not fresh:
        return False
    expected_boundary = CENTRAL_FEATURE_BOUNDARIES[feature_id]
    if boundary not in (
        expected_boundary if isinstance(expected_boundary, tuple) else (expected_boundary,)
    ):
        return False
    if not payload.get("message"):
        return False
    required = {
        "caller_contract": ("callers_verified",),
        "covering_red": (
            "check_failed",
            "command_class",
            "failure_kind",
            "attribution",
        ),
        "def_partition": ("definitions", "references"),
        "localization": ("candidate_locations",),
        "newfile_precedent": (),
        "obligations": ("requirements_present",),
        "recovery": ("repeat_count",),
        "signature_delta": ("signature_edit",),
        "submit_refusal": ("submission_risk", "blockers"),
        "syntax_result": ("ok",),
        "GT_CERT_DELIVERY": ("sensor_healthy", "readiness"),
        "GT_CHANGE_SURFACE": ("owner_feature",),
        "GT_EDIT_CHECK": ("owner_feature",),
        "GT_HYPOTHESIS": ("owner_feature",),
        "GT_LOC_RESLOT": ("owner_feature",),
        "GT_PATCH_DELTA": ("owner_feature",),
        "GT_SS_SUBMIT_RED": ("owner_feature", "blockers"),
        "select_catalog": (
            "catalog_version",
            "visible_catalog_ids_sha256",
            "request_payload_sha256",
        ),
    }[feature_id]
    if feature_id == "newfile_precedent":
        return bool(payload.get("precedent_verified") or payload.get("created_files"))
    return all(key in payload for key in required)


# A model-visible payload is grounded only when it names concrete evidence:
# an anchor path, a symbol, a caller, a validator command, a diagnostic, or a
# blocker.  Generic booleans and scope reminders are never grounded and must
# never reach the model as an advisory.  Unlisted features have no grounding
# contract yet and therefore cannot be model-visible.
_GROUNDING_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "localization": ("anchors",),
    "def_partition": ("definition_anchors", "reference_anchors"),
    "caller_contract": ("callers",),
    "newfile_precedent": ("precedent_path",),
    "signature_delta": ("symbol", "before_signature", "after_signature"),
    "covering_red": ("command", "diagnostic", "attribution"),
    "recovery": ("alternate_action",),
    "obligations": ("obligation_ids", "declared_checks"),
    "syntax_result": ("path", "command", "returncode"),
    "submit_refusal": ("blockers",),
    "GT_EDIT_CHECK": ("declared_check", "changed_paths"),
    "GT_LOC_RESLOT": ("selected_anchors",),
}


def feature_payload_grounded(feature_id: str, payload: dict[str, Any]) -> bool:
    """True only when a model-visible payload names concrete evidence."""
    if feature_id == "recovery":
        alternate = payload.get("alternate_action")
        return bool(
            isinstance(alternate, dict)
            and tuple(str(path) for path in alternate.get("paths") or () if str(path))
            and str(payload.get("failure_fingerprint") or "")
        )
    required = _GROUNDING_REQUIREMENTS.get(feature_id)
    if required is None:
        return False
    return all(bool(payload.get(key)) for key in required)


def _snapshot_revision(entries: dict[str, FileState]) -> str:
    digest = hashlib.sha256()
    for path, item in sorted(entries.items()):
        digest.update(path.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(
            "\t".join(
                (
                    item.kind,
                    str(item.size),
                    item.mtime,
                    item.ctime,
                    item.link_target,
                    item.digest,
                )
            ).encode("utf-8", "surrogateescape")
        )
        digest.update(b"\0")
    return digest.hexdigest()


def _same_metadata(left: FileState, right: FileState) -> bool:
    return (
        left.kind,
        left.size,
        left.mtime,
        left.ctime,
        left.link_target,
    ) == (
        right.kind,
        right.size,
        right.mtime,
        right.ctime,
        right.link_target,
    )


def parse_manifest(raw: str, *, elapsed_seconds: float = 0.0) -> WorkspaceSnapshot:
    """Parse the host-only metadata probe emitted by ``find -printf``."""
    entries: dict[str, FileState] = {}
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line:
            continue
        parts = line.split("\t", 5)
        if len(parts) != 6:
            return WorkspaceSnapshot(
                revision="",
                entries={},
                healthy=False,
                reason=f"malformed manifest line {line_number}",
                elapsed_seconds=elapsed_seconds,
            )
        kind, size, mtime, ctime, path, link_target = parts
        try:
            parsed_size = int(size)
        except ValueError:
            return WorkspaceSnapshot(
                revision="",
                entries={},
                healthy=False,
                reason=f"invalid size on manifest line {line_number}",
                elapsed_seconds=elapsed_seconds,
            )
        entries[path] = FileState(kind, parsed_size, mtime, ctime, link_target)
    return WorkspaceSnapshot(
        revision=_snapshot_revision(entries),
        entries=entries,
        healthy=True,
        elapsed_seconds=elapsed_seconds,
    )


def diff_snapshots(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
    *,
    action_id: int,
    command: str,
) -> WorkspaceTransition:
    before_paths = set(before.entries)
    after_paths = set(after.entries)
    shared = before_paths & after_paths
    changed_paths = (
        (after_paths - before_paths)
        | (before_paths - after_paths)
        | {path for path in shared if before.entries[path] != after.entries[path]}
    )
    return WorkspaceTransition(
        action_id=action_id,
        command=command,
        before_revision=before.revision,
        after_revision=after.revision,
        created=tuple(sorted(after_paths - before_paths)),
        modified=tuple(
            sorted(path for path in shared if before.entries[path] != after.entries[path])
        ),
        deleted=tuple(sorted(before_paths - after_paths)),
        sensor_healthy=before.healthy and after.healthy,
        before_contents={
            path: before.entries[path].content
            for path in sorted(changed_paths & before_paths)
            if before.entries[path].content is not None
        },
        after_contents={
            path: after.entries[path].content
            for path in sorted(changed_paths & after_paths)
            if after.entries[path].content is not None
        },
    )


def normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def is_submit_command(command: str) -> bool:
    compact = re.sub(r"[\s'\"\\+]", "", command)
    return _SUBMIT_MARKER in compact


class ValidationStatus(StrEnum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


class ValidationAuthority(StrEnum):
    NONE = "none"
    CUSTOM_PROBE = "custom_probe"
    STANDARD_RUNNER = "standard_runner"
    DECLARED = "declared"
    HOST_SYNTAX = "host_syntax"


@dataclass(frozen=True, slots=True)
class ValidationClassification:
    """Single immutable classification of one shell action's validation meaning.

    The agent classifies each executed action exactly once and shares this
    object with the feature runtime, the evidence ledger, the receipt writer,
    and the metrics extractor so no component ever reparses the command.
    """

    command_class: str
    is_validation: bool
    grounded: bool
    failure_kind: str
    command: str = ""
    normalized_command: str = ""
    declared_check_id: str | None = None
    result_code: int | None = None
    source_revision: str = ""
    workspace_revision: str = ""
    diagnostic_fingerprint: str = ""
    status: ValidationStatus = ValidationStatus.UNKNOWN
    status_attributed: bool = False
    validator_segment_index: int | None = None
    attribution_reason: str = "not_executed"
    shell_connectors: tuple[str, ...] = ()
    authority: ValidationAuthority = ValidationAuthority.NONE
    executable: str | None = None
    requested_timeout_sec: float | None = None
    project_scoped: bool = False

    def with_result(
        self,
        *,
        result_code: int,
        output: str,
        source_revision: str,
        workspace_revision: str,
    ) -> ValidationClassification:
        """Return a copy carrying execution outcome and revision bindings."""
        if not self.is_validation:
            return replace(
                self,
                result_code=result_code,
                source_revision=source_revision,
                workspace_revision=workspace_revision,
                status=ValidationStatus.UNKNOWN,
                status_attributed=False,
                attribution_reason="not_validation",
            )
        terminal_background = bool(
            self.shell_connectors
            and self.validator_segment_index is not None
            and len(self.shell_connectors) > self.validator_segment_index
            and "&" in self.shell_connectors[self.validator_segment_index]
        )
        if terminal_background:
            return replace(
                self,
                result_code=result_code,
                source_revision=source_revision,
                workspace_revision=workspace_revision,
                status=ValidationStatus.PENDING,
                status_attributed=False,
                failure_kind="",
                diagnostic_fingerprint="",
                attribution_reason="validator_dispatched_in_background",
            )
        segments = _shell_segments(self.command)
        if (
            self.validator_segment_index is None
            or self.validator_segment_index != len(segments) - 1
        ):
            return replace(
                self,
                result_code=result_code,
                source_revision=source_revision,
                workspace_revision=workspace_revision,
                status=ValidationStatus.UNKNOWN,
                status_attributed=False,
                failure_kind="",
                diagnostic_fingerprint="",
                attribution_reason="later_shell_segment_owns_action_status",
            )
        status = ValidationStatus.PASS if result_code == 0 else ValidationStatus.FAIL
        failure_kind = classify_failure_kind(result_code, output) if result_code != 0 else ""
        failure_signature = " ".join(
            line.strip() for line in (output or "").splitlines() if _FAILURE_LINE.search(line)
        )[:240]
        fingerprint = hashlib.sha256(
            f"{result_code}\0{failure_signature.lower()}".encode("utf-8", "replace")
        ).hexdigest()[:16]
        return replace(
            self,
            failure_kind=failure_kind,
            result_code=result_code,
            source_revision=source_revision,
            workspace_revision=workspace_revision,
            diagnostic_fingerprint=fingerprint,
            status=status,
            status_attributed=True,
            attribution_reason="terminal_foreground_validator_owns_action_status",
        )


def _shell_segments(command: str) -> tuple[tuple[str, ...], ...]:
    """Compatibility wrapper around the proposal adapter's single parser."""
    return shell_segments(command)


_JS_TEST_RUNNERS = frozenset({"jest", "vitest", "mocha", "ava", "tap"})
_JS_TEST_INTROSPECTION_FLAGS = frozenset(
    {"--help", "-h", "--version", "-v", "--listtests", "--showconfig"}
)
_TSC_INTROSPECTION_FLAGS = frozenset(
    {"--help", "-h", "--version", "-v", "--showconfig", "--init", "--listfilesonly"}
)
_NPX_VALUE_OPTIONS = frozenset({"--package", "-p", "--cache", "--userconfig"})
_NPX_SAFE_OPTIONS = frozenset(
    {"--yes", "-y", "--no", "--quiet", "-q", "--offline", "--prefer-offline"}
)


def _validation_invocation(words: tuple[str, ...]) -> ExecutableInvocation:
    """Unwrap literal package executors only for validation classification.

    General action parsing deliberately retains the package manager as the
    executable. Validation needs the literal package binary while still
    failing closed on dynamic `-c`/`--call` forms and ambiguous options.
    """

    invocation = normalize_executable_invocation(words)
    if invocation.executable is None:
        return invocation
    executable = invocation.executable.lower()
    args = list(invocation.arguments)
    inner: str | None = None
    inner_args: tuple[str, ...] = ()
    extra_wrappers: tuple[str, ...] = ()

    if executable == "npx":
        index = 0
        while index < len(args) and args[index].startswith("-"):
            option = args[index]
            lowered = option.lower()
            if lowered in {"-c", "--call"}:
                return ExecutableInvocation(None, confidence=0.0)
            if "=" in option and lowered.split("=", 1)[0] in _NPX_VALUE_OPTIONS:
                index += 1
                continue
            if lowered in _NPX_SAFE_OPTIONS:
                index += 1
                continue
            if lowered in _NPX_VALUE_OPTIONS:
                if index + 1 >= len(args):
                    return ExecutableInvocation(None, confidence=0.0)
                index += 2
                continue
            return ExecutableInvocation(None, confidence=0.0)
        if index < len(args):
            inner, inner_args = args[index], tuple(args[index + 1 :])
            extra_wrappers = ("npx",)
    elif executable == "npm" and args and args[0].lower() in {"exec", "x"}:
        if len(args) >= 3 and args[1] == "--":
            inner, inner_args = args[2], tuple(args[3:])
            extra_wrappers = (f"npm:{args[0].lower()}",)
        else:
            return ExecutableInvocation(None, confidence=0.0)
    elif executable in {"pnpm", "yarn"} and len(args) >= 2 and args[0].lower() == "exec":
        if any(arg.lower() in {"-c", "--call"} for arg in args[1:]):
            return ExecutableInvocation(None, confidence=0.0)
        inner, inner_args = args[1], tuple(args[2:])
        extra_wrappers = (f"{executable}:exec",)
    elif executable == "bunx" and args:
        if args[0].lower() in {"-c", "--call"} or args[0].startswith("-"):
            return ExecutableInvocation(None, confidence=0.0)
        inner, inner_args = args[0], tuple(args[1:])
        extra_wrappers = ("bunx",)

    if inner is None:
        return invocation
    normalized_inner = inner.rsplit("/", 1)[-1]
    if not normalized_inner or any(character in normalized_inner for character in "$`(){}"):
        return ExecutableInvocation(None, confidence=0.0)
    return ExecutableInvocation(
        executable=normalized_inner,
        arguments=inner_args,
        environment_assignments=invocation.environment_assignments,
        wrappers=(*invocation.wrappers, *extra_wrappers),
        requested_timeout_sec=invocation.requested_timeout_sec,
        confidence=1.0,
    )


def _validation_authority(words: tuple[str, ...]) -> ValidationAuthority:
    """Recognize real validator invocations only from executable positions."""

    invocation = _validation_invocation(words)
    if invocation.executable is None:
        return ValidationAuthority.NONE
    executable = invocation.executable.lower()
    args = invocation.arguments
    if executable in {"pytest", "py.test", "ctest"}:
        return ValidationAuthority.STANDARD_RUNNER
    if executable in _JS_TEST_RUNNERS:
        if any(arg.lower() in _JS_TEST_INTROSPECTION_FLAGS for arg in args):
            return ValidationAuthority.NONE
        return ValidationAuthority.STANDARD_RUNNER
    if executable == "tsc":
        lowered_args = {arg.lower() for arg in args}
        return (
            ValidationAuthority.NONE
            if lowered_args & _TSC_INTROSPECTION_FLAGS
            else ValidationAuthority.STANDARD_RUNNER
        )
    if executable in {"tsx", "node"}:
        script_name = next(
            (arg.rsplit("/", 1)[-1] for arg in args if arg and not arg.startswith("-")),
            "",
        )
        return (
            ValidationAuthority.CUSTOM_PROBE
            if re.search(r"(?:^|[._-])(?:test|tests|verify|check)(?:[._-]|$)", script_name, re.I)
            else ValidationAuthority.NONE
        )
    if executable in {"npm", "pnpm", "yarn", "mvn", "gradle", "cargo", "go"}:
        return (
            ValidationAuthority.STANDARD_RUNNER
            if args and args[0] == "test"
            else ValidationAuthority.NONE
        )
    if executable in {"python", "python3", "python3.12", "python3.11"}:
        if len(args) >= 2 and args[0] == "-m" and args[1] in {"pytest", "unittest"}:
            return ValidationAuthority.STANDARD_RUNNER
        script_name = args[0].rsplit("/", 1)[-1] if args else ""
        return (
            ValidationAuthority.CUSTOM_PROBE
            if (
                script_name
                and re.fullmatch(r"(?:test|tests|verify)[A-Za-z0-9_.-]*\.py", script_name, re.I)
            )
            else ValidationAuthority.NONE
        )
    return (
        ValidationAuthority.STANDARD_RUNNER
        if executable in {"unittest", "test"}
        else ValidationAuthority.NONE
    )


def _recognized_validation(words: tuple[str, ...]) -> bool:
    return _validation_authority(words) is not ValidationAuthority.NONE


def _standard_runner_project_scoped(invocation: Any) -> bool:
    """Whether a standard runner invocation covers its default project scope."""

    if invocation is None or invocation.executable is None:
        return False
    executable = invocation.executable.lower()
    args = tuple(invocation.arguments)
    if executable in {"pytest", "py.test"}:
        return not any(not arg.startswith("-") or "::" in arg for arg in args)
    if executable in {"python", "python3", "python3.12", "python3.11"}:
        if len(args) >= 2 and args[:2] in {("-m", "pytest"), ("-m", "unittest")}:
            return not any(not arg.startswith("-") or "::" in arg for arg in args[2:])
        return False
    if executable == "go":
        return len(args) >= 2 and args[0] == "test" and args[1:] == ("./...",)
    if executable in {"cargo", "npm", "pnpm", "yarn", "mvn", "gradle"}:
        return bool(args and args[0] == "test" and len(args) == 1)
    if executable in {"ctest", "unittest"}:
        return not any(not arg.startswith("-") for arg in args)
    if executable in _JS_TEST_RUNNERS:
        return not any(not arg.startswith("-") and arg.lower() not in {"run"} for arg in args)
    if executable == "tsc":
        return not any(arg in {"-p", "--project"} for arg in args)
    return False


def classify_validation_command(
    command: str, explicit_checks: Iterable[str] = ()
) -> ValidationClassification:
    """Classify validation by shell structure, never by source/comment text."""
    normalized = normalize_command(command)
    segments, connectors = shell_structure(command)
    checks = tuple(dict.fromkeys(normalize_command(item) for item in explicit_checks if item))
    authorities = tuple(_validation_authority(segment) for segment in segments)
    recognized_indices = [
        index
        for index, authority in enumerate(authorities)
        if authority is not ValidationAuthority.NONE
    ]
    declared_check_id: str | None = None
    declared_segment_index: int | None = None
    for check in checks:
        if check == normalized:
            declared_check_id = check
            declared_segment_index = (
                recognized_indices[-1]
                if recognized_indices
                else next(
                    (
                        index
                        for index in range(len(segments) - 1, -1, -1)
                        if (
                            segments[index][0].rsplit("/", 1)[-1].lower() if segments[index] else ""
                        )
                        not in {"cd", "echo", "printf", "true", "false"}
                    ),
                    len(segments) - 1 if segments else None,
                )
            )
            break
        try:
            check_segments = shell_segments(check)
        except ValueError:
            check_segments = ()
        if len(check_segments) == 1:
            check_words = check_segments[0]
            check_invocation = _validation_invocation(check_words)
            for index, segment in enumerate(segments):
                if tuple(segment) == tuple(check_words):
                    declared_check_id = check
                    declared_segment_index = index
                    break
                segment_invocation = _validation_invocation(segment)
                if (
                    check_invocation.confidence == 1.0
                    and segment_invocation.confidence == 1.0
                    and check_invocation.words == segment_invocation.words
                ):
                    declared_check_id = check
                    declared_segment_index = index
                    break
                # A task may name only a verifier artifact.  It is grounded
                # when that exact operand is executed by a validator, never
                # merely because a reader such as ``cat`` mentions the path.
                if (
                    len(check_words) == 1
                    and check_words[0] in segment[1:]
                    and _recognized_validation(segment)
                ):
                    declared_check_id = check
                    declared_segment_index = index
                    break
            if declared_check_id is not None:
                break
    grounded = declared_check_id is not None
    recognized = bool(recognized_indices)
    validator_segment_index = (
        declared_segment_index
        if declared_segment_index is not None
        else recognized_indices[-1]
        if recognized_indices
        else None
    )
    if grounded:
        command_class = "declared_validation"
        authority = ValidationAuthority.DECLARED
    elif recognized:
        command_class = "recognized_validation"
        authority = (
            authorities[validator_segment_index]
            if validator_segment_index is not None
            else ValidationAuthority.NONE
        )
    else:
        command_class = "exploration_or_unknown"
        authority = ValidationAuthority.NONE
    invocation = (
        _validation_invocation(segments[validator_segment_index])
        if validator_segment_index is not None
        else None
    )
    return ValidationClassification(
        command_class=command_class,
        is_validation=grounded or recognized,
        grounded=grounded,
        failure_kind="",
        command=command,
        normalized_command=normalized,
        declared_check_id=declared_check_id,
        validator_segment_index=validator_segment_index,
        shell_connectors=connectors,
        authority=authority,
        executable=invocation.executable if invocation is not None else None,
        requested_timeout_sec=(
            invocation.requested_timeout_sec if invocation is not None else None
        ),
        project_scoped=(
            authority is ValidationAuthority.STANDARD_RUNNER
            and _standard_runner_project_scoped(invocation)
        ),
    )


def _declared_check_id(normalized: str, explicit_checks: Iterable[str]) -> str | None:
    for check in explicit_checks:
        if normalize_command(check) == normalized:
            return check
    return None


def select_declared_check(
    explicit_checks: Iterable[str],
    states: dict[str, str],
) -> str | None:
    """Pick the highest-priority declared check that is not freshly passing.

    Never blindly selects ``explicit_checks[0]``.  Task verifiers and focused
    behavioral checks outrank generic build steps.  A check whose state is
    ``passed`` is satisfied at the current source revision and skipped; a
    ``stale`` check (source changed after its pass) is a candidate again.
    """
    ordered = list(dict.fromkeys(explicit_checks))
    if not ordered:
        return None

    def priority(check: str) -> int:
        lower = check.lower()
        if "verify" in lower or "/test" in lower:
            return 0
        if "pytest" in lower or "unittest" in lower or "test" in lower:
            return 1
        if "build" in lower or "compile" in lower or "setup.py" in lower:
            return 2
        return 3

    ranked = sorted(enumerate(ordered), key=lambda pair: (priority(pair[1]), pair[0]))
    for _, check in ranked:
        if states.get(check) != "passed":
            return check
    return None


def classify_failure_kind(returncode: int, output: str) -> str:
    if returncode in {126, 127} or _MISSING_EXECUTABLE.search(output or ""):
        return "environment_failure"
    return "validation_failure"


def is_check_command(command: str) -> bool:
    return classify_validation_command(command).is_validation


def explicit_check_commands(instruction: str) -> tuple[str, ...]:
    """Return explicitly named validation commands or verifier artifacts.

    Benchmark instructions frequently name a verifier as ``/app/test_x.py``
    rather than spelling out its interpreter.  The path is still grounded task
    evidence: a later command containing it is a declared validation command.
    """
    checks = []
    for line in instruction.splitlines():
        context_declares_validation = bool(
            re.search(r"\b(?:test|verify|check|validate|compile|build)\b", line, re.I)
        )
        for candidate in re.findall(r"`([^`\r\n]+)`", line):
            if (
                is_check_command(candidate) or context_declares_validation
            ) and not is_submit_command(candidate):
                checks.append(normalize_command(candidate))
        stripped = line.strip()
        if (
            context_declares_validation
            and "|" in stripped
            and re.match(r"(?:echo|printf)\b", stripped)
            and re.search(r"\b(?:python|python3|node|ruby|bash)\b", stripped)
        ):
            checks.append(normalize_command(stripped))
    for path in re.findall(
        r"(?<![A-Za-z0-9_.-])(/(?:[A-Za-z0-9_.-]+/)*(?:test|tests|verify)[A-Za-z0-9_.-]*)",
        instruction,
        flags=re.IGNORECASE,
    ):
        checks.append(normalize_command(path))
    return tuple(dict.fromkeys(checks))


def is_grounded_check(command: str, explicit_checks: Iterable[str]) -> bool:
    return classify_validation_command(command, explicit_checks).grounded


@dataclass(slots=True)
class EvidenceLedger:
    """Fresh deterministic evidence with bounded, fail-open submit holds."""

    max_holds: int = 1
    checks: dict[str, CheckEvidence] = field(default_factory=dict)
    outcomes: dict[str, CheckEvidence] = field(default_factory=dict)
    _holds: dict[tuple[str, tuple[str, ...]], int] = field(default_factory=dict)

    def record_check(
        self,
        command: str,
        *,
        returncode: int,
        revision: str,
        grounded: bool,
        classification: ValidationClassification | None = None,
    ) -> None:
        key = normalize_command(command)
        effective_returncode = returncode
        if classification is not None:
            if classification.result_code is None:
                classification = classification.with_result(
                    result_code=returncode,
                    output="",
                    source_revision=revision,
                    workspace_revision=classification.workspace_revision,
                )
            if classification.status not in {ValidationStatus.PASS, ValidationStatus.FAIL}:
                # Intent without an attributable foreground result is not a
                # certificate and cannot create or clear a submit blocker.
                return
            effective_returncode = (
                0
                if classification.status is ValidationStatus.PASS
                else (
                    classification.result_code if classification.result_code not in {None, 0} else 1
                )
            )
        effective_grounded = bool(grounded)
        if (
            classification is not None
            and classification.authority is ValidationAuthority.STANDARD_RUNNER
            and classification.status_attributed
        ):
            # A real current failure is always a safe blocker.  A pass becomes
            # readiness evidence only when the runner covered its default
            # project scope rather than a targeted subset.
            effective_grounded = bool(
                classification.status is ValidationStatus.FAIL
                or (
                    classification.status is ValidationStatus.PASS and classification.project_scoped
                )
            )
        evidence = CheckEvidence(
            command=key,
            returncode=effective_returncode,
            revision=revision,
            grounded=effective_grounded,
            command_class=(classification.command_class if classification else "unknown"),
            failure_kind=(
                classification.failure_kind
                if classification and classification.is_validation
                else ""
            ),
            source_revision=(classification.source_revision if classification else revision),
            authority=(classification.authority.value if classification else ""),
        )
        self.outcomes[key] = evidence
        if effective_returncode == 0:
            self.checks.pop(key, None)
            return
        self.checks[key] = evidence

    def submit_decision(
        self,
        revision: str,
        *,
        sensor_healthy: bool = True,
        plan_partial: bool = False,
        uncovered_obligations: tuple[str, ...] = (),
        validating_evidence_present: bool = True,
        allow_unverified_obligation_hold: bool = True,
    ) -> SubmitDecision:
        """Evaluate submission blockers without treating unknown prose as false.

        ``allow_unverified_obligation_hold`` preserves the separately authorized
        shadow diagnostic.  Active assistive policy must set it false and may
        block only on fresh grounded check evidence.
        """
        if not sensor_healthy:
            return SubmitDecision(InterventionDecision.PASS, reason="sensor degraded")
        blockers = tuple(
            sorted(
                item.command
                for item in self.checks.values()
                if item.grounded and item.revision == revision and item.returncode != 0
            )
        )
        if (
            not blockers
            and allow_unverified_obligation_hold
            and plan_partial
            and uncovered_obligations
            and not validating_evidence_present
        ):
            # The model is submitting while mechanically recognizable task
            # requirements remain unverified and no declared/standard-runner
            # validation has passed at the current revision.  This is a
            # bounded one-shot nudge (todo-gating), not a veto.
            blockers = tuple(
                text if len(text) <= 200 else text[:197] + "..."
                for text in uncovered_obligations[:2]
            )
            reason = "unverified task requirements remain"
        elif not blockers:
            return SubmitDecision(InterventionDecision.PASS)
        else:
            reason = "fresh grounded check is failing"
        hold_key = (revision, blockers)
        used = self._holds.get(hold_key, 0)
        if used >= self.max_holds:
            return SubmitDecision(
                InterventionDecision.PASS,
                blockers,
                reason="bounded hold exhausted",
            )
        self._holds[hold_key] = used + 1
        return SubmitDecision(
            InterventionDecision.HOLD_ONCE,
            blockers,
            reason=reason,
        )

    def readiness_evidence(
        self, revision: str, *, validating_only: bool = False
    ) -> tuple[CheckEvidence, ...]:
        """Return recognized checks whose results belong to the current revision.

        ``validating_only`` restricts the surface to checks with real declared
        or standard-runner authority; host syntax probes and custom probes are
        evidence but never task-validation certificates.
        """
        return tuple(
            sorted(
                (
                    item
                    for item in self.outcomes.values()
                    if item.grounded
                    and item.revision == revision
                    and (
                        not validating_only
                        or item.authority
                        in {
                            ValidationAuthority.DECLARED.value,
                            ValidationAuthority.STANDARD_RUNNER.value,
                        }
                    )
                ),
                key=lambda item: item.command,
            )
        )


def render_runtime_feedback(detail: str, *, limit: int = 320) -> str:
    """Render concise model feedback without exposing private implementation names."""
    cleaned = _PRIVATE_TERMS.sub("runtime", " ".join(detail.split()))
    prefix = "Runtime check: "
    suffix = " Submit again to continue without another hold."
    available = max(0, limit - len(prefix) - len(suffix))
    if len(cleaned) > available:
        cleaned = cleaned[: max(0, available - 3)].rstrip() + "..."
    return (prefix + cleaned + suffix)[:limit]


def render_runtime_advisory(detail: str, *, limit: int = 160) -> str:
    """Render ordinary evidence without falsely implying a submit boundary."""
    cleaned = _PRIVATE_TERMS.sub("runtime", " ".join(detail.split()))
    prefix = "Observed task fact: "
    available = max(0, limit - len(prefix))
    if len(cleaned) > available:
        return ""
    return prefix + cleaned


class WorkspaceSensor:
    """Ephemeral host-side workspace observer; writes no task-container state."""

    def __init__(
        self,
        *,
        max_entries: int = 50_000,
        max_seconds: float = 2.0,
        max_hashes: int = 100,
        max_hash_bytes: int = 50_000_000,
    ) -> None:
        self.max_entries = max_entries
        self.max_seconds = max_seconds
        self.max_hashes = max_hashes
        self.max_hash_bytes = max_hash_bytes
        self._capture_backend = "auto"

    @property
    def capture_backend(self) -> str:
        return self._capture_backend

    @staticmethod
    def _degraded(
        previous: WorkspaceSnapshot | None, reason: str, elapsed: float
    ) -> WorkspaceSnapshot:
        if previous is not None:
            return WorkspaceSnapshot(
                previous.revision,
                previous.entries,
                False,
                reason,
                elapsed,
                binary_heads=previous.binary_heads,
            )
        return WorkspaceSnapshot("", {}, False, reason, elapsed)

    async def scan(
        self,
        environment: Any,
        *,
        cwd: str,
        previous: WorkspaceSnapshot | None = None,
        recorder: HostExecutionRecorder | None = None,
        action_id: int = 0,
        source_revision: str = "",
        tracked_paths: Iterable[str] = (),
        external_paths: Iterable[str] = (),
        shebang_paths: Iterable[str] = (),
        capture_binary_heads: bool = False,
    ) -> WorkspaceSnapshot:
        started = time.monotonic()
        tracked = {
            _workspace_relative_path(path) for path in tracked_paths if str(path or "").strip()
        }
        shebang_candidates = {
            _workspace_relative_path(path) for path in shebang_paths if str(path or "").strip()
        }
        try:
            kwargs = {
                "cwd": cwd,
                "env": {},
                "timeout_sec": max(1, int(self.max_seconds + 0.999)),
            }
            result = (
                await recorder.exec(
                    environment,
                    _MANIFEST_COMMAND,
                    category=HostExecCategory.WORKSPACE_MANIFEST,
                    action_id=action_id,
                    source_revision=source_revision,
                    **kwargs,
                )
                if recorder is not None
                else await environment.exec(_MANIFEST_COMMAND, **kwargs)
            )
        except Exception as exc:  # task images and transports vary; never block
            return self._degraded(
                previous,
                f"manifest command error: {type(exc).__name__}",
                time.monotonic() - started,
            )
        elapsed = time.monotonic() - started
        if result.return_code != 0:
            return self._degraded(previous, "manifest command failed", elapsed)
        raw = result.stdout or ""
        external = tuple(
            sorted({path for raw_path in external_paths if (path := _safe_external_path(raw_path))})
        )
        if external:
            for external_path in external:
                try:
                    command = _external_manifest_command(external_path)
                    external_result = (
                        await recorder.exec(
                            environment,
                            command,
                            category=HostExecCategory.WORKSPACE_MANIFEST,
                            action_id=action_id,
                            source_revision=source_revision,
                            cwd=cwd,
                            env={},
                            timeout_sec=max(1, int(self.max_seconds + 0.999)),
                        )
                        if recorder is not None
                        else await environment.exec(
                            command,
                            cwd=cwd,
                            env={},
                            timeout_sec=max(1, int(self.max_seconds + 0.999)),
                        )
                    )
                except Exception as exc:
                    return self._degraded(
                        previous,
                        f"external manifest command error: {type(exc).__name__}",
                        time.monotonic() - started,
                    )
                if external_result.return_code != 0:
                    return self._degraded(
                        previous,
                        f"external manifest command failed: {external_path}",
                        time.monotonic() - started,
                    )
                raw += external_result.stdout or ""
        if raw.count("\n") > self.max_entries:
            return self._degraded(previous, "workspace entry limit exceeded", elapsed)
        snapshot = parse_manifest(raw, elapsed_seconds=elapsed)
        if not snapshot.healthy:
            return self._degraded(previous, snapshot.reason, elapsed)
        if elapsed > self.max_seconds:
            return replace(snapshot, healthy=False, reason="workspace scan time exceeded")
        comparison_previous = previous if previous is not None and previous.healthy else None

        if comparison_previous is None:
            changed = [
                path
                for path, state in sorted(snapshot.entries.items())
                if state.kind == "f"
                and (
                    is_validation_source(path)
                    or is_graph_input(path, state.content)
                    or _workspace_relative_path(path) in tracked
                    or _workspace_relative_path(path) in shebang_candidates
                    or _may_be_content_signature_source(path)
                )
            ]
        else:
            changed = [
                path
                for path, state in snapshot.entries.items()
                if state.kind == "f"
                and (
                    is_validation_source(path)
                    or is_graph_input(path, state.content)
                    or _workspace_relative_path(path) in tracked
                    or _workspace_relative_path(path) in shebang_candidates
                    or _may_be_content_signature_source(path)
                )
                and (
                    comparison_previous.entries.get(path) is None
                    or not _same_metadata(comparison_previous.entries[path], state)
                )
            ]
        entries = dict(snapshot.entries)
        if comparison_previous is not None:
            for path, state in tuple(entries.items()):
                old = comparison_previous.entries.get(path)
                if old is not None and _same_metadata(old, state) and old.digest:
                    entries[path] = replace(
                        state,
                        digest=old.digest,
                        content=old.content,
                    )

        def bounded_batches(paths: Iterable[str]) -> tuple[tuple[str, ...], ...]:
            """Bound each host command without truncating revision completeness."""

            batches: list[tuple[str, ...]] = []
            batch: list[str] = []
            batch_bytes = 0
            for path in paths:
                size = max(0, int(entries[path].size))
                if batch and (
                    len(batch) >= max(1, self.max_hashes)
                    or batch_bytes + size > max(1, self.max_hash_bytes)
                ):
                    batches.append(tuple(batch))
                    batch = []
                    batch_bytes = 0
                batch.append(path)
                batch_bytes += size
            if batch:
                batches.append(tuple(batch))
            return tuple(batches)

        for hash_paths in bounded_batches(changed):
            command = "sha256sum -- " + " ".join(shlex.quote(path) for path in hash_paths)
            try:
                kwargs = {"cwd": cwd, "env": {}, "timeout_sec": 10}
                hashes = (
                    await recorder.exec(
                        environment,
                        command,
                        category=HostExecCategory.WORKSPACE_HASH,
                        action_id=action_id,
                        source_revision=source_revision,
                        **kwargs,
                    )
                    if recorder is not None
                    else await environment.exec(command, **kwargs)
                )
            except Exception as exc:  # hashing is evidence, never task authority
                return replace(
                    snapshot,
                    healthy=False,
                    reason=f"changed-file hashing error: {type(exc).__name__}",
                )
            lines = (hashes.stdout or "").splitlines()
            if hashes.return_code != 0 or len(lines) != len(hash_paths):
                return replace(snapshot, healthy=False, reason="changed-file hashing failed")
            for path, line in zip(hash_paths, lines, strict=True):
                digest = line.split(maxsplit=1)[0]
                if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    return replace(snapshot, healthy=False, reason="invalid changed-file hash")
                entries[path] = replace(entries[path], digest=digest.lower())
        capture_paths = [
            path
            for path in changed
            if (
                is_validation_source(path)
                or is_graph_input(path, entries[path].content)
                or _workspace_relative_path(path) in shebang_candidates
                or _may_be_content_signature_source(path)
            )
            and entries[path].size <= _MAX_SOURCE_CAPTURE_BYTES
        ]
        for capture_batch in bounded_batches(capture_paths):
            script = (
                "import base64,json,pathlib,sys;"
                "print(json.dumps({p:base64.b64encode(pathlib.Path(p).read_bytes()).decode()"
                " for p in sys.argv[1:]}))"
            )
            command = (
                "python3 -c "
                + shlex.quote(script)
                + " "
                + " ".join(shlex.quote(path) for path in capture_batch)
            )
            try:
                kwargs = {"cwd": cwd, "env": {}, "timeout_sec": 10}
                encoded: dict[str, Any] = {}
                if self._capture_backend != "posix_base64":
                    captured = (
                        await recorder.exec(
                            environment,
                            command,
                            category=HostExecCategory.WORKSPACE_CAPTURE,
                            action_id=action_id,
                            source_revision=source_revision,
                            **kwargs,
                        )
                        if recorder is not None
                        else await environment.exec(command, **kwargs)
                    )
                    if captured.return_code == 0:
                        try:
                            parsed = json.loads(captured.stdout or "{}")
                            if isinstance(parsed, dict):
                                encoded = parsed
                        except (TypeError, ValueError):
                            encoded = {}
                    else:
                        self._capture_backend = "posix_base64"

                def apply_encoded(
                    values: Mapping[str, Any], paths: tuple[str, ...] = capture_batch
                ) -> set[str]:
                    applied: set[str] = set()
                    for path in paths:
                        value = values.get(path)
                        if not isinstance(value, str):
                            continue
                        try:
                            content = base64.b64decode(value, validate=True).decode(
                                "utf-8", "replace"
                            )
                        except (ValueError, UnicodeError):
                            continue
                        entries[path] = replace(entries[path], content=content)
                        applied.add(path)
                    return applied

                captured_paths = apply_encoded(encoded)
                if len(captured_paths) == len(capture_batch):
                    self._capture_backend = "python_json"
                missing_paths = [path for path in capture_batch if path not in captured_paths]
                if missing_paths:
                    # Task images are not required to contain Python.  Keep the
                    # fast JSON path above, but fall back to POSIX base64 so a
                    # missing interpreter cannot make the repository mirror
                    # stale after an authored source edit.  Paths come from the
                    # validated workspace manifest and remain shell-quoted.
                    fallback_command = (
                        "for p in "
                        + " ".join(shlex.quote(path) for path in missing_paths)
                        + "; do printf '%s\\t' \"$p\"; "
                        + "base64 \"$p\" | tr -d '\\n'; printf '\\n'; done"
                    )
                    fallback = (
                        await recorder.exec(
                            environment,
                            fallback_command,
                            category=HostExecCategory.WORKSPACE_CAPTURE,
                            action_id=action_id,
                            source_revision=source_revision,
                            **kwargs,
                        )
                        if recorder is not None
                        else await environment.exec(fallback_command, **kwargs)
                    )
                    if fallback.return_code == 0:
                        fallback_values: dict[str, str] = {}
                        for line in (fallback.stdout or "").splitlines():
                            path, separator, value = line.partition("\t")
                            if separator and path in missing_paths:
                                fallback_values[path] = value
                        fallback_applied = apply_encoded(fallback_values)
                        if len(fallback_applied) == len(missing_paths):
                            self._capture_backend = "posix_base64"
            except Exception:
                # Content witnesses improve semantic features, but metadata and
                # hashes remain authoritative if both capture mechanisms fail.
                pass
        binary_heads = dict(previous.binary_heads) if previous is not None else {}
        if capture_binary_heads:
            head_candidates: list[str] = []
            for path, state in sorted(entries.items()):
                if len(head_candidates) >= _BINARY_HEAD_MAX_FILES:
                    break
                if state.kind != "f":
                    continue
                relative = _workspace_relative_path(path)
                if (
                    is_validation_source(path)
                    or relative in tracked
                    or relative in shebang_candidates
                ):
                    continue
                parts = relative.split("/")
                if any(part in _BINARY_HEAD_SKIP_DIRS for part in parts[:-1]):
                    continue
                if parts[-1] in _BINARY_HEAD_SKIP_FILES:
                    continue
                old = comparison_previous.entries.get(path) if comparison_previous else None
                if old is not None and _same_metadata(old, state) and path in binary_heads:
                    continue
                head_candidates.append(path)
            for head_batch in tuple(
                head_candidates[offset : offset + 8]
                for offset in range(0, len(head_candidates), 8)
            ):
                script = (
                    "import base64,json,sys;"
                    "print(json.dumps({p:base64.b64encode(open(p,'rb').read("
                    + str(_BINARY_HEAD_BYTES)
                    + ")).decode() for p in sys.argv[1:]}))"
                )
                command = (
                    "python3 -c "
                    + shlex.quote(script)
                    + " "
                    + " ".join(shlex.quote(path) for path in head_batch)
                )
                try:
                    kwargs = {"cwd": cwd, "env": {}, "timeout_sec": 10}
                    encoded_heads: dict[str, Any] = {}
                    if self._capture_backend != "posix_base64":
                        captured = (
                            await recorder.exec(
                                environment,
                                command,
                                category=HostExecCategory.WORKSPACE_CAPTURE,
                                action_id=action_id,
                                source_revision=source_revision,
                                **kwargs,
                            )
                            if recorder is not None
                            else await environment.exec(command, **kwargs)
                        )
                        if captured.return_code == 0:
                            try:
                                parsed = json.loads(captured.stdout or "{}")
                                if isinstance(parsed, dict):
                                    encoded_heads = parsed
                            except (TypeError, ValueError):
                                encoded_heads = {}
                        else:
                            self._capture_backend = "posix_base64"
                    missing_heads: list[str] = []
                    for path in head_batch:
                        value = encoded_heads.get(path)
                        if not isinstance(value, str):
                            missing_heads.append(path)
                            continue
                        try:
                            head_bytes = base64.b64decode(value, validate=True)
                        except (ValueError, TypeError):
                            missing_heads.append(path)
                            continue
                        binary_heads[path] = head_bytes[:_BINARY_HEAD_BYTES]
                    if missing_heads:
                        fallback_command = (
                            "for p in "
                            + " ".join(shlex.quote(path) for path in missing_heads)
                            + "; do printf '%s\\t' \"$p\"; "
                            + "head -c "
                            + str(_BINARY_HEAD_BYTES)
                            + " \"$p\" | base64 | tr -d '\\n'; printf '\\n'; done"
                        )
                        fallback = (
                            await recorder.exec(
                                environment,
                                fallback_command,
                                category=HostExecCategory.WORKSPACE_CAPTURE,
                                action_id=action_id,
                                source_revision=source_revision,
                                **kwargs,
                            )
                            if recorder is not None
                            else await environment.exec(fallback_command, **kwargs)
                        )
                        if fallback.return_code == 0:
                            for line in (fallback.stdout or "").splitlines():
                                path, separator, value = line.partition("\t")
                                if separator and path in missing_heads and value:
                                    try:
                                        head_bytes = base64.b64decode(value, validate=True)
                                    except (ValueError, TypeError):
                                        continue
                                    binary_heads[path] = head_bytes[:_BINARY_HEAD_BYTES]
                except Exception:
                    pass
        return replace(
            snapshot,
            entries=entries,
            revision=_snapshot_revision(entries),
            binary_heads=binary_heads,
        )


def lint_commands(paths: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Return conservative, no-artifact syntax probes for changed files."""
    commands: list[tuple[str, str]] = []
    for path in sorted(set(paths)):
        command = syntax_probe_command(path)
        if command is None:
            continue
        commands.append((path, command))
        if len(commands) >= 4:
            break
    return tuple(commands)


@dataclass(frozen=True, slots=True)
class FeatureReceipt:
    """Private, content-minimal proof that one central feature was evaluated."""

    feature_id: str
    kind: str
    boundary: str
    action_id: int
    revision: str
    decision: str
    reason: str
    payload: dict[str, Any]
    fresh: bool
    model_visible: bool
    delivery_status: str = "pending"
    delivery_reason: str = ""
    source_revision: str = ""
    source_epoch: int = 0


class FeatureDeliveryDisposition(StrEnum):
    """Exhaustive provider-delivery accounting for one feature receipt."""

    PRIVATE_INELIGIBLE = "private_ineligible"
    CANDIDATE_DELIVERED = "candidate_delivered"
    CANDIDATE_REPRESENTED = "candidate_represented"
    CANDIDATE_WINDOW_UNSELECTED = "candidate_window_unselected"
    CANDIDATE_STALE = "candidate_stale"
    CANDIDATE_BUDGET_REJECTED = "candidate_budget_rejected"
    CANDIDATE_POLICY_REJECTED = "candidate_policy_rejected"
    NO_ELIGIBLE_MODEL_CALL = "no_eligible_model_call"


class SearchScope(StrEnum):
    WORKSPACE = "workspace"
    TARGETS = "targets"
    STDIN_FILTER = "stdin_filter"
    EXTERNAL = "external"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class SearchObservation:
    action_id: str
    query: str
    scope: SearchScope
    targets: tuple[str, ...]
    anchors: tuple[dict[str, Any], ...]
    output_format: str
    confidence: float
    source_revision: str
    output_sha256: str


@dataclass(frozen=True, slots=True)
class FeatureOpportunityReceipt:
    feature_id: str
    boundary: str
    action_id: int
    source_revision: str
    evidence_status: str
    reason_code: str
    evidence_sha256: str
    effect_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "boundary": self.boundary,
            "action_id": self.action_id,
            "source_revision": self.source_revision,
            "evidence_status": self.evidence_status,
            "reason_code": self.reason_code,
            "evidence_sha256": self.evidence_sha256,
            "effect_id": self.effect_id,
        }


@dataclass(slots=True)
class CentralControllerState:
    """Operational state reduced from feature payloads inside Mini-SWE's loop."""

    contract: dict[str, Any] = field(default_factory=dict)
    localization: dict[str, Any] = field(default_factory=dict)
    impact: dict[str, Any] = field(default_factory=dict)
    change_surface: dict[str, Any] = field(default_factory=dict)
    patch_delta: dict[str, Any] = field(default_factory=dict)
    validation_plan: dict[str, Any] = field(default_factory=dict)
    validation_results: dict[str, Any] = field(default_factory=dict)
    failure_state: dict[str, Any] = field(default_factory=dict)
    submission_state: dict[str, Any] = field(default_factory=dict)
    certificate: dict[str, Any] = field(default_factory=dict)
    source_revision: str = ""
    workspace_revision: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": dict(self.contract),
            "localization": dict(self.localization),
            "impact": dict(self.impact),
            "change_surface": dict(self.change_surface),
            "patch_delta": dict(self.patch_delta),
            "validation_plan": dict(self.validation_plan),
            "validation_results": dict(self.validation_results),
            "failure_state": dict(self.failure_state),
            "submission_state": dict(self.submission_state),
            "certificate": dict(self.certificate),
            "source_revision": self.source_revision,
            "workspace_revision": self.workspace_revision,
        }


class CentralFeatureRuntime:
    """Host-side trigger router for action-bound direct features.

    This deliberately does not scrape task source or inject a second tool. It
    observes only action metadata, command text, return status, and the
    non-Git workspace transition already collected by :class:`WorkspaceSensor`.
    The task-start select_catalog lifecycle is merged by the harness at its
    provider boundary. Every other feature is enabled by default, but a feature
    is marked DELIVERED only when its conservative trigger is present. CAP rows
    are emitted with their owning FACT, making ownership and delivery auditable without pretending
    that an untriggered feature fired.
    """

    _SEARCH = re.compile(r"(?:^|[;&|\s])(rg|grep|find|ack|ag)(?:\s|$)", re.I)
    _DEFINITION = re.compile(r"\b(?:def|class|function|func|sub|procedure)\b|=>")
    _CALLSITE = re.compile(r"\b(?:caller|callers|call\s*site|references?)\b", re.I)
    _EDIT = re.compile(
        r"(?:apply_patch|sed\s+-i|perl\s+-i|python(?:3)?\s+-c|ruby\s+-i|"
        r"awk\s+.*>|\b(?:touch|tee|cp|mv)\b|>>|\becho\b.*>)",
        re.I,
    )
    _SIGNATURE = re.compile(
        r"\b(?:def|function|func|sub|procedure|class)\s+[A-Za-z_]\w*\s*\(",
        re.I,
    )
    _FAILURE = re.compile(r"\b(?:fail(?:ed|ure)?|error|exception|traceback|red)\b", re.I)
    _PRECEDENT = re.compile(r"\b(?:precedent|sibling|registry|existing|pattern)\b", re.I)

    def __init__(
        self,
        *,
        enabled: bool = True,
        model_visible: bool = False,
        max_guidance_events: int = 4,
        max_guidance_chars: int = 640,
    ) -> None:
        self.enabled = enabled
        self.model_visible = model_visible
        self.receipts: list[FeatureReceipt] = []
        self._seen: set[tuple[str, int, str]] = set()
        self._failed_actions: dict[tuple[str, str, int, str], int] = {}
        self._searched = False
        self._precedent_verified = False
        self._initial_source_paths: set[str] | None = None
        self._post_edit_checks = 0
        self._feedback_cursor = 0
        self._feedback_calls = 0
        self._guidance_events = 0
        self._guidance_chars = 0
        self._guidance_features: list[str] = []
        self._guidance_candidates = 0
        self._guidance_suppressed = 0
        self._guided_keys: set[tuple[str, str, str]] = set()
        self._prepared_guidance: dict[str, Any] | None = None
        self._explicit_checks: tuple[str, ...] = ()
        self.max_guidance_events = max_guidance_events
        self.max_guidance_chars = max_guidance_chars
        self._action_metrics: dict[str, int] = {
            "observed_actions": 0,
            "successful_actions": 0,
            "failed_actions": 0,
            "search_actions": 0,
            "check_actions": 0,
            "workspace_change_actions": 0,
            "no_change_actions": 0,
            "repeated_commands": 0,
            "created_paths": 0,
            "modified_paths": 0,
            "deleted_paths": 0,
            "command_chars": 0,
            "observation_chars": 0,
            "lint_checks": 0,
            "lint_passes": 0,
            "lint_failures": 0,
            "engine_actions": 0,
            "submit_attempts": 0,
            "submit_holds": 0,
            "submit_risks": 0,
            "batch_interrupts": 0,
            "interrupted_actions": 0,
        }
        self._command_counts: dict[str, int] = {}
        self._lifecycle: dict[str, dict[str, Any]] = {}
        self._workspace_edited = False
        self._unvalidated_material_edits = 0
        self._validation_debt_notified = False
        self._consumer_paths: dict[str, list[str]] = {}
        self._effects: list[FeatureEffect] = []
        self._effect_cursor = 0
        self._controller_state = CentralControllerState()
        self._effect_applications: list[dict[str, Any]] = []
        # Additive provenance only.  This trace never participates in routing
        # or policy; it records whether an already-existing consumer path was
        # actually exercised.
        self._effect_trace: list[dict[str, Any]] = []
        self._last_context_compiler_call = 0
        self._producer_events: list[dict[str, Any]] = []
        self._pending_state_reads: list[dict[str, Any]] = []
        self._batch_interrupts: list[dict[str, Any]] = []
        self._task_deliverables: set[str] = set()
        self._source_epoch = 0
        self._declared_check_states: dict[str, str] = {}
        self._validation_log: list[dict[str, Any]] = []
        self._recent_source_paths: tuple[str, ...] = ()
        self._precedent_path = ""
        self._last_edit: dict[str, Any] | None = None
        self._latest_validation: dict[str, Any] | None = None
        self._latest_failure: dict[str, Any] | None = None
        self._recent_reads: list[dict[str, Any]] = []
        self._submit_risk_revisions: set[str] = set()
        self._current_source_revision = ""
        self._decisions = SemanticDecisionEngine(max_frame_chars=320)
        self._claim_receipts: dict[str, tuple[str, int]] = {}
        self._preflight_receipts: list[dict[str, Any]] = []
        self._action_cycles: dict[str, ActionCycleReceipt] = {}
        self._structural_evidence: dict[str, Any] = {}
        self._preflight_intervention_keys: set[str] = set()
        self._feature_opportunities: list[FeatureOpportunityReceipt] = []
        self._certification_decisions: list[dict[str, Any]] = []

    def changed_symbols_for_action(
        self, *, action_id: int, source_revision: str
    ) -> tuple[str, ...]:
        """Return only source-derived symbols changed by one completed action.

        This is a typed bridge for diff-to-graph binding. It reads the existing
        signature-delta producer receipt and never re-parses a command or
        invents a symbol from lexical similarity.
        """

        symbols: list[str] = []
        for receipt in self.receipts:
            if (
                receipt.feature_id != "signature_delta"
                or int(receipt.action_id) != int(action_id)
                or str(receipt.source_revision or "") != str(source_revision or "")
            ):
                continue
            payload = receipt.payload or {}
            for row in payload.get("signature_deltas") or ():
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip()
                if symbol:
                    symbols.append(symbol)
            symbol = str(payload.get("symbol") or "").strip()
            if symbol:
                symbols.append(symbol)
        return tuple(dict.fromkeys(symbols))

    def _mark_lifecycle(self, phase: str, *, action_id: int, status: str = "observed") -> None:
        item = self._lifecycle.setdefault(
            phase,
            {
                "first_action": action_id,
                "last_action": action_id,
                "status": status,
                "observations": 0,
            },
        )
        item["last_action"] = action_id
        item["status"] = status
        item["observations"] += 1

    @staticmethod
    def _search_anchors(
        output: str,
        *,
        targets: tuple[str, ...] = (),
        snapshot: WorkspaceSnapshot | None = None,
        limit: int = 8,
    ) -> tuple[list[dict[str, Any]], str]:
        """Parse bounded path-qualified or unambiguous single-target anchors."""
        anchors: list[dict[str, Any]] = []
        output_format = "unsupported"
        canonical_targets = tuple(
            dict.fromkeys(_workspace_relative_path(path) for path in targets if path)
        )
        known_paths = set(snapshot.entries) if snapshot is not None else set()

        def admitted(path: str) -> str:
            canonical = _workspace_relative_path(path)
            if not canonical or canonical.startswith("../") or canonical.startswith("/"):
                return ""
            if known_paths and canonical not in known_paths:
                return ""
            return canonical

        for line in (output or "").splitlines():
            match = re.match(r"^([^:\s][^:]*):(\d+):(.*)$", line)
            if match:
                path = admitted(match.group(1).strip())
                if not path:
                    continue
                anchors.append(
                    {
                        "path": path,
                        "line": int(match.group(2)),
                        "text": match.group(3).strip()[:80],
                    }
                )
                output_format = "path_line_text"
            else:
                single = re.match(r"^(\d+):(.*)$", line)
                if single and len(canonical_targets) == 1:
                    path = admitted(canonical_targets[0])
                    if path:
                        anchors.append(
                            {
                                "path": path,
                                "line": int(single.group(1)),
                                "text": single.group(2).strip()[:80],
                            }
                        )
                        output_format = "line_text_single_target"
                        if len(anchors) >= limit:
                            break
                        continue
                path = admitted(line.strip())
                if path and path in known_paths:
                    anchors.append({"path": path, "line": 0, "text": ""})
                    output_format = "path_only"
            if len(anchors) >= limit:
                break
        return anchors, output_format

    @staticmethod
    def _search_observation(
        proposed: ProposedAction,
        output: str,
        *,
        snapshot: WorkspaceSnapshot | None,
        source_revision: str,
    ) -> tuple[SearchObservation | None, str]:
        search_operations = tuple(
            operation
            for operation in proposed.operations
            if operation.operation == ActionOperation.SEARCH
        )
        if not search_operations:
            return None, "no_typed_repository_search"
        targets = tuple(
            dict.fromkeys(
                target.path
                for operation in search_operations
                for target in operation.targets
                if target.path
            )
        )
        receives_stdin = any(
            operation.segment_index > 0
            and operation.segment_index - 1 < len(proposed.shell_connectors)
            and proposed.shell_connectors[operation.segment_index - 1] == "|"
            and not operation.targets
            for operation in search_operations
        )
        executables = {operation.executable for operation in search_operations}
        if receives_stdin:
            scope = SearchScope.STDIN_FILTER
            reason = "stdin_filter_not_repository_search"
        elif targets and all(
            path.startswith("/") and not path.startswith("/app/") for path in targets
        ):
            scope = SearchScope.EXTERNAL
            reason = "external_search_scope"
        elif targets:
            scope = SearchScope.TARGETS
            reason = "typed_target_search"
        elif executables <= {"rg", "ack", "ag"}:
            scope = SearchScope.WORKSPACE
            reason = "typed_workspace_search"
        elif executables == {"find"}:
            scope = SearchScope.AMBIGUOUS
            reason = "find_scope_unresolved"
        else:
            scope = SearchScope.AMBIGUOUS
            reason = "search_scope_ambiguous"
        if scope not in {SearchScope.WORKSPACE, SearchScope.TARGETS}:
            return SearchObservation(
                action_id=proposed.action_id,
                query=proposed.raw_command[:240],
                scope=scope,
                targets=targets,
                anchors=(),
                output_format="not_parsed",
                confidence=proposed.parser_confidence,
                source_revision=source_revision,
                output_sha256=hashlib.sha256(output.encode("utf-8", "replace")).hexdigest(),
            ), reason
        anchors, output_format = CentralFeatureRuntime._search_anchors(
            output,
            targets=targets,
            snapshot=snapshot,
        )
        return SearchObservation(
            action_id=proposed.action_id,
            query=proposed.raw_command[:240],
            scope=scope,
            targets=targets,
            anchors=tuple(anchors),
            output_format=output_format,
            confidence=proposed.parser_confidence,
            source_revision=source_revision,
            output_sha256=hashlib.sha256(output.encode("utf-8", "replace")).hexdigest(),
        ), reason

    def _record_feature_opportunity(
        self,
        *,
        feature_id: str,
        boundary: str,
        action_id: int,
        source_revision: str,
        evidence_status: str,
        reason_code: str,
        evidence: Any,
        effect_id: str | None = None,
    ) -> None:
        encoded = json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8", "replace")
        row = FeatureOpportunityReceipt(
            feature_id=feature_id,
            boundary=boundary,
            action_id=action_id,
            source_revision=source_revision,
            evidence_status=evidence_status,
            reason_code=reason_code,
            evidence_sha256=hashlib.sha256(encoded).hexdigest(),
            effect_id=effect_id,
        )
        identity = (
            row.feature_id,
            row.boundary,
            row.action_id,
            row.evidence_status,
            row.reason_code,
            row.evidence_sha256,
        )
        if any(
            (
                item.feature_id,
                item.boundary,
                item.action_id,
                item.evidence_status,
                item.reason_code,
                item.evidence_sha256,
            )
            == identity
            for item in self._feature_opportunities
        ):
            return
        self._feature_opportunities.append(row)

    @staticmethod
    def _spec(feature_id: str) -> dict[str, str]:
        return next(item for item in CENTRAL_FEATURES if item["id"] == feature_id)

    def _emit(
        self,
        feature_id: str,
        *,
        boundary: str,
        action_id: int,
        revision: str,
        decision: str = "DELIVERED",
        reason: str,
        payload: dict[str, Any] | None = None,
        source_revision: str | None = None,
        source_epoch: int | None = None,
    ) -> None:
        if not self.enabled:
            return
        key = (feature_id, action_id, revision)
        if key in self._seen:
            return
        self._seen.add(key)
        spec = self._spec(feature_id)
        candidate_payload = payload or self._payload(feature_id, boundary, reason)
        if not feature_payload_valid(
            feature_id,
            candidate_payload,
            boundary=boundary,
            revision=revision,
            fresh=True,
        ):
            self._record_feature_opportunity(
                feature_id=feature_id,
                boundary=boundary,
                action_id=action_id,
                source_revision=source_revision if source_revision is not None else revision,
                evidence_status="ambiguous_evidence",
                reason_code="invalid_feature_payload",
                evidence=candidate_payload,
            )
            return
        self.receipts.append(
            FeatureReceipt(
                feature_id=feature_id,
                kind=spec["kind"],
                boundary=boundary,
                action_id=action_id,
                revision=revision,
                decision=decision,
                reason=reason,
                payload=candidate_payload,
                fresh=True,
                model_visible=self._is_model_actionable(feature_id, decision, candidate_payload),
                source_revision=source_revision if source_revision is not None else revision,
                source_epoch=(source_epoch if source_epoch is not None else self._source_epoch),
            )
        )
        self._route_effect(self.receipts[-1])
        self._record_feature_opportunity(
            feature_id=feature_id,
            boundary=boundary,
            action_id=action_id,
            source_revision=source_revision if source_revision is not None else revision,
            evidence_status="eligible",
            reason_code=reason,
            evidence=candidate_payload,
            effect_id=(self._effects[-1].receipt_id if self._effects else None),
        )
        self._register_decision_claim(self.receipts[-1])

    def _suppress_receipt_delivery(self, receipt: FeatureReceipt, *, reason: str) -> None:
        """Mark a grounded duplicate as private instead of orphaning it.

        Semantic deduplication is intentional: repeating the same failure at
        an unchanged source revision must not create a second model sentence.
        The old implementation left the receipt/effect marked
        ``model_visible`` even when the existing claim was already exposed,
        making delivery accounting report a payload that never reached the
        model.  Keep the engine effect and its audit trail, but make the
        suppression explicit and machine-auditable.
        """
        if not reason:
            reason = "semantic_duplicate"
        for index in range(len(self.receipts) - 1, -1, -1):
            current = self.receipts[index]
            if current is receipt or (
                current.feature_id == receipt.feature_id
                and current.action_id == receipt.action_id
                and current.revision == receipt.revision
            ):
                self.receipts[index] = replace(
                    current,
                    model_visible=False,
                    delivery_status="suppressed",
                    delivery_reason=reason,
                )
                for effect_index, effect in enumerate(self._effects):
                    if (
                        effect.feature_id == current.feature_id
                        and effect.evidence_action == current.action_id
                    ):
                        self._effects[effect_index] = replace(
                            effect,
                            model_visible=False,
                            delivery_status="suppressed",
                            delivery_reason=reason,
                        )
                return

    def _suppress_unselected_first_window(
        self,
        *,
        call: int,
        selected_items: Iterable[FeatureReceipt] = (),
    ) -> None:
        """Make first-window arbitration explicit and prohibit late leakage."""

        selected_keys = {
            (item.feature_id, item.action_id, item.source_revision) for item in selected_items
        }
        evidence_action = max(0, int(call) - 1)
        for item in tuple(self.receipts):
            key = (item.feature_id, item.action_id, item.source_revision)
            if (
                item.model_visible
                and item.delivery_status == "pending"
                and item.action_id == evidence_action
                and key not in selected_keys
            ):
                self._suppress_receipt_delivery(
                    item,
                    reason="not_selected_first_eligible_request",
                )

    @staticmethod
    def _payload(feature_id: str, boundary: str, reason: str) -> dict[str, Any]:
        messages = {
            "caller_contract": "Inspect the verified callers before changing this callable.",
            "covering_red": "A validation command failed; inspect its result before changing code.",
            "def_partition": "Separate definitions from references before editing.",
            "localization": "Inspect the most relevant source locations from the search result.",
            "newfile_precedent": "Follow the verified repository precedent for the new file.",
            "obligations": "Keep the requested task requirements in scope.",
            "recovery": (
                "The same validation failure repeated at an unchanged source revision."
            ),
            "signature_delta": "Inspect and repair callers affected by the signature edit.",
            "submit_refusal": "Resolve the fresh required failure before submitting again.",
            "syntax_result": "Repair the syntax or compiler failure on the edited file.",
        }
        return {
            "message": messages.get(feature_id, "Review the runtime evidence before continuing."),
            "boundary": boundary,
            "reason": reason,
        }

    @classmethod
    def _explicit_signature_replacement(cls, command: str) -> tuple[str, str] | None:
        """Return deterministic before/after fragments from an explicit substitution."""
        if not re.search(r"\bsed\s+-i\b", command, re.I):
            return None
        match = re.search(r"s(?P<sep>[/#|])(?P<before>.*?)(?P=sep)(?P<after>.*?)(?P=sep)", command)
        if not match:
            return None
        before = match.group("before")
        after = match.group("after")
        if before == after or not cls._SIGNATURE.search(f"{before}("):
            return None
        if not cls._SIGNATURE.search(f"{after}("):
            return None
        return before[:120], after[:120]

    @staticmethod
    def _source_signatures(path: str, content: str) -> dict[str, str]:
        """Extract deterministic callable signatures from a bounded source witness."""
        signatures: dict[str, str] = {}
        if path.lower().endswith(".py"):
            try:
                tree = ast.parse(content)
            except SyntaxError:
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    args = ast.unparse(node.args)
                    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                    signatures[node.name] = f"{prefix} {node.name}({args})"
                return signatures
        pattern = re.compile(
            r"^\s*(?:async\s+)?(?:def|function|func|sub|procedure)\s+"
            r"(?P<name>[A-Za-z_]\w*)\s*\([^\n]{0,240}\)",
            re.I | re.M,
        )
        for match in pattern.finditer(content):
            signatures[match.group("name")] = " ".join(match.group(0).split())[:280]
        return signatures

    @classmethod
    def _semantic_signature_deltas(cls, transition: WorkspaceTransition) -> list[dict[str, str]]:
        deltas: list[dict[str, str]] = []
        for path in sorted(set(transition.before_contents) & set(transition.after_contents)):
            before = cls._source_signatures(path, transition.before_contents[path])
            after = cls._source_signatures(path, transition.after_contents[path])
            for symbol in sorted(set(before) & set(after)):
                if before[symbol] == after[symbol]:
                    continue
                deltas.append(
                    {
                        "path": path,
                        "symbol": symbol,
                        "before_signature": before[symbol],
                        "after_signature": after[symbol],
                    }
                )
        return deltas

    @staticmethod
    def _decision_mapping(
        feature_id: str,
    ) -> tuple[SemanticClaimKind, DecisionNeedKind] | None:
        return {
            "syntax_result": (
                SemanticClaimKind.FAILURE,
                DecisionNeedKind.REPAIR_FAILURE,
            ),
            "covering_red": (
                SemanticClaimKind.FAILURE,
                DecisionNeedKind.REPAIR_FAILURE,
            ),
            "recovery": (
                SemanticClaimKind.RECOVERY,
                DecisionNeedKind.RECOVER_FAILURE,
            ),
            "signature_delta": (
                SemanticClaimKind.IMPACT,
                DecisionNeedKind.REPAIR_IMPACT,
            ),
            "newfile_precedent": (
                SemanticClaimKind.IMPACT,
                DecisionNeedKind.REPAIR_IMPACT,
            ),
            "GT_EDIT_CHECK": (
                SemanticClaimKind.VALIDATION,
                DecisionNeedKind.VALIDATE_CHANGE,
            ),
            "GT_LOC_RESLOT": (
                SemanticClaimKind.LOCALIZATION,
                DecisionNeedKind.LOCALIZE_TASK,
            ),
            "submit_refusal": (
                SemanticClaimKind.SUBMISSION,
                DecisionNeedKind.SUBMIT_SAFELY,
            ),
        }.get(feature_id)

    @staticmethod
    def _claim_anchors(receipt: FeatureReceipt) -> tuple[str, ...]:
        payload = receipt.payload
        feature_id = receipt.feature_id
        anchors: list[str] = []
        if feature_id == "syntax_result":
            path = str(payload.get("path") or "")
            if path:
                anchors.append(path)
        elif feature_id == "covering_red":
            anchors.extend(
                str(payload.get(key) or "") for key in ("command", "attribution", "diagnostic")
            )
        elif feature_id == "recovery":
            alternate = payload.get("alternate_action") or {}
            anchors.extend(str(path) for path in alternate.get("paths") or ())
            anchors.append(str(alternate.get("discriminator") or ""))
        elif feature_id == "signature_delta":
            anchors.extend(str(path) for path in payload.get("changed_paths") or ())
            symbol = str(payload.get("symbol") or "")
            if symbol:
                anchors.append(symbol)
            anchors.extend(
                str(item.get("caller_path") or item.get("path") or "")
                for item in payload.get("callers") or ()
            )
        elif feature_id == "newfile_precedent":
            anchors.extend(str(path) for path in payload.get("created_files") or ())
            anchors.append(str(payload.get("precedent_path") or ""))
        elif feature_id == "GT_EDIT_CHECK":
            anchors.extend(str(path) for path in payload.get("changed_paths") or ())
            anchors.append(str(payload.get("declared_check") or ""))
        elif feature_id == "GT_LOC_RESLOT":
            for item in payload.get("selected_anchors") or ():
                path = str(item.get("path") or "")
                line = int(item.get("line") or 0)
                anchors.append(f"{path}:{line}" if line else path)
        elif feature_id == "submit_refusal":
            anchors.extend(str(item) for item in payload.get("blockers") or ())
        return tuple(dict.fromkeys(item for item in anchors if item))

    def _register_decision_claim(self, receipt: FeatureReceipt) -> None:
        mapping = self._decision_mapping(receipt.feature_id)
        if (
            mapping is None
            or not receipt.model_visible
            or not feature_payload_grounded(receipt.feature_id, receipt.payload)
        ):
            return
        if receipt.feature_id == "submit_refusal" and any(
            item.feature_id == "covering_red"
            and item.action_id == receipt.action_id
            and item.source_revision == receipt.source_revision
            for item in self.receipts
        ):
            # The failure sentence already carries the actionable information.
            # Submission state remains an effect contributor, not duplicate
            # provider context.
            return
        fact = self._render_feature_fact(receipt)
        anchors = self._claim_anchors(receipt)
        if not fact or not anchors:
            return
        claim_kind, need_kind = mapping
        existing_claim = self._decisions.find_claim(
            feature_id=receipt.feature_id,
            kind=claim_kind,
            fact=fact,
            anchors=anchors,
            source_revision=receipt.source_revision,
        )
        claim = self._decisions.upsert_claim(
            feature_id=receipt.feature_id,
            kind=claim_kind,
            fact=fact,
            anchors=anchors,
            source_revision=receipt.source_revision,
            evidence_action=receipt.action_id,
            workspace_revision=receipt.revision,
        )
        if claim is None:
            return
        if existing_claim is not None and existing_claim.active:
            self._suppress_receipt_delivery(receipt, reason="semantic_duplicate")
            return
        self._claim_receipts[claim.claim_id] = (
            receipt.feature_id,
            receipt.action_id,
        )
        self._decisions.open_need(
            kind=need_kind,
            source_revision=receipt.source_revision,
            created_after_action=receipt.action_id,
            required_claim_kinds=(claim_kind,),
            anchors=anchors,
        )

    def register_structural_evidence(
        self,
        *,
        source_revision: str,
        anchors: Iterable[dict[str, Any]],
        definitions: Iterable[dict[str, Any]] = (),
        references: Iterable[dict[str, Any]] = (),
        callers: Iterable[dict[str, Any]] = (),
        graph_revision: str,
    ) -> None:
        """Register source-backed task-start localization and impact evidence."""
        if not self.enabled or not source_revision or not graph_revision:
            return
        selected: list[dict[str, Any]] = []
        for item in anchors:
            path = str(item.get("path") or "").replace("\\", "/")
            line = int(item.get("line") or 0)
            symbol = str(item.get("symbol") or "")
            if not path or line < 0:
                continue
            selected.append({"path": path, "line": line, "symbol": symbol})
            if len(selected) >= 4:
                break
        definition_rows = [dict(item) for item in definitions if str(item.get("path") or "")][:8]
        reference_rows: list[dict[str, Any]] = []
        for item in references:
            path = str(item.get("path") or "").replace("\\", "/")
            line = int(item.get("line") or 0)
            symbol = str(item.get("symbol") or "")
            if not path:
                continue
            reference_rows.append(
                {
                    "path": path,
                    "line": line,
                    "symbol": symbol,
                    "semantics": str(item.get("semantics") or "graph_reference"),
                }
            )
            if len(reference_rows) >= 8:
                break
        caller_rows = [
            dict(item)
            for item in callers
            if str(item.get("caller_path") or "")
            and str(item.get("target_path") or "")
            and str(item.get("semantics") or "") == "graph_recorded"
        ][:8]
        if not selected:
            return
        self._structural_evidence = {
            "source_revision": source_revision,
            "graph_revision": graph_revision,
            "anchors": selected,
            "definitions": definition_rows,
            "references": reference_rows,
            "callers": caller_rows,
            "fresh": True,
        }
        self._current_source_revision = source_revision
        workspace_revision = self._controller_state.workspace_revision or source_revision
        self._emit(
            "localization",
            boundary="task_start",
            action_id=0,
            revision=workspace_revision,
            source_revision=source_revision,
            reason="source_backed_task_localization",
            payload={
                "candidate_locations": len(selected),
                "anchors": selected,
                "graph_revision": graph_revision,
                "message": self._payload(
                    "localization", "task_start", "source_backed_task_localization"
                )["message"],
            },
        )
        self._emit(
            "GT_LOC_RESLOT",
            boundary="task_start",
            action_id=0,
            revision=workspace_revision,
            source_revision=source_revision,
            reason="source_backed_ranked_anchors",
            payload={
                "owner_feature": "localization",
                "selected_anchors": selected,
                "discarded_anchor_count": 0,
                "graph_revision": graph_revision,
                "message": self._payload(
                    "localization", "task_start", "source_backed_ranked_anchors"
                )["message"],
            },
        )
        if definition_rows and reference_rows:
            self._emit(
                "def_partition",
                boundary="task_start",
                action_id=0,
                revision=workspace_revision,
                source_revision=source_revision,
                reason="graph_definition_reference_partition",
                payload={
                    "definitions": True,
                    "references": True,
                    "definition_anchors": definition_rows,
                    "reference_anchors": reference_rows,
                    "graph_revision": graph_revision,
                    "message": self._payload(
                        "def_partition", "task_start", "graph_definition_reference_partition"
                    )["message"],
                },
            )
        else:
            self._record_feature_opportunity(
                feature_id="def_partition",
                boundary="task_start",
                action_id=0,
                source_revision=source_revision,
                evidence_status="correct_abstention",
                reason_code="graph_partition_incomplete",
                evidence={
                    "definition_count": len(definition_rows),
                    "reference_count": len(reference_rows),
                    "graph_revision": graph_revision,
                },
            )
        if caller_rows:
            self._emit(
                "caller_contract",
                boundary="task_start",
                action_id=0,
                revision=workspace_revision,
                source_revision=source_revision,
                reason="graph_verified_callers",
                payload={
                    "callers_verified": True,
                    "callers": caller_rows,
                    "graph_revision": graph_revision,
                    "message": self._payload(
                        "caller_contract", "task_start", "graph_verified_callers"
                    )["message"],
                },
            )
        else:
            self._record_feature_opportunity(
                feature_id="caller_contract",
                boundary="task_start",
                action_id=0,
                source_revision=source_revision,
                evidence_status="correct_abstention",
                reason_code="no_certified_direct_callers",
                evidence={"graph_revision": graph_revision},
            )

    def record_repository_evidence_status(
        self,
        *,
        source_revision: str,
        status: str,
        available: bool,
        substrate_ready: bool = False,
        retrieval_disposition: str = "",
    ) -> None:
        """Account for structural feature applicability before the first model call."""

        if available:
            return
        valid_abstentions = {"no_supported_source", "no_task_linked_evidence"}
        evidence_status = (
            "correct_abstention"
            if substrate_ready or status in valid_abstentions
            else "substrate_unavailable"
        )
        reason_code = (
            retrieval_disposition
            if substrate_ready and retrieval_disposition
            else status or "repository_evidence_unavailable"
        )
        for feature_id in (
            "localization",
            "GT_LOC_RESLOT",
            "def_partition",
            "caller_contract",
        ):
            self._record_feature_opportunity(
                feature_id=feature_id,
                boundary="task_start",
                action_id=0,
                source_revision=source_revision,
                evidence_status=evidence_status,
                reason_code=reason_code,
                evidence={
                    "available": False,
                    "status": status,
                    "substrate_ready": bool(substrate_ready),
                    "retrieval_disposition": retrieval_disposition,
                },
            )

    def suppress_task_start_delivery(self) -> None:
        """Close call-zero localization when the host disabled that surface."""

        self._decisions.resolve_open_needs_by_kind(
            DecisionNeedKind.LOCALIZE_TASK,
            resolution="task_start_advisory_disabled",
        )
        for receipt in tuple(self.receipts):
            if (
                receipt.action_id == 0
                and receipt.feature_id == "GT_LOC_RESLOT"
                and receipt.model_visible
            ):
                self._suppress_receipt_delivery(
                    receipt,
                    reason="task_start_advisory_disabled",
                )

    def refresh_structural_evidence(
        self,
        *,
        source_revision: str,
        anchors: Iterable[dict[str, Any]],
        definitions: Iterable[dict[str, Any]] = (),
        references: Iterable[dict[str, Any]] = (),
        callers: Iterable[dict[str, Any]] = (),
        graph_revision: str,
    ) -> None:
        """Replace the preflight graph view without replaying task-start effects."""
        selected = tuple(dict(item) for item in anchors if item.get("path"))[:4]
        definition_rows = tuple(dict(item) for item in definitions if item.get("path"))[:8]
        reference_rows = tuple(dict(item) for item in references if item.get("path"))[:8]
        caller_rows = tuple(
            dict(item) for item in callers if item.get("caller_path") and item.get("target_path")
        )[:8]
        if not source_revision or not graph_revision or not selected:
            self._structural_evidence = {
                "source_revision": source_revision,
                "graph_revision": graph_revision,
                "anchors": [],
                "definitions": [],
                "references": [],
                "callers": [],
                "fresh": False,
            }
            return
        self._structural_evidence = {
            "source_revision": source_revision,
            "graph_revision": graph_revision,
            "anchors": [dict(item) for item in selected],
            "definitions": [dict(item) for item in definition_rows],
            "references": [dict(item) for item in reference_rows],
            "callers": [dict(item) for item in caller_rows],
            "fresh": True,
        }

    def preflight_action(
        self,
        proposed: ProposedAction,
        snapshot: WorkspaceSnapshot,
        *,
        revision: str,
        source_revision: str,
        ledger: EvidenceLedger | None = None,
    ) -> PreflightDecision:
        """Inspect one model-selected action before host execution.

        This function is intentionally state-only and correct-or-quiet.  It
        never calls a model or indexer and defaults to literal PASS.
        """
        started = time.perf_counter()
        decision = pass_decision(proposed)
        try:
            if proposed.source_revision != source_revision:
                decision = pass_decision(proposed, "source_revision_mismatch")
            elif not snapshot.healthy:
                decision = pass_decision(proposed, "workspace_sensor_degraded")
            elif proposed.operation == ActionOperation.SUBMIT and ledger is not None:
                submit_decision = ledger.submit_decision(
                    source_revision,
                    sensor_healthy=snapshot.healthy,
                )
                blockers = submit_decision.blockers
                if submit_decision.decision is InterventionDecision.HOLD_ONCE:
                    for feature_id in (
                        "obligations",
                        "submit_refusal",
                        "GT_SS_SUBMIT_RED",
                        "GT_CERT_DELIVERY",
                    ):
                        if self._trace_for_effect(feature_id) is not None:
                            self.record_existing_consumer_read(
                                feature_id=feature_id,
                                action_id=proposed.model_call,
                                purpose=f"preflight_submit:{proposed.cycle_id}",
                            )
                    decision = PreflightDecision(
                        ActionDisposition.RETURN_TO_MODEL,
                        proposed.raw_command,
                        evidence=(
                            "Current source revision has failing required checks: "
                            + ", ".join(blockers),
                        ),
                        reason_codes=("proven_submit_blocker",),
                        confidence=1.0,
                        source_revision=source_revision,
                    )
            elif (
                proposed.operation == ActionOperation.CREATE
                and proposed.target_must_be_absent
                and proposed.targets
            ):
                existing = tuple(
                    target.path for target in proposed.targets if target.path in snapshot.entries
                )
                if existing:
                    decision = PreflightDecision(
                        ActionDisposition.RETURN_TO_MODEL,
                        proposed.raw_command,
                        evidence=("Proposed new path already exists: " + ", ".join(existing),),
                        reason_codes=("create_target_exists",),
                        confidence=1.0,
                        source_revision=source_revision,
                    )
        except Exception:
            decision = pass_decision(proposed, "preflight_exception")
        decision = replace(
            decision,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        return decision

    def admit_preflight_intervention(
        self,
        proposed: ProposedAction,
        decision: PreflightDecision,
    ) -> tuple[bool, str]:
        """Enforce the material-evidence gate and adjacent-call deduplication."""
        if decision.disposition not in {
            ActionDisposition.AUGMENT,
            ActionDisposition.RETURN_TO_MODEL,
        }:
            return False, "disposition_not_assistive"
        # A missing shell target is not a decision-changing contradiction:
        # redirects and scratch-file creation expect absence, while in-place
        # tools fail safely and expose their real diagnostic through
        # authoritative postflight.  Returning to the model here spends a
        # provider call before any new evidence exists.
        if "edit_target_absent" in decision.reason_codes:
            return False, "postflight_safe_absent_target"
        evidence = tuple(" ".join(item.split()) for item in decision.evidence if item.strip())
        if not evidence:
            return False, "missing_evidence"
        if sum(len(item) for item in evidence) > 640:
            return False, "evidence_budget_exceeded"
        if decision.evidence_grade == EvidenceGrade.HEURISTIC:
            return False, "heuristic_evidence"
        required_confidence = (
            1.0 if decision.disposition == ActionDisposition.RETURN_TO_MODEL else 0.9
        )
        if decision.confidence < required_confidence:
            return False, "confidence_below_threshold"
        if decision.source_revision not in {"", proposed.source_revision}:
            return False, "source_revision_mismatch"
        evidence_identity = decision.evidence_ids or (
            hashlib.sha256(
                "\0".join(
                    (
                        proposed.source_revision,
                        proposed.operation.value,
                        *decision.reason_codes,
                        *evidence,
                    )
                ).encode("utf-8", "replace")
            ).hexdigest()[:20],
        )
        authority = {
            EvidenceGrade.DIRECT: EvidenceAuthority.MECHANICAL,
            EvidenceGrade.STRUCTURAL: EvidenceAuthority.CERTIFIED_STRUCTURAL,
            EvidenceGrade.DERIVED: EvidenceAuthority.UNKNOWN,
            EvidenceGrade.HEURISTIC: EvidenceAuthority.HEURISTIC,
        }[decision.evidence_grade]
        opportunity = certify_opportunity(
            kind=(
                OpportunityKind.SUBMIT_DEBT
                if "proven_submit_blocker" in decision.reason_codes
                else (
                    OpportunityKind.DECISION_EVIDENCE_GAP
                    if "certified_missing_decision_evidence" in decision.reason_codes
                    else OpportunityKind.EDIT_CONTRADICTION
                )
            ),
            authority=authority,
            source_revision=decision.source_revision or proposed.source_revision,
            current_source_revision=proposed.source_revision,
            workspace_revision=proposed.workspace_revision,
            evidence_ids=tuple(evidence_identity),
            concrete_anchors=evidence,
            absent_from_provider_history=True,
            decision_relevant=True,
            eligible_call=proposed.model_call,
            current_call=proposed.model_call,
        )
        self._certification_decisions.append(
            {
                "boundary": "preflight",
                "action_id": proposed.action_id,
                **opportunity.as_dict(),
            }
        )
        if not opportunity.certified:
            return False, "opportunity_" + "_".join(opportunity.reason_codes)
        key = "\0".join(evidence_identity)
        if key in self._preflight_intervention_keys:
            return False, "duplicate_evidence"
        self._preflight_intervention_keys.add(key)
        return True, "admitted"

    def record_preflight_cycle(
        self,
        proposed: ProposedAction,
        candidate: PreflightDecision,
        *,
        mode: PreflightMode,
        applied_disposition: ActionDisposition,
        applied_reason_codes: Iterable[str],
        dispatch_command: str,
        revision: str,
        source_revision: str,
    ) -> None:
        """Record the host policy decision, including degraded PASS results."""
        reasons = tuple(dict.fromkeys(str(item) for item in applied_reason_codes if item))
        cycle = ActionCycleReceipt(
            proposed=proposed,
            mode=mode,
            candidate_decision=candidate,
            applied_disposition=applied_disposition,
            applied_reason_codes=reasons,
            dispatch_command=dispatch_command,
        )
        self._action_cycles[proposed.cycle_id] = cycle
        self._preflight_receipts.append(
            {
                "cycle_id": proposed.cycle_id,
                "proposed": proposed.as_dict(),
                "decision": candidate.as_dict(),
                "mode": mode.value,
                "applied_disposition": applied_disposition.value,
                "applied_reason_codes": list(reasons),
                "revision": revision,
                "source_revision": source_revision,
            }
        )

    def record_action_postflight(
        self,
        proposed: ProposedAction,
        *,
        action_ordinal: int,
        command: str,
        returncode: int,
        workspace_revision: str,
        source_revision: str,
    ) -> None:
        """Join actual execution and postflight revision to its proposal."""
        cycle = self._action_cycles.get(proposed.cycle_id)
        if cycle is None:
            return
        cycle.executed = True
        cycle.postflight = {
            "action_ordinal": max(0, int(action_ordinal)),
            "command": command,
            "returncode": int(returncode),
            "workspace_revision": workspace_revision,
            "source_revision": source_revision,
        }

    def record_cancelled_proposal(
        self,
        proposed: ProposedAction,
        *,
        mode: PreflightMode,
        reason: str,
    ) -> None:
        """Receipt a model-selected batch suffix that never reached preflight."""
        decision = pass_decision(proposed, "cancelled_before_preflight")
        cycle = ActionCycleReceipt(
            proposed=proposed,
            mode=mode,
            candidate_decision=decision,
            applied_disposition=ActionDisposition.PASS,
            applied_reason_codes=(reason,),
            dispatch_command=proposed.raw_command,
            executed=False,
            postflight={
                "status": "cancelled_before_dispatch",
                "reason": reason,
            },
        )
        self._action_cycles[proposed.cycle_id] = cycle

    def record_reconsideration(
        self,
        *,
        cycle_id: str,
        next_command: str,
        next_model_call: int,
    ) -> None:
        cycle = self._action_cycles.get(cycle_id)
        if cycle is None:
            return
        normalized_next = normalize_command(next_command)
        normalized_original = normalize_command(cycle.proposed.raw_command)
        cycle.reconsideration = {
            "next_model_call": max(1, int(next_model_call)),
            "next_command": next_command,
            "command_changed": normalized_next != normalized_original,
        }

    def begin_task(
        self,
        instruction: str,
        *,
        revision: str,
        source_revision: str | None = None,
        explicit_checks: Iterable[str] = (),
        task_deliverables: Iterable[str] = (),
        initial_source_paths: Iterable[str] | None = None,
    ) -> None:
        if not self.enabled:
            return
        self._current_source_revision = source_revision if source_revision is not None else revision
        self._explicit_checks = tuple(explicit_checks)
        self._task_deliverables = set(task_deliverables)
        self._initial_source_paths = (
            None
            if initial_source_paths is None
            else {_workspace_relative_path(path) for path in initial_source_paths}
        )
        self._mark_lifecycle("task_started", action_id=0)
        if instruction.strip():
            self._mark_lifecycle("contract_captured", action_id=0)
            self.record_producer_event(
                feature_id="obligations",
                action_id=0,
                kind="contract_captured",
                detail="task requirements and declared checks entered the engine contract",
            )

            self._emit(
                "obligations",
                boundary="task_start",
                action_id=0,
                revision=revision,
                source_revision=source_revision,
                reason="non_empty_task_instruction",
                payload={
                    "requirements_present": True,
                    "obligation_ids": list(self._explicit_checks)
                    or sorted(self._task_deliverables)
                    or ["task:instruction"],
                    "declared_checks": list(self._explicit_checks),
                    "message": self._payload(
                        "obligations", "task_start", "non_empty_task_instruction"
                    )["message"],
                },
            )

    def register_project_checks(self, checks: Iterable[str]) -> tuple[str, ...]:
        """Merge mechanically discovered project checks into the task contract.

        Discovery happens after the initial repository transfer, so these
        checks cannot always be supplied to :meth:`begin_task`.  They become
        declared validation obligations, but do not themselves create model
        text or claim that a check has run.
        """

        normalized = tuple(
            dict.fromkeys(
                normalize_command(item)
                for item in (*self._explicit_checks, *tuple(checks))
                if normalize_command(item)
            )
        )
        self._explicit_checks = normalized
        return normalized

    def _is_model_actionable(self, feature_id: str, decision: str, payload: dict[str, Any]) -> bool:
        """Expose only novel engine control evidence, never passive receipts."""
        if not self.model_visible or decision != "DELIVERED":
            return False
        actionable = (
            feature_id in _MODEL_ACTIONABLE_FEATURES
            or (feature_id == "GT_EDIT_CHECK" and payload.get("intervention") == "validation_debt")
        )
        return actionable and feature_payload_grounded(feature_id, payload)

    def observe_action(
        self,
        *,
        action_id: int,
        command: str,
        output: str,
        returncode: int,
        transition: WorkspaceTransition,
        revision: str,
        source_revision: str | None = None,
        snapshot: WorkspaceSnapshot | None = None,
        validation: ValidationClassification | None = None,
        proposed: ProposedAction | None = None,
    ) -> None:
        if not self.enabled:
            return
        normalized = normalize_command(command)
        source_rev = source_revision if source_revision is not None else revision
        if self._current_source_revision and source_rev != self._current_source_revision:
            self._decisions.invalidate_other_revisions(source_rev)
        self._current_source_revision = source_rev
        classification = validation or classify_validation_command(command, self._explicit_checks)
        if classification.result_code is None:
            classification = classification.with_result(
                result_code=returncode,
                output=output,
                source_revision=source_rev,
                workspace_revision=revision,
            )
        if proposed is None:
            proposed = adapt_proposed_action(
                {"command": command, "tool_call_id": f"observed-{action_id}"},
                source_revision=source_rev,
                workspace_revision=revision,
                model_call=max(1, action_id),
                batch_index=0,
                batch_size=1,
                validation=classification,
            )
        search_observation, search_reason = self._search_observation(
            proposed,
            output,
            snapshot=snapshot,
            source_revision=source_rev,
        )
        is_repository_search = bool(
            search_observation
            and search_observation.scope in {SearchScope.WORKSPACE, SearchScope.TARGETS}
        )
        self._action_metrics["observed_actions"] += 1
        self._action_metrics["successful_actions" if returncode == 0 else "failed_actions"] += 1
        self._action_metrics["command_chars"] += len(command)
        self._action_metrics["observation_chars"] += len(output or "")
        command_count = self._command_counts.get(normalized, 0) + 1
        self._command_counts[normalized] = command_count
        if command_count > 1:
            self._action_metrics["repeated_commands"] += 1
        if is_repository_search:
            self._action_metrics["search_actions"] += 1
        if proposed is not None:
            output_hash = hashlib.sha256((output or "").encode("utf-8", "replace")).hexdigest()
            for operation in proposed.operations:
                if operation.operation != ActionOperation.READ:
                    continue
                for span in operation.read_spans:
                    observation = {
                        "path": span.path,
                        "start_line": span.start_line,
                        "end_line": span.end_line,
                        "whole_file": span.whole_file,
                        "source_revision": source_rev,
                        "workspace_revision": revision,
                        "action_id": action_id,
                        "returncode": returncode,
                        "output_hash": output_hash,
                        "content_mapped": len(operation.read_spans) == 1,
                    }
                    identity = (
                        span.path,
                        span.start_line,
                        span.end_line,
                        span.whole_file,
                        source_rev,
                    )
                    self._recent_reads = [
                        item
                        for item in self._recent_reads
                        if (
                            item.get("path"),
                            item.get("start_line"),
                            item.get("end_line"),
                            item.get("whole_file"),
                            item.get("source_revision"),
                        )
                        != identity
                    ]
                    self._recent_reads.append(observation)
            self._recent_reads = self._recent_reads[-24:]
        self._validation_log.append(
            {
                "action": action_id,
                "command": classification.normalized_command,
                "command_class": classification.command_class,
                "is_validation": classification.is_validation,
                "grounded": classification.grounded,
                "validation_authority": classification.authority.value,
                "declared_check_id": classification.declared_check_id,
                "failure_kind": classification.failure_kind,
                "result_code": classification.result_code,
                "source_revision": source_rev,
                "workspace_revision": revision,
                "diagnostic_fingerprint": classification.diagnostic_fingerprint,
                "status": classification.status.value,
                "status_attributed": classification.status_attributed,
                "validator_segment_index": classification.validator_segment_index,
                "attribution_reason": classification.attribution_reason,
            }
        )
        if classification.is_validation:
            self._action_metrics["check_actions"] += 1
            self._latest_validation = {
                "command": classification.normalized_command,
                "returncode": classification.result_code,
                "status": classification.status.value,
                "status_attributed": classification.status_attributed,
                "source_revision": source_rev,
                "workspace_revision": revision,
                "action_id": action_id,
            }
        if classification.status in {ValidationStatus.PASS, ValidationStatus.FAIL}:
            self._mark_lifecycle("behavior_observed", action_id=action_id)
            if self._workspace_edited:
                self._post_edit_checks += 1
                phase = (
                    "focused_check_validated"
                    if self._post_edit_checks == 1
                    else "regression_validated"
                )
                self._mark_lifecycle(
                    phase,
                    action_id=action_id,
                    status=(
                        "passed" if classification.status is ValidationStatus.PASS else "failed"
                    ),
                )
            if classification.declared_check_id:
                self._declared_check_states[classification.declared_check_id] = (
                    "passed" if classification.status is ValidationStatus.PASS else "failed"
                )
            if classification.status is ValidationStatus.PASS:
                self._unvalidated_material_edits = 0
                self._validation_debt_notified = False
                self._submit_risk_revisions.discard(source_rev)
                self._emit(
                    "GT_CERT_DELIVERY",
                    boundary="test_result",
                    action_id=action_id,
                    revision=revision,
                    source_revision=source_rev,
                    reason="fresh_validation_pass",
                    payload={
                        "sensor_healthy": snapshot is None or snapshot.healthy,
                        "refused": False,
                        "check_count": 1,
                        "passing_checks": 1,
                        "failing_checks": 0,
                        "readiness": "validated",
                        "checks": [classification.normalized_command[:200]],
                        "message": "Current source revision has fresh passing validation.",
                    },
                )

        classified: dict[str, ClassifiedChange] = {}
        for path in transition.changed_paths:
            kind = "f"
            content: str | bytes | None = transition.after_contents.get(path)
            if snapshot is not None:
                state = snapshot.entries.get(path)
                if state is not None:
                    kind = state.kind
                    if state.content is not None:
                        content = state.content
            if path in transition.deleted:
                content = transition.before_contents.get(path)
            classified[path] = classify_change(
                path,
                kind=kind,
                task_deliverables=self._task_deliverables,
                content=content,
            )
        source_relevant = tuple(
            item.path for item in classified.values() if item.validation_relevant
        )
        authored_source = tuple(
            item.path
            for item in classified.values()
            if item.origin in {ChangeOrigin.MODEL_AUTHORED, ChangeOrigin.TASK_DELIVERABLE}
            and item.validation_relevant
        )
        if transition.changed_paths:
            if authored_source:
                # Any authored source change makes prior check results stale.
                self._source_epoch += 1
                self._recent_source_paths = tuple(authored_source)
                self._last_edit = {
                    "command": normalized,
                    "paths": list(source_relevant),
                    "source_revision": source_rev,
                    "workspace_revision": revision,
                    "action_id": action_id,
                }
                self._declared_check_states = {
                    check: ("stale" if state == "passed" else state)
                    for check, state in self._declared_check_states.items()
                }
            # GT_CHANGE_SURFACE reports every classified change, labeled by
            # origin; derived artifacts are surfaced as facts, never as source.
            self._emit(
                "GT_CHANGE_SURFACE",
                boundary="edit_result",
                action_id=action_id,
                revision=revision,
                source_revision=source_rev,
                reason="workspace_revision_changed",
                payload={
                    "owner_feature": "newfile_precedent",
                    "created": list(transition.created),
                    "modified": list(transition.modified),
                    "deleted": list(transition.deleted),
                    "source_relevant": list(source_relevant),
                    "origins": {
                        origin.value: sum(item.origin == origin for item in classified.values())
                        for origin in ChangeOrigin
                    },
                    "message": "Workspace change observed: "
                    + ", ".join(list(source_relevant)[:4] or list(transition.changed_paths)[:4]),
                },
            )
            if source_relevant:
                self._workspace_edited = True
                self._unvalidated_material_edits += 1
                self._action_metrics["workspace_change_actions"] += 1
                self._action_metrics["created_paths"] += sum(
                    item.path in transition.created
                    for item in classified.values()
                    if item.validation_relevant
                )
                self._action_metrics["modified_paths"] += sum(
                    item.path in transition.modified
                    for item in classified.values()
                    if item.validation_relevant
                )
                self._action_metrics["deleted_paths"] += sum(
                    item.path in transition.deleted
                    for item in classified.values()
                    if item.validation_relevant
                )
                self._mark_lifecycle("workspace_edited", action_id=action_id)
                self._mark_lifecycle("change_surface_certified", action_id=action_id)
                self.record_producer_event(
                    feature_id="GT_CHANGE_SURFACE",
                    action_id=action_id,
                    kind="source_revision_and_validation_debt",
                    detail=(
                        f"source_epoch={self._source_epoch}; "
                        f"unvalidated_material_edits={self._unvalidated_material_edits}"
                    ),
                )
                changed = list(source_relevant)
                self.record_producer_event(
                    feature_id="GT_PATCH_DELTA",
                    action_id=action_id,
                    kind="validation_surface_registered",
                    detail=", ".join(changed[:8]),
                )
                self._emit(
                    "GT_PATCH_DELTA",
                    boundary="edit_result",
                    action_id=action_id,
                    revision=revision,
                    source_revision=source_rev,
                    reason="non_empty_patch_surface",
                    payload={
                        "owner_feature": "signature_delta",
                        "changed_paths": changed,
                        "message": "Workspace change observed: " + ", ".join(changed[:4]),
                    },
                )
                if self._explicit_checks:
                    self.record_existing_consumer_read(
                        feature_id="obligations",
                        action_id=action_id,
                        purpose="declared_check_selection",
                    )
                    declared_check = select_declared_check(
                        self._explicit_checks, self._declared_check_states
                    )
                    if declared_check:
                        self.record_existing_consumer_read(
                            feature_id="GT_CHANGE_SURFACE",
                            action_id=action_id,
                            purpose="material_edit_count_for_validation_debt",
                        )
                        debt = (
                            self._unvalidated_material_edits >= 3
                            and not self._validation_debt_notified
                        )
                        self._emit(
                            "GT_EDIT_CHECK",
                            boundary="edit_result",
                            action_id=action_id,
                            revision=revision,
                            source_revision=source_rev,
                            reason=(
                                "multiple_material_edits_without_validation"
                                if debt
                                else "authored_edit_requires_declared_check"
                            ),
                            payload={
                                "owner_feature": "syntax_result",
                                "intervention": (
                                    "validation_debt" if debt else "validation_schedule"
                                ),
                                "material_edit_count": self._unvalidated_material_edits,
                                "declared_check": declared_check[:120],
                                "changed_paths": changed[:4],
                                "message": f"Relevant declared check: {declared_check[:120]}",
                            },
                        )
                        if debt:
                            self._validation_debt_notified = True
                        self.record_producer_event(
                            feature_id="GT_EDIT_CHECK",
                            action_id=action_id,
                            kind="declared_check_selected",
                            detail=declared_check[:120],
                        )
            else:
                self._action_metrics["no_change_actions"] += 1
        else:
            self._action_metrics["no_change_actions"] += 1
        anchors: list[dict[str, Any]] = []
        if search_observation is not None and not is_repository_search:
            for feature_id in ("localization", "GT_LOC_RESLOT"):
                self._record_feature_opportunity(
                    feature_id=feature_id,
                    boundary="search_result",
                    action_id=action_id,
                    source_revision=source_rev,
                    evidence_status="correct_abstention",
                    reason_code=search_reason,
                    evidence={
                        "scope": search_observation.scope.value,
                        "targets": search_observation.targets,
                        "output_sha256": search_observation.output_sha256,
                    },
                )
        elif is_repository_search and not output.strip():
            for feature_id in ("localization", "GT_LOC_RESLOT"):
                self._record_feature_opportunity(
                    feature_id=feature_id,
                    boundary="search_result",
                    action_id=action_id,
                    source_revision=source_rev,
                    evidence_status="trigger_absent",
                    reason_code="empty_search_output",
                    evidence={"scope": search_observation.scope.value},
                )
        elif is_repository_search and search_observation is not None:
            anchors = [dict(item) for item in search_observation.anchors]
            if not anchors:
                for feature_id in ("localization", "GT_LOC_RESLOT"):
                    self._record_feature_opportunity(
                        feature_id=feature_id,
                        boundary="search_result",
                        action_id=action_id,
                        source_revision=source_rev,
                        evidence_status="ambiguous_evidence",
                        reason_code="search_output_unanchored",
                        evidence={
                            "scope": search_observation.scope.value,
                            "output_format": search_observation.output_format,
                            "output_sha256": search_observation.output_sha256,
                        },
                    )
                anchors = []
        if is_repository_search and search_observation is not None and anchors:
            self._searched = True
            self._mark_lifecycle("location_anchored", action_id=action_id)
            for anchor in anchors:
                path = str(anchor.get("path") or "")
                if path:
                    observation = {
                        "path": path,
                        "start_line": int(anchor.get("line") or 0) or None,
                        "end_line": int(anchor.get("line") or 0) or None,
                        "whole_file": False,
                        "source_revision": source_rev,
                        "workspace_revision": revision,
                        "action_id": action_id,
                        "returncode": returncode,
                        "output_hash": hashlib.sha256(
                            (output or "").encode("utf-8", "replace")
                        ).hexdigest(),
                        "content_mapped": False,
                        "observation_kind": "search_anchor",
                    }
                    identity = (
                        observation["path"],
                        observation["start_line"],
                        observation["source_revision"],
                        observation["observation_kind"],
                    )
                    self._recent_reads = [
                        item
                        for item in self._recent_reads
                        if (
                            item.get("path"),
                            item.get("start_line"),
                            item.get("source_revision"),
                            item.get("observation_kind"),
                        )
                        != identity
                    ]
                    self._recent_reads.append(observation)
            self._recent_reads = self._recent_reads[-24:]
            self.record_producer_event(
                feature_id="localization",
                action_id=action_id,
                kind="location_anchored",
                detail=f"anchors={len(anchors)}",
            )
            self._emit(
                "localization",
                boundary="search_result",
                action_id=action_id,
                revision=revision,
                source_revision=source_rev,
                reason="non_empty_search_result",
                payload={
                    "candidate_locations": True,
                    "anchors": anchors,
                    "query": search_observation.query[:120],
                    "message": self._payload(
                        "localization", "search_result", "non_empty_search_result"
                    )["message"],
                },
            )
            self._emit(
                "GT_LOC_RESLOT",
                boundary="search_result",
                action_id=action_id,
                revision=revision,
                source_revision=source_rev,
                reason="ranked_anchors_selected",
                payload={
                    "owner_feature": "localization",
                    "selected_anchors": anchors[:4],
                    "discarded_anchor_count": max(0, len(anchors) - 4),
                    "message": "Ranked source anchors selected for the next observation.",
                },
            )
            self.record_producer_event(
                feature_id="GT_LOC_RESLOT",
                action_id=action_id,
                kind="ranked_anchors_computed",
                detail=f"selected={min(4, len(anchors))}; discarded={max(0, len(anchors) - 4)}",
            )

        failure_kind = classification.failure_kind
        if classification.status is ValidationStatus.FAIL and failure_kind == "validation_failure":
            check_phase = "post_edit" if self._workspace_edited else "reproduction"
            self.record_existing_consumer_read(
                feature_id="GT_CHANGE_SURFACE",
                action_id=action_id,
                purpose="failure_phase_selection",
            )
            bounded_diagnostic = " ".join(
                line.strip() for line in (output or "").splitlines() if self._FAILURE.search(line)
            )[:240]
            self._latest_failure = {
                "command": classification.normalized_command,
                "fingerprint": classification.diagnostic_fingerprint,
                "diagnostic": bounded_diagnostic,
                "source_revision": source_rev,
                "workspace_revision": revision,
                "action_id": action_id,
            }
            self.record_producer_event(
                feature_id="covering_red",
                action_id=action_id,
                kind="failure_state_keyed",
                detail=f"phase={check_phase}; fingerprint={classification.diagnostic_fingerprint}",
            )
            self._emit(
                "covering_red",
                boundary="test_result",
                action_id=action_id,
                revision=revision,
                source_revision=source_rev,
                decision=(
                    "DELIVERED"
                    if classification.authority is ValidationAuthority.DECLARED
                    else "PASS"
                ),
                reason=(
                    "declared_check_failed"
                    if classification.authority is ValidationAuthority.DECLARED
                    else "observed_non_authoritative_failure"
                ),
                payload={
                    "check_failed": True,
                    "returncode": returncode,
                    "phase": check_phase,
                    "command": classification.normalized_command[:200],
                    "command_class": classification.command_class,
                    "validation_authority": classification.authority.value,
                    "declared_check_id": classification.declared_check_id,
                    "failure_kind": failure_kind,
                    "attribution": (
                        classification.declared_check_id or classification.command_class
                    ),
                    "diagnostic": bounded_diagnostic,
                    "message": self._payload(
                        "covering_red", "test_result", "failed_check_or_failure_output"
                    )["message"],
                },
            )
            failure_fingerprint = classification.diagnostic_fingerprint
            failure_key = (normalized, failure_fingerprint, returncode, source_rev)
            count = self._failed_actions.get(failure_key, 0) + 1
            self._failed_actions[failure_key] = count
            self.record_producer_event(
                feature_id="GT_HYPOTHESIS",
                action_id=action_id,
                kind="failure_repeat_count_updated",
                detail=f"repeat_count={count}",
            )
            self._emit(
                "GT_HYPOTHESIS",
                boundary="test_result",
                action_id=action_id,
                revision=revision,
                source_revision=source_rev,
                reason="deterministic_failure_state_transition",
                payload={
                    "owner_feature": "recovery",
                    "failure_fingerprint": failure_fingerprint,
                    "repeat_count": count,
                    "declared_check_id": classification.declared_check_id,
                    "diagnostic": bounded_diagnostic,
                    "message": (
                        "Repeated validation failure recorded at an unchanged "
                        f"source revision ({count}x) for "
                        f"{classification.declared_check_id or classification.command_class}"
                        + (f": {bounded_diagnostic}" if bounded_diagnostic else ".")
                    ),
                },
            )
            if count >= 2:
                self.record_existing_consumer_read(
                    feature_id="GT_HYPOTHESIS",
                    action_id=action_id,
                    purpose="repeat_count_for_recovery_eligibility",
                )
            blocker = classification.declared_check_id
            blockers = [blocker] if blocker else []
            if blockers and bounded_diagnostic and source_rev not in self._submit_risk_revisions:
                self._submit_risk_revisions.add(source_rev)
                self.record_producer_event(
                    feature_id="GT_SS_SUBMIT_RED",
                    action_id=action_id,
                    kind="submit_risk_latched",
                    detail=f"source_revision={source_rev}",
                )
                self._emit(
                    "GT_SS_SUBMIT_RED",
                    boundary="test_result",
                    action_id=action_id,
                    revision=revision,
                    source_revision=source_rev,
                    reason="fresh_grounded_failure",
                    payload={
                        "owner_feature": "submit_refusal",
                        "submission_risk": True,
                        "blockers": blockers,
                        "declared_check_id": classification.declared_check_id,
                        "failure_fingerprint": failure_fingerprint,
                        "message": "Current source revision retains a failing required check.",
                    },
                )
                self._emit(
                    "submit_refusal",
                    boundary="test_result",
                    action_id=action_id,
                    revision=revision,
                    source_revision=source_rev,
                    reason="fresh_grounded_failure",
                    payload={
                        "submission_risk": True,
                        "refused": False,
                        "fresh_failure": True,
                        "blockers": blockers,
                        "declared_check_id": classification.declared_check_id,
                        "message": (
                            "The current source revision still has a failing required check: "
                            + ", ".join(blockers[:2])
                        ),
                    },
                )
                self.record_producer_event(
                    feature_id="submit_refusal",
                    action_id=action_id,
                    kind="submit_risk_latched",
                    detail=f"blockers={len(blockers)}",
                )
            if count >= 2 and classification.authority in {
                ValidationAuthority.DECLARED,
                ValidationAuthority.STANDARD_RUNNER,
            }:
                self._emit(
                    "recovery",
                    boundary="test_result",
                    action_id=action_id,
                    revision=revision,
                    source_revision=source_rev,
                    reason="same_failure_repeated",
                    payload={
                        "repeat_count": count,
                        "failure_fingerprint": failure_fingerprint,
                        "alternate_action": {
                            "kind": "inspect_then_edit",
                            "paths": list(self._recent_source_paths),
                            "discriminator": "exact repeat at unchanged source revision",
                        },
                        "message": (
                            "The same validation failure repeated at an unchanged source "
                            "revision; recorded failure evidence is "
                            f"{failure_fingerprint or classification.command_class}."
                        ),
                    },
                )

        if transition.created and not self._precedent_verified:
            available_paths = set(transition.before_contents)
            if snapshot is not None:
                available_paths.update(snapshot.entries)
            source_precedent_paths = {
                path
                for path in available_paths
                if (
                    (
                        self._initial_source_paths is None
                        or _workspace_relative_path(path) in self._initial_source_paths
                    )
                    and (
                        candidate := classify_change(
                            path,
                            kind=(
                                snapshot.entries[path].kind
                                if snapshot is not None and path in snapshot.entries
                                else "f"
                            ),
                            task_deliverables=self._task_deliverables,
                        )
                    ).origin
                    == ChangeOrigin.MODEL_AUTHORED
                    and candidate.validation_relevant
                    and is_validation_source(path)
                )
            }
            precedent_path = ""
            precedent_created_path = ""
            for created_path in transition.created:
                # Precedent is guidance for a model-authored source file only.
                # Workspace sensors also report caches, build products, generated
                # binaries, and task outputs; treating those as new source files
                # creates provider-visible spam and can steer the agent toward
                # irrelevant artifacts.  The shared classification is the source
                # of truth for this boundary.
                created_classification = classified.get(created_path)
                if (
                    created_classification is None
                    or created_classification.origin != ChangeOrigin.MODEL_AUTHORED
                    or not created_classification.validation_relevant
                    or created_classification.kind != "f"
                    or not is_validation_source(created_path)
                ):
                    continue
                parent = created_path.rsplit("/", 1)[0] if "/" in created_path else ""
                created_languages = {
                    capability.name for capability in candidate_capabilities(created_path)
                }
                created_stem = created_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                created_tokens = set(re.findall(r"[a-z0-9]+", created_stem.lower()))

                def concrete_precedent(path: str) -> bool:
                    if path in transition.before_contents:
                        return bool(transition.before_contents[path].strip())
                    if snapshot is not None and path in snapshot.entries:
                        return snapshot.entries[path].size > 0
                    return False

                def precedent_rank(
                    path: str, expected_tokens: set[str] = created_tokens
                ) -> tuple[int, bool, int, str]:
                    name = path.rsplit("/", 1)[-1]
                    stem = name.rsplit(".", 1)[0]
                    tokens = set(re.findall(r"[a-z0-9]+", stem.lower()))
                    overlap = len(expected_tokens & tokens)
                    size = (
                        snapshot.entries[path].size
                        if snapshot is not None and path in snapshot.entries
                        else len(transition.before_contents.get(path, ""))
                    )
                    return (-overlap, name == "__init__.py", -size, path)

                candidates = sorted(
                    (
                        path
                        for path in source_precedent_paths
                        if path not in transition.created
                        and (path.rsplit("/", 1)[0] if "/" in path else "") == parent
                        and bool(
                            created_languages
                            & {capability.name for capability in candidate_capabilities(path)}
                        )
                        and concrete_precedent(path)
                    ),
                    key=precedent_rank,
                )
                if candidates:
                    precedent_created_path = created_path
                    precedent_path = candidates[0]
                    break
            if precedent_path and precedent_created_path:
                self._precedent_verified = True
                self._precedent_path = precedent_path
                self.record_producer_event(
                    feature_id="newfile_precedent",
                    action_id=action_id,
                    kind="precedent_verified",
                    detail=precedent_path,
                )
                self._emit(
                    "newfile_precedent",
                    boundary="edit_result",
                    action_id=action_id,
                    revision=revision,
                    source_revision=source_rev,
                    reason="new_file_with_concrete_sibling_precedent",
                    payload={
                        "created_files": [precedent_created_path],
                        "precedent_verified": True,
                        "precedent_path": precedent_path,
                        "precedent_origin": "task_start_repository",
                        "message": (
                            f"New source file {precedent_created_path} has repository "
                            f"precedent {precedent_path}."
                        ),
                    },
                )

        signature_deltas = self._semantic_signature_deltas(transition)
        if not signature_deltas:
            signature_replacement = self._explicit_signature_replacement(normalized)
            if transition.changed_paths and signature_replacement:
                before_signature, after_signature = signature_replacement
                symbol_match = re.search(
                    r"\b(?:def|function|func|sub|procedure|class)\s+([A-Za-z_]\w*)\s*\(",
                    before_signature,
                )
                signature_deltas = [
                    {
                        "path": transition.changed_paths[0],
                        "symbol": symbol_match.group(1) if symbol_match else "",
                        "before_signature": before_signature,
                        "after_signature": after_signature,
                    }
                ]
        if signature_deltas:
            primary = signature_deltas[0]
            caller_payload = self._controller_state.impact.get("caller_contract") or {}
            if caller_payload:
                self.record_existing_consumer_read(
                    feature_id="caller_contract",
                    action_id=action_id,
                    purpose="signature_delta_caller_impact",
                )
            callers = list(caller_payload.get("callers") or [])
            contributors = ["signature_delta", "GT_PATCH_DELTA"]
            if callers:
                contributors.append("caller_contract")
            self._emit(
                "signature_delta",
                boundary="edit_result",
                action_id=action_id,
                revision=revision,
                source_revision=source_rev,
                reason="signature-shaped edit on changed path",
                payload={
                    # Signature evidence is source-bound. Workspace sensors
                    # can report bytecode/cache writes in the same action; do
                    # not let those derived paths enter the claim or provider
                    # payload.
                    "changed_paths": list(source_relevant),
                    "signature_edit": True,
                    "symbol": primary["symbol"],
                    "before_signature": primary["before_signature"],
                    "after_signature": primary["after_signature"],
                    "signature_deltas": signature_deltas,
                    "callers": callers,
                    "contributing_features": contributors,
                    "signature_fingerprint": hashlib.sha256(
                        repr(signature_deltas).encode("utf-8", "replace")
                    ).hexdigest()[:16],
                    "message": self._payload(
                        "signature_delta", "edit_result", "signature-shaped edit on changed path"
                    )["message"],
                },
            )

    def record_syntax(
        self,
        *,
        action_id: int,
        revision: str,
        failed: bool,
        reason: str,
        path: str = "",
        command: str = "",
        returncode: int | None = None,
        diagnostic: str = "",
        source_revision: str | None = None,
    ) -> None:
        source_rev = source_revision if source_revision is not None else revision
        if self._current_source_revision and source_rev != self._current_source_revision:
            self._decisions.invalidate_other_revisions(source_rev)
        self._current_source_revision = source_rev
        self._action_metrics["lint_checks"] += 1
        self._action_metrics["lint_failures" if failed else "lint_passes"] += 1
        self._action_metrics["engine_actions"] += 1
        self.record_producer_event(
            feature_id="syntax_result",
            action_id=action_id,
            kind="validation_result_recorded",
            detail=f"failed={failed}; path={path}",
        )
        self._mark_lifecycle(
            "static_validated",
            action_id=action_id,
            status="failed" if failed else "passed",
        )
        message = self._payload("syntax_result", "edit_result", reason)["message"]
        if failed and path:
            concise = " ".join(diagnostic.split())[:120]
            message = f"Repair the syntax failure in {path}"
            if concise:
                message += f": {concise}"
        self._emit(
            "syntax_result",
            boundary="edit_result",
            action_id=action_id,
            revision=revision,
            source_revision=source_rev,
            decision="DELIVERED" if failed else "PASS",
            reason=reason,
            payload={
                "ok": not failed,
                "fresh": True,
                "path": path,
                "command": command,
                "returncode": returncode,
                "message": message,
            },
        )

    def record_submit(
        self,
        *,
        action_id: int,
        revision: str,
        refused: bool,
        held: bool = False,
        sensor_healthy: bool,
        check_count: int = 0,
        passing_checks: int = 0,
        failing_checks: int = 0,
        blockers: tuple[str, ...] = (),
        source_revision: str | None = None,
        validating_pass_count: int | None = None,
        reason: str = "fresh_grounded_failure",
    ) -> None:
        if held and not refused:
            raise ValueError("a submit action cannot be held without a refusal")
        source_rev = source_revision if source_revision is not None else revision
        if self._current_source_revision and source_rev != self._current_source_revision:
            self._decisions.invalidate_other_revisions(source_rev)
        self._current_source_revision = source_rev
        self._action_metrics["submit_attempts"] += 1
        self.record_producer_event(
            feature_id="GT_CERT_DELIVERY",
            action_id=action_id,
            kind="submission_readiness_evaluated",
            detail=f"healthy={sensor_healthy}; checks={check_count}",
        )
        if held:
            self._action_metrics["submit_holds"] += 1
        if refused:
            self._action_metrics["submit_risks"] += 1
            self.record_producer_event(
                feature_id="submit_refusal",
                action_id=action_id,
                kind="submit_refusal_evaluated",
                detail=f"blockers={len(blockers)}",
            )
        elif sensor_healthy and passing_checks > 0:
            self._mark_lifecycle("submit_ready", action_id=action_id, status="passed")
        if refused:
            self._emit(
                "submit_refusal",
                boundary="submit",
                action_id=action_id,
                revision=revision,
                source_revision=source_rev,
                reason=reason,
                payload={
                    "submission_risk": True,
                    "refused": True,
                    "fresh_failure": True,
                    "blockers": list(blockers),
                    "message": self._payload("submit_refusal", "submit", reason)["message"],
                },
            )
            self._emit(
                "GT_SS_SUBMIT_RED",
                boundary="submit",
                action_id=action_id,
                revision=revision,
                source_revision=source_rev,
                reason=reason,
                payload={
                    "owner_feature": "submit_refusal",
                    "submission_risk": True,
                    "blockers": list(blockers),
                    "message": (
                        "Current source revision retains unverified task requirements."
                        if reason == "unverified_obligations"
                        else "Current source revision retains a failing required check."
                    ),
                },
            )
        validated_by = (
            validating_pass_count
            if validating_pass_count is not None
            else (passing_checks if sensor_healthy and failing_checks == 0 else 0)
        )
        self._emit(
            "GT_CERT_DELIVERY",
            boundary="submit",
            action_id=action_id,
            revision=revision,
            source_revision=source_rev,
            decision="DELIVERED" if sensor_healthy else "PASS",
            reason="submission_readiness_receipt",
            payload={
                "sensor_healthy": sensor_healthy,
                "refused": refused,
                "check_count": check_count,
                "passing_checks": passing_checks,
                "failing_checks": failing_checks,
                "validating_pass_count": (
                    validating_pass_count if validating_pass_count is not None else passing_checks
                ),
                "readiness": (
                    "blocked"
                    if refused
                    else "validated"
                    if sensor_healthy and validated_by > 0 and failing_checks == 0
                    else "unverified"
                ),
                "message": (
                    "Submission readiness has a declared or standard-runner "
                    "validation passing at the current revision."
                    if sensor_healthy and validated_by > 0 and failing_checks == 0
                    else (
                        "Submission readiness: no declared or standard-runner "
                        "validation has passed at the current revision."
                        if validating_pass_count == 0
                        else "Submission readiness was evaluated without claiming validation."
                    )
                ),
            },
        )

    def _route_effect(self, receipt: FeatureReceipt) -> None:
        """Route one produced receipt to its registered consumer."""
        spec = consumer_spec_for(receipt.feature_id)
        if spec is None:
            return
        effect = FeatureEffect(
            feature_id=receipt.feature_id,
            receipt_id=f"receipt-{len(self.receipts)}",
            effect_kind=spec.effect_kind,
            effect_action=dict(receipt.payload),
            required_before_action=(
                receipt.action_id if spec.required_before_next_action else None
            ),
            model_visible=bool(spec.model_visible and receipt.model_visible),
            delivery_status=(
                "candidate" if spec.model_visible and receipt.model_visible else "private"
            ),
            delivery_reason=(
                "" if spec.model_visible and receipt.model_visible else "private_consumer"
            ),
            evidence_action=receipt.action_id,
        )
        self._effects.append(effect)
        self._consumer_paths.setdefault(receipt.feature_id, []).append(spec.effect_kind.value)

    def record_producer_event(
        self,
        *,
        feature_id: str,
        action_id: int,
        kind: str,
        detail: str,
    ) -> None:
        """Record producer-side engine work without changing runtime policy."""
        self._producer_events.append(
            {
                "feature_id": feature_id,
                "action": action_id,
                "kind": kind,
                "detail": detail,
            }
        )

    @staticmethod
    def _controller_state_hash(state: CentralControllerState) -> str:
        return hashlib.sha256(repr(state.as_dict()).encode("utf-8", "replace")).hexdigest()

    def _apply_effect(self, effect: FeatureEffect, *, call: int) -> None:
        """Reduce one consumed feature payload into authoritative controller state."""
        state = self._controller_state
        before = self._controller_state_hash(state)
        payload = dict(effect.effect_action)
        section_by_feature = {
            "obligations": "contract",
            "localization": "localization",
            "GT_LOC_RESLOT": "localization",
            "def_partition": "impact",
            "caller_contract": "impact",
            "newfile_precedent": "impact",
            "GT_CHANGE_SURFACE": "change_surface",
            "GT_PATCH_DELTA": "patch_delta",
            "signature_delta": "patch_delta",
            "GT_EDIT_CHECK": "validation_plan",
            "syntax_result": "validation_results",
            "covering_red": "failure_state",
            "GT_HYPOTHESIS": "failure_state",
            "recovery": "failure_state",
            "submit_refusal": "submission_state",
            "GT_SS_SUBMIT_RED": "submission_state",
            "GT_CERT_DELIVERY": "certificate",
        }
        section_name = section_by_feature[effect.feature_id]
        section = getattr(state, section_name)
        section[effect.feature_id] = {
            **payload,
            "evidence_action": effect.evidence_action,
            "applied_before_call": call + 1,
        }
        receipt = next(
            (
                item
                for item in reversed(self.receipts)
                if item.feature_id == effect.feature_id and item.action_id == effect.evidence_action
            ),
            None,
        )
        if receipt is not None:
            state.source_revision = receipt.source_revision
            state.workspace_revision = receipt.revision
        after = self._controller_state_hash(state)
        self._effect_applications.append(
            {
                "feature_id": effect.feature_id,
                "receipt_id": effect.receipt_id,
                "effect_kind": effect.effect_kind.value,
                "evidence_action": effect.evidence_action,
                "source_revision": state.source_revision,
                "workspace_revision": state.workspace_revision,
                "state_fields_changed": [section_name] if before != after else [],
                "state_hash_before": before,
                "state_hash_after": after,
                "delivery_candidate": effect.model_visible,
                "private_consequence": "" if effect.model_visible else section_name,
                "quiet_reason": "" if before != after else "duplicate_state",
                "applied_before_call": call + 1,
            }
        )
        producer_events = [
            event
            for event in self._producer_events
            if event["feature_id"] == effect.feature_id
            and event["action"] == effect.evidence_action
        ]
        pending_reads = [
            read
            for read in self._pending_state_reads
            if read["feature_id"] == effect.feature_id
            and read["evidence_action"] == effect.evidence_action
        ]
        self._pending_state_reads = [
            read
            for read in self._pending_state_reads
            if not (
                read["feature_id"] == effect.feature_id
                and read["evidence_action"] == effect.evidence_action
            )
        ]
        self._effect_trace.append(
            {
                "effect_id": effect.receipt_id,
                "feature_id": effect.feature_id,
                "effect_kind": effect.effect_kind.value,
                "evidence_action": effect.evidence_action,
                "applied_call": call,
                "source_revision": state.source_revision,
                "workspace_revision": state.workspace_revision,
                "state_fields_changed": [section_name] if before != after else [],
                "state_reads": [
                    {"action": read["action"], "purpose": read["purpose"]} for read in pending_reads
                ],
                "actuator_events": [
                    {
                        "kind": "producer_engine_event",
                        "action": event["action"],
                        "event": event["kind"],
                        "detail": event["detail"],
                    }
                    for event in producer_events
                ],
                "provider_delivery_ids": [],
                "context_compiler": {},
                "disposition": (
                    "existing_engine_actuation"
                    if pending_reads
                    else "engine_internal_state"
                    if producer_events
                    else "audit_only"
                ),
                "timing": {
                    "evidence_before_effect": (
                        effect.applied_after_action is not None
                        and effect.applied_after_action >= effect.evidence_action
                    ),
                    "late": effect.late,
                    "predictive": effect.predictive,
                },
            }
        )

    def _trace_for_effect(
        self, feature_id: str, *, evidence_action: int | None = None
    ) -> dict[str, Any] | None:
        candidates = [
            row
            for row in self._effect_trace
            if row["feature_id"] == feature_id
            and (evidence_action is None or row["evidence_action"] == evidence_action)
        ]
        return candidates[-1] if candidates else None

    def record_existing_consumer_read(
        self, *, feature_id: str, action_id: int, purpose: str
    ) -> None:
        """Record an existing state read without changing runtime behavior."""
        row = self._trace_for_effect(feature_id)
        if row is None:
            self._pending_state_reads.append(
                {
                    "feature_id": feature_id,
                    "evidence_action": action_id,
                    "action": action_id,
                    "purpose": purpose,
                }
            )
            return
        row["state_reads"].append({"action": action_id, "purpose": purpose})
        if row["disposition"] == "audit_only":
            row["disposition"] = "existing_engine_actuation"
        row["actuator_events"].append(
            {"kind": "existing_consumer_read", "action": action_id, "purpose": purpose}
        )

    def record_provider_delivery(self, *, effect_ids: Iterable[str], delivery_id: str) -> None:
        """Link a confirmed provider delivery to its contributing effects."""
        ids = set(effect_ids)
        for row in self._effect_trace:
            if row["effect_id"] not in ids:
                continue
            row["provider_delivery_ids"].append(delivery_id)
            row["actuator_events"].append({"kind": "provider_payload", "delivery_id": delivery_id})
            row["disposition"] = "provider_payload"
            if row.get("context_compiler"):
                row["context_compiler"].setdefault(
                    "first_status", row["context_compiler"].get("status", "")
                )
                row["context_compiler"]["status"] = "provider_payload"
                row["context_compiler"]["provider_delivery_id"] = delivery_id
            for index, effect in enumerate(self._effects):
                if effect.receipt_id != row["effect_id"]:
                    continue
                self._effects[index] = replace(
                    effect,
                    delivery_status="delivered",
                    delivery_reason="provider_payload",
                )
                for receipt_index, receipt in enumerate(self.receipts):
                    if (
                        receipt.feature_id == effect.feature_id
                        and receipt.action_id == effect.evidence_action
                    ):
                        self.receipts[receipt_index] = replace(
                            receipt,
                            delivery_status="delivered",
                            delivery_reason="provider_payload",
                        )
                        break
                break

    def consume_effects(self, *, action_id: int, call: int) -> list[FeatureEffect]:
        """Return effects produced since the last consume, with timing bound."""
        fresh = self._effects[self._effect_cursor :]
        self._effect_cursor = len(self._effects)
        consumed: list[FeatureEffect] = []
        for offset, effect in enumerate(fresh):
            applied = max(effect.evidence_action, action_id)
            required = effect.required_before_action
            updated = replace(
                effect,
                applied_after_action=applied,
                delivered_before_call=call,
                predecided_actions_executed_after_evidence=max(
                    0, applied - effect.evidence_action - 1
                ),
                late=required is not None and applied > required,
                # `consume_effects` runs after the action has returned its
                # evidence.  Applying at that same action is immediate; a
                # predictive effect would have to precede its evidence.
                predictive=applied < effect.evidence_action,
            )
            self._effects[self._effect_cursor - len(fresh) + offset] = updated
            self._apply_effect(updated, call=call)
            consumed.append(updated)
        return consumed

    def _effect_accountability(self) -> list[dict[str, Any]]:
        """Classify effects by an observed downstream consumer.

        Mutating private state is not evidence of usefulness.  The categories
        deliberately separate confirmed provider delivery, an existing engine
        read, a prepared decision frame, a still-pending claim, and inert state.
        """
        decision_summary = self._decisions.summary()
        claims = {row["claim_id"]: row for row in decision_summary["claims"]}
        framed_claims = {
            claim_id for frame in decision_summary["frames"] for claim_id in frame["claim_ids"]
        }
        rows: list[dict[str, Any]] = []
        for trace in self._effect_trace:
            compiler_status = str((trace.get("context_compiler") or {}).get("status") or "")
            linked_claim_ids = [
                claim_id
                for claim_id, receipt_key in self._claim_receipts.items()
                if receipt_key == (trace["feature_id"], trace["evidence_action"])
            ]
            if trace["provider_delivery_ids"]:
                outcome = "provider_payload"
            elif trace["state_reads"]:
                outcome = "existing_engine_actuation"
            elif any(claim_id in framed_claims for claim_id in linked_claim_ids):
                outcome = "prepared_decision_frame"
            elif compiler_status in {
                "audit_only",
                "controller_state_considered",
                "existing_engine_actuation",
                "no_eligible_model_call",
                "provider_payload",
                "stale_state_rejected",
                "superseded_before_request",
            }:
                outcome = compiler_status
            elif any(claims[claim_id]["active"] for claim_id in linked_claim_ids):
                if int(trace.get("applied_call") or 0) >= self._last_context_compiler_call:
                    outcome = "no_eligible_model_call"
                else:
                    outcome = "pending_decision_claim"
            elif linked_claim_ids:
                suppressed = any(
                    claims[claim_id].get("invalidated_reason") == "task_start_advisory_disabled"
                    for claim_id in linked_claim_ids
                )
                outcome = (
                    "controller_state_suppressed" if suppressed else "expired_unconsumed_claim"
                )
            elif trace.get("disposition") == "engine_internal_state":
                # A producer event proves that the effect performed
                # deterministic engine work even when no provider payload was
                # emitted.  It is not equivalent to an unused receipt.
                outcome = "engine_internal_state"
            elif trace["state_fields_changed"]:
                # State changed, but this receipt has no recorded producer or
                # downstream read. Keep this category explicit rather than
                # claiming that every private state mutation was useful.
                outcome = "unread_private_state"
            else:
                outcome = "audit_only"
            rows.append(
                {
                    "effect_id": trace["effect_id"],
                    "feature_id": trace["feature_id"],
                    "evidence_action": trace["evidence_action"],
                    "outcome": outcome,
                    "claim_ids": linked_claim_ids,
                    "provider_delivery_ids": list(trace["provider_delivery_ids"]),
                    "state_read_count": len(trace["state_reads"]),
                }
            )
        return rows

    def record_skipped_action(self, *, action_id: int) -> None:
        """Count one pre-decided action cancelled by an immediate control."""
        self._action_metrics["interrupted_actions"] += 1
        self._mark_lifecycle("batch_interrupted", action_id=action_id)

    def record_predecided_continuation(self, *, evidence_action: int, executed: int) -> None:
        """Audit actions already chosen in the same model response; never cancel them."""
        if executed <= 0:
            return
        for index, effect in enumerate(self._effects):
            if effect.evidence_action == evidence_action:
                self._effects[index] = replace(
                    effect,
                    predecided_actions_executed_after_evidence=executed,
                )

    def record_batch_interrupt(self, *, action_id: int, cancelled: int, reason: str) -> None:
        self._action_metrics["batch_interrupts"] += 1
        self._batch_interrupts.append(
            {"evidence_action": action_id, "cancelled": cancelled, "reason": reason}
        )
        for index, effect in enumerate(self._effects):
            if effect.evidence_action == action_id and effect.required_before_action is not None:
                self._effects[index] = replace(effect, predecided_actions_cancelled=cancelled)

    def context_compiler_state(self) -> list[dict[str, Any]]:
        """Return at most one current controller-state candidate per feature.

        The provider compiler considers these rows but does not render their
        payloads.  Model-visible facts still require the existing grounded
        decision-frame path; this index exists to prove how private feature
        effects narrow controller state without inflating the prompt.
        """

        latest: dict[str, dict[str, Any]] = {}
        for trace in self._effect_trace:
            latest[str(trace["feature_id"])] = trace
        return [
            {
                "effect_id": str(trace["effect_id"]),
                "feature_id": feature_id,
                "evidence_action": int(trace["evidence_action"]),
                "action_id": int(trace["evidence_action"]),
                "applied_call": int(trace["applied_call"]),
                "source_revision": str(trace.get("source_revision") or ""),
                "workspace_revision": str(trace.get("workspace_revision") or ""),
                "effect_disposition": str(trace.get("disposition") or ""),
                "state_fields_changed": list(trace.get("state_fields_changed") or ()),
            }
            for feature_id, trace in sorted(latest.items())
        ]

    def record_context_compiler_call(
        self,
        *,
        call: int,
        request_payload_sha256: str,
        fact_accounting: Iterable[dict[str, Any]],
    ) -> None:
        """Account for every effect eligible before this provider request.

        This is provenance, not a claim of model influence.  A private effect
        may be considered as controller state, superseded by newer state, or
        remain audit-only.  Only a confirmed delivery is labelled provider
        visible.
        """

        self._last_context_compiler_call = max(self._last_context_compiler_call, call)
        facts_by_effect = {
            str(row.get("effect_id") or ""): row for row in fact_accounting if row.get("effect_id")
        }
        latest_by_feature: dict[str, str] = {}
        for trace in self._effect_trace:
            if int(trace.get("applied_call") or 0) < call:
                latest_by_feature[str(trace["feature_id"])] = str(trace["effect_id"])
        for trace in self._effect_trace:
            if trace.get("context_compiler"):
                continue
            applied_call = int(trace.get("applied_call") or 0)
            if applied_call >= call:
                continue
            effect_id = str(trace["effect_id"])
            fact = facts_by_effect.get(effect_id)
            if trace.get("provider_delivery_ids"):
                status = "provider_payload"
            elif fact is not None:
                status = (
                    "stale_state_rejected"
                    if fact.get("disposition") == "stale_source_revision"
                    else "controller_state_considered"
                )
            elif latest_by_feature.get(str(trace["feature_id"])) != effect_id:
                status = "superseded_before_request"
            elif trace.get("state_reads"):
                status = "existing_engine_actuation"
            else:
                status = "audit_only"
            trace["context_compiler"] = {
                "status": status,
                "first_eligible_call": applied_call + 1,
                "first_considered_call": call,
                "one_step_late": call != applied_call + 1,
                "request_payload_sha256": request_payload_sha256,
                "fact_id": str((fact or {}).get("fact_id") or ""),
                "fact_disposition": str((fact or {}).get("disposition") or ""),
                "provider_message_indices": list(
                    (fact or {}).get("provider_message_indices") or ()
                ),
                "superseded_by_effect_id": (
                    latest_by_feature.get(str(trace["feature_id"]), "")
                    if status == "superseded_before_request"
                    else ""
                ),
            }

    def progress_ledger(self) -> dict[str, Any]:
        """Bounded deterministic state summary for compacted provider views.

        Carries forward what the model actually did so compaction does not erase
        working memory: the latest source edit, the latest validation result,
        the unresolved failure, distinct read/search targets, and changed paths.
        """
        declared = {check: state for check, state in self._declared_check_states.items()}
        return {
            "last_edit": self._last_edit,
            "latest_validation": self._latest_validation,
            "unresolved_failure": self._latest_failure,
            "read_history": [
                dict(item)
                for item in self._recent_reads
                if item.get("source_revision")
            ],
            "recent_reads": [
                dict(item)
                for item in self._recent_reads
                if not self._current_source_revision
                or item.get("source_revision") == self._current_source_revision
            ],
            "changed_paths": list(self._recent_source_paths),
            "declared_checks": declared or list(self._explicit_checks),
            "source_revision": self._current_source_revision,
            "feature_states": self.context_compiler_state(),
        }

    def _delivery_disposition(self, receipt: FeatureReceipt) -> FeatureDeliveryDisposition:
        candidate = bool(
            receipt.payload.get("message")
            and feature_payload_grounded(receipt.feature_id, receipt.payload)
            and self._render_feature_fact(receipt)
        )
        if not candidate:
            return FeatureDeliveryDisposition.PRIVATE_INELIGIBLE
        status = str(receipt.delivery_status or "")
        reason = str(receipt.delivery_reason or "")
        if (
            status == "delivered"
            or (
                receipt.feature_id,
                receipt.revision,
                "",
            )
            in self._guided_keys
        ):
            return FeatureDeliveryDisposition.CANDIDATE_DELIVERED
        if reason == "represented_in_action_history":
            return FeatureDeliveryDisposition.CANDIDATE_REPRESENTED
        if reason == "not_selected_first_eligible_request":
            return FeatureDeliveryDisposition.CANDIDATE_WINDOW_UNSELECTED
        if "stale" in reason:
            return FeatureDeliveryDisposition.CANDIDATE_STALE
        if "budget" in reason:
            return FeatureDeliveryDisposition.CANDIDATE_BUDGET_REJECTED
        if status == "pending" and not self._last_context_compiler_call:
            return FeatureDeliveryDisposition.NO_ELIGIBLE_MODEL_CALL
        return FeatureDeliveryDisposition.CANDIDATE_POLICY_REJECTED

    def summary(self) -> dict[str, Any]:
        by_feature = {feature_id: 0 for feature_id in CENTRAL_FEATURE_IDS}
        for receipt in self.receipts:
            by_feature[receipt.feature_id] += 1
        accountability = self._effect_accountability()
        compiler_accountability = [
            {
                "effect_id": str(trace["effect_id"]),
                "feature_id": str(trace["feature_id"]),
                "evidence_action": int(trace["evidence_action"]),
                **(
                    dict(trace.get("context_compiler") or {})
                    if trace.get("context_compiler")
                    else {
                        "status": (
                            "no_eligible_model_call"
                            if int(trace.get("applied_call") or 0)
                            >= self._last_context_compiler_call
                            else "unaccounted_bug"
                        ),
                        "terminal": True,
                        "eligible_model_calls_after_effect": 0,
                    }
                ),
            }
            for trace in self._effect_trace
        ]
        opportunity_rows = [item.as_dict() for item in self._feature_opportunities]
        feature_applicability: dict[str, dict[str, Any]] = {}
        for feature_id in CENTRAL_FEATURE_IDS:
            rows = [row for row in opportunity_rows if row["feature_id"] == feature_id]
            eligible = [row for row in rows if row["evidence_status"] == "eligible"]
            feature_receipts = [
                receipt for receipt in self.receipts if receipt.feature_id == feature_id
            ]
            dispositions = [
                self._delivery_disposition(receipt) for receipt in feature_receipts
            ]
            delivered = any(
                disposition is FeatureDeliveryDisposition.CANDIDATE_DELIVERED
                for disposition in dispositions
            )
            certified = bool(feature_receipts)
            if delivered:
                lifecycle_state = "DELIVERED"
            elif certified:
                lifecycle_state = "CERTIFIED"
            elif eligible:
                lifecycle_state = "ABSTAINED"
            elif rows and rows[-1]["evidence_status"] == "ambiguous_evidence":
                lifecycle_state = "ABSTAINED"
            else:
                lifecycle_state = "NOT_APPLICABLE"
            feature_applicability[feature_id] = {
                "evaluations": len(rows),
                "eligible": len(eligible),
                "fired": by_feature[feature_id],
                "status": lifecycle_state,
                "lifecycle_state": lifecycle_state,
                "reason_codes": (
                    list(dict.fromkeys(row["reason_code"] for row in rows))
                    if rows
                    else ["deterministic_trigger_not_applicable"]
                ),
            }
        required_claims_without_declared_id = sum(
            1
            for item in self.receipts
            if item.feature_id in {"submit_refusal", "GT_SS_SUBMIT_RED"}
            and item.boundary == "test_result"
            and not item.payload.get("declared_check_id")
        )
        redundant_provider_payloads = sum(
            1
            for item in self.receipts
            if item.delivery_status == "delivered"
            and item.delivery_reason == "represented_in_action_history"
        )
        delivery_dispositions = [self._delivery_disposition(item) for item in self.receipts]
        delivery_disposition_counts = {
            disposition.value: delivery_dispositions.count(disposition)
            for disposition in FeatureDeliveryDisposition
        }
        guidance_candidates = sum(
            disposition is not FeatureDeliveryDisposition.PRIVATE_INELIGIBLE
            for disposition in delivery_dispositions
        )
        guidance_delivered_receipts = delivery_disposition_counts[
            FeatureDeliveryDisposition.CANDIDATE_DELIVERED.value
        ]
        return {
            "enabled": self.enabled,
            "feature_count": len(CENTRAL_FEATURE_IDS),
            "feature_ids": list(CENTRAL_FEATURE_IDS),
            "guidance_events": self._guidance_events,
            "guidance_chars": self._guidance_chars,
            "guidance_features": list(self._guidance_features),
            "guidance_candidates": guidance_candidates,
            # Compatibility field: unlike the historical counter, this now
            # counts only genuine candidates that did not become a provider
            # delivery. Engine-private effects are never called suppressed.
            "guidance_suppressed": guidance_candidates - guidance_delivered_receipts,
            "legacy_guidance_suppressed_counter": self._guidance_suppressed,
            "feature_delivery_disposition_counts": delivery_disposition_counts,
            "guidance_by_feature": {
                feature_id: self._guidance_features.count(feature_id)
                for feature_id in dict.fromkeys(self._guidance_features)
            },
            "action_metrics": dict(self._action_metrics),
            "lifecycle": dict(self._lifecycle),
            "produced_counts": by_feature,
            "consumer_paths": dict(self._consumer_paths),
            "effects": [effect.as_dict() for effect in self._effects],
            "effect_applications": list(self._effect_applications),
            "effect_trace": [dict(row) for row in self._effect_trace],
            "effect_accountability": accountability,
            "effect_accountability_counts": {
                outcome: sum(row["outcome"] == outcome for row in accountability)
                for outcome in sorted({row["outcome"] for row in accountability})
            },
            "context_compiler_effect_accountability": compiler_accountability,
            "context_compiler_effect_accountability_counts": {
                status: sum(row["status"] == status for row in compiler_accountability)
                for status in sorted({row["status"] for row in compiler_accountability})
            },
            "producer_events": list(self._producer_events),
            "controller_state": self._controller_state.as_dict(),
            "batch_interrupts": list(self._batch_interrupts),
            "source_epoch": self._source_epoch,
            "validation_log": list(self._validation_log),
            "required_check_claims_without_declared_id": required_claims_without_declared_id,
            "redundant_provider_payloads": redundant_provider_payloads,
            "declared_check_states": dict(self._declared_check_states),
            "semantic_decisions": self._decisions.summary(),
            "structural_evidence": dict(self._structural_evidence),
            "feature_opportunities": opportunity_rows,
            "certification_decisions": [dict(item) for item in self._certification_decisions],
            "feature_applicability": feature_applicability,
            "all_feature_opportunities_accounted": (
                set(feature_applicability) == set(CENTRAL_FEATURE_IDS)
                and all(
                    row["status"]
                    in {
                        "NOT_APPLICABLE",
                        "CANDIDATE",
                        "CERTIFIED",
                        "DELIVERED",
                        "CONSUMED",
                        "VALIDATED",
                        "CONTRADICTED",
                        "ABSTAINED",
                    }
                    for row in feature_applicability.values()
                )
            ),
            "preflight_receipts": list(self._preflight_receipts),
            "action_cycles": [cycle.as_dict() for cycle in self._action_cycles.values()],
            "receipts": [
                {
                    "feature_id": item.feature_id,
                    "kind": item.kind,
                    "boundary": item.boundary,
                    "action": item.action_id,
                    "revision": item.revision,
                    "decision": item.decision,
                    "reason": item.reason,
                    "payload": item.payload,
                    "fresh": item.fresh,
                    "model_visible": item.model_visible,
                    "delivery_status": item.delivery_status,
                    "delivery_reason": item.delivery_reason,
                    "source_revision": item.source_revision,
                    "source_epoch": item.source_epoch,
                    "delivery_disposition": self._delivery_disposition(item).value,
                }
                for item in self.receipts
            ],
        }

    def _record_guidance(self, metadata: dict[str, Any]) -> None:
        feedback = str(metadata["feedback"])
        feature_id = str(metadata["feature_id"])
        delivery_id = str(metadata.get("delivery_id") or f"guidance-{self._guidance_events + 1}")
        metadata["delivery_id"] = delivery_id
        self.record_provider_delivery(
            effect_ids=metadata.get("effect_ids") or (),
            delivery_id=delivery_id,
        )
        self._guidance_events += 1
        self._guidance_chars += len(feedback)
        self._guidance_features.append(feature_id)

    def prepared_guidance(self) -> dict[str, Any] | None:
        return dict(self._prepared_guidance) if self._prepared_guidance else None

    def confirm_prepared_guidance(self) -> dict[str, Any] | None:
        """Count a deferred advisory only when it reaches a model request."""
        if self._prepared_guidance is None:
            return None
        metadata = self._prepared_guidance
        self._prepared_guidance = None
        self._record_guidance(metadata)
        return dict(metadata)

    def discard_model_feedback(self) -> None:
        """Consume candidates superseded by a direct submit-hold observation."""
        fresh_receipts = self.receipts[self._feedback_cursor :]
        self._feedback_cursor = len(self.receipts)
        for item in fresh_receipts:
            if item.model_visible and item.payload.get("message"):
                self._guidance_candidates += 1
            self._guidance_suppressed += 1

    @staticmethod
    def _render_feature_fact(item: FeatureReceipt) -> str:
        payload = item.payload
        feature_id = item.feature_id
        if feature_id == "syntax_result":
            diagnostic = " ".join(str(payload.get("diagnostic") or "").split())
            outcome = diagnostic or f"return code {payload.get('returncode')}"
            return (
                f"Syntax check failed for {payload.get('path')} using "
                f"{payload.get('command')}: {outcome}."
            )
        if feature_id == "covering_red":
            return (
                f"Validation failed for the current source revision using "
                f"{payload.get('command')} ({payload.get('attribution')}): "
                f"{' '.join(str(payload.get('diagnostic') or '').split())}."
            )
        if feature_id == "recovery":
            alternate = payload.get("alternate_action") or {}
            paths = ", ".join(alternate.get("paths") or ())
            return (
                f"The same validation failure repeated {payload.get('repeat_count')} times "
                f"without a source revision change; recorded changed source: "
                f"{paths or 'unknown'}; distinguishing failure evidence: "
                f"{alternate.get('discriminator')}."
            )
        if feature_id == "signature_delta":
            paths = ", ".join(payload.get("changed_paths") or ())
            caller_paths = ", ".join(
                str(item.get("caller_path") or item.get("path") or "")
                for item in payload.get("callers") or ()
            )
            caller_fact = f" Known callers: {caller_paths}." if caller_paths else ""
            return (
                f"Signature changed for {payload.get('symbol')} in {paths}: "
                f"{payload.get('before_signature')} -> {payload.get('after_signature')}."
                + caller_fact
            )
        if feature_id == "newfile_precedent":
            created = ", ".join(payload.get("created_files") or ())
            return (
                f"New file {created} has concrete repository precedent "
                f"{payload.get('precedent_path')}."
            )
        if feature_id == "GT_LOC_RESLOT":
            anchors = payload.get("selected_anchors") or ()
            rendered = ", ".join(f"{item.get('path')}:{item.get('line')}" for item in anchors)
            return f"Highest-ranked source anchors: {rendered}."
        if feature_id == "submit_refusal":
            return (
                "Current source revision still has a failing required check: "
                + ", ".join(payload.get("blockers") or ())
                + "."
            )
        if feature_id == "GT_EDIT_CHECK":
            paths = ", ".join(payload.get("changed_paths") or ())
            return (
                f"Unvalidated authored changes in {paths}; declared check: "
                f"{payload.get('declared_check')}."
            )
        return ""

    @staticmethod
    def _history_action_text(history: Iterable[dict[str, Any]]) -> tuple[str, ...]:
        """Return assistant-authored action/reasoning text for representation checks."""
        rows: list[str] = []
        for message in history:
            if str(message.get("role") or "") != "assistant":
                continue
            parts = [str(message.get("content") or "")]
            for action in (message.get("extra") or {}).get("actions") or ():
                if isinstance(action, dict):
                    parts.append(str(action.get("command") or action.get("cmd") or ""))
            text = " ".join(part for part in parts if part).strip()
            if text:
                rows.append(text)
        return tuple(rows)

    @staticmethod
    def _anchor_in_text(anchor: str, text: str) -> bool:
        value = " ".join(str(anchor or "").replace("\\", "/").split()).strip()
        if not value:
            return False
        haystack = " ".join(text.replace("\\", "/").split())
        variants = {value, value.lstrip("./")}
        if value.startswith("/app/"):
            variants.add(value[5:])
        return any(
            candidate and candidate.casefold() in haystack.casefold() for candidate in variants
        )

    @classmethod
    def _receipt_represented_in_history(
        cls, receipt: FeatureReceipt, history: Iterable[dict[str, Any]]
    ) -> bool:
        """Whether a selected grounded fact is already in model-authored history."""
        texts = cls._history_action_text(history)
        if not texts:
            return False
        payload = receipt.payload
        if receipt.feature_id == "newfile_precedent":
            anchors = [
                *(payload.get("created_files") or ()),
                payload.get("precedent_path") or "",
            ]
        elif receipt.feature_id == "signature_delta":
            anchors = [
                *(payload.get("changed_paths") or ()),
                payload.get("symbol") or "",
                payload.get("before_signature") or "",
                payload.get("after_signature") or "",
            ]
        else:
            return False
        anchors = [str(anchor) for anchor in anchors if str(anchor).strip()]
        return bool(anchors) and any(
            all(cls._anchor_in_text(anchor, text) for anchor in anchors) for text in texts
        )

    @staticmethod
    def _change_surface_self_echo(receipt: FeatureReceipt) -> bool:
        """True when feature guidance would re-present the model's own edit surface.

        materiality_shared_abstention_v1: newfile_precedent always has a
        model-created subject; signature_delta without certified preexisting
        callers is change-surface echo rather than a compatibility obligation.
        """
        if receipt.feature_id == "newfile_precedent":
            created = [
                str(path).strip()
                for path in (receipt.payload.get("created_files") or ())
                if str(path).strip()
            ]
            return bool(created)
        if receipt.feature_id == "signature_delta":
            callers = [
                caller
                for caller in (receipt.payload.get("callers") or ())
                if caller
            ]
            if callers:
                return False
            changed = [
                str(path).strip()
                for path in (receipt.payload.get("changed_paths") or ())
                if str(path).strip()
            ]
            return bool(changed)
        return False

    def model_feedback(
        self,
        *,
        limit: int = 320,
        deferred: bool = False,
        for_call: int | None = None,
        history: Iterable[dict[str, Any]] = (),
    ) -> str:
        """Materialize one persistent claim for a current decision need.

        Fresh receipts are counted for the audit funnel, but eligibility comes
        from the persistent semantic store.  A valid lower-priority fact is no
        longer destroyed merely because another feature won the previous
        provider slot.
        """
        fresh_receipts = self.receipts[self._feedback_cursor :]
        self._feedback_cursor = len(self.receipts)
        for item in fresh_receipts:
            if (
                item.model_visible
                and item.payload.get("message")
                and feature_payload_grounded(item.feature_id, item.payload)
                and self._render_feature_fact(item)
            ):
                self._guidance_candidates += 1
            else:
                self._guidance_suppressed += 1

        if for_call is None:
            next_evidence_call = 1 + max(
                (item.action_id for item in self.receipts),
                default=0,
            )
            call = max(self._feedback_calls + 1, next_evidence_call)
            self._feedback_calls = call
        else:
            call = max(1, int(for_call))
            self._feedback_calls = max(self._feedback_calls, call)
        source_revision = self._current_source_revision
        if not source_revision:
            return ""
        frame = self._decisions.materialize(
            call=call,
            source_revision=source_revision,
        )
        if frame is None:
            self._suppress_unselected_first_window(call=call)
            return ""

        selected_items: list[FeatureReceipt] = []
        for claim_id in frame.claim_ids:
            claim = self._decisions.claim(claim_id)
            if claim is None:
                continue
            selected = next(
                (
                    item
                    for item in reversed(self.receipts)
                    if item.feature_id == claim.feature_id
                    and item.source_revision == claim.source_revision
                    and item.model_visible
                    and self._render_feature_fact(item) == claim.fact
                ),
                None,
            )
            if selected is not None:
                selected_items.append(selected)
        represented_items = [
            item for item in selected_items if self._receipt_represented_in_history(item, history)
        ]
        for item in represented_items:
            self._suppress_receipt_delivery(item, reason="represented_in_action_history")
        if represented_items:
            selected_items = [item for item in selected_items if item not in represented_items]
        self_echo_items = [
            item for item in selected_items if self._change_surface_self_echo(item)
        ]
        for item in self_echo_items:
            self._suppress_receipt_delivery(item, reason="change_surface_self_echo")
        if self_echo_items:
            selected_items = [item for item in selected_items if item not in self_echo_items]
        if not selected_items:
            self._suppress_unselected_first_window(call=call)
            self._guidance_suppressed += 1
            return ""

        claim_anchors = tuple(
            dict.fromkeys(
                anchor
                for claim_id in frame.claim_ids
                for claim in [self._decisions.claim(claim_id)]
                if claim is not None
                for anchor in claim.anchors
            )
        )
        structural_features = {"signature_delta", "newfile_precedent", "GT_LOC_RESLOT"}
        authority = (
            EvidenceAuthority.CERTIFIED_STRUCTURAL
            if selected_items
            and all(item.feature_id in structural_features for item in selected_items)
            else EvidenceAuthority.MECHANICAL
        )
        opportunity_kind = {
            DecisionNeedKind.LOCALIZE_TASK: OpportunityKind.LOCALIZATION_CONTRACTION,
            DecisionNeedKind.REPAIR_IMPACT: OpportunityKind.LOCALIZATION_CONTRACTION,
            DecisionNeedKind.REPAIR_FAILURE: OpportunityKind.DECLARED_CHECK_FAILURE,
            DecisionNeedKind.RECOVER_FAILURE: OpportunityKind.REPEATED_FAILURE,
            DecisionNeedKind.VALIDATE_CHANGE: OpportunityKind.DECLARED_CHECK_FAILURE,
            DecisionNeedKind.SUBMIT_SAFELY: OpportunityKind.DECLARED_CHECK_FAILURE,
        }[frame.need_kind]
        opportunity = certify_opportunity(
            kind=opportunity_kind,
            authority=authority,
            source_revision=frame.source_revision,
            current_source_revision=source_revision,
            workspace_revision=selected_items[0].revision,
            evidence_ids=tuple(frame.claim_ids),
            concrete_anchors=claim_anchors,
            absent_from_provider_history=True,
            decision_relevant=True,
            eligible_call=min(frame.evidence_actions) + 1,
            current_call=call,
        )
        self._certification_decisions.append(
            {
                "boundary": "provider_guidance",
                "action_id": min(frame.evidence_actions),
                **opportunity.as_dict(),
            }
        )
        if not opportunity.certified:
            for item in selected_items:
                self._suppress_receipt_delivery(
                    item,
                    reason="opportunity_" + "_".join(opportunity.reason_codes),
                )
            self._guidance_suppressed += len(selected_items)
            return ""

        # A source-bound submit risk generated from the same failing check is
        # a private contributor to the failure frame, not a duplicate sentence.
        covering_actions = {
            item.action_id for item in selected_items if item.feature_id == "covering_red"
        }
        for item in self.receipts:
            if (
                item.feature_id == "submit_refusal"
                and item.action_id in covering_actions
                and item not in selected_items
            ):
                selected_items.append(item)

        self._suppress_unselected_first_window(
            call=call,
            selected_items=selected_items,
        )

        feedback = render_runtime_advisory(frame.text, limit=limit)
        if not feedback:
            self._guidance_suppressed += len(selected_items)
            return ""
        selected = selected_items[0]
        contributing_features: list[str] = []
        for item in selected_items:
            for feature_id in [
                item.feature_id,
                *(item.payload.get("contributing_features") or []),
            ]:
                if feature_id not in contributing_features:
                    contributing_features.append(feature_id)
            self._guided_keys.add((item.feature_id, item.revision, ""))

        effect_ids = [
            row["effect_id"]
            for row in self._effect_trace
            if any(
                row["feature_id"] == item.feature_id and row["evidence_action"] == item.action_id
                for item in selected_items
            )
        ]
        metadata = {
            "feature_id": selected.feature_id,
            "contributing_features": contributing_features,
            "effect_ids": effect_ids,
            "claim_ids": list(frame.claim_ids),
            "claim_anchors": list(claim_anchors),
            "certified_opportunity": opportunity.as_dict(),
            "decision_need_id": frame.need_id,
            "decision_need_kind": frame.need_kind.value,
            "decision_frame_id": frame.frame_id,
            "evidence_action": min(frame.evidence_actions),
            "evidence_actions": list(frame.evidence_actions),
            "revision": selected.revision,
            "source_revision": frame.source_revision,
            "materialized_for_call": frame.materialized_for_call,
            "decision_boundary": (
                "FAILURE_OBSERVATION"
                if frame.need_kind
                in {DecisionNeedKind.REPAIR_FAILURE, DecisionNeedKind.RECOVER_FAILURE}
                else "POST_EDIT_GRAPH_DELTA"
                if frame.need_kind is DecisionNeedKind.REPAIR_IMPACT
                else "VERIFICATION_SELECTION"
                if frame.need_kind is DecisionNeedKind.VALIDATE_CHANGE
                else "REPOSITORY_START"
                if frame.need_kind is DecisionNeedKind.LOCALIZE_TASK
                else "PRE_SUBMIT"
            ),
            "feedback": feedback,
            # Provider-value certification is deliberately derived from the
            # selected typed feature receipt, not from the rendered sentence.
            # Signature guidance is authorized only when the existing caller
            # contract supplies a concrete nonlocal endpoint.
            "certified_nonlocal_relation": bool(
                selected.feature_id == "signature_delta"
                and selected.payload.get("callers")
            ),
            "relation": (
                "CALLS"
                if selected.feature_id == "signature_delta"
                and selected.payload.get("callers")
                else ""
            ),
            "relation_endpoint": next(
                (
                    str(item.get("caller_path") or item.get("path") or "")
                    for item in selected.payload.get("callers") or ()
                    if str(item.get("caller_path") or item.get("path") or "")
                ),
                "",
            ),
            "certified_predecision_gap": bool(
                selected.feature_id == "GT_EDIT_CHECK"
                and selected.payload.get("intervention") == "validation_debt"
                and selected.payload.get("declared_check")
                and selected.payload.get("changed_paths")
            ),
        }
        if deferred:
            self._prepared_guidance = metadata
        else:
            self._record_guidance(metadata)
        return feedback
