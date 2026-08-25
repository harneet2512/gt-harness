from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gt_harness.treatments import GroundTruthTreatment


def _git_repository(root: Path, files: dict[str, str]) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "GT Fixture"], cwd=root, check=True
    )
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)


@pytest.mark.real_graph
def test_awilix_shape_keeps_implementation_public_surface_and_test_roles(
    tmp_path: Path,
) -> None:
    root = tmp_path / "awilix-shape"
    _git_repository(
        root,
        {
            "package.json": '{"exports":"./src/awilix.ts"}',
            "src/container.ts": (
                "export interface AwilixContainer { resolve(name: string): unknown }\n"
            ),
            "src/resolvers.ts": "export function resolve(name: string) { return name }\n",
            "src/errors.ts": "export class AwilixError extends Error {}\n",
            "src/awilix.ts": "export { AwilixContainer } from './container'\n",
            "src/__tests__/container.initialization.test.ts": (
                "import { AwilixContainer } from '../awilix'\n"
            ),
        },
    )
    treatment = GroundTruthTreatment(root, state_dir=tmp_path / "state")

    context = treatment.prepare(
        "Add async `AwilixContainer.initialize` and expose it through the public API."
    )

    assert "EXACT_EDIT_TARGET src/container.ts" in context
    assert "INSPECT_PUBLIC_SURFACE src/awilix.ts" in context
    assert "src/awilix.ts" not in "\n".join(
        line for line in context.splitlines() if line.startswith("EXACT_EDIT_TARGET")
    )
    assert 'schema="gt.agent_context.v6"' in context


@pytest.mark.real_graph
def test_boa_shape_keeps_existing_integration_surfaces_and_labels_new_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "boa-shape"
    _git_repository(
        root,
        {
            "core/engine/src/job.rs": "pub fn run_jobs() {}\n",
            "core/engine/src/context/mod.rs": "pub struct Context;\n",
            "core/engine/src/script.rs": "pub fn evaluate() {}\n",
            "core/engine/src/module/mod.rs": "pub fn load_link_evaluate() {}\n",
            "core/engine/src/vm/mod.rs": "pub fn execute() {}\n",
            "core/engine/src/error.rs": "pub struct JsError;\n",
            "core/engine/src/lib.rs": (
                "pub mod context; pub mod error; pub mod job; pub mod module; "
                "pub mod script; pub mod vm;\n"
            ),
            "core/engine/src/source.rs": "pub fn transition() {}\n",
        },
    )
    treatment = GroundTruthTreatment(root, state_dir=tmp_path / "state")

    context = treatment.prepare(
        "Add `EvaluationHandle::cancel_with_reason` and "
        "`run_jobs_with_evaluation` while preserving `run_jobs` and crate exports."
    )

    assert "EXACT_EDIT_TARGET core/engine/src/job.rs" in context
    assert "INSPECT_PUBLIC_SURFACE core/engine/src/lib.rs" in context
    assert "PROPOSED_NEW_FILE core/engine/src/evaluation.rs fact=false" in context
    assert "UNCOVERED_FACET" in context
    assert not any(
        "source.rs" in line and "transition" in line
        for line in context.splitlines()
        if line.startswith(("EXACT_EDIT_TARGET", "BOUNDED_PROCESS", "BOUNDED_IMPACT"))
    )
