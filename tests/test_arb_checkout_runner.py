from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.arb_adapter import RetrievalProbe, RetrievalProbeResult
from scripts.arb_checkout_runner import (
    _load_dense_backend,
    _repo_cache_path,
    assign_repository_shards,
    group_probes,
    run_groups,
)


def _probe(sample_id: str, repo: str, commit: str) -> RetrievalProbe:
    return RetrievalProbe(sample_id, repo, commit, "q", (), f"arb:{commit}")


def test_group_probes_is_deterministic_and_snapshot_scoped() -> None:
    groups = group_probes(
        (
            _probe("b", "z/repo", "2"),
            _probe("a", "a/repo", "1"),
            _probe("c", "z/repo", "2"),
        )
    )
    assert list(groups) == [("a/repo", "1"), ("z/repo", "2")]
    assert [item.sample_id for item in groups[("z/repo", "2")]] == ["b", "c"]


def test_repo_cache_path_rejects_path_injection(tmp_path) -> None:
    with pytest.raises(ValueError, match="invalid repository"):
        _repo_cache_path(tmp_path, "../escape")


def test_shard_validation() -> None:
    with pytest.raises(ValueError, match="shard_index"):
        run_groups(
            (),
            cache_dir="cache",
            work_dir="work",
            state_dir="state",
            output_dir="out",
            shard_index=2,
            shard_count=2,
        )


def test_shard_selection_is_deterministic() -> None:
    probes = tuple(_probe(str(i), "owner/repo", str(i)) for i in range(5))
    groups = list(group_probes(probes).items())
    assert [key for key, _ in groups[1::2]] == [("owner/repo", "1"), ("owner/repo", "3")]


def test_snapshot_sharding_balances_large_repository_without_splitting_commit() -> None:
    probes = tuple(
        [
            *(
                _probe(f"large-{index}", "owner/large", str(index))
                for index in range(8)
            ),
            _probe("shared-a", "owner/shared", "same"),
            _probe("shared-b", "owner/shared", "same"),
            _probe("small", "owner/small", "1"),
            _probe("other", "owner/other", "1"),
        ]
    )

    assignments = assign_repository_shards(probes, shard_count=4)

    large_shards = {
        shard
        for shard, rows in enumerate(assignments)
        if any(probe.repository == "owner/large" for probe in rows)
    }
    shared_shards = {
        shard
        for shard, rows in enumerate(assignments)
        if any(probe.base_commit == "same" for probe in rows)
    }
    loads = [len(rows) for rows in assignments]

    assert len(large_shards) > 1
    assert len(shared_shards) == 1
    assert max(loads) - min(loads) <= 1
    assert sorted(probe.sample_id for rows in assignments for probe in rows) == sorted(
        probe.sample_id for probe in probes
    )


def test_dense_backend_is_optional_but_required_mode_fails_closed(tmp_path) -> None:
    assert _load_dense_backend(None, require_dense=False) is None
    with pytest.raises(ValueError, match="dense model directory"):
        _load_dense_backend(None, require_dense=True)


def test_dense_backend_loads_the_pinned_snowflake_onnx_model_once(tmp_path, monkeypatch) -> None:
    sentinel = object()
    calls: list[object] = []

    def fake_from_directory(path):
        calls.append(path)
        return sentinel

    monkeypatch.setattr(
        "scripts.arb_checkout_runner.SnowflakeOnnxDenseBackend.from_directory",
        fake_from_directory,
    )

    loaded = _load_dense_backend(tmp_path, require_dense=True)

    assert loaded is sentinel
    assert calls == [tmp_path]


def test_runner_emits_flushed_group_and_sample_progress(tmp_path, monkeypatch, capsys):
    probes = (
        _probe("a", "owner/repo", "1"),
        _probe("b", "owner/repo", "1"),
    )

    monkeypatch.setattr(
        "scripts.arb_checkout_runner.ensure_bare_cache",
        lambda cache, repository, base_commit: tmp_path / "bare.git",
    )

    def materialize(_bare, worktree, _base_commit):
        worktree.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("scripts.arb_checkout_runner.materialize_worktree", materialize)
    monkeypatch.setattr("scripts.arb_checkout_runner._run_git", lambda *args, **kwargs: "")
    index_calls: list[tuple[object, ...]] = []

    def fake_inspect_index(*args, **kwargs):
        index_calls.append((args, kwargs))
        return SimpleNamespace(graph_db=None)

    monkeypatch.setattr("scripts.arb_checkout_runner.inspect_index", fake_inspect_index)
    monkeypatch.setattr(
        "scripts.arb_checkout_runner.run_probe",
        lambda probe, **kwargs: RetrievalProbeResult(
            sample_id=probe.sample_id,
            repository=probe.repository,
            base_commit=probe.base_commit,
            task_type=probe.task_type,
            retrieval_intent="other",
            ranked_candidates=(),
            delivered_evidence=(),
            abstained=True,
            abstention_reason="test",
            graph_status="available",
            graph_revision="g",
            source_revision=probe.source_revision,
            index_latency_ms=1.0,
            query_latency_ms=2.0,
        ),
    )

    assert (
        run_groups(
            probes,
            cache_dir=tmp_path / "cache",
            work_dir=tmp_path / "work",
            state_dir=tmp_path / "state",
            output_dir=tmp_path / "out",
        )
        == 2
    )
    assert len(index_calls) == 1
    output = capsys.readouterr().out
    assert "status=started" in output
    assert "sample=a" in output
    assert "sample=b" in output
    assert "status=complete" in output
