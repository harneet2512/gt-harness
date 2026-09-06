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
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .output_evidence import EvidenceStore
from .request_history import store_history_evidence

_VERIFIED = "VERIFIED"


class ContextAssemblyError(ValueError):
    """The selected context units cannot be assembled without ambiguity."""


def _history_marker(reference: Mapping[str, Any], tool_call_id: str) -> str:
    body = {**dict(reference), "tool_call_id": tool_call_id}
    return "[GT_HISTORY_EVIDENCE " + json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ) + "]"


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
    artifact_store: EvidenceStore | None = None,
    protected_tool_ids: frozenset[str] = frozenset(),
    references: list[dict[str, Any]] | None = None,
) -> None:
    for message in messages:
        if (
            message.get("role") == "tool"
            and isinstance(message.get("content"), str)
        ):
            tool_call_id = str(message.get("tool_call_id") or "")
            value = message["content"]
            if len(value) > tool_output_chars and tool_call_id not in protected_tool_ids:
                if artifact_store is not None:
                    reference = store_history_evidence(
                        artifact_store, value.encode("utf-8"), kind="tool_result"
                    )
                    message["content"] = _history_marker(reference, tool_call_id)
                    if references is not None and reference not in references:
                        references.append(reference)
                else:
                    head = value[: tool_output_chars // 2]
                    tail = value[-tool_output_chars // 2:]
                    message["content"] = (
                        f"{head}\n[older tool output elided: "
                        f"{len(value) - tool_output_chars} chars]\n{tail}"
                    )
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                value = str(block.get("content") or "")
                tool_call_id = str(block.get("tool_use_id") or "")
                if len(value) > tool_output_chars and tool_call_id not in protected_tool_ids:
                    if artifact_store is not None:
                        reference = store_history_evidence(
                            artifact_store, value.encode("utf-8"), kind="tool_result"
                        )
                        block["content"] = _history_marker(reference, tool_call_id)
                        if references is not None and reference not in references:
                            references.append(reference)
                    else:
                        head = value[: tool_output_chars // 2]
                        tail = value[-tool_output_chars // 2:]
                        block["content"] = (
                            f"{head}\n[older tool output elided: "
                            f"{len(value) - tool_output_chars} chars]\n{tail}"
                        )


def render_context_units(
    units: Iterable[Mapping[str, Any]],
    *,
    byte_budget: int,
    artifact_store: EvidenceStore | None = None,
) -> tuple[str, dict[str, Any]]:
    """Render complete selected facts with explicit, auditable supersession.

    Producers retain selection authority through ``priority``. This compiler
    only removes units explicitly replaced by a later unit with the same
    supersession key, then admits whole UTF-8 units. It never slices facts.
    """

    if isinstance(byte_budget, bool) or not isinstance(byte_budget, int) or byte_budget < 1:
        raise ContextAssemblyError("invalid_byte_budget")
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    references: dict[str, dict[str, Any]] = {}
    for ordinal, value in enumerate(units):
        if not isinstance(value, Mapping):
            raise ContextAssemblyError("context_unit_object_required")
        unit_id = str(value.get("unit_id") or "").strip()
        key = str(value.get("supersession_key") or "").strip()
        content = value.get("content")
        priority = value.get("priority")
        supersedes = value.get("supersedes", [])
        if (
            not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", unit_id)
            or not key
            or not isinstance(content, str)
            or not content
        ):
            raise ContextAssemblyError("context_unit_invalid")
        if unit_id in identities:
            raise ContextAssemblyError("duplicate_context_unit_identity")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ContextAssemblyError("context_unit_priority_invalid")
        if not isinstance(supersedes, list) or any(
            not isinstance(item, str) or not item for item in supersedes
        ):
            raise ContextAssemblyError("context_unit_supersedes_invalid")
        identities.add(unit_id)
        encoded = content.encode("utf-8")
        if artifact_store is not None:
            references[unit_id] = store_history_evidence(
                artifact_store, encoded, kind="context_unit"
            )
        rows.append({
            "unit_id": unit_id,
            "supersession_key": key,
            "content": content,
            "priority": priority,
            "supersedes": tuple(supersedes),
            "ordinal": ordinal,
        })

    active: dict[str, dict[str, Any]] = {}
    seen: dict[str, dict[str, Any]] = {}
    superseded: list[dict[str, str]] = []
    for row in rows:
        for target in row["supersedes"]:
            target_row = seen.get(target)
            if target_row is None:
                raise ContextAssemblyError("superseded_context_unit_unknown")
            if target_row["supersession_key"] != row["supersession_key"]:
                raise ContextAssemblyError("cross_key_supersession_forbidden")
        previous = active.get(row["supersession_key"])
        if previous is not None:
            if previous["unit_id"] not in row["supersedes"]:
                raise ContextAssemblyError("implicit_supersession_forbidden")
            superseded.append({
                "unit_id": previous["unit_id"],
                "superseded_by": row["unit_id"],
            })
        active[row["supersession_key"]] = row
        seen[row["unit_id"]] = row

    rendered: list[str] = []
    rendered_bytes = 0
    selected: list[str] = []
    omitted: list[dict[str, Any]] = []
    for row in sorted(active.values(), key=lambda item: (-item["priority"], item["ordinal"])):
        block = f"[GT_CONTEXT_UNIT:{row['unit_id']}]\n{row['content']}"
        separator = "\n\n" if rendered else ""
        candidate_bytes = len((separator + block).encode("utf-8"))
        if rendered_bytes + candidate_bytes > byte_budget:
            omission: dict[str, Any] = {
                "unit_id": row["unit_id"], "reason": "byte_budget_exceeded"
            }
            if row["unit_id"] in references:
                omission["reference"] = references[row["unit_id"]]
            omitted.append(omission)
            continue
        rendered.append(block)
        rendered_bytes += candidate_bytes
        selected.append(row["unit_id"])
    return "\n\n".join(rendered), {
        "schema": "gt.context_assembly.v1",
        "byte_budget": byte_budget,
        "rendered_bytes": rendered_bytes,
        "selected": selected,
        "omitted": omitted,
        "superseded": superseded,
        "unit_references": references,
    }


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
    artifact_store: EvidenceStore | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Construct a bounded provider view without mutating durable history."""
    raw_chars = message_chars(messages)
    durable = copy.deepcopy(messages)
    if not durable:
        durable = [{"role": "user", "content": ""}]
    anchor = durable[0]
    anchor_content = anchor.get("content")
    suffix = f"\n\n[deterministic GT state]\n{checkpoint}".rstrip() if checkpoint else ""
    if suffix and isinstance(anchor_content, str):
        anchor["content"] = anchor_content.rstrip() + suffix
    elif suffix and isinstance(anchor_content, list):
        anchor["content"] = [*anchor_content, {"type": "text", "text": suffix}]
    elif suffix:
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
    original_groups = copy.deepcopy(groups)

    protected_ids: set[str] = set()
    for message in groups[-1] if groups else ():
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            if isinstance(call, dict) and call.get("id"):
                protected_ids.add(str(call["id"]))
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id")
                ):
                    protected_ids.add(str(block["id"]))
    protected_tool_ids = frozenset(protected_ids)
    references: list[dict[str, Any]] = []

    def group_hash(group: list[dict[str, Any]]) -> str:
        encoded = json.dumps(
            group,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", "surrogatepass")
        return hashlib.sha256(encoded).hexdigest()

    target_budget = min(
        int(char_budget),
        int(target_char_budget)
        if target_char_budget is not None
        else int(char_budget),
    )
    full_view = [anchor] + [item for group in groups for item in group]
    _bound_kept_blocks(
        full_view, tool_output_chars=tool_output_chars,
        artifact_store=artifact_store, protected_tool_ids=protected_tool_ids,
        references=references,
    )
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
            "evidence_references": references,
            "history_archive_reference": None,
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
            artifact_store=artifact_store,
            protected_tool_ids=protected_tool_ids,
            references=references,
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
            artifact_store=artifact_store,
            protected_tool_ids=protected_tool_ids,
            references=references,
        )
        if message_chars(candidate_view) > target_budget:
            break
        selected_indices.add(earliest)
        earliest -= 1
    selected = [groups[index] for index in sorted(selected_indices)]
    keep_count = len(selected)
    view = [anchor] + [item for group in selected for item in group]
    _bound_kept_blocks(
        view, tool_output_chars=tool_output_chars,
        artifact_store=artifact_store, protected_tool_ids=protected_tool_ids,
        references=references,
    )
    omitted = len(durable) - len(view)
    omitted_group_hashes = [
        group_hash(group)
        for index, group in enumerate(original_groups)
        if index not in selected_indices
    ]
    history_archive_reference: dict[str, Any] | None = None
    if artifact_store is not None and omitted_group_hashes:
        archived_groups = []
        for index, group in enumerate(original_groups):
            if index in selected_indices:
                continue
            encoded_group = json.dumps(
                group, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8", "surrogatepass")
            archived_groups.append({
                "group_sha256": omitted_group_hashes[len(archived_groups)],
                "reference": store_history_evidence(
                    artifact_store, encoded_group, kind="provider_history_group"
                ),
            })
        archive = json.dumps(
            {"schema": "gt.provider_history_archive.v1", "groups": archived_groups},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8")
        history_archive_reference = store_history_evidence(
            artifact_store, archive, kind="provider_history_archive"
        )
        marker = "[GT_HISTORY_ARCHIVE " + json.dumps(
            history_archive_reference, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True,
        ) + "]"
        anchor_content = anchor.get("content")
        if isinstance(anchor_content, str):
            anchor["content"] = anchor_content.rstrip() + "\n\n" + marker
        else:
            anchor["content"] = [
                *(anchor_content or ()), {"type": "text", "text": marker}
            ]

    # If the structural tail still exceeds the budget, retain its pairing but
    # reduce inline output/arguments further. Exact bytes remain durable.
    inline_limit = max(200, min(tool_output_chars, char_budget // 8))
    while message_chars(view) > char_budget and inline_limit > 200:
        inline_limit = max(200, inline_limit // 2)
        _bound_kept_blocks(
            view, tool_output_chars=inline_limit,
            artifact_store=artifact_store,
            protected_tool_ids=protected_tool_ids,
            references=references,
        )
    active_chars = message_chars(view)
    return view, {
        "raw_message_chars": raw_chars,
        "active_message_chars": active_chars,
        "compacted": True,
        "omitted_message_count": max(0, omitted),
        "tail_turns": keep_count,
        "semantic_tail_turns": semantic_count,
        "omitted_group_hashes": omitted_group_hashes,
        "evidence_references": references,
        "history_archive_reference": history_archive_reference,
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
