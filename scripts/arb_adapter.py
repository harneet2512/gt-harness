#!/usr/bin/env python3
"""Gold-isolated Agent Retrieval Bench adapter for the active GT ranker.

The adapter is deliberately benchmark-only.  It does not change the central
runtime and it never reads expected files, patches, or evaluator labels while
constructing a retrieval query.  Each redacted JSONL row points at an already
checked-out repository snapshot and is passed through the same task contract,
graph projection, and graph-evidence ranking functions used by the runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.graph_context import build_graph_projection
from gt_engine.graph_evidence import build_evidence_need, rank_graph_evidence
from gt_engine.repository_intelligence import inspect_repository
from gt_engine.task_contract import extract_task_contract


class RedactedSampleError(ValueError):
    """Raised when the benchmark input could leak evaluator information."""


# These keys are never valid query state.  Gold data must be joined after the
# adapter finishes, not carried through the runner under a different name.
_FORBIDDEN_KEYS = frozenset(
    {
        "gold",
        "gold_files",
        "expected",
        "expected_files",
        "patch",
        "gold_patch",
        "fix",
        "fix_files",
        "target_files",
        "evaluator",
        "labels",
    }
)


@dataclass(frozen=True, slots=True)
class RetrievalProbe:
    sample_id: str
    repository: str
    base_commit: str
    instruction: str
    active_paths: tuple[str, ...]
    source_revision: str


@dataclass(frozen=True, slots=True)
class RetrievalProbeResult:
    sample_id: str
    repository: str
    base_commit: str
    ranked_candidates: tuple[dict[str, Any], ...]
    delivered_evidence: tuple[dict[str, Any], ...]
    abstained: bool
    abstention_reason: str | None
    graph_status: str
    graph_revision: str
    source_revision: str
    index_latency_ms: float
    query_latency_ms: float


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def _paths(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise RedactedSampleError("given_files/active_paths must be a list")
    result: list[str] = []
    for raw in value:
        path = str(raw or "").replace("\\", "/").strip()
        if path and path not in result:
            result.append(path)
    return tuple(result)


def normalize_sample(raw: dict[str, Any]) -> RetrievalProbe:
    """Validate and normalize one gold-free ARB input row."""

    forbidden = sorted(_FORBIDDEN_KEYS & _walk_keys(raw))
    if forbidden:
        raise RedactedSampleError(
            "gold/fix leakage in redacted sample: " + ", ".join(forbidden)
        )
    sample_id = str(raw.get("sample_id") or raw.get("id") or "").strip()
    repository = str(raw.get("repository") or raw.get("repo") or "").strip()
    base_commit = str(raw.get("base_commit") or "").strip()
    instruction = str(raw.get("instruction") or raw.get("query") or "").strip()
    if not sample_id or not repository or not base_commit or not instruction:
        raise RedactedSampleError(
            "sample_id, repository, base_commit, and instruction/query are required"
        )
    active_paths = _paths(raw.get("active_paths", raw.get("given_files")))
    source_revision = str(raw.get("source_revision") or f"arb:{base_commit}")
    return RetrievalProbe(
        sample_id=sample_id,
        repository=repository,
        base_commit=base_commit,
        instruction=instruction,
        active_paths=active_paths,
        source_revision=source_revision,
    )


def load_redacted_samples(path: str | Path) -> tuple[RetrievalProbe, ...]:
    rows: list[RetrievalProbe] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RedactedSampleError(f"invalid JSON on line {line_number}") from exc
        if not isinstance(raw, dict):
            raise RedactedSampleError(f"line {line_number} is not an object")
        rows.append(normalize_sample(raw))
    return tuple(rows)


def _evidence_row(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_receipt"):
        return dict(item.to_receipt())
    return asdict(item)


def run_probe(
    probe: RetrievalProbe,
    *,
    repo_root: str | Path,
    state_dir: str | Path,
) -> RetrievalProbeResult:
    """Run the production graph retrieval path for one checked-out snapshot."""

    started = time.perf_counter()
    evidence = inspect_repository(
        repo_root,
        probe.instruction,
        state_dir=state_dir,
        limit=12,
        source_revision=probe.source_revision,
        active_paths=probe.active_paths,
        boundary="arb_retrieval",
    )
    indexed_at = time.perf_counter()
    ranked: tuple[Any, ...] = ()
    if evidence.index is not None and evidence.index.graph_db and evidence.substrate_ready:
        contract = extract_task_contract(probe.instruction)
        projection = build_graph_projection(
            evidence.index.graph_db,
            contract,
            limit=24,
            active_paths=probe.active_paths,
        )
        need = build_evidence_need(
            contract,
            projection,
            boundary="arb_retrieval",
            active_paths=probe.active_paths,
        )
        ranked = rank_graph_evidence(contract, projection, need, limit=12)
    ranked_rows = tuple(_evidence_row(item) for item in ranked)
    # This is the product's bounded selected set, not a gold-aware top-k.
    delivered = tuple(
        row
        for row in ranked_rows
        if float(row.get("confidence") or 0.0) >= 0.95
        and float(row.get("retrieval_relevance") or 0.0) >= 0.95
    )[:3]
    if delivered:
        abstention_reason = None
    elif evidence.status != "source_backed":
        abstention_reason = str(evidence.status)
    else:
        abstention_reason = "no_certified_ranked_evidence"
    return RetrievalProbeResult(
        sample_id=probe.sample_id,
        repository=probe.repository,
        base_commit=probe.base_commit,
        ranked_candidates=ranked_rows,
        delivered_evidence=delivered,
        abstained=not bool(delivered),
        abstention_reason=abstention_reason,
        graph_status=str(evidence.status),
        graph_revision=str(evidence.graph_revision),
        source_revision=probe.source_revision,
        index_latency_ms=round(
            float(evidence.index.elapsed_ms)
            if evidence.index is not None
            else (indexed_at - started) * 1000.0,
            6,
        ),
        query_latency_ms=round((time.perf_counter() - indexed_at) * 1000.0, 6),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, help="gold-free JSONL input")
    parser.add_argument("--repo-root", required=True, help="checked-out ARB repository snapshot")
    parser.add_argument("--state-dir", required=True, help="private index state directory")
    parser.add_argument("--output", required=True, help="JSONL predictions output")
    args = parser.parse_args(argv)
    probes = load_redacted_samples(args.samples)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for probe in probes:
            result = run_probe(probe, repo_root=args.repo_root, state_dir=args.state_dir)
            handle.write(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
            handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
