"""IE-04 observe tests: canonical observation compiler + evidence-delta projection."""
from __future__ import annotations

from gt_engine.engine.contracts import (
    ActionKind,
    ActionRequest,
    Decision,
    EvidenceArtifact,
    Fidelity,
    InterceptionDecision,
)
from gt_engine.engine.observe import compile_observation, project_evidence_delta


def make_request():
    return ActionRequest(
        action_id="a1", kind=ActionKind.FILE_READ,
        arguments={"path": "src/main.py"}, literal_shell_form="cat src/main.py",
        snapshot_token="tok-1", configuration_digest="cfg-1",
        requested_fidelity=Fidelity.RAW,
    )


def make_artifact(artifact_id="ev-1", model_visible=True, **content_overrides):
    content = {"file": "src/main.py", "ok": True}
    content.update(content_overrides)
    return EvidenceArtifact(
        artifact_id=artifact_id, owner="syntax_result", semantics="syntax",
        content=content, anchors=("src/main.py:3",), producer="py_ast",
        producer_version="1", freshness_revision="rev-9", coverage="complete",
        model_visible=model_visible,
    )


def test_pass_through_observation_retains_exact_raw():
    obs = compile_observation(
        make_request(),
        InterceptionDecision(decision=Decision.PASS_THROUGH, reason="literal"),
        raw_result="def main(): pass",
    )
    assert obs.raw_exact
    assert "def main(): pass" in obs.render()


def test_replace_observation_uses_declared_bytes():
    obs = compile_observation(
        make_request(),
        InterceptionDecision(decision=Decision.REPLACE, reason="certified"),
        replaced="declared deterministic bytes",
    )
    assert not obs.raw_exact
    assert "declared deterministic bytes" in obs.render()


def test_suppress_observation_omits_raw_but_retains_receipt():
    obs = compile_observation(
        make_request(),
        InterceptionDecision(decision=Decision.SUPPRESS, reason="blocker"),
        raw_exact=False,
        receipt_id="rcpt-1",
    )
    assert not obs.raw_exact
    assert "rcpt-1" in obs.render()


def test_evidence_delta_projection_references_unchanged_facts():
    artifact = make_artifact()
    referenced = {artifact.artifact_id: artifact.hash()}
    projected = project_evidence_delta((artifact,), referenced)
    assert len(projected) == 1
    assert projected[0].content == {"ref": artifact.artifact_id, "hash": artifact.hash()}
    assert projected[0].render_content() != artifact.render_content()


def test_evidence_delta_projection_reemits_changed_facts():
    old = make_artifact()
    changed = make_artifact(ok=False)
    referenced = {old.artifact_id: old.hash()}
    projected = project_evidence_delta((changed,), referenced)
    assert projected[0].content == {"file": "src/main.py", "ok": False}


def test_one_observation_per_action():
    obs = compile_observation(
        make_request(),
        InterceptionDecision(decision=Decision.AUGMENT, reason="augment"),
        raw_result="out",
        evidence=(make_artifact(),),
        receipt_id="rcpt-1",
    )
    assert obs.action_request.action_id == "a1"
    assert len(obs.evidence) == 1
    rendered = obs.render()
    assert "a1" in rendered
    assert "augment" in rendered
    assert "rcpt-1" in rendered
