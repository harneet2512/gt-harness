"""Validate globally unique failure-class definitions in repository text.

References may repeat. Definitions may not. A definition is either a Markdown
heading such as ``### FD-030 - title`` or a machine field named
``failure_id``. The next unused ID is calculated from every valid ID mention,
so a historical reference still reserves its identifier.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

SCHEMA = "failure-id-validation.v1"
ID_PATTERN = re.compile(r"\bFD-(\d{3})\b")
HEADING_DEFINITION = re.compile(r"^\s*#{1,6}\s+(FD-\d{3})\s*(?:-|:)")
HEADING_CANDIDATE = re.compile(r"^\s*#{1,6}\s+(FD-\d+)\s*(?:-|:)")
FIELD_DEFINITION = re.compile(r"^\s*(?:[{,]\s*)?[\"']?failure_id[\"']?\s*[:=]\s*[\"']?(FD-\d{3})\b")
FIELD_CANDIDATE = re.compile(r"^\s*(?:[{,]\s*)?[\"']?failure_id[\"']?\s*[:=]\s*[\"']?(FD-\d+)\b")

TEXT_SUFFIXES = frozenset(
    {
        ".go",
        ".json",
        ".jsonl",
        ".md",
        ".ps1",
        ".py",
        ".sh",
        ".text",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "vendor",
    }
)


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in EXCLUDED_DIRECTORIES for part in relative_parts):
            continue
        yield path


def _display_path(path: Path, roots: Sequence[Path]) -> str:
    matching_roots = [root for root in roots if path == root or root in path.parents]
    if not matching_roots:
        return path.as_posix()
    root = max(matching_roots, key=lambda candidate: len(candidate.parts))
    if root.is_file():
        return root.name
    relative = path.relative_to(root).as_posix()
    if len(roots) == 1:
        return relative
    return f"{root.name}/{relative}"


def _next_unused(observed_numbers: set[int]) -> str:
    number = max(observed_numbers, default=0) + 1
    return f"FD-{number:03d}"


def validate(roots: Sequence[str | Path], *, expected_next: str | None = None) -> dict[str, Any]:
    """Return a deterministic validation report for ``roots``."""

    resolved_roots = [Path(root).resolve() for root in roots]
    definitions: dict[str, list[str]] = defaultdict(list)
    malformed: list[str] = []
    unreadable: list[str] = []
    observed_numbers: set[int] = set()
    files_scanned = 0

    for root in resolved_roots:
        if not root.exists():
            unreadable.append(f"{root.as_posix()}:not_found")
            continue
        for path in _iter_files(root):
            display = _display_path(path, resolved_roots)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                unreadable.append(f"{display}:{type(exc).__name__}")
                continue
            files_scanned += 1
            for match in ID_PATTERN.finditer(text):
                observed_numbers.add(int(match.group(1)))
            for line_number, line in enumerate(text.splitlines(), start=1):
                definition = HEADING_DEFINITION.match(line) or FIELD_DEFINITION.match(line)
                if definition:
                    definitions[definition.group(1)].append(f"{display}:{line_number}")
                    continue
                candidate = HEADING_CANDIDATE.match(line) or FIELD_CANDIDATE.match(line)
                if candidate:
                    malformed.append(f"{display}:{line_number}:{candidate.group(1)}")

    ordered_definitions = {
        failure_id: sorted(locations) for failure_id, locations in sorted(definitions.items())
    }
    duplicates = {
        failure_id: locations
        for failure_id, locations in ordered_definitions.items()
        if len(locations) > 1
    }
    next_unused = _next_unused(observed_numbers)
    expected_next_valid = expected_next is None or expected_next == next_unused
    expected_next_format_valid = expected_next is None or bool(
        re.fullmatch(r"FD-\d{3}", expected_next)
    )
    status = (
        "pass"
        if not duplicates
        and not malformed
        and not unreadable
        and expected_next_valid
        and expected_next_format_valid
        else "fail"
    )

    return {
        "schema": SCHEMA,
        "status": status,
        "roots": sorted(root.as_posix() for root in resolved_roots),
        "files_scanned": files_scanned,
        "definitions": ordered_definitions,
        "duplicate_definitions": duplicates,
        "malformed_definitions": sorted(malformed),
        "unreadable_files": sorted(unreadable),
        "next_unused_id": next_unused,
        "expected_next_id": expected_next,
        "expected_next_matches": expected_next_valid and expected_next_format_valid,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail on duplicate or malformed FD-NNN definitions in repository text."
    )
    parser.add_argument("roots", nargs="+", help="Files or repository roots to scan")
    parser.add_argument(
        "--expected-next",
        help="Fail unless this is the first unused ID across all mentions (for example FD-030)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = validate(args.roots, expected_next=args.expected_next)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
