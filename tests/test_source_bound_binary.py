from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import gt_engine.indexer as indexer


def _fake_go(*, fail: bool = False):
    calls: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        calls.append(tuple(str(item) for item in command))
        if command[1] == "version":
            return SimpleNamespace(returncode=0, stdout="go version go-test windows/amd64\n")
        if command[1] == "env":
            return SimpleNamespace(returncode=0, stdout="windows\namd64\ncc\n")
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"partial" if fail else b"source-compatible")
        return SimpleNamespace(returncode=1 if fail else 0, stdout="", stderr="compiler failed")

    return run, calls


def _fake_go_with_version(version: str):
    calls: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        calls.append(tuple(str(item) for item in command))
        if command[1] == "version":
            return SimpleNamespace(returncode=0, stdout=version)
        if command[1] == "env":
            return SimpleNamespace(returncode=0, stdout="windows\namd64\ncc\n")
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"source-compatible")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run, calls


def test_source_bound_build_is_keyed_and_reused(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "cmd" / "gt-index").mkdir(parents=True)
    (source / "go.mod").write_text("module example\n", encoding="utf-8")
    (source / "cmd" / "gt-index" / "main.go").write_text("package main\n", encoding="utf-8")
    cache = tmp_path / "cache"
    fake_run, calls = _fake_go()
    monkeypatch.setattr(indexer, "_INDEX_SOURCE_ROOT", source)
    monkeypatch.setattr(indexer, "_binary_cache_root", lambda: cache)
    monkeypatch.setattr(indexer.shutil, "which", lambda name: "go" if name == "go" else None)
    monkeypatch.setattr(indexer.subprocess, "run", fake_run)

    first = indexer._build_source_compatible_binary()
    second = indexer._build_source_compatible_binary()

    assert first == second
    assert Path(first).read_bytes() == b"source-compatible"
    assert sum(call[1] == "build" for call in calls) == 1
    sidecars = list(cache.rglob("gt-index.exe.json")) + list(cache.rglob("gt-index.json"))
    assert len(sidecars) == 1
    metadata = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert metadata["binary_sha256"] == hashlib.sha256(Path(first).read_bytes()).hexdigest()
    assert not list(cache.rglob("*.tmp"))


def test_source_bound_cache_tamper_is_rebuilt(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "go.mod").write_text("module example\n", encoding="utf-8")
    (source / "main.go").write_text("package main\n", encoding="utf-8")
    cache = tmp_path / "cache"
    fake_run, calls = _fake_go()
    monkeypatch.setattr(indexer, "_INDEX_SOURCE_ROOT", source)
    monkeypatch.setattr(indexer, "_binary_cache_root", lambda: cache)
    monkeypatch.setattr(indexer.shutil, "which", lambda name: "go" if name == "go" else None)
    monkeypatch.setattr(indexer.subprocess, "run", fake_run)

    binary = Path(indexer._build_source_compatible_binary())
    binary.write_bytes(b"tampered")
    rebuilt = Path(indexer._build_source_compatible_binary())

    assert rebuilt.read_bytes() == b"source-compatible"
    assert sum(call[1] == "build" for call in calls) == 2


def test_source_and_header_changes_select_new_cache_identity(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "go.mod").write_text("module example\n", encoding="utf-8")
    (source / "main.go").write_text("package main\n", encoding="utf-8")
    (source / "sqlite_bridge.h").write_text("#define VERSION 1\n", encoding="utf-8")
    cache = tmp_path / "cache"
    fake_run, calls = _fake_go()
    monkeypatch.setattr(indexer, "_INDEX_SOURCE_ROOT", source)
    monkeypatch.setattr(indexer, "_binary_cache_root", lambda: cache)
    monkeypatch.setattr(indexer.shutil, "which", lambda name: "go" if name == "go" else None)
    monkeypatch.setattr(indexer.subprocess, "run", fake_run)

    first = indexer._build_source_compatible_binary()
    (source / "sqlite_bridge.h").write_text("#define VERSION 2\n", encoding="utf-8")
    second = indexer._build_source_compatible_binary()
    (source / "main.go").write_text("package main\n// changed\n", encoding="utf-8")
    third = indexer._build_source_compatible_binary()

    assert first != second != third
    assert sum(call[1] == "build" for call in calls) == 3


def test_toolchain_change_selects_new_cache_identity(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "go.mod").write_text("module example\n", encoding="utf-8")
    (source / "main.go").write_text("package main\n", encoding="utf-8")
    cache = tmp_path / "cache"
    state = {"version": "go version go-test1 windows/amd64\n"}
    calls: list[tuple[str, ...]] = []

    def fake_run(command, **_kwargs):
        calls.append(tuple(str(item) for item in command))
        if command[1] == "version":
            return SimpleNamespace(returncode=0, stdout=state["version"])
        if command[1] == "env":
            return SimpleNamespace(returncode=0, stdout="windows\namd64\ncc\n")
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"source-compatible")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(indexer, "_INDEX_SOURCE_ROOT", source)
    monkeypatch.setattr(indexer, "_binary_cache_root", lambda: cache)
    monkeypatch.setattr(indexer.shutil, "which", lambda name: "go" if name == "go" else None)
    monkeypatch.setattr(indexer.subprocess, "run", fake_run)

    first = indexer._build_source_compatible_binary()
    state["version"] = "go version go-test2 windows/amd64\n"
    second = indexer._build_source_compatible_binary()

    assert first != second
    assert sum(call[1] == "build" for call in calls) == 2


def test_in_process_source_edit_revalidates_internal_selection(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "go.mod").write_text("module example\n", encoding="utf-8")
    (source / "main.go").write_text("package main\n", encoding="utf-8")
    cache = tmp_path / "cache"
    fake_run, calls = _fake_go()
    monkeypatch.setattr(indexer, "_INDEX_SOURCE_ROOT", source)
    monkeypatch.setattr(indexer, "_binary_cache_root", lambda: cache)
    monkeypatch.setattr(indexer.shutil, "which", lambda name: "go" if name == "go" else None)
    monkeypatch.setattr(indexer, "_go_toolchain_identity", lambda _go: "toolchain")
    monkeypatch.setattr(indexer.subprocess, "run", fake_run)
    monkeypatch.delenv("GT_INDEX_BINARY", raising=False)
    monkeypatch.delenv(indexer._INTERNAL_BINARY_MARKER, raising=False)

    first = indexer._seed_binary_env()
    (source / "main.go").write_text("package main\n// changed\n", encoding="utf-8")
    second = indexer._seed_binary_env()

    assert first != second
    assert os.environ[indexer._INTERNAL_BINARY_MARKER] == "1"
    assert sum(call[1] == "build" for call in calls) == 2
    os.environ.pop("GT_INDEX_BINARY", None)
    os.environ.pop(indexer._INTERNAL_BINARY_MARKER, None)


def test_source_bound_build_failure_publishes_nothing(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "go.mod").write_text("module example\n", encoding="utf-8")
    (source / "main.go").write_text("package main\n", encoding="utf-8")
    cache = tmp_path / "cache"
    fake_run, calls = _fake_go(fail=True)
    monkeypatch.setattr(indexer, "_INDEX_SOURCE_ROOT", source)
    monkeypatch.setattr(indexer, "_binary_cache_root", lambda: cache)
    monkeypatch.setattr(indexer.shutil, "which", lambda name: "go" if name == "go" else None)
    monkeypatch.setattr(indexer.subprocess, "run", fake_run)
    monkeypatch.delenv("GT_INDEX_BINARY", raising=False)
    monkeypatch.delenv(indexer._INTERNAL_BINARY_MARKER, raising=False)

    assert indexer._build_source_compatible_binary() == ""
    assert sum(call[1] == "build" for call in calls) == 1
    assert not list(cache.rglob("gt-index*"))
    assert not list(cache.rglob("*.tmp"))
    assert indexer._binary_certification()["selection_error"] == "build_failed:exit=1"


def test_explicit_binary_is_operator_override_and_missing_fails_closed(tmp_path, monkeypatch):
    binary = tmp_path / "operator-gt-index"
    binary.write_bytes(b"operator")
    monkeypatch.setenv("GT_INDEX_BINARY", str(binary))
    monkeypatch.setattr(indexer, "_build_source_compatible_binary", lambda: (_ for _ in ()).throw(AssertionError("must not build")))

    assert indexer._seed_binary_env() == str(binary)
    assert indexer._binary_certification()["selection"] == "operator_override"

    binary.unlink()
    assert indexer._seed_binary_env() == ""
