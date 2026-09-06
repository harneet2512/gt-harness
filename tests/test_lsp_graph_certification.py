from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from gt_engine.engine_state import RuntimeLayout
from gt_engine.indexer import (
    GRAPH_SCHEMA_VERSION,
    INDEX_RESOURCE_SCHEMA,
    _graph_phase_metadata,
    _sealed_json,
    certify_graph_artifact,
    certify_lsp_candidate,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    layout = RuntimeLayout.resolve(
        workspace=workspace, state_root=tmp_path / "state", task_id="task"
    )
    directory = layout.graph_root / "revisions" / "source-r1" / "core"
    directory.mkdir(parents=True)
    graph = directory / "graph.db"
    with sqlite3.connect(graph) as db:
        db.execute("CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
    root_sha = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    source_snapshot_sha = "2" * 64
    binary_sha = "3" * 64
    resource_path = directory / "index-resource.json"
    _sealed_json(resource_path, {
        "schema": INDEX_RESOURCE_SCHEMA,
        "identity_scope": "benchmark_bound",
        "task_id": "task",
        "product_source_sha": "4" * 40,
        "repository_root_sha256": root_sha,
        "source_manifest_sha256": source_snapshot_sha,
        "producer_binary_sha256": binary_sha,
        "status": "completed",
        "error_code": "",
        "exit_code": 0,
        "memory_evidence": False,
    }, "evidence_sha256")
    graph_sha = _sha(graph)
    manifest = {
        "schema": GRAPH_SCHEMA_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "identity_scope": "benchmark_bound",
        "task_id": "task",
        "product_source_sha": "4" * 40,
        "repository_root_sha256": root_sha,
        "source_manifest_sha256": source_snapshot_sha,
        "source_revision": "source-r1",
        "graph_revision": graph_sha,
        "graph_sha256": graph_sha,
        "graph_bytes": graph.stat().st_size,
        "index_resource_sha256": _sha(resource_path),
        "binary_sha256": binary_sha,
        "binary_certified": True,
        **_graph_phase_metadata(graph),
    }
    graph.with_suffix(".manifest.json").write_text(json.dumps(manifest))
    return layout, graph, root_sha, source_snapshot_sha, binary_sha


def _candidate(base: Path) -> Path:
    graph_root = base.parents[3]
    directory = graph_root / "enrichments" / "candidate-1"
    directory.mkdir(parents=True)
    candidate = directory / "graph.db"
    shutil.copyfile(base, candidate)
    with sqlite3.connect(candidate) as db:
        db.execute("CREATE TABLE lsp_edges (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO lsp_edges VALUES (1)")
    return candidate


def _receipt(base: Path, candidate: Path, source_snapshot_sha: str) -> dict:
    manifest = json.loads(base.with_suffix(".manifest.json").read_text())
    return {
        "schema": "gt.lsp_promotion_task.v1",
        "terminal": True,
        "status": "succeeded",
        "publishable": True,
        "source_revision": "source-r1",
        "repository_root_sha256": "6" * 64,
        "repository_snapshot_sha256": source_snapshot_sha,
        "input_graph_revision": manifest["graph_revision"],
        "input_graph_sha256": _sha(base),
        "candidate_path": str(candidate.resolve()),
        "output_graph_sha256": _sha(candidate),
    }


def _certify(tmp_path: Path):
    layout, base, root_sha, snapshot_sha, binary_sha = _base(tmp_path)
    candidate = _candidate(base)
    result = certify_lsp_candidate(
        base, candidate, _receipt(base, candidate, snapshot_sha),
        expected_source_revision="source-r1", expected_repository_root_sha256="6" * 64,
        expected_repository_snapshot_sha256=snapshot_sha, layout=layout,
        expected_root_sha256=root_sha, expected_binary_sha256=binary_sha,
        expected_task_id="task", expected_product_source_sha="4" * 40,
    )
    return layout, base, candidate, result, root_sha, binary_sha


def test_lsp_candidate_has_separate_certified_lineage(tmp_path):
    layout, base, candidate, result, root_sha, binary_sha = _certify(tmp_path)
    assert result.success, result.error
    assert result.graph_path == str(candidate.resolve())
    manifest_path = candidate.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["derivation"]["phase"] == "lsp"
    assert not Path(manifest["derivation"]["base_graph"]).is_absolute()
    assert manifest["derivation"]["base_graph_sha256"] == _sha(base)
    assert manifest["graph_sha256"] == _sha(candidate)
    assert manifest["binary_sha256"] == binary_sha
    assert str(tmp_path.resolve()) not in manifest_path.read_text()
    valid, reason = certify_graph_artifact(
        candidate, manifest_path, expected_root_sha256=root_sha,
        expected_source_revision="source-r1", expected_binary_sha256=binary_sha,
        expected_task_id="task", expected_product_source_sha="4" * 40,
    )
    assert valid, reason
    assert candidate.resolve().is_relative_to(layout.graph_root)


def test_collected_derivative_revalidates_after_graph_root_relocation(tmp_path):
    layout, _, candidate, result, root_sha, binary_sha = _certify(tmp_path)
    assert result.success
    collected_root = tmp_path / "collected-graph-root"
    shutil.copytree(layout.graph_root, collected_root)
    relative = candidate.relative_to(layout.graph_root)
    collected = collected_root / relative

    valid, reason = certify_graph_artifact(
        collected, collected.with_suffix(".manifest.json"),
        expected_root_sha256=root_sha, expected_source_revision="source-r1",
        expected_binary_sha256=binary_sha, expected_task_id="task",
        expected_product_source_sha="4" * 40,
    )

    assert valid, reason


@pytest.mark.parametrize("damage", [
    "status", "publishable", "source", "root", "snapshot", "base_sha", "base_revision",
    "candidate_sha", "candidate_path",
])
def test_terminal_receipt_must_bind_actual_lineage(tmp_path, damage):
    layout, base, root_sha, snapshot_sha, binary_sha = _base(tmp_path)
    candidate = _candidate(base)
    receipt = _receipt(base, candidate, snapshot_sha)
    replacements = {
        "status": ("status", "failed"),
        "publishable": ("publishable", False),
        "source": ("source_revision", "obsolete"),
        "root": ("repository_root_sha256", "5" * 64),
        "snapshot": ("repository_snapshot_sha256", "5" * 64),
        "base_sha": ("input_graph_sha256", "5" * 64),
        "base_revision": ("input_graph_revision", "5" * 64),
        "candidate_sha": ("output_graph_sha256", "5" * 64),
        "candidate_path": ("candidate_path", str(base.resolve())),
    }
    key, value = replacements[damage]
    receipt[key] = value
    result = certify_lsp_candidate(
        base, candidate, receipt, expected_source_revision="source-r1",
        expected_repository_root_sha256="6" * 64,
        expected_repository_snapshot_sha256=snapshot_sha,
        layout=layout, expected_root_sha256=root_sha,
        expected_binary_sha256=binary_sha, expected_task_id="task",
        expected_product_source_sha="4" * 40,
    )
    assert not result.success
    assert not candidate.with_suffix(".manifest.json").exists()


@pytest.mark.parametrize("damage", [
    "base_graph", "base_manifest", "base_resource", "terminal_receipt",
    "candidate", "spliced_reference", "nested_derivation", "cycle",
    "obsolete_receipt", "spliced_root_receipt", "unknown_derivation",
])
def test_collector_revalidates_derivative_lineage(tmp_path, damage):
    _, base, candidate, result, root_sha, binary_sha = _certify(tmp_path)
    assert result.success
    manifest_path = candidate.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    derivation = manifest["derivation"]
    targets = {
        "base_graph": base,
        "base_manifest": base.with_suffix(".manifest.json"),
        "base_resource": base.with_name("index-resource.json"),
        "terminal_receipt": candidate.with_name(derivation["terminal_receipt"]),
        "candidate": candidate,
    }
    if damage in targets:
        targets[damage].write_bytes(targets[damage].read_bytes() + b"corrupt")
    elif damage == "spliced_reference":
        derivation["base_graph"] = "../../../outside.db"
        manifest_path.write_text(json.dumps(manifest))
    elif damage == "nested_derivation":
        base_manifest = json.loads(base.with_suffix(".manifest.json").read_text())
        base_manifest["derivation"] = dict(derivation)
        base.with_suffix(".manifest.json").write_text(json.dumps(base_manifest))
    elif damage in {"obsolete_receipt", "spliced_root_receipt"}:
        receipt_path = targets["terminal_receipt"]
        receipt = json.loads(receipt_path.read_text())
        receipt.pop("receipt_sha256")
        if damage == "obsolete_receipt":
            receipt["source_revision"] = "obsolete"
        else:
            receipt["repository_root_sha256"] = "7" * 64
        seal = _sealed_json(receipt_path, receipt, "receipt_sha256")
        derivation["terminal_receipt_seal"] = seal
        derivation["terminal_receipt_sha256"] = _sha(receipt_path)
        manifest_path.write_text(json.dumps(manifest))
    elif damage == "unknown_derivation":
        derivation["phase"] = "unknown"
        manifest_path.write_text(json.dumps(manifest))
    else:
        derivation["base_graph"] = candidate.name
        manifest_path.write_text(json.dumps(manifest))
    valid, reason = certify_graph_artifact(
        candidate, manifest_path, expected_root_sha256=root_sha,
        expected_source_revision="source-r1", expected_binary_sha256=binary_sha,
        expected_task_id="task", expected_product_source_sha="4" * 40,
    )
    assert not valid, reason


def test_candidate_outside_graph_layout_is_refused(tmp_path):
    layout, base, root_sha, snapshot_sha, binary_sha = _base(tmp_path)
    candidate = tmp_path / "outside.db"
    shutil.copyfile(base, candidate)
    result = certify_lsp_candidate(
        base, candidate, _receipt(base, candidate, snapshot_sha),
        expected_source_revision="source-r1", expected_repository_root_sha256="6" * 64,
        expected_repository_snapshot_sha256=snapshot_sha, layout=layout,
        expected_root_sha256=root_sha, expected_binary_sha256=binary_sha,
        expected_task_id="task", expected_product_source_sha="4" * 40,
    )
    assert not result.success


@pytest.mark.parametrize("existing", ["manifest", "receipt"])
def test_preexisting_certification_is_never_overwritten_or_deleted(tmp_path, existing):
    layout, base, root_sha, snapshot_sha, binary_sha = _base(tmp_path)
    candidate = _candidate(base)
    path = (
        candidate.with_suffix(".manifest.json")
        if existing == "manifest"
        else candidate.with_suffix(".lsp-terminal.json")
    )
    original = b"preexisting immutable certification"
    path.write_bytes(original)

    result = certify_lsp_candidate(
        base, candidate, _receipt(base, candidate, snapshot_sha),
        expected_source_revision="source-r1",
        expected_repository_root_sha256="6" * 64,
        expected_repository_snapshot_sha256=snapshot_sha, layout=layout,
        expected_root_sha256=root_sha, expected_binary_sha256=binary_sha,
        expected_task_id="task", expected_product_source_sha="4" * 40,
    )

    assert not result.success
    assert result.error == "lsp_certification_already_exists"
    assert path.read_bytes() == original


def test_candidate_symlink_is_refused_even_when_target_remains_under_graph_root(tmp_path):
    layout, base, root_sha, snapshot_sha, binary_sha = _base(tmp_path)
    target = _candidate(base)
    link_directory = layout.graph_root / "enrichments" / "candidate-link"
    link_directory.mkdir()
    link = link_directory / "graph.db"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("filesystem symlink creation unavailable")

    result = certify_lsp_candidate(
        base, link, _receipt(base, link, snapshot_sha),
        expected_source_revision="source-r1",
        expected_repository_root_sha256="6" * 64,
        expected_repository_snapshot_sha256=snapshot_sha, layout=layout,
        expected_root_sha256=root_sha, expected_binary_sha256=binary_sha,
        expected_task_id="task", expected_product_source_sha="4" * 40,
    )

    assert not result.success
    assert result.error == "lsp_layout_symlink_forbidden"
