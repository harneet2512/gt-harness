from pathlib import Path

from scripts.documentation_consistency_audit import audit_documentation


def test_authoritative_release_documentation_is_complete_and_linked() -> None:
    root = Path(__file__).resolve().parents[1]
    report = audit_documentation(root)

    assert report["status"] == "PASS", report["failures"]
    assert report["checked_documents"] == 25


def test_documentation_audit_rejects_unearned_outcome_guarantee(tmp_path) -> None:
    root = Path(__file__).resolve().parents[1]
    contract = tmp_path / "contract.md"
    dossier = tmp_path / "dossier.md"
    contract.write_text(
        (root / "docs/GT_MECHANICAL_COMPLETENESS_CONTRACT.md").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    dossier.write_text("# GroundTruth final benchmark release dossier\n\n100% solve guaranteed.\n")

    report = audit_documentation(root, documents=(contract, dossier))

    assert report["status"] == "BLOCKED"
    assert any("unearned_outcome_claim" in item for item in report["failures"])
