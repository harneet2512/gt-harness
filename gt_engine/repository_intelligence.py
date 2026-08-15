"""Correct-or-quiet repository evidence for the host-owned central runtime.

The model must not spend its first call rediscovering structure GT can derive
deterministically.  This module turns the existing GroundTruth index and graph
projection into a small source-bound result.  It never invents a caller or a
location when the graph cannot prove one.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import tomllib
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from gt_engine.graph_context import build_graph_projection
from gt_engine.graph_evidence import build_evidence_need, rank_graph_evidence
from gt_engine.indexer import (
    IndexBuildReceipt,
    IndexBuildStatus,
    ensure_index_with_receipt,
    refresh_index_files,
)
from gt_engine.language_registry import is_indexable_source
from gt_engine.task_contract import Obligation, TaskContract, extract_task_contract


class RepositoryIntelligenceStatus(StrEnum):
    HEALTHY_CURRENT = "source_backed"
    NOT_INDEXED = "not_indexed"
    ENVIRONMENT_TRANSFER_UNAVAILABLE = "environment_transfer_unavailable"
    NO_SUPPORTED_SOURCE = "no_supported_source"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    INCOMPLETE_COVERAGE = "incomplete_source_coverage"
    INDEX_UNAVAILABLE = "index_unavailable"
    SCHEMA_INVALID = "schema_invalid"
    STALE = "stale_source_revision"
    EMPTY_RETRIEVAL = "no_task_linked_evidence"
    LOW_PRECISION = "low_precision"
    MIRROR_INCOMPLETE = "mirror_incomplete"
    SENSOR_DEGRADED = "sensor_degraded"


class RepositorySubstrateStatus(StrEnum):
    """Health of the graph substrate, independent of task retrieval quality."""

    HEALTHY_CURRENT = "healthy_current"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


class RetrievalDisposition(StrEnum):
    """Outcome of task-conditioned retrieval from a usable substrate."""

    NOT_EVALUATED = "not_evaluated"
    MATCHED = "matched"
    REPRESENTED_IN_TASK = "represented_in_task"
    EMPTY = "empty"
    LOW_PRECISION = "low_precision"
    STALE = "stale"


class RepositoryApplicability(StrEnum):
    """Whether repository intelligence is applicable to this task."""

    SOURCE_BACKED = "source_backed"
    NOT_APPLICABLE_NO_SUPPORTED_SOURCE = "not_applicable_no_supported_source"
    SUBSTRATE_FAILURE = "substrate_failure"


@dataclass(frozen=True, slots=True)
class RepositoryEvidence:
    # Compatibility: ``available`` means task-linked structural evidence is
    # available.  It must not be used as a proxy for graph substrate health.
    available: bool = False
    graph_revision: str = ""
    anchors: tuple[dict[str, Any], ...] = ()
    definitions: tuple[dict[str, Any], ...] = ()
    references: tuple[dict[str, Any], ...] = ()
    callers: tuple[dict[str, Any], ...] = ()
    project_checks: tuple[str, ...] = ()
    status: str = "unavailable"
    index: IndexBuildReceipt | None = None
    source_revision: str = ""
    index_current: bool = False
    intelligence_valid: bool = False
    substrate_ready: bool = False
    substrate_status: str = RepositorySubstrateStatus.UNAVAILABLE.value
    retrieval_disposition: str = RetrievalDisposition.NOT_EVALUATED.value

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def graph_gate_failures(evidence: RepositoryEvidence) -> tuple[str, ...]:
    """Return analytical treatment failures for the graph substrate.

    This is intentionally a substrate gate, not a retrieval-quality oracle.
    The host records these reasons and runs Mini-SWE without uncertified graph
    payloads; promotion fails closed later.  Healthy empty retrieval is not a
    substrate failure.
    """

    failures: list[str] = []
    index = evidence.index
    if not evidence.substrate_ready:
        failures.append(evidence.status or "repository_unavailable")
    if index is None or not index.graph_db:
        failures.append("graph_missing")
    else:
        if not index.schema_valid:
            failures.append("graph_schema_invalid")
        if index.node_count <= 0:
            failures.append("graph_empty")
        if not index.coverage_complete:
            failures.append("graph_source_coverage_incomplete")
        if not index.graph_revision:
            failures.append("graph_revision_missing")
        if not index.source_revision:
            failures.append("graph_source_revision_missing")
        elif evidence.source_revision and index.source_revision != evidence.source_revision:
            failures.append("graph_source_revision_mismatch")
    if not evidence.source_revision:
        failures.append("source_revision_missing")
    if not evidence.index_current:
        failures.append("graph_not_current")
    if not evidence.intelligence_valid:
        failures.append("repository_intelligence_invalid")
    return tuple(dict.fromkeys(failures))


def classify_repository_applicability(evidence: RepositoryEvidence) -> str:
    """Classify graph applicability without weakening source-backed gates.

    A source-less task is not a healthy graph and must never receive invented
    facts.  It is nevertheless different from a failed index for a task that
    contains supported source.
    """

    if (
        evidence.status == RepositoryIntelligenceStatus.NO_SUPPORTED_SOURCE.value
        and evidence.substrate_status == RepositorySubstrateStatus.NOT_APPLICABLE.value
    ):
        return RepositoryApplicability.NOT_APPLICABLE_NO_SUPPORTED_SOURCE.value
    if (
        evidence.status == RepositoryIntelligenceStatus.HEALTHY_CURRENT.value
        and evidence.substrate_ready
        and evidence.index_current
        and evidence.intelligence_valid
    ):
        return RepositoryApplicability.SOURCE_BACKED.value
    return RepositoryApplicability.SUBSTRATE_FAILURE.value


class RepositorySession:
    """Task-scoped host mirror whose graph is bound to a source revision.

    The initial environment transfer is performed by the host agent.  This
    object then applies only source contents captured by the authoritative
    workspace sensor.  Missing content or an unsafe path invalidates the
    mirror; it never serves a stale graph as current evidence.
    """

    def __init__(
        self,
        *,
        root: str | Path,
        state_dir: str | Path,
        instruction: str,
    ) -> None:
        self.root = Path(root).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.instruction = instruction
        self.source_revision = ""
        self.indexed_source_revision = ""
        self.fresh = False
        self.evidence = RepositoryEvidence(status=RepositoryIntelligenceStatus.NOT_INDEXED.value)
        self.refresh_log: list[dict[str, Any]] = []
        self._pending_index_paths: set[str] = set()
        self._requires_full_rebuild = False
        self._query_cache: dict[
            tuple[str, tuple[str, ...], tuple[str, ...], str, str],
            RepositoryEvidence,
        ] = {}
        self._owned_directories: tuple[TemporaryDirectory[str], ...] = ()

    @classmethod
    def temporary(cls, *, instruction: str) -> RepositorySession:
        mirror = TemporaryDirectory(prefix="gt-repository-")
        state = TemporaryDirectory(prefix="gt-state-")
        session = cls(root=mirror.name, state_dir=state.name, instruction=instruction)
        session._owned_directories = (mirror, state)
        return session

    def close(self) -> None:
        for directory in reversed(self._owned_directories):
            directory.cleanup()
        self._owned_directories = ()

    def _target(self, relative_path: str) -> Path | None:
        normalized = str(relative_path or "").replace("\\", "/")
        if normalized.startswith(("/etc/nginx/", "/var/log/nginx/")):
            normalized = "__external__" + normalized
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized or normalized.startswith("/") or ".." in Path(normalized).parts:
            return None
        target = (self.root / normalized).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return None
        return target

    def invalidate(self, *, source_revision: str, status: str) -> None:
        self.source_revision = source_revision
        self.fresh = False
        self.evidence = RepositoryEvidence(
            project_checks=self.evidence.project_checks,
            status=status,
        )

    def apply_transition(
        self,
        transition: Any,
        *,
        source_revision: str,
        changed_paths: tuple[str, ...] | None = None,
    ) -> bool:
        """Advance the mirror only when every changed source has captured text."""
        if not bool(getattr(transition, "sensor_healthy", False)):
            self.invalidate(source_revision=source_revision, status="sensor_degraded")
            return False
        deleted = set(getattr(transition, "deleted", ()) or ())
        after_contents = dict(getattr(transition, "after_contents", {}) or {})
        selected_paths = (
            tuple(changed_paths)
            if changed_paths is not None
            else tuple(getattr(transition, "changed_paths", ()) or ())
        )
        for path in selected_paths:
            target = self._target(str(path))
            if target is None:
                self.invalidate(source_revision=source_revision, status="unsafe_mirror_path")
                return False
            if path in deleted:
                # Content-signature and shebang languages have no reliable
                # path-only identity.  Resolve the file while its captured
                # bytes still exist so deletion cannot leave stale nodes in
                # an otherwise current graph.
                prior_prefix: bytes = b""
                try:
                    if target.is_file() or target.is_symlink():
                        prior_prefix = target.read_bytes()[:65_536]
                except OSError:
                    prior_prefix = b""
                if is_indexable_source(path, prior_prefix) or not os.path.splitext(
                    str(path).replace("\\", "/")
                )[1]:
                    self._requires_full_rebuild = True
                if target.is_file() or target.is_symlink():
                    target.unlink()
                continue
            content = after_contents.get(path)
            if not isinstance(content, str):
                self.invalidate(source_revision=source_revision, status="mirror_incomplete")
                return False
            prior_prefix: bytes = b""
            try:
                if target.is_file() or target.is_symlink():
                    prior_prefix = target.read_bytes()[:65_536]
            except OSError:
                prior_prefix = b""
            was_indexable = is_indexable_source(path, prior_prefix)
            is_indexable = is_indexable_source(path, content[:65_536])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            # The indexer also resolves extensionless/content-signature files
            # from bounded content.  The session must use the same evidence
            # when deciding whether to enqueue an incremental refresh.
            if was_indexable and not is_indexable:
                # The incremental binary can update a file, but it cannot
                # reliably remove a path whose language identity disappeared
                # without rebuilding closure from the current mirror.
                self._requires_full_rebuild = True
            if is_indexable:
                normalized = str(path).replace("\\", "/")
                if normalized.startswith(("/etc/nginx/", "/var/log/nginx/")):
                    normalized = "__external__" + normalized
                self._pending_index_paths.add(normalized)
        self.source_revision = source_revision
        self.fresh = False
        self._query_cache.clear()
        return True

    def refresh(self, *, source_revision: str, limit: int = 8) -> RepositoryEvidence:
        if source_revision == self.indexed_source_revision and self.evidence.intelligence_valid:
            self.refresh_log.append(
                {
                    "source_revision": source_revision,
                    "graph_revision": self.evidence.graph_revision,
                    "available": self.evidence.available,
                    "status": self.evidence.status,
                    "mode": "revision_cache_hit",
                    "elapsed_ms": 0.0,
                }
            )
            return self.evidence
        prior_index = self.evidence.index
        mode = "full"
        if prior_index is not None and prior_index.graph_db and not self._requires_full_rebuild:
            if self._pending_index_paths:
                mode = "incremental"
                index_receipt = refresh_index_files(
                    self.root,
                    prior_index.graph_db,
                    tuple(sorted(self._pending_index_paths)),
                    source_revision=source_revision,
                )
                evidence = inspect_repository(
                    self.root,
                    self.instruction,
                    state_dir=self.state_dir,
                    limit=limit,
                    index_receipt=index_receipt,
                    source_revision=source_revision,
                )
            else:
                mode = "source_revision_only"
                evidence = self.evidence
        else:
            evidence = inspect_repository(
                self.root,
                self.instruction,
                state_dir=self.state_dir,
                limit=limit,
                source_revision=source_revision,
            )
        evidence = replace(
            evidence,
            source_revision=source_revision,
            index_current=bool(evidence.substrate_ready and source_revision),
            intelligence_valid=bool(
                evidence.substrate_ready
                and evidence.status == RepositoryIntelligenceStatus.HEALTHY_CURRENT.value
                and source_revision
            ),
        )
        self.source_revision = source_revision
        self.indexed_source_revision = source_revision
        self.fresh = evidence.intelligence_valid
        self.evidence = evidence
        self._query_cache.clear()
        self._pending_index_paths.clear()
        self._requires_full_rebuild = False
        self.refresh_log.append(
            {
                "source_revision": source_revision,
                "graph_revision": evidence.graph_revision,
                "available": evidence.available,
                "status": evidence.status,
                "mode": mode,
                "elapsed_ms": (
                    float(evidence.index.elapsed_ms) if evidence.index is not None else 0.0
                ),
            }
        )
        return evidence

    def query(
        self,
        *,
        source_revision: str,
        active_paths: tuple[str, ...],
        boundary: str,
        active_symbols: tuple[str, ...] = (),
        diagnostic_fingerprint: str = "",
        limit: int = 8,
    ) -> RepositoryEvidence:
        """Re-rank the current graph for typed action paths without reindexing."""

        normalized = tuple(
            dict.fromkeys(
                str(path or "").replace("\\", "/")
                for path in active_paths
                if str(path or "").strip()
            )
        )
        normalized_symbols = tuple(
            dict.fromkeys(
                str(symbol or "").strip()
                for symbol in active_symbols
                if str(symbol or "").strip()
            )
        )
        normalized_boundary = str(boundary or "unknown")
        normalized_diagnostic = str(diagnostic_fingerprint or "").strip()
        index = self.evidence.index
        if (
            not normalized
            or source_revision != self.indexed_source_revision
            or index is None
            or not index.graph_db
            or not self.evidence.substrate_ready
        ):
            return self.evidence
        cache_key = (
            source_revision,
            normalized,
            normalized_symbols,
            normalized_boundary,
            normalized_diagnostic,
        )
        cached = self._query_cache.get(cache_key)
        if cached is not None:
            self.evidence = cached
            self.refresh_log.append(
                {
                    "source_revision": source_revision,
                    "graph_revision": cached.graph_revision,
                    "available": cached.available,
                    "status": cached.status,
                    "mode": "action_query_cache_hit",
                    "boundary": normalized_boundary,
                    "active_paths": list(normalized),
                    "active_symbols": list(normalized_symbols),
                    "diagnostic_fingerprint": normalized_diagnostic,
                    "elapsed_ms": 0.0,
                }
            )
            return cached
        started = time.monotonic()
        evidence = inspect_repository(
            self.root,
            self.instruction,
            state_dir=self.state_dir,
            limit=limit,
            index_receipt=index,
            source_revision=source_revision,
            active_paths=normalized,
            boundary=normalized_boundary,
        )
        evidence = replace(
            evidence,
            source_revision=source_revision,
            index_current=bool(evidence.substrate_ready),
            intelligence_valid=bool(
                evidence.substrate_ready
                and evidence.status == RepositoryIntelligenceStatus.HEALTHY_CURRENT.value
            ),
        )
        self.evidence = evidence
        self._query_cache[cache_key] = evidence
        self.fresh = evidence.intelligence_valid
        self.refresh_log.append(
            {
                "source_revision": source_revision,
                "graph_revision": evidence.graph_revision,
                "available": evidence.available,
                "status": evidence.status,
                "mode": "action_query",
                "boundary": normalized_boundary,
                "active_paths": list(normalized),
                "active_symbols": list(normalized_symbols),
                "diagnostic_fingerprint": normalized_diagnostic,
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 6),
            }
        )
        return evidence

    def summary(self) -> dict[str, Any]:
        return {
            "source_revision": self.source_revision,
            "indexed_source_revision": self.indexed_source_revision,
            "fresh": self.fresh,
            "evidence": self.evidence.as_dict(),
            "refresh_log": list(self.refresh_log),
        }


_PROJECT_MANIFESTS = frozenset(
    {
        "pyproject.toml",
        "pytest.ini",
        "setup.cfg",
        "tox.ini",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "makefile",
    }
)


def _project_roots(base: Path, active_paths: tuple[str, ...]) -> tuple[Path, ...]:
    if not active_paths:
        return (base,)
    roots: list[Path] = []
    for raw_path in active_paths:
        candidate = (base / str(raw_path).replace("\\", "/")).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            continue
        directory = candidate if candidate.is_dir() else candidate.parent
        while True:
            if any((directory / name).is_file() for name in _PROJECT_MANIFESTS):
                roots.append(directory)
                break
            if directory == base or directory.parent == directory:
                roots.append(base)
                break
            directory = directory.parent
    return tuple(dict.fromkeys(roots or (base,)))


def _pyproject_uses_pytest(project: Path) -> bool:
    path = project / "pyproject.toml"
    if not path.is_file():
        return False
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return False
    tool = data.get("tool") if isinstance(data, dict) else None
    if isinstance(tool, dict) and isinstance(tool.get("pytest"), dict):
        return True
    project_data = data.get("project") if isinstance(data, dict) else None
    dependency_values: list[str] = []
    if isinstance(project_data, dict):
        dependencies = project_data.get("dependencies")
        if isinstance(dependencies, list):
            dependency_values.extend(str(item) for item in dependencies)
        optional = project_data.get("optional-dependencies")
        if isinstance(optional, dict):
            for values in optional.values():
                if isinstance(values, list):
                    dependency_values.extend(str(item) for item in values)
    return any(re.match(r"^pytest(?:\W|$)", item.strip(), re.I) for item in dependency_values)


def _has_pytest_evidence(project: Path) -> bool:
    if (project / "pytest.ini").is_file() or _pyproject_uses_pytest(project):
        return True
    for config_name in ("setup.cfg", "tox.ini"):
        path = project / config_name
        try:
            if path.is_file() and re.search(
                r"\[(?:tool:)?pytest", path.read_text(encoding="utf-8"), re.I
            ):
                return True
        except (OSError, UnicodeError):
            pass
    tests = project / "tests"
    return tests.is_dir() and any(
        child.is_file() and (child.name.startswith("test_") or child.name.endswith("_test.py"))
        for child in tests.rglob("*.py")
    )


def _package_has_test(project: Path) -> bool:
    path = project / "package.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    command = str(scripts.get("test") or "").strip() if isinstance(scripts, dict) else ""
    return bool(command and "no test specified" not in command.lower())


def _make_has_test(project: Path) -> bool:
    path = project / "Makefile"
    if not path.is_file():
        path = project / "makefile"
    try:
        return bool(
            path.is_file()
            and re.search(
                r"(?m)^test\s*:(?![=])", path.read_text(encoding="utf-8")
            )
        )
    except (OSError, UnicodeError):
        return False


def discover_project_checks(
    root: str | Path,
    active_paths: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Return mechanically evidenced checks at the nearest project boundary."""

    base = Path(root).resolve()
    checks: list[str] = []
    for project in _project_roots(base, active_paths):
        try:
            relative = project.relative_to(base).as_posix()
        except ValueError:
            continue
        prefix = "" if relative == "." else f"cd {relative} && "
        if _has_pytest_evidence(project):
            checks.append(prefix + "pytest -q")
        if _package_has_test(project):
            checks.append(prefix + "npm test")
        if (project / "Cargo.toml").is_file():
            checks.append(prefix + "cargo test")
        if (project / "go.mod").is_file():
            checks.append(prefix + "go test ./...")
        if _make_has_test(project):
            checks.append(prefix + "make test")
    return tuple(dict.fromkeys(checks))


def inspect_index(
    root: str | Path,
    *,
    state_dir: str | Path | None = None,
    source_revision: str = "",
) -> IndexBuildReceipt:
    """Build the repository graph while retaining its exact availability status."""

    return ensure_index_with_receipt(
        root, state_dir=state_dir, source_revision=source_revision
    )


def _graph_structural_roles(
    graph_db: str,
    anchors: tuple[dict[str, Any], ...],
    *,
    limit: int,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
]:
    """Resolve definitions, call references, and direct callers from graph identity."""

    definitions: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    callers: list[dict[str, Any]] = []
    target_ids: list[int] = []
    target_scores: dict[int, tuple[float, float]] = {}
    connection = sqlite3.connect(f"file:{Path(graph_db).resolve().as_posix()}?mode=ro", uri=True)
    try:
        for anchor in anchors:
            path = str(anchor.get("path") or "")
            symbol = str(anchor.get("symbol") or "")
            line = int(anchor.get("line") or 0)
            if not path:
                continue
            rows = connection.execute(
                "SELECT id,label,name,COALESCE(qualified_name,''),file_path,"
                "COALESCE(start_line,0),COALESCE(signature,''),language "
                "FROM nodes WHERE file_path=? AND "
                "((?<>'' AND name=?) OR (? > 0 AND start_line=?)) "
                "ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END,start_line,id LIMIT 4",
                (path, symbol, symbol, line, line, symbol),
            ).fetchall()
            for row in rows:
                node_id = int(row[0])
                definition = {
                    "path": str(row[4]),
                    "line": int(row[5]),
                    "symbol": str(row[2]),
                    "qualified_symbol": str(row[3]),
                    "kind": str(row[1]),
                    "signature": str(row[6]),
                    "language": str(row[7]),
                    "semantics": "graph_definition",
                    "semantic_certainty": float(
                        anchor.get("semantic_certainty") or 0.0
                    ),
                    "retrieval_relevance": float(
                        anchor.get("retrieval_relevance") or 0.0
                    ),
                }
                key = (definition["path"], definition["line"], definition["symbol"])
                if not any(
                    (item["path"], item["line"], item["symbol"]) == key for item in definitions
                ):
                    definitions.append(definition)
                    target_ids.append(node_id)
                    target_scores[node_id] = (
                        float(anchor.get("semantic_certainty") or 0.0),
                        float(anchor.get("retrieval_relevance") or 0.0),
                    )
                if len(definitions) >= limit:
                    break
            if len(definitions) >= limit:
                break
        for target_id in dict.fromkeys(target_ids):
            target_certainty, target_relevance = target_scores.get(
                target_id, (0.0, 0.0)
            )
            rows = connection.execute(
                "SELECT src.name,src.file_path,COALESCE(e.source_line,src.start_line,0),"
                "tgt.name,tgt.file_path,COALESCE(e.resolution_method,''),"
                "COALESCE(e.confidence,0),COALESCE(e.trust_tier,''),"
                "COALESCE(e.candidate_count,0),COALESCE(e.evidence_type,''),"
                "COALESCE(src.language,''),COALESCE(tgt.language,'') "
                "FROM edges e JOIN nodes src ON src.id=e.source_id "
                "JOIN nodes tgt ON tgt.id=e.target_id "
                "WHERE e.type='CALLS' AND e.target_id=? "
                "AND COALESCE(e.confidence,0)>=0.95 "
                "AND COALESCE(e.trust_tier,'')='CERTIFIED' "
                "AND COALESCE(e.candidate_count,0)=1 "
                "ORDER BY src.file_path,e.source_line,src.name LIMIT ?",
                (target_id, limit),
            ).fetchall()
            for row in rows:
                reference = {
                    "path": str(row[1]),
                    "line": int(row[2]),
                    "symbol": str(row[3]),
                    "semantics": "graph_call_reference",
                    "semantic_certainty": min(target_certainty, float(row[6])),
                    "retrieval_relevance": target_relevance,
                    "language": str(row[10]),
                }
                caller = {
                    "caller": str(row[0]),
                    "caller_path": str(row[1]),
                    "caller_line": int(row[2]),
                    "target": str(row[3]),
                    "target_path": str(row[4]),
                    "resolution_method": str(row[5]),
                    "confidence": float(row[6]),
                    "trust_tier": str(row[7]),
                    "candidate_count": int(row[8]),
                    "evidence_type": str(row[9]),
                    "semantics": "graph_recorded",
                    "semantic_certainty": min(target_certainty, float(row[6])),
                    "retrieval_relevance": target_relevance,
                    "language": str(row[10]),
                    "target_language": str(row[11]),
                }
                if reference not in references:
                    references.append(reference)
                if caller not in callers:
                    callers.append(caller)
                if len(callers) >= limit:
                    break
            if len(callers) >= limit:
                break
    finally:
        connection.close()
    return tuple(definitions), tuple(references), tuple(callers)


def inspect_repository(
    root: str | Path,
    instruction: str,
    *,
    state_dir: str | Path | None = None,
    limit: int = 8,
    index_receipt: IndexBuildReceipt | None = None,
    source_revision: str = "",
    active_paths: tuple[str, ...] = (),
    boundary: str = "task_start",
) -> RepositoryEvidence:
    """Index and rank task-specific source anchors without raising.

    An empty result is deliberate abstention. Ranked lexical/body facts become
    anchors only with a concrete symbol, positive line, and high retrieval
    confidence. Callers require a certified directed CALLS edge; ambiguous or
    heuristic relations remain absent.
    """
    base = Path(root)
    checks = discover_project_checks(base, active_paths=active_paths)
    try:
        index_receipt = index_receipt or inspect_index(
            base, state_dir=state_dir, source_revision=source_revision
        )
        graph_db = index_receipt.graph_db
        if not graph_db:
            status = {
                IndexBuildStatus.NO_SUPPORTED_SOURCE: (
                    RepositoryIntelligenceStatus.NO_SUPPORTED_SOURCE.value
                ),
                IndexBuildStatus.UNSUPPORTED_LANGUAGE: (
                    RepositoryIntelligenceStatus.UNSUPPORTED_LANGUAGE.value
                ),
                IndexBuildStatus.INCOMPLETE_COVERAGE: (
                    RepositoryIntelligenceStatus.INCOMPLETE_COVERAGE.value
                ),
                IndexBuildStatus.INVALID_DATABASE: (
                    RepositoryIntelligenceStatus.SCHEMA_INVALID.value
                ),
            }.get(index_receipt.status, RepositoryIntelligenceStatus.INDEX_UNAVAILABLE.value)
            return RepositoryEvidence(
                project_checks=checks,
                status=status,
                index=index_receipt,
                substrate_status=(
                    RepositorySubstrateStatus.NOT_APPLICABLE.value
                    if index_receipt.status is IndexBuildStatus.NO_SUPPORTED_SOURCE
                    else RepositorySubstrateStatus.INCOMPLETE.value
                    if index_receipt.status
                    in {
                        IndexBuildStatus.UNSUPPORTED_LANGUAGE,
                        IndexBuildStatus.INCOMPLETE_COVERAGE,
                    }
                    else RepositorySubstrateStatus.INVALID.value
                ),
            )
        if not index_receipt.schema_valid:
            return RepositoryEvidence(
                graph_revision=index_receipt.graph_revision,
                project_checks=checks,
                status=RepositoryIntelligenceStatus.SCHEMA_INVALID.value,
                index=index_receipt,
                substrate_status=RepositorySubstrateStatus.INVALID.value,
            )
        if index_receipt.status is IndexBuildStatus.INCOMPLETE_COVERAGE:
            return RepositoryEvidence(
                graph_revision=index_receipt.graph_revision,
                project_checks=checks,
                status=RepositoryIntelligenceStatus.INCOMPLETE_COVERAGE.value,
                index=index_receipt,
                substrate_status=RepositorySubstrateStatus.INCOMPLETE.value,
            )
        contract = extract_task_contract(instruction)
        if not contract.obligations and instruction.strip():
            # The strict contract extractor intentionally ignores prose that
            # contains no explicit modal requirement.  Localization still
            # needs lexical task anchors, so retain that prose as one bounded
            # search obligation without pretending it is a verifier oracle.
            contract = TaskContract(
                role=contract.role,
                task_mode=contract.task_mode,
                predicates=contract.predicates,
                obligations=(
                    Obligation(
                        obligation_id="task:instruction",
                        text=" ".join(instruction.split())[:2000],
                        source="instruction",
                    ),
                ),
            )
        projection = build_graph_projection(
            graph_db,
            contract,
            limit=max(8, limit * 2),
            active_paths=active_paths,
        )
        need = build_evidence_need(
            contract,
            projection,
            boundary=boundary,
            active_paths=active_paths,
        )
        ranked = rank_graph_evidence(contract, projection, need, limit=limit)
        anchors: list[dict[str, Any]] = []
        for item in ranked:
            if (
                not item.file_path
                or not item.symbol
                or int(item.line) <= 0
                or float(item.semantic_certainty) < 0.95
                or float(item.retrieval_relevance) < 0.95
            ):
                continue
            anchor = {
                "path": item.file_path,
                "line": int(item.line),
                "symbol": item.symbol,
                "surface": item.surface,
                # These are independent axes.  Extractor certainty must not
                # inflate task-conditioned retrieval relevance.
                "confidence": float(item.semantic_certainty),
                "retrieval_relevance": float(item.retrieval_relevance),
                "semantic_certainty": float(item.semantic_certainty),
                "relevance_reason_codes": list(item.relevance_reason_codes),
            }
            key = (anchor["path"], anchor["line"], anchor["symbol"])
            if any((row["path"], row["line"], row["symbol"]) == key for row in anchors):
                continue
            anchors.append(anchor)
            if len(anchors) >= 4:
                break
        if not anchors:
            return RepositoryEvidence(
                graph_revision=projection.revision,
                project_checks=checks,
                status=RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
                index=index_receipt,
                source_revision=source_revision,
                index_current=bool(source_revision),
                intelligence_valid=bool(source_revision),
                substrate_ready=True,
                substrate_status=RepositorySubstrateStatus.HEALTHY_CURRENT.value,
                retrieval_disposition=RetrievalDisposition.EMPTY.value,
            )
        definitions, references, callers = _graph_structural_roles(
            graph_db,
            tuple(anchors),
            limit=max(1, limit),
        )
        return RepositoryEvidence(
            available=True,
            graph_revision=projection.revision,
            anchors=tuple(anchors),
            definitions=definitions,
            references=references,
            callers=callers,
            project_checks=checks,
            status=RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
            index=index_receipt,
            source_revision=source_revision,
            index_current=bool(source_revision),
            intelligence_valid=bool(source_revision),
            substrate_ready=True,
            substrate_status=RepositorySubstrateStatus.HEALTHY_CURRENT.value,
            retrieval_disposition=RetrievalDisposition.MATCHED.value,
        )
    except Exception as exc:
        return RepositoryEvidence(
            project_checks=checks,
            status=RepositoryIntelligenceStatus.INDEX_UNAVAILABLE.value,
            substrate_status=RepositorySubstrateStatus.INVALID.value,
            index=IndexBuildReceipt(
                IndexBuildStatus.BUILD_FAILED,
                error_type=type(exc).__name__,
            ),
        )


__all__ = [
    "RepositoryApplicability",
    "RepositoryEvidence",
    "RepositoryIntelligenceStatus",
    "RepositorySession",
    "RepositorySubstrateStatus",
    "RetrievalDisposition",
    "discover_project_checks",
    "graph_gate_failures",
    "classify_repository_applicability",
    "inspect_index",
    "inspect_repository",
]
