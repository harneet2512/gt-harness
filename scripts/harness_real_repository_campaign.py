#!/usr/bin/env python3
"""Provider-free GT Harness product-boundary lifecycle campaign.

This certifies the actual Mini-SWE treatment seam, not an MCP substitute.  A
real repository is cloned at an exact revision, GT builds its graph, an action
changes a real source file, the resulting context is attached to that same raw
observation, and a fresh treatment reopens the updated persistent state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from minisweagent.agents.default import AgentConfig

from gt_harness.miniswe_runner import TreatmentMiniSweAgent
from gt_harness.treatments import GroundTruthTreatment, _bounded_token_count

_TARGET = re.compile(r"^EXACT_EDIT_TARGET ([^:]+):\d+#", re.MULTILINE)


def _run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(
        list(args), cwd=cwd, text=True, encoding="utf-8", stderr=subprocess.STDOUT
    ).strip()


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
    temporary.replace(path)


class _ObservationModel:
    def get_template_vars(self) -> dict[str, Any]:
        return {}

    def format_observation_messages(self, _message, outputs, _variables):
        return ({"role": "user", "content": outputs[0]["output"], "extra": {}},)

    def serialize(self) -> dict[str, Any]:
        return {}


class _EditingEnvironment:
    def __init__(self, root: Path, target: str) -> None:
        self.root = root
        self.target = target
        self.raw_output = f"updated {target}"

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        if action != {"command": f"edit {self.target}"}:
            raise AssertionError(f"unexpected action: {action}")
        path = self.root / self.target
        path.write_text(
            path.read_text(encoding="utf-8") + "\n# gt harness e2e lifecycle probe\n",
            encoding="utf-8",
        )
        return {"output": self.raw_output, "returncode": 0, "exception_info": ""}

    def get_template_vars(self) -> dict[str, Any]:
        return {}

    def serialize(self) -> dict[str, Any]:
        return {}


def run_campaign(
    *, source_repository: Path, commit: str, run_dir: Path, output: Path
) -> dict[str, Any]:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    _run("git", "clone", "--no-local", "--quiet", str(source_repository), str(run_dir))
    _run("git", "checkout", "--detach", "--quiet", commit, cwd=run_dir)
    exact_commit = _run("git", "rev-parse", "HEAD", cwd=run_dir)
    if exact_commit != commit:
        raise RuntimeError(f"checkout mismatch: expected {commit}, got {exact_commit}")

    state = run_dir.parent / f"{run_dir.name}-gt-state"
    treatment = GroundTruthTreatment(run_dir, state_dir=state, retrieval_mode="sparse_only")
    initial = treatment.prepare("Inspect Signer behavior and its callers before changing it")
    match = _TARGET.search(initial)
    if match is None:
        raise RuntimeError("GT did not produce an exact source target")
    target = match.group(1)
    before = treatment.finalize(None)

    environment = _EditingEnvironment(run_dir, target)
    agent = TreatmentMiniSweAgent(
        _ObservationModel(),
        environment,
        config_class=AgentConfig,
        system_template="system",
        instance_template="{{task}}",
        step_limit=2,
        cost_limit=0.0,
        treatment=treatment,
    )
    observation = agent.execute_actions(
        {
            "role": "assistant",
            "content": "edit",
            "extra": {"actions": [{"command": f"edit {target}"}]},
        }
    )[0]
    after = treatment.finalize(None)
    content = str(observation.get("content") or "")
    same_observation = bool(
        content.startswith(environment.raw_output)
        and "<groundtruth-repository-context" in content
        and 'kind="repository_update"' in content
    )
    if not same_observation:
        raise RuntimeError("GT update was not bound to the raw action observation")
    if treatment.before_model_call(2) != "":
        raise RuntimeError("GT attempted late synthetic-user context injection")

    restarted = GroundTruthTreatment(run_dir, state_dir=state, retrieval_mode="sparse_only")
    restart_context = restarted.prepare(
        "Inspect Signer behavior and its callers before changing it"
    )
    reopened = restarted.finalize(None)
    restart_reused_current_graph = bool(
        reopened["graph_available"]
        and reopened["source_revision"] == after["source_revision"]
        and reopened["graph_identity"] == after["graph_identity"]
        and restart_context
    )
    if not restart_reused_current_graph:
        raise RuntimeError("restart did not reuse the exact updated graph identity")

    delivery = after["delivery_receipts"][-1]
    receipt = {
        "schema": "gt.harness_e2e_audit_receipt.v1",
        "status": "PASS",
        "product_boundary": "gt-harness run / TreatmentMiniSweAgent",
        "agent_scaffold": "mini-swe-agent",
        "agent_scaffold_version": "2.2.8",
        "repository": str(run_dir),
        "commit_sha": exact_commit,
        "target": target,
        "initial_graph_identity": before["graph_identity"],
        "updated_graph_identity": after["graph_identity"],
        "initial_source_revision": before["source_revision"],
        "updated_source_revision": after["source_revision"],
        "same_observation": same_observation,
        "raw_output_preserved": content.startswith(environment.raw_output),
        "raw_output_sha256": hashlib.sha256(
            environment.raw_output.encode("utf-8")
        ).hexdigest(),
        "delivery_receipt": delivery,
        "initial_context_token_count": _bounded_token_count(initial),
        "update_context_token_count": int(delivery["context_token_count"]),
        "before_model_call_injected_context": False,
        "restart_reused_current_graph": restart_reused_current_graph,
        "provider_calls": 0,
        "provider_credentials_inspected": False,
    }
    _write_atomic(output, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = run_campaign(
            source_repository=args.source_repository.resolve(),
            commit=args.commit,
            run_dir=args.run_dir.resolve(),
            output=args.output.resolve(),
        )
    except Exception as exc:  # noqa: BLE001 - command boundary writes explicit failure
        _write_atomic(
            args.output.resolve(),
            {
                "schema": "gt.harness_e2e_audit_receipt.v1",
                "status": "FAIL",
                "error_type": type(exc).__name__,
                "error": " ".join(str(exc).split())[:2000],
                "provider_calls": 0,
                "provider_credentials_inspected": False,
            },
        )
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
