"""Project central-agent artifacts into the production run receipt contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .run_receipt_v2 import RunReceiptFinalizer

_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
}


def _certified_claims(
    delivery_kind: str,
    delivery: dict[str, Any],
    *,
    source_file_digests: dict[str, str],
) -> list[dict[str, Any]]:
    if delivery_kind == "context_frontier":
        facts = delivery.get("facts") or ()
    elif delivery_kind == "semantic_evidence":
        facts = delivery.get("items") or ()
    else:
        return []
    repository_revision = str(delivery.get("source_revision") or "")
    graph_revision = str(delivery.get("graph_revision") or "")
    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, str]] = set()
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        path = str(fact.get("path") or "").replace("\\", "/").removeprefix("./")
        line = int(fact.get("line") or 0)
        symbol = str(fact.get("symbol") or "")
        kind = str(fact.get("kind") or "").lower()
        digest = str(fact.get("content_sha256") or source_file_digests.get(path) or "")
        identity = (path, line, symbol, kind)
        if (
            not path
            or line < 1
            or len(digest) != 64
            or not repository_revision
            or not graph_revision
            or identity in seen
        ):
            continue
        seen.add(identity)
        language = str(fact.get("language") or _LANGUAGE_BY_SUFFIX.get(Path(path).suffix) or "")
        if kind in {"definition", "signature", "symbol"} and symbol:
            role = "edit_owner"
            feature_id = "implementation_owner"
            claim_id = f"symbol:{language}:{path}:{symbol}"
            text = f"{symbol} is defined at {path}:{line}"
            symbol_identity = f"{language}:{path}:{symbol}"
            action = f"Inspect {path}:{line} before choosing the edit target."
        elif kind == "test":
            role = "affected_test"
            feature_id = "affected_tests"
            claim_id = f"test:{path}:{line}:{symbol}"
            text = f"Affected test candidate at {path}:{line} {symbol}".strip()
            symbol_identity = ""
            action = f"Include {path} when selecting verification."
        else:
            role = "inspection_dependency"
            feature_id = "inspection_files"
            claim_id = f"inspect:{path}:{line}:{symbol or kind}"
            text = f"Inspect {path}:{line} for {kind or 'repository evidence'}"
            symbol_identity = ""
            action = f"Inspect {path}:{line} before editing related code."
        claims.append(
            {
                "claim_id": claim_id,
                "text": text,
                "role": role,
                "requirement_id": feature_id,
                "repository_revision": repository_revision,
                "graph_revision": graph_revision,
                "source_evidence": [
                    {
                        "path": path,
                        "start_line": line,
                        "end_line": line,
                        "content_sha256": digest,
                        "excerpt": "",
                    }
                ],
                "action": action,
                "prevents": "prevents selecting an unsupported repository target",
                "symbol_identity": symbol_identity,
                "relationship": str(fact.get("relation") or ""),
                "competing_identities": [],
                "disambiguation_action": "",
                "semantic_similarity": float(fact.get("retrieval_relevance") or 0.0),
                "exact_identifier_match": bool(symbol),
                "graph_distance": None,
                "authoritative_edge": bool(fact.get("relation")),
                "evidence_quality": float(fact.get("semantic_certainty") or 1.0),
                "certified": True,
                "retrieval_truncated": bool(delivery.get("truncated_count")),
            }
        )
    return claims


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _classification(terminal: str, *, artifacts_present: bool) -> str:
    normalized = terminal.strip().lower()
    if normalized in {
        "modeltimeout",
        "providertimeout",
        "providererror",
        "providerfailed",
        "providermodelmismatch",
    }:
        return "PROVIDER_ERROR"
    if not artifacts_present:
        return "SETUP_ERROR"
    if normalized in {
        "runtimeerror",
        "internalerror",
        "internal_error",
        "harnesserror",
    }:
        return "HARNESS_ERROR"
    return "COMPLETED"


def _terminal(
    central: dict[str, Any],
    trajectory: dict[str, Any],
    exception: BaseException | None,
) -> str:
    value = str((trajectory.get("info") or {}).get("exit_status") or "").strip()
    if value:
        return value
    value = str((central.get("metrics") or {}).get("terminal") or "").strip()
    if value:
        return value
    return type(exception).__name__ if exception is not None else "Unknown"


def _provider_duration_ms(central: dict[str, Any]) -> float:
    contexts = central.get("model_call_contexts") or ()
    total = 0.0
    for row in contexts:
        if not isinstance(row, dict):
            continue
        for key in ("provider_duration_ms", "query_duration_ms", "duration_ms"):
            if isinstance(row.get(key), (int, float)):
                total += max(0.0, float(row[key]))
                break
    return total


def _graph_rows(central: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for receipt in central.get("repository_work_receipts") or ():
        if not isinstance(receipt, dict) or receipt.get("kind") != "initial_index":
            continue
        rows.append(
            {
                "kind": "initial",
                "repository_revision": str(receipt.get("source_revision") or ""),
                "graph_revision": str(receipt.get("graph_revision") or ""),
                "duration_ms": float(receipt.get("duration_ms") or 0.0),
                "success": str(receipt.get("status") or "")
                not in {"failed", "error", "unavailable"},
                "workspace_revision": str(receipt.get("source_revision") or ""),
                "mode": "full",
            }
        )
    session = central.get("repository_session") or {}
    for receipt in session.get("refresh_log") or ():
        if not isinstance(receipt, dict):
            continue
        rows.append(
            {
                "kind": "refresh",
                "repository_revision": str(
                    receipt.get("source_revision") or receipt.get("repository_revision") or ""
                ),
                "graph_revision": str(receipt.get("graph_revision") or ""),
                "duration_ms": float(receipt.get("duration_ms") or 0.0),
                "success": str(receipt.get("status") or "")
                not in {"failed", "error", "unavailable"},
                "workspace_revision": str(receipt.get("workspace_revision") or ""),
                "mode": str(receipt.get("mode") or "central_incremental"),
            }
        )
    return rows


def finalize_central_run_receipt(
    finalizer: RunReceiptFinalizer,
    *,
    logs_dir: str | Path,
    exception: BaseException | None = None,
) -> dict[str, Any]:
    """Finalize v2 from whatever authoritative central artifacts exist."""

    root = Path(logs_dir)
    central = _read_json(root / "central_receipt.json")
    trajectory = _read_json(root / "miniswe_trajectory.json")
    metrics = central.get("metrics") or {}
    finalizer.record_provider_usage(
        calls=int(metrics.get("api_calls", central.get("calls", 0)) or 0),
        input_tokens=int(metrics.get("input_tokens") or 0),
        output_tokens=int(metrics.get("output_tokens") or 0),
        duration_ms=_provider_duration_ms(central),
    )
    source_revision = str(central.get("source_revision") or "")
    graph_rows = _graph_rows(central)
    successful_graphs = {
        (
            str(row.get("repository_revision") or ""),
            str(row.get("graph_revision") or ""),
        )
        for row in graph_rows
        if row.get("success") is True
    }
    initial_revision = next(
        (
            str(row.get("repository_revision") or "")
            for row in graph_rows
            if str(row.get("repository_revision") or "")
        ),
        source_revision,
    )
    finalizer.record_repository_identity(
        initial_repository_revision=initial_revision,
        final_repository_revision=source_revision,
    )
    for row in graph_rows:
        finalizer.record_graph_build(**row)
    source_file_digests = {
        str(path).replace("\\", "/").removeprefix("./"): str(digest)
        for path, digest in (central.get("source_file_digests") or {}).items()
    }
    delivery_groups = (
        (
            "preemptive_retrieval",
            (central.get("preemptive_retrieval") or {}).get("deliveries") or (),
        ),
        ("relational_context", (central.get("relational_context") or {}).get("deliveries") or ()),
        ("semantic_evidence", (central.get("semantic_evidence") or {}).get("deliveries") or ()),
        ("repository_context", (central.get("repository_context") or {}).get("deliveries") or ()),
        (
            "context_frontier",
            (central.get("repository_intelligence") or {}).get("frontier_deliveries") or (),
        ),
        ("guidance", central.get("guidance_deliveries") or ()),
    )
    for delivery_kind, deliveries in delivery_groups:
        for delivery in deliveries:
            if not isinstance(delivery, dict):
                continue
            visible_hex = str(delivery.get("model_visible_bytes_hex") or "")
            delivery_graph_revision = str(delivery.get("graph_revision") or "")
            finalizer.record_delivery(
                {
                    "request_id": str(delivery.get("delivery_id") or ""),
                    "payload_sha256": str(delivery.get("request_payload_sha256") or ""),
                    "model_visible_bytes_hex": visible_hex,
                    "model_visible_bytes_sha256": str(
                        delivery.get("model_visible_bytes_sha256") or ""
                    ),
                    "repository_revision": str(delivery.get("source_revision") or source_revision),
                    "graph_revision": delivery_graph_revision,
                    "role": str(delivery.get("feature_id") or delivery_kind),
                    "decision_boundary": str(delivery.get("decision_need_kind") or ""),
                    "delivery_tokens": max(0, (len(visible_hex) // 2 + 3) // 4),
                    "resulting_agent_action": str(delivery.get("next_command") or ""),
                }
            )
            delivery_repository_revision = str(
                delivery.get("source_revision") or source_revision
            )
            claims = _certified_claims(
                delivery_kind,
                delivery,
                source_file_digests=(
                    source_file_digests
                    if delivery_repository_revision == source_revision
                    else {}
                ),
            )
            try:
                visible = bytes.fromhex(visible_hex)
            except ValueError:
                visible = b""
            if visible:
                digest_valid = hashlib.sha256(visible).hexdigest() == str(
                    delivery.get("model_visible_bytes_sha256") or ""
                )
            else:
                digest_valid = False
            identity_current = (
                delivery_repository_revision,
                delivery_graph_revision,
            ) in successful_graphs
            triggering_event = str(
                delivery.get("decision_need_id")
                or delivery.get("delivery_id")
                or f"central:{delivery_kind}"
            )
            if claims and digest_valid and identity_current:
                feature_id = (
                    "implementation_owner"
                    if any(claim["role"] == "edit_owner" for claim in claims)
                    else "affected_tests"
                    if any(claim["role"] == "affected_test" for claim in claims)
                    else "inspection_files"
                )
                boundary = (
                    "REPOSITORY_START"
                    if int(delivery.get("call") or delivery.get("delivered_before_call") or 0)
                    <= 1
                    else "PRE_EDIT"
                )
                resulting_action = str(
                    delivery.get("semantic_use_action_id")
                    or delivery.get("next_command")
                    or ""
                )
                consumed = str(delivery.get("semantic_utilization") or "") == "matched"
                transitions = [
                    {"from": None, "to": "CANDIDATE", "reason": "central trigger matched"},
                    {
                        "from": "CANDIDATE",
                        "to": "CERTIFIED",
                        "reason": "source claims certified",
                    },
                    {
                        "from": "CERTIFIED",
                        "to": "DELIVERED",
                        "reason": "exact bytes exposed",
                    },
                ]
                if consumed:
                    transitions.extend(
                        (
                            {
                                "from": "DELIVERED",
                                "to": "CONSUMED",
                                "reason": "later action used delivery",
                            },
                            {
                                "from": "CONSUMED",
                                "to": "VALIDATED",
                                "reason": "source identity and matched action remained consistent",
                            },
                        )
                    )
                lifecycle = {
                    "schema": "gt.feature_lifecycle.v1",
                    "feature_id": feature_id,
                    "stage": "VALIDATED" if consumed else "DELIVERED",
                    "triggering_event": triggering_event,
                    "repository_revision": delivery_repository_revision,
                    "graph_revision": delivery_graph_revision,
                    "decision_boundary": boundary,
                    "claims": claims,
                    "model_visible_bytes_hex": visible_hex,
                    "model_visible_bytes_sha256": str(
                        delivery.get("model_visible_bytes_sha256") or ""
                    ),
                    "resulting_agent_action": resulting_action if consumed else "",
                    "validation_result": (
                        "source identity and matched action remained consistent"
                        if consumed
                        else ""
                    ),
                    "terminal_reason": "",
                    "transitions": transitions,
                }
            else:
                lifecycle = {
                    "schema": "gt.feature_lifecycle.v1",
                    "feature_id": str(delivery.get("feature_id") or delivery_kind),
                    "stage": "ABSTAINED",
                    "triggering_event": triggering_event,
                    "repository_revision": delivery_repository_revision,
                    "graph_revision": delivery_graph_revision or "not-applicable",
                    "decision_boundary": "",
                    "claims": [],
                    "model_visible_bytes_hex": "",
                    "model_visible_bytes_sha256": "",
                    "resulting_agent_action": "",
                    "validation_result": "",
                    "terminal_reason": "independent source certification unavailable",
                    "transitions": [
                        {"from": None, "to": "CANDIDATE", "reason": "central trigger matched"},
                        {
                            "from": "CANDIDATE",
                            "to": "ABSTAINED",
                            "reason": "source certification unavailable",
                        },
                    ],
                }
            finalizer.record_feature_lifecycle(lifecycle)
    terminal = _terminal(central, trajectory, exception)
    return finalizer.finalize(
        terminal=terminal,
        infrastructure_classification=_classification(
            terminal, artifacts_present=bool(central or trajectory)
        ),
        exception=exception,
        trajectory=trajectory,
    )


__all__ = ["finalize_central_run_receipt"]
