"""Restore advisory plan inputs, never historical execution authority."""
from __future__ import annotations

import hashlib
import json
import types
from dataclasses import asdict, fields, is_dataclass
from typing import Any, get_args, get_origin, get_type_hints

from . import PersistentPlan

# v4: BaselineResult gained test_file_digests, config_sha256 and dependency_sha256.
# v3: PlanRow gained check_basis, check_missing_paths and symbol_basis.
# v2: BaselineResult gained source_revision and after_source_revision.
#
# The decoder below requires EXACT dataclass field-set equality, so the
# serialized shape and this string are the same fact stated twice. A v1
# checkpoint decoded by this build would fail on "checkpoint dataclass fields
# mismatch", which reads like corruption; against a bumped layout it fails on
# "checkpoint task/layout mismatch", which is the truth -- a different format.
#
# Rejection is the whole migration, deliberately. Restoring nothing costs one
# planning call; inventing the two missing fields would mean asserting which
# source revision an older baseline observed, and the only value available is
# the CURRENT workspace, which is precisely the thing those fields exist to
# distinguish from. A checkpoint that cannot say what it saw does not get to
# borrow what we see now.
LAYOUT = "gt.plan_checkpoint.v4"


def _decode(value, annotation):
    if annotation is Any:
        return value
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        for choice in args:
            try:
                return _decode(value, choice)
            except (ValueError, TypeError):
                pass
        raise ValueError("checkpoint union type mismatch")
    if is_dataclass(annotation):
        if not isinstance(value, dict) or set(value) != {f.name for f in fields(annotation)}:
            raise ValueError("checkpoint dataclass fields mismatch")
        hints = get_type_hints(annotation)
        return annotation(**{key: _decode(item, hints[key]) for key, item in value.items()})
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError("checkpoint tuple mismatch")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(item, args[0]) for item in value)
        if len(value) != len(args):
            raise ValueError("checkpoint tuple length mismatch")
        return tuple(_decode(item, kind) for item, kind in zip(value, args, strict=True))
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError("checkpoint mapping mismatch")
        return {(_decode(int(key), int) if args[0] is int and str(int(key)) == key
                 else _decode(key, args[0])): _decode(item, args[1])
                for key, item in value.items()}
    if annotation is float and type(value) is int:
        return float(value)
    if type(value) is not annotation:
        raise ValueError("checkpoint scalar type mismatch")
    return value


def checkpoint_plan(store, plan: PersistentPlan, issue_text: str) -> None:
    payload = json.dumps({"layout": LAYOUT, "plan": asdict(plan)},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    store.put_blob("plan_checkpoints", digest, payload)
    store.append("persistent_plan_checkpoint", layout=LAYOUT, checkpoint_sha256=digest,
                 issue_sha256=hashlib.sha256(issue_text.encode("utf-8", "surrogatepass")).hexdigest(),
                 plan_sha256=hashlib.sha256(plan.canonical_json().encode("utf-8")).hexdigest())


def restore_plan(store, issue_text: str) -> PersistentPlan | None:
    if not store.startup_journal_valid:
        return None
    issue_digest = hashlib.sha256(issue_text.encode("utf-8", "surrogatepass")).hexdigest()
    events = [row for row in store.startup_plan_events if row.get("event") == "persistent_plan_checkpoint"]
    if not events:
        return None
    # Only the original checkpoint is authoritative for the design-revision
    # chain. Never silently fall back to another task or a later partial plan.
    event = events[0]
    try:
        if event.get("layout") != LAYOUT or event.get("issue_sha256") != issue_digest:
            raise ValueError("checkpoint task/layout mismatch")
        digest = event["checkpoint_sha256"]
        if not store.blob_exists("plan_checkpoints", digest):
            raise ValueError("checkpoint content missing or corrupt")
        payload = json.loads((store.root / "plan_checkpoints" / f"{digest}.json").read_bytes())
        if set(payload) != {"layout", "plan"} or payload["layout"] != LAYOUT:
            raise ValueError("checkpoint payload layout mismatch")
        plan = _decode(payload["plan"], PersistentPlan)
        if plan.status not in {"READY", "PARTIAL", "ABSTAINED"}:
            raise ValueError("checkpoint status invalid")
        if hashlib.sha256(plan.canonical_json().encode("utf-8")).hexdigest() != event["plan_sha256"]:
            raise ValueError("checkpoint plan digest mismatch")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        store.append("persistent_plan_restore_rejected", reason=type(exc).__name__)
        return None
    store.append("persistent_plan_restored", checkpoint_sha256=digest,
                 evidence_restored=False, source_revision=plan.inputs.source_revision)
    return plan
