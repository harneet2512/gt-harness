"""Join real trajectory, event journal, and provider-delivery receipts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PATH = re.compile(
    r"(?<![\w.-])(?:[A-Za-z]:)?[\w.-]+(?:/[\w.@+-]+)+(?::\d+(?:-\d+)?)?"
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        row
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and isinstance((row := json.loads(line)), dict)
    ]


def _assistant_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message for message in messages if message.get("role") == "assistant"]


def _action_text(message: dict[str, Any]) -> str:
    parts = [
        str(action.get("command") or action.get("cmd") or "")
        for action in (message.get("extra") or {}).get("actions") or ()
        if isinstance(action, dict)
    ]
    return "\n".join(parts)


def _delivery_text(row: dict[str, Any], journal_by_request: dict[str, dict]) -> str:
    encoded = str(row.get("model_visible_bytes_hex") or "")
    if encoded:
        try:
            return bytes.fromhex(encoded).decode("utf-8", "replace")
        except ValueError:
            return ""
    request_id = str(row.get("delivery_id") or row.get("request_id") or "")
    return str((journal_by_request.get(request_id) or {}).get("suffix") or "")


@dataclass(frozen=True, slots=True)
class UptakeAudit:
    valid: bool
    deliveries: tuple[dict[str, Any], ...]
    issues: tuple[str, ...]
    consumed: int
    validated: int
    contradicted: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gt.delivery_uptake.v2",
            "valid": self.valid,
            "deliveries": list(self.deliveries),
            "issues": list(self.issues),
            "consumed": self.consumed,
            "validated": self.validated,
            "contradicted": self.contradicted,
        }


def audit_delivery_uptake(
    *,
    trajectory_path: str | Path,
    event_journal_path: str | Path,
    run_receipt_path: str | Path,
) -> UptakeAudit:
    """Measure later action/validation against exact delivered bytes.

    The trajectory may use the production ``gt-run.trajectory.json`` name or
    a benchmark scaffold name; content, not filename, is authoritative.
    """

    trajectory = _json(Path(trajectory_path))
    journal = _jsonl(Path(event_journal_path))
    receipt = _json(Path(run_receipt_path))
    issues: list[str] = []
    if receipt.get("schema") != "gt.run_receipt.v2":
        issues.append("run_receipt_schema_invalid")
    journal_deliveries = {
        str(row.get("request_id") or ""): row
        for row in journal
        if row.get("event") == "provider_delivery" and row.get("request_id")
    }
    messages = list(trajectory.get("messages") or ())
    turns = _assistant_turns(messages)
    assistant_positions = [
        index for index, message in enumerate(messages) if message.get("role") == "assistant"
    ]
    delivery_rows: list[dict[str, Any]] = []
    for raw in receipt.get("deliveries") or ():
        if not isinstance(raw, dict):
            issues.append("delivery_row_invalid")
            continue
        request_id = str(raw.get("delivery_id") or raw.get("request_id") or "")
        if request_id and request_id not in journal_deliveries:
            issues.append(f"delivery_missing_from_event_journal:{request_id}")
        iteration = max(0, int(raw.get("iteration") or 0))
        delivered_text = _delivery_text(raw, journal_deliveries)
        anchors = tuple(dict.fromkeys(_PATH.findall(delivered_text)))
        # Provider delivery precedes the response for this iteration, so the
        # response's action is eligible uptake (iteration 1 -> turn index 0).
        later = turns[max(0, iteration - 1):]
        action_texts = [_action_text(message) for message in later]
        consumed = bool(
            anchors
            and any(
                any(anchor.split(":", 1)[0] in action for anchor in anchors)
                for action in action_texts
            )
        )
        delivery_position = (
            assistant_positions[iteration - 1]
            if iteration and iteration <= len(assistant_positions)
            else -1
        )
        later_tool_texts = [
            str(message.get("content") or "").lower()
            for index, message in enumerate(messages)
            if index > delivery_position and message.get("role") == "tool"
        ]
        outcomes = [
            outcome
            for text in later_tool_texts
            for outcome in (
                ["VALIDATED"]
                if re.search(r"\b(?:passed|pass|ok|success)\b", text)
                else ["CONTRADICTED"]
                if re.search(r"\b(?:failed|failure|error|traceback)\b", text)
                else []
            )
        ]
        validated = consumed and bool(outcomes and outcomes[-1] == "VALIDATED")
        contradicted = consumed and bool(outcomes and outcomes[-1] == "CONTRADICTED")
        delivery_rows.append(
            {
                "delivery_id": request_id,
                "iteration": iteration,
                "anchors": list(anchors),
                "consumed": consumed,
                "resulting_agent_action": next(
                    (
                        action
                        for action in action_texts
                        if any(anchor.split(":", 1)[0] in action for anchor in anchors)
                    ),
                    "",
                ),
                "validated": validated,
                "contradicted": contradicted,
            }
        )
    return UptakeAudit(
        valid=not issues,
        deliveries=tuple(delivery_rows),
        issues=tuple(issues),
        consumed=sum(bool(row["consumed"]) for row in delivery_rows),
        validated=sum(bool(row["validated"]) for row in delivery_rows),
        contradicted=sum(bool(row["contradicted"]) for row in delivery_rows),
    )


__all__ = ["UptakeAudit", "audit_delivery_uptake"]
