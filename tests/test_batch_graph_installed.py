"""Execute the actual batch producer through the harness resource guard."""
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from gt_engine import indexer
from gt_engine.engine_state import RuntimeLayout


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="installed Linux producer required")
def test_installed_batch_preserves_parent_and_reuses_parser_inputs(tmp_path):
    binary = os.environ.get("GT_INDEX_BINARY", "")
    assert binary and Path(binary).is_file(), "provide the exact candidate Linux binary"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mod.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    (root / "caller.py").write_text("from mod import answer\ndef call():\n    return answer()\n", encoding="utf-8")
    state = tmp_path / "state"

    def build(name, parent=None):
        logs = state / "revisions" / name
        logs.mkdir(parents=True)
        output = logs / "graph.db"
        factory = None if parent is None else lambda binary, root, output: (
            indexer._index_command(binary, root, output) + ["-amend-parent", str(parent)])
        result = indexer._run_index_bounded(str(root), output, logs, command_factory=factory)
        assert result.success, (result.error_code, result.stderr_tail)
        return output, result

    parent, _ = build("parent")
    original = hashlib.sha256(parent.read_bytes()).hexdigest()
    with closing(sqlite3.connect(f"file:{parent}?mode=ro", uri=True)) as db:
        old_id = db.execute("SELECT id FROM nodes WHERE name='call' AND label='Function'").fetchone()[0]
    (root / "mod.py").write_text("def answer():\n    return 2\n", encoding="utf-8")
    candidate, result = build("candidate", parent)
    assert "Parse cache: 1 hits, 1 misses" in result.stderr_tail
    assert "Batch structure:" in result.stderr_tail
    with closing(sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)) as db:
        assert db.execute("SELECT id FROM nodes WHERE name='call' AND label='Function'").fetchone()[0] == old_id
        assert db.execute("SELECT value FROM project_meta WHERE key='analysis_state'").fetchone()[0] == "complete"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        receipt = json.loads(db.execute("SELECT value FROM project_meta WHERE key='core_phase_receipt'").fetchone()[0])
        assert receipt["state"] == "committed"
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == original


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="installed Linux producer required")
def test_installed_refresh_selects_one_certified_batch(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mod.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    (root / "caller.py").write_text("from mod import answer\ndef call():\n    return answer()\n", encoding="utf-8")
    layout = RuntimeLayout.resolve(workspace=root, state_root=tmp_path / "state", task_id="batch")
    parent = indexer.ensure_index(str(root), layout=layout)
    assert parent, "actual initial graph was not certified"
    before = Path(parent).read_bytes()
    changed = ("mod.py", "new.py", "extra.py", "fourth.py", "pyproject.toml")
    for path in changed:
        (root / path).write_text("# changed\n", encoding="utf-8")
    receipt = indexer.refresh_index_files(root, parent, changed, layout=layout, source_revision="candidate")
    assert receipt.success, (receipt.error_type, receipt.build_mode_reason)
    assert receipt.build_mode == "incremental", receipt.build_mode_reason
    assert len(receipt.incremental_results) == 1
    result = receipt.incremental_results[0]
    assert result["mode"] == "batch"
    assert result["paths"] == sorted(changed)
    assert result["parser_nodes_retained"] > 0
    assert result["parse_cache_hits"] == 1
    assert result["resolver_passes"] == 1
    assert Path(parent).read_bytes() == before
