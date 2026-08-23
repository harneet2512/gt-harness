"""Fail-closed product certification over independently produced gate receipts."""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CERTIFIED_WITH_LIMITATIONS = "CERTIFIED_WITH_DECLARED_LIMITATIONS"

REQUIRED_STEPS = {
    "install",
    "doctor",
    "python_tests",
    "go_tests",
    "canonical_lint",
    "repository_matrix",
    "graph_truth",
    "graph_lifecycle",
    "language_lifecycle",
    "dense_model",
    "harness_e2e",
    "failure_campaign",
}

REQUIRED_RECEIPTS = {
    "real-repository-matrix.json": "gt.real_repository_matrix_receipt.v1",
    "graph-truth.json": "gt.graph_truth_audit_receipt.v1",
    "graph-lifecycle.json": "gt.graph_lifecycle_audit_receipt.v1",
    "language-lifecycle.json": "gt.language_support_audit_receipt.v1",
    "harness-e2e.json": "gt.harness_e2e_audit_receipt.v1",
    "failure-campaign.json": "gt.failure_campaign_receipt.v1",
}


@dataclass(frozen=True)
class CertificationError:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        stderr=subprocess.DEVNULL,
    ).strip()


def _find_receipt(bundle: Path, name: str) -> Path | None:
    direct = bundle / name
    if direct.is_file():
        return direct
    nested = bundle / "receipts" / name
    if nested.is_file():
        return nested
    matches = sorted(path for path in bundle.rglob(name) if path.is_file())
    return matches[0] if len(matches) == 1 else None


def _provider_free(value: dict[str, Any]) -> bool:
    return value.get("provider_calls") == 0 and value.get("provider_credentials_inspected") is False


def _rows(value: dict[str, Any], *names: str) -> list[dict[str, Any]]:
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    return []


def _check_specialized_receipt(
    filename: str, value: dict[str, Any], errors: list[CertificationError]
) -> None:
    def fail(code: str, message: str) -> None:
        errors.append(CertificationError(code, f"{filename}: {message}"))

    if filename == "real-repository-matrix.json":
        repositories = _rows(value, "repositories", "results")
        if len(repositories) < 10:
            fail(
                "matrix_too_small",
                f"expected at least 10 repositories, observed {len(repositories)}",
            )
        for row in repositories:
            if isinstance(row.get("build_receipt"), dict):
                receipt = row["build_receipt"]
            elif isinstance(row.get("receipt"), dict):
                receipt = row["receipt"]
            else:
                receipt = row
            if receipt.get("build_status") not in {
                "READY",
                "READY_WITH_DECLARED_LIMITATIONS",
            }:
                fail("matrix_graph_not_ready", str(row.get("name") or row.get("repository")))
            if receipt.get("query_ready") is not True:
                fail("matrix_query_not_ready", str(row.get("name") or row.get("repository")))
            if int(receipt.get("files_failed", 1)) != 0:
                fail("matrix_file_failures", str(row.get("name") or row.get("repository")))
    elif filename == "graph-truth.json":
        aggregate = value.get("aggregate")
        if not isinstance(aggregate, dict):
            aggregate = value.get("metrics") if isinstance(value.get("metrics"), dict) else value
        sampled = int(
            aggregate.get(
                "sample_size",
                aggregate.get("true_positives", aggregate.get("facts", 0)),
            )
        )
        if sampled < 60:
            fail("truth_sample_too_small", f"expected at least 60 facts, observed {sampled}")
        if float(aggregate.get("precision", -1.0)) < 0.95:
            fail("truth_precision_below_gate", str(aggregate.get("precision")))
        if float(aggregate.get("recall", -1.0)) < 0.95:
            fail("truth_recall_below_gate", str(aggregate.get("recall")))
    elif filename == "graph-lifecycle.json":
        cases = _rows(value, "phases", "cases", "checks")
        if len(cases) < 9 or any(row.get("status") != "PASS" for row in cases):
            fail("lifecycle_incomplete", f"observed {len(cases)} cases")
    elif filename == "language-lifecycle.json":
        rows = _rows(value, "languages", "results")
        required = {"python", "javascript", "typescript", "go", "rust", "java"}
        observed = {
            str(row.get("language", row.get("name", ""))).lower()
            for row in rows
            if row.get("status") == "PASS"
        }
        missing = sorted(required - observed)
        if missing:
            fail("languages_missing", ", ".join(missing))
    elif filename == "harness-e2e.json":
        if value.get("agent_scaffold_version") != "2.2.8":
            fail("harness_scaffold_mismatch", "Mini-SWE 2.2.8 was not exercised")
        if value.get("same_observation") is not True:
            fail("harness_delivery_timing", "GT update was not on the action observation")
        if value.get("raw_output_preserved") is not True:
            fail("harness_observation_mutated", "raw action output was not preserved")
        if value.get("restart_reused_current_graph") is not True:
            fail("harness_restart_failed", "updated graph identity was not reused")
        if value.get("retrieval_mode") != "hybrid_required":
            fail("harness_retrieval_mode", "release E2E did not require dense retrieval")
        if value.get("dense_lifecycle_ready") is not True:
            fail("harness_dense_lifecycle", "dense build/query/update/restart did not pass")
        dense_queries = _rows(value, "dense_queries")
        if not dense_queries or any(
            row.get("query_ready") is not True
            or int(row.get("candidate_count", 0)) < 1
            for row in dense_queries
        ):
            fail("harness_dense_query", "no non-empty exact-revision dense query receipt")
        if int(value.get("initial_context_token_count", 501)) > 500:
            fail("harness_initial_context_budget", "initial GT context exceeded 500 tokens")
        if int(value.get("update_context_token_count", 351)) > 350:
            fail("harness_update_context_budget", "GT update exceeded 350 tokens")
    elif filename == "failure-campaign.json":
        cases = _rows(value, "cases", "checks", "results")
        if len(cases) < 18 or any(row.get("status") != "PASS" for row in cases):
            fail("failure_campaign_incomplete", f"observed {len(cases)} cases")


def certify_receipt_bundle(
    receipt_dir: str | Path,
    *,
    repository: str | Path = ".",
    expected_commit: str | None = None,
) -> dict[str, Any]:
    """Validate the Gate 0-12 bundle without rerunning or weakening any gate."""

    bundle = Path(receipt_dir).resolve()
    root = Path(repository).resolve()
    errors: list[CertificationError] = []

    wrapper_path = _find_receipt(bundle, "codespaces-product-certification.json")
    wrapper: dict[str, Any] = {}
    if wrapper_path is None:
        errors.append(
            CertificationError("wrapper_missing", "Codespaces certification receipt missing")
        )
    else:
        try:
            wrapper = _read_json(wrapper_path)
        except ValueError as exc:
            errors.append(CertificationError("wrapper_invalid", str(exc)))

    try:
        current_commit = _git(root, "rev-parse", "HEAD")
        current_dirty = bool(_git(root, "status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError) as exc:
        current_commit = ""
        current_dirty = True
        errors.append(CertificationError("repository_invalid", str(exc)))

    subject_commit = expected_commit or current_commit
    if current_commit and expected_commit and expected_commit != current_commit:
        errors.append(
            CertificationError(
                "checkout_sha_mismatch",
                f"expected {expected_commit}, current checkout is {current_commit}",
            )
        )
    if current_dirty:
        errors.append(CertificationError("checkout_dirty", "current product checkout is not clean"))

    if wrapper:
        if wrapper.get("schema") != "gt.codespaces_product_certification.v1":
            errors.append(CertificationError("wrapper_schema", "unexpected wrapper schema"))
        if wrapper.get("status") != "PASS":
            errors.append(CertificationError("wrapper_failed", "campaign status is not PASS"))
        if wrapper.get("commit_sha") != subject_commit:
            errors.append(
                CertificationError(
                    "evidence_sha_mismatch",
                    f"receipt is for {wrapper.get('commit_sha')}, expected {subject_commit}",
                )
            )
        if wrapper.get("working_tree_state") != "clean":
            errors.append(
                CertificationError("evidence_checkout_dirty", "campaign checkout was dirty")
            )
        if not str(wrapper.get("platform", "")).startswith("Linux-"):
            errors.append(
                CertificationError("linux_evidence_missing", "campaign was not run on Linux")
            )
        if not _provider_free(wrapper):
            errors.append(CertificationError("provider_use", "campaign was not provider-free"))
        steps = _rows(wrapper, "steps")
        passed_steps = {str(row.get("name")) for row in steps if row.get("status") == "PASS"}
        missing_steps = sorted(REQUIRED_STEPS - passed_steps)
        if missing_steps:
            errors.append(
                CertificationError(
                    "steps_missing", f"missing passing steps: {', '.join(missing_steps)}"
                )
            )

    receipt_summaries: dict[str, dict[str, Any]] = {}
    for filename, schema in REQUIRED_RECEIPTS.items():
        path = _find_receipt(bundle, filename)
        if path is None:
            errors.append(CertificationError("receipt_missing", filename))
            continue
        try:
            value = _read_json(path)
        except ValueError as exc:
            errors.append(CertificationError("receipt_invalid", str(exc)))
            continue
        receipt_summaries[filename] = {
            "path": str(path),
            "schema": value.get("schema"),
            "status": value.get("status"),
            "completed": value.get("completed"),
        }
        if value.get("schema") != schema:
            errors.append(CertificationError("receipt_schema", f"{filename}: unexpected schema"))
        if value.get("status") != "PASS":
            errors.append(CertificationError("receipt_failed", f"{filename}: status is not PASS"))
        if not _provider_free(value):
            errors.append(CertificationError("provider_use", f"{filename}: not provider-free"))
        _check_specialized_receipt(filename, value, errors)

    limitations = [
        "Graph truth is certified on a bounded independently derived sample, not universal recall.",
        "Post-edit correctness uses an atomic full rebuild until file-keyed incremental parity "
        "is proven.",
        "Parser recovery and deliberate exclusions are explicit "
        "READY_WITH_DECLARED_LIMITATIONS states.",
        "Cold builds and graph storage are material on the largest certified repositories.",
        "Competitive superiority and paid-agent solve-rate uplift are not part of product "
        "certification.",
    ]
    return {
        "schema": "gt.product_certification.v1",
        "completed": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": CERTIFIED_WITH_LIMITATIONS if not errors else "NOT_CERTIFIED",
        "subject_commit": subject_commit,
        "current_commit": current_commit,
        "platform": platform.platform(),
        "provider_calls": 0,
        "provider_credentials_inspected": False,
        "evidence_wrapper": str(wrapper_path) if wrapper_path else None,
        "receipts": receipt_summaries,
        "limitations": limitations,
        "errors": [error.as_dict() for error in errors],
    }
