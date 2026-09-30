"""gt-index runs at the lowest CPU priority (gt_engine.indexer._low_priority).

DeepSWE campaign 2026-09-29, same model and same days for both arms: the
agent's own commands took p90 22.6 s per step with GT against 8.6 s without,
while a background graph refresh ran up to 58 minutes per task
(dynamodb-toolbox-lazy-recursive-schemas). The rebuild competed with the
agent's builds and tests for CPU; at nice 19 the agent's work goes first.
"""
from __future__ import annotations

from gt_engine import indexer

ARGV = ["/opt/gt/gt-index", "-root", "/app", "-output", "/tmp/g.db"]


def test_index_command_is_wrapped_in_nice_on_posix(monkeypatch):
    monkeypatch.setattr(indexer.os, "name", "posix")
    monkeypatch.setattr(indexer.shutil, "which", lambda name: "/usr/bin/nice" if name == "nice" else None)
    monkeypatch.delenv(indexer.INDEX_NICE_ENV, raising=False)

    assert indexer._low_priority(ARGV) == ["/usr/bin/nice", "-n", "19", *ARGV]


def test_no_wrapper_when_nice_is_missing(monkeypatch):
    monkeypatch.setattr(indexer.os, "name", "posix")
    monkeypatch.delenv(indexer.INDEX_NICE_ENV, raising=False)
    monkeypatch.setattr(indexer.shutil, "which", lambda _name: None)

    assert indexer._low_priority(ARGV) == ARGV


def test_no_wrapper_on_windows(monkeypatch):
    monkeypatch.setattr(indexer.os, "name", "nt")
    monkeypatch.delenv(indexer.INDEX_NICE_ENV, raising=False)
    monkeypatch.setattr(indexer.shutil, "which", lambda _name: "C:/nice.exe")

    assert indexer._low_priority(ARGV) == ARGV


def test_env_switch_turns_the_wrapper_off(monkeypatch):
    monkeypatch.setattr(indexer.os, "name", "posix")
    monkeypatch.setattr(indexer.shutil, "which", lambda _name: "/usr/bin/nice")
    monkeypatch.setenv(indexer.INDEX_NICE_ENV, "0")

    assert indexer._low_priority(ARGV) == ARGV


def test_the_bounded_launch_path_is_wrapped(monkeypatch, tmp_path):
    """The wrapper reaches Popen for a command_factory build (incremental and
    revision amends go through it), not only in isolation. Platform-independent:
    a spy stands in for _low_priority (faking os.name breaks pathlib on Windows)."""
    monkeypatch.setattr(indexer, "_low_priority", lambda argv: ["NICED", *argv])
    seen = []

    class Stop(Exception):
        pass

    def fake_popen(argv, *args, **kwargs):
        seen.append(list(argv))
        raise Stop

    monkeypatch.setattr(indexer.subprocess, "Popen", fake_popen)
    # The launch guards that run before Popen (process-tree guard, memory headroom, binary).
    monkeypatch.setattr(indexer, "_has_verified_index_process_tree_guard", lambda: True)
    monkeypatch.setattr(indexer, "_cgroup_snapshot", lambda: {})
    monkeypatch.setattr(indexer, "_effective_index_memory_limit", lambda _before: 1 << 30)
    monkeypatch.setattr(indexer, "_resolved_binary_path", lambda: "/opt/gt/gt-index")
    try:
        indexer._run_index_bounded(str(tmp_path), tmp_path / "g.db", tmp_path,
                                   command_factory=lambda b, r, o: [b, "-amend", r, o])
    except Stop:
        pass
    except Exception:  # noqa: BLE001 - later failures do not matter once argv is seen
        pass
    assert seen and seen[0] == ["NICED", "/opt/gt/gt-index", "-amend", str(tmp_path), str(tmp_path / "g.db")]
