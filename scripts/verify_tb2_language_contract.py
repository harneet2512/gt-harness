#!/usr/bin/env python3
"""Fail-closed Terminal-Bench 2 authored-language coverage certificate."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.language_registry import (  # noqa: E402
    LanguageResolutionStatus,
    candidate_capabilities,
    resolve_language,
)

DEFAULT_CONTRACT = ROOT / "config" / "terminal_bench_2_language_contract.json"
_FILE_SUFFIX_TOKEN = re.compile(
    r"(?<![a-z0-9_.-])(?:[a-z0-9_+.-]+/)*[a-z0-9_+.-]+"
    r"(?P<suffix>\.[a-z][a-z0-9]{0,9})(?![a-z0-9])",
    re.IGNORECASE,
)


def _load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "gt.tb2.language-contract.v1":
        raise RuntimeError("language contract schema mismatch")
    witnesses = payload.get("witnesses")
    if not isinstance(witnesses, list) or not witnesses:
        raise RuntimeError("language contract has no witnesses")
    return payload


def _dataset_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _verify_dataset(payload: dict[str, Any], root: Path) -> dict[str, int]:
    if not root.is_dir():
        raise RuntimeError("dataset root missing")
    expected_revision = str(payload.get("dataset_commit") or "")
    if expected_revision:
        observed_revision = _dataset_revision(root)
        if observed_revision != expected_revision:
            raise RuntimeError(
                f"dataset revision mismatch: {observed_revision or 'unknown'}"
            )
    task_roots = sorted(
        path.parent for path in root.glob("*/task.toml") if path.is_file()
    )
    expected_count = int(payload.get("expected_task_count") or 0)
    if len(task_roots) != expected_count:
        raise RuntimeError(
            f"dataset task-count mismatch: observed={len(task_roots)} expected={expected_count}"
        )
    tasks = {path.name: path for path in task_roots}
    instructions: dict[str, str] = {}
    for witness in payload["witnesses"]:
        task_name = str(witness.get("task") or "")
        task_root = tasks.get(task_name)
        if task_root is None:
            raise RuntimeError(f"contract task missing: {task_name}")
        instruction_path = task_root / "instruction.md"
        instruction = instructions.setdefault(
            task_name,
            (
                instruction_path.read_text(encoding="utf-8", errors="replace")
                if instruction_path.is_file()
                else ""
            ),
        )
        witness_path = str(witness.get("path") or "")
        if witness_path.lower() not in instruction.lower():
            raise RuntimeError(
                f"instruction witness missing: task={task_name} path={witness_path}"
            )
    observed_suffix_counts: dict[str, int] = {}
    for task_name, task_root in tasks.items():
        instruction = instructions.setdefault(
            task_name,
            (task_root / "instruction.md").read_text(
                encoding="utf-8", errors="replace"
            ),
        )
        lowered = instruction.lower()
        for match in _FILE_SUFFIX_TOKEN.finditer(lowered):
            suffix = match.group("suffix")
            observed_suffix_counts[suffix] = observed_suffix_counts.get(suffix, 0) + 1
    declared_suffixes = payload.get("instruction_source_suffixes", {})
    unclassified_structural_suffixes = sorted(
        suffix
        for suffix in observed_suffix_counts
        if suffix not in declared_suffixes
        and any(
            capability.validation_relevant and capability.structural_required
            for capability in candidate_capabilities("fixture" + suffix)
        )
    )
    if unclassified_structural_suffixes:
        raise RuntimeError(
            "unclassified structural suffix: "
            + ", ".join(unclassified_structural_suffixes)
        )
    suffix_counts: dict[str, int] = {}
    for suffix, expected_language in declared_suffixes.items():
        count = observed_suffix_counts.get(str(suffix).lower(), 0)
        if not count:
            continue
        suffix_counts[suffix] = count
        candidates = candidate_capabilities("fixture" + suffix)
        matching = [
            capability
            for capability in candidates
            if capability.name == expected_language
        ]
        if not matching or not matching[0].structural_index:
            raise RuntimeError(
                "instruction source suffix unsupported: "
                f"suffix={suffix} language={expected_language}"
            )
    missing_suffixes = sorted(
        set(payload.get("instruction_source_suffixes", {})) - set(suffix_counts)
    )
    if missing_suffixes:
        raise RuntimeError(
            "pinned instruction source suffixes absent: " + ", ".join(missing_suffixes)
        )
    return dict(sorted(suffix_counts.items()))


def verify_contract(
    *,
    contract_path: str | Path = DEFAULT_CONTRACT,
    dataset_root: str | Path | None = None,
) -> dict[str, Any]:
    payload = _load_contract(Path(contract_path))
    missing_languages: list[str] = []
    non_structural_languages: list[str] = []
    ambiguous_samples: list[str] = []
    symbol_gaps: list[str] = []
    caller_gaps: list[str] = []
    resolved: dict[str, int] = {}
    for witness in payload["witnesses"]:
        path = str(witness.get("path") or "")
        expected = str(witness.get("language") or "")
        resolution = resolve_language(path, str(witness.get("sample") or ""))
        if resolution.status is not LanguageResolutionStatus.RESOLVED:
            ambiguous_samples.append(f"{witness.get('task')}:{path}")
            continue
        capability = resolution.capability
        if capability is None or capability.name != expected:
            missing_languages.append(expected)
            continue
        resolved[expected] = resolved.get(expected, 0) + 1
        if not capability.structural_index:
            non_structural_languages.append(expected)
        if witness.get("requires_symbols") and not capability.symbol_support:
            symbol_gaps.append(expected)
        if witness.get("requires_callers") and not capability.caller_support:
            caller_gaps.append(expected)
    failures = {
        "missing_languages": sorted(set(missing_languages)),
        "non_structural_languages": sorted(set(non_structural_languages)),
        "ambiguous_samples": sorted(set(ambiguous_samples)),
        "symbol_gaps": sorted(set(symbol_gaps)),
        "caller_gaps": sorted(set(caller_gaps)),
    }
    if any(failures.values()):
        raise RuntimeError("language contract failed: " + json.dumps(failures, sort_keys=True))
    instruction_source_suffix_counts: dict[str, int] = {}
    if dataset_root is not None:
        instruction_source_suffix_counts = _verify_dataset(payload, Path(dataset_root))
    return {
        "schema": payload["schema"],
        "dataset_repository": payload.get("dataset_repository"),
        "dataset_commit": payload.get("dataset_commit"),
        "expected_task_count": payload.get("expected_task_count"),
        "witness_count": len(payload["witnesses"]),
        "resolved_language_counts": dict(sorted(resolved.items())),
        **failures,
        "dataset_verified": dataset_root is not None,
        "instruction_source_suffix_counts": instruction_source_suffix_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--dataset-root", type=Path)
    args = parser.parse_args()
    try:
        receipt = verify_contract(
            contract_path=args.contract,
            dataset_root=args.dataset_root,
        )
    except Exception as exc:  # noqa: BLE001 - CLI gate is intentionally fail-closed
        print(f"TB2_LANGUAGE_CONTRACT_FAILED {type(exc).__name__}: {exc}")
        return 1
    print("TB2_LANGUAGE_CONTRACT_PROVEN")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
