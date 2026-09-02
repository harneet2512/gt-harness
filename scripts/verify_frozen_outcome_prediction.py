"""Fail-closed verification for the pre-outcome Terminal-Bench prediction.

This is a benchmark-integrity check, not an outcome evaluator.  It verifies
that the prediction was frozen before the candidate run, that it describes the
selected frozen profile exactly, and that the runtime proof commit is present
in the checked-out treatment commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.repository_identity import repository_file_sha256  # noqa: E402
from scripts.release_manifest import (  # noqa: E402
    ReleaseManifest,
    load_release_manifest,
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return repository_file_sha256(path)


def _task_set_sha256(tasks: list[str]) -> str:
    canonical = "\n".join(sorted(str(task) for task in tasks)) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _profile_tasks(baseline: dict[str, Any], profile_id: str) -> tuple[list[str], dict[str, Any]]:
    profiles = (baseline.get("manifest") or {}).get("profiles") or {}
    if profile_id not in profiles:
        raise ValueError(f"unknown frozen comparison profile: {profile_id}")
    profile = profiles[profile_id]
    if profile.get("task_source") == "all_rows":
        tasks = sorted(str(row["task"]) for row in baseline.get("rows") or [])
    else:
        tasks = sorted(str(task) for task in profile.get("task_ids") or [])
    return tasks, profile


def _is_ancestor(commit: str, current: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, current],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _changed_paths(commit: str, current: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{commit}..{current}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    )


def _validate_post_runtime_paths(
    *,
    changed_paths: tuple[str, ...],
    allowed_paths: tuple[str, ...],
) -> None:
    allowed = {str(path).replace("\\", "/") for path in allowed_paths}
    unexpected = sorted(set(changed_paths) - allowed)
    if unexpected:
        raise ValueError(
            "runtime or harness changed after prediction freeze: "
            + ", ".join(unexpected)
        )


def _validate_release_freeze_paths(
    *,
    manifest_relative: str,
    prediction_relative: str,
    allowed_paths: tuple[str, ...],
) -> None:
    expected = {manifest_relative, prediction_relative}
    observed = {str(path).replace("\\", "/") for path in allowed_paths}
    if observed != expected or len(allowed_paths) != 2:
        raise ValueError(
            "release freeze may allow only its manifest and prediction artifact"
        )


def verify(
    *,
    prediction_path: Path,
    baseline_path: Path,
    profile_id: str,
    current_commit: str,
    allowed_post_runtime_paths: tuple[str, ...] | None = None,
    expected_runtime_commit: str | None = None,
) -> dict[str, Any]:
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if prediction.get("prediction_before_outcome") is not True:
        raise ValueError("prediction is not marked as frozen before outcome")
    if prediction.get("outcome_run_executed") is not False:
        raise ValueError("prediction already claims an outcome run was executed")

    tasks, profile = _profile_tasks(baseline, profile_id)
    expected_digest = _task_set_sha256(tasks)
    if len(tasks) != int(profile.get("task_count", -1)):
        raise ValueError("frozen profile task count does not match its task list")
    if expected_digest != str(profile.get("task_set_sha256") or ""):
        raise ValueError("frozen profile task-set hash is invalid")
    if prediction.get("task_profile") != profile_id:
        raise ValueError("prediction profile does not match selected profile")
    if prediction.get("task_count") != len(tasks):
        raise ValueError("prediction task count does not match selected profile")
    if prediction.get("task_order") != tasks:
        raise ValueError("prediction task order does not match selected profile")

    rows = prediction.get("predictions")
    if not isinstance(rows, list) or len(rows) != len(tasks):
        raise ValueError("prediction rows do not cover the selected profile")
    row_tasks = [row.get("task") for row in rows if isinstance(row, dict)]
    if row_tasks != tasks:
        raise ValueError("prediction rows are not in the frozen task order")
    if any(not isinstance(row.get("predicted_solved"), bool) for row in rows):
        raise ValueError("every prediction row needs a boolean predicted_solved")

    aggregate = prediction.get("aggregate_prediction") or {}
    predicted_solved = sum(bool(row["predicted_solved"]) for row in rows)
    if aggregate.get("point_solved") != predicted_solved:
        raise ValueError("aggregate point_solved disagrees with prediction rows")
    if aggregate.get("point_resolve_rate") != predicted_solved / len(tasks):
        raise ValueError("aggregate point_resolve_rate disagrees with prediction rows")

    baseline_digest = _sha256(baseline_path)
    source_baseline = (prediction.get("source_artifacts") or {}).get("frozen_gt_off") or {}
    if source_baseline.get("sha256") != baseline_digest:
        raise ValueError("prediction does not bind the checked-in frozen baseline hash")

    proof_commit = str(prediction.get("candidate_runtime_proof_commit") or "")
    runtime_commit = str(prediction.get("candidate_runtime_commit") or "")
    if expected_runtime_commit is not None and runtime_commit != expected_runtime_commit:
        raise ValueError("prediction runtime commit disagrees with release manifest")
    commits = (
        ("candidate runtime proof", proof_commit),
        ("candidate runtime", runtime_commit),
    )
    for label, commit in commits:
        if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit.lower()):
            raise ValueError(f"{label} is not a full commit SHA")
        if not _is_ancestor(commit, current_commit):
            raise ValueError(f"{label} commit is not an ancestor of the treatment commit")
    if prediction.get("schema") == "gt.final_20_task_outcome_prediction.v2":
        allowed_paths = (
            allowed_post_runtime_paths
            if allowed_post_runtime_paths is not None
            else tuple(prediction.get("allowed_post_runtime_paths") or ())
        )
        if not allowed_paths:
            raise ValueError("v2 prediction has no allowed post-runtime paths")
        prediction_relative = prediction_path.as_posix()
        try:
            prediction_relative = prediction_path.resolve().relative_to(
                Path.cwd().resolve()
            ).as_posix()
        except ValueError:
            pass
        if prediction_relative not in allowed_paths:
            raise ValueError("prediction artifact is not in allowed post-runtime paths")
        _validate_post_runtime_paths(
            changed_paths=_changed_paths(runtime_commit, current_commit),
            allowed_paths=allowed_paths,
        )

    return {
        "schema": "gt.frozen_outcome_prediction_proof.v1",
        "prediction_path": prediction_path.as_posix(),
        "prediction_sha256": _sha256(prediction_path),
        "profile_id": profile_id,
        "task_count": len(tasks),
        "task_set_sha256": expected_digest,
        "prediction_before_outcome": True,
        "outcome_run_executed": False,
        "candidate_runtime_commit": runtime_commit,
        "candidate_runtime_proof_commit": proof_commit,
        "predicted_solved": predicted_solved,
        "post_runtime_paths_verified": (
            prediction.get("schema") == "gt.final_20_task_outcome_prediction.v2"
        ),
    }


def verify_release_manifest(
    *,
    manifest_path: Path,
    current_commit: str,
    root: Path | None = None,
    expected_profile: str | None = None,
) -> dict[str, Any]:
    release: ReleaseManifest = load_release_manifest(manifest_path, root=root)
    if expected_profile is not None and release.task_profile != expected_profile:
        raise ValueError("selected profile disagrees with release manifest")
    release_root = (root or Path.cwd()).resolve()
    try:
        manifest_relative = manifest_path.resolve().relative_to(release_root).as_posix()
    except ValueError as exc:
        raise ValueError("release manifest is outside release root") from exc
    if manifest_relative not in release.allowed_post_runtime_paths:
        raise ValueError("release manifest is not in allowed post-runtime paths")
    _validate_release_freeze_paths(
        manifest_relative=manifest_relative,
        prediction_relative=release.prediction_relative,
        allowed_paths=release.allowed_post_runtime_paths,
    )
    proof = verify(
        prediction_path=release.prediction_path,
        baseline_path=release.baseline_path,
        profile_id=release.task_profile,
        current_commit=current_commit,
        allowed_post_runtime_paths=release.allowed_post_runtime_paths,
        expected_runtime_commit=release.runtime_commit,
    )
    prediction = json.loads(release.prediction_path.read_text(encoding="utf-8"))
    if str(prediction.get("candidate_runtime_proof_commit") or "") != release.runtime_commit:
        raise ValueError("prediction proof commit disagrees with release manifest")
    return {
        **proof,
        "release_manifest_path": manifest_relative,
        "release_manifest_sha256": _sha256(manifest_path),
        "release_id": release.release_id,
        "treatment_path": release.treatment_relative,
        "treatment_sha256": _sha256(release.treatment_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--prediction", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--current-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.release_manifest is not None:
        proof = verify_release_manifest(
            manifest_path=args.release_manifest,
            current_commit=args.current_commit,
            expected_profile=args.profile,
        )
    else:
        if args.prediction is None or args.baseline is None or not args.profile:
            parser.error(
                "--release-manifest or --prediction/--baseline/--profile is required"
            )
        proof = verify(
            prediction_path=args.prediction,
            baseline_path=args.baseline,
            profile_id=args.profile,
            current_commit=args.current_commit,
        )
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
