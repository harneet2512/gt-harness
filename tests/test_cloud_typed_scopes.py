"""HAR-85: glob scopes must not make ``exact_literal_search`` abstain.

The producer walks ``paths`` as concrete filesystem paths, so ``src/pkg/**``
resolves to nothing and the evidence is honestly ``incomplete``. These tests
pin both the pure normaliser and the real typed-action code path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloud.server.typed_scopes import (
    normalize_literal_search_arguments,
    normalize_scope,
    normalize_typed_action,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "core.py").write_text(
        "class Command:\n    def invoke(self):\n        return None\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("from .core import Command\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("class Command lives in src/pkg\n", encoding="utf-8")
    return tmp_path


def _literal_action(paths: list[str], literal: str = "class Command") -> dict:
    return {
        "tool_name": "groundtruth",
        "tool_call_id": "call_1",
        "gt_action": {
            "kind": "exact_literal_search",
            "arguments": {"literal": literal, "paths": paths},
        },
    }


def _run(action: dict, repo: Path) -> dict:
    """Execute the action through the real typed router, not a stand-in."""
    # The deterministic producer ships in the vendored GroundTruth wheel that
    # the cloud image installs; without it the router falls back to a legacy
    # shape and these assertions would pin the wrong contract.
    pytest.importorskip("groundtruth.runtime.deterministic_queries")
    from gt_engine.miniswe_typed_actions import execute_typed_action_fail_open

    _request, result = execute_typed_action_fail_open(
        action,
        repo_root=repo,
        configuration={"graph_db": "", "graph_fresh": False, "gt_mode": "advisory"},
    )
    payload = json.loads(result["output"])
    payload["returncode"] = result["returncode"]
    return payload


# --- the pure normaliser ----------------------------------------------------


def test_recursive_glob_reduces_to_its_directory(repo: Path) -> None:
    assert normalize_scope("src/pkg/**", repo) == "src/pkg"


def test_extension_glob_reduces_to_its_directory(repo: Path) -> None:
    assert normalize_scope("src/pkg/*.py", repo) == "src/pkg"


def test_interior_glob_keeps_only_the_literal_prefix(repo: Path) -> None:
    assert normalize_scope("src/**/core.py", repo) == "src"


def test_bare_glob_becomes_the_repository_root(repo: Path) -> None:
    assert normalize_scope("**", repo) == "."


def test_concrete_paths_are_returned_unchanged(repo: Path) -> None:
    assert normalize_scope("src/pkg", repo) == "src/pkg"
    assert normalize_scope("src/pkg/core.py", repo) == "src/pkg/core.py"


def test_a_typo_without_a_glob_is_not_widened(repo: Path) -> None:
    # A plain missing path must still abstain, not silently search its parent.
    assert normalize_scope("src/pkg/coree.py", repo) == "src/pkg/coree.py"


def test_a_glob_over_a_missing_prefix_is_left_alone(repo: Path) -> None:
    assert normalize_scope("src/nope/**", repo) == "src/nope/**"


def test_escape_attempts_are_left_alone(repo: Path) -> None:
    assert normalize_scope("../**", repo) == "../**"
    assert normalize_scope("/etc/**", repo) == "/etc/**"


def test_non_string_scopes_pass_through(repo: Path) -> None:
    assert normalize_scope(None, repo) is None
    assert normalize_scope(7, repo) == 7


def test_arguments_are_copied_not_mutated(repo: Path) -> None:
    arguments = {"literal": "class Command", "paths": ["src/pkg/**", "src/pkg/*.py"]}
    rewritten = normalize_literal_search_arguments(arguments, repo)
    assert rewritten["paths"] == ["src/pkg"]  # deduplicated
    assert arguments["paths"] == ["src/pkg/**", "src/pkg/*.py"]


def test_only_literal_search_actions_are_rewritten(repo: Path) -> None:
    action = {
        "tool_name": "groundtruth",
        "gt_action": {"kind": "syntax", "arguments": {"path": "src/pkg/*.py"}},
    }
    assert normalize_typed_action(action, repo) is action
    bash = {"tool_name": "bash", "command": "grep -r 'class Command' src/**"}
    assert normalize_typed_action(bash, repo) is bash


# --- the real typed-action code path ---------------------------------------


def test_glob_scope_abstains_before_normalization(repo: Path) -> None:
    payload = _run(_literal_action(["src/pkg/**"]), repo)
    evidence = payload["evidence"]
    assert payload["returncode"] == 2
    assert evidence["semantics"] == "incomplete"
    assert "missing_scope:src/pkg/**" in evidence["omissions"]
    # The exact symptom reported in HAR-85: an empty match list on a repo that
    # plainly contains the literal.
    assert payload["direct_answer"]["matches"] == []


def test_normalized_glob_scope_returns_exact_matches(repo: Path) -> None:
    action = normalize_typed_action(_literal_action(["src/pkg/**"]), repo)
    payload = _run(action, repo)
    evidence = payload["evidence"]
    assert payload["returncode"] == 0
    assert evidence["semantics"] == "exact"
    assert evidence["coverage"] == "complete"
    assert evidence["omissions"] == []
    answer = payload["direct_answer"]
    assert answer["scope"] == ["src/pkg"]
    matches = answer["matches"]
    assert matches, "the normalised scope must produce matches"
    assert {match["path"] for match in matches} == {"src/pkg/core.py"}
    assert matches[0]["line"] == 1


def test_normalization_does_not_rescue_a_genuinely_missing_scope(repo: Path) -> None:
    action = normalize_typed_action(_literal_action(["src/nope/**"]), repo)
    payload = _run(action, repo)
    assert payload["returncode"] == 2
    assert payload["evidence"]["semantics"] == "incomplete"
    assert "missing_scope:src/nope/**" in payload["evidence"]["omissions"]
