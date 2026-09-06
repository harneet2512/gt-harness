"""Deterministic bundle, plan, result, and provider-free acceptance contracts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from gt_engine.result_envelope import envelope_for_result, envelope_from_mapping
from gt_harness.canonical_io import atomic_json, canonical_json_bytes
from gt_harness.recorded_content import measure_recorded_content

PRODUCT_SOURCE_SCHEMA = "gt.product_bundle_source.v1"
PRODUCT_BUNDLE_SCHEMA = "gt.product_bundle.v1"
INSTALL_SCHEMA = "gt.install_attestation.v1"
PLAN_SCHEMA = "gt.benchmark_plan.v1"
TASK_RESULT_SCHEMA = "gt.benchmark_task_result.v1"
SUMMARY_SCHEMA = "gt.benchmark_summary.v1"
CLOSEOUT_SCHEMA = "gt.product_closeout.v1"

_SAFE_GT_ENV = frozenset({"GT_INDEX_BINARY", "GT_RETRIEVAL_MODE", "GT_RL_PROFILE"})
_HASH = frozenset("0123456789abcdef")


class BundleError(ValueError):
    """A bundle or one of its bound inputs is invalid."""


def groundtruth_release(root: Path | None = None) -> dict[str, Any]:
    """Read the sole source or installed-wheel producer and wheel pins."""
    root = root or Path(__file__).resolve().parent.parent
    path = root / "config" / "deepswe_product_bundle_v1.json"
    if not path.is_file():
        path = root / "gt_harness" / "data" / "product_bundle_v1.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != PRODUCT_SOURCE_SCHEMA:
        raise ValueError("invalid product release manifest")
    release = manifest["groundtruth"]
    for key in ("wheel_sha256", "producer_sha256"):
        digest = release.get(key, "")
        if not isinstance(digest, str) or len(digest) != 64 or not set(digest) <= _HASH:
            raise ValueError(f"invalid groundtruth release pin: {key}")
    return release


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HASH


def _repository_root(manifest_path: Path) -> Path:
    manifest_path = manifest_path.resolve()
    if manifest_path.parent.name != "config":
        raise BundleError("manifest_must_be_in_config_directory")
    return manifest_path.parents[1]


def _git_identity(root: Path) -> tuple[str, str]:
    def run(*args: str) -> str:
        process = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return process.stdout.strip()

    return run("rev-parse", "HEAD"), run("rev-parse", "HEAD^{tree}")


def _assert_committed_source_closure(root: Path, paths: list[str]) -> None:
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", *paths],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if tracked.returncode != 0:
        raise BundleError("source_closure_contains_untracked_path")
    clean = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", *paths],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if clean.returncode == 1:
        raise BundleError("source_closure_differs_from_head")
    if clean.returncode != 0:
        raise BundleError("source_closure_git_validation_failed")


def _normalize_wheel(source: Path, target: Path) -> None:
    """Rewrite a wheel with stable order, timestamps, permissions, and compression."""
    with (
        zipfile.ZipFile(source) as incoming,
        zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as outgoing,
    ):
        for name in sorted(incoming.namelist()):
            data = incoming.read(name)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.flag_bits = 0x800
            outgoing.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _build_deterministic_wheel(root: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gt-wheel-build-") as temporary:
        raw_dir = Path(temporary)
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--disable-pip-version-check",
                "--no-deps",
                "--wheel-dir",
                str(raw_dir),
                str(root),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if process.returncode != 0:
            raise BundleError(f"harness_wheel_build_failed:{process.stderr[-400:]}")
        wheels = list(raw_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise BundleError(f"harness_wheel_count:{len(wheels)}")
        target = output_dir / wheels[0].name
        _normalize_wheel(wheels[0], target)
    return target


def _load_source_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"manifest_unreadable:{type(exc).__name__}") from exc
    if not isinstance(value, dict) or value.get("schema") != PRODUCT_SOURCE_SCHEMA:
        raise BundleError("manifest_schema")
    if value.get("miniswe_agent_version") != "2.4.6":
        raise BundleError("manifest_miniswe_version")
    return value


def _validate_tasks(tasks: object) -> list[dict[str, Any]]:
    if not isinstance(tasks, list) or not tasks:
        raise BundleError("tasks_missing")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for expected_ordinal, raw in enumerate(tasks, start=1):
        if not isinstance(raw, dict):
            raise BundleError(f"task_invalid:{expected_ordinal}")
        task_id = raw.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in seen:
            raise BundleError(f"task_identity_invalid:{expected_ordinal}")
        if raw.get("ordinal") != expected_ordinal:
            raise BundleError(f"task_ordinal_invalid:{task_id}")
        if not _is_sha256(raw.get("task_config_sha256")):
            raise BundleError(f"task_config_digest_invalid:{task_id}")
        image_digest = raw.get("container_digest")
        if not isinstance(image_digest, str) or not image_digest.startswith("sha256:"):
            raise BundleError(f"task_image_digest_invalid:{task_id}")
        if not _is_sha256(image_digest.removeprefix("sha256:")):
            raise BundleError(f"task_image_digest_invalid:{task_id}")
        seen.add(task_id)
        validated.append(dict(raw))
    return validated


def _groundtruth_release_blockers(
    groundtruth: Mapping[str, Any], *, root: str | Path
) -> list[str]:
    """Validate the closed Route-B source, binary, wheel, and lineage contract."""
    blockers: list[str] = []
    source_commit = groundtruth.get("source_commit")
    source_tree = groundtruth.get("source_tree")
    producer_sha = groundtruth.get("producer_sha256")
    if not (
        isinstance(source_commit, str)
        and len(source_commit) == 40
        and isinstance(source_tree, str)
        and len(source_tree) == 40
        and _is_sha256(producer_sha)
    ):
        return ["groundtruth_source_identity_invalid"]

    build = groundtruth.get("producer_build")
    if not isinstance(build, Mapping):
        blockers.append("groundtruth_source_to_artifact_provenance_unverified")
    else:
        build_info_relative = build.get("build_info_path")
        build_info_path = (
            Path(root) / build_info_relative
            if isinstance(build_info_relative, str)
            else Path()
        )
        try:
            build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            build_info = None
        required_capabilities = set(groundtruth.get("producer_capabilities") or [])
        build_valid = (
            groundtruth.get("provenance_status") == "VERIFIED_ROUTE_B_SOURCE_BOUND"
            and groundtruth.get("wheel_runtime_status") == "VERIFIED_VENDORED_ROUTE_B"
            and build.get("schema") == "gt.producer_build_binding.v1"
            and build.get("status") == "VERIFIED"
            and build.get("source_commit") == source_commit
            and build.get("source_tree") == source_tree
            and build.get("binary_sha256") == producer_sha
            and isinstance(build_info_relative, str)
            and build_info_path.is_file()
            and _is_sha256(build.get("build_info_sha256"))
            and _sha256_file(build_info_path) == build.get("build_info_sha256")
            and _is_sha256(build.get("builder_image_digest"))
            and build.get("static_linking") is True
            and isinstance(build_info, Mapping)
            and build_info.get("schema") == "gt-index.build.v1"
            and build_info.get("complete") is True
            and build_info.get("git_commit") == source_commit
            and build_info.get("executable_sha256") == producer_sha
            and build_info.get("graph_schema_version")
            == groundtruth.get("graph_schema_version")
            and build_info.get("build_tags") == build.get("build_tags")
            and isinstance(build_info.get("source_fingerprint"), str)
            and _is_sha256(build_info.get("source_fingerprint"))
            and required_capabilities
            and required_capabilities.issubset(set(build_info.get("capabilities") or []))
        )
        if not build_valid:
            blockers.append("groundtruth_source_to_artifact_provenance_unverified")

    lineage = groundtruth.get("lineage_exception")
    if groundtruth.get("accepted_default_ancestor") is True:
        if not isinstance(groundtruth.get("accepted_default_ref"), str):
            blockers.append("groundtruth_accepted_default_identity_invalid")
    elif not isinstance(lineage, Mapping):
        blockers.append("groundtruth_source_not_accepted_default_ancestor")
    else:
        unsigned = dict(lineage)
        supplied_digest = unsigned.pop("attestation_digest_sha256", None)
        path = lineage.get("ancestry_path")
        reviews = lineage.get("review_packets")
        product_reviews = lineage.get("product_review_packets")
        accepted = lineage.get("accepted_default_commit")
        certified = lineage.get("certified_source_commit")
        changed_paths = lineage.get("post_certification_changed_paths")
        lineage_valid = (
            lineage.get("schema") == "gt.groundtruth_lineage_exception.v1"
            and lineage.get("status") == "VERIFIED_DESCENDANT_LINEAGE_EXCEPTION"
            and lineage.get("authority") == "HAR-81_OWNER_DIRECTIVE"
            and lineage.get("source_commit") == source_commit
            and isinstance(accepted, str)
            and len(accepted) == 40
            and isinstance(path, list)
            and len(path) >= 2
            and len(path) == len(set(path))
            and path[0] == accepted
            and path[-1] == source_commit
            and all(isinstance(value, str) and len(value) == 40 for value in path)
            and certified in path
            and isinstance(changed_paths, list)
            and all(isinstance(value, str) and value for value in changed_paths)
            and isinstance(reviews, list)
            and len(reviews) >= 2
            and all(
                isinstance(row, Mapping)
                and isinstance(row.get("packet_id"), str)
                and _is_sha256(row.get("packet_digest_sha256"))
                and row.get("head_sha") in path
                for row in reviews
            )
            and any(
                isinstance(row, Mapping)
                and row.get("head_sha") == source_commit
                and row.get("kind") == "check_outcome"
                and row.get("status") == "PASS"
                for row in reviews
            )
            and isinstance(product_reviews, list)
            and len(product_reviews) >= 1
            and sum(
                isinstance(row, Mapping)
                and row.get("purpose") == "recorded_content_correctness"
                and row.get("kind") == "measurement"
                and row.get("status") == "PASS"
                and isinstance(row.get("packet_id"), str)
                and isinstance(row.get("head_sha"), str)
                and len(row.get("head_sha")) == 40
                and _is_sha256(row.get("packet_digest_sha256"))
                and _is_sha256(row.get("measurement_digest_sha256"))
                and _is_sha256(row.get("measurement_file_sha256"))
                and isinstance(row.get("supersedes"), str)
                and isinstance(row.get("source_runs"), list)
                and len(row.get("source_runs")) == 3
                and len(set(row.get("source_runs"))) == 3
                for row in product_reviews
            )
            == 1
            and _is_sha256(supplied_digest)
            and _digest(unsigned) == supplied_digest
        )
        if not lineage_valid:
            blockers.append("groundtruth_lineage_exception_invalid")
    return sorted(set(blockers))


def build_product_bundle(
    manifest_path: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build a semantic content-addressed product manifest from local bytes."""
    path = Path(manifest_path)
    root = _repository_root(path)
    source = _load_source_manifest(path)
    tasks = _validate_tasks(source.get("tasks"))
    closure = source.get("source_closure")
    if not isinstance(closure, list) or not closure:
        raise BundleError("source_closure_missing")
    relative_paths = sorted(set(closure))
    artifacts: list[dict[str, Any]] = []
    for relative in relative_paths:
        if not isinstance(relative, str) or not relative or ".." in Path(relative).parts:
            raise BundleError("source_closure_path_invalid")
        file_path = root / relative
        if not file_path.is_file():
            raise BundleError(f"source_closure_missing:{relative}")
        artifacts.append(
            {
                "path": relative.replace("\\", "/"),
                "bytes": file_path.stat().st_size,
                "sha256": _sha256_file(file_path),
            }
        )
    _assert_committed_source_closure(root, relative_paths)
    commit, tree = _git_identity(root)
    wheel_record: dict[str, Any] | None = None
    if output_dir is not None:
        wheel = _build_deterministic_wheel(root, Path(output_dir) / "dist")
        wheel_record = {
            "filename": wheel.name,
            "bytes": wheel.stat().st_size,
            "sha256": _sha256_file(wheel),
        }
    groundtruth = dict(source["groundtruth"])
    release_blockers = _groundtruth_release_blockers(groundtruth, root=root)
    body: dict[str, Any] = {
        "schema": PRODUCT_BUNDLE_SCHEMA,
        "harness": {"commit": commit, "tree": tree},
        "groundtruth": groundtruth,
        "python_wheel": wheel_record,
        "python_version": source["python_version"],
        "uv": dict(source["uv"]),
        "harbor_version": source["harbor_version"],
        "pier_version": source["pier_version"],
        "miniswe_agent_version": source["miniswe_agent_version"],
        "provider_receipt_ledger": _PROVIDER_RECEIPT_LEDGER,
        "dataset": dict(source["dataset"]),
        "tasks": tasks,
        "artifacts": artifacts,
        "capabilities": sorted(set(source["capabilities"])),
        "release_eligible": not release_blockers,
        "release_blockers": release_blockers,
        "arm_contract": {
            "structural_fields": [
                "bundle_digest_sha256",
                "dataset",
                "harbor_version",
                "miniswe_agent_version",
                "model_route",
                "pier_version",
                "python_version",
                "tasks",
            ],
            "allowed_deltas": ["activation", "evidence_delivery"],
        },
    }
    body["source_closure_sha256"] = _digest(artifacts)
    body["bundle_digest_sha256"] = _digest(body)
    validate_product_bundle(body, root=root)
    if output_dir is not None:
        target = Path(output_dir) / "product-bundle.json"
        _atomic_json(target, body)
    return body


#: What a frozen candidate's provider ledger attests. Stated in the manifest
#: rather than inferred from a schema version, because the digest subject
#: changed at v2 and a reader comparing a v1 row to a v2 row would otherwise
#: conclude the provider returned something different when only the seam moved.
_PROVIDER_RECEIPT_LEDGER: dict[str, Any] = {
    "schema": "gt.provider-receipt.v2",
    "terminal_granularity": "attempt",
    "response_digest_subject": "provider_response.model_dump(mode=json)",
    "invariant": (
        "every provider_request row has exactly one terminal row "
        "(provider_response XOR provider_failure), paired by request identity"
    ),
    "capture_seam": "model._query",
    "supersedes": {
        "schema": "gt.provider-receipt.v1",
        "response_sha256_comparable": False,
        "changes": [
            "v1 digested the query wrapper's message dict via extra.response "
            "(response.model_dump()); v2 digests the raw provider response as "
            "response.model_dump(mode=\"json\") captured at the transport seam",
            "v1 emitted terminal rows per logical call while request rows were "
            "already per attempt, so any retried provider call produced N "
            "request rows against a single terminal row",
            "v1 recorded a provider_failure when the query wrapper raised after "
            "a successful provider response (for example during cost "
            "calculation), attributing a local fault to the provider",
        ],
    },
}


def validate_product_bundle(bundle: Mapping[str, Any], *, root: str | Path) -> None:
    if bundle.get("schema") != PRODUCT_BUNDLE_SCHEMA:
        raise BundleError("bundle_schema")
    supplied = bundle.get("bundle_digest_sha256")
    body = dict(bundle)
    body.pop("bundle_digest_sha256", None)
    if not _is_sha256(supplied) or _digest(body) != supplied:
        raise BundleError("bundle_digest_mismatch")
    if bundle.get("miniswe_agent_version") != "2.4.6":
        raise BundleError("bundle_miniswe_version")
    _validate_tasks(bundle.get("tasks"))
    root_path = Path(root).resolve()
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise BundleError("bundle_artifacts_missing")
    for row in artifacts:
        if not isinstance(row, dict):
            raise BundleError("bundle_artifact_invalid")
        relative = row.get("path")
        if not isinstance(relative, str) or ".." in Path(relative).parts:
            raise BundleError("bundle_artifact_path_invalid")
        path = root_path / relative
        if not path.is_file():
            raise BundleError(f"bundle_artifact_missing:{relative}")
        if path.stat().st_size != row.get("bytes") or _sha256_file(path) != row.get("sha256"):
            raise BundleError(f"bundle_artifact_mismatch:{relative}")
    groundtruth = bundle.get("groundtruth")
    if not isinstance(groundtruth, Mapping):
        raise BundleError("groundtruth_identity_missing")
    for kind in ("wheel", "producer"):
        relative = groundtruth.get(f"{kind}_path")
        expected = groundtruth.get(f"{kind}_sha256")
        if not isinstance(relative, str) or not _is_sha256(expected):
            raise BundleError(f"groundtruth_{kind}_identity_invalid")
        candidate = root_path / relative
        if not candidate.is_file() or _sha256_file(candidate) != expected:
            raise BundleError(f"groundtruth_{kind}_digest_mismatch")
    expected_blockers = _groundtruth_release_blockers(groundtruth, root=root_path)
    if bundle.get("release_blockers") != expected_blockers:
        raise BundleError("groundtruth_release_blockers_mismatch")
    if bundle.get("release_eligible") is not (not expected_blockers):
        raise BundleError("groundtruth_release_eligibility_mismatch")
    wheel = bundle.get("python_wheel")
    if wheel is not None and (
        not isinstance(wheel, Mapping)
        or not _is_sha256(wheel.get("sha256"))
        or not isinstance(wheel.get("bytes"), int)
    ):
        raise BundleError("python_wheel_identity_invalid")


# The two arms, named once and imported by every consumer. The pair was
# hand-typed FIVE times across four modules: both accept-validators below, the
# loop that drives them, and gt_harness/runtime_receipts.py's own raising
# validator. Three writers produce it - miniswe_gt_run.py:1128 and :1159, and
# miniswe_supervisor.py:328 - each as its own conditional expression.
#
# Two of the five copies raise, and project_task_environment runs per task
# inside the paid window, so a third arm would have raised during setup on
# every task with spend already committed. All five agreed, which is why no
# sweep flagged them: this is the same defect class as the refusal allow-list
# and the prompt-kind pair, caught before it was wrong rather than after.
BENCHMARK_ARMS = ("bare", "groundtruth")


def project_task_environment(host: Mapping[str, str], *, treatment: str) -> dict[str, str]:
    """Project a closed, typed, credential-free environment into task tools."""
    if treatment not in BENCHMARK_ARMS:
        raise ValueError(f"unsupported_treatment:{treatment}")
    projected = {
        name: str(host[name]).strip()
        for name in sorted(_SAFE_GT_ENV)
        if str(host.get(name, "")).strip()
    }
    projected.update(
        {
            "GT_TREATMENT": treatment,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    return projected


def build_benchmark_plan(bundle: Mapping[str, Any], *, arm: str) -> dict[str, Any]:
    if arm not in BENCHMARK_ARMS:
        raise ValueError(f"unsupported_arm:{arm}")
    tasks = [
        {
            "ordinal": row["ordinal"],
            "task_id": row["task_id"],
            "task_config_sha256": row["task_config_sha256"],
            "container_digest": row["container_digest"],
        }
        for row in bundle["tasks"]
    ]
    structural = {
        "bundle_digest_sha256": bundle["bundle_digest_sha256"],
        "dataset": bundle["dataset"],
        "harbor_version": bundle["harbor_version"],
        "miniswe_agent_version": bundle["miniswe_agent_version"],
        "model_route": "provider-disabled-deterministic-fixture",
        "pier_version": bundle["pier_version"],
        "python_version": bundle["python_version"],
        "tasks": tasks,
    }
    return {
        "schema": PLAN_SCHEMA,
        "arm": arm,
        "activation": arm == "groundtruth",
        "provider_call_ceiling": 0,
        "estimated_cost_usd": 0,
        "tasks": tasks,
        "structural_identity": structural,
        "parity_identity_sha256": _digest(structural),
    }


def _validated_honesty(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != "gt.honesty_envelope.v1":
        raise ValueError("task_result_honesty_missing")
    return envelope_from_mapping(value).as_dict()


def _unknown_result_honesty(task_id: str, reason: str) -> dict[str, Any]:
    return envelope_for_result(
        source_revision="benchmark_task_result",
        workspace_revision=task_id,
        payload=None,
        returned_count=0,
        true_total=None,
        incomplete=True,
        abstention_reason=reason,
    )


def aggregate_results(
    plan: Mapping[str, Any], results: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError("benchmark_plan_schema")
    supplied: dict[str, dict[str, Any]] = {}
    for raw in results:
        task_id = str(raw.get("task_id") or "")
        if task_id in supplied:
            raise ValueError(f"duplicate_task_result:{task_id}")
        supplied[task_id] = dict(raw)
    normalized: list[dict[str, Any]] = []
    solved = 0
    expected = [str(row["task_id"]) for row in plan["tasks"]]
    unexpected = sorted(set(supplied) - set(expected))
    if unexpected:
        raise ValueError(f"unexpected_task_result:{unexpected[0]}")
    for task_id in expected:
        row = supplied.get(task_id)
        malformed_reason: str | None = None
        if row is None:
            malformed_reason = "missing_result"
        elif row.get("schema") != TASK_RESULT_SCHEMA:
            malformed_reason = "malformed_result"
        else:
            try:
                row = dict(row)
                row["honesty"] = _validated_honesty(row.get("honesty"))
            except (TypeError, ValueError):
                malformed_reason = "malformed_honesty_envelope"
        if malformed_reason is not None:
            row = {
                "schema": TASK_RESULT_SCHEMA,
                "task_id": task_id,
                "status": "incomplete",
                "stop_reason": malformed_reason,
                "grader": {"solved": False},
                "honesty": _unknown_result_honesty(task_id, malformed_reason),
            }
        row = dict(row)
        is_solved = row.get("status") == "complete" and row.get("grader") == {"solved": True}
        row["counts_as_solved"] = is_solved
        solved += int(is_solved)
        normalized.append(row)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "arm": plan.get("arm"),
        "planned": len(expected),
        "solved": solved,
        "failed": len(expected) - solved,
        "results": normalized,
        "honesty": envelope_for_result(
            source_revision="benchmark_summary",
            workspace_revision=str(plan.get("parity_identity_sha256") or ""),
            payload=None,
            returned_count=len(normalized),
            true_total=len(expected),
        ),
    }
    summary["summary_digest_sha256"] = _digest(summary)
    return summary


def _atomic_json(path: Path, value: Any) -> None:
    atomic_json(path, value)


def _install_and_attest_wheel(
    bundle: Mapping[str, Any], *, bundle_dir: Path, output: Path
) -> dict[str, Any]:
    wheel_record = bundle.get("python_wheel")
    if not isinstance(wheel_record, Mapping):
        raise BundleError("acceptance_python_wheel_missing")
    wheel = bundle_dir / "dist" / str(wheel_record["filename"])
    if not wheel.is_file() or _sha256_file(wheel) != wheel_record.get("sha256"):
        raise BundleError("acceptance_python_wheel_mismatch")
    installed = output / "installed-wheel"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        cwd=output,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if process.returncode != 0:
        raise BundleError(f"acceptance_wheel_install_failed:{process.stderr[-400:]}")
    probe_code = (
        "import importlib.metadata as m,json,sys;"
        f"sys.path.insert(0,{str(installed)!r});"
        "import eval.pier_gt_harness_adapter as a,gt_harness.product as p;"
        "import scripts.miniswe_gt_run as r;"
        "d=next(m.Distribution.discover(name='nano-harness',path=[sys.path[0]]));"
        "print(json.dumps({'adapter':a.__file__,'product':p.__file__,'runner':r.__file__,"
        "'distribution_version':d.version},sort_keys=True))"
    )
    probe_env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME"}
    }
    probe = subprocess.run(
        [sys.executable, "-c", probe_code],
        cwd=output,
        env=probe_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode != 0:
        raise BundleError(f"acceptance_installed_import_failed:{probe.stderr[-400:]}")
    try:
        resolved = json.loads(probe.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise BundleError("acceptance_installed_import_output_invalid") from exc
    installed_root = str(installed.resolve()).casefold()
    import_names = ("adapter", "product", "runner")
    if any(not str(resolved[name]).casefold().startswith(installed_root) for name in import_names):
        raise BundleError("acceptance_import_resolved_to_source")
    observed: dict[str, str | None] = {}
    for distribution in ("mini-swe-agent", "harbor", "datacurve-pier"):
        try:
            import importlib.metadata as metadata

            observed[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            observed[distribution] = None
    expected = {
        "mini-swe-agent": bundle["miniswe_agent_version"],
        "harbor": bundle["harbor_version"],
        "datacurve-pier": bundle["pier_version"],
    }
    mismatches = sorted(name for name, version in expected.items() if observed.get(name) != version)
    attestation: dict[str, Any] = {
        "schema": INSTALL_SCHEMA,
        "status": "VERIFIED" if not mismatches else "INCOMPLETE",
        "environment": "local_provider_free",
        "container": False,
        "bundle_digest_sha256": bundle["bundle_digest_sha256"],
        "harness_wheel": dict(wheel_record),
        "installed_distribution": {
            "name": "nano-harness",
            "version": resolved["distribution_version"],
        },
        "resolved_imports": resolved,
        "expected_dependencies": expected,
        "observed_dependencies": observed,
        "dependency_mismatches": mismatches,
        "installation_commands": ["pip install --no-deps --target <isolated> <bundle-wheel>"],
        "smoke_checks": {
            "installed_adapter_import": True,
            "installed_product_import": True,
            "installed_runner_import": True,
            "source_checkout_not_imported": True,
        },
    }
    attestation["attestation_digest_sha256"] = _digest(attestation)
    _atomic_json(output / "install-attestation.json", attestation)
    return attestation


def _run_fixture_arm(root: Path, *, arm: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    task_root = root / arm / "task"
    task_root.mkdir(parents=True, exist_ok=False)
    source = task_root / "calculator.py"
    test = task_root / "test_calculator.py"
    source.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
    test.write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    canaries = {
        "OPENAI_API_KEY": "_".join(("GT", "SECRET", "CANARY", "OPENAI")),
        "GOOGLE_APPLICATION_CREDENTIALS": "_".join(("GT", "SECRET", "CANARY", "GCP")),
        "GITHUB_TOKEN": "_".join(("GT", "SECRET", "CANARY", "GITHUB")),
    }
    safe_env = project_task_environment(
        {
            "GT_RL_PROFILE": "2",
            "GT_RETRIEVAL_MODE": "fixture",
            **canaries,
        },
        treatment=arm,
    )
    trace: list[dict[str, Any]] = []

    def test_command(label: str) -> subprocess.CompletedProcess[str]:
        started = time.monotonic_ns()
        # The three edits intentionally keep the same file size.  Remove the
        # timestamp/size-based CPython cache so a fast runner cannot reuse the
        # previous implementation between test subprocesses.
        shutil.rmtree(task_root / "__pycache__", ignore_errors=True)
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                "from calculator import add; assert add(2, 3) == 5",
            ],
            cwd=task_root,
            env={**os.environ, **safe_env},
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        trace.append(
            {
                "action": label,
                "command": "python -c 'from calculator import add; assert add(2, 3) == 5'",
                "return_code": process.returncode,
                "output_sha256": hashlib.sha256(
                    (process.stdout + process.stderr).encode("utf-8")
                ).hexdigest(),
                "duration_ns": time.monotonic_ns() - started,
            }
        )
        return process

    initial = test_command("test_initial")
    trace.append({"action": "inspect", "source_sha256": _sha256_file(source)})
    source.write_text("def add(left, right):\n    return left * right\n", encoding="utf-8")
    trace.append({"action": "edit_first_attempt", "source_sha256": _sha256_file(source)})
    recovery = test_command("test_failed_edit")
    source.write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    trace.append({"action": "edit_recovery", "source_sha256": _sha256_file(source)})
    final = test_command("test_final")
    if initial.returncode == 0 or recovery.returncode == 0 or final.returncode != 0:
        raise RuntimeError(
            f"fixture_execution_contract:{arm}:"
            f"initial={initial.returncode}:recovery={recovery.returncode}:final={final.returncode}:"
            f"initial_stderr={initial.stderr[-240:]!r}:"
            f"recovery_stderr={recovery.stderr[-240:]!r}:"
            f"final_stderr={final.stderr[-240:]!r}:"
            f"initial_stdout={initial.stdout[-240:]!r}:"
            f"recovery_stdout={recovery.stdout[-240:]!r}:"
            f"final_stdout={final.stdout[-240:]!r}"
        )
    trace_path = root / arm / "trajectory.json"
    _atomic_json(trace_path, trace)
    evidence_count = 1 if arm == "groundtruth" else 0
    result = {
        "schema": TASK_RESULT_SCHEMA,
        "task_id": "provider-free-fixture",
        "arm": arm,
        "install_identity": plan["structural_identity"]["bundle_digest_sha256"],
        "agent_exit": 0,
        "stop_reason": "finished",
        "status": "complete",
        "grader": {"solved": True},
        "usage": {"provider_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0},
        "evidence": {"delivered": evidence_count, "refused": 0},
        "artifacts": {
            "trajectory_sha256": _sha256_file(trace_path),
            "final_source_sha256": _sha256_file(source),
        },
        "honesty": envelope_for_result(
            source_revision="provider_free_fixture",
            workspace_revision=_sha256_file(source),
            payload={"evidence_delivered": evidence_count},
            returned_count=evidence_count,
            true_total=evidence_count,
        ),
    }
    result["result_digest_sha256"] = _digest(result)
    _atomic_json(root / arm / "task-result.json", result)
    return result


def _prove_container_install(bundle: Mapping[str, Any], *, bundle_dir: Path) -> dict[str, Any]:
    """Install the built wheel in one pinned task image and smoke its entrypoints.

    This is intentionally a small provider-free proof: it does not run a benchmark
    task, but it exercises the same image/runtime boundary used by the Harbor
    adapter.  Missing Docker or a failed install is recorded, never converted into
    a passing receipt.
    """
    wheel_record = bundle.get("python_wheel")
    tasks = bundle.get("tasks")
    if not isinstance(wheel_record, Mapping) or not isinstance(tasks, list) or not tasks:
        return {"status": "FAILED", "reason": "container_fixture_identity_missing"}
    wheel = bundle_dir / "dist" / str(wheel_record.get("filename"))
    image = str(tasks[0].get("container_image") or "")
    digest = str(tasks[0].get("container_digest") or "")
    if not wheel.is_file() or not image or not digest:
        return {"status": "FAILED", "reason": "container_fixture_artifact_missing"}
    image_ref = f"{image}@{digest}"
    pull_output = ""
    pull_return_code = 1
    pull_attempts = 0
    image_source = "registry_pull"
    try:
        cached = subprocess.run(
            ["docker", "image", "inspect", image_ref],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "FAILED",
            "reason": f"container_cache_inspect_{type(exc).__name__}",
            "image": image,
            "digest": digest,
            "pull_attempts": 0,
        }
    if cached.returncode == 0:
        image_source = "local_digest_cache"
        pull_return_code = 0
    else:
        for delay in (0, 15, 30, 60):
            pull_attempts += 1
            if delay:
                time.sleep(delay)
            try:
                pull = subprocess.run(
                    ["docker", "pull", image_ref],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=180,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {
                    "status": "FAILED",
                    "reason": f"container_image_pull_{type(exc).__name__}",
                    "image": image,
                    "digest": digest,
                    "pull_attempts": pull_attempts,
                }
            pull_output += pull.stdout + pull.stderr
            pull_return_code = pull.returncode
            if pull.returncode == 0:
                break
    if pull_return_code != 0:
        lowered = pull_output.casefold()
        reason = (
            "container_image_pull_rate_limited"
            if "toomanyrequests" in lowered or "rate exceeded" in lowered
            else "container_image_pull_failed"
        )
        return {
            "status": "FAILED",
            "reason": reason,
            "image": image,
            "digest": digest,
            "pull_attempts": pull_attempts,
            "output_sha256": hashlib.sha256(pull_output.encode("utf-8")).hexdigest(),
        }
    target = f"/tmp/{wheel.name}"
    mount = f"{wheel.resolve()}:{target}:ro"
    probe = (
        "import subprocess,sys; "
        f"subprocess.check_call([sys.executable,'-m','pip','install','--no-deps',"
        f"'--target','/tmp/gt-installed','{target}']); "
        "sys.path.insert(0,'/tmp/gt-installed'); "
        "import gt_harness.product; print('installed-product-ok')"
    )
    try:
        process = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "-v",
                mount,
                image_ref,
                "python",
                "-c",
                probe,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "FAILED", "reason": f"container_probe_{type(exc).__name__}"}
    output = process.stdout + process.stderr
    return {
        "status": "VERIFIED"
        if process.returncode == 0 and "installed-product-ok" in output
        else "FAILED",
        "image": image,
        "digest": digest,
        "pull_attempts": pull_attempts,
        "image_source": image_source,
        "wheel_sha256": wheel_record.get("sha256"),
        "return_code": process.returncode,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def _prove_fake_openai_transport() -> dict[str, Any]:
    """Exercise a deterministic OpenAI-compatible chat-completions transport."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import Request, urlopen

    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self):  # noqa: N802 - stdlib handler API
            Handler.calls += 1
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            body = canonical_json_bytes(
                {
                    "id": "fake-provider-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = canonical_json_bytes(
            {"model": "provider-free-fixture", "messages": [{"role": "user", "content": "ping"}]}
        )
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        valid = body.get("choices", [{}])[0].get("message", {}).get("content") == "ok"
        return {
            "status": "VERIFIED" if valid and Handler.calls == 1 else "FAILED",
            "calls": Handler.calls,
        }
    except Exception as exc:  # noqa: BLE001 - receipt must retain typed failure
        return {"status": "FAILED", "calls": Handler.calls, "reason": type(exc).__name__}
    finally:
        server.shutdown()
        server.server_close()


def run_provider_free_acceptance(
    manifest_path: str | Path, *, output_dir: str | Path
) -> dict[str, Any]:
    """Run both deterministic fake-provider arms and emit a closeout receipt."""
    # Resolve once before passing paths to subprocesses.  The operator normally
    # supplies a relative output path (for example ``artifacts/closeout``),
    # while the wheel installer runs with ``cwd=output``.  Leaving the path
    # relative makes pip resolve it a second time under that cwd and produces
    # ``output/output/...`` instead of the staged wheel path.
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("acceptance_output_must_be_empty")
    output.mkdir(parents=True, exist_ok=True)
    repository_root = _repository_root(Path(manifest_path))
    content_fixture = (
        repository_root / "tests" / "fixtures" / "har81_attestation" / "content_recordings.json"
    )
    content_correctness = measure_recorded_content(content_fixture)
    _atomic_json(output / "har81-a21-content-measurement.json", content_correctness)
    bundle_dir = output / "bundle"
    bundle = build_product_bundle(manifest_path, output_dir=bundle_dir)
    installation = _install_and_attest_wheel(bundle, bundle_dir=bundle_dir, output=output)
    container_proof = _prove_container_install(bundle, bundle_dir=bundle_dir)
    fake_provider_proof = _prove_fake_openai_transport()
    arms: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    for arm in BENCHMARK_ARMS:
        plan = build_benchmark_plan(bundle, arm=arm)
        # The frozen provider-free fixture is deliberately one task; the released
        # DeepSWE task list remains immutable in structural_identity.
        fixture_plan = dict(plan)
        fixture_plan["tasks"] = [{"task_id": "provider-free-fixture"}]
        _atomic_json(output / arm / "plan.json", fixture_plan)
        result = _run_fixture_arm(output, arm=arm, plan=fixture_plan)
        summary = aggregate_results(fixture_plan, [result])
        _atomic_json(output / arm / "summary.json", summary)
        plans.append(plan)
        arms.append(
            {
                "arm": arm,
                "plan_sha256": _digest(plan),
                "task_result_sha256": result["result_digest_sha256"],
                "summary_sha256": summary["summary_digest_sha256"],
                "solved": summary["solved"],
                "evidence_delivered": result["evidence"]["delivered"],
            }
        )
    parity_equal = plans[0]["parity_identity_sha256"] == plans[1]["parity_identity_sha256"]
    canaries = tuple(
        "_".join(("GT", "SECRET", "CANARY", suffix)) for suffix in ("OPENAI", "GCP", "GITHUB")
    )
    matches: list[str] = []
    for path in output.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            matches.extend(canary for canary in canaries if canary.encode() in content)
    acceptance_verified = (
        parity_equal
        and not matches
        and installation["status"] == "VERIFIED"
        and container_proof["status"] == "VERIFIED"
        and fake_provider_proof["status"] == "VERIFIED"
        and content_correctness["status"] == "PASS"
    )
    receipt: dict[str, Any] = {
        "schema": CLOSEOUT_SCHEMA,
        "status": ("VERIFIED_PROVIDER_FREE" if acceptance_verified else "FAILED"),
        "bundle_digest_sha256": bundle["bundle_digest_sha256"],
        "release_eligible": bundle["release_eligible"] and installation["status"] == "VERIFIED",
        "release_blockers": sorted(
            set(bundle["release_blockers"])
            | {
                f"dependency_identity_mismatch:{name}"
                for name in installation["dependency_mismatches"]
            }
            | (
                {"container_install_not_executed"}
                if container_proof["status"] != "VERIFIED"
                else set()
            )
            | (
                {"openai_compatible_fake_provider_not_executed"}
                if fake_provider_proof["status"] != "VERIFIED"
                else set()
            )
            | (
                {"recorded_content_correctness_failed"}
                if content_correctness["status"] != "PASS"
                else set()
            )
        ),
        "install_attestation_sha256": installation["attestation_digest_sha256"],
        "provider_calls": 0,
        "benchmark_runs": 0,
        "execution_mode": "provider_disabled_deterministic_fixture",
        "arms": arms,
        "parity": {
            "structural_identity_equal": parity_equal,
            "allowed_deltas": ["activation", "evidence_delivery"],
        },
        "secret_canary_matches": sorted(set(matches)),
        "live_smoke": {"status": "NOT_EXECUTED", "approved": False},
        "full_benchmark": {"status": "APPROVAL_GATED", "executed": False},
        "container_proof": container_proof,
        "fake_provider_proof": fake_provider_proof,
        "content_correctness": content_correctness,
    }
    receipt["closeout_digest_sha256"] = _digest(receipt)
    _atomic_json(output / "product-closeout.json", receipt)
    return receipt
