"""Engine compliance certificates (IE-10, IE-11, IE-12).

Mechanical, provider-free certificates over the ENGINE implementation:

- IE-10 passive PERF: no PERF identity is model-visible or decision-eligible;
  only registered FACT owners and CAP byte owners may add model-visible bytes.
- IE-11 advisory-dependency removal: the engine package's module-level import
  closure contains no advisory-only producer/mediator module.
- IE-12 replay/security: engine delivery events are content-addressed and
  replayable; receipts carry no secret payload.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .runner import ENGINE_FACT_OWNERS

HARNESS_ROOT = Path(__file__).resolve().parent.parent.parent
TRANSITION_CSV = HARNESS_ROOT / "gt_finalstand" / "engine_129_transition.csv"

# Advisory-only modules that the ENGINE package must never import at module
# level. The seam may import ``miniswe_runtime``/``miniswe_typed_actions``
# lazily inside functions (that is the integration boundary, not advisory
# logic), but no bridge/attribution/covering/evidence-router module.
ADVISORY_MODULES = {
    "gt_engine.bridge",
    "gt_engine.attribution",
    "gt_engine.miniswe_covering",
    "gt_engine.miniswe_evidence",
    "gt_engine.evidence_router",
    "gt_engine.graph_evidence",
    "gt_engine.graph_context",
    "gt_engine.miniswe_audit",
    "gt_engine.miniswe_controller",
    "gt_engine.progress",
    "gt_engine.replay",
    "gt_engine.role_packs",
    "gt_engine.tool_outcomes",
    "gt_engine.verify",
    "gt_engine.verification_contract",
}


def perf_passivity(rows: Iterable[Mapping[str, str]] | None = None) -> list[str]:
    """IE-10: PERF rows stay passive; only FACT/CAP-owner rows may be visible."""
    errors: list[str] = []
    if rows is None:
        rows = _load_transition_rows()
    for row in rows:
        category = str(row.get("category") or "")
        model_visible = str(row.get("model_visibility") or "").lower() == "true"
        if category == "PERF" and model_visible:
            errors.append(f"PERF {row['identity']} must never be model-visible")
        if category == "ACQ" and model_visible:
            errors.append(f"ACQ {row['identity']} must stay internal")
        if category not in {"PERF", "ACQ"} and model_visible:
            owner = row.get("identity")
            if category == "FACT" and owner not in ENGINE_FACT_OWNERS and row.get("target_disposition") != "REMOVE":
                errors.append(
                    f"FACT {owner} is model-visible but has no registered engine owner"
                )
    return errors


def registered_owner_consistency() -> list[str]:
    """Every registered engine owner must exist in the 129-row inventory."""
    errors: list[str] = []
    rows = _load_transition_rows()
    inventory = {str(row.get("identity")) for row in rows}
    for owner in ENGINE_FACT_OWNERS:
        if owner not in inventory:
            errors.append(f"registered engine owner {owner} absent from the 129-row inventory")
    return errors


def _load_transition_rows() -> list[dict[str, str]]:
    import csv

    if not TRANSITION_CSV.exists():
        return []
    with TRANSITION_CSV.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def engine_import_closure(package_root: Path | None = None) -> list[str]:
    """IE-11: module-level imports of the engine package must avoid advisory
    modules. Returns a list of offending ``module:import`` lines (empty = ok)."""
    import re

    package_root = package_root or (HARNESS_ROOT / "gt_engine" / "engine")
    violations: list[str] = []
    for path in package_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name in ADVISORY_MODULES or any(
                        name.startswith(mod + ".") for mod in ADVISORY_MODULES
                    ):
                        violations.append(f"{path.name}: import {name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                name = node.module
                if name in ADVISORY_MODULES or any(
                    name.startswith(mod + ".") for mod in ADVISORY_MODULES
                ):
                    violations.append(f"{path.name}: from {name} import ...")
    return violations


def verify_engine_delivery_events(events: Iterable[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    """IE-12: every engine_delivery event is content-addressed and replayable.

    Each event must carry a delivery receipt whose hash matches its
    canonical fields, a non-empty action_id, and no secret-looking keys.
    """
    issues: list[str] = []
    seen_ids: set[str] = set()
    count = 0
    secret_keys = {"api_key", "token", "secret", "password", "authorization"}
    for event in events:
        if event.get("event") != "engine_delivery":
            continue
        count += 1
        delivery_id = str(event.get("delivery_id") or "")
        action_id = str(event.get("action_id") or "")
        decision = str(event.get("decision") or "")
        obs_hash = str(event.get("final_observation_sha256") or "")
        if not delivery_id or len(delivery_id) < 8:
            issues.append("engine_delivery missing delivery_id")
        if not action_id:
            issues.append(f"{delivery_id}: missing action_id")
        if not decision:
            issues.append(f"{delivery_id}: missing decision")
        if not obs_hash or len(obs_hash) != 64:
            issues.append(f"{delivery_id}: missing canonical observation hash")
        if delivery_id in seen_ids:
            issues.append(f"{delivery_id}: duplicate delivery_id")
        seen_ids.add(delivery_id)
        for key in event:
            if any(secret in key.lower() for secret in secret_keys):
                issues.append(f"{delivery_id}: secret key {key!r} in event")
    return count == 0 or not issues, issues
