"""IE-02/03/05/08 runner tests: normalization, classification, barriers, fallback."""
from __future__ import annotations

from gt_engine.engine.contracts import ActionKind, ActionRequest, Decision, Fidelity
from gt_engine.engine.runner import (
    build_analyzer_state,
    classify_batch_barriers,
    classify_shell,
    configuration_digest_for,
    fallback_shell_for_typed,
    normalize_action,
    snapshot_token_for,
)


def test_classify_shell_file_read():
    assert classify_shell("cat src/main.py") is ActionKind.FILE_READ
    assert classify_shell("less README.md") is ActionKind.FILE_READ


def test_classify_shell_search():
    assert classify_shell("grep -r foo src") is ActionKind.SEARCH
    assert classify_shell("rg main.py .") is ActionKind.SEARCH


def test_classify_shell_opaque_stays_shell():
    assert classify_shell("sed -i s/a/b/ x.py") is ActionKind.SHELL
    assert classify_shell("cat a | grep foo") is ActionKind.SHELL
    assert classify_shell("echo hi > file") is ActionKind.SHELL
    assert classify_shell("pytest tests") is ActionKind.SHELL


def test_fallback_for_typed_literal_search():
    shell = fallback_shell_for_typed(
        "exact_literal_search", {"literal": "main()", "paths": "src"}
    )
    assert "grep" in shell and "main()" in shell and "src" in shell


def test_fallback_for_typed_syntax():
    shell = fallback_shell_for_typed("syntax", {"path": "src/x.py"})
    assert "py_compile" in shell


def test_fallback_empty_when_unsafe():
    assert fallback_shell_for_typed("exact_literal_search", {}) == ""
    assert fallback_shell_for_typed("references", {}) == ""


def test_normalize_shell_action():
    req = normalize_action(
        {"command": "cat src/main.py", "tool_call_id": "call_1"},
        repo_root="/repo", configuration_digest="cfg",
        snapshot_token="tok", batch_id="b1", sequence_position=1,
    )
    assert req.kind is ActionKind.FILE_READ
    assert req.literal_shell_form == "cat src/main.py"
    assert req.snapshot_token == "tok"
    assert req.batch_id == "b1"
    assert req.sequence_position == 1
    assert req.request_hash()


def test_normalize_typed_action():
    req = normalize_action(
        {
            "tool_call_id": "call_2",
            "gt_action": {
                "kind": "exact_literal_search",
                "arguments": {"literal": "def main", "paths": "src"},
                "requested_fidelity": "exact",
            },
        },
        repo_root="/repo", configuration_digest="cfg",
        snapshot_token="tok", batch_id="b1", sequence_position=2,
    )
    assert req.kind is ActionKind.SEARCH
    assert "grep" in req.literal_shell_form  # literal fallback bound


def test_snapshot_token_is_content_addressed():
    a = snapshot_token_for("rev-1", "/repo", {"x.py": (1, 2)}, "cfg")
    b = snapshot_token_for("rev-1", "/repo", {"x.py": (1, 2)}, "cfg")
    c = snapshot_token_for("rev-2", "/repo", {"x.py": (1, 2)}, "cfg")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_configuration_digest_stable():
    assert configuration_digest_for("/r", "g", "rev") == configuration_digest_for("/r", "g", "rev")
    assert configuration_digest_for("/r", "g", "rev") != configuration_digest_for("/r", "g", "rev2")


def test_batch_barriers_on_mutations_and_verification():
    def rq(kind, seq):
        return ActionRequest(
            action_id=f"a{seq}", kind=kind, arguments={}, literal_shell_form="",
            snapshot_token="tok", configuration_digest="cfg",
            requested_fidelity=Fidelity.RAW, batch_id="b", sequence_position=seq,
        )

    batch = (
        rq(ActionKind.FILE_READ, 1),
        rq(ActionKind.EDIT_PROPOSAL, 2),
        rq(ActionKind.FILE_READ, 3),
        rq(ActionKind.SUBMIT, 4),
    )
    barriers = classify_batch_barriers(batch)
    assert 2 in barriers and 4 in barriers
    assert 1 not in barriers and 3 not in barriers


def test_tool_output_shape_matches_formatter_expectations():
    """Regression: Mini-SWE's Jinja formatter reads result.exception_info as an
    attribute; a missing key previously crashed the engine (gt_degraded_fail_open)."""
    from gt_engine.engine.contracts import CanonicalObservation, InterceptionDecision
    from gt_engine.engine.runner import _tool_output

    request = ActionRequest(
        action_id="c1", kind=ActionKind.FILE_READ, arguments={},
        literal_shell_form="cat a", snapshot_token="tok",
        configuration_digest="cfg", batch_id="b", sequence_position=1,
    )
    observation = CanonicalObservation(
        action_request=request,
        decision=InterceptionDecision(decision=Decision.PASS_THROUGH, reason="literal"),
        raw_result="file contents",
    )
    out = _tool_output(observation, 0)
    assert "output" in out
    assert out["returncode"] == 0
    assert "exception_info" in out  # formatter attribute access must not raise
    assert out["extra"]["gt_engine"] is True
    assert out["extra"]["engine_decision"] == "pass_through"
    assert len(out["extra"]["canonical_observation_sha256"]) == 64


def test_analyzer_state_certified_from_typed_result():
    state = build_analyzer_state(
        ActionRequest(
            action_id="a", kind=ActionKind.SEARCH, arguments={}, literal_shell_form="",
            snapshot_token="tok", configuration_digest="cfg",
        ),
        repository_revision="rev-9",
        graph_fresh=True,
        graph_available=True,
        typed_result={
            "output": '{"schema":"gt.compiled_observation.v1","direct_answer":"hit",'
                      '"evidence":{"semantics":"exact","omissions":[]},'
                      '"decision":{"mode":"REPLACE"}}'
        },
    )
    assert state.certified_replacement
    assert state.replacement_complete
    assert state.replacement_fresh
