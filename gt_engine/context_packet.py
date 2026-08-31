"""Deterministic, receipt-bound context packets for HAR-74 design prep.

This module deliberately stops at the fixture/builder boundary.  Serving and
eligibility wiring are deferred until the HAR-69 index-reuse boundary lands.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

SCHEMA = "gt.context_packet.v1"
BOUNDARIES = frozenset({"open", "view", "edit"})


class ContextPacketError(ValueError):
    """Base error for malformed or unverifiable context packets."""


class ContextPacketAbstention(ContextPacketError):
    """Typed fail-closed result when the packet cannot be certified."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _normalise_claim(claim: Mapping[str, Any], source_revision: str, graph_revision: str) -> dict[str, Any]:
    required = ("claim_id", "kind", "confidence")
    if any(key not in claim for key in required):
        raise ContextPacketError("claim_missing_identity")
    confidence = claim["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ContextPacketError("claim_confidence_not_numeric")
    if not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
        raise ContextPacketError("claim_confidence_out_of_range")
    row = {
        "claim_id": str(claim["claim_id"]),
        "kind": str(claim["kind"]),
        "confidence": float(confidence),
        "source_revision": str(claim.get("source_revision", source_revision)),
        "graph_revision": str(claim.get("graph_revision", graph_revision)),
    }
    if "digest" in claim:
        row["digest"] = str(claim["digest"])
    return row


def _normalise_edge(edge: Mapping[str, Any], source_revision: str, graph_revision: str) -> dict[str, Any]:
    required = ("edge_id", "kind", "from_claim", "to_claim")
    if any(key not in edge for key in required):
        raise ContextPacketError("edge_missing_identity")
    return {
        "edge_id": str(edge["edge_id"]),
        "kind": str(edge["kind"]),
        "from_claim": str(edge["from_claim"]),
        "to_claim": str(edge["to_claim"]),
        "source_revision": str(edge.get("source_revision", source_revision)),
        "graph_revision": str(edge.get("graph_revision", graph_revision)),
    }


def build_context_packet(
    *,
    source_revision: str,
    graph_revision: str,
    file_path: str,
    boundary: str,
    claims: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]] = (),
    byte_budget: int = 8192,
) -> dict[str, Any]:
    """Build a deterministic packet from already-certified fixture rows."""
    if boundary not in BOUNDARIES:
        raise ContextPacketAbstention("unsupported_boundary")
    if not source_revision or not graph_revision or not file_path:
        raise ContextPacketAbstention("missing_freshness_identity")
    if not isinstance(byte_budget, int) or byte_budget <= 0:
        raise ContextPacketError("invalid_byte_budget")
    claim_rows = sorted(
        (_normalise_claim(row, source_revision, graph_revision) for row in claims),
        key=lambda row: (-row["confidence"], row["claim_id"]),
    )
    edge_rows = sorted(
        (_normalise_edge(row, source_revision, graph_revision) for row in edges),
        key=lambda row: row["edge_id"],
    )
    if any(row["source_revision"] != source_revision or row["graph_revision"] != graph_revision for row in (*claim_rows, *edge_rows)):
        raise ContextPacketAbstention("stale_graph_revision")
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "packet_id": "",
        "file_path": file_path,
        "boundary": boundary,
        "source_revision": source_revision,
        "graph_revision": graph_revision,
        "claims": claim_rows,
        "edges": edge_rows,
        "byte_budget": byte_budget,
    }
    identity = {key: body[key] for key in ("schema", "file_path", "boundary", "source_revision", "graph_revision")}
    body["packet_id"] = "ctx-" + _digest(identity)[:24]
    if len(_canonical(body)) > byte_budget:
        raise ContextPacketAbstention("byte_budget_exceeded")
    body["packet_digest_sha256"] = _digest(body)
    return body


def verify_context_packet(packet: Mapping[str, Any]) -> bool:
    """Verify schema, identity, ordering, freshness, and canonical digest."""
    if packet.get("schema") != SCHEMA or packet.get("boundary") not in BOUNDARIES:
        return False
    digest = packet.get("packet_digest_sha256")
    if not isinstance(digest, str):
        return False
    unsigned = {key: value for key, value in packet.items() if key != "packet_digest_sha256"}
    if _digest(unsigned) != digest:
        return False
    expected_id = "ctx-" + _digest({key: unsigned[key] for key in ("schema", "file_path", "boundary", "source_revision", "graph_revision")})[:24]
    if unsigned.get("packet_id") != expected_id:
        return False
    claims = unsigned.get("claims", [])
    edges = unsigned.get("edges", [])
    if list(claims) != sorted(claims, key=lambda row: (-row.get("confidence", -1), row.get("claim_id", ""))):
        return False
    if list(edges) != sorted(edges, key=lambda row: row.get("edge_id", "")):
        return False
    source_revision = unsigned.get("source_revision")
    graph_revision = unsigned.get("graph_revision")
    return all(
        row.get("source_revision") == source_revision and row.get("graph_revision") == graph_revision
        for row in (*claims, *edges)
    )


@dataclass(frozen=True)
class ContextPacketFixture:
    file_path: str
    boundary: str
    packet: dict[str, Any]


def build_fixture_matrix(*, source_revision: str, graph_revision: str) -> tuple[ContextPacketFixture, ...]:
    """Return the 3-file x 3-boundary deterministic design fixture."""
    files = ("src/alpha.py", "src/beta.py", "tests/test_gamma.py")
    fixtures: list[ContextPacketFixture] = []
    for file_path in files:
        for boundary in ("open", "view", "edit"):
            packet = build_context_packet(
                source_revision=source_revision,
                graph_revision=graph_revision,
                file_path=file_path,
                boundary=boundary,
                claims=(
                    {"claim_id": f"{file_path}:definition", "kind": "definition", "confidence": 0.9},
                    {"claim_id": f"{file_path}:obligation", "kind": "obligation", "confidence": 0.8},
                ),
                edges=(),
            )
            fixtures.append(ContextPacketFixture(file_path, boundary, packet))
    return tuple(fixtures)

