"""IE-09 audit tests: 129-row inventory integrity + CSV round-trip."""
from __future__ import annotations

from collections import Counter

import pytest

from scripts.engine_129_audit import (
    CATEGORY_COUNTS,
    parse_transition_dispositions,
    validate,
    build_transition_rows,
    load_direct_identities,
    load_role_inventory,
)


@pytest.fixture(scope="module")
def audit():
    rows, warnings = build_transition_rows()
    return rows, warnings


def test_inventory_has_129_unique_rows(audit):
    rows, _ = audit
    identities = [row["identity"] for row in rows]
    assert len(identities) == 129
    assert len(set(identities)) == 129


def test_category_counts(audit):
    rows, _ = audit
    counts = Counter(row["category"] for row in rows)
    for category, expected in CATEGORY_COUNTS.items():
        assert counts.get(category) == expected, category


def test_no_defers(audit):
    rows, _ = audit
    for row in rows:
        assert "DEFER" not in row["target_disposition"].upper()


def test_all_dispositions_are_terminal(audit):
    rows, _ = audit
    allowed = {"BUILD", "MODIFY", "KEEP", "REMOVE"}
    for row in rows:
        assert row["target_disposition"] in allowed


def test_validate_passes(audit):
    rows, _ = audit
    ok, errors = validate(rows)
    assert ok, errors


def test_direct_identities_all_present(audit):
    from scripts.engine_129_audit import DIRECT_CSV

    rows, _ = audit
    inventory = {row["identity"] for row in rows}
    direct = load_direct_identities(DIRECT_CSV)
    assert direct.issubset(inventory)
    for row in rows:
        if row["identity"] in direct:
            assert row["direct_identity"] == "true"


def test_removed_rows_are_marked_removed(audit):
    rows, _ = audit
    for row in rows:
        if row["target_disposition"] == "REMOVE":
            assert row["status"] == "removed"
        else:
            assert row["status"] == "pending"


def test_transition_doc_parser_finds_dispositions():
    from scripts import engine_129_audit

    dispositions = parse_transition_dispositions(engine_129_audit.TRANSITION_MD)
    assert len(dispositions) >= 100  # most identities carry a disposition
    assert dispositions.get("graph_validity") == "MODIFY"
    assert dispositions.get("semantic_embedder") == "REMOVE"
    assert dispositions.get("GT_BRIEF_MINIMAL") == "REMOVE"
    assert dispositions.get("submit_refusal") == "BUILD"
