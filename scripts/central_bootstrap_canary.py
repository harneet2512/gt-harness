"""Run the exact one-call persistent-state bootstrap contract against a provider."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path

from eval.gt_central_agent import MiniSweCentralAgent
from gt_engine.hybrid_retrieval import EvidenceAuthority, EvidenceOrigin
from gt_engine.persistent_execution_state import (
    SELECT_CATALOG_TOOL_NAME,
    BootstrapCatalog,
    BootstrapCatalogItem,
    CatalogItemKind,
)


def _item(
    *,
    kind: CatalogItemKind,
    index: int,
    label: str,
    required: bool = False,
    evidence_authority: EvidenceAuthority = EvidenceAuthority.IDENTITY_ONLY,
) -> BootstrapCatalogItem:
    digest = hashlib.sha256(f"{kind.value}:{index}:{label}".encode()).hexdigest()[:20]
    return BootstrapCatalogItem(
        item_id=f"pes-{digest}",
        kind=kind,
        label=label,
        path=f"src/module_{index:02d}.py",
        symbol=f"symbol_{index:02d}",
        relation="calls" if evidence_authority is EvidenceAuthority.CERTIFIED_RELATION else "",
        required=required,
        origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
        evidence_authority=evidence_authority,
    )


def production_shaped_catalog() -> BootstrapCatalog:
    """Live-shaped catalog: many hashed IDs, mixed kinds, larger than the 2k visible ceiling."""

    items: list[BootstrapCatalogItem] = []
    items.append(
        _item(
            kind=CatalogItemKind.VALIDATION,
            index=0,
            label="Required validation: pytest tests/test_service.py -q --tb=short",
            required=True,
            evidence_authority=EvidenceAuthority.EXECUTION_OBSERVATION,
        )
    )
    for index in range(1, 12):
        items.append(
            _item(
                kind=CatalogItemKind.FOCUS,
                index=index,
                label=(
                    f"Hybrid-ranked repository candidate #{index}: "
                    f"src/module_{index:02d}.py:1#symbol_{index:02d} implementation surface"
                ),
            )
        )
    for index in range(12, 24):
        items.append(
            _item(
                kind=CatalogItemKind.DEPENDENCY,
                index=index,
                label=(
                    f"Certified calls neighbor src/module_{index:02d}.py#symbol_{index:02d} "
                    "connected to the active implementation candidate"
                ),
                evidence_authority=EvidenceAuthority.CERTIFIED_RELATION,
            )
        )
    for index in range(24, 32):
        items.append(
            _item(
                kind=CatalogItemKind.FOCUS,
                index=index,
                label=f"Task-named repository path filler src/module_{index:02d}.py",
            )
        )
    return BootstrapCatalog(
        source_revision="bootstrap-canary-source-v1",
        graph_source_revision="bootstrap-canary-source-v1",
        graph_revision="bootstrap-canary-graph-v1",
        items=tuple(items),
        complete=True,
    )


def legacy_one_item_catalog() -> BootstrapCatalog:
    """Negative fixture only. Live canary must not use a 1-item FOCUS catalog."""

    return BootstrapCatalog(
        source_revision="bootstrap-canary-source-v1",
        graph_source_revision="bootstrap-canary-source-v1",
        graph_revision="bootstrap-canary-graph-v1",
        items=(
            BootstrapCatalogItem(
                item_id="focus:src/service.py:save_user",
                kind=CatalogItemKind.FOCUS,
                label="save_user at src/service.py",
                path="src/service.py",
                symbol="save_user",
                required=True,
                provenance=("bootstrap_canary",),
            ),
        ),
        complete=True,
    )


async def run_canary(*, model_name: str, timeout_sec: float) -> dict[str, object]:
    catalog = production_shaped_catalog()
    with tempfile.TemporaryDirectory(prefix="gt-bootstrap-canary-") as logs_dir:
        agent = MiniSweCentralAgent(logs_dir=Path(logs_dir), model_name=model_name)
        model = agent._build_model()
        selection, receipt = await agent._run_persistent_state_bootstrap(
            model,
            instruction="Select the certified implementation focus and related files.",
            catalog=catalog,
            timeout_sec=timeout_sec,
        )
        effective_model = str(
            getattr(getattr(model, "config", None), "model_name", "")
            or getattr(model, "model_name", "")
        )
    result: dict[str, object] = {
        "schema": "gt.persistent_bootstrap_canary.v1",
        "model_requested": model_name,
        "model_effective": effective_model,
        "selection_valid": selection.valid,
        "selection": selection.as_dict(),
        "receipt": receipt,
        "catalog_count": len(catalog.items),
    }
    return result


def validate_canary(result: dict[str, object]) -> tuple[str, ...]:
    """Fail closed on every bootstrap property needed before task fan-out."""

    receipt = result.get("receipt")
    if not isinstance(receipt, dict):
        return ("receipt_missing",)
    failures: list[str] = []
    identity = receipt.get("response_identity") or {}
    contract = receipt.get("call_contract") or {}
    if result.get("selection_valid") is not True or receipt.get("status") != "selected":
        failures.append("selection_invalid")
    if receipt.get("response_received") is not True:
        failures.append("response_missing")
    if int(receipt.get("logical_calls") or 0) != 1 or int(
        receipt.get("provider_calls") or 0
    ) != 1:
        failures.append("not_exactly_one_call")
    if int(receipt.get("action_executions") or 0) != 0:
        failures.append("bootstrap_action_executed")
    if receipt.get("transport") != "direct_single_provider_call":
        failures.append("transport_not_direct_single_call")
    if "provider_query_marker_error" not in receipt or str(
        receipt.get("provider_query_marker_error") or ""
    ):
        failures.append("provider_query_marker_failed")
    if receipt.get("provider_error"):
        failures.append("provider_error")
    if contract.get("forced_tool") != SELECT_CATALOG_TOOL_NAME or contract.get(
        "tool_choice"
    ) != ("named_function"):
        failures.append("forced_select_catalog_contract_missing")
    if contract.get("num_retries") != 0:
        failures.append("provider_retry_enabled")
    effective_model = str(result.get("model_effective") or "").lower()
    if "deepseek-v4" in effective_model and contract.get("thinking_mode") != "disabled":
        failures.append("bootstrap_thinking_adapter_missing")
    catalog_count = int(receipt.get("catalog_count") or result.get("catalog_count") or 0)
    visible_count = int(receipt.get("visible_catalog_count") or 0)
    if catalog_count < 16:
        failures.append("catalog_not_production_shaped")
    if visible_count <= 0:
        failures.append("visible_catalog_missing")
    elif visible_count >= catalog_count:
        failures.append("catalog_not_truncated")

    def is_sha256(value: object) -> bool:
        text = str(value or "")
        return len(text) == 64 and all(character in "0123456789abcdef" for character in text)

    if not is_sha256(receipt.get("request_payload_sha256")) or not is_sha256(
        receipt.get("provider_messages_sha256")
    ):
        failures.append("request_hash_missing")
    if not is_sha256(receipt.get("visible_catalog_ids_sha256")):
        failures.append("visible_catalog_missing")
    if not is_sha256(receipt.get("raw_tool_arguments_sha256")):
        failures.append("raw_bootstrap_args_missing")
    response_model = str(identity.get("model") or "")
    if not response_model:
        failures.append("served_model_missing")
    elif not effective_model or response_model.lower().split("/")[-1] != (
        effective_model.lower().split("/")[-1]
    ):
        failures.append("served_model_mismatch")
    if not str(identity.get("provider") or ""):
        failures.append("provider_identity_missing")
    return tuple(failures)


def provider_route_proof(result: dict[str, object], *, provider: str) -> dict[str, object]:
    """Map the production canary receipt into the DeepSWE merge proof schema."""

    receipt = result.get("receipt") if isinstance(result.get("receipt"), dict) else {}
    identity = receipt.get("response_identity") or {}
    contract = receipt.get("call_contract") or {}
    requested = str(result.get("model_effective") or result.get("model_requested") or "")
    cost = receipt.get("cost")
    cost_observed = cost is not None
    return {
        "schema": "gt.provider_bootstrap_canary.v1",
        "provider_calls": int(receipt.get("provider_calls") or 0),
        "tool_choice_forced": True,
        "action_count": 1,
        "selection_valid": bool(result.get("selection_valid")),
        "selection": result.get("selection") or {},
        "requested_model": requested,
        "response_model": str(identity.get("model") or ""),
        "system_fingerprint": str(identity.get("system_fingerprint") or ""),
        "fingerprint_available": bool(identity.get("system_fingerprint")),
        "response_provider": str(identity.get("provider") or ""),
        "requested_provider": (
            "deepseek"
            if provider == "openrouter"
            else "tokenrouter"
            if provider == "tokenrouter"
            else "deepseek_native"
            if provider == "deepseek"
            else "configured"
        ),
        "catalog_model_confirmed": True if provider == "tokenrouter" else None,
        "thinking_mode": str(contract.get("thinking_mode") or "provider_default"),
        "forced_tool": str(contract.get("forced_tool") or ""),
        "allow_fallbacks": False if provider == "openrouter" else None,
        "require_parameters": True if provider == "openrouter" else None,
        "data_collection": "allow" if provider == "openrouter" else None,
        "benchmark_setup_overhead": {
            "provider_calls": int(receipt.get("provider_calls") or 0),
            "input_tokens": int(receipt.get("input_tokens") or 0),
            "output_tokens": int(receipt.get("output_tokens") or 0),
            "cost_usd": float(cost) if cost_observed else None,
            "cost_observed": cost_observed,
            "latency_ms": float(receipt.get("latency_ms") or 0.0),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.environ.get("MODEL") or "")
    parser.add_argument("--timeout-sec", type=float, default=45.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provider-proof", type=Path)
    args = parser.parse_args(argv)
    if not args.model:
        parser.error("--model or MODEL is required")
    result = asyncio.run(run_canary(model_name=args.model, timeout_sec=args.timeout_sec))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    failures = validate_canary(result)
    if args.provider_proof is not None:
        proof = provider_route_proof(
            result, provider=os.environ.get("PROVIDER") or "configured"
        )
        args.provider_proof.write_text(
            json.dumps(proof, indent=2) + "\n", encoding="utf-8"
        )
    if failures:
        print(json.dumps({"canary_failures": failures}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
