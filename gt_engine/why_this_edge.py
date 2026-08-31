"""Certified producer facts for the ``why-this-edge`` query.

The producer owns these facts; this harness module only validates the wire
shape and provides a private compatibility consumer for older wheels.  A
failed or incomplete certification is a typed abstention, never a shell
fallback or an inferred answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "gt.why_this_edge.v1"
ALLOWED_EDGE_KINDS = {"HAS_CALLSITE", "CANDIDATE_TARGET", "SELECTED_TARGET"}
SUBSTRATE_SCHEMA = "gt.resolution_substrate.v1"


class WhyThisEdgeAbstention(ValueError):
    """Typed, conservative refusal to certify an edge explanation."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _require_text(facts: Mapping[str, Any], field: str) -> str:
    value = facts.get(field)
    if not isinstance(value, str) or not value.strip():
        raise WhyThisEdgeAbstention(f"{field}_identity")
    return value.strip()


def _normalise_candidates(
    facts: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    raw = facts.get("candidates")
    if not isinstance(raw, list):
        raise WhyThisEdgeAbstention("candidate_facts_missing")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in raw:
        if not isinstance(candidate, Mapping):
            raise WhyThisEdgeAbstention("candidate_facts_invalid")
        target_id = candidate.get("target_id")
        if not isinstance(target_id, str) or not target_id.strip():
            raise WhyThisEdgeAbstention("candidate_target_identity")
        target_id = target_id.strip()
        if target_id in seen:
            raise WhyThisEdgeAbstention("candidate_duplicate")
        seen.add(target_id)
        witnesses = candidate.get("flow_witnesses", [])
        if not isinstance(witnesses, list) or any(
            not isinstance(witness, str) or not witness for witness in witnesses
        ):
            raise WhyThisEdgeAbstention("witness_conservation")
        if len(witnesses) != len(set(witnesses)):
            raise WhyThisEdgeAbstention("witness_conservation")
        candidates.append(
            {"target_id": target_id, "flow_witnesses": sorted(witnesses)}
        )
    candidates.sort(key=lambda item: item["target_id"])
    witness_map = facts.get("flow_witnesses")
    if not isinstance(witness_map, Mapping):
        raise WhyThisEdgeAbstention("witness_conservation")
    normalised_map: dict[str, list[str]] = {}
    for target_id, witnesses in witness_map.items():
        if not isinstance(target_id, str) or not isinstance(witnesses, list):
            raise WhyThisEdgeAbstention("witness_conservation")
        if any(not isinstance(witness, str) or not witness for witness in witnesses):
            raise WhyThisEdgeAbstention("witness_conservation")
        if len(witnesses) != len(set(witnesses)):
            raise WhyThisEdgeAbstention("witness_conservation")
        normalised_map[target_id] = sorted(witnesses)
    expected_map = {item["target_id"]: item["flow_witnesses"] for item in candidates}
    if normalised_map != expected_map:
        raise WhyThisEdgeAbstention("witness_conservation")
    return candidates, dict(sorted(normalised_map.items()))


def _validate_common(
    facts: Mapping[str, Any],
    *,
    expected_callsite_id: str | None = None,
    expected_target_id: str | None = None,
    expected_source_revision: str | None = None,
    expected_graph_revision: str | None = None,
) -> dict[str, Any]:
    edge_kind = str(facts.get("edge_kind") or "").strip().upper()
    if edge_kind not in ALLOWED_EDGE_KINDS:
        raise WhyThisEdgeAbstention("unsupported_edge_kind")
    edge_id = _require_text(facts, "edge_id")
    callsite_id = _require_text(facts, "callsite_id")
    target_id = _require_text(facts, "target_id")
    source_revision = _require_text(facts, "source_revision")
    graph_revision = _require_text(facts, "graph_revision")
    completion_identity = _require_text(facts, "completion_identity")
    if facts.get("complete") is not True:
        raise WhyThisEdgeAbstention("incomplete_build")
    if expected_callsite_id is not None and callsite_id != expected_callsite_id:
        raise WhyThisEdgeAbstention("target_identity_callsite")
    if expected_target_id is not None and target_id != expected_target_id:
        raise WhyThisEdgeAbstention("target_identity")
    if expected_source_revision is not None and source_revision != expected_source_revision:
        raise WhyThisEdgeAbstention("stale_source_revision")
    if expected_graph_revision is not None and graph_revision != expected_graph_revision:
        raise WhyThisEdgeAbstention("stale_graph_revision")
    candidates, witnesses = _normalise_candidates(facts)
    try:
        candidate_count = int(facts.get("candidate_count"))
    except (TypeError, ValueError):
        raise WhyThisEdgeAbstention("candidate_count_invalid") from None
    if candidate_count != len(candidates) or candidate_count < 0:
        raise WhyThisEdgeAbstention("candidate_count_conservation")
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "edge_id": edge_id,
        "callsite_id": callsite_id,
        "edge_kind": edge_kind,
        "target_id": target_id,
        "dispatch_state": str(facts.get("dispatch_state") or "unknown"),
        "candidate_count": candidate_count,
        "candidates": candidates,
        "flow_witnesses": witnesses,
        "source_revision": source_revision,
        "graph_revision": graph_revision,
        "completion_identity": completion_identity,
        "complete": True,
    }
    return payload


def certify_why_this_edge(facts: Mapping[str, Any], **expected: str | None) -> dict[str, Any]:
    """Validate producer facts and add a canonical content digest."""
    payload = _validate_common(facts, **expected)
    supplied = facts.get("digest_sha256")
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if supplied is not None and supplied != digest:
        raise WhyThisEdgeAbstention("digest_mismatch")
    payload["digest_sha256"] = digest
    return payload


def query_why_this_edge(
    facts: Mapping[str, Any],
    *,
    expected_callsite_id: str | None = None,
    expected_target_id: str | None = None,
    expected_source_revision: str | None = None,
    expected_graph_revision: str | None = None,
) -> dict[str, Any]:
    """Private compatibility consumer for a producer-supplied record."""
    return certify_why_this_edge(
        facts,
        expected_callsite_id=expected_callsite_id,
        expected_target_id=expected_target_id,
        expected_source_revision=expected_source_revision,
        expected_graph_revision=expected_graph_revision,
    )


def verify_why_this_edge(result: Mapping[str, Any]) -> bool:
    try:
        if result.get("schema") != SCHEMA:
            return False
        supplied = result.get("digest_sha256")
        if not isinstance(supplied, str):
            return False
        body = dict(result)
        body.pop("digest_sha256", None)
        if hashlib.sha256(_canonical_bytes(body)).hexdigest() != supplied:
            return False
        return certify_why_this_edge(body) == dict(result)
    except (KeyError, TypeError, ValueError):
        return False


def why_this_edge_receipt(result: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the certified kind and explanation digest into the receipt chain."""
    certified = certify_why_this_edge(result)
    return {
        "schema": "gt.why_this_edge_receipt.v1",
        "kind": "why_this_edge",
        "explanation_schema": SCHEMA,
        "explanation_digest_sha256": certified["digest_sha256"],
        "edge_id": certified["edge_id"],
        "callsite_id": certified["callsite_id"],
        "candidate_count": certified["candidate_count"],
    }


def harvest_resolution_substrate(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Load producer-emitted candidate/witness rows from a shipped substrate."""
    path = Path(root) / "gt_finalstand" / "receipts" / "har63_resolution_substrate.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WhyThisEdgeAbstention("resolution_substrate_unavailable") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != SUBSTRATE_SCHEMA:
        raise WhyThisEdgeAbstention("resolution_substrate_invalid")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise WhyThisEdgeAbstention("resolution_substrate_empty")
    result = [dict(row) for row in rows if isinstance(row, Mapping)]
    if len(result) != len(rows):
        raise WhyThisEdgeAbstention("resolution_substrate_invalid")
    supplied = payload.get("rows_digest_sha256")
    if supplied is not None:
        digest = hashlib.sha256(
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if supplied != digest:
            raise WhyThisEdgeAbstention("resolution_substrate_digest_mismatch")
    return result


class WhyThisEdgeStore:
    """Producer-owned content-addressed store for certified edge facts."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def publish(self, facts: Mapping[str, Any]) -> dict[str, Any]:
        certified = certify_why_this_edge(facts)
        records = self._load()
        records[certified["edge_id"]] = certified
        receipt = why_this_edge_receipt(certified)
        payload = {
            "schema": "gt.why_this_edge_store.v1",
            "records": [records[key] for key in sorted(records)],
            "receipt": receipt,
        }
        payload["store_digest_sha256"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=self.path.parent, delete=False
        ) as handle:
            temporary = handle.name
            handle.write(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
        os.replace(temporary, self.path)
        return receipt

    def publish_substrate(self, rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Certify and atomically publish every row harvested from the substrate."""
        if not isinstance(rows, list) or not rows:
            raise WhyThisEdgeAbstention("resolution_substrate_empty")
        records = self._load()
        receipts: list[dict[str, Any]] = []
        for facts in rows:
            certified = certify_why_this_edge(facts)
            records[certified["edge_id"]] = certified
            receipts.append(why_this_edge_receipt(certified))
        payload = {
            "schema": "gt.why_this_edge_store.v1",
            "records": [records[key] for key in sorted(records)],
            "receipts": receipts,
            "receipt": receipts[-1],
        }
        payload["store_digest_sha256"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=self.path.parent, delete=False
        ) as handle:
            temporary = handle.name
            handle.write(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
        os.replace(temporary, self.path)
        return receipts

    def query(self, edge_id: str, **expected: str | None) -> dict[str, Any]:
        records = self._load()
        try:
            facts = records[edge_id]
        except KeyError:
            raise WhyThisEdgeAbstention("edge_not_found") from None
        return query_why_this_edge(facts, **expected)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        records = payload.get("records") if isinstance(payload, Mapping) else None
        if not isinstance(records, list):
            raise WhyThisEdgeAbstention("producer_store_invalid")
        return {str(item["edge_id"]): dict(item) for item in records if isinstance(item, Mapping)}


why_this_edge = query_why_this_edge

__all__ = [
    "ALLOWED_EDGE_KINDS",
    "SCHEMA",
    "WhyThisEdgeAbstention",
    "certify_why_this_edge",
    "query_why_this_edge",
    "verify_why_this_edge",
    "why_this_edge_receipt",
    "WhyThisEdgeStore",
    "SUBSTRATE_SCHEMA",
    "harvest_resolution_substrate",
    "why_this_edge",
]
