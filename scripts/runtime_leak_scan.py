"""Static, provider-free anti-leak checks for the canonical runtime closure."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_SUSPICIOUS_PATH = re.compile(
    r"(?i)(?:reference[\s_.-]*patch|model[\s_.-]*\.patch|expected[\s_.-]*(?:file|path)|"
    r"benchmark[\s_.-]*(?:task|oracle)|(?:^|[/\\])oracle(?:[/\\.]|$))"
)
_FORBIDDEN_IMPORT = re.compile(
    r"(?i)(?:^|\.)(?:oracle|reference_patch|gt_central_agent|pier_gt_adapter)$"
)
_ENTRYPOINTS = (
    "gt_harness.cli",
    "gt_harness.treatments",
    "gt_harness.miniswe_runner",
    "eval.harbor_gt_harness_adapter",
    "eval.pier_gt_harness_adapter",
    "eval.swe_live_lite_gt_harness_adapter",
)


@dataclass(frozen=True, slots=True)
class LeakFinding:
    path: str
    line: int
    reason: str
    detail: str


def _module_file(root: Path, module: str) -> Path | None:
    relative = Path(*module.split("."))
    candidate = root / f"{relative}.py"
    if candidate.is_file():
        return candidate
    package = root / relative / "__init__.py"
    return package if package.is_file() else None


def _internal_imports(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(dict.fromkeys(modules))


def canonical_runtime_paths(root: str | Path) -> tuple[Path, ...]:
    """Resolve the statically reachable in-repository canonical runtime files."""

    base = Path(root).resolve()
    pending = list(_ENTRYPOINTS)
    seen_modules: set[str] = set()
    paths: list[Path] = []
    while pending:
        module = pending.pop()
        if module in seen_modules or not (
            module.startswith("gt_harness.")
            or module.startswith("gt_engine.")
            or module.startswith("eval.")
        ):
            continue
        seen_modules.add(module)
        path = _module_file(base, module)
        if path is None:
            continue
        paths.append(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            continue
        pending.extend(_internal_imports(tree))
    return tuple(sorted(set(paths)))


def _literal_strings(node: ast.AST) -> Iterable[tuple[int, str]]:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.lineno, child.value


def scan_paths(
    paths: Iterable[str | Path], *, forbidden_values: Iterable[str] = ()
) -> tuple[LeakFinding, ...]:
    """Scan source files without treating comments/docstrings as runtime access.

    ``forbidden_values`` is supplied by a certification manifest at invocation
    time; no benchmark task or repository identity is embedded in this module.
    """

    forbidden = tuple(value for value in forbidden_values if str(value))
    findings: list[LeakFinding] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            findings.append(LeakFinding(str(path), 1, "source unreadable", type(exc).__name__))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _FORBIDDEN_IMPORT.search(alias.name):
                        findings.append(
                            LeakFinding(str(path), node.lineno, "forbidden import", alias.name)
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if _FORBIDDEN_IMPORT.search(node.module):
                    findings.append(
                        LeakFinding(str(path), node.lineno, "forbidden import", node.module)
                    )
            elif isinstance(node, ast.Call) and node.args:
                for line, value in _literal_strings(node):
                    if _SUSPICIOUS_PATH.search(value):
                        findings.append(
                            LeakFinding(str(path), line, "reference/oracle access", value)
                        )
            for line, value in _literal_strings(node):
                if any(value == forbidden_value for forbidden_value in forbidden):
                    findings.append(LeakFinding(str(path), line, "forbidden value", value))
    return tuple(dict.fromkeys(findings))


def scan_canonical_runtime(
    root: str | Path, *, forbidden_values: Iterable[str] = ()
) -> tuple[LeakFinding, ...]:
    return scan_paths(canonical_runtime_paths(root), forbidden_values=forbidden_values)


__all__ = ["LeakFinding", "canonical_runtime_paths", "scan_canonical_runtime", "scan_paths"]
