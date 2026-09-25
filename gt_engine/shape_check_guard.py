"""Harness-side guard for the producer's cross-language interface defect.

The producer's ``shape_check`` emits one ``interface_conformance`` row per
``IMPLEMENTS``/``DECLARED_IMPLEMENTS`` edge, but resolves those edges' targets
by bare interface name — so a TypeScript ``class C implements Greeter`` can
absorb an unrelated same-named Go ``Greeter`` and fail its contract.

This module replays the producer's exact edge query on the same graph,
attributes each conformance row to its edge (in emitted order), and drops
rows whose interface node's ``language`` differs from the subject class's.
Filtered verdicts are declared via the ``cross_language_interface_filtered``
omission; when row-to-edge alignment cannot be proven the answer is left
untouched and ``cross_language_interface_unattributed`` is named instead.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

_IMPLEMENTS_SQL = (
    "SELECT e.target_id, n.name, n.language FROM edges e"
    " JOIN nodes n ON n.id = e.target_id"
    " WHERE e.source_id = ?"
    " AND e.type IN ('IMPLEMENTS','DECLARED_IMPLEMENTS')"
)


def _subject_nodes(
    conn: sqlite3.Connection, arguments: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Resolve the class nodes exactly as the producer did."""
    from groundtruth.runtime.deterministic_queries import _resolve_symbol_nodes

    return _resolve_symbol_nodes(
        conn,
        arguments.get("symbol"),
        arguments.get("path"),
        arguments.get("language"),
        [],
    )


def filter_cross_language_interfaces(
    conn: sqlite3.Connection,
    arguments: Mapping[str, Any],
    answer: dict[str, Any],
) -> list[str]:
    """Drop cross-language conformance verdicts from ``answer`` in place.

    Returns the omissions this pass adds (empty when nothing was filtered).
    """
    checks = answer.get("checks")
    if not isinstance(checks, list):
        return []
    nodes = _subject_nodes(conn, arguments)
    if not nodes:
        return []
    # Per-node edge rows in the order the producer emitted verdict rows:
    # node resolution order, then the edge query's row order per class.
    expected: list[tuple[dict[str, Any], str, str]] = []
    for node in nodes[:5]:
        if node["kind"] != "Class":
            continue
        for target_id, name, language in conn.execute(
            _IMPLEMENTS_SQL, (node["id"],)
        ):
            expected.append((node, str(name or ""), str(language or "")))
    conformance = [
        (i, c)
        for i, c in enumerate(checks)
        if isinstance(c, Mapping) and c.get("check") == "interface_conformance"
    ]
    # One conformance row per edge — if counts or names disagree, row-to-edge
    # attribution is unproven: keep everything and say so.
    if len(conformance) != len(expected) or any(
        check.get("interface") != iface_name
        for (_, check), (_, iface_name, _) in zip(conformance, expected)
    ):
        return ["cross_language_interface_unattributed"]
    drop: set[int] = set()
    for (idx, _), (node, _, iface_lang) in zip(conformance, expected):
        class_lang = str(node.get("language") or "").lower()
        # Only a verdict provably measured against a different-language
        # interface is dropped; unknown languages keep the row.
        if class_lang and iface_lang and iface_lang.lower() != class_lang:
            drop.add(idx)
    if not drop:
        return []
    answer["checks"] = [c for i, c in enumerate(checks) if i not in drop]
    answer["check_count"] = len(answer["checks"])
    answer["failed"] = sum(
        1
        for c in answer["checks"]
        if isinstance(c, Mapping) and c.get("status") == "fail"
    )
    return ["cross_language_interface_filtered"]
