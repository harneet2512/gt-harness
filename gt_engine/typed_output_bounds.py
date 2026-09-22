"""Deterministic byte bounds for a compiled typed observation.

A typed answer reaches the model in three places: ``direct_answer``, the
canonical artifact's escaped ``evidence.direct_answer_json`` and the honesty
envelope's ``payload``. The artifact also carries ``anchors`` and
``witnesses`` lists. A byte ceiling that only pops rows from a *list* answer
never bounds the dict answers every graph kind returns, so this module
shrinks list fields wherever they sit inside the answer, keeps all three
projections identical, and records every omission it makes.

Nothing here changes the envelope's shape: the same keys are present before
and after, only list lengths and ``omissions`` differ.
"""
from __future__ import annotations

import json
from typing import Any

_MAX_LIST_DEPTH = 4
LITERAL_MATCH_LIMIT = 20
LITERAL_LINE_MAX_BYTES = 256


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _truncate_text(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore")


def _list_paths(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, list]]:
    """Every non-empty list inside ``value``, addressed by a stable path."""
    found: list[tuple[str, list]] = []
    if depth > _MAX_LIST_DEPTH:
        return found
    if isinstance(value, dict):
        for key in sorted(value):
            child = value[key]
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, list) and child:
                found.append((path, child))
            found.extend(_list_paths(child, path, depth + 1))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_list_paths(child, f"{prefix}[{index}]", depth + 1))
    return found


def _add_omission(evidence: dict, omission: str) -> None:
    omissions = list(evidence.get("omissions") or ())
    if omission not in omissions:
        omissions.append(omission)
    evidence["omissions"] = omissions


def cap_literal_search(answer: Any, evidence: dict) -> bool:
    """Apply the literal-search match and per-line caps to a canonical answer."""
    if not isinstance(answer, dict) or not isinstance(answer.get("matches"), list):
        return False
    capped = False
    matches = answer["matches"]
    if len(matches) > LITERAL_MATCH_LIMIT:
        answer["matches"] = matches[:LITERAL_MATCH_LIMIT]
        _add_omission(evidence, "query_match_limit")
        capped = True
    for match in answer["matches"]:
        text = match.get("line_text") if isinstance(match, dict) else None
        if isinstance(text, str):
            short = _truncate_text(text, LITERAL_LINE_MAX_BYTES)
            if short != text:
                match["line_text"] = short
                _add_omission(evidence, "query_line_limit")
                capped = True
    anchors = evidence.get("anchors")
    if capped and isinstance(anchors, list) and len(anchors) > LITERAL_MATCH_LIMIT:
        evidence["anchors"] = anchors[:LITERAL_MATCH_LIMIT]
    return capped


def _sync_projections(result: dict, answer: Any) -> None:
    result["direct_answer"] = answer
    evidence = result.get("evidence")
    if isinstance(evidence, dict) and "direct_answer_json" in evidence:
        evidence["direct_answer_json"] = canonical_json(answer).decode("utf-8")
    elif isinstance(evidence, dict) and "answer" in evidence:
        evidence["answer"] = answer
    honesty = result.get("honesty")
    if isinstance(honesty, dict) and "payload" in honesty:
        honesty["payload"] = answer


def _shrink_largest_list(result: dict, answer: Any) -> str:
    """Halve the largest list among the answer and evidence lists.

    Returns the path that was shrunk, or "" when nothing is left to shrink.
    """
    evidence = result.get("evidence")
    candidates: list[tuple[int, str, list]] = [
        (len(canonical_json(items)), f"answer.{path}", items)
        for path, items in _list_paths(answer)
    ]
    if isinstance(answer, list) and answer:
        candidates.append((len(canonical_json(answer)), "answer", answer))
    if isinstance(evidence, dict):
        for key in ("anchors", "witnesses"):
            items = evidence.get(key)
            if isinstance(items, list) and items:
                candidates.append((len(canonical_json(items)), f"evidence.{key}", items))
    if not candidates:
        return ""
    # Largest first; the path breaks ties so the choice is deterministic.
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _, path, items = candidates[0]
    keep = len(items) // 2
    del items[keep:]
    return path


def bound_compiled_observation(
    result: dict, *, max_bytes: int, kind: str = ""
) -> tuple[dict, list[str]]:
    """Bound ``result`` to ``max_bytes`` of canonical JSON, in place.

    Returns the result and the omissions added. The literal-search caps apply
    whatever the size; the byte bound then halves the largest list field
    (inside the answer or the evidence anchor/witness lists) until the whole
    observation fits, recording ``query_result_truncated:<path>:<kept>/<total>``
    per field. An answer that cannot fit even with every list emptied is
    withheld with ``query_result_unbounded_payload``.
    """
    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        return result, []
    before = list(evidence.get("omissions") or ())
    answer = result.get("direct_answer")
    if kind == "exact_literal_search" and cap_literal_search(answer, evidence):
        _sync_projections(result, answer)
    if len(canonical_json(result)) > max_bytes:
        _add_omission(evidence, "query_result_byte_limit")
        totals = _list_lengths(result, answer)
        shrunk: set[str] = set()
        while len(canonical_json(result)) > max_bytes:
            path = _shrink_largest_list(result, answer)
            if not path:
                break
            shrunk.add(path)
            _sync_projections(result, answer)
        kept = _list_lengths(result, answer)
        for path in sorted(shrunk):
            _add_omission(
                evidence,
                f"query_result_truncated:{path}:{kept.get(path, 0)}/{totals.get(path, 0)}",
            )
        if len(canonical_json(result)) > max_bytes:
            _sync_projections(result, None)
            _add_omission(evidence, "query_result_unbounded_payload")
    added = [item for item in evidence.get("omissions") or () if item not in before]
    return result, added


def _list_lengths(result: dict, answer: Any) -> dict[str, int]:
    """Lengths of every shrinkable list, keyed like ``_shrink_largest_list``."""
    lengths = {f"answer.{path}": len(items) for path, items in _list_paths(answer)}
    if isinstance(answer, list):
        lengths["answer"] = len(answer)
    evidence = result.get("evidence")
    if isinstance(evidence, dict):
        for key in ("anchors", "witnesses"):
            if isinstance(evidence.get(key), list):
                lengths[f"evidence.{key}"] = len(evidence[key])
    return lengths


__all__ = [
    "LITERAL_LINE_MAX_BYTES",
    "LITERAL_MATCH_LIMIT",
    "bound_compiled_observation",
    "canonical_json",
    "cap_literal_search",
]
