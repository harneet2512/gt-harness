#!/usr/bin/env python3
"""Run the GT ARB adapter against lossless exact-base Git worktrees."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from scripts.arb_adapter import RetrievalProbe, load_redacted_samples, run_probe

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


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


def _repo_cache_path(cache_dir: Path, repository: str) -> Path:
    if (
        not _REPO_RE.fullmatch(repository)
        or any(part in {".", ".."} for part in repository.split("/"))
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
) -> int:
    if shard_count < 1 or shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must be in [0, shard_count)")
    cache = Path(cache_dir).resolve()
    work = Path(work_dir).resolve()
    state = Path(state_dir).resolve()
    output = Path(output_dir).resolve()
    count = 0
    groups = list(group_probes(probes).items())
    selected_groups = groups[shard_index::shard_count]
    for (repository, base_commit), rows in selected_groups:
        bare = ensure_bare_cache(cache, repository, base_commit)
        slug = repository.replace("/", "__")
        worktree = work / f"{slug}--{base_commit}"
        snapshot_state = state / f"{slug}--{base_commit}"
        snapshot_output = output / f"{slug}--{base_commit}.jsonl"
        materialize_worktree(bare, worktree, base_commit)
        try:
            snapshot_output.parent.mkdir(parents=True, exist_ok=True)
            with snapshot_output.open("w", encoding="utf-8", newline="\n") as handle:
                for probe in rows:
                    result = run_probe(probe, repo_root=worktree, state_dir=snapshot_state)
                    handle.write(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
                    handle.write("\n")
                    count += 1
        finally:
            _run_git(["worktree", "remove", "--force", str(worktree)], cwd=bare)
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
    args = parser.parse_args()
    probes = load_redacted_samples(args.samples)
    print(run_groups(
        probes,
        cache_dir=args.cache_dir,
        work_dir=args.work_dir,
        state_dir=args.state_dir,
        output_dir=args.output_dir,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
