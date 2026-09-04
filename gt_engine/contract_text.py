"""Render a symbol contract as the text an embedding is taken over.

Separated from the store because the two answer different questions.  This
module answers "what does this symbol do, said the same way every time"; the
store answers "has that changed since the vector was made".

The one rule the rendering must never break: **no line number reaches the
text.**  Not the symbol's own range, not a fact's ``line`` column.  A symbol
that moved down the file has not changed, and a rendering that leaked its
position would re-embed the entire repository on every formatter run -- exactly
the failure embedding the contract instead of the source exists to avoid.
:func:`gt_engine.contract.contract_digest` deliberately *does* cover the lines,
which is why it cannot be used as the cache key and this rendering exists.

Order inside a field is the contract's own order, which is by line then property
id.  That is a line-*derived* order, not a line-*bearing* one: a reformat shifts
every line by the same amount and so preserves it.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

__all__ = ["KEY_SCHEMA", "contract_text", "invalidation_key", "text_digest"]

# Versioned because it is a cache key: bumping it re-embeds every symbol, which
# is the correct and only response to changing how a key is computed.
KEY_SCHEMA = "gt.contract_embedding_key.v1"

# Multiple fingerprint rows on one node have never been observed; if one ever
# appears, every value is kept in row order rather than one being chosen.
FINGERPRINT_JOINER = "\x1f"


def _param_line(fact: Mapping[str, Any]) -> str:
    parts = ["param", str(fact.get("name") or "")]
    if fact.get("type"):
        parts.append(f":: {fact['type']}")
    required = fact.get("required")
    if required is True:
        parts.append("required")
    elif required is False:
        parts.append(f"optional default {fact.get('default') or ''}".rstrip())
    return " ".join(part for part in parts if part)


def _guard_line(fact: Mapping[str, Any]) -> str:
    parts = ["guard"]
    if fact.get("action"):
        parts.append(str(fact["action"]))
    parts.append(f"when {fact.get('condition') or ''}".rstrip())
    if fact.get("effect"):
        parts.append(f"then {fact['effect']}")
    return " ".join(parts)


def _side_effect_line(fact: Mapping[str, Any]) -> str:
    effect = fact.get("effect")
    target = str(fact.get("target") or "")
    head = f"side_effect {effect}" if effect else "side_effect"
    return f"{head} {target}".strip()


def _boundary_line(fact: Mapping[str, Any]) -> str:
    check = fact.get("check")
    expression = fact.get("expression") or ""
    return f"boundary {check} {expression}".strip() if check else f"boundary {expression}".strip()


def _data_flow_line(fact: Mapping[str, Any]) -> str:
    sinks = ", ".join(str(sink) for sink in fact.get("sinks") or ())
    source = fact.get("source") or ""
    return f"data_flow {source} -> {sinks}".rstrip(" ->")


def contract_text(symbol_contract: Mapping[str, Any]) -> str:
    """Render a contract as the deterministic text that gets embedded.

    Carries no line number anywhere -- not the symbol's range, not a fact's
    ``line`` -- so a pure relocation produces byte-identical text.  Fact order
    within a field is the contract's own order, which is by line then property
    id; a reformat shifts every line by the same amount and so preserves it.

    Property kinds outside the contract (``caller_usage``, ``field_read``,
    ``call_order`` and the rest) never reach the text, for the same reason
    :mod:`gt_engine.contract` does not project them: the contract carries only
    claims its schema describes.
    """
    symbol = symbol_contract["symbol"]
    lines = [
        f"{symbol['kind']} {symbol['qualified_name']}",
        f"file {symbol['file_path']}",
    ]
    lines.extend(_param_line(fact) for fact in symbol_contract["params"])

    returns = symbol_contract["returns"]
    if returns.get("declared_type"):
        lines.append(f"returns {returns['declared_type']}")
    for fact in returns.get("shapes") or ():
        detail = fact.get("detail")
        lines.append(
            f"return_shape {fact.get('shape') or ''}"
            + (f" | {detail}" if detail else "")
        )

    lines.extend(_guard_line(fact) for fact in symbol_contract["guards"])
    lines.extend(_boundary_line(fact) for fact in symbol_contract["boundaries"])
    lines.extend(_side_effect_line(fact) for fact in symbol_contract["side_effects"])
    lines.extend(_data_flow_line(fact) for fact in symbol_contract["data_flow"])

    visibility = symbol_contract.get("visibility")
    if visibility is not None:
        lines.append(f"visibility {visibility['value']}")
    return "\n".join(lines)


def text_digest(text: str) -> str:
    """The digest of a rendered contract: the text half of the cache key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def invalidation_key(fingerprint: str, text: str) -> str:
    """The two-part cache key: the producer's fingerprint and the text digest.

    Both halves are needed and neither is redundant.  The fingerprint is what
    survives a reformat; the text digest is what catches a behaviour change the
    fingerprint's branch-and-call summary cannot see, such as a changed return
    shape.  A stored vector is reusable only when both agree.
    """
    material = FINGERPRINT_JOINER.join((KEY_SCHEMA, fingerprint, text_digest(text)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
