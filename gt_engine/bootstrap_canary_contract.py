"""Import-safe validation for the one-call bootstrap canary receipt."""

from __future__ import annotations

from typing import Any

from gt_engine.persistent_execution_state import SELECT_CATALOG_TOOL_NAME


def validate_canary(result: dict[str, object]) -> tuple[str, ...]:
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
    ) != "named_function":
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

    def is_sha256(value: Any) -> bool:
        text = str(value or "")
        return len(text) == 64 and all(
            character in "0123456789abcdef" for character in text
        )

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


__all__ = ["validate_canary"]
