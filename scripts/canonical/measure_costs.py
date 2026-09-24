"""G — canonical cost measurement (plan item G, P9).

Measures REAL costs of the canonical implementation on this host and writes
``docs/canonical/COSTS.json`` + ``docs/canonical/COSTS.md``. Nothing is
estimated: every number comes from a timed subprocess, a psutil memory
sample, a stat call, or the ``CapabilityResult.cost`` fields the facades
already report.

Repo legs (per plan):
  * ``fixture``         — tests/canonical/fixtures/polyglot
  * ``harness``         — D:/gt-canonical itself
  * ``groundtruth600``  — D:/gt-canonical-producer, bounded to the same
                          lexical-first-600 file subset the smoke matrix
                          used (the FULL producer index was measured
                          storage-blocked three times on this host:
                          ~58GiB peak WAL+DB vs. the machine's free
                          space; recorded here as a limitation, not a
                          guess)
  * ``airflow600``      — apache/airflow checkout, same 600-file bound

Per repo: full index wall time + peak RSS (sampled from the live process
tree every 50ms) + graph.db bytes; certified-publish time through
``indexer.ensure_index``; batch-amend time for a 1-file and a 10-file
edit through the REAL adapter path (``record_edit_transaction``) plus the
shared ``_ensure_index_incremental_unlocked`` seam where the transaction
boundary defers (parents > 512 MiB); per-file amend support; a timed
full-workspace semantic snapshot (streaming row scan + content hash —
the same tables the determinism suite compares).

Per capability (on the fixture session — the substrate that exercises
every certified kind): 20 calls each, p50/p95 of the facade-measured
elapsed_ms, output bytes, status, and an invalidated-by classification
derived from what each facade actually reads (graph tables -> graph
revision; journal/CAS -> edit/execution; engine state -> edit+graph).

Wire overhead: for each of the 16 certified typed kinds, the compiled
observation's output bytes vs. the serialized direct_answer bytes.

Windows note: ``_run_index_bounded`` refuses to spawn the producer
without a verified descendant-teardown guard (Job Object). This script
installs the same test-grade verified-kill patch the canonical suite
uses (tests/canonical/conftest.py, tests/test_index_incremental.py);
the refusal remains production behavior outside measurement.

Usage:
    python scripts/canonical/measure_costs.py                 # all legs
    python scripts/canonical/measure_costs.py --only fixture
    python scripts/canonical/measure_costs.py --only capabilities
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

CANON_DIR = REPO_ROOT / "tests" / "canonical"
FIXTURE_DIR = CANON_DIR / "fixtures" / "polyglot"
FIXTURE_REVISION = "canon-polyglot-rev-1"
OUT_JSON = REPO_ROOT / "docs" / "canonical" / "COSTS.json"
OUT_MD = REPO_ROOT / "docs" / "canonical" / "COSTS.md"
SCRATCH = Path(tempfile.gettempdir()) / "gt-canonical-costs"

PRODUCER_CANDIDATES = (
    os.environ.get("GT_INDEX_BINARY", ""),
    r"C:\gt-smoke-a6\gt-index-new.exe",
    shutil.which("gt-index") or "",
)

# The content-bearing tables the determinism suite compares — the same
# set a "semantic snapshot" means here.
_SNAPSHOT_TABLES = (
    "nodes", "edges", "assertions", "closure", "cochanges",
    "communities", "community_members", "processes", "process_steps",
    "cfg_blocks", "cfg_defs", "cfg_edges", "cfg_uses",
    "resolution_callsites", "resolution_candidates",
)

_RSS_SAMPLE_SECONDS = 0.05

_REPO_LEGS = (
    {
        "id": "fixture",
        "title": "tests/canonical/fixtures/polyglot",
        "source": FIXTURE_DIR,
        "cap": None,
        "amend": True,
    },
    {
        "id": "airflow600",
        "title": "apache/airflow @ first-600 lexical indexable files",
        "source": Path(r"D:\gt_runs\airflow_54145\repo"),
        "cap": 600,
        "amend": True,
    },
    {
        "id": "groundtruth600",
        "title": "gt-index producer repo @ first-600 lexical indexable files",
        "source": Path(r"D:\gt-canonical-producer"),
        "cap": 600,
        "amend": True,
    },
    {
        "id": "harness",
        "title": "gt-harness canonical checkout (D:/gt-canonical)",
        "source": Path(r"D:\gt-canonical"),
        "cap": None,
        "amend": True,
    },
)

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".gt", "__pycache__", ".pytest_cache", "node_modules",
    ".venv", "venv", ".mypy_cache", ".ruff_cache", "graph.db",
)

_COMMENT_BY_SUFFIX = {
    ".py": "#", ".sh": "#", ".go": "//", ".ts": "//", ".tsx": "//",
    ".js": "//", ".jsx": "//", ".java": "//", ".c": "//", ".h": "//",
    ".rs": "//", ".rb": "#", ".cs": "//", ".cpp": "//", ".cc": "//",
}


def _resolve_producer() -> tuple[str, dict[str, Any]]:
    for candidate in PRODUCER_CANDIDATES:
        if not candidate or not Path(candidate).is_file():
            continue
        probe = subprocess.run(
            [candidate, "-build-info"], capture_output=True, timeout=30,
        )
        if probe.returncode != 0:
            continue
        try:
            info = json.loads(probe.stdout.decode("utf-8", "replace"))
        except ValueError:
            info = {}
        return candidate, info if isinstance(info, dict) else {}
    raise SystemExit("no runnable gt-index producer found on this host")


def _peak_rss_sampler(process: subprocess.Popen) -> tuple[list[int], threading.Event]:
    """Sample the whole process tree's RSS every 50ms; return the peak."""
    import psutil

    peaks: list[int] = [0]
    stop = threading.Event()

    def poll() -> None:
        try:
            root = psutil.Process(process.pid)
        except psutil.Error:
            return
        while not stop.is_set():
            try:
                total = 0
                procs = [root] + root.children(recursive=True)
                for proc in procs:
                    try:
                        total += proc.memory_info().rss
                    except psutil.Error:
                        continue
                if total > peaks[0]:
                    peaks[0] = total
            except psutil.Error:
                break
            stop.wait(_RSS_SAMPLE_SECONDS)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    return peaks, stop


def _copy_repo(source: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=_COPY_IGNORE)
    return dest


def _indexed_paths(db: Path) -> set[str]:
    """Repo-relative paths the producer actually indexed (file_hashes)."""
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "file_hashes" in tables:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(file_hashes)")]
            path_col = "path" if "path" in cols else cols[0]
            return {
                str(row[0]).replace("\\", "/")
                for row in conn.execute(f"SELECT {path_col} FROM file_hashes")
            }
    return set()


def _trim_to_indexed(workspace: Path, keep: set[str]) -> int:
    removed = 0
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            rel = path.relative_to(workspace).as_posix()
            if rel not in keep:
                path.unlink()
                removed += 1
    for dirpath in sorted(
        (p for p in workspace.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts), reverse=True,
    ):
        try:
            next(dirpath.iterdir())
        except StopIteration:
            dirpath.rmdir()
    return removed


def _run_producer_index(
    binary: str, workspace: Path, output: Path, *,
    max_files: int | None, source_revision: str, log: Path,
    capabilities: list[str],
) -> dict[str, Any]:
    argv = [binary, "-root", str(workspace), "-output", str(output), "-workers", "8"]
    if max_files:
        argv += ["-max-files", str(max_files)]
    if source_revision and "source_revision_meta_v1" in capabilities:
        argv += ["-source-revision", source_revision]
    started = time.monotonic()
    with log.open("wb") as handle:
        process = subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT)
        peaks, stop = _peak_rss_sampler(process)
        returncode = process.wait()
        stop.set()
    elapsed = time.monotonic() - started
    return {
        "argv": argv,
        "returncode": returncode,
        "wall_seconds": round(elapsed, 3),
        "peak_rss_bytes": peaks[0],
        "graph_db_bytes": output.stat().st_size if output.is_file() else None,
        "log": str(log),
    }


def _install_test_guard() -> None:
    """Same verified-kill guard patch the canonical suite installs."""
    if os.name != "nt":
        return
    from gt_engine import indexer

    def verified_kill(process: subprocess.Popen) -> bool:
        if process.poll() is None:
            process.kill()
        return True

    indexer._has_verified_index_process_tree_guard = lambda: True  # noqa: SLF001
    indexer._kill_index_process_tree = verified_kill  # noqa: SLF001


def _ensure_published(workspace: Path, state: Path, task_id: str,
                      revision: str) -> tuple[str | None, float, list[str]]:
    from gt_engine import indexer
    from gt_engine.engine_state import RuntimeLayout

    layout = RuntimeLayout.resolve(
        workspace=workspace, state_root=state, task_id=task_id
    )
    diagnostics: list[str] = []
    started = time.monotonic()
    graph = indexer.ensure_index(
        str(workspace), layout=layout, source_revision=revision,
        diagnostics=diagnostics,
    )
    return graph, time.monotonic() - started, diagnostics


def _bind_session(workspace: Path, state: Path, task_id: str, graph: str,
                  revision: str):
    from gt_engine.engine_state import RuntimeLayout
    from gt_engine.gt_session import GTSession, GTSessionConfig
    from gt_engine.miniswe_integration import MiniSweAdapter

    layout = RuntimeLayout.resolve(
        workspace=workspace, state_root=state, task_id=task_id
    )
    adapter = MiniSweAdapter(
        task_id=task_id, state_dir=state, predicates=[],
        repo_root=workspace, graph_db=graph, layout=layout,
    )
    adapter.engine_state.bind_initial_source(revision)
    session = GTSession(GTSessionConfig(task_id=task_id), engine=adapter)
    return session, adapter, layout


def _edit_files(workspace: Path, relpaths: list[str], tag: str) -> list[str]:
    """Apply one real comment-append edit per file; return touched relpaths."""
    touched: list[str] = []
    for rel in relpaths:
        target = workspace / rel
        if not target.is_file():
            continue
        comment = _COMMENT_BY_SUFFIX.get(target.suffix.lower())
        if comment is None:
            continue
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{comment} canonical-cost-probe {tag}\n")
        touched.append(rel)
    return touched


def _source_like(paths: set[str]) -> list[str]:
    exts = set(_COMMENT_BY_SUFFIX)
    return sorted(p for p in paths if Path(p).suffix.lower() in exts)


def _record_amend(adapter, workspace: Path, before_snap, action_id: int):
    from gt_engine.runtime_observation import capture_workspace, diff_workspace

    after = capture_workspace(workspace)
    txn = diff_workspace(before_snap, after, action_id=action_id,
                         command=f"canonical-cost-edit-{action_id}")
    started = time.monotonic()
    adapter.record_edit_transaction(txn)
    elapsed = time.monotonic() - started
    return txn, elapsed


def _journal_rows(state: Path, task_id: str) -> list[dict[str, Any]]:
    path = state / task_id / "events.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _last_event(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any] | None:
    last = None
    for row in rows:
        if str(row.get("event", "")).startswith(prefix):
            last = row
    return last


def _direct_amend(workspace: Path, layout, parent: str,
                  dirty: list[str], revision: str) -> dict[str, Any]:
    """Time the shared incremental seam directly — what both serving and
    transaction boundaries call (used when the txn boundary defers)."""
    from gt_engine import indexer
    from gt_engine.indexer import _graph_publication_lock

    started = time.monotonic()
    try:
        with _graph_publication_lock(layout.graph_root / ".graph.lock"):
            published, reason, results = indexer._ensure_index_incremental_unlocked(
                str(workspace), layout=layout, parent_graph=Path(parent),
                changed_paths=tuple(dirty),
                excluded_roots=tuple(layout.excluded_roots),
                source_revision=revision,
            )
    except Exception as exc:  # noqa: BLE001 - measurement records the refusal
        published, reason, results = None, f"{type(exc).__name__}: {exc}"[:200], ()
    return {
        "wall_seconds": round(time.monotonic() - started, 3),
        "published": str(published) if published else None,
        "reason": reason or "",
        "amended_paths": len(results or ()),
    }


def _snapshot(graph: Path) -> dict[str, Any]:
    """Stream every content table; per-table rows + content sha256."""
    tables: dict[str, Any] = {}
    started = time.monotonic()
    with sqlite3.connect(f"file:{graph.as_posix()}?mode=ro", uri=True) as conn:
        existing = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in _SNAPSHOT_TABLES:
            if table not in existing:
                tables[table] = {"rows": None, "sha256": None}
                continue
            digest = hashlib.sha256()
            count = 0
            cursor = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
            cols = "|".join(row[1] for row in conn.execute(
                f"PRAGMA table_info({table})"))
            digest.update(cols.encode())
            for row in cursor:
                digest.update(repr(row).encode("utf-8", "surrogatepass"))
                digest.update(b"\n")
                count += 1
            tables[table] = {"rows": count, "sha256": digest.hexdigest()}
    return {
        "wall_seconds": round(time.monotonic() - started, 3),
        "tables": tables,
    }


def measure_repo_leg(leg: dict[str, Any], binary: str,
                     build_info: dict[str, Any], scratch: Path) -> dict[str, Any]:
    from gt_engine.runtime_observation import capture_workspace

    capabilities = list(build_info.get("capabilities") or [])
    leg_dir = scratch / leg["id"]
    workspace = _copy_repo(Path(leg["source"]), leg_dir / "repo")
    result: dict[str, Any] = {
        "title": leg["title"],
        "workspace": str(workspace),
        "file_bound": leg["cap"],
    }

    # ── index (direct producer run: true binary cost) ────────────────────
    index = _run_producer_index(
        binary, workspace, leg_dir / "index.db",
        max_files=leg["cap"], source_revision="costs-index-1",
        log=leg_dir / "index.log", capabilities=capabilities,
    )
    result["index"] = index
    if index["returncode"] != 0 or not (leg_dir / "index.db").is_file():
        result["error"] = "producer index failed; see index.log"
        return result

    indexed = _indexed_paths(leg_dir / "index.db")
    result["indexed_file_count"] = len(indexed)

    # Bounded legs: rebuild the workspace to exactly the indexed subset so
    # ensure_index's unbounded walk cannot outrun the bound.
    if leg["cap"] and indexed:
        removed = _trim_to_indexed(workspace, indexed)
        result["subset_workspace"] = {
            "files_kept": len(indexed), "files_removed": removed,
        }

    if not leg["amend"]:
        return result

    # ── certified publish through the production path ────────────────────
    _install_test_guard()
    state = leg_dir / "state"
    task_id = f"costs-{leg['id']}"
    graph, publish_seconds, diagnostics = _ensure_published(
        workspace, state, task_id, "costs-rev-0")
    result["ensure_index_publish_seconds"] = round(publish_seconds, 3)
    result["ensure_index_diagnostics"] = diagnostics
    if graph is None:
        result["amend"] = {
            "error": "ensure_index could not publish a certified parent",
            "diagnostics": diagnostics,
        }
        return result
    result["published_graph"] = graph

    session, adapter, layout = _bind_session(
        workspace, state, task_id, graph, "costs-rev-0")

    # ── snapshot cost on the certified parent ────────────────────────────
    result["snapshot"] = _snapshot(Path(graph))

    candidates = _source_like(indexed)
    one_file = candidates[:1]
    ten_files = [p for p in candidates if p not in one_file][:10]
    result["amend_edit_sets"] = {"one_file": one_file, "ten_files": ten_files}

    # ── 1-file edit through the real transaction boundary ────────────────
    amend: dict[str, Any] = {}
    if one_file:
        before = capture_workspace(workspace)
        _edit_files(workspace, one_file, "one")
        txn, elapsed = _record_amend(adapter, workspace, before, 1)
        rows = _journal_rows(state, task_id)
        adopted = _last_event(rows, "graph_sync_amend")
        refused = _last_event(rows, "graph_sync_amend_refused")
        deferred = _last_event(rows, "graph_amend_deferred")
        amend["one_file"] = {
            "txn_wall_seconds": round(elapsed, 3),
            "changed_paths": list(txn.changed_paths),
            "journal_outcome": (
                "adopted" if adopted and adopted.get("adopted")
                else f"refused:{refused.get('reason')}" if refused
                else f"deferred" if deferred
                else "boundary_skipped"
            ),
        }
        parent = adapter.engine_state.graph_path or graph
        parent_bytes = Path(parent).stat().st_size if Path(parent).is_file() else 0
        if parent_bytes > adapter.SYNC_AMEND_MAX_GRAPH_BYTES:
            # The txn boundary silently defers past 512 MiB; measure the
            # shared seam directly so the leg still gets a real number.
            amend["one_file"]["direct_seam"] = _direct_amend(
                workspace, layout, parent, list(txn.changed_paths),
                "costs-rev-1")
            amend["one_file"]["note"] = (
                f"parent {parent_bytes} B > SYNC_AMEND_MAX_GRAPH_BYTES; "
                "txn-boundary amend skipped by design, shared seam timed "
                "directly")

    # ── 10-file edit ─────────────────────────────────────────────────────
    if ten_files:
        before = capture_workspace(workspace)
        _edit_files(workspace, ten_files, "ten")
        txn, elapsed = _record_amend(adapter, workspace, before, 2)
        rows = _journal_rows(state, task_id)
        adopted = _last_event(rows, "graph_sync_amend")
        refused = _last_event(rows, "graph_sync_amend_refused")
        amend["ten_file"] = {
            "txn_wall_seconds": round(elapsed, 3),
            "changed_paths": list(txn.changed_paths),
            "journal_outcome": (
                "adopted" if adopted and adopted.get("adopted")
                else f"refused:{refused.get('reason')}" if refused
                else "boundary_skipped"
            ),
        }
        parent = adapter.engine_state.graph_path or graph
        parent_bytes = Path(parent).stat().st_size if Path(parent).is_file() else 0
        if parent_bytes > adapter.SYNC_AMEND_MAX_GRAPH_BYTES:
            amend["ten_file"]["direct_seam"] = _direct_amend(
                workspace, layout, parent, list(txn.changed_paths),
                "costs-rev-2")

    # ── per-file amend support ───────────────────────────────────────────
    from gt_engine import indexer as _idx
    amend["per_file"] = {
        "supported": _idx._producer_supports_incremental_amend(),  # noqa: SLF001
        "note": (
            "producer declares incremental_amend_in_place"
            if _idx._producer_supports_incremental_amend()  # noqa: SLF001
            else "producer build-info does not declare "
                 "incremental_amend_in_place; only the batch lane exists "
                 "on this binary"),
    }
    result["amend"] = amend
    return result


# ── per-capability costs (fixture substrate) ─────────────────────────────

def _capability_calls():
    from gt_engine.capabilities import (
        analysis, change, freshness, localization, runtime, structure,
    )

    return [
        ("localization.lexical_search",
         lambda s: localization.lexical_search(s, "Depends(get_store)", scope=["pyapp"]),
         "graph_revision"),
        ("localization.hybrid_rank",
         lambda s: localization.hybrid_rank(s, "sanitize", k=8),
         "edit|graph_revision"),
        ("localization.definition",
         lambda s: localization.definition(s, "sanitize"),
         "graph_revision"),
        ("localization.references",
         lambda s: localization.references(s, "sanitize"),
         "graph_revision"),
        ("structure.callers",
         lambda s: structure.callers(s, "execute", depth=3),
         "graph_revision"),
        ("structure.callees",
         lambda s: structure.callees(s, "list_items"),
         "graph_revision"),
        ("structure.symbol_context",
         lambda s: structure.symbol_context(s, "FriendlyGreeter"),
         "graph_revision"),
        ("structure.processes",
         lambda s: structure.processes(s, concept="items"),
         "graph_revision"),
        ("structure.communities",
         lambda s: structure.communities(s, "pyapp/server.py"),
         "graph_revision"),
        ("structure.framework_relationships",
         lambda s: structure.framework_relationships(s, "/api/items"),
         "graph_revision"),
        ("analysis.cfg",
         lambda s: analysis.cfg(s, "list_items"),
         "graph_revision"),
        ("analysis.reaching_definitions",
         lambda s: analysis.reaching_definitions(s, "list_items"),
         "graph_revision"),
        ("analysis.control_dependence",
         lambda s: analysis.control_dependence(s, "list_items"),
         "graph_revision"),
        ("analysis.slice",
         lambda s: analysis.slice(s, "list_items", line=82),
         "graph_revision"),
        ("analysis.callable_values",
         lambda s: analysis.callable_values(s, "dispatch"),
         "graph_revision"),
        ("analysis.taint",
         lambda s: analysis.taint(s, sources="list_items", sinks="execute"),
         "graph_revision"),
        ("change.edit_transaction",
         lambda s: change.edit_transaction(s),
         "edit"),
        ("change.patch_impact",
         lambda s: change.patch_impact(
             s, {"pyapp/server.py": {"before": "x = 1\n", "after": "x = 2\n"}}),
         "graph_revision|edit"),
        ("change.route_impact",
         lambda s: change.route_impact(s, route="/api/items"),
         "graph_revision"),
        ("change.shape_change",
         lambda s: change.shape_change(s, "Friendly"),
         "graph_revision"),
        ("change.affected_tests",
         lambda s: change.affected_tests(s, ["pyapp/server.py"]),
         "graph_revision|edit"),
        ("runtime.last_test_result",
         lambda s: runtime.last_test_result(s),
         "execution"),
        ("runtime.covering_tests",
         lambda s: runtime.covering_tests(s, ["pyapp/server.py"]),
         "graph_revision|edit"),
        ("runtime.failure_fingerprint",
         lambda s: runtime.failure_fingerprint(s),
         "execution"),
        ("runtime.repeated_failure_state",
         lambda s: runtime.repeated_failure_state(s),
         "execution"),
        ("runtime.verification_state",
         lambda s: runtime.verification_state(s),
         "execution"),
        ("freshness.index_revision",
         lambda s: freshness.index_revision(s),
         "edit|graph_revision"),
        ("freshness.graph_state",
         lambda s: freshness.graph_state(s),
         "edit|graph_revision"),
        ("freshness.amend_state",
         lambda s: freshness.amend_state(s),
         "edit|graph_revision"),
        ("freshness.fallback_state",
         lambda s: freshness.fallback_state(s),
         "edit|graph_revision"),
        ("freshness.unit_state",
         lambda s: freshness.unit_state(s, "canon-unknown"),
         "edit"),
    ]


_TYPED_KIND_ARGS = {
    "exact_literal_search": {"literal": "Depends(get_store)", "paths": ["pyapp"]},
    "syntax": {"path": "pyapp/server.py"},
    "patch_impact": {"edited_files": {"pyapp/server.py": {"before": "x\n", "after": "y\n"}}},
    "verification_status": {},
    "definition": {"symbol": "sanitize"},
    "references": {"symbol": "sanitize"},
    "callers": {"symbol": "execute", "depth": 3},
    "symbol_context": {"symbol": "FriendlyGreeter"},
    "processes": {"concept": "items"},
    "route_map": {"path": "pyapp/server.py"},
    "api_impact": {"symbol": "list_items"},
    "taint": {"source": "list_items", "sink": "execute", "depth": 4},
    "rename": {"symbol": "sanitize"},
    "shape_check": {"symbol": "Friendly"},
    "tool_map": {},
    "slice": {"symbol": "list_items", "line": 82},
}


def measure_capabilities(session, conf: dict[str, Any],
                         calls: int = 20) -> tuple[dict[str, Any], dict[str, Any]]:
    from gt_engine.miniswe_typed_actions import (
        build_action_request, execute_typed_action,
    )

    engine = session._engine  # noqa: SLF001 - same object the facades use
    per_cap: dict[str, Any] = {}
    for name, invoke, invalidated_by in _capability_calls():
        first = invoke(session)
        samples: list[int] = []
        for _ in range(calls):
            result = invoke(session)
            samples.append(int(result.cost.get("elapsed_ms", 0)))
        ordered = sorted(samples)
        per_cap[name] = {
            "calls": calls,
            "status": first.status,
            "semantics": first.semantics,
            "p50_ms": ordered[len(ordered) // 2],
            "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            "min_ms": ordered[0],
            "max_ms": ordered[-1],
            "answer_bytes": int(first.cost.get("output_bytes", 0)),
            "cacheable": False,
            "invalidated_by": invalidated_by,
        }

    wire: dict[str, Any] = {}
    for kind, args in _TYPED_KIND_ARGS.items():
        request = build_action_request(
            {
                "gt_action": {"kind": kind, "arguments": dict(args)},
                "tool_call_id": f"costs:{kind}",
            },
            repo_root=engine.repo_root,
            configuration=conf,
        )
        result = execute_typed_action(
            request, repo_root=engine.repo_root,
            graph_db=engine.graph_db or None,
        )
        envelope = result["output"].encode("utf-8")
        payload = json.loads(result["output"])
        answer = json.dumps(
            payload.get("direct_answer"), sort_keys=True).encode("utf-8")
        wire[kind] = {
            "envelope_bytes": len(envelope),
            "answer_bytes": len(answer),
            "overhead_ratio": round(len(envelope) / max(len(answer), 1), 3),
        }
    return per_cap, wire


def _fixture_session(binary: str, build_info: dict[str, Any],
                     scratch: Path):
    """A session bound to a producer-indexed fixture copy."""
    leg_dir = scratch / "capabilities"
    workspace = _copy_repo(FIXTURE_DIR, leg_dir / "repo")
    index = _run_producer_index(
        binary, workspace, leg_dir / "graph.db", max_files=None,
        source_revision=FIXTURE_REVISION, log=leg_dir / "index.log",
        capabilities=list(build_info.get("capabilities") or []))
    if index["returncode"] != 0:
        raise SystemExit("fixture index failed for capability leg")
    session, _adapter, _layout = _bind_session(
        workspace, leg_dir / "state", "costs-capabilities",
        str(leg_dir / "graph.db"), FIXTURE_REVISION)
    conf = {
        "configuration_id": "costs-capabilities",
        "graph_db": str(leg_dir / "graph.db"),
        "graph_source_revision": FIXTURE_REVISION,
    }
    return session, conf, index


def _load_costs() -> dict[str, Any]:
    if OUT_JSON.is_file():
        try:
            return json.loads(OUT_JSON.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def _write_outputs(data: dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_markdown(data)


def _write_markdown(data: dict[str, Any]) -> None:
    lines = [
        "# Canonical cost measurements (G)",
        "",
        f"Generated by `scripts/canonical/measure_costs.py` on "
        f"{data.get('generated_at', '?')} — every figure measured, none "
        "estimated.",
        "",
        "## Host / producer",
        "",
        f"- platform: `{data.get('host', {}).get('platform', '?')}`",
        f"- producer: `{data.get('producer', {}).get('binary', '?')}`",
        f"- producer sha256: `{data.get('producer', {}).get('sha256', '?')}`",
        f"- amend capabilities declared: "
        f"`{data.get('producer', {}).get('amend_capabilities', [])}`",
        "",
        "## Per-repo costs",
        "",
        "| repo | index wall s | peak RSS | graph.db | publish s | "
        "snapshot s | amend 1-file | amend 10-file | per-file amend |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for leg_id, leg in sorted((data.get("repos") or {}).items()):
        index = leg.get("index") or {}
        amend = leg.get("amend") or {}
        snap = leg.get("snapshot") or {}

        def _fmt_amend(entry: dict[str, Any]) -> str:
            if not entry:
                return "—"
            if entry.get("direct_seam"):
                seam = entry["direct_seam"]
                return (f"{seam['wall_seconds']}s (seam; "
                        f"{seam.get('reason') or 'published'})")
            return f"{entry.get('txn_wall_seconds')}s ({entry.get('journal_outcome')})"

        lines.append(
            f"| {leg_id} | {index.get('wall_seconds', '—')} | "
            f"{index.get('peak_rss_bytes', '—')} | "
            f"{index.get('graph_db_bytes', '—')} | "
            f"{leg.get('ensure_index_publish_seconds', '—')} | "
            f"{snap.get('wall_seconds', '—')} | "
            f"{_fmt_amend(amend.get('one_file') or {})} | "
            f"{_fmt_amend(amend.get('ten_file') or {})} | "
            f"{'yes' if (amend.get('per_file') or {}).get('supported') else 'no'} |"
        )
    lines += [
        "",
        "Notes:",
    ]
    for leg_id, leg in sorted((data.get("repos") or {}).items()):
        index = leg.get("index") or {}
        amend = leg.get("amend") or {}
        notes = []
        if leg.get("file_bound"):
            notes.append(f"bounded to first {leg['file_bound']} indexable files")
        for key in ("one_file", "ten_file"):
            note = (amend.get(key) or {}).get("note")
            if note:
                notes.append(f"{key}: {note}")
        if (amend.get("per_file") or {}).get("note"):
            notes.append(f"per-file: {amend['per_file']['note']}")
        if leg.get("error"):
            notes.append(f"ERROR: {leg['error']}")
        if notes:
            lines.append(f"- **{leg_id}**: " + "; ".join(notes))
    limitations = data.get("storage_limitations") or []
    if limitations:
        lines += ["", "## Storage limitations", ""]
        lines += [f"- {item}" for item in limitations]
    caps = data.get("capabilities") or {}
    if caps:
        lines += [
            "", "## Per-capability costs (fixture substrate, 20 calls)", "",
            "| capability | status | semantics | p50 ms | p95 ms | "
            "max ms | answer bytes | invalidated by |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for name, row in sorted(caps.items()):
            lines.append(
                f"| {name} | {row['status']} | {row['semantics']} | "
                f"{row['p50_ms']} | {row['p95_ms']} | {row['max_ms']} | "
                f"{row['answer_bytes']} | {row['invalidated_by']} |")
    wire = data.get("wire_overhead") or {}
    if wire:
        lines += [
            "", "## Typed wire overhead", "",
            "| kind | envelope bytes | answer bytes | envelope/answer |",
            "|---|---|---|---|",
        ]
        for kind, row in sorted(wire.items()):
            lines.append(
                f"| {kind} | {row['envelope_bytes']} | "
                f"{row['answer_bytes']} | {row['overhead_ratio']} |")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=[l["id"] for l in _REPO_LEGS] + ["capabilities"],
                        help="measure just one leg")
    parser.add_argument("--calls", type=int, default=20,
                        help="timed calls per capability")
    args = parser.parse_args()

    binary, build_info = _resolve_producer()
    # The harness publish path (_seed_binary_env) must resolve the same
    # producer the direct runs use — otherwise it tries to download a
    # released binary that is not the canonical build.
    os.environ["GT_INDEX_BINARY"] = binary
    SCRATCH.mkdir(parents=True, exist_ok=True)
    data = _load_costs()
    data["schema"] = "gt.canonical_costs.v1"
    data["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["host"] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
    }
    sha = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    data["producer"] = {
        "binary": binary,
        "sha256": sha,
        "build_info": build_info,
        "amend_capabilities": [
            cap for cap in (build_info.get("capabilities") or [])
            if "amend" in cap or "reuse" in cap
        ],
    }
    data.setdefault("storage_limitations", [
        "groundtruth FULL index is storage-blocked on this host: the "
        "producer repo writes a ~58GiB WAL+DB peak at checkpoint and the "
        "run failed three times (C: exhaustion; see smoke logs "
        "C:/gt-smoke-a6/producer_new.log + run notes on HAR-90). The "
        "groundtruth600 leg measures the same deterministic lexical-first-"
        "600-file bound the smoke matrix used.",
    ])
    repos = data.setdefault("repos", {})

    selected = [l for l in _REPO_LEGS if args.only in (None, l["id"])]
    for leg in selected:
        print(f"[costs] leg {leg['id']}: indexing {leg['source']}", flush=True)
        repos[leg["id"]] = measure_repo_leg(leg, binary, build_info, SCRATCH)
        _write_outputs(data)
        print(f"[costs] leg {leg['id']} done", flush=True)

    if args.only in (None, "capabilities"):
        print("[costs] capability leg on fixture substrate", flush=True)
        session, conf, _idx = _fixture_session(binary, build_info, SCRATCH)
        caps, wire = measure_capabilities(session, conf, calls=args.calls)
        data["capabilities"] = caps
        data["wire_overhead"] = wire
        _write_outputs(data)
        print("[costs] capability leg done", flush=True)

    _write_outputs(data)


if __name__ == "__main__":
    main()
