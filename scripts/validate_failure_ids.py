"""Validate globally unique failure-class definitions in repository text.

References may repeat. Definitions may not. A definition is either a Markdown
heading such as ``### FD-030 - title`` or a machine field named
``failure_id``. Without a pinned ledger, references reserve IDs for local
allocation. With a pinned ledger, its signed allocation boundary controls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

SCHEMA = "failure-id-validation.v1"
SNAPSHOT_SCHEMA = "failure-id-ledger-snapshot.v1"
ID_PATTERN = re.compile(r"\bFD-(\d{3})\b")
HEADING_DEFINITION = re.compile(r"^\s*#{1,6}\s+(FD-\d{3})\s*(?:-|:)")
HEADING_CANDIDATE = re.compile(r"^\s*#{1,6}\s+(FD-\d+)\s*(?:-|:)")
FIELD_DEFINITION = re.compile(r"(?:^|[{,\s-])[\"']?failure_id[\"']?\s*[:=]\s*[\"']?(FD-\d{3})\b")
FIELD_CANDIDATE = re.compile(r"(?:^|[{,\s-])[\"']?failure_id[\"']?\s*[:=]\s*[\"']?(FD-\d+)\b")

TEXT_SUFFIXES = frozenset(
    {
        ".go",
        ".json",
        ".jsonl",
        ".md",
        ".ps1",
        ".py",
        ".receipt",
        ".sh",
        ".text",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
MACHINE_FIELD_SUFFIXES = frozenset({".json", ".jsonl", ".receipt", ".toml", ".yaml", ".yml"})
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


class _DuplicateJsonKeyError(ValueError):
    """Raised before a snapshot's duplicate JSON key can be collapsed."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


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


def _display_path(path: Path, root: Path, root_index: int, root_count: int) -> str:
    if root.is_file():
        relative = root.name
    else:
        relative = path.relative_to(root).as_posix()
    if root_count == 1:
        return relative
    return f"root-{root_index + 1}/{relative}"


def _next_unused(observed_numbers: set[int]) -> str:
    number = max(observed_numbers, default=0) + 1
    return f"FD-{number:03d}"


def _snapshot_payload_sha256(snapshot: dict[str, Any]) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "payload_sha256"}
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_snapshot(
    path: str | Path,
    expected_revision: str | None,
    expected_payload_sha256: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    snapshot_path = Path(path)
    if not snapshot_path.is_file():
        return None, ["snapshot:not_found"]
    try:
        snapshot = json.loads(
            snapshot_path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except _DuplicateJsonKeyError:
        return None, ["snapshot:duplicate_key"]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, ["snapshot:invalid_json"]
    if not isinstance(snapshot, dict):
        return None, ["snapshot:invalid_schema"]

    errors: list[str] = []
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        errors.append("snapshot:invalid_schema")
    computed_hash = _snapshot_payload_sha256(snapshot)
    declared_hash = snapshot.get("payload_sha256")
    if not isinstance(declared_hash, str) or declared_hash != computed_hash:
        errors.append("snapshot:payload_sha256_mismatch")
    if expected_payload_sha256 is None:
        errors.append("snapshot:expected_payload_sha256_required")
    elif (
        not re.fullmatch(r"[0-9a-f]{64}", expected_payload_sha256)
        or computed_hash != expected_payload_sha256
    ):
        errors.append("snapshot:unexpected_payload_sha256")

    source = snapshot.get("source")
    if (
        not isinstance(source, dict)
        or source.get("issue") != "HAR-55"
        or not isinstance(source.get("observed_updated_at"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("description_sha256", "")))
        or source.get("revision") != f"sha256:{source.get('description_sha256', '')}"
    ):
        errors.append("snapshot:invalid_source")
    elif expected_revision is not None and source["revision"] != expected_revision:
        errors.append("snapshot:unexpected_source_revision")

    canonical = snapshot.get("canonical_definitions")
    if not isinstance(canonical, dict) or not canonical:
        errors.append("snapshot:invalid_canonical_definitions")
        canonical = {}
    elif any(
        not re.fullmatch(r"FD-\d{3}", str(failure_id)) or not isinstance(title, str) or not title
        for failure_id, title in canonical.items()
    ):
        errors.append("snapshot:invalid_canonical_definitions")

    canonical_numbers = sorted(int(failure_id[3:]) for failure_id in canonical)
    last_allocated = snapshot.get("last_allocated_id")
    next_unused = snapshot.get("next_unused_id")
    if (
        not canonical_numbers
        or last_allocated != f"FD-{canonical_numbers[-1]:03d}"
        or next_unused != f"FD-{canonical_numbers[-1] + 1:03d}"
    ):
        errors.append("snapshot:invalid_allocation_boundary")
    if canonical_numbers and canonical_numbers != list(range(1, canonical_numbers[-1] + 1)):
        errors.append("snapshot:allocation_gap")

    legacy = snapshot.get("legacy_ambiguities")
    if not isinstance(legacy, dict) or any(
        failure_id not in canonical
        or not isinstance(titles, list)
        or len(titles) < 2
        or any(not isinstance(title, str) or not title for title in titles)
        for failure_id, titles in (legacy.items() if isinstance(legacy, dict) else ())
    ):
        errors.append("snapshot:invalid_legacy_ambiguities")

    return snapshot, sorted(set(errors))


def validate(
    roots: Sequence[str | Path],
    *,
    expected_next: str | None = None,
    ledger_snapshot: str | Path | None = None,
    expected_ledger_revision: str | None = None,
    expected_ledger_payload_sha256: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic validation report for ``roots``."""

    resolved_roots = [Path(root).resolve() for root in roots]
    definitions: dict[str, list[str]] = defaultdict(list)
    malformed: list[str] = []
    unreadable: list[str] = []
    observed_numbers: set[int] = set()
    references: dict[str, list[str]] = defaultdict(list)
    files_scanned = 0
    snapshot: dict[str, Any] | None = None
    snapshot_errors: list[str] = []
    snapshot_path: Path | None = None
    if ledger_snapshot is not None:
        snapshot_path = Path(ledger_snapshot).resolve()
        snapshot, snapshot_errors = _load_snapshot(
            snapshot_path,
            expected_ledger_revision,
            expected_ledger_payload_sha256,
        )
    elif expected_ledger_revision is not None or expected_ledger_payload_sha256 is not None:
        snapshot_errors = ["snapshot:expectation_without_snapshot"]

    for root_index, root in enumerate(resolved_roots):
        if not root.exists():
            label = "." if len(resolved_roots) == 1 else f"root-{root_index + 1}"
            unreadable.append(f"{label}:not_found")
            continue
        for path in _iter_files(root):
            if snapshot_path is not None and path.resolve() == snapshot_path:
                continue
            display = _display_path(path, root, root_index, len(resolved_roots))
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                unreadable.append(f"{display}:{type(exc).__name__}")
                continue
            files_scanned += 1
            for match in ID_PATTERN.finditer(text):
                failure_id = f"FD-{match.group(1)}"
                observed_numbers.add(int(match.group(1)))
                line_number = text.count("\n", 0, match.start()) + 1
                references[failure_id].append(f"{display}:{line_number}")
            for line_number, line in enumerate(text.splitlines(), start=1):
                heading_definition = HEADING_DEFINITION.match(line)
                field_definitions = (
                    list(FIELD_DEFINITION.finditer(line))
                    if path.suffix.lower() in MACHINE_FIELD_SUFFIXES
                    else []
                )
                if heading_definition:
                    definitions[heading_definition.group(1)].append(f"{display}:{line_number}")
                for definition in field_definitions:
                    definitions[definition.group(1)].append(f"{display}:{line_number}")
                heading_candidate = HEADING_CANDIDATE.match(line)
                field_candidates = (
                    list(FIELD_CANDIDATE.finditer(line))
                    if path.suffix.lower() in MACHINE_FIELD_SUFFIXES
                    else []
                )
                if heading_candidate and not re.fullmatch(r"FD-\d{3}", heading_candidate.group(1)):
                    malformed.append(f"{display}:{line_number}:{heading_candidate.group(1)}")
                for candidate in field_candidates:
                    if not re.fullmatch(r"FD-\d{3}", candidate.group(1)):
                        malformed.append(f"{display}:{line_number}:{candidate.group(1)}")

    ordered_definitions = {
        failure_id: sorted(locations) for failure_id, locations in sorted(definitions.items())
    }
    duplicates = {
        failure_id: locations
        for failure_id, locations in ordered_definitions.items()
        if len(locations) > 1
    }
    canonical = snapshot.get("canonical_definitions", {}) if snapshot else {}
    legacy_ambiguities = snapshot.get("legacy_ambiguities", {}) if snapshot else {}
    snapshot_conflicts = {
        failure_id: locations
        for failure_id, locations in ordered_definitions.items()
        if failure_id in canonical
    }
    unallocated_definitions = {
        failure_id: locations
        for failure_id, locations in ordered_definitions.items()
        if snapshot and failure_id not in canonical
    }
    next_unused = str(snapshot["next_unused_id"]) if snapshot else _next_unused(observed_numbers)
    allowed_references = set(canonical)
    if snapshot:
        allowed_references.add(next_unused)
    unknown_references = {
        failure_id: sorted(locations)
        for failure_id, locations in sorted(references.items())
        if snapshot and failure_id not in allowed_references
    }
    expected_next_valid = expected_next is None or expected_next == next_unused
    expected_next_format_valid = expected_next is None or bool(
        re.fullmatch(r"FD-\d{3}", expected_next)
    )
    status = (
        "pass"
        if not duplicates
        and not malformed
        and not unreadable
        and not snapshot_errors
        and not snapshot_conflicts
        and not unallocated_definitions
        and not unknown_references
        and expected_next_valid
        and expected_next_format_valid
        else "fail"
    )

    return {
        "schema": SCHEMA,
        "status": status,
        "roots": ["."]
        if len(resolved_roots) == 1
        else [f"root-{index + 1}" for index in range(len(resolved_roots))],
        "files_scanned": files_scanned,
        "definitions": ordered_definitions,
        "duplicate_definitions": duplicates,
        "malformed_definitions": sorted(malformed),
        "unreadable_files": sorted(unreadable),
        "snapshot_errors": snapshot_errors,
        "snapshot_conflicts": snapshot_conflicts,
        "unallocated_definitions": unallocated_definitions,
        "legacy_ambiguities": legacy_ambiguities,
        "unknown_references": unknown_references,
        "ledger_snapshot": (
            {
                "schema": snapshot.get("schema"),
                "source": snapshot.get("source"),
                "payload_sha256": snapshot.get("payload_sha256"),
            }
            if snapshot
            else None
        ),
        "next_unused_id": next_unused,
        "expected_next_id": expected_next,
        "expected_next_matches": expected_next_valid and expected_next_format_valid,
        "expected_ledger_payload_sha256": expected_ledger_payload_sha256,
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
    parser.add_argument(
        "--ledger-snapshot",
        help="Pinned HAR-55 ledger snapshot used for global allocation and legacy cutover",
    )
    parser.add_argument(
        "--expected-ledger-revision",
        help="Fail unless the snapshot carries this exact content-derived HAR-55 revision",
    )
    parser.add_argument(
        "--expected-ledger-payload-sha256",
        help=(
            "Required with --ledger-snapshot; fail unless independently pinned content "
            "matches this SHA-256"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = validate(
        args.roots,
        expected_next=args.expected_next,
        ledger_snapshot=args.ledger_snapshot,
        expected_ledger_revision=args.expected_ledger_revision,
        expected_ledger_payload_sha256=args.expected_ledger_payload_sha256,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
