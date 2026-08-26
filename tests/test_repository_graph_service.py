from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gt_engine.repository_graph_service import (
    GRAPH_BUILDER_VERSION,
    GraphNotReadyError,
    GraphReceipt,
    GraphStatus,
    RepositoryGraphService,
    _GraphBuildStats,
    compute_repository_identity,
)
from gt_harness.cli import _graph, _graph_receipt_output


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "gt@example.invalid")
    _git(root, "config", "user.name", "GT Test")
    (root / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-qm", "initial")
    return root


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY, label TEXT, name TEXT, qualified_name TEXT,
                file_path TEXT, start_line INTEGER, end_line INTEGER, signature TEXT,
                return_type TEXT, is_exported INTEGER, is_test INTEGER, language TEXT,
                parent_id INTEGER, repo_id TEXT
            );
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, type TEXT,
                source_line INTEGER, source_file TEXT, resolution_method TEXT,
                confidence REAL, metadata TEXT, trust_tier TEXT, candidate_count INTEGER,
                evidence_type TEXT, verification_status TEXT, repo_id TEXT
            );
            INSERT INTO nodes VALUES
              (1,'function','answer','answer','app.py',1,2,'answer()','',1,0,'python',NULL,'repo'),
              (2,'method','helper','Answer.answer','app.py',4,5,'helper()','',0,0,'python',NULL,'repo'),
              (3,'function','invoke','invoke','app.py',7,8,'invoke()','',0,0,'python',NULL,'repo'),
              (10,'File','app','app.py','app.py',1,8,'','',1,0,'python',NULL,'repo');
            INSERT INTO edges VALUES
              (1,2,1,'CALLS',4,'app.py','name_match',0.2,'','low',2,'name_match','unverified','repo'),
              (2,3,1,'CALLS',8,'app.py','same_file',1.0,'','high',1,'same_file','verified','repo'),
              (3,10,1,'RE_EXPORTS',1,'app.py','re_export',1.0,'','high',1,'re_export','verified','repo');
            """
        )
        connection.commit()
    finally:
        connection.close()


def _receipt(root: Path, graph: Path) -> GraphReceipt:
    identity = compute_repository_identity(root)
    return GraphReceipt(
        repository=str(root.resolve()),
        commit_sha=identity.commit_sha,
        branch=identity.branch,
        working_tree_state=identity.working_tree_state,
        source_revision=identity.source_revision,
        graph_schema_version="test-v1",
        graph_builder_version=GRAPH_BUILDER_VERSION,
        build_started="2026-08-22T00:00:00Z",
        build_completed="2026-08-22T00:00:01Z",
        build_status=GraphStatus.READY,
        files_discovered=1,
        files_attempted=1,
        files_indexed=1,
        files_skipped=0,
        files_failed=0,
        symbols=1,
        nodes_by_type={"function": 1},
        edges_by_type={},
        coverage=1.0,
        build_duration_ms=1000.0,
        persistent_graph_path=str(graph),
        graph_checksum_or_identity=RepositoryGraphService.file_sha256(graph),
        query_ready=True,
        degraded_reasons=(),
        repository_files_discovered=identity.files_discovered,
        graph_input_hashes=identity.graph_input_hashes,
        graph_input_sizes=identity.graph_input_sizes,
        graph_input_fingerprints=identity.graph_input_fingerprints,
        git_status_paths=identity.git_status_paths,
        submodule_state=identity.submodule_state,
        generation_id="a" * 64,
        manifest_sha256="b" * 64,
    )


def _publish_test_generation(
    root: Path, state: Path
) -> tuple[RepositoryGraphService, GraphReceipt]:
    state.mkdir(exist_ok=True)
    candidate = state / "candidate.db"
    _database(candidate)
    manifest = candidate.with_suffix(".manifest.json")
    manifest.write_text('{"schema":"gt.graph_certification.v1"}\n', encoding="utf-8")
    service = RepositoryGraphService(root, state_dir=state)
    receipt = replace(
        _receipt(root, candidate),
        generation_id=RepositoryGraphService.file_sha256(candidate),
        manifest_sha256=RepositoryGraphService.file_sha256(manifest),
    )
    return service, service._publish_generation(receipt, candidate)


def test_ready_graph_is_published_as_one_immutable_generation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    service, published = _publish_test_generation(root, state)

    assert service.current_path.read_text(encoding="ascii").strip() == published.generation_id
    assert service.graph_path.parent.name == published.generation_id
    assert service.receipt_path.parent == service.graph_path.parent
    assert Path(published.persistent_graph_path) == service.graph_path
    assert service.status().query_ready is True


def test_generation_manifest_corruption_revokes_query_readiness(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    service, _published = _publish_test_generation(root, tmp_path / "state")
    service.graph_path.with_name("graph.manifest.json").write_text(
        '{"corrupt":true}\n', encoding="utf-8"
    )

    observed = service.status()

    assert observed.build_status is GraphStatus.STALE
    assert observed.query_ready is False
    assert "graph_manifest_checksum_mismatch" in observed.degraded_reasons
    with pytest.raises(GraphNotReadyError):
        service.query("definition", "answer")


def test_interrupted_build_attempt_recovers_prior_complete_generation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    service, published = _publish_test_generation(root, tmp_path / "state")
    attempt = replace(
        published,
        build_status=GraphStatus.BUILDING,
        query_ready=False,
        degraded_reasons=("build_in_progress",),
    )
    service._write_build_attempt(attempt)

    observed = service.status()

    assert service.build_attempt_path.exists() is False
    assert observed.generation_id == published.generation_id
    assert observed.query_ready is True


def test_source_revision_includes_dirty_and_untracked_graph_inputs(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    clean = compute_repository_identity(root)
    assert clean.working_tree_state == "clean"

    (root / "app.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
    modified = compute_repository_identity(root)
    assert modified.commit_sha == clean.commit_sha
    assert modified.source_revision != clean.source_revision
    assert modified.working_tree_state == "dirty"

    (root / "new.py").write_text("from app import answer\n", encoding="utf-8")
    untracked = compute_repository_identity(root)
    assert untracked.source_revision != modified.source_revision


def test_definition_query_is_exact_and_accepts_documented_plural_alias(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    service = RepositoryGraphService(root, state_dir=state)

    result = service.query("definitions", "answer")

    assert result["mode"] == "definition"
    assert [row["name"] for row in result["evidence"]] == ["answer"]

    default_callers = service.query("callers", "answer")
    forensic_callers = service.query("callers", "answer", min_confidence=0.0)
    assert [row["name"] for row in default_callers["evidence"]] == ["invoke"]
    assert [row["name"] for row in forensic_callers["evidence"]] == ["invoke", "helper"]


def test_relationship_query_refuses_to_merge_ambiguous_symbols(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "INSERT INTO nodes VALUES "
            "(4,'function','answer','answer','other.py',1,2,'answer()','',1,0,'python',NULL,'repo')"
        )
        connection.commit()
    finally:
        connection.close()
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    service = RepositoryGraphService(root, state_dir=state)

    ambiguous = service.query("callers", "answer")
    selected = service.query("callers", "answer", file_path="app.py")

    assert ambiguous["status"] == "AMBIGUOUS"
    assert ambiguous["evidence"] == []
    assert len(ambiguous["ambiguous_candidates"]) == 2
    assert selected["status"] == "READY"
    assert [row["name"] for row in selected["evidence"]] == ["invoke"]


def test_reexport_queries_traverse_explicit_file_anchor(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    service = RepositoryGraphService(root, state_dir=state)

    exported = service.query("reexports", "app.py")
    exporters = service.query("exporters", "answer", file_path="app.py")

    assert [(row["name"], row["file_path"]) for row in exported["evidence"]] == [
        ("answer", "app.py")
    ]
    assert [(row["label"], row["qualified_name"]) for row in exporters["evidence"]] == [
        ("File", "app.py")
    ]


def test_hierarchy_query_prefers_type_over_same_named_constructor(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    connection = sqlite3.connect(graph)
    try:
        connection.executescript(
            """
            INSERT INTO nodes VALUES
              (20,'Class','BillingInstrument','Outer.BillingInstrument','app.py',10,20,'','',0,0,'java',NULL,'repo'),
              (21,'Method','BillingInstrument','BillingInstrument.BillingInstrument','app.py',12,14,'BillingInstrument()','',0,0,'java',20,'repo'),
              (22,'Class','CreditCard','Outer.CreditCard','app.py',22,30,'','',0,0,'java',NULL,'repo');
            INSERT INTO edges VALUES
              (20,22,20,'EXTENDS',22,'app.py','inheritance',1.0,'','high',1,'inheritance','verified','repo');
            """
        )
        connection.commit()
    finally:
        connection.close()
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )

    result = RepositoryGraphService(root, state_dir=state).query(
        "subclasses", "BillingInstrument", file_path="app.py"
    )

    assert result["status"] == "READY"
    assert result["resolved_symbol"]["label"] == "Class"
    assert [(row["name"], row["relationship"]) for row in result["evidence"]] == [
        ("CreditCard", "EXTENDS")
    ]


def test_cli_receipt_is_compact_by_default_and_lossless_when_verbose(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    graph = tmp_path / "graph.db"
    _database(graph)
    service = RepositoryGraphService(root, state_dir=tmp_path / "state")
    receipt = _receipt(root, graph)

    summary = _graph_receipt_output(service, receipt, verbose=False)
    verbose = _graph_receipt_output(service, receipt, verbose=True)

    assert summary["query_ready"] is True
    assert summary["receipt_path"] == str(service.receipt_path)
    assert "graph_input_hashes" not in summary
    assert "graph_input_hashes" in verbose


def test_query_refuses_graph_after_worktree_changes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    service = RepositoryGraphService(root, state_dir=state)
    assert service.status().build_status is GraphStatus.READY

    (root / "app.py").write_text("def answer():\n    return 0\n", encoding="utf-8")
    stale = service.status()
    assert stale.build_status is GraphStatus.STALE
    assert stale.query_ready is False
    with pytest.raises(GraphNotReadyError):
        service.query("definition", "answer")


def test_status_detects_skip_worktree_source_mutation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    service = RepositoryGraphService(root, state_dir=state)
    assert service.status().query_ready

    _git(root, "update-index", "--skip-worktree", "app.py")
    (root / "app.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
    assert _git(root, "status", "--porcelain=v1") == ""

    stale = service.status()
    assert stale.build_status is GraphStatus.STALE
    assert stale.query_ready is False
    assert "source_revision_mismatch" in stale.degraded_reasons


def test_repository_identity_excludes_gitignored_untracked_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = compute_repository_identity(root)
    exclude = root / ".git" / "info" / "exclude"
    exclude.write_text("ignored.py\n", encoding="utf-8")
    (root / "ignored.py").write_text("ignored = True\n", encoding="utf-8")

    after = compute_repository_identity(root)

    assert after.source_revision == before.source_revision
    assert "ignored.py" not in after.graph_input_hashes


def test_ready_receipt_cannot_claim_missing_or_changed_database(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )

    graph.write_bytes(graph.read_bytes() + b"corruption")
    observed = RepositoryGraphService(root, state_dir=state).status()
    assert observed.build_status is GraphStatus.FAILED
    assert observed.query_ready is False
    assert "graph_checksum_mismatch" in observed.degraded_reasons


def test_query_readiness_keeps_source_checks_but_reuses_unchanged_graph_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gt_engine.repository_graph_service as graph_service

    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    git_calls: list[tuple[str, ...]] = []
    checksum_calls = 0
    original_git = graph_service._run_git
    original_checksum = RepositoryGraphService.file_sha256
    service = RepositoryGraphService(root, state_dir=state)

    def observed_git(repository: Path, *args: str) -> tuple[int, str]:
        git_calls.append(args)
        return original_git(repository, *args)

    def observed_checksum(path: str | Path) -> str:
        nonlocal checksum_calls
        checksum_calls += 1
        return original_checksum(path)

    monkeypatch.setattr(graph_service, "_run_git", observed_git)
    monkeypatch.setattr(RepositoryGraphService, "file_sha256", staticmethod(observed_checksum))

    assert service.status().query_ready
    assert service.status().query_ready

    assert [call[0] for call in git_calls].count("status") == 2
    assert [call[:3] for call in git_calls].count(("ls-files", "-v", "-z")) == 2
    assert checksum_calls == 1


def test_clean_readiness_uses_git_delta_without_full_inventory_rescan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gt_engine.repository_graph_service as graph_service

    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    monkeypatch.setattr(
        graph_service,
        "_repository_paths",
        lambda _root: pytest.fail("clean readiness performed a full inventory rescan"),
    )

    assert RepositoryGraphService(root, state_dir=state).status().query_ready


def test_status_detects_assume_unchanged_source_mutation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    receipt = _receipt(root, graph)
    (state / "graph-receipt.json").write_text(
        json.dumps(receipt.as_dict(), sort_keys=True), encoding="utf-8"
    )
    service = RepositoryGraphService(root, state_dir=state)
    assert service.status().query_ready

    _git(root, "update-index", "--assume-unchanged", "app.py")
    (root / "app.py").write_text("def answer():\n    return 44\n", encoding="utf-8")
    assert _git(root, "status", "--porcelain=v1") == ""

    stale = service.status()
    assert stale.build_status is GraphStatus.STALE
    assert stale.query_ready is False
    assert "source_revision_mismatch" in stale.degraded_reasons


def test_receipt_rejects_ready_without_query_readiness(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    graph = tmp_path / "graph.db"
    _database(graph)
    with pytest.raises(ValueError):
        replace(_receipt(root, graph), query_ready=False)


def test_discovery_accounting_mismatch_can_never_report_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    monkeypatch.setattr(
        "gt_engine.repository_graph_service.ensure_index_with_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(
            graph_db=str(graph),
            status=SimpleNamespace(value="available"),
            error_type=None,
            error_diagnostic="",
            elapsed_ms=1.0,
            schema_valid=True,
            graph_db_sha256=RepositoryGraphService.file_sha256(graph),
        ),
    )
    monkeypatch.setattr(
        RepositoryGraphService,
        "_graph_stats",
        staticmethod(
            lambda _graph: _GraphBuildStats(
                schema="v15.3-discovery-receipt",
                symbols=10,
                nodes={"Function": 10},
                edges={"CALLS": 1},
                files_attempted=10,
                files_parsed=10,
                file_hashes=10,
                parse_failures=0,
                file_hash_failures=0,
                files_discovered=12,
                skipped_count=1,
                discovery_method="git_ls_files",
                skipped_reasons={"unsupported_path": 1},
                skipped_paths=({"path": "README.txt", "reason": "unsupported_path"},),
                parse_failure_details=(),
                file_hash_failure_details=(),
                excluded_directories=(),
                receipt_complete=True,
            )
        ),
    )

    receipt = RepositoryGraphService(root, state_dir=state).build(force=True)
    assert receipt.build_status is GraphStatus.DEGRADED
    assert receipt.query_ready is False
    assert "discovery_accounting_mismatch" in receipt.degraded_reasons


def test_graph_component_failure_can_never_report_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    monkeypatch.setattr(
        "gt_engine.repository_graph_service.ensure_index_with_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(
            graph_db=str(graph),
            status=SimpleNamespace(value="available"),
            error_type=None,
            error_diagnostic="",
            elapsed_ms=1.0,
            schema_valid=True,
            graph_db_sha256=RepositoryGraphService.file_sha256(graph),
        ),
    )
    monkeypatch.setattr(
        RepositoryGraphService,
        "_graph_stats",
        staticmethod(
            lambda _graph: _GraphBuildStats(
                schema="v15.3-discovery-receipt",
                symbols=10,
                nodes={"Function": 10},
                edges={"CALLS": 1},
                files_attempted=10,
                files_parsed=10,
                file_hashes=10,
                parse_failures=0,
                file_hash_failures=0,
                files_discovered=11,
                skipped_count=1,
                discovery_method="git_ls_files",
                skipped_reasons={"unsupported_path": 1},
                skipped_paths=({"path": "README.txt", "reason": "unsupported_path"},),
                parse_failure_details=(),
                file_hash_failure_details=(),
                excluded_directories=(),
                receipt_complete=True,
                component_failures=("import_edges",),
            )
        ),
    )

    receipt = RepositoryGraphService(root, state_dir=state).build(force=True)

    assert receipt.build_status is GraphStatus.DEGRADED
    assert receipt.query_ready is False
    assert receipt.component_failures == ("import_edges",)
    assert "graph_component_failed:import_edges" in receipt.degraded_reasons


def test_parser_recovery_is_declared_and_cannot_report_unqualified_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    graph = state / "graph.db"
    _database(graph)
    manifest = graph.with_suffix(".manifest.json")
    manifest.write_text('{"schema":"gt.graph_certification.v1"}\n', encoding="utf-8")
    monkeypatch.setattr(
        "gt_engine.repository_graph_service.ensure_index_with_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(
            graph_db=str(graph),
            status=SimpleNamespace(value="available"),
            error_type=None,
            error_diagnostic="",
            elapsed_ms=1.0,
            schema_valid=True,
            graph_db_sha256=RepositoryGraphService.file_sha256(graph),
            graph_manifest_sha256=RepositoryGraphService.file_sha256(manifest),
        ),
    )
    limitation = "app.py: tree_sitter_syntax_error_recovered"
    monkeypatch.setattr(
        RepositoryGraphService,
        "_graph_stats",
        staticmethod(
            lambda _graph: _GraphBuildStats(
                schema="v15.3-discovery-receipt",
                symbols=10,
                nodes={"Function": 10},
                edges={"CALLS": 1},
                files_attempted=10,
                files_parsed=10,
                file_hashes=10,
                parse_failures=0,
                file_hash_failures=0,
                files_discovered=10,
                skipped_count=0,
                discovery_method="git_ls_files",
                skipped_reasons={},
                skipped_paths=(),
                parse_failure_details=(),
                file_hash_failure_details=(),
                excluded_directories=(),
                receipt_complete=True,
                parser_limitations=(limitation,),
            )
        ),
    )

    receipt = RepositoryGraphService(root, state_dir=state).build(force=True)

    assert receipt.build_status is GraphStatus.READY_WITH_DECLARED_LIMITATIONS
    assert receipt.query_ready is True
    assert receipt.parser_limitations == (limitation,)
    assert "parser_limitations:1" in receipt.degraded_reasons


def test_cli_graph_build_converts_state_write_failure_to_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path)

    class FailingService:
        def __init__(self, service_root: str | Path, *, state_dir: str | Path | None) -> None:
            self.root = Path(service_root).resolve()
            self.receipt_path = Path(state_dir or self.root) / "graph-receipt.json"

        def build(self, *, force: bool, timeout: float) -> GraphReceipt:
            raise PermissionError("state directory is read-only")

    monkeypatch.setattr("gt_harness.cli.RepositoryGraphService", FailingService)
    args = SimpleNamespace(
        root=str(root),
        state_dir=str(tmp_path / "state"),
        graph_command="build",
        force=True,
        timeout=1.0,
        verbose=False,
    )

    assert _graph(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAILED"
    assert payload["query_ready"] is False
    assert payload["error_type"] == "PermissionError"
