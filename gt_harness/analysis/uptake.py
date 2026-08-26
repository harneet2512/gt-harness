"""Measure only observable agent use of provider-visible GT evidence."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

_EDIT_COMMAND = re.compile(
    r"(?i)(?:\bapply_patch\b|\bsed\s+-i\b|\bperl\s+-pi\b|\btee\b|"
    r"\b(?:rm|mv|cp|touch)\b|>|\bpython\b[^\n]*(?:write_text|open\())"
)


def _path(value: object) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    candidate = PurePosixPath(normalized)
    if not normalized or candidate.is_absolute() or ".." in candidate.parts:
        return ""
    return candidate.as_posix()


def _assistant_actions(transcript: object) -> list[tuple[int, str]]:
    if not isinstance(transcript, list):
        return []
    actions: list[tuple[int, str]] = []
    call = 0
    for message in transcript:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        call += 1
        extra = message.get("extra")
        rows = extra.get("actions") if isinstance(extra, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            command = str(row.get("command") or row.get("input") or "")
            if command:
                actions.append((call, command.replace("\\", "/")))
    return actions


def measure_delivery_uptake(run_receipt: dict[str, Any]) -> dict[str, Any]:
    """Bind delivered paths to later visible shell actions.

    This deliberately does not claim that a model "reasoned about" a fact.
    Uptake means only that a later durable action cited the exact relative path.
    """

    treatment = run_receipt.get("treatment_receipt")
    deliveries = (
        treatment.get("provider_delivery_receipts", []) if isinstance(treatment, dict) else []
    )
    actions = _assistant_actions(run_receipt.get("transcript"))
    rows: list[dict[str, Any]] = []
    all_paths: set[str] = set()
    used_paths: set[str] = set()
    edited_paths: set[str] = set()
    for delivery in deliveries if isinstance(deliveries, list) else []:
        if not isinstance(delivery, dict):
            continue
        before_call = int(delivery.get("delivered_before_call") or 0)
        role_paths = delivery.get("provider_visible_role_paths")
        role_paths = role_paths if isinstance(role_paths, dict) else {}
        delivered = {
            path
            for values in role_paths.values()
            if isinstance(values, list)
            for value in values
            if (path := _path(value))
        }
        all_paths.update(delivered)
        observed: dict[str, list[int]] = {}
        edited: dict[str, list[int]] = {}
        for call, command in actions:
            if call < before_call:
                continue
            for path in delivered:
                if path not in command:
                    continue
                observed.setdefault(path, []).append(call)
                used_paths.add(path)
                if _EDIT_COMMAND.search(command):
                    edited.setdefault(path, []).append(call)
                    edited_paths.add(path)
        rows.append(
            {
                "delivery_index": int(delivery.get("delivery_index") or 0),
                "delivered_before_call": before_call,
                "delivered_paths": sorted(delivered),
                "observed_action_calls": observed,
                "observed_edit_calls": edited,
                "status": "USED" if observed else "NOT_OBSERVED",
            }
        )
    return {
        "schema": "gt.delivery_uptake.v1",
        "measurement": "exact_relative_path_in_durable_assistant_action",
        "delivery_count": len(rows),
        "delivered_path_count": len(all_paths),
        "used_path_count": len(used_paths),
        "edited_path_count": len(edited_paths),
        "path_uptake_rate": round(len(used_paths) / len(all_paths), 6) if all_paths else None,
        "used_paths": sorted(used_paths),
        "edited_paths": sorted(edited_paths),
        "deliveries": rows,
        "hidden_reasoning_inferred": False,
    }
