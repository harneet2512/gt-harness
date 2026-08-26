"""Canonical repository graph lifecycle and query boundary.

Every product surface uses this service.  A SQLite file is never evidence by
itself: graph-derived answers are released only through a receipt bound to the
current Git commit, graph-input content revision, schema, and database digest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from gt_engine.graph_inputs import is_graph_input
from gt_engine.indexer import ensure_index_with_receipt
from gt_harness.indexer_setup import GT_INDEX_BUILD_ID

GRAPH_BUILDER_VERSION = f"gt-index-{GT_INDEX_BUILD_ID}-repository-identity-v4"
GRAPH_RECEIPT_SCHEMA = "gt.graph_receipt.v5"
CANONICAL_QUERY_MODES = (
    "definition",
    "callers",
    "callees",
    "imports",
    "importers",
    "reexports",
    "exporters",
    "implementations",
    "subclasses",
    "references",
    "impact",
    "tests",
    "search",
)
QUERY_MODE_ALIASES = {
    "definitions": "definition",
    "caller": "callers",
    "callee": "callees",
    "import": "imports",
    "importer": "importers",
    "reexport": "reexports",
    "exporter": "exporters",
    "implementation": "implementations",
    "subclass": "subclasses",
    "reference": "references",
    "test": "tests",
    "refs": "references",
}
SUPPORTED_QUERY_MODES = tuple((*CANONICAL_QUERY_MODES, *QUERY_MODE_ALIASES))
_TYPE_ANCHOR_LABELS = ("class", "interface", "trait", "struct", "enum", "type")
PUBLIC_GRAPH_RECEIPT_FIELDS = (
    "receipt_schema",
    "generation_id",
    "build_attempt_id",
    "manifest_sha256",
    "repository",
    "commit_sha",
    "working_tree_state",
    "source_revision",
    "graph_schema_version",
    "graph_builder_version",
    "build_started",
    "build_completed",
    "build_status",
    "files_discovered",
    "files_attempted",
    "files_indexed",
    "files_skipped",
    "files_failed",
    "symbols",
    "nodes_by_type",
    "edges_by_type",
    "coverage",
    "build_duration_ms",
    "persistent_graph_path",
    "graph_checksum_or_identity",
    "query_ready",
    "degraded_reasons",
    "component_failures",
    "parser_limitations",
    "skipped_reasons",
    "update_mode",
    "graph_bytes",
    "source_bytes",
)
_READY = frozenset({"READY", "READY_WITH_DECLARED_LIMITATIONS"})
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".gt",
        ".groundtruth",
        ".idea",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".vscode",
        ".venv",
        ".eggs",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
    }
)


class GraphStatus(StrEnum):
    ABSENT = "ABSENT"
    BUILDING = "BUILDING"
    READY = "READY"
    READY_WITH_DECLARED_LIMITATIONS = "READY_WITH_DECLARED_LIMITATIONS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STALE = "STALE"


class GraphNotReadyError(RuntimeError):
    """Raised when a caller attempts to query an uncertified graph."""


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    repository: str
    commit_sha: str
    branch: str
    working_tree_state: str
    source_revision: str
    files_discovered: int
    graph_input_files: int
    source_bytes: int
    graph_input_hashes: dict[str, str]
    graph_input_sizes: dict[str, int]
    graph_input_fingerprints: dict[str, str]
    git_status_paths: tuple[str, ...]
    submodule_state: str


@dataclass(frozen=True, slots=True)
class GraphReceipt:
    repository: str
    commit_sha: str
    branch: str
    working_tree_state: str
    source_revision: str
    graph_schema_version: str
    graph_builder_version: str
    build_started: str
    build_completed: str
    build_status: GraphStatus
    files_discovered: int
    files_attempted: int
    files_indexed: int
    files_skipped: int
    files_failed: int
    symbols: int
    nodes_by_type: dict[str, int]
    edges_by_type: dict[str, int]
    coverage: float
    build_duration_ms: float
    persistent_graph_path: str
    graph_checksum_or_identity: str
    query_ready: bool
    degraded_reasons: tuple[str, ...]
    repository_files_discovered: int = 0
    discovery_method: str = ""
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    skipped_paths: tuple[dict[str, str], ...] = ()
    failed_paths: tuple[str, ...] = ()
    excluded_directories: tuple[dict[str, str], ...] = ()
    graph_input_hashes: dict[str, str] = field(default_factory=dict)
    graph_input_sizes: dict[str, int] = field(default_factory=dict)
    graph_input_fingerprints: dict[str, str] = field(default_factory=dict)
    git_status_paths: tuple[str, ...] = ()
    submodule_state: str = ""
    component_failures: tuple[str, ...] = ()
    parser_limitations: tuple[str, ...] = ()
    update_mode: str = ""
    parser_runtime: str = "gt-index/tree-sitter"
    graph_bytes: int = 0
    source_bytes: int = 0
    generation_id: str = ""
    build_attempt_id: str = ""
    manifest_sha256: str = ""
    receipt_schema: str = GRAPH_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if self.query_ready != (self.build_status.value in _READY):
            raise ValueError("query_ready must exactly match a READY graph status")
        if self.query_ready and (
            not self.commit_sha
            or not self.source_revision
            or not self.persistent_graph_path
            or not self.graph_checksum_or_identity
            or not self.generation_id
            or not self.manifest_sha256
        ):
            raise ValueError("a READY receipt requires repository and graph identities")
        if not 0.0 <= float(self.coverage) <= 1.0:
            raise ValueError("coverage must be between zero and one")

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["build_status"] = self.build_status.value
        value["degraded_reasons"] = list(self.degraded_reasons)
        value["skipped_paths"] = list(self.skipped_paths)
        value["failed_paths"] = list(self.failed_paths)
        value["excluded_directories"] = list(self.excluded_directories)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GraphReceipt:
        row = dict(value)
        if row.get("receipt_schema", GRAPH_RECEIPT_SCHEMA) != GRAPH_RECEIPT_SCHEMA:
            raise ValueError("unsupported graph receipt schema")
        row["build_status"] = GraphStatus(str(row["build_status"]))
        row["degraded_reasons"] = tuple(str(item) for item in row.get("degraded_reasons", ()))
        row["skipped_reasons"] = {
            str(key): int(count) for key, count in dict(row.get("skipped_reasons", {})).items()
        }
        row["skipped_paths"] = tuple(
            {"path": str(item.get("path", "")), "reason": str(item.get("reason", ""))}
            for item in row.get("skipped_paths", ())
            if isinstance(item, dict)
        )
        row["failed_paths"] = tuple(str(item) for item in row.get("failed_paths", ()))
        row["excluded_directories"] = tuple(
            {"path": str(item.get("path", "")), "reason": str(item.get("reason", ""))}
            for item in row.get("excluded_directories", ())
            if isinstance(item, dict)
        )
        row["graph_input_hashes"] = {
            str(path): str(digest)
            for path, digest in dict(row.get("graph_input_hashes", {})).items()
        }
        row["graph_input_sizes"] = {
            str(path): int(size) for path, size in dict(row.get("graph_input_sizes", {})).items()
        }
        row["graph_input_fingerprints"] = {
            str(path): str(fingerprint)
            for path, fingerprint in dict(row.get("graph_input_fingerprints", {})).items()
        }
        row["git_status_paths"] = tuple(str(path) for path in row.get("git_status_paths", ()))
        row["component_failures"] = tuple(
            str(component) for component in row.get("component_failures", ())
        )
        row["parser_limitations"] = tuple(
            str(limitation) for limitation in row.get("parser_limitations", ())
        )
        row["nodes_by_type"] = {
            str(key): int(count) for key, count in dict(row.get("nodes_by_type", {})).items()
        }
        row["edges_by_type"] = {
            str(key): int(count) for key, count in dict(row.get("edges_by_type", {})).items()
        }
        return cls(**row)


def public_graph_receipt(
    receipt: GraphReceipt, *, receipt_path: str | Path | None = None
) -> dict[str, Any]:
    """Return the bounded receipt shared by CLI, MCP, and agent delivery."""

    value = receipt.as_dict()
    output = {key: value[key] for key in PUBLIC_GRAPH_RECEIPT_FIELDS}
    if receipt_path is not None:
        output["receipt_path"] = str(receipt_path)
    return output


@dataclass(frozen=True, slots=True)
class _GraphBuildStats:
    schema: str
    symbols: int
    nodes: dict[str, int]
    edges: dict[str, int]
    files_attempted: int
    files_parsed: int
    file_hashes: int
    parse_failures: int
    file_hash_failures: int
    files_discovered: int
    skipped_count: int
    discovery_method: str
    skipped_reasons: dict[str, int]
    skipped_paths: tuple[dict[str, str], ...]
    parse_failure_details: tuple[str, ...]
    file_hash_failure_details: tuple[str, ...]
    excluded_directories: tuple[dict[str, str], ...]
    receipt_complete: bool
    component_failures: tuple[str, ...] = ()
    parser_limitations: tuple[str, ...] = ()


def _run_git(root: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return int(result.returncode), result.stdout.strip()


def _repository_root(root: str | os.PathLike[str]) -> Path:
    candidate = Path(root).resolve()
    code, top = _run_git(candidate, "rev-parse", "--show-toplevel")
    return Path(top).resolve() if code == 0 and top else candidate


def _iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.is_file() or path.is_symlink():
                yield path


def _repository_paths(root: Path) -> tuple[str, ...]:
    """Mirror the indexer's tracked plus non-ignored repository discovery set."""

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0:
        discovered: set[str] = set()
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            relative = os.fsdecode(raw).replace("\\", "/")
            parts = relative.split("/")
            if any(part in _SKIP_DIRS for part in parts[:-1]):
                continue
            discovered.add(relative)
        return tuple(sorted(discovered))
    return tuple(path.relative_to(root).as_posix() for path in _iter_files(root))


@dataclass(frozen=True, slots=True)
class _GitSnapshot:
    commit_sha: str
    branch: str
    working_tree_state: str
    changed_paths: tuple[str, ...]


def _git_snapshot(repository: Path) -> _GitSnapshot | None:
    code, output = _run_git(
        repository,
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if code != 0:
        return None
    commit_sha = "NO_COMMIT"
    branch = "DETACHED"
    changed: list[str] = []
    records = output.split("\0") if output else []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# branch.oid "):
            commit_sha = record.removeprefix("# branch.oid ").strip()
            if commit_sha == "(initial)":
                commit_sha = "NO_COMMIT"
            continue
        if record.startswith("# branch.head "):
            value = record.removeprefix("# branch.head ").strip()
            branch = "DETACHED" if value == "(detached)" else value
            continue
        path = ""
        if record.startswith("1 "):
            fields = record.split(" ", 8)
            path = fields[8] if len(fields) == 9 else ""
        elif record.startswith("2 "):
            fields = record.split(" ", 9)
            path = fields[9] if len(fields) == 10 else ""
            if index < len(records):
                original = records[index]
                index += 1
                if original:
                    changed.append(original.replace("\\", "/"))
        elif record.startswith("u "):
            fields = record.split(" ", 10)
            path = fields[10] if len(fields) == 11 else ""
        elif record.startswith("? "):
            path = record[2:]
        if path:
            changed.append(path.replace("\\", "/"))
    return _GitSnapshot(
        commit_sha=commit_sha,
        branch=branch,
        working_tree_state="dirty" if changed else "clean",
        changed_paths=tuple(dict.fromkeys(changed)),
    )


def _special_index_paths(repository: Path) -> tuple[str, ...] | None:
    """Return tracked paths Git is allowed to omit from ordinary status output.

    Lowercase ``git ls-files -v`` tags denote assume-unchanged entries and ``S``
    denotes skip-worktree. Their content is hashed on every readiness check so
    these performance hints cannot conceal a repository mutation from GT.
    """

    code, output = _run_git(repository, "ls-files", "-v", "-z")
    if code != 0:
        return None
    paths: list[str] = []
    for record in output.split("\0") if output else ():
        if len(record) < 3 or record[1] != " ":
            continue
        marker = record[0]
        if marker == "S" or marker.islower():
            paths.append(record[2:].replace("\\", "/"))
    return tuple(dict.fromkeys(paths))


def _graph_input_payload(path: Path) -> bytes:
    try:
        if path.is_symlink():
            return ("SYMLINK\0" + os.readlink(path)).encode("utf-8", "surrogatepass")
        return path.read_bytes()
    except OSError as exc:
        return f"UNREADABLE\0{type(exc).__name__}".encode()


def _graph_input_prefix(path: Path) -> bytes:
    try:
        if path.is_symlink():
            return ("SYMLINK\0" + os.readlink(path)).encode("utf-8", "surrogatepass")
        with path.open("rb") as handle:
            return handle.read(65_536)
    except OSError as exc:
        return f"UNREADABLE\0{type(exc).__name__}".encode()


def _graph_input_fingerprint(path: Path) -> str:
    """Return a cheap mutation detector; content hashes remain authoritative."""

    try:
        stat = path.lstat()
        target = os.readlink(path) if path.is_symlink() else ""
        return ":".join(
            (
                str(int(stat.st_mode)),
                str(int(stat.st_size)),
                str(int(stat.st_mtime_ns)),
                str(int(stat.st_ctime_ns)),
                target,
            )
        )
    except OSError as exc:
        return f"UNREADABLE:{type(exc).__name__}"


def _source_revision(
    commit_sha: str, submodule_state: str, graph_input_hashes: dict[str, str]
) -> str:
    digest = hashlib.sha256()
    for value in (commit_sha, submodule_state):
        encoded = value.encode("utf-8", "surrogatepass")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    for relative, content_sha256 in sorted(graph_input_hashes.items()):
        name = relative.encode("utf-8", "surrogatepass")
        content_digest = bytes.fromhex(content_sha256)
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content_digest).to_bytes(8, "big"))
        digest.update(content_digest)
    return digest.hexdigest()


def compute_repository_identity(
    root: str | os.PathLike[str], *, canonical_root: bool = False
) -> RepositoryIdentity:
    """Hash the actual graph inputs, including modified and untracked files."""

    repository = Path(root).resolve() if canonical_root else _repository_root(root)
    snapshot = _git_snapshot(repository)
    commit_sha = snapshot.commit_sha if snapshot is not None else "NO_COMMIT"
    branch_name = snapshot.branch if snapshot is not None else "DETACHED"
    working_tree_state = snapshot.working_tree_state if snapshot is not None else "not_git"
    git_status_paths = snapshot.changed_paths if snapshot is not None else ()

    files_discovered = 0
    source_bytes = 0
    graph_input_hashes: dict[str, str] = {}
    graph_input_sizes: dict[str, int] = {}
    graph_input_fingerprints: dict[str, str] = {}
    repository_paths = _repository_paths(repository)
    for relative in repository_paths:
        files_discovered += 1
        path = repository / Path(relative)
        if not (path.is_file() or path.is_symlink()):
            continue
        prefix = _graph_input_prefix(path)
        if relative == ".gitmodules" or is_graph_input(relative, prefix):
            payload = _graph_input_payload(path)
            graph_input_hashes[relative] = hashlib.sha256(payload).hexdigest()
            graph_input_sizes[relative] = len(payload)
            graph_input_fingerprints[relative] = _graph_input_fingerprint(path)
            source_bytes += len(payload)

    submodules = ""
    if (repository / ".gitmodules").is_file():
        _, submodules = _run_git(repository, "submodule", "status", "--recursive")
    return RepositoryIdentity(
        repository=str(repository),
        commit_sha=commit_sha,
        branch=branch_name,
        working_tree_state=working_tree_state,
        source_revision=_source_revision(commit_sha, submodules, graph_input_hashes),
        files_discovered=files_discovered,
        graph_input_files=len(graph_input_hashes),
        source_bytes=source_bytes,
        graph_input_hashes=graph_input_hashes,
        graph_input_sizes=graph_input_sizes,
        graph_input_fingerprints=graph_input_fingerprints,
        git_status_paths=git_status_paths,
        submodule_state=submodules,
    )


def _inventory_repository_identity(root: Path, stored: GraphReceipt) -> RepositoryIdentity:
    """Recompute identity from Git deltas while preserving hidden-path safety."""

    # Submodule worktrees require recursively hashing their files. Keep this path
    # conservative until a submodule-aware incremental inventory is implemented.
    if (root / ".gitmodules").is_file() or stored.submodule_state:
        return compute_repository_identity(root, canonical_root=True)
    snapshot = _git_snapshot(root)
    if snapshot is None:
        return compute_repository_identity(root, canonical_root=True)
    special_paths = _special_index_paths(root)
    if special_paths is None or not stored.graph_input_hashes:
        return compute_repository_identity(root, canonical_root=True)
    hashes = dict(stored.graph_input_hashes)
    sizes = dict(stored.graph_input_sizes)
    fingerprints = dict(stored.graph_input_fingerprints)
    forced = set(stored.git_status_paths) | set(snapshot.changed_paths) | set(special_paths)
    for relative in sorted(forced):
        candidate = root / Path(relative)
        if not (candidate.is_file() or candidate.is_symlink()):
            hashes.pop(relative, None)
            sizes.pop(relative, None)
            fingerprints.pop(relative, None)
            continue
        known_input = relative in hashes
        if not known_input:
            prefix = _graph_input_prefix(candidate)
            if relative != ".gitmodules" and not is_graph_input(relative, prefix):
                continue
        fingerprint = _graph_input_fingerprint(candidate)
        fingerprints[relative] = fingerprint
        payload = _graph_input_payload(candidate)
        hashes[relative] = hashlib.sha256(payload).hexdigest()
        sizes[relative] = len(payload)
    return RepositoryIdentity(
        repository=str(root),
        commit_sha=snapshot.commit_sha,
        branch=snapshot.branch,
        working_tree_state=snapshot.working_tree_state,
        source_revision=_source_revision(snapshot.commit_sha, "", hashes),
        files_discovered=stored.repository_files_discovered or stored.files_discovered,
        graph_input_files=len(hashes),
        source_bytes=sum(sizes.values()),
        graph_input_hashes=hashes,
        graph_input_sizes=sizes,
        graph_input_fingerprints=fingerprints,
        git_status_paths=snapshot.changed_paths,
        submodule_state="",
    )


def _lock_file(handle: Any, *, blocking: bool) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(handle.fileno(), mode, 1)
        return
    import fcntl

    mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    fcntl.flock(handle.fileno(), mode)


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        _lock_file(handle, blocking=True)
        try:
            yield
        finally:
            _unlock_file(handle)


def _file_lock_held(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        try:
            _lock_file(handle, blocking=False)
        except OSError:
            return True
        _unlock_file(handle)
        return False


class RepositoryGraphService:
    """Build, reopen, certify, and query the one canonical graph database."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        state_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.root = _repository_root(root)
        self.state_dir = (
            Path(state_dir).resolve() if state_dir is not None else self.root / ".groundtruth"
        )
        self._legacy_graph_path = self.state_dir / "graph.db"
        self._legacy_receipt_path = self.state_dir / "graph-receipt.json"
        self.generations_dir = self.state_dir / "generations"
        self.current_path = self.state_dir / "CURRENT"
        self.build_attempt_path = self.state_dir / "build-attempt.json"
        self.lifecycle_lock_path = self.state_dir / "graph-build.lock"
        self._verified_graph_fingerprint: tuple[str, int, int, str] | None = None

    def _current_generation_dir(self) -> Path | None:
        try:
            generation = self.current_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", generation):
            return None
        candidate = self.generations_dir / generation
        try:
            candidate.resolve().relative_to(self.generations_dir.resolve())
        except (OSError, ValueError):
            return None
        return candidate if candidate.is_dir() else None

    @property
    def graph_path(self) -> Path:
        generation = self._current_generation_dir()
        return generation / "graph.db" if generation is not None else self._legacy_graph_path

    @property
    def receipt_path(self) -> Path:
        generation = self._current_generation_dir()
        return (
            generation / "graph-receipt.json"
            if generation is not None
            else self._legacy_receipt_path
        )

    @staticmethod
    def file_sha256(path: str | os.PathLike[str]) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _write_receipt(self, receipt: GraphReceipt, *, path: Path | None = None) -> None:
        destination = path or self.receipt_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(receipt.as_dict(), ensure_ascii=False, sort_keys=True, indent=2).encode(
                "utf-8"
            )
            + b"\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=".graph-receipt.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)

    def _write_build_attempt(self, receipt: GraphReceipt) -> None:
        self._write_receipt(receipt, path=self.build_attempt_path)

    def _publish_generation(self, receipt: GraphReceipt, candidate_graph: Path) -> GraphReceipt:
        manifest = candidate_graph.with_suffix(".manifest.json")
        if not manifest.is_file():
            raise OSError("graph_manifest_missing")
        generation_dir = self.generations_dir / receipt.generation_id
        self.generations_dir.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".generation-", dir=self.generations_dir))
        try:
            shutil.copy2(candidate_graph, temporary / "graph.db")
            shutil.copy2(manifest, temporary / "graph.manifest.json")
            published = replace(
                receipt,
                persistent_graph_path=str(generation_dir / "graph.db"),
            )
            self._write_receipt(published, path=temporary / "graph-receipt.json")
            for child in temporary.iterdir():
                with open(child, "r+b") as handle:
                    os.fsync(handle.fileno())
            if generation_dir.exists():
                shutil.rmtree(temporary)
            else:
                os.replace(temporary, generation_dir)
            pointer = receipt.generation_id.encode("ascii") + b"\n"
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.state_dir, prefix=".CURRENT.", delete=False
            ) as handle:
                pointer_path = Path(handle.name)
                handle.write(pointer)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pointer_path, self.current_path)
            self._verified_graph_fingerprint = None
            return published
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)

    def _empty_receipt(
        self,
        status: GraphStatus,
        identity: RepositoryIdentity,
        *reasons: str,
    ) -> GraphReceipt:
        return GraphReceipt(
            repository=identity.repository,
            commit_sha=identity.commit_sha,
            branch=identity.branch,
            working_tree_state=identity.working_tree_state,
            source_revision=identity.source_revision,
            graph_schema_version="",
            graph_builder_version=GRAPH_BUILDER_VERSION,
            build_started="",
            build_completed="",
            build_status=status,
            files_discovered=identity.files_discovered,
            files_attempted=0,
            files_indexed=0,
            files_skipped=0,
            files_failed=0,
            symbols=0,
            nodes_by_type={},
            edges_by_type={},
            coverage=0.0,
            build_duration_ms=0.0,
            persistent_graph_path=str(self.graph_path) if self.graph_path.exists() else "",
            graph_checksum_or_identity="",
            query_ready=False,
            degraded_reasons=tuple(dict.fromkeys(reasons)),
            repository_files_discovered=identity.files_discovered,
            graph_input_hashes=identity.graph_input_hashes,
            graph_input_sizes=identity.graph_input_sizes,
            graph_input_fingerprints=identity.graph_input_fingerprints,
            git_status_paths=identity.git_status_paths,
            submodule_state=identity.submodule_state,
            source_bytes=identity.source_bytes,
        )

    def status(self) -> GraphReceipt:
        if self.build_attempt_path.is_file():
            if _file_lock_held(self.lifecycle_lock_path):
                current = compute_repository_identity(self.root, canonical_root=True)
                return self._empty_receipt(GraphStatus.BUILDING, current, "graph_build_in_progress")
            # A durable attempt with no lock owner is an interrupted writer.
            # The immutable CURRENT pointer still names either the complete
            # prior generation or no generation at all; terminalize the stale
            # attempt before evaluating that state.
            self.build_attempt_path.unlink(missing_ok=True)
        if not self.receipt_path.is_file():
            current = compute_repository_identity(
                self.root,
                canonical_root=True,
            )
            state = GraphStatus.FAILED if self.graph_path.exists() else GraphStatus.ABSENT
            reason = "graph_receipt_missing" if self.graph_path.exists() else "graph_not_built"
            return self._empty_receipt(state, current, reason)
        try:
            stored = GraphReceipt.from_dict(
                json.loads(self.receipt_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            current = compute_repository_identity(
                self.root,
                canonical_root=True,
            )
            return self._empty_receipt(GraphStatus.FAILED, current, "graph_receipt_invalid")
        current = _inventory_repository_identity(self.root, stored)

        stale: list[str] = []
        if Path(stored.repository).resolve() != self.root:
            stale.append("repository_root_mismatch")
        if stored.commit_sha != current.commit_sha:
            stale.append("commit_sha_mismatch")
        if stored.source_revision != current.source_revision:
            stale.append("source_revision_mismatch")
        if stored.graph_builder_version != GRAPH_BUILDER_VERSION:
            stale.append("graph_builder_version_mismatch")
        generation = self._current_generation_dir()
        if generation is not None:
            if stored.generation_id != generation.name:
                stale.append("graph_generation_mismatch")
            manifest = generation / "graph.manifest.json"
            try:
                manifest_sha256 = self.file_sha256(manifest)
            except OSError:
                manifest_sha256 = ""
            if not manifest_sha256 or manifest_sha256 != stored.manifest_sha256:
                stale.append("graph_manifest_checksum_mismatch")
        if stale:
            return replace(
                stored,
                branch=current.branch,
                working_tree_state=current.working_tree_state,
                git_status_paths=current.git_status_paths,
                build_status=GraphStatus.STALE,
                query_ready=False,
                degraded_reasons=tuple(dict.fromkeys((*stored.degraded_reasons, *stale))),
            )
        graph = Path(stored.persistent_graph_path)
        if not graph.is_file():
            return replace(
                stored,
                build_status=GraphStatus.FAILED,
                query_ready=False,
                degraded_reasons=tuple(
                    dict.fromkeys((*stored.degraded_reasons, "graph_database_missing"))
                ),
            )
        try:
            graph_stat = graph.stat()
            fingerprint = (
                str(graph.resolve()),
                int(graph_stat.st_size),
                int(graph_stat.st_mtime_ns),
                stored.graph_checksum_or_identity,
            )
        except OSError:
            fingerprint = (str(graph), -1, -1, stored.graph_checksum_or_identity)
        if fingerprint != self._verified_graph_fingerprint:
            try:
                checksum = self.file_sha256(graph)
            except OSError:
                checksum = ""
            if checksum != stored.graph_checksum_or_identity:
                return replace(
                    stored,
                    build_status=GraphStatus.FAILED,
                    query_ready=False,
                    degraded_reasons=tuple(
                        dict.fromkeys((*stored.degraded_reasons, "graph_checksum_mismatch"))
                    ),
                )
            try:
                connection = sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True)
                try:
                    quick = str(connection.execute("PRAGMA quick_check").fetchone()[0]).lower()
                    connection.execute("SELECT 1 FROM nodes LIMIT 1").fetchall()
                    connection.execute("SELECT 1 FROM edges LIMIT 1").fetchall()
                finally:
                    connection.close()
            except (sqlite3.Error, OSError):
                quick = "error"
            if quick != "ok":
                return replace(
                    stored,
                    build_status=GraphStatus.FAILED,
                    query_ready=False,
                    degraded_reasons=tuple(
                        dict.fromkeys((*stored.degraded_reasons, "graph_integrity_failed"))
                    ),
                )
            self._verified_graph_fingerprint = fingerprint
        return replace(
            stored,
            branch=current.branch,
            working_tree_state=current.working_tree_state,
            git_status_paths=current.git_status_paths,
        )

    @staticmethod
    def _graph_stats(graph: Path) -> _GraphBuildStats:
        connection = sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True)
        try:
            metadata = {
                str(key): str(value)
                for key, value in connection.execute("SELECT key,value FROM project_meta")
            }

            def integer(key: str, default: int = -1) -> int:
                try:
                    return int(metadata.get(key, default))
                except (TypeError, ValueError):
                    return default

            def json_list(key: str) -> tuple[Any, ...]:
                try:
                    value = json.loads(metadata.get(key, "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return ()
                return tuple(value) if isinstance(value, list) else ()

            nodes = {
                str(label or "UNKNOWN"): int(count)
                for label, count in connection.execute(
                    "SELECT label,COUNT(*) FROM nodes GROUP BY label ORDER BY label"
                )
            }
            edges = {
                str(kind or "UNKNOWN"): int(count)
                for kind, count in connection.execute(
                    "SELECT type,COUNT(*) FROM edges GROUP BY type ORDER BY type"
                )
            }
            try:
                skipped_reasons_value = json.loads(metadata.get("discovery_skipped_reasons", "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                skipped_reasons_value = {}
            skipped_reasons = (
                {
                    str(key): int(value)
                    for key, value in skipped_reasons_value.items()
                    if isinstance(value, int) and value >= 0
                }
                if isinstance(skipped_reasons_value, dict)
                else {}
            )

            def records(key: str) -> tuple[dict[str, str], ...]:
                return tuple(
                    {"path": str(item.get("path", "")), "reason": str(item.get("reason", ""))}
                    for item in json_list(key)
                    if isinstance(item, dict)
                )

            required = {
                "files_parsed",
                "parse_failure_details",
                "discovery_method",
                "discovery_files_seen",
                "discovery_skipped_count",
                "discovery_skipped_reasons",
                "discovery_skipped_paths",
                "discovery_skipped_directories",
                "file_hash_failures",
                "file_hash_failure_details",
                "component_failures",
                "parser_limitation_details",
            }
            file_nodes = sum(count for label, count in nodes.items() if label.casefold() == "file")
            return _GraphBuildStats(
                schema=metadata.get("schema_version", ""),
                symbols=max(0, sum(nodes.values()) - file_nodes),
                nodes=nodes,
                edges=edges,
                files_attempted=integer("file_count"),
                files_parsed=integer("files_parsed"),
                file_hashes=int(
                    connection.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0]
                ),
                parse_failures=integer("parse_failures"),
                file_hash_failures=integer("file_hash_failures"),
                files_discovered=integer("discovery_files_seen"),
                skipped_count=integer("discovery_skipped_count"),
                discovery_method=metadata.get("discovery_method", ""),
                skipped_reasons=skipped_reasons,
                skipped_paths=records("discovery_skipped_paths"),
                parse_failure_details=tuple(
                    str(item) for item in json_list("parse_failure_details")
                ),
                file_hash_failure_details=tuple(
                    str(item) for item in json_list("file_hash_failure_details")
                ),
                excluded_directories=records("discovery_skipped_directories"),
                receipt_complete=required <= metadata.keys(),
                component_failures=tuple(str(item) for item in json_list("component_failures")),
                parser_limitations=tuple(
                    str(item) for item in json_list("parser_limitation_details")
                ),
            )
        finally:
            connection.close()

    def _finalize_graph_receipt(
        self,
        index: Any,
        before: RepositoryIdentity,
        after: RepositoryIdentity,
        started: str,
        *,
        update_mode: str,
    ) -> GraphReceipt:
        graph = Path(index.graph_db).resolve()
        stats = self._graph_stats(graph)
        reasons: list[str] = []
        attempted = max(0, stats.files_attempted)
        files_indexed = max(0, stats.files_parsed)
        coverage = min(1.0, files_indexed / attempted) if attempted else 0.0
        critical_skip_reasons = {"content_read_failed", "metadata_access_failed"}
        critical_skipped = tuple(
            item for item in stats.skipped_paths if item.get("reason") in critical_skip_reasons
        )
        failed_paths = tuple(
            dict.fromkeys(
                (
                    *stats.parse_failure_details,
                    *stats.file_hash_failure_details,
                    *(
                        f"{item.get('path', '')}: {item.get('reason', '')}"
                        for item in critical_skipped
                    ),
                )
            )
        )
        receipt_consistency_errors: list[str] = []
        if attempted + stats.skipped_count != stats.files_discovered:
            receipt_consistency_errors.append("discovery_accounting_mismatch")
        if sum(stats.skipped_reasons.values()) != stats.skipped_count:
            receipt_consistency_errors.append("skip_reason_accounting_mismatch")
        if files_indexed + stats.parse_failures != attempted:
            receipt_consistency_errors.append("parse_accounting_mismatch")
        if stats.file_hashes + stats.file_hash_failures != attempted:
            receipt_consistency_errors.append("file_hash_accounting_mismatch")
        if len(stats.parse_failure_details) != stats.parse_failures:
            receipt_consistency_errors.append("parse_failure_detail_mismatch")
        if len(stats.file_hash_failure_details) != stats.file_hash_failures:
            receipt_consistency_errors.append("file_hash_failure_detail_mismatch")
        if before.source_revision != after.source_revision or before.commit_sha != after.commit_sha:
            status = GraphStatus.STALE
            reasons.append("repository_changed_during_build")
        elif not index.schema_valid:
            status = GraphStatus.FAILED
            reasons.append("graph_schema_invalid")
        elif not stats.receipt_complete:
            status = GraphStatus.FAILED
            reasons.append("graph_discovery_receipt_missing")
        elif receipt_consistency_errors:
            status = GraphStatus.DEGRADED
            reasons.extend(receipt_consistency_errors)
        elif stats.component_failures:
            status = GraphStatus.DEGRADED
            reasons.extend(
                f"graph_component_failed:{component}" for component in stats.component_failures
            )
        elif stats.symbols <= 0 or files_indexed <= 0:
            status = GraphStatus.DEGRADED
            reasons.append("suspiciously_empty_graph")
        elif coverage < 0.95:
            status = GraphStatus.DEGRADED
            reasons.append("indexed_file_coverage_below_95_percent")
        elif critical_skipped or stats.file_hash_failures:
            status = GraphStatus.DEGRADED
            if critical_skipped:
                reasons.append(f"source_access_failures:{len(critical_skipped)}")
            if stats.file_hash_failures:
                reasons.append(f"file_hash_failures:{stats.file_hash_failures}")
        elif attempted >= 20 and not stats.edges:
            status = GraphStatus.DEGRADED
            reasons.append("suspicious_graph_has_no_edges")
        elif (
            stats.parse_failures
            or stats.parser_limitations
            or stats.discovery_method != "git_ls_files"
            or any(
                stats.skipped_reasons.get(reason, 0)
                for reason in (
                    "generated",
                    "language_unresolved",
                    "non_regular_file",
                    "too_large",
                )
            )
            or stats.excluded_directories
        ):
            status = GraphStatus.READY_WITH_DECLARED_LIMITATIONS
            if stats.parse_failures:
                reasons.append(f"parser_failures:{stats.parse_failures}")
            if stats.parser_limitations:
                reasons.append(f"parser_limitations:{len(stats.parser_limitations)}")
            if stats.discovery_method != "git_ls_files":
                reasons.append(f"discovery_method:{stats.discovery_method or 'unknown'}")
            for reason in (
                "generated",
                "language_unresolved",
                "non_regular_file",
                "too_large",
            ):
                if count := stats.skipped_reasons.get(reason, 0):
                    reasons.append(f"{reason}:{count}")
            if stats.excluded_directories:
                reasons.append(f"excluded_directory_files:{len(stats.excluded_directories)}")
        else:
            status = GraphStatus.READY
        manifest_path = graph.with_suffix(".manifest.json")
        manifest_sha256 = str(getattr(index, "graph_manifest_sha256", "") or "")
        if not manifest_sha256 and manifest_path.is_file():
            manifest_sha256 = self.file_sha256(manifest_path)
        result = GraphReceipt(
            repository=after.repository,
            commit_sha=after.commit_sha,
            branch=after.branch,
            working_tree_state=after.working_tree_state,
            source_revision=before.source_revision,
            graph_schema_version=stats.schema,
            graph_builder_version=GRAPH_BUILDER_VERSION,
            build_started=started,
            build_completed=self._now(),
            build_status=status,
            files_discovered=max(0, stats.files_discovered),
            files_attempted=attempted,
            files_indexed=files_indexed,
            files_skipped=max(0, stats.skipped_count),
            files_failed=len(failed_paths),
            symbols=stats.symbols,
            nodes_by_type=stats.nodes,
            edges_by_type=stats.edges,
            coverage=coverage,
            build_duration_ms=index.elapsed_ms,
            persistent_graph_path=str(graph),
            graph_checksum_or_identity=index.graph_db_sha256 or self.file_sha256(graph),
            query_ready=status
            in {
                GraphStatus.READY,
                GraphStatus.READY_WITH_DECLARED_LIMITATIONS,
            },
            degraded_reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
            repository_files_discovered=after.files_discovered,
            discovery_method=stats.discovery_method,
            skipped_reasons=stats.skipped_reasons,
            skipped_paths=stats.skipped_paths,
            failed_paths=failed_paths,
            excluded_directories=stats.excluded_directories,
            graph_input_hashes=after.graph_input_hashes,
            graph_input_sizes=after.graph_input_sizes,
            graph_input_fingerprints=after.graph_input_fingerprints,
            git_status_paths=after.git_status_paths,
            submodule_state=after.submodule_state,
            component_failures=stats.component_failures,
            parser_limitations=stats.parser_limitations,
            update_mode=update_mode,
            graph_bytes=graph.stat().st_size,
            source_bytes=after.source_bytes,
            generation_id=hashlib.sha256(
                "\0".join(
                    (
                        before.source_revision,
                        index.graph_db_sha256 or self.file_sha256(graph),
                        stats.schema,
                        GRAPH_BUILDER_VERSION,
                        manifest_sha256,
                    )
                ).encode("utf-8")
            ).hexdigest(),
            build_attempt_id=hashlib.sha256(
                f"{before.source_revision}\0{started}".encode()
            ).hexdigest()[:24],
            manifest_sha256=manifest_sha256,
        )
        return result

    def build(
        self,
        *,
        force: bool = False,
        timeout: float = 600.0,
        _update_mode: str = "full",
    ) -> GraphReceipt:
        current = self.status()
        if not force and current.query_ready:
            return current
        if not force and current.build_status is GraphStatus.STALE:
            return self.update(timeout=timeout)
        with _exclusive_file_lock(self.lifecycle_lock_path):
            # Another writer may have completed while this process waited.
            current = self.status()
            if not force and current.query_ready:
                return current
            before = compute_repository_identity(self.root)
            started = self._now()
            attempt = replace(
                self._empty_receipt(GraphStatus.BUILDING, before, "build_in_progress"),
                build_started=started,
                build_attempt_id=hashlib.sha256(
                    f"{before.source_revision}\0{started}".encode()
                ).hexdigest()[:24],
            )
            self._write_build_attempt(attempt)
            try:
                index = ensure_index_with_receipt(
                    self.root,
                    state_dir=self.state_dir,
                    source_revision=before.source_revision,
                    timeout=timeout,
                    exact_state_dir=True,
                )
                after = compute_repository_identity(self.root)
                if not index.graph_db:
                    result = replace(
                        self._empty_receipt(
                            GraphStatus.FAILED,
                            after,
                            index.status.value,
                            index.error_type or "graph_build_failed",
                            index.error_diagnostic,
                        ),
                        build_started=started,
                        build_completed=self._now(),
                        build_duration_ms=index.elapsed_ms,
                        files_attempted=index.indexable_files,
                        files_failed=index.parser_failures,
                        update_mode=_update_mode,
                        build_attempt_id=attempt.build_attempt_id,
                    )
                    self._write_receipt(result, path=self._legacy_receipt_path)
                    return result
                result = self._finalize_graph_receipt(
                    index, before, after, started, update_mode=_update_mode
                )
                if not result.query_ready:
                    self._write_receipt(result, path=self._legacy_receipt_path)
                    return result
                return self._publish_generation(result, Path(index.graph_db).resolve())
            finally:
                self.build_attempt_path.unlink(missing_ok=True)

    def update(self, *, timeout: float = 120.0) -> GraphReceipt:
        """Converge a stale graph without exposing partial relationship updates.

        The vendored file-keyed index path refreshes calls, imports, containment,
        properties, and incoming edges, but it does not yet rerun every whole-repo
        relationship pass. Until full-vs-incremental parity is independently proven,
        the canonical product takes the correct full-rebuild path for every mutation.
        """

        observed = self.status()
        if observed.query_ready:
            return observed
        if observed.build_status is not GraphStatus.STALE:
            return self.build(force=True, timeout=timeout, _update_mode="full_fallback")
        return self.build(
            force=True,
            timeout=timeout,
            _update_mode="full_fallback_unproven_incremental_parity",
        )

    def _ready_graph(self) -> tuple[GraphReceipt, Path]:
        receipt = self.status()
        if not receipt.query_ready:
            reasons = ",".join(receipt.degraded_reasons) or receipt.build_status.value
            raise GraphNotReadyError(f"graph is not query-ready: {reasons}")
        return receipt, Path(receipt.persistent_graph_path)

    def query(
        self,
        mode: str,
        symbol: str,
        *,
        limit: int = 50,
        file_path: str | None = None,
        min_confidence: float = 0.5,
    ) -> dict[str, Any]:
        receipt, graph = self._ready_graph()
        requested = str(mode or "").strip().lower()
        normalized = QUERY_MODE_ALIASES.get(requested, requested)
        if normalized not in CANONICAL_QUERY_MODES:
            choices = ", ".join(CANONICAL_QUERY_MODES)
            raise ValueError(f"unsupported query mode: {mode}; supported modes: {choices}")
        bound = max(1, min(int(limit), 200))
        token = str(symbol or "").strip()
        selected_file = str(file_path or "").strip().replace("\\", "/")
        confidence_floor = max(0.0, min(float(min_confidence), 1.0))
        connection = sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        resolution_status = "READY"
        resolved_symbol: dict[str, Any] | None = None
        ambiguous_candidates: list[dict[str, Any]] = []
        try:
            node_projection = (
                "n.id,n.label,n.name,n.qualified_name,n.file_path,n.start_line,n.end_line,"
                "n.signature,n.language,n.is_test"
            )
            if normalized == "definition":
                file_clause = "AND n.file_path=?" if selected_file else ""
                parameters: tuple[Any, ...] = (token, token, selected_file, token, bound)
                if not selected_file:
                    parameters = (token, token, token, bound)
                rows = connection.execute(
                    f"SELECT {node_projection} FROM nodes n "
                    f"WHERE (n.name=? OR n.qualified_name=?) {file_clause} "
                    "ORDER BY CASE WHEN n.name=? THEN 0 ELSE 1 END,n.file_path,n.start_line "
                    "LIMIT ?",
                    parameters,
                ).fetchall()
            elif normalized == "search":
                file_clause = "AND n.file_path=?" if selected_file else ""
                parameters = (
                    token,
                    token,
                    f"%{token}%",
                    f"%{token}%",
                    selected_file,
                    token,
                    token,
                    bound,
                )
                if not selected_file:
                    parameters = (
                        token,
                        token,
                        f"%{token}%",
                        f"%{token}%",
                        token,
                        token,
                        bound,
                    )
                rows = connection.execute(
                    f"SELECT {node_projection} FROM nodes n "
                    "WHERE (n.name=? OR n.qualified_name=? OR n.name LIKE ? "
                    f"OR n.qualified_name LIKE ?) {file_clause} "
                    "ORDER BY CASE WHEN n.name=? THEN 0 WHEN n.qualified_name=? THEN 1 ELSE 2 END,"
                    "n.file_path,n.start_line LIMIT ?",
                    parameters,
                ).fetchall()
            else:
                anchor_conditions = ["(n.name=? OR n.qualified_name=?)"]
                anchor_values: list[Any] = [token, token]
                if selected_file:
                    anchor_conditions.append("n.file_path=?")
                    anchor_values.append(selected_file)
                if normalized in {"implementations", "subclasses"}:
                    placeholders = ",".join("?" for _ in _TYPE_ANCHOR_LABELS)
                    anchor_conditions.append(f"LOWER(n.label) IN ({placeholders})")
                    anchor_values.extend(_TYPE_ANCHOR_LABELS)
                anchor_rows = connection.execute(
                    f"SELECT {node_projection} FROM nodes n "
                    f"WHERE {' AND '.join(anchor_conditions)} "
                    "ORDER BY n.file_path,n.start_line",
                    tuple(anchor_values),
                ).fetchall()
                exact_qualified = [row for row in anchor_rows if row["qualified_name"] == token]
                anchors = exact_qualified if exact_qualified else list(anchor_rows)
                if not anchors:
                    resolution_status = "NOT_FOUND"
                    rows = []
                elif len(anchors) > 1:
                    resolution_status = "AMBIGUOUS"
                    ambiguous_candidates = [dict(row) for row in anchors[:bound]]
                    rows = []
                else:
                    resolved_symbol = dict(anchors[0])
                    anchor_id = int(anchors[0]["id"])
                    reverse = normalized in {
                        "callers",
                        "importers",
                        "exporters",
                        "implementations",
                        "subclasses",
                        "references",
                        "impact",
                        "tests",
                    }
                    edge_types = {
                        "callers": ("CALLS",),
                        "callees": ("CALLS",),
                        "imports": ("IMPORTS", "IMPORTS_FROM"),
                        "importers": ("IMPORTS", "IMPORTS_FROM"),
                        "reexports": ("RE_EXPORTS",),
                        "exporters": ("RE_EXPORTS",),
                        "implementations": ("IMPLEMENTS",),
                        "subclasses": ("EXTENDS", "INHERITS"),
                        "references": ("REFERENCES", "CALLS", "IMPORTS", "IMPORTS_FROM"),
                        "impact": (
                            "CALLS",
                            "REFERENCES",
                            "IMPORTS",
                            "IMPORTS_FROM",
                            "IMPLEMENTS",
                            "EXTENDS",
                            "INHERITS",
                        ),
                        "tests": ("CALLS", "REFERENCES", "TESTS"),
                    }[normalized]
                    placeholders = ",".join("?" for _ in edge_types)
                    test_clause = "AND n.is_test=1" if normalized == "tests" else ""
                    edge_column = "target_id" if reverse else "source_id"
                    node_column = "source_id" if reverse else "target_id"
                    rows = connection.execute(
                        f"SELECT DISTINCT {node_projection},e.type AS relationship,"
                        "e.source_file,e.source_line,"
                        "e.confidence,e.resolution_method,e.verification_status "
                        f"FROM edges e JOIN nodes n ON n.id=e.{node_column} "
                        f"WHERE e.{edge_column}=? AND e.type IN ({placeholders}) "
                        f"AND e.confidence>=? {test_clause} "
                        "ORDER BY e.confidence DESC,n.file_path,n.start_line LIMIT ?",
                        (anchor_id, *edge_types, confidence_floor, bound),
                    ).fetchall()
        finally:
            connection.close()
        if normalized in {"definition", "search"}:
            resolution_status = "READY" if rows else "NOT_FOUND"
        return {
            "schema": "gt.graph_query.v1",
            "status": resolution_status,
            "mode": normalized,
            "symbol": token,
            "file_path": selected_file,
            "min_confidence": confidence_floor,
            "repository": receipt.repository,
            "commit_sha": receipt.commit_sha,
            "source_revision": receipt.source_revision,
            "graph_identity": receipt.graph_checksum_or_identity,
            "build_status": receipt.build_status.value,
            "evidence": [dict(row) for row in rows],
            "count": len(rows),
            "resolved_symbol": resolved_symbol,
            "ambiguous_candidates": ambiguous_candidates,
            "degraded_reasons": list(receipt.degraded_reasons),
        }


__all__ = [
    "CANONICAL_QUERY_MODES",
    "GraphNotReadyError",
    "GraphReceipt",
    "GraphStatus",
    "PUBLIC_GRAPH_RECEIPT_FIELDS",
    "RepositoryGraphService",
    "RepositoryIdentity",
    "SUPPORTED_QUERY_MODES",
    "compute_repository_identity",
    "public_graph_receipt",
]
