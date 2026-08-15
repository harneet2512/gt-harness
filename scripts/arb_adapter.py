#!/usr/bin/env python3
"""Gold-isolated Agent Retrieval Bench adapter for the shared GT retriever.

The adapter is deliberately benchmark-only.  It does not change the central
runtime and it never reads expected files, patches, or evaluator labels while
constructing a retrieval query.  Each redacted JSONL row points at an already
checked-out repository snapshot and is mapped into the same typed state,
bounded repository corpus, hybrid channels, fusion, and selection used by the
runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.hybrid_repository import (
    HybridRepository,
    build_hybrid_repository,
    build_query_hybrid_repository,
)
from gt_engine.hybrid_retrieval import (
    DenseEmbeddingBackend,
    HybridRetrievalResult,
    HybridRetriever,
    RankedFile,
    RetrievalChannel,
    RetrievalIntent,
    RetrievalState,
    build_preemptive_frame,
)
from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
from gt_engine.repository_intelligence import inspect_index
from gt_engine.retrieval_profile import FINAL_RETRIEVAL_PROFILE

ARB_RETRIEVAL_PROFILE = FINAL_RETRIEVAL_PROFILE
# Backward-compatible public constant for existing result compilers/tests.
ARB_DENSE_CANDIDATE_LIMIT = ARB_RETRIEVAL_PROFILE.dense_candidate_limit


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
    task_type: str = ""


@dataclass(frozen=True, slots=True)
class RetrievalProbeResult:
    sample_id: str
    repository: str
    base_commit: str
    task_type: str
    retrieval_intent: str
    ranked_candidates: tuple[dict[str, Any], ...]
    delivered_evidence: tuple[dict[str, Any], ...]
    abstained: bool
    abstention_reason: str | None
    graph_status: str
    graph_revision: str
    source_revision: str
    index_latency_ms: float
    query_latency_ms: float
    index_cache_hit: bool = False
    repository_cache_hit: bool = False
    query_hash: str = ""
    selected_token_count: int = 0
    payload_chars: int = 0
    payload_tokens: int = 0
    channel_receipts: tuple[dict[str, Any], ...] = ()
    dense_backend_receipt: dict[str, Any] | None = None
    retrieval_reason_codes: tuple[str, ...] = ()
    repository_complete: bool = False
    repository_reason_codes: tuple[str, ...] = ()
    repository_document_count: int = 0
    repository_document_chars: int = 0
    repository_structural_link_count: int = 0
    # Index-build diagnostics are part of the retrieval measurement.  A bare
    # ``index_unavailable`` status cannot distinguish a missing executable,
    # parser/coverage failure, invalid SQLite schema, or a build exception,
    # which makes a GitHub run impossible to diagnose or promote.
    index_error_type: str | None = None
    index_error_diagnostic: str = ""
    index_source_files: int = 0
    index_indexable_files: int = 0
    index_schema_valid: bool = False
    index_node_count: int = 0
    index_edge_count: int = 0
    index_binary_sha256: str = ""
    phase_latency_ms: dict[str, float] | None = None


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
        raise RedactedSampleError("gold/fix leakage in redacted sample: " + ", ".join(forbidden))
    sample_id = str(raw.get("sample_id") or raw.get("id") or "").strip()
    repository = str(raw.get("repository") or raw.get("repo") or "").strip()
    base_commit = str(raw.get("base_commit") or "").strip()
    instruction = str(raw.get("instruction") or raw.get("query") or "").strip()
    if not sample_id or not repository or not base_commit or not instruction:
        raise RedactedSampleError(
            "sample_id, repository, base_commit, and instruction/query are required"
        )
    active_paths = _paths(raw.get("active_paths", raw.get("given_files")))
    task_type = str(raw.get("task_type") or "").strip().lower()
    source_revision = str(raw.get("source_revision") or f"arb:{base_commit}")
    return RetrievalProbe(
        sample_id=sample_id,
        repository=repository,
        base_commit=base_commit,
        instruction=instruction,
        task_type=task_type,
        active_paths=active_paths,
        source_revision=source_revision,
    )


def _intent_for_task_type(task_type: str) -> RetrievalIntent:
    """Map ARB's public task family to the production retrieval vocabulary."""

    return {
        "code2test": RetrievalIntent.VALIDATION_CONTEXT,
        "comment2context": RetrievalIntent.MISSING_CONTEXT,
        "trace2code": RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE,
        "edit2ripple": RetrievalIntent.CHANGE_IMPACT,
    }.get(str(task_type or "").strip().lower(), RetrievalIntent.OTHER)


_TRACE_PATH = re.compile(r"(?<![A-Za-z0-9_])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+)(?::\d+)?")
_TRACE_SYMBOL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b")


def _state_for_probe(probe: RetrievalProbe, intent: RetrievalIntent) -> RetrievalState:
    """Compile literal workflow evidence into the production retrieval state."""

    lines = tuple(line.strip() for line in probe.instruction.splitlines() if line.strip())
    instruction_paths = tuple(
        dict.fromkeys(
            match.group(1).replace("\\", "/").lstrip("./")
            for match in _TRACE_PATH.finditer(probe.instruction)
            if ".." not in Path(match.group(1)).parts
        )
    )
    diagnostic_lines = tuple(
        line[:500]
        for line in lines
        if re.search(
            r"(?i)(?:\b(?:error|failed?|exception|undefined|traceback)\b|"
            r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+:\d+)",
            line,
        )
    )[:12]
    symbols = tuple(
        dict.fromkeys(
            match.group(1) for text in diagnostic_lines for match in _TRACE_SYMBOL.finditer(text)
        )
    )[:12]
    proposed_action = next(
        (
            line
            for line in lines[:4]
            if re.match(
                r"^(?:python(?:3)?\s+-m\s+pytest|pytest|go\s+test|cargo\s+test|"
                r"npm\s+test|mvn\s+test|gradle\s+test)\b",
                line,
                re.IGNORECASE,
            )
        ),
        None,
    )
    return RetrievalState(
        task_text=probe.instruction,
        intent=intent,
        proposed_action=proposed_action,
        active_paths=tuple(dict.fromkeys((*probe.active_paths, *instruction_paths))),
        active_symbols=symbols,
        changed_paths=(probe.active_paths if intent is RetrievalIntent.CHANGE_IMPACT else ()),
        diagnostics=diagnostic_lines,
        validation_state=("fail" if intent is RetrievalIntent.DIAGNOSTIC_ROOT_CAUSE else "unknown"),
        source_revision=probe.source_revision,
    )


def _graph_status(index: IndexBuildReceipt | Any) -> str:
    status = getattr(index, "status", None)
    if status is IndexBuildStatus.AVAILABLE or (
        status is None and bool(getattr(index, "graph_db", None))
    ):
        return "source_backed"
    return str(getattr(status, "value", status) or "index_unavailable")


def _dense_receipt_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if after is None:
        return None
    result = dict(after)
    before = before or {}
    for key in (
        "document_cache_hits",
        "document_cache_misses",
        "query_cache_hits",
        "query_cache_misses",
    ):
        if key in after:
            result[f"{key}_delta"] = max(
                0,
                int(after.get(key) or 0) - int(before.get(key) or 0),
            )
    return result


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


def _source_chunk(
    row: dict[str, Any],
    *,
    repo_root: str | Path,
    graph_db: str | None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    """Persist the exact ranked source span used by ARB evaluation.

    GraphEvidence intentionally remains a small runtime receipt.  The ARB
    artifact needs a reproducible source window for line/block/BCY metrics, so
    this benchmark-only layer resolves the indexed node range and reads the
    corresponding base-checkout bytes.  Missing/ambiguous ranges degrade to a
    one-line window; they never fabricate a span.
    """

    path = str(row.get("file_path") or "").replace("\\", "/")
    start = int(row.get("line") or 0)
    end = start
    excerpt = ""
    if graph_db and path:
        try:
            connection = sqlite3.connect(
                f"file:{Path(graph_db).resolve().as_posix()}?mode=ro", uri=True
            )
            try:
                result = connection.execute(
                    "SELECT start_line,end_line,COALESCE(signature,'') "
                    "FROM nodes WHERE file_path=? AND name=? "
                    "ORDER BY start_line,id LIMIT 1",
                    (path, str(row.get("symbol") or "")),
                ).fetchone()
            finally:
                connection.close()
            if result:
                start = int(result[0] or start or 0)
                end = int(result[1] or start)
                excerpt = str(result[2] or "")
        except (OSError, sqlite3.Error, TypeError, ValueError):
            # Source persistence must not make retrieval fail.  The receipt
            # still records a conservative source window below.
            pass

    source_path = (Path(repo_root) / Path(path)).resolve()
    root = Path(repo_root).resolve()
    try:
        source_path.relative_to(root)
    except ValueError:
        source_path = root / Path(path)
    source_text = ""
    try:
        lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if start <= 0:
            start = 1
        if end < start:
            end = start
        source_text = "\n".join(lines[start - 1 : end])
    except OSError:
        source_text = excerpt
    if not source_text:
        source_text = excerpt
    source_text = source_text[:max_chars]
    return {
        "path": path,
        "start_line": start,
        "end_line": max(start, end),
        "text": source_text,
    }


def _attach_source_chunks(
    rows: tuple[dict[str, Any], ...],
    *,
    repo_root: str | Path,
    graph_db: str | None,
) -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    for row in rows:
        enriched = dict(row)
        chunk = _source_chunk(enriched, repo_root=repo_root, graph_db=graph_db)
        enriched["source_span"] = {
            "path": chunk["path"],
            "start_line": chunk["start_line"],
            "end_line": chunk["end_line"],
        }
        enriched["source_text"] = chunk["text"]
        enriched["source_chunk"] = chunk
        output.append(enriched)
    return tuple(output)


def _confidence_fields(ranked: RankedFile) -> tuple[float | None, str]:
    provenance = set(ranked.provenance)
    if "exact_path" in provenance or "exact_symbol" in provenance:
        return 1.0, "mechanically_exact"
    if "structural_certified" in provenance:
        return 1.0, "certified_graph_relation"
    channels = {channel for channel, _rank in ranked.channel_ranks}
    sparse = bool(
        channels
        & {
            RetrievalChannel.EXACT,
            RetrievalChannel.LEXICAL,
            RetrievalChannel.BM25,
        }
    )
    if sparse and RetrievalChannel.DENSE in channels:
        return None, "cross_channel_corroborated_uncalibrated"
    return None, "rank_only_uncalibrated"


def _ranked_row(ranked: RankedFile, *, rank: int) -> dict[str, Any]:
    candidate = ranked.representative
    confidence, confidence_kind = _confidence_fields(ranked)
    source_span = {
        "path": candidate.path,
        "start_line": candidate.start_line,
        "end_line": candidate.end_line,
    }
    return {
        "rank": rank,
        "path": candidate.path,
        "file_path": candidate.path,
        "symbol": candidate.symbol or "",
        "start_line": candidate.start_line,
        "end_line": candidate.end_line,
        "source_span": source_span,
        "source_text": candidate.text,
        "source_chunk": {**source_span, "text": candidate.text},
        "relation": candidate.relation,
        "fused_score": ranked.fused_score,
        "channel_ranks": tuple(
            {"channel": channel.value, "rank": channel_rank}
            for channel, channel_rank in ranked.channel_ranks
        ),
        "representative_channel": candidate.channel.value,
        "representative_channel_score": candidate.channel_score,
        "provenance": ranked.provenance,
        "confidence": confidence,
        "confidence_kind": confidence_kind,
        "source_revision": candidate.source_revision,
        "claim_hash": candidate.claim_hash,
    }


def _hybrid_rows(
    result: HybridRetrievalResult,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    ranked_rows = tuple(
        _ranked_row(ranked, rank=rank) for rank, ranked in enumerate(result.ranked_files, 1)
    )
    rows_by_claim = {str(row["claim_hash"]): row for row in ranked_rows}
    delivered = tuple(
        dict(rows_by_claim[candidate.claim_hash])
        for candidate in result.selected_context
        if candidate.claim_hash in rows_by_claim
    )
    return ranked_rows, delivered


def run_probe(
    probe: RetrievalProbe,
    *,
    repo_root: str | Path,
    state_dir: str | Path,
    dense_backend: DenseEmbeddingBackend | None = None,
    index_receipt: IndexBuildReceipt | None = None,
    prepared_repository: HybridRepository | None = None,
    prepared_retriever: HybridRetriever | None = None,
) -> RetrievalProbeResult:
    """Run the shared hybrid retrieval path for one checked-out snapshot."""

    index_started = time.perf_counter()
    index = index_receipt or inspect_index(
        repo_root,
        state_dir=state_dir,
        source_revision=probe.source_revision,
    )
    index_finished = time.perf_counter()
    query_started = index_finished
    phases: dict[str, float] = {}

    phase_started = time.perf_counter()
    intent = _intent_for_task_type(probe.task_type)
    state = _state_for_probe(probe, intent)
    phases["state_build_ms"] = (time.perf_counter() - phase_started) * 1000.0

    phase_started = time.perf_counter()
    graph_db = getattr(index, "graph_db", None)
    if prepared_repository is not None:
        repository = prepared_repository
    elif graph_db:
        repository = build_query_hybrid_repository(
            repo_root,
            graph_db,
            state,
            candidate_limit=128,
        )
    else:
        repository = build_hybrid_repository(
            repo_root,
            Path(state_dir) / "graph.unavailable",
            source_revision=probe.source_revision,
        )
    phases["repository_prepare_ms"] = (time.perf_counter() - phase_started) * 1000.0

    phase_started = time.perf_counter()
    dense_receipt = getattr(dense_backend, "receipt", None)
    dense_receipt_before = dict(dense_receipt()) if callable(dense_receipt) else None
    retriever = prepared_retriever or HybridRetriever(
        repository.documents,
        structural_links=repository.structural_links,
        dense_backend=dense_backend,
        dense_candidate_limit=ARB_RETRIEVAL_PROFILE.dense_candidate_limit,
    )
    retrieval = retriever.retrieve(
        state,
        channel_limit=ARB_RETRIEVAL_PROFILE.channel_limit,
        top_k=ARB_RETRIEVAL_PROFILE.top_k,
        selection_limit=ARB_RETRIEVAL_PROFILE.selection_limit,
        token_budget=ARB_RETRIEVAL_PROFILE.token_budget,
    )
    phases["retrieval_ms"] = (time.perf_counter() - phase_started) * 1000.0
    dense_receipt_after = dict(dense_receipt()) if callable(dense_receipt) else None

    phase_started = time.perf_counter()
    ranked_rows, delivered = _hybrid_rows(retrieval)
    preemptive_frame = build_preemptive_frame(
        retrieval,
        state,
        trigger=f"arb_{probe.task_type or 'retrieval'}",
    )
    phases["frame_pack_ms"] = (time.perf_counter() - phase_started) * 1000.0
    if delivered:
        abstention_reason = None
    elif _graph_status(index) != "source_backed":
        abstention_reason = _graph_status(index)
    else:
        abstention_reason = (
            ",".join((*retrieval.reason_codes, *repository.reason_codes)) or "no_retrieval_evidence"
        )
    query_latency_ms = (time.perf_counter() - query_started) * 1000.0
    phases["receipt_compile_ms"] = max(0.0, query_latency_ms - sum(phases.values()))
    return RetrievalProbeResult(
        sample_id=probe.sample_id,
        repository=probe.repository,
        base_commit=probe.base_commit,
        task_type=probe.task_type,
        retrieval_intent=intent.value,
        ranked_candidates=ranked_rows,
        delivered_evidence=delivered,
        abstained=not bool(delivered),
        abstention_reason=abstention_reason,
        graph_status=_graph_status(index),
        graph_revision=str(getattr(index, "graph_revision", "")),
        source_revision=probe.source_revision,
        index_latency_ms=round(
            0.0 if index_receipt is not None else (index_finished - index_started) * 1000.0,
            6,
        ),
        query_latency_ms=round(query_latency_ms, 6),
        index_cache_hit=index_receipt is not None,
        repository_cache_hit=prepared_repository is not None,
        query_hash=retrieval.query_hash,
        selected_token_count=retrieval.selected_token_count,
        payload_chars=(len(preemptive_frame.rendered_text) if preemptive_frame is not None else 0),
        payload_tokens=retrieval.selected_token_count,
        channel_receipts=tuple(
            {
                **asdict(receipt),
                "channel": receipt.channel.value,
            }
            for receipt in retrieval.channel_receipts
        ),
        dense_backend_receipt=_dense_receipt_delta(
            dense_receipt_before,
            dense_receipt_after,
        ),
        retrieval_reason_codes=retrieval.reason_codes,
        repository_complete=repository.complete,
        repository_reason_codes=repository.reason_codes,
        repository_document_count=len(repository.documents),
        repository_document_chars=repository.document_chars,
        repository_structural_link_count=len(repository.structural_links),
        index_error_type=(str(index.error_type) if getattr(index, "error_type", None) else None),
        index_error_diagnostic=str(getattr(index, "error_diagnostic", "")),
        index_source_files=int(getattr(index, "source_files", 0)),
        index_indexable_files=int(getattr(index, "indexable_files", 0)),
        index_schema_valid=bool(getattr(index, "schema_valid", False)),
        index_node_count=int(getattr(index, "node_count", 0)),
        index_edge_count=int(getattr(index, "edge_count", 0)),
        index_binary_sha256=str(getattr(index, "binary_sha256", "")),
        phase_latency_ms={key: round(value, 6) for key, value in phases.items()},
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
