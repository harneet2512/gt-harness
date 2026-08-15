from gt_engine.checkpoint_ledger import ShadowCheckpointLedger, VerificationVector


def test_verification_vector_requires_strict_non_regressing_improvement():
    base = VerificationVector(("syntax",), ())
    improved = VerificationVector(("syntax", "unit"), ())
    mixed = VerificationVector(("syntax", "unit"), ("integration",))

    assert improved.dominates(base) is True
    assert base.dominates(base) is False
    assert mixed.dominates(base) is False


def test_shadow_checkpoint_records_rollback_opportunity_without_actuating():
    ledger = ShadowCheckpointLedger()
    ledger.observe(
        source_revision="s1",
        workspace_revision="w1",
        changed_paths=("app.py",),
        passing_checks=("pytest -q",),
        failing_checks=(),
        action_id=2,
    )
    ledger.observe(
        source_revision="s2",
        workspace_revision="w2",
        changed_paths=("app.py",),
        passing_checks=(),
        failing_checks=("pytest -q",),
        action_id=3,
    )

    summary = ledger.summary()
    assert summary["mode"] == "shadow"
    assert summary["best"]["source_revision"] == "s1"
    assert summary["rollback_opportunities"] == [
        {
            "failed_revision": "s2",
            "failed_after_action": 3,
            "candidate_revision": "s1",
            "candidate_after_action": 2,
            "failing_checks": ["pytest -q"],
            "shadow_only": True,
        }
    ]
