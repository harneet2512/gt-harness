"""Measure whether a certified graph actually reached the model.

``treatment_graph_certification_invalid`` proves a graph was built. It does not
prove the graph was used. Run 33708231670 delivered five evidence types across
160 provider calls -- ``new_file_destination``, ``context_delta``,
``trace_frame``, ``missing_role_postcreate:*`` and ``context_contract`` -- and
every one of them is producible without a graph. For graph-derived capability
that run was indistinguishable from GT-off, at full cost.

A certified-but-unused graph is therefore a distinct and currently invisible
condition, and it is the one that quietly turns a paid treatment run into a
measurement of nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from gt_engine.attribution import feature_for_evidence

__all__ = [
    "GRAPH_BACKED_FEATURES",
    "graph_utilisation",
]

# Features that cannot be produced without the graph tables: caller contracts
# come from call edges, co-change priors from the cochange tables, definition
# partitions from indexed symbols, and signature deltas from stored signatures.
#
# `localization` is deliberately excluded: `trace_frame` maps to it and is
# runtime-derived, so its presence would not evidence graph use.
GRAPH_BACKED_FEATURES: frozenset[str] = frozenset(
    {
        "caller_contract",
        "cochange_prior",
        "def_partition",
        "signature_delta",
    }
)


def graph_utilisation(deliveries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarise which delivered evidence could only have come from the graph."""

    delivered_features: set[str] = set()
    graph_backed: set[str] = set()
    for row in deliveries:
        evidence_type = str(row.get("evidence_type") or row.get("kind") or "")
        feature = feature_for_evidence(evidence_type)
        if not feature:
            continue
        delivered_features.add(feature)
        if feature in GRAPH_BACKED_FEATURES:
            graph_backed.add(feature)

    return {
        "schema": "gt.graph_utilisation.v1",
        "delivered_features": sorted(delivered_features),
        "graph_backed_features": sorted(graph_backed),
        "graph_backed_delivery": bool(graph_backed),
    }
