"""Bounded covering / syntax / recovery lanes for the Mini-SWE seam.

The Gateway does not execute tests: a ``covering_red`` fact requires the seam to
run a bounded probe on the edited surface and inject a ``CoveringResult``. This
module also owns the proactive post-edit syntax probe (``syntax_result`` /
``GT_EDIT_CHECK``), the attribution of the model's OWN failing test to the
edited surface (``covering_red``), and the failure-fingerprint recovery
governor (``recovery`` / ``GT_HYPOTHESIS``). All deterministic, LLM-free,
bounded.

Correct-or-quiet: no graph, no GT_VERIFY_EXECUTE, no edited source, no selected
test, or a non-failing run all yield ``None`` (the feature stays quiet).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from gt_engine.language_registry import INDEXABLE_SOURCE_SUFFIXES

_SOURCE_EXTS = INDEXABLE_SOURCE_SUFFIXES


def _repo_relative(path: str, repo_root: str) -> str | None:
    try:
        root = os.path.realpath(os.path.abspath(repo_root))
        abs_path = os.path.realpath(
            os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))
        )
        if os.path.commonpath((root, abs_path)) != root:
            return None
        return os.path.relpath(abs_path, root).replace("\\", "/")
    except (OSError, ValueError):
        return None


def _symbols_for_files(graph_db: str, files: tuple[str, ...], repo_root: str) -> tuple[str, ...]:
    """Symbols defined in the edited files, resolved from the graph."""
    if not graph_db or not os.path.isfile(graph_db) or not files:
        return ()
    rels = [
        rel for rel in (_repo_relative(f, repo_root) for f in files)
        if rel
    ]
    if not rels:
        return ()
    try:
        con = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True)
        try:
            out: set[str] = set()
            for rel in rels:
                for (name,) in con.execute(
                    "SELECT name FROM nodes WHERE file_path = ?", (rel,)
                ).fetchall():
                    if name:
                        out.add(str(name))
            return tuple(sorted(out))
        finally:
            con.close()
    except sqlite3.Error:
        return ()


_TEST_FAILURE_FILE_RE = __import__("re").compile(
    r"(?m)([A-Za-z0-9_./-]+\.py)(?::\d+|::)"
)


def _failing_test_files(output: str) -> tuple[str, ...]:
    """Test source files named in a failing test's output (best-effort)."""
    return tuple(dict.fromkeys(_TEST_FAILURE_FILE_RE.findall(output or "")))


def attribute_test_failure(adapter, command: str, output: str, *, returncode):
    """Attribute the model's OWN failing test to the edited surface.

    covering_red fires when a covering test fails BECAUSE of an edited file.
    Rather than requiring a separate covering run, a failing test whose output
    references an edited file (traceback frame / test path) IS the covering RED
    for that surface. Correct-or-quiet: no edit, no failing test, no file link.
    """
    if not returncode or not adapter._edited_files:
        return None
    if not command or not output:
        return None
    low_output = (output or "").lower()
    edited = sorted(adapter._edited_files)
    linked = [f for f in edited if f.lower() in low_output]
    if not linked:
        return None
    from groundtruth.runtime.gateway import CoveringResult

    test_files = _failing_test_files(output)
    return CoveringResult(
        target=linked[0],
        verdict="fail",
        body_lines=[
            line.strip() for line in (output or "").splitlines()[-12:]
            if line.strip()
        ] or ["failing test references the edited surface"],
        evidence=[],
        tier="WARNING",
        test_files=test_files,
    )


def run_syntax_probe(adapter, changed_files: tuple[str, ...]) -> str:
    """Proactive post-edit syntax check (syntax_result / GT_EDIT_CHECK).

    Reframed trigger: GT runs a bounded syntax probe on every edit of a
    checkable file, instead of waiting for the model to run a check. A broken
    edit is delivered as syntax evidence immediately. Correct-or-quiet: no
    edited .py files or a clean compile.
    """
    if os.environ.get("GT_VERIFY_EXECUTE", "").strip() != "1":
        return ""
    py_files = [f for f in changed_files if f.endswith(".py")]
    if not py_files:
        return ""
    lines: list[str] = []
    for rel in py_files[:3]:
        path = rel if os.path.isabs(rel) else os.path.join(adapter.repo_root or "", rel)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", path],
                capture_output=True, text=True, timeout=20,
                cwd=adapter.repo_root or None,
            )
        except Exception:  # noqa: BLE001 - a probe fault is correct-or-quiet
            continue
        if proc.returncode != 0:
            tail = [
                line.strip()
                for line in (proc.stderr or "").splitlines()[-8:]
                if line.strip()
            ]
            lines.append(f"{rel}: syntax error\n" + "\n".join(tail))
    return "\n".join(lines)


def run_newfile_precedent(adapter, created_files: tuple[str, ...]) -> str:
    """newfile_precedent / GT_CHANGE_SURFACE on an actual file creation.

    Reframed trigger: ANY file-creation edit delivers sibling/precedent
    evidence from the same directory (a bounded, decision-serving nudge),
    instead of the old failed_search + create combo. Correct-or-quiet: no
    created files.
    """
    lines: list[str] = []
    for rel in created_files[:3]:
        if not rel or not os.path.splitext(rel)[1]:
            continue
        try:
            directory = Path(adapter.repo_root) / os.path.dirname(rel)
            siblings = sorted(
                p.name for p in directory.glob(f"*{os.path.splitext(rel)[1]}")
                if p.name != os.path.basename(rel)
            )[:4]
        except OSError:
            siblings = []
        if siblings:
            revision = str(getattr(adapter, "repository_revision", "") or "unknown")
            lines.append(
                f"{rel}: advisory precedent revision={revision}; "
                "reason=same_directory,same_extension; inspect="
                + ", ".join(
                    f"{os.path.dirname(rel)}/{name}".lstrip("/") for name in siblings
                )
            )
    return "\n".join(lines)


def run_covering_lane(adapter, changed_files: tuple[str, ...]):
    """One bounded covering probe on the edited surface.

    Returns a ``CoveringResult`` when an executed covering test FAILS against
    the edited files; otherwise ``None`` (correct-or-quiet).
    """
    if os.environ.get("GT_VERIFY_EXECUTE", "").strip() != "1":
        return None
    if not adapter.graph_db:
        return None
    src = [
        path for path in changed_files
        if path and os.path.splitext(path)[1].lower() in _SOURCE_EXTS
    ]
    if not src:
        return None
    repo_root = adapter.repo_root or os.getcwd()
    try:
        from groundtruth.runtime.covering_runner import (
            run_covering_tests,
            select_covering_tests,
        )
        from groundtruth.runtime.gateway import CoveringResult
    except Exception:  # noqa: BLE001 - covering absent -> feature quiet
        return None

    symbols = _symbols_for_files(adapter.graph_db, tuple(src), repo_root)
    if not symbols:
        return None
    try:
        selected = select_covering_tests(
            adapter.graph_db, symbols, limit=2, repo_root=repo_root
        )
        files = [c["file"] for c in (selected or []) if c.get("file")]
    except Exception:  # noqa: BLE001
        files = []
    if not files:
        return None
    try:
        result = run_covering_tests(
            repo_root,
            files,
            per_file_timeout=20,
            total_budget_seconds=35,
        )
    except Exception:  # noqa: BLE001 - a covering fault must not crash the agent
        return None
    if not result or result.get("verdict") != "fail":
        return None
    return CoveringResult(
        target=src[0],
        verdict="fail",
        body_lines=(str(result.get("stdout_tail") or "").splitlines()
                    or ["covering test failed"]),
        evidence=[],
        tier="WARNING",
        test_files=tuple(result.get("ran") or files),
    )
