"""Fail unless every operative RED producer uses the canonical capture CLI."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "gt.red_evidence_producer_check.v1"
MANIFEST_SCHEMA = "gt.red_evidence_producers.v2"
REQUIRED_TOKENS = (
    "scripts/red_evidence.py capture",
    "--evidence-dir",
    'PYTHON_EXE="${pythonLocation}/bin/python"',
    "scripts/check_red_evidence_producers.py",
)
FORBIDDEN_TOKENS = (" sed ", " awk ", " perl ", "<DURATION>")


class _DuplicateKey(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def validate(root: str | Path) -> dict[str, Any]:
    resolved = Path(root).resolve()
    errors: list[str] = []
    manifest_path = resolved / "config" / "red_evidence_producers.json"
    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (_DuplicateKey, OSError, UnicodeError, json.JSONDecodeError):
        manifest = None
        errors.append("manifest:invalid")
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema",
        "canonical_library",
        "operative_producers",
        "historical_roots",
        "historical_producers",
    }:
        errors.append("manifest:invalid_schema")
        manifest = {}
    if manifest.get("schema") != MANIFEST_SCHEMA:
        errors.append("manifest:unexpected_schema")
    canonical = manifest.get("canonical_library")
    if not isinstance(canonical, str) or not (resolved / canonical).is_file():
        errors.append("canonical_library:missing")
    operative = manifest.get("operative_producers")
    if (
        not isinstance(operative, list)
        or not operative
        or any(not isinstance(path, str) for path in operative)
        or operative != sorted(set(operative))
    ):
        errors.append("operative_producers:invalid")
        operative = []
    for logical in operative:
        path = resolved / logical
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append(f"operative:unreadable:{logical}")
            continue
        for token in REQUIRED_TOKENS:
            if token not in text:
                errors.append(f"operative:missing_canonical_token:{logical}:{token}")
        if re.search(r"(?m)^\s*python(?:3)?(?:\s|$)", text):
            errors.append(f"operative:bare_python:{logical}")
        padded = f" {text.lower()} "
        for token in FORBIDDEN_TOKENS:
            if token.lower() in padded:
                errors.append(f"operative:private_normalizer:{logical}:{token.strip()}")
    workflows = resolved / ".github" / "workflows"
    discovered: list[str] = []
    if workflows.is_dir():
        for path in sorted(workflows.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".yml", ".yaml", ".sh"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                errors.append(f"discovery:unreadable:{path.relative_to(resolved).as_posix()}")
                continue
            logical = path.relative_to(resolved).as_posix()
            if (
                "red" in path.name.lower()
                or "scripts/red_evidence.py" in text
                or "gt.red_evidence" in text
                or "RED evidence" in text
            ):
                discovered.append(logical)
    discovered.sort()
    if discovered != operative:
        errors.append("operative_producers:inventory_mismatch")
    historical_producers = manifest.get("historical_producers")
    expected_historical_paths = {
        ".githooks/tests/cha_rta_boundary_red.sh",
        ".githooks/tests/cha_rta_fixed_point_red.sh",
        ".githooks/tests/cha_rta_receiver_scope_red.sh",
        ".githooks/tests/cha_rta_root_completeness_red.sh",
        ".githooks/tests/cha_rta_root_policy_red.sh",
        ".githooks/tests/cha_rta_roots_red.sh",
        ".githooks/tests/vta_step5_candidate_proof_red.sh",
        ".githooks/tests/vta_step5_closed_boundary_red.sh",
        ".githooks/tests/vta_step5_evidence_red.sh",
        ".githooks/tests/vta_step5_scope_red.sh",
        ".githooks/tests/vta_step5_typed_fact_refs_red.sh",
        ".githooks/tests/vta_step5_typed_facts_red.sh",
        ".githooks/tests/vta_step5_typed_provenance_red.sh",
        ".githooks/tests/vta_variable_flow_red.sh",
    }
    if (
        not isinstance(historical_producers, list)
        or len(historical_producers) != len(expected_historical_paths)
        or any(
            not isinstance(item, dict)
            or set(item) != {"repository", "commit", "path", "status", "replay_disposition"}
            or item.get("repository") != "harneet2512/groundtruth"
            or item.get("status") != "frozen_historical"
            or item.get("path") not in expected_historical_paths
            or not re.fullmatch(r"[0-9a-f]{40}", item.get("commit", ""))
            or item.get("replay_disposition") not in {"inventory_only", "representative_replay"}
            for item in (historical_producers or [])
        )
        or {item.get("path") for item in (historical_producers or [])} != expected_historical_paths
    ):
        errors.append("historical_producers:invalid")
    historical = manifest.get("historical_roots")
    if (
        not isinstance(historical, list)
        or not historical
        or any(not isinstance(path, str) or not (resolved / path).is_dir() for path in historical)
    ):
        errors.append("historical_roots:invalid")
        historical = []
    if any(
        path == root or path.startswith(f"{root}/") for path in operative for root in historical
    ):
        errors.append("historical_root_marked_operative")
    return {
        "schema": SCHEMA,
        "status": "pass" if not errors else "fail",
        "errors": sorted(set(errors)),
        "canonical_library": canonical,
        "operative_producers": operative,
        "historical_roots": historical,
        "historical_producers": (
            historical_producers if isinstance(historical_producers, list) else []
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    report = validate(parser.parse_args().root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
