"""Bounded source-only repository transfer planning.

The provider-facing agent runs inside a task container, while repository
indexing runs on the Harbor host.  Mirroring the whole workspace makes graph
availability depend on unrelated checkpoints, binaries, caches, and outputs.
This module selects only authored source and small project metadata from the
authoritative workspace manifest.  It never reads task data or guesses that an
unknown path is source.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from gt_engine.central_runtime import WorkspaceSnapshot
from gt_engine.language_registry import is_indexable_source

_METADATA_NAMES = frozenset(
    {
        "cargo.toml",
        "cmakelists.txt",
        "go.mod",
        "go.sum",
        "makefile",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "pytest.ini",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "tox.ini",
        "tsconfig.json",
        "yarn.lock",
    }
)
_IGNORED_PARTS = frozenset(
    {
        ".git",
        ".gt",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "venv",
    }
)
_EXTERNAL_MIRROR_ROOTS = ("/etc/nginx/", "/var/log/nginx/")


@dataclass(frozen=True, slots=True)
class SourceMirrorPlan:
    paths: tuple[str, ...]
    total_bytes: int
    source_files: int
    metadata_files: int
    excluded_artifacts: int
    excluded_deliverables: int
    excluded_oversize: int
    excluded_source_oversize: int
    excluded_budget: int
    excluded_source_budget: int
    manifest_sha256: str
    complete: bool
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_relative(path: str) -> str:
    normalized = str(path or "").replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("app/"):
        normalized = normalized[4:]
    if normalized.startswith("/app/"):
        normalized = normalized[5:]
    if normalized.startswith(_EXTERNAL_MIRROR_ROOTS):
        normalized = "__external__" + normalized
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or ".." in candidate.parts
        or any(part in {"", "."} for part in candidate.parts)
        or any(ord(char) < 32 for char in normalized)
    ):
        return ""
    return candidate.as_posix()


def plan_source_mirror(
    snapshot: WorkspaceSnapshot,
    *,
    excluded_paths: set[str] | frozenset[str] = frozenset(),
    max_source_file_bytes: int = 50_000_000,
    max_metadata_file_bytes: int = 256_000,
    max_total_bytes: int = 50_000_000,
    max_files: int = 20_000,
) -> SourceMirrorPlan:
    """Select a deterministic, bounded graph substrate from one snapshot."""

    if not snapshot.healthy:
        return SourceMirrorPlan(
            paths=(),
            total_bytes=0,
            source_files=0,
            metadata_files=0,
            excluded_artifacts=0,
            excluded_deliverables=0,
            excluded_oversize=0,
            excluded_source_oversize=0,
            excluded_budget=0,
            excluded_source_budget=0,
            manifest_sha256=hashlib.sha256(b"").hexdigest(),
            complete=False,
            reason_codes=("workspace_snapshot_unhealthy",),
        )
    candidates: list[tuple[int, str, int, str]] = []
    normalized_exclusions = {
        normalized
        for raw_path in excluded_paths
        if (normalized := _safe_relative(raw_path))
    }
    excluded_artifacts = 0
    excluded_deliverables = 0
    excluded_oversize = 0
    excluded_source_oversize = 0
    for raw_path, state in snapshot.entries.items():
        path = _safe_relative(raw_path)
        if not path or state.kind != "f":
            excluded_artifacts += 1
            continue
        # A task output can also be authored code. Exclude only output-only
        # paths; code deliverables remain part of the graph substrate.
        deliverable_source = is_indexable_source(path, state.content)
        if path in normalized_exclusions and not deliverable_source:
            excluded_deliverables += 1
            continue
        parts = PurePosixPath(path).parts
        if any(part.lower() in _IGNORED_PARTS for part in parts):
            excluded_artifacts += 1
            continue
        is_metadata = PurePosixPath(path).name.lower() in _METADATA_NAMES
        is_source = is_indexable_source(path, state.content) and not is_metadata
        if not is_source and not is_metadata:
            excluded_artifacts += 1
            continue
        limit = max_source_file_bytes if is_source else max_metadata_file_bytes
        if state.size < 0 or state.size > limit:
            excluded_oversize += 1
            excluded_source_oversize += int(is_source)
            continue
        # Source precedes metadata under pressure.  Within a class, paths are
        # lexical so the same snapshot always yields the same archive.
        candidates.append(
            (
                0 if is_source else 1,
                path,
                state.size,
                "source" if is_source else "metadata",
            )
        )

    selected: list[str] = []
    total = 0
    source_files = 0
    metadata_files = 0
    excluded_budget = 0
    excluded_source_budget = 0
    for _priority, path, size, kind in sorted(candidates):
        if len(selected) >= max_files or total + size > max_total_bytes:
            excluded_budget += 1
            excluded_source_budget += int(kind == "source")
            continue
        selected.append(path)
        total += size
        if kind == "source":
            source_files += 1
        else:
            metadata_files += 1
    manifest = b"".join(
        path.encode("utf-8", "surrogateescape") + b"\0" for path in selected
    )
    reasons: list[str] = []
    if excluded_source_oversize:
        reasons.append("source_mirror_source_oversize")
    if excluded_source_budget:
        reasons.append("source_mirror_source_budget_exceeded")
    complete = not reasons
    return SourceMirrorPlan(
        paths=tuple(selected),
        total_bytes=total,
        source_files=source_files,
        metadata_files=metadata_files,
        excluded_artifacts=excluded_artifacts,
        excluded_deliverables=excluded_deliverables,
        excluded_oversize=excluded_oversize,
        excluded_source_oversize=excluded_source_oversize,
        excluded_budget=excluded_budget,
        excluded_source_budget=excluded_source_budget,
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        complete=complete,
        reason_codes=tuple(reasons),
    )


__all__ = ["SourceMirrorPlan", "plan_source_mirror"]
