import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.runtime_observation import capture_workspace


def test_shared_workspace_state_does_not_hide_neighbor_or_capture_graph_churn(tmp_path):
    source = tmp_path / "neighbor.py"
    source.write_text("value = 1\n")
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path,
                             repo_root=tmp_path, predicates=[])
    layout = adapter.engine_state.layout
    before = capture_workspace(tmp_path, excluded_roots=layout.excluded_roots)
    layout.graph_root.mkdir(parents=True, exist_ok=True)
    (layout.graph_root / "cache.py").write_text("internal = 1\n")
    adapter.store.append("internal_churn")
    after = capture_workspace(tmp_path, excluded_roots=layout.excluded_roots)
    assert before.revision == after.revision
    source.write_text("value = 2\n")
    edited = capture_workspace(tmp_path, excluded_roots=layout.excluded_roots)
    assert edited.revision != after.revision
    assert [item.path for item in edited.files] == ["neighbor.py"]


def test_explicit_nested_state_preserves_parent_sources(tmp_path):
    parent = tmp_path / "src"
    parent.mkdir()
    (parent / "neighbor.py").write_text("value = 1\n")
    adapter = MiniSweAdapter(task_id="task", state_dir=parent / "runtime",
                             repo_root=tmp_path, predicates=[])
    layout = adapter.engine_state.layout
    snapshot = capture_workspace(tmp_path, excluded_roots=layout.excluded_roots)
    assert [item.path for item in snapshot.files] == ["src/neighbor.py"]


def test_state_namespace_cannot_alias_workspace(tmp_path):
    from gt_engine.engine_state import RuntimeLayout

    with pytest.raises(ValueError, match="contains workspace"):
        RuntimeLayout.resolve(workspace=tmp_path / "task", state_root=tmp_path,
                              task_id="task")


def test_nested_state_churn_preserves_producer_input_and_languages(tmp_path):
    from gt_engine.indexer import source_manifest_digest

    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path / "internal",
                             repo_root=tmp_path, predicates=[])
    layout = adapter.engine_state.layout
    for extension in (".svelte", ".proto", ".yaml", ".sql", ".cxx"):
        (tmp_path / ("source" + extension)).write_text("original\n")
    before = source_manifest_digest(tmp_path, excluded_roots=layout.excluded_roots)
    (layout.task_root / "cache.py").write_text("internal\n")
    assert source_manifest_digest(tmp_path, excluded_roots=layout.excluded_roots) == before
    for extension in (".svelte", ".proto", ".yaml", ".sql", ".cxx"):
        source = tmp_path / ("source" + extension)
        source.write_text("edited\n")
        assert source_manifest_digest(tmp_path, excluded_roots=layout.excluded_roots) != before
        source.write_text("original\n")


@pytest.mark.skipif(os.name != "posix" or not os.environ.get("GT_INDEX_BINARY"),
                    reason="installed Linux producer required")
def test_installed_producer_preserves_graph_and_reuses_after_internal_churn(tmp_path):
    from gt_engine.indexer import ensure_index_with_receipt

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "neighbor.py").write_text("def neighbor():\n    return 1\n")
    (repo / "Widget.svelte").write_text("<script>export let name = 'world';</script><h1>{name}</h1>")
    (repo / "schema.proto").write_text('syntax = "proto3"; message User { string name = 1; }')
    env = os.environ | {"GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
                        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                        "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    for command in (("init", "-q"), ("add", "."), ("-c", "core.hooksPath=", "commit", "-qm", "initial")):
        subprocess.run(["git", "-C", str(repo), *command], env=env, check=True, capture_output=True)
    baseline = ensure_index_with_receipt(repo, state_dir=tmp_path / "baseline")
    assert baseline.success, baseline

    def graph_facts(path):
        with sqlite3.connect(path) as connection:
            return connection.execute("SELECT name, label, file_path, start_line FROM nodes ORDER BY name, label, file_path, start_line").fetchall()

    original = graph_facts(baseline.graph_db)
    assert original
    adapter = MiniSweAdapter(task_id="task", state_dir=repo,
                             repo_root=repo, predicates=[])
    layout = adapter.engine_state.layout
    (layout.task_root / "internal.py").write_text("def must_not_be_indexed(): pass\n")
    built = ensure_index_with_receipt(repo, state_dir=layout.graph_root,
                                      excluded_roots=layout.excluded_roots)
    assert built.success, built
    assert graph_facts(built.graph_db) == original
    graph = Path(built.graph_db)
    original_identity = (graph.stat().st_mtime_ns, graph.read_bytes())
    (layout.task_root / "internal.py").write_text("def still_not_source(): pass\n")
    adapter.store.append("internal_churn")
    reused = ensure_index_with_receipt(repo, state_dir=layout.graph_root,
                                       excluded_roots=layout.excluded_roots)
    assert reused.success, reused
    assert (graph.stat().st_mtime_ns, graph.read_bytes()) == original_identity
    (repo / "neighbor.py").write_text("def edited_neighbor():\n    return 2\n")
    rebuilt = ensure_index_with_receipt(repo, state_dir=layout.graph_root,
                                        excluded_roots=layout.excluded_roots)
    assert rebuilt.success, rebuilt
    assert graph_facts(rebuilt.graph_db) != original


def test_runtime_layout_preserves_auditable_journal_envelope(tmp_path):
    from gt_engine.event_journal import verify_event_journal
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path / "runtime",
                             repo_root=tmp_path, predicates=[])
    journal = adapter.engine_state.layout.task_root / "events.jsonl"
    result = verify_event_journal(journal)
    assert result.valid, result.issues
