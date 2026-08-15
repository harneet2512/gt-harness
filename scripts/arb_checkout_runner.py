#!/usr/bin/env python3
"""Run the GT ARB adapter against lossless exact-base Git worktrees."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from gt_engine.hybrid_retrieval import DenseEmbeddingBackend
from gt_engine.repository_intelligence import inspect_index
from gt_engine.snowflake_onnx import SnowflakeOnnxDenseBackend
from scripts.arb_adapter import RetrievalProbe, load_redacted_samples, run_probe

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _load_dense_backend(
    model_dir: str | Path | None,
    *,
    require_dense: bool,
) -> DenseEmbeddingBackend | None:
    """Load one local, pinned dense model for the entire shard process."""

    if model_dir is None or not str(model_dir).strip():
        if require_dense:
            raise ValueError("a dense model directory is required")
        return None
    return SnowflakeOnnxDenseBackend.from_directory(model_dir)


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def group_probes(
    probes: tuple[RetrievalProbe, ...],
) -> dict[tuple[str, str], tuple[RetrievalProbe, ...]]:
    groups: dict[tuple[str, str], list[RetrievalProbe]] = defaultdict(list)
    for probe in probes:
        groups[(probe.repository, probe.base_commit)].append(probe)
    return {key: tuple(value) for key, value in sorted(groups.items())}


def assign_repository_shards(
    probes: tuple[RetrievalProbe, ...], *, shard_count: int
) -> tuple[tuple[RetrievalProbe, ...], ...]:
    """Balance exact snapshots without splitting a reusable checkout group.

    Repository-wide affinity made a large repository a serial bottleneck: the
    complete ARB corpus produced 20-way shard loads ranging from 7 to 88 rows.
    The real reusable unit is one ``(repository, base_commit)`` checkout and
    index, so longest-processing-time assignment keeps that unit atomic while
    allowing independent snapshots of a large repository to run in parallel.
    """

    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    assignments: list[list[RetrievalProbe]] = [[] for _ in range(shard_count)]
    loads = [0 for _ in range(shard_count)]
    snapshot_groups = group_probes(probes)
    for (_repository, _base_commit), rows in sorted(
        snapshot_groups.items(),
        key=lambda item: (
            -len(item[1]),
            item[0][0].lower(),
            item[0][0],
            item[0][1],
        ),
    ):
        target = min(range(shard_count), key=lambda index: (loads[index], index))
        stable_rows = sorted(rows, key=lambda row: row.sample_id)
        assignments[target].extend(stable_rows)
        loads[target] += len(stable_rows)
    return tuple(tuple(rows) for rows in assignments)


def _repo_cache_path(cache_dir: Path, repository: str) -> Path:
    if not _REPO_RE.fullmatch(repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        raise ValueError(f"invalid repository slug: {repository!r}")
    return cache_dir / (repository.replace("/", "__") + ".git")


def ensure_bare_cache(cache_dir: Path, repository: str, base_commit: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    bare = _repo_cache_path(cache_dir, repository)
    if not bare.exists():
        _run_git(["clone", "--bare", f"https://github.com/{repository}.git", str(bare)])
    _run_git(["fetch", "origin", base_commit, "--depth=1"], cwd=bare)
    resolved = _run_git(["rev-parse", f"{base_commit}^{{commit}}"], cwd=bare)
    if resolved.lower() != base_commit.lower():
        raise RuntimeError(
            f"base commit unavailable: {repository}@{base_commit} resolved {resolved}"
        )
    return bare


def materialize_worktree(bare: Path, worktree: Path, base_commit: str) -> None:
    if worktree.exists():
        shutil.rmtree(worktree)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["worktree", "add", "--detach", str(worktree), base_commit], cwd=bare)
    actual = _run_git(["rev-parse", "HEAD"], cwd=worktree)
    if actual.lower() != base_commit.lower():
        raise RuntimeError(f"worktree HEAD mismatch: {actual} != {base_commit}")


def run_groups(
    probes: tuple[RetrievalProbe, ...],
    *,
    cache_dir: str | Path,
    work_dir: str | Path,
    state_dir: str | Path,
    output_dir: str | Path,
    shard_index: int = 0,
    shard_count: int = 1,
    dense_model_dir: str | Path | None = None,
    require_dense: bool = False,
) -> int:
    if shard_count < 1 or shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must be in [0, shard_count)")
    cache = Path(cache_dir).resolve()
    work = Path(work_dir).resolve()
    state = Path(state_dir).resolve()
    output = Path(output_dir).resolve()
    dense_backend = _load_dense_backend(
        dense_model_dir,
        require_dense=require_dense,
    )
    count = 0
    assigned = assign_repository_shards(probes, shard_count=shard_count)[shard_index]
    selected_groups = list(group_probes(assigned).items())
    total_groups = len(selected_groups)
    started = time.perf_counter()
    print(
        f"[arb-progress] shard={shard_index} groups={total_groups} rows="
        f"{sum(len(rows) for _, rows in selected_groups)} status=started",
        flush=True,
    )
    for group_number, ((repository, base_commit), rows) in enumerate(selected_groups, 1):
        group_started = time.perf_counter()
        print(
            f"[arb-progress] shard={shard_index} group={group_number}/{total_groups} "
            f"repository={repository} commit={base_commit} rows={len(rows)} status=started",
            flush=True,
        )
        bare = ensure_bare_cache(cache, repository, base_commit)
        slug = repository.replace("/", "__")
        worktree = work / f"{slug}--{base_commit}"
        snapshot_state = state / f"{slug}--{base_commit}"
        snapshot_output = output / f"{slug}--{base_commit}.jsonl"
        materialize_worktree(bare, worktree, base_commit)
        try:
            # All rows in a snapshot group share one certified graph build.
            # Each probe then performs a bounded FTS/structural query against
            # that graph; it never materializes the whole repository corpus.
            # A mixed source revision is not cache-safe.
            source_revisions = {row.source_revision for row in rows}
            prepared_index = None
            if len(source_revisions) == 1:
                shared_revision = next(iter(source_revisions))
                prepared_index = inspect_index(
                    worktree,
                    state_dir=snapshot_state,
                    source_revision=shared_revision,
                )
            snapshot_output.parent.mkdir(parents=True, exist_ok=True)
            with snapshot_output.open("w", encoding="utf-8", newline="\n") as handle:
                for probe in rows:
                    result = run_probe(
                        probe,
                        repo_root=worktree,
                        state_dir=snapshot_state,
                        dense_backend=dense_backend,
                        index_receipt=prepared_index,
                    )
                    handle.write(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
                    handle.write("\n")
                    count += 1
                    print(
                        f"[arb-progress] shard={shard_index} group={group_number}/{total_groups} "
                        f"sample={probe.sample_id} rows_done={count} "
                        f"graph={result.graph_status} selected={len(result.delivered_evidence)} "
                        f"query_ms={result.query_latency_ms:.1f}",
                        flush=True,
                    )
        finally:
            _run_git(["worktree", "remove", "--force", str(worktree)], cwd=bare)
        print(
            f"[arb-progress] shard={shard_index} group={group_number}/{total_groups} "
            f"repository={repository} rows_done={count} "
            f"elapsed_s={time.perf_counter() - group_started:.1f} status=complete",
            flush=True,
        )
    print(
        f"[arb-progress] shard={shard_index} rows_done={count} "
        f"elapsed_s={time.perf_counter() - started:.1f} status=complete",
        flush=True,
    )
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument(
        "--dense-model-dir",
        help="local directory containing pinned Snowflake model.onnx/tokenizer.json",
    )
    parser.add_argument(
        "--require-dense",
        action="store_true",
        help="fail instead of silently evaluating a hybrid configuration without dense",
    )
    args = parser.parse_args()
    probes = load_redacted_samples(args.samples)
    print(
        run_groups(
            probes,
            cache_dir=args.cache_dir,
            work_dir=args.work_dir,
            state_dir=args.state_dir,
            output_dir=args.output_dir,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
            dense_model_dir=args.dense_model_dir,
            require_dense=args.require_dense,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
