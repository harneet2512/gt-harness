"""Deterministic, receipt-bound context packets for HAR-74.

Packets are built from certified rows and may be served at an open/view/edit
boundary only through the existing eligibility router.  The router seals the
delivered bytes before they can enter a model request.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

SCHEMA = "gt.context_packet.v2"
LEGACY_SCHEMA = "gt.context_packet.v1"
BOUNDARIES = frozenset({"open", "view", "edit"})


class ContextPacketError(ValueError):
    """Base error for malformed or unverifiable context packets."""


class ContextPacketAbstention(ContextPacketError):
    """Typed fail-closed result when the packet cannot be certified."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _normalise_claim(claim: Mapping[str, Any], source_revision: str, graph_revision: str) -> dict[str, Any]:
    required = ("claim_id", "kind", "confidence")
    if any(key not in claim for key in required):
        raise ContextPacketError("claim_missing_identity")
    if any(not isinstance(claim[key], str) or not claim[key].strip()
           for key in ("claim_id", "kind")):
        raise ContextPacketError("claim_invalid_identity")
    payload = claim.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise ContextPacketError("claim_missing_payload")
    try:
        payload = json.loads(_canonical(payload))
    except (TypeError, ValueError) as exc:
        raise ContextPacketError("claim_invalid_payload") from exc
    refs = claim.get("evidence_refs")
    if (not isinstance(refs, list) or not refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in refs)):
        raise ContextPacketError("claim_missing_evidence_refs")
    sources = claim.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ContextPacketError("claim_missing_sources")
    source_rows = []
    for source in sources:
        if not isinstance(source, dict):
            raise ContextPacketError("claim_invalid_source")
        path = source.get("path")
        start, end = source.get("start_line"), source.get("end_line")
        digest = source.get("content_sha256")
        if (not isinstance(path, str) or not path or "\\" in path
                or path.startswith("/") or ":" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or type(start) is not int or type(end) is not int
                or start < 1 or end < start
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ContextPacketError("claim_invalid_source")
        source_rows.append({"path": path, "start_line": start, "end_line": end,
                            "content_sha256": digest})
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
        "payload": payload,
        "evidence_refs": sorted(set(refs)),
        "sources": sorted(source_rows, key=lambda item: (item["path"], item["start_line"],
                                                         item["end_line"], item["content_sha256"])),
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
    if type(byte_budget) is not int or byte_budget <= 0:
        raise ContextPacketError("invalid_byte_budget")
    claim_rows = sorted(
        (_normalise_claim(row, source_revision, graph_revision) for row in claims),
        key=lambda row: (-row["confidence"], row["claim_id"]),
    )
    edge_rows = sorted(
        (_normalise_edge(row, source_revision, graph_revision) for row in edges),
        key=lambda row: row["edge_id"],
    )
    claim_ids = {row["claim_id"] for row in claim_rows}
    if len(claim_ids) != len(claim_rows):
        raise ContextPacketError("duplicate_claim_identity")
    if len({row["edge_id"] for row in edge_rows}) != len(edge_rows):
        raise ContextPacketError("duplicate_edge_identity")
    if any(row["from_claim"] not in claim_ids or row["to_claim"] not in claim_ids
           for row in edge_rows):
        raise ContextPacketError("dangling_claim_reference")
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
    identity = {key: body[key] for key in ("schema", "file_path", "boundary", "source_revision",
                                         "graph_revision", "claims", "edges")}
    body["packet_id"] = "ctx-" + _digest(identity)[:24]
    body["packet_digest_sha256"] = _digest(body)
    if len(_canonical(body)) > byte_budget:
        raise ContextPacketAbstention("byte_budget_exceeded")
    return body


def verify_context_packet(packet: Mapping[str, Any], *, allow_legacy: bool = False) -> bool:
    """Validate v2 content structure, not the truth of caller-supplied evidence.

    Historical v1 integrity is opt-in; it never certifies substantive content.
    Dependency/source validation remains the responsibility of the query service.
    """
    if not isinstance(packet, Mapping):
        return False
    try:
        if allow_legacy and packet.get("schema") == LEGACY_SCHEMA:
            return _verify_legacy_context_packet(packet)
        if packet.get("schema") != SCHEMA:
            return False
        expected = build_context_packet(
            source_revision=packet["source_revision"], graph_revision=packet["graph_revision"],
            file_path=packet["file_path"], boundary=packet["boundary"],
            claims=packet["claims"], edges=packet["edges"], byte_budget=packet["byte_budget"],
        )
        return _canonical(expected) == _canonical(packet)
    except (ContextPacketError, KeyError, TypeError, ValueError, AttributeError):
        return False


def _verify_legacy_context_packet(packet: Mapping[str, Any]) -> bool:
    if packet.get("schema") != LEGACY_SCHEMA or packet.get("boundary") not in BOUNDARIES:
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


def serve_context_packet(
    *,
    source_revision: str,
    graph_revision: str,
    file_path: str,
    boundary: str,
    claims: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]] = (),
    byte_budget: int = 8192,
    eligibility_router: Any,
    decision_id: str,
    iteration_id: str,
    baseline_request: Any | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Build and seal one packet through the existing admission boundary.

    Return the router's actual transported request and receipt, never an
    unadmitted packet that a caller could accidentally splice into the request.
    """
    packet = build_context_packet(
        source_revision=source_revision,
        graph_revision=graph_revision,
        file_path=file_path,
        boundary=boundary,
        claims=claims,
        edges=edges,
        byte_budget=byte_budget,
    )
    if not verify_context_packet(packet):
        raise ContextPacketAbstention("packet_verification_failed")
    baseline = baseline_request if baseline_request is not None else {"messages": [{"content": ""}]}
    rendered = _canonical(packet).decode("utf-8")
    transported, receipt = eligibility_router.admit_decision(
        decision_id=decision_id,
        iteration_id=iteration_id,
        candidates=[
            {
                "claim_id": packet["packet_id"],
                "evidence_type": "context_packet",
                "rendered": rendered,
            }
        ],
        baseline_request=baseline,
    )
    return transported, receipt


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
                    {
                        "claim_id": f"{file_path}:fixture", "kind": "fixture", "confidence": 0.0,
                        "payload": {"statement": "Synthetic packet-shape fixture; not a repository fact"},
                        "evidence_refs": [f"fixture:{file_path}"],
                        "sources": [{"path": file_path, "start_line": 1, "end_line": 1,
                                     "content_sha256": hashlib.sha256(b"fixture\n").hexdigest()}],
                    },
                ),
                edges=(),
            )
            fixtures.append(ContextPacketFixture(file_path, boundary, packet))
    return tuple(fixtures)

