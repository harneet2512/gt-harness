"""IE-09 historical 129-row structural-inventory auditor.

Loads the frozen 129-row inventory (role_audit.csv), the 17 DIRECT
capabilities (direct_capabilities.csv), and the committed transition rows.
This inventory is legacy crosswalk evidence, not semantic implementation proof
for the active 18-direct-feature product. Mechanically enforces:

- exactly 129 unique identities with category counts 12/48/11/58 (ACQ/CAP/FACT/PERF);
- every target disposition in {BUILD, MODIFY, KEEP, REMOVE} (DEFER forbidden);
- the 17 DIRECT identities are present and marked;
- every transition row exactly matches its canonical role/category/direct ID.

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


def load_transition_dispositions(path: Path = OUTPUT_CSV) -> dict[str, str]:
    """Load the committed transition inventory used by the live audit.

    ``INLINE_ENGINE_129_TRANSITION.md`` was intentionally removed when the
    repository's internal planning documents were retired.  Keeping it as an
    implicit runtime dependency made a clean checkout fail during test
    collection.  The checked-in CSV is the executable inventory and already
    carries the normalized four-value disposition for every identity.
    """
    rows = load_transition_rows(path)
    dispositions: dict[str, str] = {}
    for row in rows:
        identity = str(row.get("identity") or "")
        if not identity or identity in dispositions:
            raise ValueError(f"invalid or duplicate transition identity: {identity!r}")
        dispositions[identity] = _normalize_disposition(
            str(row.get("target_disposition") or "")
        )
    return dispositions


def load_transition_rows(path: Path = OUTPUT_CSV) -> list[dict[str, str]]:
    """Load the checked-in historical crosswalk without reconstructing it."""

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            raise ValueError(
                f"transition columns differ: expected {COLUMNS!r}, got {reader.fieldnames!r}"
            )
        return [dict(row) for row in reader]


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
    """Cross-check and return the committed 22-field historical inventory."""
    role_rows = load_role_inventory(ROLE_CSV)
    direct = load_direct_identities(DIRECT_CSV)
    rows = load_transition_rows()
    warnings: list[str] = []
    role_by_id = {str(row.get("identity") or ""): row for row in role_rows}
    transition_ids = {str(row.get("identity") or "") for row in rows}
    if transition_ids != set(role_by_id):
        warnings.append("transition identities do not exactly match role inventory")
    for row in rows:
        identity = str(row.get("identity") or "")
        canonical = role_by_id.get(identity)
        if canonical is None:
            continue
        for field in ("role", "category", "category_index", "direct_identity"):
            if str(row.get(field) or "") != str(canonical.get(field) or ""):
                warnings.append(
                    f"{identity}: {field} differs from canonical role inventory"
                )
        expected_direct = identity in direct
        if (str(row.get("direct_identity") or "").lower() == "true") != expected_direct:
            warnings.append(f"{identity}: direct identity differs from direct inventory")
    return rows, warnings


def validate(rows: list[dict], *, crosswalk_errors: tuple[str, ...] = ()) -> tuple[bool, list[str]]:
    """Enforce structural crosswalk integrity; never claim semantic completeness."""
    errors: list[str] = []
    errors.extend(crosswalk_errors)
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
    ok, errors = validate(rows, crosswalk_errors=tuple(warnings))
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
    print("OK: historical structural inventory integrity holds")
    print("NOTE: this is not active-product semantic completeness evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
