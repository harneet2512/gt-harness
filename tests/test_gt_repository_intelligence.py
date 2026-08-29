from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import groundtruth._binary as binary
import pytest

import gt_engine.indexer as indexer
from gt_engine.indexer import (
    IndexBuildReceipt,
    IndexBuildStatus,
    _certify_published_graph,
    _graph_publication_lock,
    _graph_schema_receipt,
    ensure_index_with_receipt,
    refresh_index_files,
)
from gt_engine.language_registry import LANGUAGE_CAPABILITIES
from gt_engine.repository_intelligence import (
    RepositoryApplicability,
    RepositoryEvidence,
    RepositoryIntelligenceStatus,
    RepositorySession,
    RepositorySubstrateStatus,
    _graph_structural_roles,
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

    def fail_index(root, output, *, timeout=600):
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


def test_incremental_refresh_rejects_mismatched_graph_manifest(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute("CREATE TABLE nodes(id INTEGER PRIMARY KEY)")
        connection.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(name,file_path)")
        connection.execute("CREATE TABLE project_meta(key TEXT,value TEXT)")
        connection.execute("INSERT INTO project_meta VALUES ('parse_failures','0')")
        connection.commit()
    finally:
        connection.close()
    binary_path = tmp_path / "gt-index"
    binary_path.write_bytes(b"binary")
    binary_sha256 = hashlib.sha256(binary_path.read_bytes()).hexdigest()
    manifest = {
        "schema": "gt.graph_certification.v1",
        "repository_root_sha256": hashlib.sha256(
            os.path.realpath(repo).encode("utf-8", "surrogatepass")
        ).hexdigest(),
        "graph_sha256": "0" * 64,
        "graph_bytes": graph.stat().st_size,
        "sqlite_quick_check": "ok",
        "source_revision": "s1",
        "parser_failures": 0,
        "path_sha256": hashlib.sha256(str(binary_path).encode("utf-8")).hexdigest(),
        "binary_sha256": binary_sha256,
        "binary_certified": True,
    }
    graph.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: str(binary_path))
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {
            "path_sha256": manifest["path_sha256"],
            "binary_sha256": binary_sha256,
        },
    )

    receipt = refresh_index_files(repo, graph, (), source_revision="s1")

    assert receipt.status is IndexBuildStatus.INVALID_DATABASE
    assert receipt.graph_db is None
    assert receipt.error_type == "certification:graph_sha256_mismatch"


@pytest.mark.parametrize(
    ("field", "bad_value", "expected_error"),
    (
        ("schema", "gt.graph_certification.v0", "manifest_schema_mismatch"),
        ("repository_root_sha256", "0" * 64, "repository_root_mismatch"),
        ("graph_bytes", 999, "graph_bytes_mismatch"),
        ("graph_sha256", "0" * 64, "graph_sha256_mismatch"),
        ("source_revision", "stale", "source_revision_mismatch"),
        ("binary_sha256", "0" * 64, "binary_identity_mismatch"),
        ("binary_certified", False, "binary_not_certified"),
    ),
)
def test_published_graph_certification_rejects_identity_mismatch(
    tmp_path, field, bad_value, expected_error
):
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"certified-graph")
    manifest_path = graph.with_suffix(".manifest.json")
    manifest = {
        "schema": "gt.graph_certification.v1",
        "repository_root_sha256": hashlib.sha256(
            os.path.realpath(tmp_path).encode("utf-8", "surrogatepass")
        ).hexdigest(),
        "graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
        "graph_bytes": graph.stat().st_size,
        "source_revision": "s1",
        "binary_sha256": "b" * 64,
        "binary_certified": True,
    }
    manifest[field] = bad_value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    certified, error = _certify_published_graph(
        graph,
        manifest_path,
        expected_root=tmp_path,
        expected_source_revision="s1",
        expected_binary_sha256="b" * 64,
    )

    assert certified is False
    assert error == expected_error


def test_published_graph_certification_rejects_hash_matching_invalid_sqlite(tmp_path):
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"not-a-sqlite-database")
    manifest_path = graph.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "gt.graph_certification.v1",
                "repository_root_sha256": hashlib.sha256(
                    os.path.realpath(tmp_path).encode("utf-8", "surrogatepass")
                ).hexdigest(),
                "graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
                "graph_bytes": graph.stat().st_size,
                "sqlite_quick_check": "ok",
                "source_revision": "s1",
                "binary_sha256": "b" * 64,
                "binary_certified": True,
            }
        ),
        encoding="utf-8",
    )

    certified, error = _certify_published_graph(
        graph,
        manifest_path,
        expected_root=tmp_path,
        expected_source_revision="s1",
        expected_binary_sha256="b" * 64,
    )

    assert certified is False
    assert error.startswith("graph_schema_invalid:")


def test_graph_schema_rejects_unknown_schema_version(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    receipt = ensure_index_with_receipt(
        tmp_path,
        state_dir=tmp_path / "state",
        source_revision="s1",
    )
    assert receipt.graph_db
    connection = sqlite3.connect(receipt.graph_db)
    try:
        connection.execute(
            "UPDATE project_meta SET value='v999-unknown' WHERE key='schema_version'"
        )
        connection.commit()
    finally:
        connection.close()

    valid, _nodes, _edges, _fts, detail = _graph_schema_receipt(Path(receipt.graph_db))

    assert valid is False
    assert detail == "unsupported_schema_version:v999-unknown"


def test_graph_pair_publication_restores_previous_pair_on_manifest_failure(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    state = tmp_path / "state"
    root.mkdir()
    (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    initial = ensure_index_with_receipt(root, state_dir=state, source_revision="s1")
    assert initial.graph_db
    graph = Path(initial.graph_db)
    manifest = graph.with_suffix(".manifest.json")
    old_graph = graph.read_bytes()
    old_manifest = manifest.read_bytes()
    candidate = graph.parent / "candidate.db"
    shutil.copyfile(graph, candidate)
    connection = sqlite3.connect(candidate)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO project_meta(key,value) VALUES ('manifest_failure_test','1')"
        )
        connection.commit()
    finally:
        connection.close()
    new_manifest = json.loads(old_manifest)
    new_manifest.update(
        {
            "graph_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "graph_bytes": candidate.stat().st_size,
            "source_revision": "s2",
        }
    )
    new_manifest_bytes = json.dumps(
        new_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    original_atomic_write = indexer._atomic_write

    def fail_manifest_write(path: Path, payload: bytes):
        if path == manifest and payload == new_manifest_bytes:
            raise OSError("injected_manifest_failure")
        return original_atomic_write(path, payload)

    monkeypatch.setattr(indexer, "_atomic_write", fail_manifest_write)

    with pytest.raises(OSError, match="injected_manifest_failure"):
        indexer._publish_graph_pair(
            graph,
            candidate,
            new_manifest_bytes,
            expected_root=root,
            expected_source_revision="s2",
            expected_binary_sha256=initial.binary_sha256,
        )

    assert graph.read_bytes() == old_graph
    assert manifest.read_bytes() == old_manifest


def test_interrupted_graph_pair_publication_recovers_new_certified_pair(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    state = tmp_path / "state"
    root.mkdir()
    (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    initial = ensure_index_with_receipt(root, state_dir=state, source_revision="s1")
    assert initial.graph_db
    graph = Path(initial.graph_db)
    manifest_path = graph.with_suffix(".manifest.json")
    candidate = graph.parent / "interrupted-candidate.db"
    shutil.copyfile(graph, candidate)
    connection = sqlite3.connect(candidate)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO project_meta(key,value) VALUES "
            "('interrupted_publication_test','1')"
        )
        connection.commit()
    finally:
        connection.close()
    new_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    new_manifest.update(
        {
            "graph_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "graph_bytes": candidate.stat().st_size,
            "source_revision": "s2",
        }
    )
    new_manifest_bytes = json.dumps(
        new_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    original_atomic_write = indexer._atomic_write

    def terminate_during_manifest_swap(path: Path, payload: bytes):
        if path == manifest_path and payload == new_manifest_bytes:
            raise SystemExit("injected_process_termination")
        return original_atomic_write(path, payload)

    monkeypatch.setattr(indexer, "_atomic_write", terminate_during_manifest_swap)
    with pytest.raises(SystemExit, match="injected_process_termination"):
        indexer._publish_graph_pair(
            graph,
            candidate,
            new_manifest_bytes,
            expected_root=root,
            expected_source_revision="s2",
            expected_binary_sha256=initial.binary_sha256,
        )
    monkeypatch.setattr(indexer, "_atomic_write", original_atomic_write)

    certified, error = _certify_published_graph(
        graph,
        manifest_path,
        expected_root=root,
        expected_source_revision="s2",
        expected_binary_sha256=initial.binary_sha256,
    )

    assert certified is True, error
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["source_revision"] == "s2"
    assert not indexer._graph_publication_journal_path(graph).exists()


def test_graph_publication_lock_serializes_processes(tmp_path):
    ready = tmp_path / "ready"
    program = "\n".join(
        (
            "import sys, time",
            "from pathlib import Path",
            "from gt_engine.indexer import _graph_publication_lock",
            "with _graph_publication_lock(Path(sys.argv[1])):",
            "    Path(sys.argv[2]).write_text('ready', encoding='utf-8')",
            "    time.sleep(2)",
        )
    )
    child = subprocess.Popen(
        [sys.executable, "-c", program, str(tmp_path), str(ready)],
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.is_file()
        with pytest.raises(TimeoutError, match="graph_publication_lock_timeout"):
            with _graph_publication_lock(tmp_path, timeout=0.1):
                pass
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_graph_reader_cannot_certify_while_publication_lock_is_held(tmp_path):
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"current-graph")
    manifest = graph.with_suffix(".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "schema": "gt.graph_certification.v1",
                "repository_root_sha256": hashlib.sha256(
                    os.path.realpath(tmp_path).encode("utf-8", "surrogatepass")
                ).hexdigest(),
                "graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
                "graph_bytes": graph.stat().st_size,
                "source_revision": "s1",
                "binary_sha256": "b" * 64,
                "binary_certified": True,
            }
        ),
        encoding="utf-8",
    )
    ready = tmp_path / "reader-ready"
    program = "\n".join(
        (
            "import sys, time",
            "from pathlib import Path",
            "from gt_engine.indexer import _graph_publication_lock",
            "with _graph_publication_lock(Path(sys.argv[1])):",
            "    Path(sys.argv[2]).write_text('ready', encoding='utf-8')",
            "    time.sleep(2)",
        )
    )
    child = subprocess.Popen(
        [sys.executable, "-c", program, str(tmp_path), str(ready)],
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.is_file()

        certified, error = _certify_published_graph(
            graph,
            manifest,
            expected_root=tmp_path,
            expected_source_revision="s1",
            expected_binary_sha256="b" * 64,
            lock_timeout=0.1,
        )

        assert certified is False
        assert error == "publication_lock_timeout"
    finally:
        child.terminate()
        child.wait(timeout=5)


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
        classify_repository_applicability(evidence) == RepositoryApplicability.SOURCE_BACKED.value
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


def test_file_anchor_is_graph_identity_not_semantic_definition(tmp_path: Path):
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,label TEXT,name TEXT,"
            "qualified_name TEXT,file_path TEXT,start_line INTEGER,signature TEXT,"
            "language TEXT,return_type TEXT,is_exported BOOLEAN,is_test BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO nodes VALUES (1,'File','start','vm/start.sh','vm/start.sh',"
            "1,'','bash','',0,0)"
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,source_line INTEGER,resolution_method TEXT,confidence REAL,"
            "trust_tier TEXT,candidate_count INTEGER,evidence_type TEXT)"
        )
        connection.commit()
    finally:
        connection.close()

    definitions, references, callers, properties = _graph_structural_roles(
        str(graph),
        (
            {
                "path": "vm/start.sh",
                "line": 1,
                "symbol": "start",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        limit=8,
    )

    assert definitions == ()
    assert references == ()
    assert callers == ()
    assert properties == ()


def test_shipped_index_fixture_covers_every_registered_parser_language():
    result = verify_gt_index_runtime()
    expected = {
        "bash" if capability.name == "shell" else capability.name
        for capability in LANGUAGE_CAPABILITIES
        if capability.structural_index
    }

    assert expected <= set(result["language_file_counts"])


def test_declaration_free_shell_source_builds_identity_only_graph(tmp_path: Path):
    script = tmp_path / "vm" / "start.sh"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\nexec qemu-system-x86_64 -nographic\n",
        encoding="utf-8",
    )

    evidence = inspect_repository(
        tmp_path,
        "Create the QEMU launcher and make it start the VM.",
        state_dir=tmp_path / ".state",
        source_revision="shell-source-1",
    )

    assert evidence.available is False
    assert evidence.substrate_ready is True
    assert evidence.index_current is True
    assert evidence.intelligence_valid is True
    assert evidence.retrieval_disposition == "empty"
    assert evidence.index is not None and evidence.index.graph_db
    connection = sqlite3.connect(evidence.index.graph_db)
    try:
        rows = connection.execute(
            "SELECT label,is_exported FROM nodes WHERE file_path='vm/start.sh'"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("File", 0)]
    assert evidence.definitions == ()
    assert evidence.callers == ()


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


def test_structural_roles_preserve_node_types_and_property_provenance(tmp_path: Path):
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,label TEXT,name TEXT,qualified_name TEXT,"
            "file_path TEXT,start_line INTEGER,signature TEXT,language TEXT,return_type TEXT,"
            "is_exported BOOLEAN,is_test BOOLEAN)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "Function",
                    "load_user",
                    "service.load_user",
                    "src/service.py",
                    3,
                    "def load_user(id: int) -> User",
                    "python",
                    "User",
                    1,
                    0,
                ),
                (
                    2,
                    "Function",
                    "handle",
                    "api.handle",
                    "src/api.py",
                    8,
                    "def handle()",
                    "python",
                    "",
                    0,
                    0,
                ),
                (
                    3,
                    "Function",
                    "guess",
                    "other.guess",
                    "src/other.py",
                    5,
                    "def guess()",
                    "python",
                    "",
                    0,
                    0,
                ),
            ),
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,"
            "type TEXT,source_line INTEGER,resolution_method TEXT,confidence REAL,"
            "trust_tier TEXT,candidate_count INTEGER,evidence_type TEXT)"
        )
        connection.execute(
            "CREATE TABLE properties (id INTEGER PRIMARY KEY,node_id INTEGER,kind TEXT,value TEXT,"
            "line INTEGER,confidence REAL,trust_tier TEXT,evidence_method TEXT,"
            "verification_status TEXT,property_id TEXT)"
        )
        connection.execute(
            "INSERT INTO properties VALUES (1,1,'param','id: int [required]',3,1.0,"
            "'CERTIFIED','tree_sitter_exact','verified','prop-1')"
        )
        connection.executemany(
            "INSERT INTO edges VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                (1, 2, 1, "CALLS", 8, "lsp_verified", 1.0, "CERTIFIED", 1, "lsp"),
                (2, 3, 1, "CALLS", 5, "verified_unique", 1.0, "CERTIFIED", 1, "static"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    definitions, references, callers, properties = _graph_structural_roles(
        str(graph),
        (
            {
                "path": "src/service.py",
                "line": 3,
                "symbol": "load_user",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        limit=8,
    )

    assert definitions[0]["return_type"] == "User"
    assert definitions[0]["is_exported"] is True
    assert definitions[0]["origin"] == "program"
    assert properties[0]["evidence_method"] == "tree_sitter_exact"
    assert properties[0]["property_id"] == "prop-1"
    assert [item["caller"] for item in callers] == ["handle"]
    assert references[0]["origin"] == "program"
    assert references[0]["target_path"] == "src/service.py"
    assert "resolution_method:lsp_verified" in references[0]["provenance"]


def test_structural_roles_abstain_on_ambiguous_same_file_symbol(tmp_path: Path):
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY,label TEXT,name TEXT,"
            "qualified_name TEXT,file_path TEXT,start_line INTEGER,signature TEXT,"
            "language TEXT,return_type TEXT,is_exported BOOLEAN,is_test BOOLEAN)"
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "Function",
                    "run",
                    "First.run",
                    "src/app.py",
                    3,
                    "def run()",
                    "python",
                    "",
                    0,
                    0,
                ),
                (
                    2,
                    "Function",
                    "run",
                    "Second.run",
                    "src/app.py",
                    13,
                    "def run()",
                    "python",
                    "",
                    0,
                    0,
                ),
            ),
        )
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY,source_id INTEGER,"
            "target_id INTEGER,type TEXT,source_line INTEGER,resolution_method TEXT,"
            "confidence REAL,trust_tier TEXT,candidate_count INTEGER,evidence_type TEXT)"
        )
        connection.commit()
    finally:
        connection.close()

    definitions, references, callers, properties = _graph_structural_roles(
        str(graph),
        ({"path": "src/app.py", "line": 0, "symbol": "run"},),
        limit=8,
    )

    assert definitions == ()
    assert references == ()
    assert callers == ()
    assert properties == ()


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


def test_incremental_refresh_holds_publication_lock_across_build(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    state = tmp_path / "state"
    root.mkdir()
    source = root / "app.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    helper = root / "helper.py"
    helper.write_text("def helper():\n    return 1\n", encoding="utf-8")
    initial = ensure_index_with_receipt(root, state_dir=state, source_revision="s1")
    assert initial.graph_db
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    helper.write_text("def helper():\n    return 2\n", encoding="utf-8")

    lock_state = {"depth": 0, "provider_runs": 0, "manifest_paths": []}

    @contextmanager
    def tracked_lock(_directory: Path, *, timeout: float = 30.0):
        del timeout
        lock_state["depth"] += 1
        try:
            yield
        finally:
            lock_state["depth"] -= 1

    original_run = indexer.subprocess.run

    def guarded_run(*args, **kwargs):
        assert lock_state["depth"] == 1
        lock_state["provider_runs"] += 1
        command = list(args[0])
        manifest = Path(command[command.index("-files-manifest") + 1])
        lock_state["manifest_paths"] = json.loads(manifest.read_text(encoding="utf-8"))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(indexer, "_graph_publication_lock", tracked_lock)
    monkeypatch.setattr(indexer.subprocess, "run", guarded_run)

    refreshed = refresh_index_files(
        root,
        initial.graph_db,
        ("app.py", "helper.py"),
        source_revision="s2",
    )

    assert refreshed.status is IndexBuildStatus.AVAILABLE
    assert lock_state == {
        "depth": 0,
        "provider_runs": 1,
        "manifest_paths": ["app.py", "helper.py"],
    }


def test_repository_session_reindexes_reverse_dependents_in_same_generation(tmp_path: Path):
    mirror = tmp_path / "mirror"
    state = tmp_path / "state"
    mirror.mkdir()
    (mirror / "app.py").write_text(
        "def value():\n    return 1\n", encoding="utf-8"
    )
    (mirror / "caller.py").write_text(
        "from app import value\n\ndef use():\n    return value()\n", encoding="utf-8"
    )
    for index in range(5):
        (mirror / f"spare_{index}.py").write_text(
            f"def spare_{index}():\n    return {index}\n", encoding="utf-8"
        )
    session = RepositorySession(
        root=mirror,
        state_dir=state,
        instruction="Change value while preserving its callers.",
    )
    initial = session.refresh(source_revision="r1")
    assert initial.intelligence_valid is True

    transition = SimpleNamespace(
        sensor_healthy=True,
        deleted=(),
        after_contents={"app.py": "def value():\n    return 2\n"},
        changed_paths=("app.py",),
    )
    assert session.apply_transition(transition, source_revision="r2") is True
    assert session._pending_index_paths == {"app.py", "caller.py"}
    assert session._requires_full_rebuild is False

    refreshed = session.refresh(source_revision="r2")
    assert refreshed.intelligence_valid is True
    assert refreshed.index is not None and refreshed.index.graph_db
    manifest = json.loads(
        Path(refreshed.index.graph_db).with_suffix(".manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["refresh_mode"] == "incremental"
    assert manifest["changed_paths"] == ["app.py", "caller.py"]


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


def test_repository_session_rejects_unexplained_graph_revision_advance(tmp_path: Path):
    """A new graph-input revision cannot certify an unchanged prior graph."""
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "app.py").write_text("x = 1\n", encoding="utf-8")
    session = RepositorySession(
        root=mirror,
        state_dir=tmp_path / "state",
        instruction="Change x.",
    )
    session.refresh(source_revision="s1")
    transition = SimpleNamespace(
        changed_paths=("app.py",),
        deleted=(),
        after_contents={"app.py": "x = 2\n"},
        sensor_healthy=True,
    )

    # This simulates a caller-side selector defect.  The repository session is
    # the last authority before publication and must fail closed rather than
    # rebind the old graph to s2 through the source_revision_only path.
    assert (
        session.apply_transition(
            transition,
            source_revision="s2",
            changed_paths=(),
        )
        is False
    )
    assert session.fresh is False
    assert session.evidence.status == "unexplained_graph_revision"


def test_repository_session_rebuilds_resolution_after_metadata_change(
    tmp_path: Path,
) -> None:
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "app.py").write_text("x = 1\n", encoding="utf-8")
    (mirror / "package.json").write_text('{"name":"one"}\n', encoding="utf-8")
    session = RepositorySession(
        root=mirror,
        state_dir=tmp_path / "state",
        instruction="Change x.",
    )
    initial = session.refresh(source_revision="s1")
    assert initial.substrate_ready is True
    transition = SimpleNamespace(
        changed_paths=("package.json",),
        deleted=(),
        before_contents={"package.json": '{"name":"one"}\n'},
        after_contents={"package.json": '{"name":"two"}\n'},
        sensor_healthy=True,
    )

    advanced, refreshed = session.apply_transition_and_refresh(
        transition,
        source_revision="s2",
        changed_paths=("package.json",),
    )

    assert advanced is True
    assert refreshed.substrate_ready is True
    assert session.refresh_log[-1]["mode"] == "full"


def test_repository_query_failure_is_not_reported_as_healthy_empty(tmp_path: Path) -> None:
    receipt = IndexBuildReceipt(
        status=IndexBuildStatus.AVAILABLE,
        graph_db=str(tmp_path / "missing-graph.db"),
        graph_revision="graph-1",
        source_revision="source-1",
        schema_valid=True,
        node_count=1,
        source_files=1,
        indexable_files=1,
        parser_failures=0,
    )

    evidence = inspect_repository(
        tmp_path,
        "Change app.py.",
        index_receipt=receipt,
        source_revision="source-1",
    )

    assert evidence.status == RepositoryIntelligenceStatus.INDEX_UNAVAILABLE.value
    assert evidence.substrate_status == RepositorySubstrateStatus.INVALID.value
    assert evidence.substrate_ready is False
    assert evidence.index is not None
    assert str(evidence.index.error_type).startswith("graph_query_")


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


def test_empty_to_materialized_source_refresh_builds_current_graph(tmp_path):
    """Deterministic reproduction of the count-dataset-tokens transition.

    A task whose workspace starts empty (no supported source) legitimately
    reports NOT_APPLICABLE.  When the model materializes source mid-task, the
    very next refresh must build and certify a current graph bound to the new
    source revision -- never fail the whole task on the empty start state.
    """
    session = RepositorySession(
        root=tmp_path / "mirror",
        state_dir=tmp_path / "state",
        instruction="fix the bug",
    )
    try:
        session.root.mkdir(parents=True, exist_ok=True)
        evidence = session.refresh(source_revision="r0-empty")
        assert evidence.substrate_ready is False
        assert evidence.index is None or not evidence.index.graph_db

        transition = SimpleNamespace(
            sensor_healthy=True,
            deleted=(),
            after_contents={"app.py": "def greet():\n    return 1\n"},
            changed_paths=("app.py",),
        )
        assert session.apply_transition(transition, source_revision="r1-source")
        evidence = session.refresh(source_revision="r1-source")

        assert evidence.substrate_ready is True
        assert evidence.substrate_status == RepositorySubstrateStatus.HEALTHY_CURRENT.value
        assert evidence.index_current is True
        assert evidence.intelligence_valid is True
        assert evidence.index is not None
        assert evidence.index.graph_db
        assert evidence.index.source_revision == "r1-source"
        assert evidence.source_revision == "r1-source"
        assert bool(evidence.graph_revision)
        assert session.fresh is True
    finally:
        session.close()


def test_full_index_forwards_the_host_budget_to_the_binary(monkeypatch, tmp_path):
    observed: dict[str, object] = {}

    def bounded_index(root, output, *, timeout):
        observed.update(root=root, output=output, timeout=timeout)
        return False

    monkeypatch.setattr(binary, "run_index", bounded_index)
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {
            "path_sha256": "a" * 64,
            "binary_sha256": "b" * 64,
        },
    )
    (tmp_path / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")

    receipt = ensure_index_with_receipt(tmp_path, timeout=7.9)

    assert observed["timeout"] == 7
    assert receipt.status is IndexBuildStatus.BUILD_FAILED
    assert receipt.error_type == "run_index_false"


def test_transition_and_refresh_is_one_synchronous_session_operation(monkeypatch, tmp_path):
    session = RepositorySession(
        root=tmp_path / "mirror",
        state_dir=tmp_path / "state",
        instruction="fix the bug",
    )
    session.root.mkdir(parents=True)
    transition = SimpleNamespace(
        sensor_healthy=True,
        deleted=(),
        after_contents={"app.py": "def value():\n    return 1\n"},
        changed_paths=("app.py",),
    )
    observed: dict[str, object] = {}

    def bounded_inspection(*args, **kwargs):
        observed.update(kwargs)
        return RepositoryEvidence(
            status=RepositoryIntelligenceStatus.INDEX_UNAVAILABLE.value,
            source_revision="r1",
        )

    monkeypatch.setattr("gt_engine.repository_intelligence.inspect_repository", bounded_inspection)

    advanced, evidence = session.apply_transition_and_refresh(
        transition,
        source_revision="r1",
        changed_paths=("app.py",),
        timeout=11.5,
    )

    assert advanced is True
    assert observed["timeout"] == 11.5
    assert evidence.status == RepositoryIntelligenceStatus.INDEX_UNAVAILABLE.value
    assert session.source_revision == "r1"


def test_failed_refresh_retains_dirty_work_and_does_not_advance_index_revision(
    monkeypatch, tmp_path
):
    session = RepositorySession(
        root=tmp_path / "mirror",
        state_dir=tmp_path / "state",
        instruction="fix the bug",
    )
    session.root.mkdir(parents=True)
    (session.root / "app.py").write_text(
        "def value():\n    return 1\n", encoding="utf-8"
    )
    initial = session.refresh(source_revision="r1")
    assert initial.intelligence_valid is True
    transition = SimpleNamespace(
        sensor_healthy=True,
        deleted=(),
        after_contents={"app.py": "def value():\n    return 2\n"},
        changed_paths=("app.py",),
    )
    assert session.apply_transition(transition, source_revision="r2") is True

    def failed_inspection(*args, **kwargs):
        return RepositoryEvidence(
            status=RepositoryIntelligenceStatus.INDEX_UNAVAILABLE.value,
            source_revision="r2",
            index_current=False,
            intelligence_valid=False,
            substrate_ready=False,
        )

    monkeypatch.setattr(
        "gt_engine.repository_intelligence.inspect_repository", failed_inspection
    )
    failed = session.refresh(source_revision="r2")

    assert failed.intelligence_valid is False
    assert session.indexed_source_revision == "r1"
    assert session._pending_index_paths == {"app.py"}
    assert session.refresh_log[-1]["published"] is False


def test_central_host_never_wraps_repository_refresh_in_an_abandonable_thread():
    source = (Path(__file__).resolve().parents[1] / "eval" / "gt_central_agent.py").read_text(
        encoding="utf-8"
    )

    assert "asyncio.to_thread(session.refresh" not in source
    abandonable_call = (
        "asyncio.to_thread(\n                                        repository_session.refresh"
    )
    assert abandonable_call not in source
    assert "repository_service.record_action(" in source
    assert "repository_service.prepare(" in source


def test_failed_refresh_never_serves_previous_graph_as_current(tmp_path):
    """A refresh timeout must never let the host deliver the prior graph."""
    session = RepositorySession(
        root=tmp_path / "mirror",
        state_dir=tmp_path / "state",
        instruction="fix the bug",
    )
    try:
        session.root.mkdir(parents=True, exist_ok=True)
        (session.root / "app.py").write_text("def greet():\n    return 1\n", encoding="utf-8")
        evidence = session.refresh(source_revision="r1")
        assert evidence.substrate_ready is True

        session.invalidate(source_revision="r2", status="refresh_timeout")
        assert session.fresh is False

        queried = session.query(
            source_revision="r2",
            active_paths=("app.py",),
            boundary="post_validate",
        )
        assert queried.substrate_ready is False
        assert queried.status == "refresh_timeout"
        assert queried.graph_revision == ""
    finally:
        session.close()


def test_refresh_recovers_after_timeout_invalidation(tmp_path):
    """After a failed refresh, the same current transition can be rebuilt."""
    session = RepositorySession(
        root=tmp_path / "mirror",
        state_dir=tmp_path / "state",
        instruction="fix the bug",
    )
    try:
        session.root.mkdir(parents=True, exist_ok=True)
        session.invalidate(source_revision="r1", status="refresh_timeout")

        transition = SimpleNamespace(
            sensor_healthy=True,
            deleted=(),
            after_contents={"app.py": "def greet():\n    return 1\n"},
            changed_paths=("app.py",),
        )
        assert session.apply_transition(transition, source_revision="r2")
        evidence = session.refresh(source_revision="r2")

        assert evidence.substrate_ready is True
        assert evidence.intelligence_valid is True
        assert session.fresh is True
    finally:
        session.close()
