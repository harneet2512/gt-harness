"""The receipt ladder (L1-L4) for the Mini-SWE seam — vendored from the real GT.

Doctrine (gt_math): "fired is not delivered; delivered is not consumed." A fact
class is WORKING only on measured non-re-acquisition + a decision change in the
chronological read — never on fired/delivered/rendered.

States mirror ``groundtruth.runtime.evidence_envelope``:
    none -> delivered -> referenced -> acted -> resolved_state -> causal
L5 (causal) is counterfactual/paired-only and never computed single-arm.

This module owns the deterministic promotion: the seam records L1 (delivered) at
seal time; the audit promotes L2 (referenced - the agent quoted/read the fact),
L3 (acted - the next action targeted it), and L4 (resolved_state - the run
reached a terminal state after it) by reading the agent's OWN trajectory bytes
chronologically. Both-sides: every L1 must have an agent-side observation block
(the dose law); a seal with no agent-side block is a delivery lie.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

RECEIPT_SCHEMA = "gt_receipt.v1"

RECEIPT_NONE = "none"
RECEIPT_DELIVERED = "delivered"
RECEIPT_REFERENCED = "referenced"
RECEIPT_ACTED = "acted"
RECEIPT_RESOLVED_STATE = "resolved_state"
RECEIPT_CAUSAL = "causal"

_TRANSITION_RANK = {
    RECEIPT_NONE: 0,
    RECEIPT_DELIVERED: 1,
    RECEIPT_REFERENCED: 2,
    RECEIPT_ACTED: 3,
    RECEIPT_RESOLVED_STATE: 4,
    RECEIPT_CAUSAL: 5,
}


@dataclass(frozen=True)
class Receipt:
    """One delivery's ladder position, monotone over the episode."""

    evidence_type: str
    dedup_key: str
    transition: str
    target: str = ""
    action_index: int = 0
    iteration: int = 0
    referenced: bool = False
    acted: bool = False
    resolved_state: bool = False

    def to_row(self) -> dict[str, Any]:
        return {
            "schema": RECEIPT_SCHEMA,
            "transition": self.transition,
            "evidence_type": self.evidence_type,
            "dedup_key": self.dedup_key,
            "target": self.target,
            "action_index": self.action_index,
            "iteration": self.iteration,
            "referenced": self.referenced,
            "acted": self.acted,
            "resolved_state": self.resolved_state,
        }


def payload_hash(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mentions(value: str, target: str, base: str) -> bool:
    """True when a trajectory string references the delivered target."""
    return bool(target and (target in value or (base and base in value)))


def _action_map(msgs: list[dict]) -> dict[int, dict]:
    """action_index -> {tool_idx, next_assistant_idx} (trajectory positions)."""
    out: dict[int, dict] = {}
    counter = 0
    for i, msg in enumerate(msgs):
        if msg.get("role") != "assistant":
            continue
        actions = (msg.get("extra") or {}).get("actions") or []
        for j, _a in enumerate(actions):
            counter += 1
            tool_idx = i + 1 + j
            nxt = next(
                (k for k in range(tool_idx + 1, len(msgs))
                 if msgs[k].get("role") == "assistant"),
                None,
            )
            out[counter] = {"tool_idx": tool_idx, "next_assistant_idx": nxt}
    return out


def promote_receipts(
    receipts: list[dict],
    msgs: list[dict],
    *,
    terminal_finished: bool,
) -> tuple[list[Receipt], dict[str, dict[str, int]]]:
    """Promote each L1 (delivered) receipt to its highest observed transition.

    Reads the agent's own trajectory chronologically AFTER the delivery:
    L2 referenced = a later assistant message content mentions the target;
    L3 acted = a later action command targets it; L4 resolved_state = the run
    reached a terminal state after it. Returns the promoted receipts and a
    per-evidence-type L1-L4 census.
    """
    action_map = _action_map(msgs)

    def _after(act_idx: int) -> tuple[list[str], list[str]]:
        texts: list[str] = []
        commands: list[str] = []
        start = None
        if act_idx == 0:
            start = next((i for i, m in enumerate(msgs)
                          if m.get("role") == "assistant"), None)
        else:
            info = action_map.get(act_idx)
            start = info["next_assistant_idx"] if info else None
        for k in range(start if start is not None else 0, len(msgs)):
            msg = msgs[k]
            texts.append(str(msg.get("content") or ""))
            if msg.get("role") == "assistant":
                for a in (msg.get("extra") or {}).get("actions", []) or []:
                    commands.append(str(a.get("command") or ""))
        return texts, commands

    promoted: list[Receipt] = []
    census: dict[str, dict[str, int]] = {}
    for raw in receipts:
        if raw.get("schema") != RECEIPT_SCHEMA:
            continue
        ev_type = str(raw.get("evidence_type") or "")
        target = str(raw.get("target") or "")
        base = target.split("/")[-1] if target else ""
        texts, commands = _after(int(raw.get("action_index") or 0))
        referenced = any(_mentions(t, target, base) for t in texts) or any(
            _mentions(c, target, base) for c in commands
        )
        acted = any(_mentions(c, target, base) for c in commands)
        resolved = bool(terminal_finished)
        transition = RECEIPT_DELIVERED
        if referenced:
            transition = RECEIPT_REFERENCED
        if acted:
            transition = RECEIPT_ACTED
        if resolved:
            transition = RECEIPT_RESOLVED_STATE
        receipt = Receipt(
            evidence_type=ev_type,
            dedup_key=str(raw.get("dedup_key") or ""),
            transition=transition,
            target=target,
            action_index=int(raw.get("action_index") or 0),
            iteration=int(raw.get("iteration") or 0),
            referenced=referenced,
            acted=acted,
            resolved_state=resolved,
        )
        promoted.append(receipt)
        entry = census.setdefault(ev_type, {
            "delivered": 0, "referenced": 0, "acted": 0, "resolved_state": 0,
        })
        entry["delivered"] += 1
        for key in ("referenced", "acted", "resolved_state"):
            if getattr(receipt, key):
                entry[key] += 1
    return promoted, census


def load_receipts(events: list[dict]) -> list[dict]:
    """Filter the event log to L1 delivered receipt rows."""
    return [row for row in events if row.get("event") == "receipt"]


def both_sides_dose_check(receipts: list[dict], msgs: list[dict]) -> tuple[bool, list[str]]:
    """The dose law: every L1 seal must have an agent-side observation block.

    A GT-side seal with no agent-side <gt-facts> block is a delivery lie (the
    F1 class). Returns (ok, issues).
    """
    issues: list[str] = []
    action_map = _action_map(msgs)
    for raw in receipts:
        act_idx = int(raw.get("action_index") or 0)
        info = action_map.get(act_idx)
        tool_idx = info["tool_idx"] if info else None
        block_present = False
        if tool_idx is not None and tool_idx < len(msgs):
            block_present = "<gt-facts>" in str(msgs[tool_idx].get("content") or "")
        if not block_present and act_idx != 0:
            issues.append(
                f"seal {raw.get('dedup_key', '')} (action {act_idx}) has no "
                "agent-side observation block"
            )
    return not issues, issues
