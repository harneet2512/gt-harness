#!/usr/bin/env python3
"""Replay known localization regressions against exact repository revisions.

The task text is the only input to GroundTruth.  Successful comparator patch
paths are applied only after context generation as a post-hoc audit oracle.
No model or provider is invoked.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from gt_harness.treatments import GroundTruthTreatment


@dataclass(frozen=True)
class ReplayCase:
    commit: str
    task: str
    existing_oracle_paths: tuple[str, ...]
    new_oracle_paths: tuple[str, ...] = ()


CASES = {
    "awilix": ReplayCase(
        commit="82ac179c1de4c216c4e333093044fac643303f0c",
        task="""Add support for asynchronous initialization of container
registrations with automatic dependency-aware startup ordering.

The API adds an initializer to asClass/asFunction registrations and
container.initialize({ concurrency }). Initialization is idempotent,
dependency-level ordered, concurrent within each level, and rolls back already
initialized services in reverse order after a failure. Add
AwilixNotInitializedError and AwilixInitializationError behavior. Circular
dependency failures during graph construction must remain retryable. Expose the
public API and verify the behavior.""",
        existing_oracle_paths=(
            "src/container.ts",
            "src/errors.ts",
            "src/resolvers.ts",
            "src/awilix.ts",
            "src/__tests__/container.initialization.test.ts",
        ),
    ),
    "boa": ReplayCase(
        commit="70409a5052984325dccfdc5f6520818568a81f39",
        task="""Implement evaluation cancellation with parent/child
EvaluationHandle values and cancellation checkpoints.

Public capabilities include Context::{new_evaluation_handle,
new_child_evaluation_handle, eval_with_evaluation,
enqueue_job_with_evaluation, run_jobs_with_evaluation},
Script::evaluate_with_evaluation,
Module::{evaluate_with_evaluation, load_link_evaluate_with_evaluation}, and
EvaluationHandle::{child, cancel, cancel_with_reason, is_cancelled,
cancellation_reason}. Cancellation must cascade from parent to descendants, be
first-wins, preserve Context usability, propagate reasons through module
promises, and skip associated queued jobs at cancellation checkpoints. Expose
the crate surface and add cancellation tests.""",
        existing_oracle_paths=(
            "core/engine/src/builtins/promise/mod.rs",
            "core/engine/src/context/mod.rs",
            "core/engine/src/error/mod.rs",
            "core/engine/src/job.rs",
            "core/engine/src/lib.rs",
            "core/engine/src/module/mod.rs",
            "core/engine/src/script.rs",
            "core/engine/src/vm/mod.rs",
        ),
        new_oracle_paths=(
            "core/engine/src/evaluation.rs",
            "core/engine/tests/evaluation_cancellation.rs",
        ),
    ),
}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, encoding="utf-8"
    ).strip()


def _path_lines(context: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in context.splitlines():
        for prefix in (
            "EXACT_EDIT_TARGET ",
            "INSPECT_PUBLIC_SURFACE ",
            "INSPECT_INTEGRATION ",
            "AFFECTED_TEST ",
            "PROPOSED_NEW_FILE ",
            "BOUNDED_PROCESS ",
            "BOUNDED_IMPACT ",
        ):
            if line.startswith(prefix):
                path = line.removeprefix(prefix).split(":", 1)[0].split(" ", 1)[0]
                found.setdefault(path, []).append(prefix.strip())
    return found


def replay(case_name: str, repository: Path, state_dir: Path) -> dict[str, object]:
    case = CASES[case_name]
    actual_commit = _git(repository, "rev-parse", "HEAD")
    if actual_commit != case.commit:
        raise RuntimeError(
            f"repository revision mismatch: expected {case.commit}, got {actual_commit}"
        )
    if _git(repository, "status", "--porcelain"):
        raise RuntimeError("replay repository must have a clean working tree")

    treatment = GroundTruthTreatment(
        repository, state_dir=state_dir, retrieval_mode="sparse_only"
    )
    context = treatment.prepare(case.task)
    receipt = treatment.finalize(None)
    delivered = _path_lines(context)
    existing_hits = tuple(
        path for path in case.existing_oracle_paths if path in delivered
    )
    proposed_hits = tuple(path for path in case.new_oracle_paths if path in delivered)
    return {
        "schema": "gt.localization_regression_replay.v1",
        "case": case_name,
        "repository": str(repository.resolve()),
        "commit": actual_commit,
        "task_is_only_retrieval_input": True,
        "oracle_applied_post_hoc": True,
        "graph_available": receipt.get("graph_available"),
        "source_revision": receipt.get("source_revision"),
        "graph_identity": receipt.get("graph_identity"),
        "treatment_status": receipt.get("treatment_status"),
        "context_schema_v5": 'schema="gt.agent_context.v5"' in context,
        "context": context,
        "delivered_paths": delivered,
        "existing_oracle_paths": case.existing_oracle_paths,
        "existing_hits": existing_hits,
        "existing_recall": len(existing_hits) / len(case.existing_oracle_paths),
        "new_oracle_paths": case.new_oracle_paths,
        "proposed_new_file_hits": proposed_hits,
        "feature_states": receipt.get("feature_states"),
        "feature_paths": receipt.get("feature_paths"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=tuple(CASES), required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.case, args.repository, args.state_dir)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
