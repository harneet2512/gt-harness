"""Feature proof matrix: per-identity audit cells with digest-bound evidence."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gt_engine.attribution import DIRECT_FEATURES

SCHEMA = "gt.feature_matrix.v2"
MARKDOWN_SCHEMA = "gt.feature_matrix.md.v2"

# Curated pytest nodes that exercise each attribution identity.
FEATURE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "caller_contract": (
        "tests/test_gt_engine.py::test_file_view_fires_verified_caller_contract",
    ),
    "cochange_prior": (
        "tests/test_gt_attribution.py::test_cochange_evidence_binds_to_dark_trigger_identity",
    ),
    "covering_red": (
        "tests/test_gt_engine.py::test_covering_red_fires_at_post_edit",
    ),
    "def_partition": (
        "tests/test_gt_engine.py::test_bridge_delivers_sealed_pure_suffix",
    ),
    "localization": (
        "tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot",
    ),
    "newfile_precedent": (
        "tests/test_miniswe_runtime.py::test_newfile_precedent_delivered_on_file_create",
    ),
    "obligations": (
        "tests/test_gt_engine.py::test_submit_certificate_receives_obligation_coverage",
    ),
    "recovery": (
        "tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit",
    ),
    "signature_delta": (
        "tests/test_gt_engine.py::test_edit_fires_signature_mismatch_under_profile_2",
    ),
    "submit_refusal": (
        "tests/test_gt_engine.py::test_sdlc_submit_refuses_edit_without_post_edit_verification",
    ),
    "syntax_result": (
        "tests/test_gt_engine.py::test_post_edit_syntax_failure_delivers_immediately",
    ),
    "select_catalog": (
        "tests/test_persistent_execution_state.py::"
        "test_feature18_selection_lifecycle_is_content_safe_and_action_bound",
    ),
    "GT_CERT_DELIVERY": (
        "tests/test_gt_engine.py::test_bridge_proves_exact_delivery_exposure",
    ),
    "GT_CHANGE_SURFACE": (
        "tests/test_gt_engine.py::"
        "test_repeated_failed_search_fires_newfile_precedent_and_change_surface",
    ),
    "GT_EDIT_CHECK": (
        "tests/test_gt_attribution.py::"
        "test_executed_clean_edit_check_is_witnessed_but_no_target_is_ineligible",
    ),
    "GT_HYPOTHESIS": (
        "tests/test_gt_engine.py::test_recovery_fires_on_same_failure_recurring_across_edit",
    ),
    "GT_LOC_RESLOT": (
        "tests/test_gt_engine.py::test_search_fires_ranked_localization_and_loc_reslot",
    ),
    "GT_PATCH_DELTA": (
        "tests/test_runtime_observation.py::"
        "test_python_signature_delta_distinguishes_body_and_signature_edits",
    ),
    "GT_SS_SUBMIT_RED": (
        "tests/test_gt_engine.py::test_submit_red_blocks_on_unresolved_observed_fail",
    ),
}

# Every identity also needs an abstention/ineligibility/staleness witness. A
# positive firing test alone cannot show that the feature is deterministic or
# safe for Mini-SWE when its prerequisites are absent.
FEATURE_NEGATIVE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "caller_contract": (
        "tests/test_gt_engine.py::test_bridge_records_gateway_no_candidate_reason",
    ),
    "cochange_prior": (
        "tests/test_gt_attribution.py::test_authority_abstention_is_named_suppression_not_triggered_dark",
    ),
    "covering_red": (
        "tests/test_gt_engine.py::test_covering_green_stays_quiet_and_rechecks_after_later_edit",
    ),
    "def_partition": (
        "tests/test_gt_engine.py::test_smart_truncate_byte_identical_when_no_deliveries",
    ),
    "localization": (
        "tests/test_gt_engine.py::test_task_start_abstains_without_issue_text",
    ),
    "newfile_precedent": (
        "tests/test_miniswe_runtime.py::test_newfile_precedent_is_quiet_without_inspectable_sibling",
    ),
    "obligations": (
        "tests/test_miniswe_runtime.py::test_submit_magic_string_executes_when_no_red_evidence",
    ),
    "recovery": (
        "tests/test_gt_engine.py::test_recovery_quiet_on_different_failure",
    ),
    "signature_delta": (
        "tests/test_runtime_observation.py::test_python_signature_delta_distinguishes_body_and_signature_edits",
    ),
    "submit_refusal": (
        "tests/test_miniswe_runtime.py::test_submit_magic_string_executes_when_no_red_evidence",
    ),
    "syntax_result": (
        "tests/test_miniswe_runtime.py::test_newfile_precedent_does_not_preempt_executed_syntax_failure",
    ),
    "select_catalog": (
        "tests/test_persistent_execution_state.py::test_feature18_rejects_duplicate_and_out_of_catalog_ids_without_consumption",
    ),
    "GT_CERT_DELIVERY": (
        "tests/test_miniswe_runtime.py::test_final_provider_refusal_does_not_consume_gt_delivery",
    ),
    "GT_CHANGE_SURFACE": (
        "tests/test_gt_engine.py::test_new_file_change_surface_records_correct_quiet_execution",
    ),
    "GT_EDIT_CHECK": (
        "tests/test_gt_attribution.py::test_executed_clean_edit_check_is_witnessed_but_no_target_is_ineligible",
    ),
    "GT_HYPOTHESIS": (
        "tests/test_gt_engine.py::test_recovery_quiet_without_intervening_edit",
    ),
    "GT_LOC_RESLOT": (
        "tests/test_gt_engine.py::test_task_start_empty_brief_is_named_correct_quiet",
    ),
    "GT_PATCH_DELTA": (
        "tests/test_runtime_observation.py::test_python_signature_delta_distinguishes_body_and_signature_edits",
    ),
    "GT_SS_SUBMIT_RED": (
        "tests/test_gt_engine.py::test_submit_red_quiet_when_no_test_ever_observed",
    ),
}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest_body(body: dict[str, Any], *, field: str) -> str:
    payload = {k: v for k, v in body.items() if k != field}
    return hashlib.sha256(canonical(payload)).hexdigest()


def _git_head(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run_evidence(nodes: tuple[str, ...], *, repo_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="gt-pytest-evidence-") as temporary:
        report_path = Path(temporary) / "execution.json"
        command = [sys.executable, str(Path(__file__).with_name("pytest_evidence.py")),
                   str(report_path), "-q", *nodes]
        completed = subprocess.run(
            command, cwd=repo_root, capture_output=True, check=False,
        )
        try:
            execution = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            execution = None
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_digest_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_digest_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        "node_ids": list(nodes),
        "execution": execution,
    }


def _witness_passed(witness: Any) -> bool:
    if not isinstance(witness, dict) or type(witness.get("exit_code")) is not int:
        return False
    if witness["exit_code"] != 0:
        return False
    nodes = witness.get("node_ids")
    execution = witness.get("execution")
    if not isinstance(nodes, list) or not nodes or not isinstance(execution, dict):
        return False
    collected = execution.get("collected")
    reports = execution.get("reports")
    if not isinstance(collected, list) or not collected or not isinstance(reports, list):
        return False
    if not all(isinstance(node, str) and node for node in nodes + collected):
        return False
    if len(set(collected)) != len(collected):
        return False
    def matches(actual, requested):
        return actual == requested or actual.startswith(requested + "[")
    if not all(any(matches(actual, node) for actual in collected) for node in nodes):
        return False
    if not all(any(matches(actual, node) for node in nodes) for actual in collected):
        return False
    phases = {node: [] for node in collected}
    for report in reports:
        if (not isinstance(report, dict)
                or not isinstance(report.get("node_id"), str)
                or report["node_id"] not in phases):
            return False
        if report.get("outcome") != "passed" or report.get("wasxfail") is not False:
            return False
        phases[report["node_id"]].append(report.get("phase"))
    return all(value == ["setup", "call", "teardown"] for value in phases.values())


def _disposition_from_evidence(evidence: dict[str, Any]) -> str:
    positive = evidence.get("positive") or {}
    negative = evidence.get("negative") or {}
    if _witness_passed(positive) and _witness_passed(negative):
        return "WITNESSED"
    return "not_run"


def build_cell(
    identity: str,
    *,
    repo_root: Path,
    execute: bool = True,
) -> dict[str, Any]:
    spec = DIRECT_FEATURES[identity]
    nodes = FEATURE_EVIDENCE.get(identity, ())
    negative_nodes = FEATURE_NEGATIVE_EVIDENCE.get(identity, ())
    if not nodes or not negative_nodes:
        body: dict[str, Any] = {
            "identity": identity,
            "kind": spec["kind"],
            "disposition": "not_run",
            "trigger_source": "",
            "evidence": {
                "positive": {"node_ids": []},
                "negative": {"node_ids": []},
                "reason": "no_evidence_binding",
            },
            "freshness_pins": {
                "source_revision": _git_head(repo_root) if execute else "",
            },
            "receipt_digest_sha256": None,
        }
        body["cell_digest_sha256"] = digest_body(body, field="cell_digest_sha256")
        return body

    if execute:
        evidence = {
            "positive": _run_evidence(nodes, repo_root=repo_root),
            "negative": _run_evidence(negative_nodes, repo_root=repo_root),
        }
    else:
        evidence = {
            "positive": {
                "command": [sys.executable, "-m", "pytest", "-q", *nodes],
                "exit_code": None,
                "node_ids": list(nodes),
            },
            "negative": {
                "command": [sys.executable, "-m", "pytest", "-q", *negative_nodes],
                "exit_code": None,
                "node_ids": list(negative_nodes),
            },
            "reason": "execution_skipped",
        }
    disposition = _disposition_from_evidence(evidence) if execute else "not_run"
    body = {
        "identity": identity,
        "kind": spec["kind"],
        "disposition": disposition,
        "trigger_source": nodes[0],
        "evidence": evidence,
        "proof_dimensions": {
            "produced": nodes[0],
            "admitted": nodes[0],
            "sent_or_correct_quiet": nodes[0],
            "behaviorally_relevant": nodes[0],
            "negative_or_stale": negative_nodes[0],
        },
        "freshness_pins": {
            "source_revision": _git_head(repo_root),
        },
        "receipt_digest_sha256": None,
    }
    body["cell_digest_sha256"] = digest_body(body, field="cell_digest_sha256")
    return body


def build_matrix(*, repo_root: Path, execute: bool = True) -> dict[str, Any]:
    identities = sorted(DIRECT_FEATURES)
    rows = [
        build_cell(identity, repo_root=repo_root, execute=execute)
        for identity in identities
    ]
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "source_revision": _git_head(repo_root),
        "generated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "identity_count": len(rows),
        "rows": rows,
    }
    body["matrix_digest_sha256"] = digest_body(body, field="matrix_digest_sha256")
    return body


def verify_matrix(
    matrix: dict[str, Any],
    *,
    expected_source_revision: str | None = None,
    require_witnessed: bool = False,
) -> list[str]:
    errors: list[str] = []
    if matrix.get("schema") != SCHEMA:
        errors.append("schema mismatch")
    if (
        expected_source_revision is not None
        and matrix.get("source_revision") != expected_source_revision
    ):
        errors.append("source_revision does not match checkout HEAD")
    supplied = matrix.get("matrix_digest_sha256")
    if not isinstance(supplied, str):
        errors.append("missing matrix_digest_sha256")
    elif digest_body(matrix, field="matrix_digest_sha256") != supplied:
        errors.append("matrix_digest_sha256 mismatch")

    rows = matrix.get("rows")
    if not isinstance(rows, list):
        errors.append("rows must be a list")
        return errors

    identities = {row.get("identity") for row in rows if isinstance(row, dict)}
    expected = set(DIRECT_FEATURES)
    if identities != expected:
        missing = sorted(expected - identities)
        extra = sorted(identities - expected)
        if missing:
            errors.append(f"missing identities: {', '.join(missing)}")
        if extra:
            errors.append(f"unexpected identities: {', '.join(extra)}")

    for row in rows:
        if not isinstance(row, dict):
            errors.append("row is not an object")
            continue
        cell_digest = row.get("cell_digest_sha256")
        if not isinstance(cell_digest, str):
            errors.append(f"{row.get('identity')}: missing cell_digest_sha256")
            continue
        if digest_body(row, field="cell_digest_sha256") != cell_digest:
            errors.append(f"{row.get('identity')}: cell_digest_sha256 mismatch")
        evidence = row.get("evidence")
        if not isinstance(evidence, dict):
            errors.append(f"{row.get('identity')}: evidence object required")
            continue
        if (row.get("disposition") == "WITNESSED"
                and _disposition_from_evidence(evidence) != "WITNESSED"):
            errors.append(f"{row.get('identity')}: WITNESSED lacks passing execution receipts")
        for polarity in ("positive", "negative"):
            witness = evidence.get(polarity)
            if (
                not isinstance(witness, dict)
                or not isinstance(witness.get("node_ids"), list)
                or not witness.get("node_ids")
            ):
                errors.append(f"{row.get('identity')}: {polarity} witness required")
        dimensions = row.get("proof_dimensions")
        required = {
            "produced", "admitted", "sent_or_correct_quiet",
            "behaviorally_relevant", "negative_or_stale",
        }
        if not isinstance(dimensions, dict) or set(dimensions) != required:
            errors.append(f"{row.get('identity')}: proof dimensions incomplete")
        if require_witnessed:
            identity = row.get("identity")
            if row.get("disposition") != "WITNESSED":
                errors.append(f"{identity}: disposition is not WITNESSED")
            for polarity in ("positive", "negative"):
                witness = evidence.get(polarity)
                if not _witness_passed(witness):
                    errors.append(f"{identity}: {polarity} witness did not pass")
    return errors


def render_markdown(matrix: dict[str, Any]) -> str:
    lines = [
        "# GT Feature Proof Matrix",
        "",
        f"Schema: `{MARKDOWN_SCHEMA}`",
        f"Source revision: `{matrix.get('source_revision', '')}`",
        f"Generated at: `{matrix.get('generated_at', '')}`",
        f"Matrix digest: `{matrix.get('matrix_digest_sha256', '')}`",
        "",
        "| Identity | Kind | Disposition | Trigger | Evidence exit | Cell digest |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in matrix.get("rows", ()):
        if not isinstance(row, dict):
            continue
        evidence = row.get("evidence") or {}
        positive = evidence.get("positive") or {}
        negative = evidence.get("negative") or {}
        lines.append(
            "| {identity} | {kind} | {disposition} | `{trigger}` | {exit_code} | `{digest}` |".format(
                identity=row.get("identity", ""),
                kind=row.get("kind", ""),
                disposition=row.get("disposition", ""),
                trigger=row.get("trigger_source", ""),
                exit_code=f"{positive.get('exit_code', '')}/{negative.get('exit_code', '')}",
                digest=row.get("cell_digest_sha256", ""),
            )
        )
    lines.append("")
    return "\n".join(lines)
