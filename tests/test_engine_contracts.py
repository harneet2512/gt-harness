"""IE-01 contract tests: round-trip, hash stability, versioning, invariants."""
from __future__ import annotations

import pytest

from gt_engine.engine.contracts import (
    CONTRACTS_SCHEMA_VERSION,
    ActionKind,
    ActionRequest,
    CanonicalObservation,
    Decision,
    DeliveryReceipt,
    EngineMode,
    EngineModeBinding,
    EvidenceArtifact,
    ExecutionState,
    Fidelity,
    InterceptionDecision,
    MutationCommitReceipt,
    MutationCommitRequest,
    MutationProposal,
    RepositorySnapshot,
)


def make_request(**overrides):
    base = dict(
        action_id="a1",
        kind=ActionKind.FILE_READ,
        arguments={"path": "src/main.py"},
        literal_shell_form="cat src/main.py",
        snapshot_token="tok-1",
        configuration_digest="cfg-1",
        requested_fidelity=Fidelity.EXACT,
        batch_id="b1",
        sequence_position=0,
        raw_fallback=True,
    )
    base.update(overrides)
    return ActionRequest(**base)


def make_snapshot(**overrides):
    base = dict(
        revision_heads={"HEAD": "abc123"},
        dirty_files={"src/x.py": "deadbeef"},
        untracked_files=("tmp/",),
        configuration_digest="cfg-1",
        complete=True,
    )
    base.update(overrides)
    return RepositorySnapshot(**base)


def test_action_request_roundtrip():
    req = make_request()
    rt = ActionRequest.from_dict(req.to_dict())
    assert rt == req
    assert rt.to_dict() == req.to_dict()


def test_action_request_hash_excludes_no_field():
    req = make_request()
    base = req.request_hash()
    for field in ("action_id", "kind", "arguments", "literal_shell_form",
                  "snapshot_token", "configuration_digest", "requested_fidelity",
                  "batch_id", "sequence_position", "raw_fallback"):
        changed = make_request(**{field: _alt(req, field)})
        assert changed.request_hash() != base, f"hash ignored {field}"


def _alt(req, field):
    if field == "kind":
        return ActionKind.SEARCH
    if field == "arguments":
        return {"path": "src/other.py"}
    if field == "requested_fidelity":
        return Fidelity.RAW
    if field in ("batch_id",):
        return "other-batch"
    if field == "sequence_position":
        return req.sequence_position + 1
    if field == "raw_fallback":
        return not req.raw_fallback
    return "changed"


def test_repository_snapshot_token_is_content_addressed():
    a = make_snapshot()
    b = make_snapshot()
    assert a.token() == b.token()
    changed = make_snapshot(dirty_files={"src/x.py": "cafebabe"})
    assert changed.token() != a.token()


def test_snapshot_token_authorizes_commit():
    snap = make_snapshot()
    token = snap.token()
    assert token == snap.token()
    assert len(token) == 64


def test_evidence_artifact_roundtrip_and_model_visible():
    art = EvidenceArtifact(
        artifact_id="ev-1",
        owner="syntax_result",
        semantics="exact syntax result for changed file",
        content={"file": "src/x.py", "ok": True},
        anchors=("src/x.py:3",),
        witnesses=("producer-1",),
        producer="py_ast",
        producer_version="1.0",
        freshness_revision="abc123",
        coverage="complete",
        model_visible=True,
    )
    rt = EvidenceArtifact.from_dict(art.to_dict())
    assert rt == art
    assert "src/x.py" in art.render_content()
    assert art.hash() == art.hash()


def test_canonical_observation_render_decision_and_receipt():
    # Pure pass-through with no facts renders the raw alone (no wrapper, no
    # GT framing) - the raw IS the answer.
    req = make_request()
    decision = InterceptionDecision(
        decision=Decision.PASS_THROUGH, reason="literal file view retains semantics"
    )
    obs = CanonicalObservation(
        action_request=req,
        decision=decision,
        raw_result="def main(): pass",
        receipt_id="rcpt-1",
    )
    rendered = obs.render()
    assert rendered == "def main(): pass"
    assert "<result" not in rendered

    # A fact-bearing observation leads with the decision header + fact.
    fact = EvidenceArtifact(
        artifact_id="ev-1", owner="obligations", semantics="task span",
        content={"file": "src/main.py"}, model_visible=True,
    )
    obs2 = CanonicalObservation(
        action_request=req,
        decision=InterceptionDecision(decision=Decision.AUGMENT, reason="postflight"),
        raw_result="def main(): pass",
        evidence=(fact,),
        receipt_id="rcpt-1",
    )
    rendered2 = obs2.render()
    assert "pass_through" not in rendered2
    assert "augment" in rendered2
    assert "rcpt-1" in rendered2
    assert "def main(): pass" in rendered2  # raw retained byte-exact after the block


def test_canonical_observation_replace_uses_declared_bytes():
    req = make_request()
    decision = InterceptionDecision(decision=Decision.REPLACE, reason="certified exact search")
    obs = CanonicalObservation(
        action_request=req,
        decision=decision,
        replaced="declared deterministic bytes",
        raw_exact=False,
    )
    rendered = obs.render()
    assert "declared deterministic bytes" in rendered
    assert "def main(): pass" not in rendered


def test_mutation_commit_requires_matching_token():
    proposal = MutationProposal(
        proposal_id="p1",
        snapshot_token="tok-1",
        target_path="src/x.py",
        expected_preimage_hash="pre",
        proposed_postimage_hash="post",
    )
    commit = MutationCommitRequest(proposal=proposal, commit_token="tok-1")
    assert commit.commit_hash() == commit.commit_hash()
    receipt = MutationCommitReceipt(
        commit_id="c1",
        proposal_id="p1",
        snapshot_token="tok-1",
        committed_files={"src/x.py": "post"},
        commit_hash=commit.commit_hash(),
    )
    rt = MutationCommitReceipt.from_dict(receipt.to_dict())
    assert rt == receipt


def test_delivery_receipt_binds_action_and_observation():
    req = make_request()
    receipt = DeliveryReceipt(
        delivery_id="d1",
        action_request=req,
        pre_state_hash="pre",
        raw_result_hash="raw",
        transformation_version="1.0",
        final_observation_bytes="<gt-engine>...</gt-engine>",
        provider_request_id="rq-1",
        provider_response_id="rs-1",
        next_action_hash="next",
    )
    assert receipt.hash() == receipt.hash()
    rt = DeliveryReceipt.from_dict(receipt.to_dict())
    assert rt == receipt


def test_engine_mode_binding_roundtrip():
    binding = EngineModeBinding(mode=EngineMode.ENGINE)
    assert EngineModeBinding.from_dict(binding.to_dict()) == binding
    off = EngineModeBinding(mode=EngineMode.OFF)
    assert off.mode is EngineMode.OFF


def test_newer_major_schema_rejected():
    with pytest.raises(ValueError):
        ActionRequest.from_dict(
            {"schema": f"gt.engine.action_request.v{CONTRACTS_SCHEMA_VERSION + 1}",
             "action_id": "x", "kind": "file_read", "arguments": {},
             "literal_shell_form": "", "snapshot_token": "",
             "configuration_digest": ""}
        )


def test_all_schemas_roundtrip():
    """Every public schema serializes and decodes losslessly."""
    schemas = [
        make_request(),
        make_snapshot(),
        InterceptionDecision(decision=Decision.AUGMENT, reason="r"),
        MutationProposal(
            proposal_id="p", snapshot_token="t", target_path="f",
            expected_preimage_hash="a", proposed_postimage_hash="b",
        ),
    ]
    for schema in schemas:
        assert schema.to_dict()["schema"].startswith("gt.engine.")
