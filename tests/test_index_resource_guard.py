from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

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
    monkeypatch.setattr(indexer, "_effective_index_memory_limit", lambda _snapshot: 1)
    monkeypatch.setattr(indexer, "_process_rss_bytes", lambda _pid: 2)

    started = time.monotonic()
    result = indexer._run_index_bounded(
        str(tmp_path), tmp_path / "graph.db", tmp_path
    )

    assert time.monotonic() - started < 10
    assert result.success is False
    assert result.status == "memory_guard_triggered"
    assert result.error_code == "GT_INDEX_MEMORY_GUARD_TRIGGERED"
    assert result.memory_evidence is True
