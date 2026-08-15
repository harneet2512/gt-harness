"""Evidence-aware context management for nano-harness.

``smart_truncate`` preserves nano's exact two-phase, char-metric truncation
(agent.py:_truncate_if_needed) as the base behavior. The single change is
phase 1's treatment of tool_result blocks that carry delivered GT evidence:

    SEALED => SEEN (FIX E, structural): a block containing ANY delivered
    span is EXEMPT from phase-1 truncation — never merely ranked last. A
    delivery sealed at the end of iteration N must still be present when the
    model reads iteration N+1; a sealed-but-never-seen delivery would be the
    F1 lie class (the seal attests the model received the bytes). Overflow
    falls through to phase 2 (tool_use input shrinking) and ultimately to
    nano's provider-side token cap — accepted cost: deliveries are <=4000
    chars and <=1 per observation.

Phase 2 (shrinking >200-char string args of past tool_use blocks) is kept
verbatim - giant edit_file `new` strings would otherwise re-inflate every
request. When GT never delivered anything there are no exemptions, so
behavior (including transcript truncation events) is byte-identical to the
original oldest-first pass (GT-off byte identity).
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

_VERIFIED = "VERIFIED"


def message_chars(messages: list[dict[str, Any]]) -> int:
    """Count all provider-visible strings, including tool arguments."""
    def size(value: Any) -> int:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, list):
            return sum(size(item) for item in value)
        if isinstance(value, dict):
            return sum(size(item) for item in value.values())
        return 0

    return sum(size(message) for message in messages)


def _bound_kept_blocks(
    messages: list[dict[str, Any]],
    *,
    tool_output_chars: int,
) -> None:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                value = str(block.get("content") or "")
                if len(value) > tool_output_chars:
                    head = value[: tool_output_chars // 2]
                    tail = value[-tool_output_chars // 2:]
                    block["content"] = (
                        f"{head}\n[older tool output elided: "
                        f"{len(value) - tool_output_chars} chars]\n{tail}"
                    )
            elif block.get("type") == "tool_use":
                inputs = block.get("input")
                if not isinstance(inputs, dict):
                    continue
                for key, value in list(inputs.items()):
                    if isinstance(value, str) and len(value) > tool_output_chars:
                        inputs[key] = (
                            f"{value[:tool_output_chars // 2]}\n"
                            f"[executed argument elided: "
                            f"{len(value) - tool_output_chars} chars]\n"
                            f"{value[-tool_output_chars // 2:]}"
                        )


def compact_provider_view(
    messages: list[dict[str, Any]],
    *,
    checkpoint: str,
    char_budget: int,
    target_char_budget: int | None = None,
    tail_turns: int = 2,
    max_tail_turns: int = 2,
    semantic_needles: tuple[str, ...] = (),
    tool_output_chars: int = 4000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Construct a bounded provider view without mutating durable history."""
    raw_chars = message_chars(messages)
    durable = copy.deepcopy(messages)
    if not durable:
        durable = [{"role": "user", "content": ""}]
    anchor = durable[0]
    anchor_content = anchor.get("content")
    suffix = f"\n\n[deterministic GT state]\n{checkpoint}".rstrip()
    if isinstance(anchor_content, str):
        anchor["content"] = anchor_content.rstrip() + suffix
    else:
        anchor["content"] = [
            {"type": "text", "text": str(anchor_content or "") + suffix}
        ]

    # Keep complete assistant -> following user/tool-result groups. Starting a
    # suffix at a tool_result would violate provider tool pairing.
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in durable[1:]:
        if message.get("role") == "assistant" and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)

    def group_hash(group: list[dict[str, Any]]) -> str:
        encoded = json.dumps(
            group,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8", "surrogatepass")
        return hashlib.sha256(encoded).hexdigest()

    target_budget = min(
        int(char_budget),
        int(target_char_budget)
        if target_char_budget is not None
        else int(char_budget),
    )
    full_view = [anchor] + [item for group in groups for item in group]
    _bound_kept_blocks(full_view, tool_output_chars=tool_output_chars)
    if (
        message_chars(full_view) <= target_budget
        and len(groups) <= max(1, int(max_tail_turns))
    ):
        active_chars = message_chars(full_view)
        return full_view, {
            "raw_message_chars": raw_chars,
            "active_message_chars": active_chars,
            "compacted": active_chars < raw_chars,
            "omitted_message_count": 0,
            "tail_turns": len(groups),
            "semantic_tail_turns": 0,
            "omitted_group_hashes": [],
        }

    keep_count = min(max(1, int(tail_turns)), len(groups))
    selected_indices = set(range(len(groups) - keep_count, len(groups)))
    normalized_needles = tuple(
        needle.lower() for needle in semantic_needles if str(needle).strip()
    )
    semantic_count = 0
    for index in range(len(groups) - keep_count - 1, -1, -1):
        if not normalized_needles:
            break
        rendered_group = json.dumps(
            groups[index],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).lower()
        if not any(needle in rendered_group for needle in normalized_needles):
            continue
        candidate_indices = sorted({*selected_indices, index})
        candidate_view = [
            anchor,
            *(
                item
                for selected_index in candidate_indices
                for item in groups[selected_index]
            ),
        ]
        _bound_kept_blocks(
            candidate_view,
            tool_output_chars=tool_output_chars,
        )
        if message_chars(candidate_view) <= target_budget:
            selected_indices.add(index)
            semantic_count = 1
        break

    earliest = min(selected_indices, default=len(groups)) - 1
    while (
        earliest >= 0
        and len(selected_indices) < max(keep_count, max_tail_turns)
    ):
        candidate_indices = sorted({*selected_indices, earliest})
        candidate_view = [
            anchor,
            *(
                item
                for selected_index in candidate_indices
                for item in groups[selected_index]
            ),
        ]
        _bound_kept_blocks(
            candidate_view,
            tool_output_chars=tool_output_chars,
        )
        if message_chars(candidate_view) > target_budget:
            break
        selected_indices.add(earliest)
        earliest -= 1
    selected = [groups[index] for index in sorted(selected_indices)]
    keep_count = len(selected)
    view = [anchor] + [item for group in selected for item in group]
    _bound_kept_blocks(view, tool_output_chars=tool_output_chars)
    omitted = len(durable) - len(view)
    omitted_group_hashes = [
        group_hash(group)
        for index, group in enumerate(groups)
        if index not in selected_indices
    ]

    # If the structural tail still exceeds the budget, retain its pairing but
    # reduce inline output/arguments further. Exact bytes remain durable.
    inline_limit = max(200, min(tool_output_chars, char_budget // 8))
    while message_chars(view) > char_budget and inline_limit > 200:
        inline_limit = max(200, inline_limit // 2)
        _bound_kept_blocks(view, tool_output_chars=inline_limit)
    active_chars = message_chars(view)
    return view, {
        "raw_message_chars": raw_chars,
        "active_message_chars": active_chars,
        "compacted": True,
        "omitted_message_count": max(0, omitted),
        "tail_turns": keep_count,
        "semantic_tail_turns": semantic_count,
        "omitted_group_hashes": omitted_group_hashes,
    }


def _block_evidence_rank(content: str, spans) -> int:
    """0 = no GT evidence, 1 = evidence below VERIFIED, 2 = VERIFIED evidence."""
    rank = 0
    for span in spans:
        probe = span.text.strip()
        if probe and probe in content:
            if span.tier == _VERIFIED:
                return 2
            rank = 1
    return rank


def smart_truncate(
    messages: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
    *,
    char_budget: int,
    delivered_spans=(),
) -> None:
    # --- identical char metric to Agent._truncate_if_needed ---
    def total_chars() -> int:
        n = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                n += len(c)
            elif isinstance(c, list):
                for b in c:
                    n += len(b.get("text", "")) + len(b.get("content", ""))
                    for v in (b.get("input") or {}).values():
                        if isinstance(v, str):
                            n += len(v)
        return n

    if total_chars() <= char_budget:
        return

    # Phase 1: drop tool_result content - evidence-bearing blocks are EXEMPT
    # (sealed => seen): any block containing a delivered span is never
    # phase-1 truncated, whatever its tier. Evidence-free blocks keep the
    # stock oldest-first order; with no delivered spans NOTHING is exempt
    # and this is byte-identical to the stock pass.
    candidates: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if b.get("type") == "tool_result" and not str(
                    b.get("content", "")).startswith("[truncated"):
                if _block_evidence_rank(str(b.get("content", "")),
                                        delivered_spans) > 0:
                    continue  # sealed => seen: exempt, fall through to phase 2
                candidates.append(b)

    for b in candidates:
        original_len = len(b.get("content", ""))
        b["content"] = f"[truncated - {original_len} chars dropped]"
        transcript.append({"type": "truncation",
                           "tool_use_id": b.get("tool_use_id"),
                           "dropped_chars": original_len})
        if total_chars() <= char_budget:
            return

    # Phase 2: still over budget - shrink the largest string args of past
    # tool_use blocks (identical to the original).
    for m in messages:
        if not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if b.get("type") != "tool_use":
                continue
            inp = b.get("input") or {}
            for k, v in list(inp.items()):
                if isinstance(v, str) and len(v) > 200 and not v.startswith(
                        "[truncated"):
                    inp[k] = f"[truncated - {len(v)} chars dropped]"
                    transcript.append({"type": "truncation",
                                       "tool_use_id": b.get("id"),
                                       "dropped_chars": len(v)})
                    if total_chars() <= char_budget:
                        return
