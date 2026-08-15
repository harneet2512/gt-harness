from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from minisweagent.exceptions import FormatError

from gt_engine.miniswe_typed_actions import (
    GROUNDTRUTH_TOOL,
    GroundTruthLitellmModel,
    build_action_request,
    execute_typed_action,
    parse_groundtruth_toolcalls,
)


def _tool_call(name: str, arguments: dict, call_id: str = "call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def test_parser_retains_bash_and_emits_explicit_typed_action():
    actions = parse_groundtruth_toolcalls(
        [
            _tool_call("bash", {"command": "git status"}, "bash-1"),
            _tool_call(
                "groundtruth",
                {"kind": "exact_literal_search", "arguments": {"literal": "needle"}},
                "gt-1",
            ),
        ],
        format_error_template="{{ error }}",
    )
    assert actions[0] == {
        "command": "git status",
        "tool_call_id": "bash-1",
        "tool_name": "bash",
    }
    assert actions[1]["tool_name"] == "groundtruth"
    assert actions[1]["gt_action"]["kind"] == "exact_literal_search"
    assert actions[1]["tool_call_id"] == "gt-1"


def test_parser_rejects_an_unknown_tool_without_reinterpreting_it_as_bash():
    with pytest.raises(FormatError) as caught:
        parse_groundtruth_toolcalls(
            [_tool_call("mystery", {"command": "echo unsafe"})],
            format_error_template="{{ error }}",
        )
    assert "Unknown tool" in str(caught.value.messages)


def test_native_model_declares_bash_and_groundtruth_tools(monkeypatch):
    captured = {}

    class Response:
        pass

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("gt_engine.miniswe_typed_actions.litellm.completion", fake_completion)
    model = GroundTruthLitellmModel(
        model_name="openai/test-model",
        model_kwargs={"temperature": 0},
    )
    assert model._query([]).__class__ is Response
    names = [tool["function"]["name"] for tool in captured["tools"]]
    assert names == ["bash", "groundtruth"]
    assert GROUNDTRUTH_TOOL["function"]["parameters"]["required"] == [
        "kind",
        "arguments",
    ]
    parameters = GROUNDTRUTH_TOOL["function"]["parameters"]
    assert parameters["properties"]["kind"]["enum"] == [
        "exact_literal_search",
        "syntax",
        "verification_status",
    ]
    assert parameters["x-groundtruth-certification"]["removed_kinds"] == [
        "patch_impact",
        "definition",
        "references",
        "callers",
    ]


def test_exact_literal_action_is_snapshot_bound_and_canonical(tmp_path):
    (tmp_path / "a.py").write_text("first\nneedle here\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle twice needle\n", encoding="utf-8")
    request = build_action_request(
        {
            "tool_call_id": "gt-7",
            "gt_action": {
                "kind": "exact_literal_search",
                "arguments": {"literal": "needle", "paths": ["."]},
            },
        },
        repo_root=tmp_path,
        configuration={"language": "python"},
    )
    result = execute_typed_action(request, repo_root=tmp_path)
    payload = json.loads(result["output"])
    assert payload["action_request"]["repository_snapshot"]
    assert payload["decision"]["mode"] == "REPLACE"
    assert payload["evidence"]["semantics"] == "exact"
    answer = payload["direct_answer"]
    matches = answer["matches"] if isinstance(answer, dict) else answer
    actual = [
        (row["path"], row["line"])
        for row in matches
        for _ in range(row.get("occurrences", 1))
    ]
    assert actual == [("a.py", 2), ("b.py", 1), ("b.py", 1)]
    assert result["returncode"] == 0


def test_exact_literal_action_revokes_replacement_when_snapshot_changes(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("needle\n", encoding="utf-8")
    request = build_action_request(
        {
            "tool_call_id": "gt-stale",
            "gt_action": {
                "kind": "exact_literal_search",
                "arguments": {"literal": "needle", "paths": ["."]},
            },
        },
        repo_root=tmp_path,
        configuration={"language": "python"},
    )
    target.write_text("needle changed\n", encoding="utf-8")

    payload = json.loads(execute_typed_action(request, repo_root=tmp_path)["output"])

    assert payload["decision"]["mode"] == "AUGMENT"
    assert payload["evidence"]["semantics"] == "incomplete"
    assert {
        "repository_revision_mismatch",
        "working_tree_sha256_mismatch",
    } <= set(payload["evidence"]["omissions"])


def test_unavailable_typed_producer_returns_incomplete_without_shell_fallback(tmp_path):
    request = build_action_request(
        {
            "tool_call_id": "gt-8",
            "gt_action": {"kind": "callers", "arguments": {"symbol": "f"}},
        },
        repo_root=tmp_path,
        configuration={},
    )
    result = execute_typed_action(request, repo_root=tmp_path)
    payload = json.loads(result["output"])
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] in {"PASS_THROUGH", "AUGMENT"}
    assert payload["evidence"]["semantics"] == "incomplete"
    assert payload["evidence"]["omissions"] == ["typed_kind_removed"]


@pytest.mark.parametrize("kind", ["patch_impact", "definition", "references", "callers"])
def test_removed_kind_is_never_dispatched_even_when_manually_constructed(tmp_path, kind):
    request = build_action_request(
        {
            "tool_call_id": f"gt-removed-{kind}",
            "gt_action": {"kind": kind, "arguments": {}},
        },
        repo_root=tmp_path,
        configuration={},
    )
    payload = json.loads(execute_typed_action(request, repo_root=tmp_path)["output"])
    assert payload["decision"]["mode"] == "PASS_THROUGH"
    assert payload["evidence"]["omissions"] == ["typed_kind_removed"]


@pytest.mark.parametrize(
    ("arguments", "omission"),
    [
        (
            {"literal": "needle", "paths": ["../outside"]},
            {"invalid_scope:../outside", "scope_outside_repository_or_missing"},
        ),
        (
            {"literal": "two\nlines"},
            {"invalid_literal", "query_must_be_single_line_text"},
        ),
        (
            {"literal": "needle", "case_sensitive": False},
            {"unsupported_argument:case_sensitive"},
        ),
    ],
)
def test_exact_literal_search_never_certifies_unimplemented_semantics(
    tmp_path, arguments, omission
):
    request = build_action_request(
        {
            "tool_call_id": "gt-boundary",
            "gt_action": {"kind": "exact_literal_search", "arguments": arguments},
        },
        repo_root=tmp_path,
        configuration={},
    )
    payload = json.loads(execute_typed_action(request, repo_root=tmp_path)["output"])
    assert payload["decision"]["mode"] != "REPLACE"
    assert omission.intersection(payload["evidence"]["omissions"])


@pytest.mark.parametrize(
    ("kind", "arguments"),
    [
        ("exact_literal_search", {"literal": "value", "paths": ["."]}),
        ("syntax", {"path": "mod.py"}),
        ("verification_status", {"plan": {}, "result": {}}),
    ],
)
def test_canonical_dispatcher_owns_every_advertised_typed_kind(tmp_path, kind, arguments):
    pytest.importorskip("groundtruth.runtime.deterministic_queries")
    (tmp_path / "mod.py").write_text("value = 1\n", encoding="utf-8")
    request = build_action_request(
        {
            "tool_call_id": f"gt-{kind}",
            "gt_action": {"kind": kind, "arguments": arguments},
        },
        repo_root=tmp_path,
        configuration={},
    )
    payload = json.loads(execute_typed_action(request, repo_root=tmp_path)["output"])
    assert payload["evidence"]["producer"].startswith("deterministic_query.")
    assert payload["direct_answer"] is not None
    assert payload["decision"]["mode"] in {"REPLACE", "AUGMENT"}


def test_uncertified_syntax_language_is_not_dispatched(tmp_path):
    (tmp_path / "mod.java").write_text("class Mod {}\n", encoding="utf-8")
    request = build_action_request(
        {
            "tool_call_id": "gt-java-syntax",
            "gt_action": {"kind": "syntax", "arguments": {"path": "mod.java"}},
        },
        repo_root=tmp_path,
        configuration={},
    )
    payload = json.loads(execute_typed_action(request, repo_root=tmp_path)["output"])
    assert payload["decision"]["mode"] == "PASS_THROUGH"
    assert payload["evidence"]["omissions"] == ["syntax_language_removed"]
