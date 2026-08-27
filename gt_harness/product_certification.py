"""Fail-closed product certification over independently produced gate receipts."""

from __future__ import annotations

import ast
import glob
import json
import platform
import subprocess
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

CERTIFIED_WITH_LIMITATIONS = "CERTIFIED_WITH_DECLARED_LIMITATIONS"
PRODUCT_SURFACE_SCHEMA = "gt.product_surface.v1"

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
    "localization_source",
    "localization_truth",
    "localization_gate",
    "harness_e2e",
    "failure_campaign",
    "product_surface",
    "cli_verification",
}

REQUIRED_RECEIPTS = {
    "real-repository-matrix.json": "gt.real_repository_matrix_receipt.v1",
    "graph-truth.json": "gt.graph_truth_audit_receipt.v1",
    "graph-lifecycle.json": "gt.graph_lifecycle_audit_receipt.v1",
    "language-lifecycle.json": "gt.language_support_audit_receipt.v1",
    "harness-e2e.json": "gt.harness_e2e_audit_receipt.v1",
    "failure-campaign.json": "gt.failure_campaign_receipt.v1",
    "localization-truth.json": "gt.localization_truth_report.v2",
    "product-surface.json": "gt.product_surface_verification.v1",
    "verification-summary.json": "gt.cli_verification.v1",
}


@dataclass(frozen=True)
class CertificationError:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ProductSurface:
    python_modules: tuple[str, ...]
    console_entry_points: tuple[str, ...]
    benchmark_adapters: tuple[str, ...]
    dispatchable_workflows: tuple[str, ...]
    forbidden_modules: tuple[str, ...]
    schemas: dict[str, str]
    budgets: dict[str, int]
    languages: dict[str, tuple[str, ...]]


def load_product_surface(repository: str | Path = ".") -> ProductSurface:
    """Load the one release allowlist shared by packaging and certification."""

    root = Path(repository).resolve()
    path = root / "production-surface.toml"
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read production surface: {exc}") from exc
    if value.get("schema") != PRODUCT_SURFACE_SCHEMA:
        raise ValueError("unsupported product surface schema")

    def strings(name: str) -> tuple[str, ...]:
        rows = value.get(name)
        if not isinstance(rows, list) or not all(isinstance(row, str) and row for row in rows):
            raise ValueError(f"production surface {name} must be a non-empty string list")
        if len(rows) != len(set(rows)):
            raise ValueError(f"production surface {name} contains duplicates")
        return tuple(rows)

    language_rows = value.get("languages")
    if not isinstance(language_rows, dict):
        raise ValueError("production surface languages table is missing")
    return ProductSurface(
        python_modules=strings("python_modules"),
        console_entry_points=strings("console_entry_points"),
        benchmark_adapters=strings("benchmark_adapters"),
        dispatchable_workflows=strings("dispatchable_workflows"),
        forbidden_modules=strings("forbidden_modules"),
        schemas={str(key): str(item) for key, item in dict(value.get("schemas", {})).items()},
        budgets={str(key): int(item) for key, item in dict(value.get("budgets", {})).items()},
        languages={
            str(key): tuple(str(item) for item in items)
            for key, items in language_rows.items()
            if isinstance(items, list)
        },
    )


def _module_path(module: str) -> str:
    return module.replace(".", "/") + ".py"


def _workflow_path_entries(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    entries: list[str] = []
    for row in value.splitlines() or [value]:
        text = row.strip()
        if text and not text.startswith("#"):
            entries.append(text)
    return tuple(entries)


def _is_static_local_workflow_path(value: str) -> bool:
    path = value.strip()
    if not path:
        return False
    if any(marker in path for marker in ("${{", "$", "`", "://")):
        return False
    if path.startswith(("~", "/", "\\")):
        return False
    if len(path) > 1 and path[1] == ":":
        return False
    return True


def _local_workflow_path_exists(root: Path, relative: str) -> bool:
    normalized = relative[2:] if relative.startswith("./") else relative
    if glob.has_magic(normalized):
        return any((root / match).exists() for match in glob.glob(normalized, root_dir=root))
    return (root / normalized).exists()


def _validate_workflow_local_path(
    root: Path,
    workflow_name: str,
    label: str,
    value: object,
    errors: list[CertificationError],
) -> None:
    for relative in _workflow_path_entries(value):
        if not _is_static_local_workflow_path(relative):
            continue
        if not _local_workflow_path_exists(root, relative):
            errors.append(
                CertificationError(
                    "surface_workflow_local_path_missing",
                    f"{workflow_name}: {label} {relative}",
                )
            )


def _validate_dispatchable_workflow_paths(
    root: Path,
    workflow_name: str,
    errors: list[CertificationError],
) -> None:
    path = root / ".github" / "workflows" / workflow_name
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors.append(CertificationError("surface_workflow_invalid", f"{workflow_name}: {exc}"))
        return
    if not isinstance(document, dict):
        errors.append(
            CertificationError("surface_workflow_invalid", f"{workflow_name}: expected mapping")
        )
        return
    defaults = document.get("defaults")
    if isinstance(defaults, dict) and isinstance(defaults.get("run"), dict):
        _validate_workflow_local_path(
            root,
            workflow_name,
            "working-directory",
            defaults["run"].get("working-directory"),
            errors,
        )
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        defaults = job.get("defaults")
        if isinstance(defaults, dict) and isinstance(defaults.get("run"), dict):
            _validate_workflow_local_path(
                root,
                workflow_name,
                "working-directory",
                defaults["run"].get("working-directory"),
                errors,
            )
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if isinstance(uses, str) and uses.startswith("./"):
                _validate_workflow_local_path(
                    root,
                    workflow_name,
                    "local action",
                    uses[2:],
                    errors,
                )
            _validate_workflow_local_path(
                root,
                workflow_name,
                "working-directory",
                step.get("working-directory"),
                errors,
            )
            with_values = step.get("with")
            if not isinstance(with_values, dict):
                continue
            _validate_workflow_local_path(
                root,
                workflow_name,
                "go-version-file",
                with_values.get("go-version-file"),
                errors,
            )
            _validate_workflow_local_path(
                root,
                workflow_name,
                "cache-dependency-path",
                with_values.get("cache-dependency-path"),
                errors,
            )


def validate_product_surface(
    repository: str | Path = ".",
    *,
    wheel: str | Path | None = None,
) -> tuple[CertificationError, ...]:
    """Validate source/workflow and optional built-wheel equality fail closed."""

    root = Path(repository).resolve()
    try:
        surface = load_product_surface(root)
    except ValueError as exc:
        return (CertificationError("product_surface_invalid", str(exc)),)
    errors: list[CertificationError] = []
    expected_modules = set(surface.python_modules)
    for module in sorted(expected_modules):
        if not (root / _module_path(module)).is_file():
            errors.append(
                CertificationError("surface_module_missing", f"missing module: {module}")
            )
    # Historical sources may remain recoverable in the checkout while the
    # prerelease is stabilized. They are forbidden from the installed/runtime
    # dependency closure, not from Git history. Follow imports from every
    # canonical module and fail if one reaches a forbidden implementation.
    module_paths = {
        module: root / _module_path(module)
        for module in expected_modules
        if (root / _module_path(module)).is_file()
    }
    pending = list(module_paths)
    reached = set(pending)
    while pending:
        module = pending.pop()
        try:
            tree = ast.parse(module_paths[module].read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            continue
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        for candidate in imported:
            candidate_path = root / _module_path(candidate)
            if (
                candidate.startswith(("eval.", "gt_engine.", "gt_harness."))
                and candidate_path.is_file()
                and candidate not in expected_modules
            ):
                errors.append(
                    CertificationError(
                        "undeclared_runtime_module",
                        f"{module} imports undeclared production path {candidate}",
                    )
                )
            if any(
                candidate == forbidden or candidate.startswith(forbidden + ".")
                for forbidden in surface.forbidden_modules
            ):
                errors.append(
                    CertificationError(
                        "forbidden_module_reachable",
                        f"{module} imports forbidden production path {candidate}",
                    )
                )
            if candidate in module_paths and candidate not in reached:
                reached.add(candidate)
                pending.append(candidate)
    workflow_root = root / ".github" / "workflows"
    actual_workflows = {
        path.name for path in workflow_root.glob("*.yml") if path.is_file()
    } | {path.name for path in workflow_root.glob("*.yaml") if path.is_file()}
    expected_workflows = set(surface.dispatchable_workflows)
    for name in sorted(expected_workflows - actual_workflows):
        errors.append(CertificationError("surface_workflow_missing", name))
    for name in sorted(actual_workflows - expected_workflows):
        errors.append(CertificationError("surface_workflow_unexpected", name))
    for name in sorted(expected_workflows & actual_workflows):
        _validate_dispatchable_workflow_paths(root, name, errors)
    if wheel is not None:
        wheel_path = Path(wheel).resolve()
        try:
            with zipfile.ZipFile(wheel_path) as archive:
                actual_modules = {
                    name[:-3].replace("/", ".")
                    for name in archive.namelist()
                    if name.endswith(".py")
                    and ".dist-info/" not in name
                    and not name.endswith("/__init__.py")
                }
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append(CertificationError("surface_wheel_invalid", str(exc)))
        else:
            missing = sorted(expected_modules - actual_modules)
            unexpected = sorted(actual_modules - expected_modules)
            forbidden = sorted(
                module
                for module in actual_modules
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in surface.forbidden_modules
                )
            )
            if missing:
                errors.append(
                    CertificationError("surface_wheel_missing", ", ".join(missing))
                )
            if unexpected:
                errors.append(
                    CertificationError("surface_wheel_unexpected", ", ".join(unexpected))
                )
            if forbidden:
                errors.append(
                    CertificationError("surface_wheel_forbidden", ", ".join(forbidden))
                )
    return tuple(errors)


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
        if value.get("agent_scaffold_version") != "2.4.6":
            fail("harness_scaffold_mismatch", "Mini-SWE 2.4.6 was not exercised")
        if value.get("same_observation") is not True:
            fail("harness_delivery_timing", "GT update was not on the action observation")
        if value.get("context_schema_v7") is not True:
            fail("harness_context_schema", "context-v7 was not exercised end to end")
        if value.get("raw_output_preserved") is not True:
            fail("harness_observation_mutated", "raw action output was not preserved")
        if value.get("trajectory_delivery_receipt_preserved") is not True:
            fail(
                "harness_trajectory_receipt",
                "trajectory did not preserve the exact GT observation receipt",
            )
        if value.get("restart_reused_current_graph") is not True:
            fail("harness_restart_failed", "updated graph identity was not reused")
        if value.get("retrieval_mode") != "hybrid_required":
            fail("harness_retrieval_mode", "release E2E did not require dense retrieval")
        if value.get("dense_lifecycle_ready") is not True:
            fail("harness_dense_lifecycle", "dense build/query/update/restart did not pass")
        dense_queries = _rows(value, "dense_queries")
        if not dense_queries or any(
            row.get("query_ready") is not True or int(row.get("candidate_count", 0)) < 1
            for row in dense_queries
        ):
            fail("harness_dense_query", "no non-empty exact-revision dense query receipt")
        if int(value.get("initial_context_token_count", 501)) > 500:
            fail("harness_initial_context_budget", "initial GT context exceeded 500 tokens")
        if int(value.get("update_context_token_count", 351)) > 350:
            fail("harness_update_context_budget", "GT update exceeded 350 tokens")
        if int(value.get("total_context_token_count", 1_201)) > 1_200:
            fail("harness_total_context_budget", "GT context exceeded 1,200 tokens")
        provider_deliveries = _rows(value, "provider_delivery_receipts")
        serialized_claims = {
            str(claim)
            for delivery in provider_deliveries
            for claim in delivery.get("serialized_claim_ids", ())
        }
        delivered_claims = {str(claim) for claim in value.get("delivered_claim_ids", ())}
        if (
            value.get("delivery_reconciliation") != "PASS"
            or not provider_deliveries
            or serialized_claims != delivered_claims
        ):
            fail(
                "harness_delivery_reconciliation",
                "serialized provider claims do not equal the delivered claim ledger",
            )
        delivery_calls = [
            int(delivery.get("delivered_before_call", 0)) for delivery in provider_deliveries
        ]
        if delivery_calls[:1] != [1] or any(
            current <= previous
            for previous, current in zip(delivery_calls, delivery_calls[1:], strict=False)
        ):
            fail(
                "harness_provider_call_timing",
                f"invalid provider delivery calls: {delivery_calls}",
            )
    elif filename == "failure-campaign.json":
        cases = _rows(value, "cases", "checks", "results")
        if len(cases) < 18 or any(row.get("status") != "PASS" for row in cases):
            fail("failure_campaign_incomplete", f"observed {len(cases)} cases")
    elif filename == "localization-truth.json":
        summary = value.get("summary")
        if not isinstance(summary, dict):
            fail("localization_summary_missing", "summary object missing")
            return
        if summary.get("retrieval_mode") != "hybrid_required":
            fail("localization_not_hybrid", str(summary.get("retrieval_mode")))
        if int(summary.get("cases_run", -1)) != int(summary.get("cases_expected", -2)):
            fail(
                "localization_task_set_incomplete",
                f"expected {summary.get('cases_expected')}, ran {summary.get('cases_run')}",
            )
        for key in (
            "case_failures",
            "missing_oracle_tasks",
            "extra_oracle_tasks",
            "tasks_with_false_edit_authority",
            "tasks_below_half_required_coverage",
            "treatment_failures",
            "dense_not_ready_tasks",
        ):
            if summary.get(key):
                fail("localization_gate_failure", f"{key}: {summary[key]}")
        try:
            exact_precision = float(summary.get("mean_exact_edit_precision"))
        except (TypeError, ValueError):
            exact_precision = -1.0
        try:
            facet_coverage = float(summary.get("mean_required_facet_coverage"))
        except (TypeError, ValueError):
            facet_coverage = -1.0
        try:
            implementation_precision = float(summary.get("implementation_role_precision"))
        except (TypeError, ValueError):
            implementation_precision = -1.0
        if exact_precision < 0.95:
            fail(
                "localization_precision_below_gate",
                str(summary.get("mean_exact_edit_precision")),
            )
        if facet_coverage < 0.85:
            fail(
                "localization_coverage_below_gate",
                str(summary.get("mean_required_facet_coverage")),
            )
        if implementation_precision < 0.50:
            fail(
                "localization_implementation_precision_below_gate",
                str(summary.get("implementation_role_precision")),
            )


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
