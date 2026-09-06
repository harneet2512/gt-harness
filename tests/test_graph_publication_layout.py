import hashlib
import json
import os
from pathlib import Path

import pytest

from gt_engine.indexer import certify_graph_artifact
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.runtime_observation import capture_workspace


def test_internal_source_file_does_not_start_indexing(tmp_path, monkeypatch):
    from gt_engine.indexer import IndexBuildStatus, ensure_index_with_receipt

    root = tmp_path / "repo"
    root.mkdir()
    adapter = MiniSweAdapter(task_id="task", state_dir=root / "internal",
                             repo_root=root, predicates=[])
    (adapter.store.root / "internal.py").write_text("def internal(): pass\n")

    def forbidden(*args, **kwargs):
        pytest.fail("internal state started a producer build")

    monkeypatch.setattr("gt_engine.indexer._build_index_with_attempts", forbidden)
    result = ensure_index_with_receipt(root, layout=adapter.engine_state.layout)
    assert result.status == IndexBuildStatus.NOT_APPLICABLE


def test_exhausted_discovery_is_incomplete_not_non_code(tmp_path, monkeypatch):
    from gt_engine.indexer import IndexBuildStatus, ensure_index_with_receipt

    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path / "state",
                             repo_root=tmp_path, predicates=[])
    for ordinal in range(4):
        (tmp_path / f"{ordinal}.txt").write_text("text")
    monkeypatch.setattr("gt_engine.indexer._MAX_SCAN_FILES", 2)
    result = ensure_index_with_receipt(tmp_path, layout=adapter.engine_state.layout)
    assert result.status == IndexBuildStatus.BUILD_FAILED
    assert result.error_type == "source_discovery_incomplete"


def test_last_published_graph_is_historical_after_a_later_edit():
    from gt_harness.runtime_receipts import _graph_publication_state

    rows = [{"event": "graph_publication", "repository_revision": "a" * 64,
             "artifact_sha256": "c" * 64},
            {"event": "repository_snapshot", "repository_revision": "b" * 64,
             "complete": True}]
    state = _graph_publication_state(rows)
    assert state["status"] == "historical"
    rows[-1]["complete"] = False
    assert _graph_publication_state(rows)["status"] == "unknown"


def test_collector_uses_published_revision_among_retained_artifacts(tmp_path):
    from gt_harness.runtime_receipts import _published_graph

    publications = []
    for name in ("initial", "current", "obsolete"):
        directory = tmp_path / name
        directory.mkdir()
        payload = {"graph_sha256": hashlib.sha256(name.encode()).hexdigest()}
        path = directory / "graph.manifest.json"
        path.write_text(json.dumps(payload))
        if name != "obsolete":
            publications.append({"event": "graph_publication",
                                 "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 **payload})
    path, _ = _published_graph(tmp_path, publications)
    assert path.parent.name == "current"
    path.write_text(json.dumps({"graph_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="missing_or_ambiguous"):
        _published_graph(tmp_path, publications)


@pytest.mark.skipif(os.name != "posix" or not os.environ.get("GT_INDEX_BINARY"),
                    reason="installed Linux producer required")
def test_background_publications_keep_workspace_identity_and_immutable_base(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "one.py"
    source.write_text("def one(): return 1\n")
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path / "state",
                             repo_root=root, predicates=[])
    first = adapter._build_frozen_graph(adapter._frozen_graph_input(capture_workspace(root)))
    assert first.success, first.error
    graph = Path(first.graph_path)
    original = graph.read_bytes()
    valid, reason = certify_graph_artifact(
        graph, graph.with_suffix(".manifest.json"),
        expected_root_sha256=hashlib.sha256(str(root.resolve()).encode()).hexdigest(),
    )
    assert valid, reason
    from gt_engine.indexer import _sealed_json

    manifest_path = graph.with_suffix(".manifest.json")
    resource_path = graph.with_name("index-resource.json")
    manifest_original = manifest_path.read_bytes()
    resource_original = resource_path.read_bytes()
    try:
        for changed in ({"exit_code": 7}, {"exit_code": False}, {"schema": "invented"}):
            resource = json.loads(resource_original)
            resource.pop("evidence_sha256", None)
            resource.update(changed)
            _sealed_json(resource_path, resource, "evidence_sha256")
            manifest = json.loads(manifest_original)
            manifest["index_resource_sha256"] = hashlib.sha256(resource_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            accepted, reason = certify_graph_artifact(
                graph, manifest_path,
                expected_root_sha256=hashlib.sha256(str(root.resolve()).encode()).hexdigest(),
            )
            assert not accepted, f"invalid resource certified: {changed}"
            assert reason == "index_resource_identity_mismatch"
    finally:
        manifest_path.write_bytes(manifest_original)
        resource_path.write_bytes(resource_original)
    source.write_text("def one(): return 2\n")
    second = adapter._build_frozen_graph(adapter._frozen_graph_input(capture_workspace(root)))
    assert second.success, second.error
    assert second.graph_path != first.graph_path
    assert graph.read_bytes() == original
    assert graph.parent.parent == adapter.engine_state.layout.graph_root / "revisions"
    source.write_text("def one(): return 1\n")
    graph.write_bytes(b"corrupted published artifact")
    refused = adapter._build_frozen_graph(adapter._frozen_graph_input(capture_workspace(root)))
    assert not refused.success
    assert graph.read_bytes() == b"corrupted published artifact"
