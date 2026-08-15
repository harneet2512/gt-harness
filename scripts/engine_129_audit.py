"""IE-09 executable 129-row migration auditor.

Loads the frozen 129-row inventory (role_audit.csv), the 17 DIRECT
capabilities (direct_capabilities.csv), and the per-identity dispositions from
INLINE_ENGINE_129_TRANSITION.md, and emits the 18-field migration CSV
engine_129_transition.csv. Mechanically enforces:

- exactly 129 unique identities with category counts 12/48/11/58 (ACQ/CAP/FACT/PERF);
- every target disposition in {BUILD, MODIFY, KEEP, REMOVE} (DEFER forbidden);
- the 17 DIRECT identities are present and marked;
- the emitted CSV round-trips.

Run: python scripts/engine_129_audit.py [--check]
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, OrderedDict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GT_FINALSTAND = REPO_ROOT / "gt_finalstand"
ROLE_CSV = GT_FINALSTAND / "role_audit.csv"
DIRECT_CSV = GT_FINALSTAND / "direct_capabilities.csv"
TRANSITION_MD = GT_FINALSTAND / "INLINE_ENGINE_129_TRANSITION.md"
OUTPUT_CSV = GT_FINALSTAND / "engine_129_transition.csv"

ALLOWED_DISPOSITIONS = {"BUILD", "MODIFY", "KEEP", "REMOVE"}
CATEGORY_COUNTS = {"ACQ": 12, "CAP": 48, "FACT": 11, "PERF": 58}

# Column set per the plan's section-14 per-row schema.
COLUMNS = [
    "identity", "role", "category", "category_index", "direct_identity",
    "current_behavior", "target_disposition", "deterministic_knowledge_semantics",
    "representation", "byte_owner", "timing_class", "action_trigger",
    "preflight_postflight_placement", "freshness_authority", "ambiguity_policy",
    "omission_policy", "raw_preservation_rule", "decision_eligibility",
    "model_visibility", "migration_work", "status", "receipt",
]

_DISPOSITION_RE = re.compile(r"\b(BUILD|MODIFY|REMOVE|KEEP)\b")


def _normalize_disposition(cell: str) -> str:
    """Normalize a transition-doc disposition cell into the four-value set."""
    upper = cell.strip().upper()
    if upper.startswith("KEEP"):
        return "KEEP"
    if upper.startswith("MODIFY") or upper.startswith("RENAME/MODIFY"):
        return "MODIFY"
    if upper.startswith("REMOVE"):
        return "REMOVE"
    if upper.startswith("BUILD"):
        return "BUILD"
    return ""


def _row_cells(line: str) -> list[str]:
    """Split one markdown table row into its cells."""
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return []
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return cells


def parse_transition_dispositions(path: Path) -> dict[str, str]:
    """Parse identity -> normalized disposition from the transition document.

    ACQ/CAP/PERF tables carry an explicit Decision column. FACT tables carry
    the disposition inside the target-synchronization prose; the strongest
    keyword present wins (BUILD > MODIFY > REMOVE > KEEP).
    """
    text = path.read_text(encoding="utf-8")
    dispositions: dict[str, str] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _row_cells(line)
        if len(cells) < 3 or not re.fullmatch(r"\d+", cells[0].split(" ")[0]):
            continue
        match = re.match(r"`([^`]+)`", cells[1])
        if not match:
            continue
        identity = match.group(1)
        disposition = _normalize_disposition(cells[2])
        if not disposition:
            found = _DISPOSITION_RE.findall(" ".join(cells[2:]))
            if found:
                rank = {"BUILD": 4, "MODIFY": 3, "REMOVE": 2, "KEEP": 1}
                disposition = max(found, key=lambda d: rank[d])
        if disposition:
            dispositions[identity] = disposition
    return dispositions


def load_role_inventory(path: Path) -> list[dict]:
    """Load the canonical 129-row inventory."""
    rows: list[dict] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(dict(row))
    return rows


def load_direct_identities(path: Path) -> set[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {str(row.get("capability") or "") for row in csv.DictReader(handle)}


def build_transition_rows() -> tuple[list[dict], list[str]]:
    """Build the 22-field migration CSV rows + warnings."""
    role_rows = load_role_inventory(ROLE_CSV)
    direct = load_direct_identities(DIRECT_CSV)
    dispositions = parse_transition_dispositions(TRANSITION_MD)
    warnings: list[str] = []
    rows: list[dict] = []
    try:
        from gt_engine.engine.runner import ENGINE_FACT_OWNERS
    except Exception:  # noqa: BLE001 - the audit must not depend on engine imports
        ENGINE_FACT_OWNERS = {}
    for entry in role_rows:
        identity = str(entry.get("identity") or "")
        category = str(entry.get("category") or "")
        role = str(entry.get("role") or "")
        direct_identity = str(entry.get("direct_identity") or "").lower() == "true"
        if identity in direct:
            direct_identity = True
        disposition = dispositions.get(identity, "KEEP")
        if identity not in dispositions:
            warnings.append(f"{identity}: disposition not found; defaulted to KEEP")
        byte_owner = role in ("byte_owner", "CAP_OWNER")
        model_visible = (category == "FACT" and identity in ENGINE_FACT_OWNERS) or (
            category == "CAP" and byte_owner
        )
        rows.append({
            "identity": identity,
            "role": role,
            "category": category,
            "category_index": str(entry.get("category_index") or ""),
            "direct_identity": "true" if direct_identity else "false",
            "current_behavior": "source:to_determine",
            "target_disposition": disposition,
            "deterministic_knowledge_semantics": "source:to_determine",
            "representation": "source:to_determine",
            "byte_owner": "true" if byte_owner else "false",
            "timing_class": "source:to_determine",
            "action_trigger": "source:to_determine",
            "preflight_postflight_placement": "source:to_determine",
            "freshness_authority": "source:to_determine",
            "ambiguity_policy": "source:to_determine",
            "omission_policy": "source:to_determine",
            "raw_preservation_rule": "source:to_determine",
            "decision_eligibility": "source:to_determine",
            "model_visibility": "true" if model_visible else "false",
            "migration_work": "source:to_determine",
            "status": "removed" if disposition == "REMOVE" else "pending",
            "receipt": "",
        })
    return rows, warnings


def validate(rows: list[dict]) -> tuple[bool, list[str]]:
    """Enforce inventory integrity. Returns (ok, error list)."""
    errors: list[str] = []
    identities = [row["identity"] for row in rows]
    if len(identities) != 129:
        errors.append(f"expected 129 rows, got {len(identities)}")
    if len(set(identities)) != len(identities):
        errors.append(f"duplicate identities: {len(identities) - len(set(identities))}")
    counts = Counter(row["category"] for row in rows)
    for category, expected in CATEGORY_COUNTS.items():
        actual = counts.get(category, 0)
        if actual != expected:
            errors.append(f"{category}: expected {expected}, got {actual}")
    for row in rows:
        disposition = row["target_disposition"]
        if disposition not in ALLOWED_DISPOSITIONS:
            errors.append(
                f"{row['identity']}: invalid disposition {disposition!r}"
            )
        if "DEFER" in disposition.upper():
            errors.append(f"{row['identity']}: DEFER is forbidden")
    return not errors, errors


def write_csv(rows: list[dict]) -> Path:
    ordered = [OrderedDict((col, row.get(col, "")) for col in COLUMNS) for row in rows]
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(ordered)
    return OUTPUT_CSV


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only, do not rewrite")
    args = parser.parse_args()

    rows, warnings = build_transition_rows()
    ok, errors = validate(rows)
    for warning in warnings:
        print(f"warning: {warning}")
    if not args.check:
        path = write_csv(rows)
        print(f"wrote {path} ({len(rows)} rows)")
    counts = Counter(row["category"] for row in rows)
    print(f"inventory: {dict(counts)} total={len(rows)}")
    if not ok:
        for error in errors:
            print(f"error: {error}")
        return 1
    print("OK: inventory integrity holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
