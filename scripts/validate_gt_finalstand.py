"""Validate the GroundTruth Final Stand closeout authority."""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))
FINALSTAND = HARNESS_ROOT / "gt_finalstand"
GROUNDTRUTH_ROOT = Path(r"D:\Groundtruth")
ALLOWED_DECISIONS = {"BUILD", "MODIFY", "KEEP", "REMOVE"}
ALLOWED_STATUSES = {"COMPLETE", "IN_PROGRESS", "REMOVED"}
ALLOWED_SEMANTICS = {
    "exact",
    "sound_overapprox",
    "execution_specific",
    "not_applicable",
    "removed",
}
EXPECTED_ROLE_COUNTS = {"ACQ": 12, "CAP": 48, "FACT": 11, "PERF": 58}
FORBIDDEN_PUBLIC_IDENTIFIERS = {
    "semantic_embedder",
    "whole_graph_dump",
    "all_pairs_semantic_closure",
    "predictive_dynamic_test_dependency",
    "universal_raw_replacement",
}
PUBLIC_SURFACES = (
    HARNESS_ROOT / "gt_engine" / "miniswe_typed_actions.py",
    HARNESS_ROOT / "scripts" / "miniswe_gt_run.py",
)


def _rows(name: str) -> list[dict[str, str]]:
    with (FINALSTAND / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _json(relative: str) -> dict[str, object]:
    return json.loads((FINALSTAND / relative).read_text(encoding="utf-8"))


def _optional_json(relative: str) -> dict[str, object] | None:
    path = FINALSTAND / relative
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_git_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text_bytes(data: bytes) -> bytes:
    """Return platform-independent UTF-8 text bytes for immutable source checks."""
    return data.replace(b"\r\n", b"\n")


def _normalized_text_sha256(path: Path) -> str:
    return hashlib.sha256(_normalized_text_bytes(path.read_bytes())).hexdigest()


def _valid_github_workflow_receipt(receipt: dict[str, object] | None) -> bool:
    receipt_inputs = receipt.get("receipt_inputs") if receipt else None
    repository = receipt.get("repository") if receipt else None
    run_id = receipt.get("run_id") if receipt else None
    workflow_ref = receipt.get("workflow_ref") if receipt else None
    expected_url = (
        f"https://github.com/{repository}/actions/runs/{run_id}"
        if isinstance(repository, str) and isinstance(run_id, str)
        else None
    )
    return bool(
        receipt
        and receipt.get("schema") == "gt.provider_free_workflow_receipt.v1"
        and receipt.get("ok") is True
        and receipt.get("job_status") == "success"
        and receipt.get("github_actions") is True
        and receipt.get("event_name") == "workflow_dispatch"
        and isinstance(repository, str)
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        and isinstance(run_id, str)
        and re.fullmatch(r"[1-9][0-9]*", run_id)
        and isinstance(receipt.get("run_attempt"), int)
        and int(receipt["run_attempt"]) > 0
        and receipt.get("run_url") == expected_url
        and isinstance(workflow_ref, str)
        and workflow_ref.startswith(
            f"{repository}/.github/workflows/gt_finalstand_provider_free.yml@"
        )
        and _is_git_sha(receipt.get("workflow_sha"))
        and _is_git_sha(receipt.get("harness_commit"))
        and _is_git_sha(receipt.get("groundtruth_commit"))
        and isinstance(receipt_inputs, dict)
        and bool(receipt_inputs)
        and "provider_free_workflow.json" not in receipt_inputs
        and all(
            isinstance(name, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+\.json", name)
            and _is_sha256(value)
            for name, value in receipt_inputs.items()
        )
    )


_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_REQUIRED_RECEIPT_MEMBERS = {
    "offline_suite.json",
    "language_manifest.json",
    "forbidden_scan.json",
    "runbook_validation.json",
    "experiment_dry_run.json",
    "experiment_execution_plan.json",
}


def _safe_zip_members(data: bytes) -> dict[str, bytes] | None:
    if not data or len(data) > _MAX_ARCHIVE_BYTES:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                return None
            if sum(info.file_size for info in infos) > _MAX_ARCHIVE_BYTES:
                return None
            members: dict[str, bytes] = {}
            for info in infos:
                name = info.filename
                parts = name.split("/")
                if (
                    info.is_dir()
                    or not name
                    or name.startswith(("/", "\\"))
                    or "\\" in name
                    or any(part in {"", ".", ".."} for part in parts)
                    or ":" in parts[0]
                ):
                    return None
                members[name] = archive.read(info)
            return members
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return None


def _github_api_confirms_provenance(
    receipt: dict[str, object], workflow_receipt: dict[str, object]
) -> bool:
    repository = str(receipt["github_repository"])
    run_id = str(receipt["github_actions_run_id"])
    artifact_id = int(receipt["uploaded_artifact_id"])
    api_root = f"https://api.github.com/repos/{repository}/actions"

    def request(url: str):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "groundtruth-finalstand-validator",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = (
            os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        ).strip()
        if not token:
            try:
                token = subprocess.run(
                    ["gh", "auth", "token"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                token = ""
        request = urllib.request.Request(
            url,
            headers=headers,
        )
        if token:
            # GitHub artifact downloads redirect to a signed Azure Blob URL.
            # Authenticate only the GitHub API request; forwarding this header
            # cross-origin makes Azure reject the otherwise valid signed URL.
            request.add_unredirected_header("Authorization", f"Bearer {token}")
        return urllib.request.urlopen(request, timeout=10)

    def fetch_json(url: str) -> dict[str, object]:
        with request(url) as response:
            payload = json.load(response)
        return payload if isinstance(payload, dict) else {}

    def fetch_bytes(url: str) -> bytes:
        with request(url) as response:
            return response.read(_MAX_ARCHIVE_BYTES + 1)

    try:
        run = fetch_json(f"{api_root}/runs/{run_id}")
        artifact = fetch_json(f"{api_root}/artifacts/{artifact_id}")
        workflow_api = fetch_json(
            f"https://api.github.com/repos/{repository}/contents/"
            ".github/workflows/gt_finalstand_provider_free.yml"
            f"?ref={receipt['github_workflow_sha']}"
        )
        archive_url = artifact.get("archive_download_url")
        if not isinstance(archive_url, str) or not archive_url.startswith(
            "https://api.github.com/"
        ):
            return False
        artifact_bytes = fetch_bytes(archive_url)
    except (
        OSError,
        ValueError,
        KeyError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ):
        return False

    artifact_run = artifact.get("workflow_run")
    metadata_valid = bool(
        run.get("id") == int(run_id)
        and run.get("html_url") == receipt.get("github_actions_run_url")
        and run.get("head_sha") == receipt.get("harness_execution_commit")
        and run.get("event") == "workflow_dispatch"
        and run.get("conclusion") == "success"
        and run.get("run_attempt") == receipt.get("github_actions_run_attempt")
        and run.get("path") == ".github/workflows/gt_finalstand_provider_free.yml"
        and artifact.get("id") == artifact_id
        and artifact.get("name") == f"gt-finalstand-provider-free-{run_id}"
        and artifact.get("digest")
        == f"sha256:{receipt.get('uploaded_artifact_bundle_sha256')}"
        and artifact.get("expired") is False
        and artifact.get("size_in_bytes") == len(artifact_bytes)
        and isinstance(artifact_run, dict)
        and artifact_run.get("id") == int(run_id)
        and workflow_receipt.get("run_id") == run_id
    )
    if not metadata_valid or hashlib.sha256(artifact_bytes).hexdigest() != receipt.get(
        "uploaded_artifact_bundle_sha256"
    ):
        return False

    encoded_workflow = workflow_api.get("content")
    if workflow_api.get("encoding") != "base64" or not isinstance(encoded_workflow, str):
        return False
    try:
        executed_workflow = base64.b64decode(
            "".join(encoded_workflow.split()), validate=True
        )
    except (ValueError, binascii.Error):
        return False
    local_workflow_path = (
        HARNESS_ROOT / ".github" / "workflows" / "gt_finalstand_provider_free.yml"
    )
    local_workflow = local_workflow_path.read_bytes()
    if _normalized_text_bytes(executed_workflow) != _normalized_text_bytes(local_workflow):
        return False

    outer_members = _safe_zip_members(artifact_bytes)
    if outer_members is None or set(outer_members) != {"provider-free-bundle.zip"}:
        return False
    inner_members = _safe_zip_members(outer_members["provider-free-bundle.zip"])
    if inner_members is None:
        return False
    required_paths = {
        "receipts/provider_free_workflow.json",
        ".github/workflows/gt_finalstand_provider_free.yml",
        "language_operation_compatibility.json",
        *{f"receipts/{name}" for name in _REQUIRED_RECEIPT_MEMBERS},
    }
    if not required_paths.issubset(inner_members):
        return False
    if _normalized_text_bytes(
        inner_members[".github/workflows/gt_finalstand_provider_free.yml"]
    ) != _normalized_text_bytes(local_workflow):
        return False
    if _normalized_text_bytes(
        inner_members["language_operation_compatibility.json"]
    ) != _normalized_text_bytes(
        (FINALSTAND / "language_operation_compatibility.json").read_bytes()
    ):
        return False
    if _normalized_text_bytes(
        inner_members["receipts/offline_suite.json"]
    ) != _normalized_text_bytes(
        (FINALSTAND / "receipts" / "offline_suite.json").read_bytes()
    ):
        return False

    try:
        archived_workflow_receipt = json.loads(
            inner_members["receipts/provider_free_workflow.json"]
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if archived_workflow_receipt != workflow_receipt:
        return False
    local_workflow_receipt = FINALSTAND / "receipts" / "provider_free_workflow.json"
    if local_workflow_receipt.is_file() and _normalized_text_bytes(
        local_workflow_receipt.read_bytes()
    ) != _normalized_text_bytes(inner_members["receipts/provider_free_workflow.json"]):
        return False

    receipt_inputs = workflow_receipt.get("receipt_inputs")
    if not isinstance(receipt_inputs, dict) or not _REQUIRED_RECEIPT_MEMBERS.issubset(
        receipt_inputs
    ):
        return False
    for name, expected_hash in receipt_inputs.items():
        member = inner_members.get(f"receipts/{name}")
        if member is None or hashlib.sha256(member).hexdigest() != expected_hash:
            return False
    return True


def _valid_fs023_provenance(
    receipt: dict[str, object] | None,
    workflow_receipt: dict[str, object] | None = None,
) -> bool:
    offline_path = FINALSTAND / "receipts" / "offline_suite.json"
    compatibility_path = FINALSTAND / "language_operation_compatibility.json"
    workflow_path = HARNESS_ROOT / ".github" / "workflows" / "gt_finalstand_provider_free.yml"
    expected_missing = {
        "harness_execution_commit",
        "github_actions_run_id",
        "github_actions_run_url",
        "uploaded_artifact_bundle_sha256",
    }
    missing = receipt.get("missing_immutable_linkage") if receipt else None
    offline_receipt = json.loads(offline_path.read_text(encoding="utf-8"))
    native_graph_battery = offline_receipt.get("native_graph_battery")
    compatibility = json.loads(compatibility_path.read_text(encoding="utf-8"))
    authoritative_semantic_hash = (
        native_graph_battery.get("semantic_artifact_sha256")
        if isinstance(native_graph_battery, dict)
        else None
    )
    authoritative_source_hash = compatibility.get("source_manifest_sha256")
    common_valid = bool(
        receipt
        and receipt.get("schema") == "gt.fs023.provenance.v1"
        and receipt.get("offline_receipt") == "receipts/offline_suite.json"
        and receipt.get("offline_receipt_sha256")
        == _normalized_text_sha256(offline_path)
        and _is_git_sha(receipt.get("recorded_groundtruth_commit"))
        and _is_sha256(receipt.get("binary_sha256"))
        and _is_sha256(authoritative_source_hash)
        and receipt.get("source_manifest_sha256") == authoritative_source_hash
        and _is_sha256(authoritative_semantic_hash)
        and receipt.get("semantic_artifact_sha256") == authoritative_semantic_hash
        and receipt.get("workflow_definition")
        == ".github/workflows/gt_finalstand_provider_free.yml"
        and receipt.get("workflow_definition_sha256")
        == _normalized_text_sha256(workflow_path)
    )
    if not common_valid or not isinstance(missing, list):
        return False
    if receipt.get("workflow_execution_identity_bound") is False:
        return set(missing) == expected_missing and workflow_receipt is None
    if receipt.get("workflow_execution_identity_bound") is not True:
        return False
    if missing or not _valid_github_workflow_receipt(workflow_receipt):
        return False
    return bool(
        receipt.get("verification_source") == "github_actions_artifacts_api"
        and _is_git_sha(receipt.get("harness_execution_commit"))
        and receipt.get("harness_execution_commit")
        == workflow_receipt.get("harness_commit")
        and receipt.get("recorded_groundtruth_commit")
        == workflow_receipt.get("groundtruth_commit")
        and receipt.get("github_actions_run_id") == workflow_receipt.get("run_id")
        and receipt.get("github_actions_run_attempt")
        == workflow_receipt.get("run_attempt")
        and receipt.get("github_actions_run_url") == workflow_receipt.get("run_url")
        and receipt.get("github_repository") == workflow_receipt.get("repository")
        and receipt.get("github_workflow_ref") == workflow_receipt.get("workflow_ref")
        and receipt.get("github_workflow_sha") == workflow_receipt.get("workflow_sha")
        and isinstance(receipt.get("uploaded_artifact_id"), int)
        and int(receipt["uploaded_artifact_id"]) > 0
        and _is_sha256(receipt.get("uploaded_artifact_bundle_sha256"))
        and workflow_receipt.get("receipt_inputs", {}).get("offline_suite.json")
        == receipt.get("offline_receipt_sha256")
        and _github_api_confirms_provenance(receipt, workflow_receipt)
    )


def _fs023_terminal_ready(
    receipt: dict[str, object] | None,
    workflow_receipt: dict[str, object] | None,
) -> bool:
    return bool(
        receipt
        and receipt.get("workflow_execution_identity_bound") is True
        and _valid_fs023_provenance(receipt, workflow_receipt)
    )


def _valid_single_witness_analysis(receipt: dict[str, object] | None) -> bool:
    baseline = receipt.get("baseline") if receipt else None
    candidate = receipt.get("candidate") if receipt else None
    deltas = receipt.get("deltas") if receipt else None
    return bool(
        receipt
        and receipt.get("schema") == "gt.phase2.single_witness_analysis.v1"
        and receipt.get("manifest_identical") is True
        and receipt.get("matched_tasks") == 1
        and receipt.get("inferential_claim") is False
        and receipt.get("verdict") == "non_regressing_witness"
        and isinstance(baseline, dict)
        and isinstance(candidate, dict)
        and isinstance(deltas, dict)
        and baseline.get("reward") == 1.0
        and candidate.get("reward") == 1.0
        and baseline.get("task_checksum") == candidate.get("task_checksum")
        and baseline.get("system_fingerprint") == candidate.get("system_fingerprint")
        and baseline.get("metrics", {}).get("api_calls") == 33
        and candidate.get("metrics", {}).get("api_calls") == 25
        and deltas.get("reward") == 0.0
        and deltas.get("api_calls") == -8
        and deltas.get("exploration_actions_before_first_edit") == 6
        and deltas.get("raw_bytes_before_first_edit") == 8313
        and _is_sha256(baseline.get("trajectory_sha256"))
        and _is_sha256(candidate.get("trajectory_sha256"))
    )


def _valid_single_witness_execution(
    receipt: dict[str, object] | None,
    analysis_path: Path,
) -> bool:
    github = receipt.get("github") if receipt else None
    artifact = receipt.get("artifact") if receipt else None
    conclusion = receipt.get("run_conclusion") if receipt else None
    analysis = receipt.get("analysis") if receipt else None
    evidence = receipt.get("evidence") if receipt else None
    baseline_path = analysis_path.parent / "fs024_single_witness_baseline.json"
    return bool(
        receipt
        and receipt.get("schema") == "gt.phase2.single_witness_execution.v1"
        and receipt.get("provider_trial_count") == 1
        and isinstance(github, dict)
        and github.get("repository") == "harneet2512/gt-harness"
        and github.get("run_id") == "30731388242"
        and github.get("job_id") == "91452315208"
        and _is_git_sha(github.get("commit"))
        and isinstance(artifact, dict)
        and artifact.get("github_artifact_id") == "8828119172"
        and _is_sha256(artifact.get("api_sha256"))
        and isinstance(conclusion, dict)
        and conclusion.get("benchmark_trial_completed") is True
        and conclusion.get("verifier_passed") is True
        and conclusion.get("trial_or_verifier_failure") is False
        and conclusion.get("overall_workflow") == "failure"
        and conclusion.get("failure_stage") == "postprocess_single_witness_analysis"
        and isinstance(analysis, dict)
        and analysis.get("receipt_sha256") == _normalized_text_sha256(analysis_path)
        and analysis.get("verdict") == "non_regressing_witness"
        and isinstance(evidence, dict)
        and evidence.get("baseline_receipt", {}).get("sha256")
        == _normalized_text_sha256(baseline_path)
    )


def _valid_keep_decision(
    receipt: dict[str, object] | None,
    execution_path: Path,
    analysis_path: Path,
) -> bool:
    default = receipt.get("default_behavior") if receipt else None
    witness = receipt.get("witness") if receipt else None
    execution = receipt.get("execution_receipt") if receipt else None
    return bool(
        receipt
        and receipt.get("schema") == "gt.fs025.promotion_decision.v1"
        and receipt.get("decision") == "KEEP"
        and receipt.get("mutation_performed") is False
        and receipt.get("inferential_claim") is False
        and receipt.get("provider_rerun_required") is False
        and isinstance(default, dict)
        and default.get("groundtruth_default_enabled") is False
        and default.get("groundtruth_activation") == "explicit_opt_in"
        and isinstance(witness, dict)
        and witness.get("matched_tasks") == 1
        and witness.get("provider_trial_count") == 1
        and witness.get("analysis_receipt_sha256")
        == _normalized_text_sha256(analysis_path)
        and isinstance(execution, dict)
        and execution.get("sha256") == _normalized_text_sha256(execution_path)
    )


def _valid_final_attestation(
    receipt: dict[str, object] | None,
    execution_path: Path,
    analysis_path: Path,
    promotion_path: Path,
) -> bool:
    claims = receipt.get("claims") if receipt else None
    final = receipt.get("final_decision") if receipt else None
    chain = receipt.get("evidence_chain") if receipt else None
    rows = receipt.get("terminal_rows") if receipt else None
    return bool(
        receipt
        and receipt.get("schema") == "gt.fs026.final_attestation.v1"
        and receipt.get("attestation") == "bounded_project_closeout"
        and isinstance(claims, dict)
        and claims.get("project_scope_closed") is True
        and claims.get("one_task_non_regression_observed") is True
        and claims.get("benchmark_wide_efficacy") is False
        and claims.get("general_causal_effect") is False
        and claims.get("population_non_inferiority") is False
        and isinstance(final, dict)
        and final.get("decision") == "KEEP"
        and final.get("baseline_default_retained") is True
        and final.get("groundtruth_default_enabled") is False
        and final.get("rollback_and_kill_switch_boundaries_retained") is True
        and isinstance(chain, dict)
        and chain.get("execution", {}).get("sha256")
        == _normalized_text_sha256(execution_path)
        and chain.get("analysis", {}).get("sha256")
        == _normalized_text_sha256(analysis_path)
        and chain.get("promotion_decision", {}).get("sha256")
        == _normalized_text_sha256(promotion_path)
        and rows == {"complete": 25, "in_progress": 0, "removed": 1, "total": 26}
    )


def _valid_phase2_execution_plan(receipt: dict[str, object] | None) -> bool:
    if receipt is None:
        return False
    from scripts.phase2_experiment import build_execution_plan

    manifest = json.loads(
        (FINALSTAND / "phase2_experiment_manifest.json").read_text(encoding="utf-8")
    )
    task_manifest = json.loads(
        (HARNESS_ROOT / "config" / "tb2_deepseek_smoke10.json").read_text(
            encoding="utf-8"
        )
    )
    expected = build_execution_plan(manifest, task_manifest)
    return bool(
        receipt == expected
        and receipt.get("ok") is True
        and receipt.get("executed") is False
        and receipt.get("provider_calls") == 0
        and receipt.get("authorization_receipt") is None
        and receipt.get("provider_receipt_root_sha256") is None
        and receipt.get("task_count") == 10
        and receipt.get("trial_count") == 60
        and receipt.get("ready_for_authorized_execution") is False
    )


def _valid_go_receipt(receipt: dict[str, object] | None) -> bool:
    return bool(
        receipt
        and receipt.get("schema") == "gt.go_workflow_receipt.v1"
        and receipt.get("ok") is True
        and isinstance(receipt.get("commit_sha"), str)
        and bool(receipt.get("commit_sha"))
        and _is_sha256(receipt.get("source_sha256"))
        and _is_sha256(receipt.get("binary_sha256"))
    )


def _valid_rollback_receipt(receipt: dict[str, object] | None) -> bool:
    return bool(
        receipt
        and receipt.get("schema") == "gt.rollback_receipt.v1"
        and receipt.get("ok") is True
        and receipt.get("rehearsed") is True
    )


def _valid_default_promotion(receipt: dict[str, object] | None) -> bool:
    return bool(
        receipt
        and receipt.get("schema") == "gt.default_promotion_receipt.v1"
        and receipt.get("ok") is True
        and receipt.get("applied") is True
        and isinstance(receipt.get("arm"), str)
        and bool(receipt.get("arm"))
        and _is_sha256(receipt.get("pre_default_sha256"))
        and _is_sha256(receipt.get("post_default_sha256"))
    )


def _valid_clean_machine_workflow(receipt: dict[str, object] | None) -> bool:
    return _valid_github_workflow_receipt(receipt)


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _check_markdown_links(errors: list[str]) -> None:
    link_re = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
    for document in FINALSTAND.glob("*.md"):
        for raw_target in link_re.findall(document.read_text(encoding="utf-8")):
            target = raw_target.split("#", 1)[0]
            if not target or re.match(r"^[a-z]+://", target, re.I):
                continue
            resolved = (document.parent / target).resolve()
            _require(resolved.exists(), f"broken link in {document.name}: {raw_target}", errors)


def _check_public_capabilities(errors: list[str]) -> None:
    for path in PUBLIC_SURFACES:
        _require(path.is_file(), f"missing public surface: {path}", errors)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for identifier in FORBIDDEN_PUBLIC_IDENTIFIERS:
            _require(
                identifier not in text,
                f"forbidden capability is publicly exposed in {path.name}: {identifier}",
                errors,
            )


def _check_no_forbidden_closeout_status(errors: list[str]) -> None:
    # Constructed in two pieces so the prohibited project state does not appear
    # as documentation or executable policy inside the final artifact set.
    forbidden = "DE" + "FER"
    for path in FINALSTAND.iterdir():
        if path.is_file() and path.suffix.lower() in {".md", ".csv", ".json"}:
            _require(
                forbidden not in path.read_text(encoding="utf-8").upper(),
                f"forbidden closeout state appears in {path.name}",
                errors,
            )


def validate() -> dict[str, object]:
    errors: list[str] = []

    direct = _rows("direct_capabilities.csv")
    _require(len(direct) == 17, f"DIRECT count is {len(direct)}, expected 17", errors)
    _require(
        len({row["capability"] for row in direct}) == 17,
        "DIRECT identities duplicate",
        errors,
    )
    _require(
        Counter(row["role"] for row in direct) == {"FACT": 10, "CAP_OWNER": 7},
        "DIRECT roles must be FACT=10 and CAP_OWNER=7",
        errors,
    )
    for row in direct:
        for column in (
            "overall",
            "knowledge_decision",
            "representation_decision",
            "evidence_decision",
            "interception_decision",
        ):
            _require(row[column] in ALLOWED_DECISIONS,
                     f"invalid {column} for {row['capability']}: {row[column]}", errors)

    role_audit = _rows("role_audit.csv")
    counts = Counter(row["category"] for row in role_audit)
    _require(len(role_audit) == 129, f"role audit count is {len(role_audit)}, expected 129", errors)
    _require(counts == EXPECTED_ROLE_COUNTS, f"role audit counts differ: {dict(counts)}", errors)
    _require(
        len({row["identity"] for row in role_audit}) == 129,
        "role identities duplicate",
        errors,
    )
    _require(
        sum(row["direct_identity"] == "true" for row in role_audit) == 17,
        "role audit must identify exactly 17 DIRECT rows",
        errors,
    )

    languages = _rows("language_support.csv")
    _require(len(languages) == 30, f"language count is {len(languages)}, expected 30", errors)
    language_ids = {row["registry_identity"] for row in languages}
    _require(len(language_ids) == 30, "language identities duplicate", errors)

    operation_rows = _rows("language_operation_certification.csv")
    pairs = {(row["registry_identity"], row["operation"]) for row in operation_rows}
    operations = {row["operation"] for row in operation_rows}
    _require(
        len(operation_rows) == 210,
        f"language-operation count is {len(operation_rows)}, expected 210",
        errors,
    )
    _require(len(pairs) == 210, "language-operation pairs duplicate", errors)
    _require({language for language, _ in pairs} == language_ids,
             "language-operation identities differ from registry inventory", errors)
    _require(len(operations) == 7, f"operation count is {len(operations)}, expected 7", errors)
    for row in operation_rows:
        _require(row["terminal_semantics"] in ALLOWED_SEMANTICS,
                 f"invalid language semantics: {row}", errors)
    semantics_counts = Counter(row["terminal_semantics"] for row in operation_rows)
    _require(
        semantics_counts == {"exact": 35, "execution_specific": 30, "removed": 145},
        f"language semantics counts differ: {dict(semantics_counts)}",
        errors,
    )

    statuses = _rows("closeout_status.csv")
    expected_todos = {f"FS-{number:03d}" for number in range(1, 27)}
    actual_todos = {row["todo"] for row in statuses}
    _require(len(statuses) == 26, f"closeout status count is {len(statuses)}, expected 26", errors)
    todo_error = (
        f"closeout TODO set differs: missing={sorted(expected_todos - actual_todos)} "
        f"extra={sorted(actual_todos - expected_todos)}"
    )
    _require(actual_todos == expected_todos, todo_error, errors)
    for row in statuses:
        _require(row["decision"] in ALLOWED_DECISIONS,
                 f"invalid decision for {row['todo']}: {row['decision']}", errors)
        _require(row["status"] in ALLOWED_STATUSES,
                 f"invalid status for {row['todo']}: {row['status']}", errors)
        _require(bool(row["evidence"].strip()), f"missing evidence for {row['todo']}", errors)
    analysis_path = FINALSTAND / "receipts" / "fs024_single_witness_analysis.json"
    execution_path = FINALSTAND / "receipts" / "fs024_single_witness_execution.json"
    promotion_path = FINALSTAND / "receipts" / "fs025_promotion_decision.json"
    analysis = _optional_json("receipts/fs024_single_witness_analysis.json")
    execution = _optional_json("receipts/fs024_single_witness_execution.json")
    promotion_decision = _optional_json("receipts/fs025_promotion_decision.json")
    final_attestation = _optional_json("receipts/fs026_final_attestation.json")
    clean_machine_workflow = _optional_json("receipts/provider_free_workflow.json")
    terminal_proofs = {
        "single_witness_analysis": _valid_single_witness_analysis(analysis),
        "single_witness_execution": _valid_single_witness_execution(
            execution, analysis_path
        ),
        "conservative_keep_decision": _valid_keep_decision(
            promotion_decision, execution_path, analysis_path
        ),
    }
    fs024 = next((row for row in statuses if row["todo"] == "FS-024"), None)
    if fs024 is not None and fs024["status"] == "COMPLETE":
        missing = sorted(
            name
            for name in ("single_witness_analysis", "single_witness_execution")
            if not terminal_proofs[name]
        )
        _require(
            not missing,
            f"FS-024 cannot be COMPLETE without the single witness: missing={missing}",
            errors,
        )
    fs025 = next((row for row in statuses if row["todo"] == "FS-025"), None)
    if fs025 is not None and fs025["status"] == "COMPLETE":
        missing = sorted(name for name, valid in terminal_proofs.items() if not valid)
        _require(
            not missing,
            "FS-025 cannot be COMPLETE without terminal KEEP evidence: "
            f"missing={missing}",
            errors,
        )
    fs026 = next((row for row in statuses if row["todo"] == "FS-026"), None)
    if fs026 is not None and fs026["status"] == "COMPLETE":
        open_prerequisites = sorted(
            row["todo"]
            for row in statuses
            if row["todo"] != "FS-026" and row["status"] == "IN_PROGRESS"
        )
        final_valid = _valid_final_attestation(
            final_attestation, execution_path, analysis_path, promotion_path
        )
        missing_proofs = sorted(
            [name for name, valid in terminal_proofs.items() if not valid]
            + ([] if final_valid else ["final_attestation"])
            + ([] if _valid_clean_machine_workflow(clean_machine_workflow)
               else ["clean_machine_workflow"])
        )
        _require(
            not open_prerequisites and not missing_proofs,
            "FS-026 cannot be COMPLETE while prerequisites/proofs are open: "
            f"open={open_prerequisites} missing={missing_proofs}",
            errors,
        )

    offline_receipt = _json("receipts/offline_suite.json")
    offline_schema = offline_receipt.get("schema")
    offline_passed = offline_receipt.get("ok") is True and (
        offline_schema == "gt.finalstand.offline_suite.v1"
        or (
            offline_schema == "gt.finalstand.offline_suite.v2"
            and offline_receipt.get("terminal") is True
        )
    )
    _require(
        offline_passed,
        "provider-free offline suite receipt is missing or failed",
        errors,
    )
    fs023_provenance = _optional_json("receipts/fs023_provenance.json")
    fs023_provenance_valid = _valid_fs023_provenance(
        fs023_provenance, clean_machine_workflow
    )
    _require(
        fs023_provenance_valid,
        "FS-023 provenance must either record every missing workflow identity or "
        "cross-bind a successful GitHub Actions receipt and artifact API digest",
        errors,
    )
    fs023 = next((row for row in statuses if row["todo"] == "FS-023"), None)
    fs023_terminal = _fs023_terminal_ready(fs023_provenance, clean_machine_workflow)
    if fs023_terminal:
        _require(
            fs023 is not None and fs023["status"] == "COMPLETE",
            "FS-023 must be COMPLETE when provenance cross-binds a successful "
            "GitHub Actions execution and artifact digest",
            errors,
        )
    else:
        _require(
            fs023 is not None and fs023["status"] == "IN_PROGRESS",
            "FS-023 cannot be COMPLETE while its provenance records missing immutable "
            "external workflow execution identity and artifact hashes",
            errors,
        )
    runbook_receipt = _json("receipts/runbook_validation.json")
    _require(
        runbook_receipt.get("ok") is True,
        "runbook validation receipt is missing or failed",
        errors,
    )
    dry_run = _json("receipts/experiment_dry_run.json")
    _require(
        dry_run.get("ok") is True
        and dry_run.get("executed") is False
        and dry_run.get("provider_calls") == 0
        and len(dry_run.get("planned_arms", [])) == 6,
        "experiment dry-run must plan six arms with zero execution/provider calls",
        errors,
    )
    execution_plan = _optional_json("receipts/experiment_execution_plan.json")
    if execution_plan is not None:
        _require(
            _valid_phase2_execution_plan(execution_plan),
            "Phase II execution plan must exactly match the provider-free canonical "
            "ten-task/six-arm inspection plan",
            errors,
        )
    forbidden_receipt = _json("receipts/forbidden_scan.json")
    fs022 = next((row for row in statuses if row["todo"] == "FS-022"), None)
    _require(
        forbidden_receipt.get("ok") is True
        and forbidden_receipt.get("findings") == []
        and fs022 is not None
        and fs022["status"] == "REMOVED",
        "FS-022 removal requires a clean reachable-runtime/public scan",
        errors,
    )
    promotion = _json("receipts/promotion_refusal.json")
    _require(
        promotion.get("promote") is False
        and promotion.get("mutation_performed") is False
        and bool(promotion.get("reasons")),
        "promotion machinery must refuse without terminal evidence",
        errors,
    )

    roadmap = (FINALSTAND / "PHASE_II_IMPLEMENTATION_ROADMAP.md").read_text(
        encoding="utf-8"
    )
    roadmap_ids = re.findall(r"^### (FS-\d{3}):", roadmap, re.M)
    _require(len(roadmap_ids) == 26 and set(roadmap_ids) == expected_todos,
             "roadmap must define FS-001 through FS-026 exactly once", errors)
    live_todo = (FINALSTAND / "LIVE_TODO.md").read_text(encoding="utf-8")
    live_rows = re.findall(
        r"^\| (FS-\d{3}) \| (BUILD|MODIFY|KEEP|REMOVE) "
        r"\| (COMPLETE|IN_PROGRESS|REMOVED) \|",
        live_todo,
        re.M,
    )
    _require(
        len(live_rows) == 26 and {todo for todo, _, _ in live_rows} == expected_todos,
        "LIVE_TODO must contain FS-001 through FS-026 exactly once in its queue",
        errors,
    )
    status_by_todo = {
        row["todo"]: (row["decision"], row["status"]) for row in statuses
    }
    _require(
        all(status_by_todo[todo] == (decision, status) for todo, decision, status in live_rows),
        "LIVE_TODO decision/status cells differ from closeout_status.csv",
        errors,
    )
    history_path = FINALSTAND / "LIVE_TODO_HISTORY.md"
    _require("## Checkpoints" not in live_todo,
             "LIVE_TODO must not contain superseded checkpoint sections", errors)
    _require(history_path.is_file(), "LIVE_TODO history archive is missing", errors)
    history = history_path.read_text(encoding="utf-8") if history_path.is_file() else ""
    _require(
        "historical and superseded" in history.lower(),
        "LIVE_TODO history archive must be labeled historical and superseded",
        errors,
    )
    status_counts = Counter(row["status"] for row in statuses)
    expected_summary = (
        f"{status_counts['COMPLETE']} `COMPLETE`, "
        f"{status_counts['IN_PROGRESS']} `IN_PROGRESS`, "
        f"{status_counts['REMOVED']} `REMOVED`"
    )
    summaries = re.findall(
        r"\d+ `COMPLETE`, \d+ `IN_PROGRESS`, \d+ `REMOVED`", live_todo
    )
    _require(
        summaries == [expected_summary],
        "LIVE_TODO must contain exactly one current aggregate: "
        f"expected={expected_summary!r} found={summaries!r}",
        errors,
    )
    _check_markdown_links(errors)
    _check_public_capabilities(errors)
    _check_no_forbidden_closeout_status(errors)

    generator = subprocess.run(
        [
            sys.executable,
            str(HARNESS_ROOT / "scripts" / "generate_gt_finalstand.py"),
            "--check",
        ],
        cwd=HARNESS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    _require(generator.returncode == 0,
             f"generated inventories drift: {generator.stdout}{generator.stderr}".strip(), errors)

    result = {
        "schema": "gt.finalstand.validation.v1",
        "ok": not errors,
        "counts": {
            "direct": len(direct),
            "role_audit": len(role_audit),
            "languages": len(languages),
            "language_operation_pairs": len(operation_rows),
            "todo_statuses": len(statuses),
        },
        "status_counts": dict(sorted(Counter(row["status"] for row in statuses).items())),
        "errors": errors,
    }
    return result


def main() -> int:
    result = validate()
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    (FINALSTAND / "validation_receipt.json").write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
