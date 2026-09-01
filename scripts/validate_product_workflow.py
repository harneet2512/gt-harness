"""Static reachability and secret-boundary validation for the product workflow."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

_ACTION = re.compile(r"uses:\s*[^@\s]+@([^\s#]+)")
_MODULE = re.compile(
    r"(?:python\s+-m|--agent-import-path)\s+([A-Za-z_][\w.]*)(?::([A-Za-z_]\w*))?"
)
_PATH = re.compile(r"(?:--manifest|--workflow)\s+([^\s\\]+)")


def validate_workflow(workflow: Path, *, root: Path) -> list[str]:
    text = workflow.read_text(encoding="utf-8")
    failures: list[str] = []
    paid = "approve_paid_run" in text
    if "${{ secrets." in text and not paid:
        failures.append("workflow_secret_reference")
    if paid:
        required_paid_controls = (
            "inputs.approve_paid_run == true",
            "python -m scripts.provider_preflight",
            "config/provider_route.v1.json",
            "needs: [plan, provider_gate]",
            "secrets.OPENROUTER_API_KEY",
        )
        if any(control not in text for control in required_paid_controls):
            failures.append("paid_provider_gate_incomplete")
    if "|| true" in text:
        failures.append("unobservable_failure_suppression")
    if not paid and (
        "scripts/gt_product_acceptance.py" not in text or "--fake-provider" not in text
    ):
        failures.append("product_acceptance_unreachable")
    for revision in _ACTION.findall(text):
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            failures.append(f"action_not_pinned:{revision}")
    for module_name, class_name in _MODULE.findall(text):
        if module_name in {"pip", "pytest"}:
            continue
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            failures.append(f"module_missing:{module_name}")
            continue
        if class_name:
            module = __import__(module_name, fromlist=[class_name])
            if not hasattr(module, class_name):
                failures.append(f"class_missing:{module_name}:{class_name}")
    for raw in _PATH.findall(text):
        candidate = raw.strip("'\"")
        if "$" not in candidate and not (root / candidate).exists():
            failures.append(f"path_missing:{candidate}")
    manifest_path = root / "config" / "deepswe_product_bundle_v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lock_path = root / "config" / "product-requirements.lock"
    lock_text = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else ""
    if (
        "--require-hashes -r config/product-requirements.lock" not in text
        or f'mini-swe-agent=={manifest["miniswe_agent_version"]}' not in lock_text
    ):
        failures.append("miniswe_version_not_reached")
    if not paid and ("provider_calls" not in text or "benchmark_runs" not in text):
        failures.append("zero_spend_assertion_missing")
    return sorted(set(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    failures = validate_workflow(args.workflow, root=root)
    print(json.dumps({"schema": "gt.workflow_reachability.v1", "failures": failures}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
