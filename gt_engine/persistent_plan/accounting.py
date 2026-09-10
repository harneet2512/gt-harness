"""One complete accounting of a plan: every row, in every dimension, by name.

Counts without names are unauditable. "17 rows, 9 verified" cannot be checked
against anything -- it cannot even be checked against itself, and the count that
said "verified" turned out to mean "had a command written down". So every bucket
here is a list of row ids first and a number second, and the number is derived
from the list rather than counted separately.

Four dimensions, each a partition. Every row appears exactly once in each:

  symbols   what the graph resolved: existing / name_guess / unmapped
  checks    what the repository held: existing / proposed / unnamed / none
  evidence  what actually ran: proven / passed / failed / deferred / unverified
  exposure  what the model was actually shown

Exposure is the one that is routinely overstated. A row id printed in the plan's
index is retrievable -- the agent can ask for it -- and that is not the same as
having been shown it. The index is cheap and always complete; the rendered block
is capped, so rows past the cap are listed and not delivered. This module counts
a row as model-exposed only when its whole block survived the cap, and reports
the digest of the exact text that was delivered, so the claim can be checked
against the stored rendering rather than believed.
"""
from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping
from typing import Any

ACCOUNTING_SCHEMA = "gt.plan_accounting.v1"

_SYMBOL_BUCKETS = ("existing", "name_guess", "unmapped")
_CHECK_BUCKETS = ("existing", "proposed", "unnamed", "none", "unclassified")
_EVIDENCE_BUCKETS = ("proven", "passed", "failed", "deferred", "unverified")


def _bucket(rows, attribute: str, known: tuple[str, ...], fallback: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {name: [] for name in known}
    for row in rows:
        value = getattr(row, attribute, "") or fallback
        out.setdefault(value if value in known else fallback, []).append(row.row_id)
    return {name: sorted(values) for name, values in out.items()}


def _evidence_bucket(row_id: str, state: str, proven: Collection[str]) -> str:
    """Proof outranks a passing check; a failure outranks both.

    A row can carry evidence in two independent channels -- a bound check that
    ran, and a mapped predicate a receipt turned green. A current failure in
    either is the answer, because neither channel cancels the other's failure.
    """
    if state == "CHECK_FAILED":
        return "failed"
    if row_id in proven:
        return "proven"
    if state == "CHECK_PASSED":
        return "passed"
    if state == "DEFERRED":
        return "deferred"
    return "unverified"


def plan_accounting(
    plan,
    *,
    states: Mapping[str, str] | None = None,
    proven: Collection[str] = (),
    executed: Collection[str] = (),
    rendering: Mapping[str, Any] | None = None,
) -> dict:
    """Name every row in every dimension, with the digest of what was delivered."""
    rows = tuple(plan.rows)
    ids = [row.row_id for row in rows]
    states = dict(states or {})
    proven = {row_id for row_id in proven if row_id in set(ids)}
    executed_ids = sorted({row_id for row_id in executed if row_id in set(ids)})

    evidence: dict[str, list[str]] = {name: [] for name in _EVIDENCE_BUCKETS}
    for row_id in ids:
        evidence[_evidence_bucket(row_id, states.get(row_id, ""), proven)].append(row_id)

    rendering = dict(rendering or {})
    # Indexed means the id was printed and can be retrieved. Complete means the
    # whole row survived the block's length cap. Only the second is exposure.
    indexed = sorted(str(value) for value in rendering.get("indexed_row_ids") or ())
    complete = sorted(str(value) for value in rendering.get("complete_row_block_ids") or ())
    started = sorted(str(value) for value in rendering.get("rendered_requirement_row_ids") or ())
    omitted = sorted(str(value) for value in rendering.get("omitted_requirement_row_ids") or ())

    payload = {
        "schema": ACCOUNTING_SCHEMA,
        "process_id": plan.process_id,
        "plan_digest": hashlib.sha256(
            plan.canonical_json().encode("utf-8", "surrogatepass")
        ).hexdigest(),
        "status": plan.status,
        "rows_total": len(rows),
        "row_ids": sorted(ids),
        "symbols": _bucket(rows, "symbol_basis", _SYMBOL_BUCKETS, "unmapped"),
        "checks": _bucket(rows, "check_basis", _CHECK_BUCKETS, "unclassified"),
        "evidence": {name: sorted(values) for name, values in evidence.items()},
        "executed": executed_ids,
        "exposure": {
            "retrievable_row_ids": indexed,
            "partially_rendered_row_ids": started,
            "model_exposed_row_ids": complete,
            "omitted_row_ids": omitted,
            "rendered_sha256": str(rendering.get("rendered_sha256") or ""),
            # Stated, not implied: a retrievable id is an offer, not a delivery.
            "basis": "complete_row_blocks_only_retrieval_is_not_exposure",
        },
    }
    payload["counts"] = counts_of(payload)
    return payload


def counts_of(payload: Mapping[str, Any]) -> dict[str, int]:
    """Scalars derived from the lists above, never counted independently."""
    out: dict[str, int] = {"rows_total": int(payload["rows_total"])}
    for dimension in ("symbols", "checks", "evidence"):
        for name, values in payload[dimension].items():
            out[f"{dimension}_{name}"] = len(values)
    out["executed"] = len(payload["executed"])
    exposure = payload["exposure"]
    out["retrievable"] = len(exposure["retrievable_row_ids"])
    out["model_exposed"] = len(exposure["model_exposed_row_ids"])
    out["omitted"] = len(exposure["omitted_row_ids"])
    return out
