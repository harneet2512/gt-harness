"""Frozen retrieval settings shared by ARB and the live Mini-SWE runtime.

The profile is deliberately data-only.  Benchmark and agent adapters may map
their inputs into the shared retriever, but may not silently select different
ranking, packing, or dense-candidate limits.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalProfile:
    schema: str
    channel_limit: int
    top_k: int
    selection_limit: int
    token_budget: int
    task_budget_chars: int
    dense_candidate_limit: int
    cold_start_timeout_sec: float
    steady_state_timeout_sec: float


FINAL_RETRIEVAL_PROFILE = RetrievalProfile(
    schema="gt.retrieval_profile.final.v1",
    channel_limit=100,
    top_k=20,
    selection_limit=8,
    token_budget=1_200,
    task_budget_chars=12_000,
    dense_candidate_limit=32,
    # The accepted ARB run measured query p99 ~= 23.1 seconds on a cold
    # 32-span Snowflake ONNX pool.  Thirty seconds is measurement-backed
    # startup headroom, not the steady-state budget.
    cold_start_timeout_sec=30.0,
    # Once passage embeddings are cached, live retrieval remains bounded and
    # fail-open.  A timeout never blocks the model loop or serves partial data.
    steady_state_timeout_sec=2.0,
)


__all__ = ["FINAL_RETRIEVAL_PROFILE", "RetrievalProfile"]
