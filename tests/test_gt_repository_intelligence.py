from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import groundtruth._binary as binary

import gt_engine.indexer as indexer
from gt_engine.indexer import IndexBuildStatus, ensure_index_with_receipt
from gt_engine.language_registry import LANGUAGE_CAPABILITIES
from gt_engine.repository_intelligence import (
    RepositoryApplicability,
    RepositoryEvidence,
    RepositoryIntelligenceStatus,
    RepositorySession,
    RepositorySubstrateStatus,
    classify_repository_applicability,
    discover_project_checks,
    graph_gate_failures,
    inspect_index,
    inspect_repository,
)
from scripts.verify_gt_index_runtime import verify as verify_gt_index_runtime


def test_source_less_repository_is_explicitly_not_applicable():
    evidence = RepositoryEvidence(
        status=RepositoryIntelligenceStatus.NO_SUPPORTED_SOURCE.value,
        substrate_status=RepositorySubstrateStatus.NOT_APPLICABLE.value,
    )

    assert (
        classify_repository_applicability(evidence)
        == RepositoryApplicability.NOT_APPLICABLE_NO_SUPPORTED_SOURCE.value
    )


def test_failed_index_binary_preserves_bounded_diagnostic(tmp_path, monkeypatch):
    (tmp_path / "query.sql").write_text("select 1;\n", encoding="utf-8")

    def fail_index(root, output):
        sys.stderr.write("GroundTruth: gt-index failed: SQL parser exploded on query.sql\n")
        return False

    monkeypatch.setattr(binary, "run_index", fail_index)
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"binary_sha256": "a" * 64},
    )

    receipt = ensure_index_with_receipt(tmp_path, state_dir=tmp_path / "state")

    assert receipt.status is IndexBuildStatus.BUILD_FAILED
    assert receipt.error_type == "run_index_false"
    assert "SQL parser exploded" in receipt.error_diagnostic
    assert len(receipt.error_diagnostic) <= 600

def test_source_backed_graph_failure_remains_a_hard_gate():
    evidence = RepositoryEvidence(
        status=RepositoryIntelligenceStatus.INDEX_UNAVAILABLE.value,
        source_revision="source-r1",
        substrate_status=RepositorySubstrateStatus.UNAVAILABLE.value,
    )

    failures = graph_gate_failures(evidence)

    assert "index_unavailable" in failures
    assert "repository_intelligence_invalid" in failures

def test_source_backed_empty_retrieval_remains_applicable():
    evidence = RepositoryEvidence(
        status=RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        source_revision="r1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
        substrate_status=RepositorySubstrateStatus.HEALTHY_CURRENT.value,
        retrieval_disposition="empty",
    )

    assert (
        classify_repository_applicability(evidence)
        == RepositoryApplicability.SOURCE_BACKED.value
    )


def test_project_checks_are_repository_backed_not_guessed(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "go.mod").write_text("module example.test/demo\n")

    assert discover_project_checks(tmp_path) == ("go test ./...",)


def test_project_checks_require_mechanical_manifest_evidence(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\ndependencies=['pytest>=8']\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}),
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text(
        "build:\n\tpython -m build\ntest:\n\tpytest -q\n",
        encoding="utf-8",
    )

    assert discover_project_checks(tmp_path) == (
        "pytest -q",
        "npm test",
        "make test",
    )


def test_project_checks_reject_placeholder_scripts_and_missing_make_target(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "echo Error: no test specified && exit 1"}}),
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text("build:\n\techo ok\n", encoding="utf-8")

    assert discover_project_checks(tmp_path) == ()


def test_project_checks_scope_to_nearest_changed_project(tmp_path: Path):
    package = tmp_path / "packages" / "api"
    source = package / "src"
    source.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}),
        encoding="utf-8",
    )
    (source / "handler.ts").write_text("export const handler = () => 1\n")

    assert discover_project_checks(
        tmp_path,
        active_paths=("packages/api/src/handler.ts",),
    ) == ("cd packages/api && npm test",)


def test_shipped_index_fixture_covers_every_registered_parser_language():
    result = verify_gt_index_runtime()
    expected = {
        "bash" if capability.name == "shell" else capability.name
        for capability in LANGUAGE_CAPABILITIES
        if capability.structural_index
    }

    assert expected <= set(result["language_file_counts"])


def test_repository_intelligence_returns_task_linked_source_anchor(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    source = tmp_path / "src"
    source.mkdir()
    (source / "greeter.py").write_text("def greet(name: str) -> str:\n    return f'hello {name}'\n")

    evidence = inspect_repository(
        tmp_path,
        "Change greet so it returns an uppercase greeting.",
        state_dir=tmp_path / ".state",
    )

    assert evidence.available is True
    assert evidence.graph_revision
    assert any(item["path"].endswith("greeter.py") for item in evidence.anchors)
    assert evidence.definitions
    assert evidence.references == ()
    assert evidence.callers == ()
    # A generic Python package is not proof that pytest is installed or that
    # the repository declares a project-wide pytest contract.
    assert evidence.project_checks == ()
    assert evidence.index is not None
    assert evidence.index.schema_valid is True
    assert evidence.index.node_count > 0
    assert "nodes_fts" in evidence.index.fts_tables


def test_non_code_repository_has_explicit_index_abstention(tmp_path: Path):
    (tmp_path / "README.md").write_text("documentation only")

    receipt = inspect_index(tmp_path, state_dir=tmp_path / ".state")

    assert receipt.status is IndexBuildStatus.NO_SUPPORTED_SOURCE
    assert receipt.graph_db is None
    assert receipt.error_type is None


def test_repository_intelligence_abstains_for_non_code_repository(tmp_path: Path):
    (tmp_path / "README.md").write_text("documentation only")

    evidence = inspect_repository(tmp_path, "Update the documentation")

    assert evidence.available is False
    assert evidence.anchors == ()


def test_repository_session_persists_and_refreshes_captured_source(tmp_path: Path):
    mirror = tmp_path / "mirror"
    state = tmp_path / "state"
    (mirror / "src").mkdir(parents=True)
    (mirror / "src" / "greeter.py").write_text("def greet():\n    return 'hi'\n")
    session = RepositorySession(
        root=mirror,
        state_dir=state,
        instruction="Change greet to return uppercase text.",
    )

    first = session.refresh(source_revision="s1")
    assert first.available is True
    first_graph_revision = first.graph_revision
    transition = SimpleNamespace(
        changed_paths=("src/greeter.py",),
        deleted=(),
        after_contents={"src/greeter.py": "def greet():\n    return 'HI'\n"},
        sensor_healthy=True,
    )
    assert session.apply_transition(transition, source_revision="s2") is True
    second = session.refresh(source_revision="s2")

    assert second.available is True
    assert second.graph_revision != first_graph_revision
    assert session.source_revision == "s2"
    assert "'HI'" in (mirror / "src" / "greeter.py").read_text()
    assert [row["source_revision"] for row in session.refresh_log] == ["s1", "s2"]
    assert [row["mode"] for row in session.refresh_log] == ["full", "incremental"]
    assert second.index is not None and second.index.graph_db
    assert second.index.source_revision == "s2"
    assert graph_gate_failures(second) == ()
    manifest = json.loads(Path(second.index.graph_db).with_suffix(".manifest.json").read_text())
    assert manifest["refresh_mode"] == "incremental"
    assert manifest["changed_paths"] == ["src/greeter.py"]
    assert manifest["source_revision"] == "s2"

    cached = session.refresh(source_revision="s2")
    assert cached.graph_revision == second.graph_revision
    assert session.refresh_log[-1]["mode"] == "revision_cache_hit"
    assert session.refresh_log[-1]["elapsed_ms"] == 0.0


def test_repository_session_recovers_when_source_is_created_after_initial_empty_mirror(
    tmp_path: Path,
):
    mirror = tmp_path / "mirror"
    state = tmp_path / "state"
    mirror.mkdir()
    session = RepositorySession(
        root=mirror,
        state_dir=state,
        instruction="Implement the requested C program.",
    )

    initial = session.refresh(source_revision="empty")
    assert initial.status == IndexBuildStatus.NO_SUPPORTED_SOURCE.value
    assert initial.available is False

    transition = SimpleNamespace(
        changed_paths=("gpt2.c",),
        deleted=(),
        after_contents={"gpt2.c": "int main(void) { return 0; }\n"},
        sensor_healthy=True,
    )
    assert session.apply_transition(transition, source_revision="source") is True
    recovered = session.refresh(source_revision="source")

    # A source-backed repository can have no task-linked anchor; substrate
    # health is independent from retrieval availability.
    assert recovered.available is False
    assert recovered.substrate_ready is True
    assert recovered.index_current is True
    assert recovered.intelligence_valid is True
    assert graph_gate_failures(recovered) == ()


def test_repository_session_incrementally_indexes_content_signature_source(
    tmp_path: Path,
):
    """A shebang-only source must enter the incremental graph after creation."""
    mirror = tmp_path / "mirror"
    state = tmp_path / "state"
    mirror.mkdir()
    (mirror / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    session = RepositorySession(
        root=mirror,
        state_dir=state,
        instruction="Update the command-line tool.",
    )

    initial = session.refresh(source_revision="s1")
    assert initial.substrate_ready is True
    first_graph_revision = initial.graph_revision

    tool = "#!/bin/sh\necho ready\n"
    transition = SimpleNamespace(
        changed_paths=("tool",),
        deleted=(),
        after_contents={"tool": tool},
        sensor_healthy=True,
    )
    assert session.apply_transition(transition, source_revision="s2") is True
    updated = session.refresh(source_revision="s2")

    assert updated.substrate_ready is True
    assert updated.graph_revision != first_graph_revision
    assert session.refresh_log[-1]["mode"] == "incremental"
    assert updated.index is not None
    manifest = json.loads(Path(updated.index.graph_db).with_suffix(".manifest.json").read_text())
    assert manifest["refresh_mode"] == "incremental"
    assert manifest["changed_paths"] == ["tool"]
    assert dict(updated.index.language_file_counts).get("shell") == 1


def test_repository_session_rebuilds_after_content_signature_source_deletion(
    tmp_path: Path,
):
    """Deleting an extensionless source must not leave stale graph nodes."""
    mirror = tmp_path / "mirror"
    state = tmp_path / "state"
    mirror.mkdir()
    (mirror / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (mirror / "tool").write_text("#!/bin/sh\necho ready\n", encoding="utf-8")
    session = RepositorySession(
        root=mirror,
        state_dir=state,
        instruction="Remove the command-line tool.",
    )

    initial = session.refresh(source_revision="s1")
    assert initial.substrate_ready is True
    assert dict(initial.index.language_file_counts).get("shell") == 1
    initial_graph_revision = initial.graph_revision

    transition = SimpleNamespace(
        changed_paths=("tool",),
        deleted=("tool",),
        after_contents={},
        sensor_healthy=True,
    )
    assert session.apply_transition(transition, source_revision="s2") is True
    updated = session.refresh(source_revision="s2")

    assert updated.substrate_ready is True
    assert updated.graph_revision != initial_graph_revision
    assert session.refresh_log[-1]["mode"] == "full"
    assert dict(updated.index.language_file_counts).get("shell", 0) == 0


def test_repository_session_rebuilds_when_content_signature_source_becomes_data(
    tmp_path: Path,
):
    """A source-to-data edit must remove its old graph nodes."""
    mirror = tmp_path / "mirror"
    state = tmp_path / "state"
    mirror.mkdir()
    (mirror / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (mirror / "tool").write_text("#!/bin/sh\necho ready\n", encoding="utf-8")
    session = RepositorySession(
        root=mirror,
        state_dir=state,
        instruction="Replace the command-line tool with data.",
    )

    initial = session.refresh(source_revision="s1")
    assert initial.substrate_ready is True
    assert dict(initial.index.language_file_counts).get("shell") == 1

    transition = SimpleNamespace(
        changed_paths=("tool",),
        deleted=(),
        after_contents={"tool": "plain task data\n"},
        sensor_healthy=True,
    )
    assert session.apply_transition(transition, source_revision="s2") is True
    updated = session.refresh(source_revision="s2")

    assert updated.substrate_ready is True
    assert session.refresh_log[-1]["mode"] == "full"
    assert dict(updated.index.language_file_counts).get("shell", 0) == 0


def test_repository_session_invalidates_when_changed_source_was_not_captured(tmp_path: Path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "app.py").write_text("x = 1\n")
    session = RepositorySession(
        root=mirror,
        state_dir=tmp_path / "state",
        instruction="Change x.",
    )
    session.refresh(source_revision="s1")
    transition = SimpleNamespace(
        changed_paths=("app.py",),
        deleted=(),
        after_contents={},
        sensor_healthy=True,
    )

    assert session.apply_transition(transition, source_revision="s2") is False
    assert session.fresh is False
    assert session.evidence.available is False
    assert session.evidence.status == "mirror_incomplete"


def test_current_graph_with_empty_retrieval_is_healthy_substrate(tmp_path: Path):
    (tmp_path / "decomp.c").write_text(
        "int decode(void) { return 0; }\n",
        encoding="utf-8",
    )

    evidence = inspect_repository(
        tmp_path,
        "Create data.comp containing the requested artifact.",
        state_dir=tmp_path / ".state",
        source_revision="s1",
    )
    session = RepositorySession(
        root=tmp_path,
        state_dir=tmp_path / ".session-state",
        instruction="Create data.comp containing the requested artifact.",
    )
    refreshed = session.refresh(source_revision="s1")

    assert evidence.index is not None and evidence.index.schema_valid is True
    assert evidence.index.node_count > 0
    assert evidence.retrieval_disposition == "empty"
    assert evidence.substrate_ready is True
    assert graph_gate_failures(evidence) == ()
    assert refreshed.substrate_ready is True
    assert refreshed.index_current is True
    assert graph_gate_failures(refreshed) == ()


def test_typed_action_path_requeries_current_graph_without_rebuilding(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "greeter.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    session = RepositorySession(
        root=tmp_path,
        state_dir=tmp_path / ".state",
        instruction="Repair the implementation.",
    )
    initial = session.refresh(source_revision="s1")
    assert initial.index is not None
    graph_revision = initial.graph_revision

    action_evidence = session.query(
        source_revision="s1",
        active_paths=("src/greeter.py",),
        boundary="post_read",
    )

    assert action_evidence.graph_revision == graph_revision
    assert action_evidence.available is True
    assert any(item["path"] == "src/greeter.py" for item in action_evidence.anchors)
    assert action_evidence.definitions
    assert session.refresh_log[-1]["mode"] == "action_query"
    assert session.refresh_log[-1]["active_paths"] == ["src/greeter.py"]

    cached = session.query(
        source_revision="s1",
        active_paths=("src/greeter.py",),
        boundary="post_read",
    )
    assert cached == action_evidence
    assert session.refresh_log[-1]["mode"] == "action_query_cache_hit"


def test_repository_query_cache_is_scoped_to_boundary_and_diagnostic_state(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "greeter.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    session = RepositorySession(
        root=tmp_path,
        state_dir=tmp_path / ".state",
        instruction="Repair the implementation.",
    )
    session.refresh(source_revision="s1")

    session.query(
        source_revision="s1",
        active_paths=("src/greeter.py",),
        boundary="post_read",
    )
    session.query(
        source_revision="s1",
        active_paths=("src/greeter.py",),
        active_symbols=("greet",),
        diagnostic_fingerprint="failure-1",
        boundary="post_validate",
    )

    assert session.refresh_log[-1]["mode"] == "action_query"
    assert session.refresh_log[-1]["boundary"] == "post_validate"
    assert session.refresh_log[-1]["active_symbols"] == ["greet"]
    assert session.refresh_log[-1]["diagnostic_fingerprint"] == "failure-1"
