"""Feature proof matrix: per-identity audit cells with digest-bound evidence."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gt_engine.attribution import DIRECT_FEATURES

SCHEMA = "gt.feature_matrix.v1"
MARKDOWN_SCHEMA = "gt.feature_matrix.md.v1"

# Curated pytest nodes that exercise each attribution identity.
FEATURE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "caller_contract": (
        "tests/test_gt_engine.py::test_file_view_fires_verified_caller_contract",
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
    command = [sys.executable, "-m", "pytest", "-q", *nodes]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_digest_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_digest_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        "node_ids": list(nodes),
    }


def _disposition_from_evidence(evidence: dict[str, Any]) -> str:
    exit_code = int(evidence.get("exit_code", 1))
    if exit_code == 0:
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
    if not nodes:
        body: dict[str, Any] = {
            "identity": identity,
            "kind": spec["kind"],
            "disposition": "not_run",
            "trigger_source": "",
            "evidence": {
                "command": [],
                "exit_code": None,
                "stdout_digest_sha256": "",
                "stderr_digest_sha256": "",
                "node_ids": [],
                "reason": "no_evidence_binding",
            },
            "freshness_pins": {
                "source_revision": _git_head(repo_root) if execute else "",
            },
            "receipt_digest_sha256": None,
        }
        body["cell_digest_sha256"] = digest_body(body, field="cell_digest_sha256")
        return body

    evidence = _run_evidence(nodes, repo_root=repo_root) if execute else {
        "command": [sys.executable, "-m", "pytest", "-q", *nodes],
        "exit_code": None,
        "stdout_digest_sha256": "",
        "stderr_digest_sha256": "",
        "node_ids": list(nodes),
        "reason": "execution_skipped",
    }
    disposition = _disposition_from_evidence(evidence) if execute else "not_run"
    body = {
        "identity": identity,
        "kind": spec["kind"],
        "disposition": disposition,
        "trigger_source": nodes[0],
        "evidence": evidence,
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


def verify_matrix(matrix: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if matrix.get("schema") != SCHEMA:
        errors.append("schema mismatch")
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
        lines.append(
            "| {identity} | {kind} | {disposition} | `{trigger}` | {exit_code} | `{digest}` |".format(
                identity=row.get("identity", ""),
                kind=row.get("kind", ""),
                disposition=row.get("disposition", ""),
                trigger=row.get("trigger_source", ""),
                exit_code=evidence.get("exit_code", ""),
                digest=row.get("cell_digest_sha256", ""),
            )
        )
    lines.append("")
    return "\n".join(lines)
