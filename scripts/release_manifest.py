"""Canonical, content-addressed GroundTruth release identity."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gt_engine.repository_identity import matches_repository_file_sha256

ACTIVE_RELEASE_PATH = Path("eval/release/active_release.json")


def _matches_checkout_sha256(path: Path, expected: str) -> bool:
    """Compare against platform-stable repository bytes."""

    return matches_repository_file_sha256(path, expected)


def _full_sha(value: object, *, field: str) -> str:
    text = str(value or "").lower()
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{field} is not a full commit SHA")
    return text


def _resolve_release_path(root: Path, value: object, *, field: str) -> tuple[str, Path]:
    relative = str(value or "").replace("\\", "/")
    if not relative or Path(relative).is_absolute():
        raise ValueError(f"{field} path is invalid")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{field} path is outside release root") from exc
    if not resolved.is_file():
        raise ValueError(f"{field} file is missing: {relative}")
    return relative, resolved


@dataclass(frozen=True)
class ReleaseManifest:
    path: Path
    release_id: str
    task_profile: str
    runtime_commit: str
    prediction_path: Path
    baseline_path: Path
    treatment_path: Path
    prediction_relative: str
    baseline_relative: str
    treatment_relative: str
    allowed_post_runtime_paths: tuple[str, ...]
    payload: dict[str, Any]


def load_release_manifest(
    path: Path = ACTIVE_RELEASE_PATH,
    *,
    root: Path | None = None,
) -> ReleaseManifest:
    manifest_path = path.resolve()
    release_root = (root or Path.cwd()).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "gt.release_manifest.v1":
        raise ValueError("release manifest schema is not gt.release_manifest.v1")
    release_id = str(payload.get("release_id") or "")
    task_profile = str(payload.get("task_profile") or "")
    if not release_id or not task_profile:
        raise ValueError("release manifest identity is incomplete")
    runtime_commit = _full_sha(payload.get("runtime_commit"), field="runtime_commit")

    resolved: dict[str, tuple[str, Path]] = {}
    for field in ("prediction", "baseline", "treatment"):
        entry = payload.get(field)
        if not isinstance(entry, dict):
            raise ValueError(f"release manifest {field} entry is missing")
        relative, artifact = _resolve_release_path(
            release_root, entry.get("path"), field=field
        )
        expected_hash = str(entry.get("sha256") or "").lower()
        if len(expected_hash) != 64 or not _matches_checkout_sha256(
            artifact, expected_hash
        ):
            raise ValueError(f"{field} sha256 mismatch")
        resolved[field] = (relative, artifact)

    allowed = tuple(
        str(item).replace("\\", "/")
        for item in payload.get("allowed_post_runtime_paths") or ()
    )
    if not allowed or len(allowed) != len(set(allowed)):
        raise ValueError("release manifest allowed post-runtime paths are invalid")
    for relative in allowed:
        _resolve_release_path(release_root, relative, field="allowed_post_runtime")

    return ReleaseManifest(
        path=manifest_path,
        release_id=release_id,
        task_profile=task_profile,
        runtime_commit=runtime_commit,
        prediction_path=resolved["prediction"][1],
        baseline_path=resolved["baseline"][1],
        treatment_path=resolved["treatment"][1],
        prediction_relative=resolved["prediction"][0],
        baseline_relative=resolved["baseline"][0],
        treatment_relative=resolved["treatment"][0],
        allowed_post_runtime_paths=allowed,
        payload=payload,
    )
