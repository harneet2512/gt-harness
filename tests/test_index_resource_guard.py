from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gt_engine import indexer


def _write_fake_indexer(path: Path) -> None:
    path.write_text(
        """from __future__ import annotations
import os
import sqlite3
import sys

output = sys.argv[sys.argv.index('-output') + 1]
assert 'OPENAI_API_KEY' not in os.environ
assert 'OPENROUTER_API_KEY' not in os.environ
assert os.environ['GOMAXPROCS'] == '2'
assert os.environ['GOMEMLIMIT'].endswith('B')
with sqlite3.connect(output) as connection:
    connection.execute('create table project_meta (key text)')
sys.stdout.write('x' * 200_000)
sys.stderr.write('y' * 200_000)
""",
        encoding="utf-8",
    )


def test_bounded_indexer_sanitizes_environment_and_seals_resource_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
    fake = tmp_path / "fake-index.py"
    _write_fake_indexer(fake)
    monkeypatch.setenv("OPENAI_API_KEY", "SECRET-CANARY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "SECOND-CANARY")
    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: sys.executable)
    monkeypatch.setattr(
        indexer,
        "_index_command",
        lambda binary, root, output: [binary, str(fake), "-root", root, "-output", output],
    )
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )

    graph = indexer.ensure_index(str(repo), state_dir=str(state))

    assert graph is not None
    evidence_path = Path(graph).with_name("index-resource.json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    supplied = evidence.pop("evidence_sha256")
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    assert supplied == hashlib.sha256(encoded).hexdigest()
    assert evidence["schema"] == "gt.index_resource.v1"
    assert evidence["status"] == "completed"
    assert evidence["stdout_bytes"] == 200_000
    assert evidence["stderr_bytes"] == 200_000
    assert "SECRET-CANARY" not in evidence_path.read_text(encoding="utf-8")
    assert not list(evidence_path.parent.glob("*.stdout"))
    assert not list(evidence_path.parent.glob("*.stderr"))
    manifest_path = Path(graph).with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["index_resource_sha256"] == hashlib.sha256(
        evidence_path.read_bytes()
    ).hexdigest()
    evidence_path.write_text("{}", encoding="utf-8")
    valid, reason = indexer._certify_published_graph(
        Path(graph),
        manifest_path,
        expected_root=repo,
        expected_binary_sha256="b" * 64,
    )
    assert valid is False
    assert reason == "index_resource_mismatch"


def test_memory_guard_failure_is_sealed_and_preserves_existing_graph(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
    root_key = hashlib.sha256(
        os.path.realpath(repo).encode("utf-8", "surrogatepass")
    ).hexdigest()[:16]
    graph = state / root_key / "graph.db"
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b"known-good")
    monkeypatch.setattr(
        indexer,
        "_run_index_bounded",
        lambda *_args, **_kwargs: indexer.IndexProcessResult(
            success=False,
            status="memory_guard_triggered",
            error_code="GT_INDEX_MEMORY_GUARD_TRIGGERED",
            exit_code=137,
            peak_rss_bytes=900_000_000,
            memory_limit_bytes=800_000_000,
        ),
    )
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )

    assert indexer.ensure_index(str(repo), state_dir=str(state)) is None
    assert graph.read_bytes() == b"known-good"
    failure_path = graph.with_name("graph.failure.json")
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    supplied = failure.pop("manifest_sha256")
    encoded = json.dumps(failure, sort_keys=True, separators=(",", ":")).encode()
    assert supplied == hashlib.sha256(encoded).hexdigest()
    assert failure["error_code"] == "GT_INDEX_MEMORY_GUARD_TRIGGERED"
    assert failure["resource_evidence_sha256"]

    receipt = indexer.ensure_index_with_receipt(repo, state_dir=state)
    assert receipt.error_type == "GT_INDEX_MEMORY_GUARD_TRIGGERED"
    assert receipt.memory_evidence is True
    assert receipt.exit_code == 137
    assert receipt.resource_evidence_sha256 == failure["resource_evidence_sha256"]
    failure_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(indexer, "ensure_index", lambda *_args, **_kwargs: None)
    invalid = indexer.ensure_index_with_receipt(repo, state_dir=state)
    assert invalid.error_type == "index_failure_evidence_invalid"
    assert invalid.memory_evidence is False


def test_bounded_indexer_kills_only_child_when_rss_guard_is_crossed(
    tmp_path: Path, monkeypatch
) -> None:
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: sys.executable)
    monkeypatch.setattr(
        indexer,
        "_index_command",
        lambda binary, root, output: [binary, str(sleeper)],
    )
    limit = 64 * 1024 * 1024
    monkeypatch.setattr(indexer, "_effective_index_memory_limit", lambda _snapshot: limit)
    monkeypatch.setattr(indexer, "_process_rss_bytes", lambda _pid: limit + 1)

    started = time.monotonic()
    result = indexer._run_index_bounded(
        str(tmp_path), tmp_path / "graph.db", tmp_path
    )

    assert time.monotonic() - started < 10
    assert result.success is False
    assert result.status == "memory_guard_triggered"
    assert result.error_code == "GT_INDEX_MEMORY_GUARD_TRIGGERED"
    assert result.memory_evidence is True


def test_pipe_cleanup_closes_raw_descriptors_not_buffered_streams(monkeypatch) -> None:
    closed: list[int] = []

    class Stream:
        def __init__(self, fd: int):
            self.fd = fd

        def fileno(self):
            return self.fd

        def close(self):
            raise AssertionError("buffered close may block")

    process = type("Process", (), {"stdout": Stream(7), "stderr": Stream(8)})()
    monkeypatch.setattr(indexer.os, "close", closed.append)

    indexer._close_pipe_descriptors(process)
    assert closed == [7, 8]


def test_index_memory_budget_accounts_for_current_cgroup_usage() -> None:
    mib = 1024 * 1024
    limit = indexer._effective_index_memory_limit(
        {"max": 1024 * mib, "current": 800 * mib}
    )

    assert 0 < limit <= 96 * mib
    assert indexer._effective_index_memory_limit({"max": 1024 * mib, "current": None}) == 0


def test_partial_benchmark_identity_refuses_before_process_launch(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    (repo / "main.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setenv("GT_TASK_ID", "task-a")
    monkeypatch.delenv("GT_PRODUCT_SOURCE_SHA", raising=False)
    calls = 0

    def run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("gt-index must not launch")

    monkeypatch.setattr(indexer, "_run_index_bounded", run)
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )

    assert indexer.ensure_index(str(repo), state_dir=str(state)) is None
    assert calls == 0
    failures = list(state.rglob("graph.failure.json"))
    evidence = list(state.rglob("index-failure-resource.json"))
    assert len(failures) == len(evidence) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert failure["error_code"] == "GT_INDEX_IDENTITY_INVALID"
    assert failure["identity_scope"] == "benchmark_invalid"
    receipt = indexer.ensure_index_with_receipt(repo, state_dir=state)
    assert receipt.error_type == "GT_INDEX_IDENTITY_INVALID"


def test_failure_pair_publication_rolls_back_on_manifest_write_failure(
    tmp_path: Path, monkeypatch
) -> None:
    gt_dir = tmp_path / "state"
    gt_dir.mkdir()
    old_evidence = gt_dir / "index-failure-resource.json"
    old_failure = gt_dir / "graph.failure.json"
    old_evidence.write_bytes(b"old-evidence")
    old_failure.write_bytes(b"old-failure")
    staged = gt_dir / ".new-resource.json"
    staged.write_bytes(b"new-evidence")
    reuse_key = indexer.IndexReuseKey("a" * 64, "b" * 64, "v")
    original = indexer._sealed_json

    def fail_manifest(path, payload, digest_field):
        if path.name == "graph.failure.json":
            raise OSError("injected publication failure")
        return original(path, payload, digest_field)

    monkeypatch.setattr(indexer, "_sealed_json", fail_manifest)
    with pytest.raises(OSError, match="injected"):
        indexer._publish_graph_failure(
            gt_dir, root=str(tmp_path), reuse_key=reuse_key,
            error_code="GT_INDEX_PROCESS_FAILED", staged_evidence=staged,
            identity={"identity_scope": "benchmark_bound", "task_id": "task-a",
                      "product_source_sha": "a" * 40},
        )
    assert old_evidence.read_bytes() == b"old-evidence"
    assert old_failure.read_bytes() == b"old-failure"


def test_successful_process_with_corrupt_database_emits_sealed_failure(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    (repo / "main.py").write_text("pass\n", encoding="utf-8")

    def corrupt(_root: str, output: Path, _log_dir: Path):
        output.write_bytes(b"not sqlite")
        return indexer.IndexProcessResult(True, "completed", "", exit_code=0)

    monkeypatch.setattr(indexer, "_run_index_bounded", corrupt)
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )

    receipt = indexer.ensure_index_with_receipt(repo, state_dir=state)

    assert receipt.success is False
    assert receipt.error_type == "GT_INDEX_OUTPUT_INVALID"
    assert receipt.resource_evidence_sha256


def test_concurrent_index_builds_are_serialized_and_publish_one_pair(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    (repo / "main.py").write_text("pass\n", encoding="utf-8")
    active = 0
    maximum = 0
    calls = 0

    def build(_root: str, output: Path, _log_dir: Path):
        nonlocal active, maximum, calls
        active += 1
        maximum = max(maximum, active)
        calls += 1
        time.sleep(0.2)
        connection = indexer.sqlite3.connect(output)
        try:
            connection.execute("create table project_meta (key text)")
            connection.commit()
        finally:
            connection.close()
        active -= 1
        return indexer.IndexProcessResult(True, "completed", "", exit_code=0)

    monkeypatch.setattr(indexer, "_run_index_bounded", build)
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _item: indexer.ensure_index(str(repo), state_dir=str(state)), range(2))
        )

    assert results[0] == results[1]
    assert maximum == 1
    assert calls in {1, 2}
    graph = Path(results[0])
    manifest = json.loads(graph.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert manifest["graph_sha256"] == hashlib.sha256(graph.read_bytes()).hexdigest()


def test_failed_refresh_preserves_previous_graph_manifest_and_resource_pair(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    source = repo / "main.py"
    source.write_text("x = 1\n", encoding="utf-8")

    def build(_root: str, output: Path, _log_dir: Path):
        connection = indexer.sqlite3.connect(output)
        connection.execute("create table project_meta (key text)")
        connection.commit()
        connection.close()
        return indexer.IndexProcessResult(True, "completed", "", exit_code=0)

    monkeypatch.setattr(indexer, "_run_index_bounded", build)
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )
    graph = Path(indexer.ensure_index(str(repo), state_dir=str(state)))
    manifest = graph.with_suffix(".manifest.json")
    resource = graph.with_name("index-resource.json")
    before = (graph.read_bytes(), manifest.read_bytes(), resource.read_bytes())
    source.write_text("x = 2\n", encoding="utf-8")
    monkeypatch.setattr(
        indexer,
        "_run_index_bounded",
        lambda *_args: indexer.IndexProcessResult(
            False, "memory_guard_triggered", "GT_INDEX_MEMORY_GUARD_TRIGGERED", exit_code=-9
        ),
    )

    assert indexer.ensure_index(str(repo), state_dir=str(state)) is None
    assert (graph.read_bytes(), manifest.read_bytes(), resource.read_bytes()) == before
    valid, reason = indexer._certify_published_graph(
        graph, manifest, expected_root=repo, expected_binary_sha256="b" * 64
    )
    assert valid is True
    assert reason == "ok"
    assert graph.with_name("index-failure-resource.json").is_file()

    source.write_text("x = 1\n", encoding="utf-8")
    assert indexer.ensure_index(str(repo), state_dir=str(state)) == str(graph)
    assert not graph.with_name("graph.failure.json").exists()
    assert not graph.with_name("index-failure-resource.json").exists()


def test_failure_receipt_rejects_semantically_mismatched_pair(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    (repo / "main.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(
        indexer,
        "_run_index_bounded",
        lambda *_args: indexer.IndexProcessResult(
            False, "timeout", "GT_INDEX_TIMEOUT", exit_code=-9
        ),
    )
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"path_sha256": "a" * 64, "binary_sha256": "b" * 64},
    )
    first = indexer.ensure_index_with_receipt(repo, state_dir=state)
    assert first.error_type == "GT_INDEX_TIMEOUT"
    failure_path = next(state.rglob("graph.failure.json"))
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    failure["error_code"] = "GT_INDEX_OUTPUT_INVALID"
    failure["resource_evidence_path"] = "wrong.json"
    failure.pop("manifest_sha256")
    failure["manifest_sha256"] = hashlib.sha256(
        json.dumps(failure, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    monkeypatch.setattr(indexer, "ensure_index", lambda *_args, **_kwargs: None)

    receipt = indexer.ensure_index_with_receipt(repo, state_dir=state)
    assert receipt.error_type == "index_failure_evidence_invalid"
