"""End-to-end dispatch coverage for every certified ``GROUNDTRUTH_TOOL`` kind.

Each test drives the real harness seam a Mini-SWE ``groundtruth`` tool call
hits:

    wire action dict
      -> build_action_request            (snapshot-bound ActionRequest or wire)
      -> execute_typed_action_fail_open  (the environment's fail-open entry)
      -> execute_query                   (canonical deterministic producer)
      -> evaluate_interception           (REPLACE / AUGMENT / PASS_THROUGH)
      -> gt.compiled_observation.v1      (canonical JSON in result["output"])

The fixture here is a synthetic graph.db plus the matching source files, so
the producer's answer content is pinned exactly. The advanced kinds are also
executed against a graph the real gt-index builds, in
``test_typed_graph_real_producer.py``.

Ported from MAIN bb6f8925 (HAR-90 typed surface). Two things differ from MAIN
on purpose: graph-backed kinds are certified ``partial`` in canonical, so
they AUGMENT and never REPLACE; and the typed graph revision is the
EngineState workspace revision the caller binds, never a value read back out
of the graph (MAIN read ``project_meta.git_commit``, the producer's build
commit, which made the revision check compare the graph with itself).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

observation_compiler = pytest.importorskip(
    "groundtruth.runtime.observation_compiler",
    reason="typed dispatch requires the canonical observation compiler",
)
deterministic_queries = pytest.importorskip("groundtruth.runtime.deterministic_queries")
ActionRequest = observation_compiler.ActionRequest

from gt_engine import miniswe_typed_actions  # noqa: E402
from gt_engine.generated_typed_capabilities import (  # noqa: E402
    CERTIFIED_TYPED_KIND_LANGUAGES,
    CERTIFIED_TYPED_KIND_SEMANTICS,
    CERTIFIED_TYPED_KINDS,
    REMOVED_TYPED_KINDS,
)
from gt_engine.miniswe_typed_actions import (  # noqa: E402
    GRAPH_REVISION_UNBOUND,
    GROUNDTRUTH_TOOL,
    KIND_ARGUMENT_DOCS,
    QUERY_RESULT_MAX_BYTES,
    build_action_request,
    execute_typed_action_fail_open,
)
from gt_engine.typed_output_bounds import bound_compiled_observation  # noqa: E402

GRAPH_REVISION = "fixture-graph-revision-0001"
_NON_GRAPH_KINDS = {"exact_literal_search", "syntax", "verification_status"}

# main -> run_pipeline -> helper -> emit, all CERTIFIED CALLS edges.
_FIXTURE_FILES = {
    "main.py": "from pkg.pipeline import run_pipeline\n\n\ndef main():\n    run_pipeline()\n",
    "pkg/__init__.py": "",
    "pkg/pipeline.py": "from pkg.util import helper\n\n\ndef run_pipeline():\n    helper()\n",
    "pkg/util.py": "def helper():\n    return emit()\n\n\ndef emit():\n    return 42\n",
}

_NODES = [
    # id, label, name, qualified_name, file_path, start, end, signature,
    # return_type, is_exported, is_test, language, parent_id
    (1, "Function", "main", "main.main", "main.py", 4, 5, "def main()", "None", 1, 0, "python", None),
    (2, "Function", "run_pipeline", "pkg.pipeline.run_pipeline", "pkg/pipeline.py", 4, 5, "def run_pipeline()", "None", 1, 0, "python", None),
    (3, "Function", "helper", "pkg.util.helper", "pkg/util.py", 1, 2, "def helper()", "int", 1, 0, "python", None),
    (4, "Function", "emit", "pkg.util.emit", "pkg/util.py", 5, 6, "def emit()", "int", 1, 0, "python", None),
]

_EDGES = [
    # id, source_id, target_id, type, source_line, source_file,
    # resolution_method, trust_tier, confidence
    (1, 1, 2, "CALLS", 5, "main.py", "import", "CERTIFIED", 1.0),
    (2, 2, 3, "CALLS", 5, "pkg/pipeline.py", "import", "CERTIFIED", 1.0),
    (3, 3, 4, "CALLS", 2, "pkg/util.py", "same_file", "CERTIFIED", 1.0),
    (4, 2, 3, "IMPORTS", 1, "pkg/pipeline.py", "import", "CERTIFIED", 1.0),
]

_GRAPH_SCHEMA = """
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY,
    label TEXT,
    name TEXT,
    qualified_name TEXT,
    file_path TEXT,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,
    return_type TEXT,
    is_exported INTEGER,
    is_test INTEGER,
    language TEXT,
    parent_id INTEGER
);
CREATE TABLE edges (
    id INTEGER PRIMARY KEY,
    source_id INTEGER,
    target_id INTEGER,
    type TEXT,
    source_line INTEGER,
    source_file TEXT,
    resolution_method TEXT,
    trust_tier TEXT,
    confidence REAL
);
CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE file_hashes (file_path TEXT, content_hash TEXT);
"""


def _write_graph(graph_db: Path, nodes, edges) -> None:
    with sqlite3.connect(graph_db) as conn:
        conn.executescript(_GRAPH_SCHEMA)
        conn.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", nodes)
        conn.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?,?,?,?)", edges)
        # The graph's workspace identity is project_meta.source_revision (the
        # -source-revision contract). The vendored wheel predating that
        # contract reads git_commit, so a hand-built fixture pins both to the
        # one revision the caller binds; a real gt-index graph keeps its
        # build commit in git_commit (see the real-producer tests).
        conn.executemany(
            "INSERT INTO project_meta VALUES (?,?)",
            [("git_commit", GRAPH_REVISION), ("source_revision", GRAPH_REVISION)],
        )


def _build_fixture_repo(root: Path) -> Path:
    """Write the source tree and a matching graph.db; return the db path."""
    for relative, text in _FIXTURE_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    graph_db = root / "graph.db"
    _write_graph(graph_db, _NODES, _EDGES)
    with sqlite3.connect(graph_db) as conn:
        conn.executemany(
            "INSERT INTO file_hashes VALUES (?,?)",
            [
                (rel, hashlib.sha256(text.encode("utf-8")).hexdigest())
                for rel, text in _FIXTURE_FILES.items()
            ],
        )
    return graph_db


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, Path]:
    graph_db = _build_fixture_repo(tmp_path)
    return tmp_path, graph_db


def _wire(kind: str, arguments: dict, tool_call_id: str = "gt-dispatch") -> dict:
    """The parsed action shape parse_groundtruth_toolcalls emits."""
    return {
        "tool_call_id": tool_call_id,
        "tool_name": "groundtruth",
        "gt_action": {"kind": kind, "arguments": arguments},
    }


def _dispatch(
    root: Path, graph_db: Path, kind: str, arguments: dict, tool_call_id: str = "gt-dispatch",
    *, graph_source_revision: str = GRAPH_REVISION,
):
    """Run one wire call through the real fail-open entry the environment uses."""
    request, result = execute_typed_action_fail_open(
        _wire(kind, arguments, tool_call_id),
        repo_root=root,
        configuration={
            "graph_db": str(graph_db),
            "graph_source_revision": graph_source_revision,
        },
    )
    return request, result, json.loads(result["output"])


def _assert_exact_replace(result: dict, payload: dict, producer: str) -> dict:
    """Shared invariants for an exact, complete, fully dispatched answer."""
    assert result["returncode"] == 0, result.get("exception_info")
    assert payload["schema"] == "gt.compiled_observation.v1"
    assert payload["decision"]["mode"] == "REPLACE"
    assert payload["decision"]["reason_codes"] == ["EXACT_COMPLETE_EQUIVALENCE"]
    evidence = payload["evidence"]
    assert evidence["producer"] == producer
    assert evidence["semantics"] == "exact"
    assert evidence["coverage"] == "complete"
    assert evidence["omissions"] == []
    assert payload["direct_answer"] is not None
    return payload["direct_answer"]


def _assert_partial_augment(result: dict, payload: dict, producer: str) -> dict:
    """A clean producer answer for a ``partial``-certified kind.

    The producer found nothing missing and labels its artifact exact, but the
    kind's certification is partial: the decision is AUGMENT and the
    certification ceiling is named, never REPLACE.
    """
    assert result["returncode"] == 2, result.get("exception_info")
    assert payload["decision"]["mode"] == "AUGMENT"
    assert "CERTIFICATION_NOT_EXACT" in payload["decision"]["reason_codes"]
    evidence = payload["evidence"]
    assert evidence["producer"] == producer
    assert evidence["omissions"] == ["certified_semantics:partial"]
    assert payload["direct_answer"] is not None
    return payload["direct_answer"]


# ------------------------------------------------------------- tool schema


def test_groundtruth_tool_schema_advertises_exactly_the_certified_kinds():
    enum = GROUNDTRUTH_TOOL["function"]["parameters"]["properties"]["kind"]["enum"]
    assert enum == list(CERTIFIED_TYPED_KINDS)
    assert set(enum) == {
        "exact_literal_search",
        "syntax",
        "patch_impact",
        "verification_status",
        "definition",
        "references",
        "callers",
        "symbol_context",
        "processes",
        "route_map",
        "api_impact",
        "taint",
        "rename",
        "shape_check",
        "tool_map",
        "slice",
    }
    assert "why_this_edge" not in enum
    assert REMOVED_TYPED_KINDS == ()


def test_tool_description_documents_every_certified_kind_and_nothing_else():
    description = GROUNDTRUTH_TOOL["function"]["parameters"]["properties"]["arguments"][
        "description"
    ]
    documented = {part.split(":", 1)[0].strip().removeprefix("Exact typed arguments per kind. ")
                  for part in description.rstrip(".").split("; ")
                  if ":" in part}
    assert documented == set(CERTIFIED_TYPED_KINDS)
    assert "why_this_edge" not in description
    for kind in REMOVED_TYPED_KINDS:
        assert kind not in description


@pytest.mark.parametrize("kind", sorted(CERTIFIED_TYPED_KINDS))
def test_tool_description_names_every_argument_the_producer_accepts(kind):
    allowed = deterministic_queries._ALLOWED_ARGUMENTS[
        observation_compiler.ActionKind({"syntax": "syntax_query"}.get(kind, kind))
    ]
    doc = KIND_ARGUMENT_DOCS[kind]
    for name in allowed:
        assert name in doc, f"{kind} description omits argument {name!r}"


def test_slice_description_carries_the_interprocedural_bounds():
    doc = KIND_ARGUMENT_DOCS["slice"]
    for name in ("interprocedural", "max_hops", "max_depth", "direction", "variables"):
        assert name in doc


# ------------------------------------------------ certification honesty


def test_no_graph_kind_is_certified_beyond_partial():
    graph_kinds = set(CERTIFIED_TYPED_KINDS) - _NON_GRAPH_KINDS
    assert {CERTIFIED_TYPED_KIND_SEMANTICS[kind] for kind in graph_kinds} == {"partial"}
    assert CERTIFIED_TYPED_KIND_SEMANTICS["exact_literal_search"] == "exact"
    assert CERTIFIED_TYPED_KIND_SEMANTICS["verification_status"] == "execution_specific"
    assert "sound_overapprox" not in CERTIFIED_TYPED_KIND_SEMANTICS.values()


def test_slice_and_route_kinds_are_certified_only_where_the_substrate_exists():
    cfg_languages = {"go", "java", "javascript", "python", "typescript"}
    assert set(CERTIFIED_TYPED_KIND_LANGUAGES["slice"]) == cfg_languages
    assert set(CERTIFIED_TYPED_KIND_LANGUAGES["route_map"]) <= cfg_languages
    assert "rust" not in CERTIFIED_TYPED_KIND_LANGUAGES["slice"]


def test_certification_basis_states_the_taint_limit():
    import csv

    rows = csv.DictReader(
        (Path(__file__).resolve().parents[1] / "gt_finalstand"
         / "language_operation_certification.csv").open(encoding="utf-8", newline="")
    )
    taint = {row["certification_basis"] for row in rows
             if row["operation"] == "taint" and row["terminal_semantics"] != "removed"}
    (basis,) = taint
    assert "symbol-level reachability" in basis
    assert "not an over-approximation" in basis


@pytest.mark.parametrize(
    ("kind", "arguments", "language"),
    [
        ("slice", {"symbol": "helper", "line": 2, "language": "rust"}, "rust"),
        ("definition", {"symbol": "helper", "path": "lib/helper.rb"}, "ruby"),
        ("route_map", {"path": "src/main.rs"}, "rust"),
    ],
)
def test_uncertified_language_is_refused_at_the_gate(repo, kind, arguments, language):
    root, graph_db = repo
    _, result, payload = _dispatch(root, graph_db, kind, arguments)
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "PASS_THROUGH"
    assert payload["evidence"]["omissions"] == [f"typed_language_not_certified:{language}"]


# ------------------------------------------------------- request binding


@pytest.mark.parametrize("kind", sorted(CERTIFIED_TYPED_KINDS))
def test_certified_kind_builds_a_core_action_request(repo, kind):
    """Every advertised kind must bind to a canonical ActionRequest — a raw
    wire mapping here means the kind is certified but not dispatchable."""
    root, graph_db = repo
    arguments = {
        "exact_literal_search": {"literal": "helper", "paths": ["."]},
        "syntax": {"path": "pkg/util.py"},
        "patch_impact": {
            "edited_files": {
                "pkg/util.py": {"before": _FIXTURE_FILES["pkg/util.py"], "after": "x"}
            }
        },
        "verification_status": {"plan": {}, "result": {}},
        "definition": {"symbol": "helper"},
        "references": {"symbol": "helper"},
        "callers": {"symbol": "helper", "depth": 1},
        "symbol_context": {"symbol": "run_pipeline"},
        "processes": {},
        "route_map": {},
        "api_impact": {},
        "taint": {"source": "helper"},
        "rename": {"symbol": "helper"},
        "shape_check": {"symbol": "helper"},
        "tool_map": {},
        "slice": {"symbol": "helper", "line": 7},
    }[kind]
    request = build_action_request(
        _wire(kind, arguments),
        repo_root=root,
        configuration={"graph_db": str(graph_db)},
    )
    assert isinstance(request, ActionRequest), (
        f"certified kind {kind!r} did not bind to a core ActionRequest"
    )
    # The wire spelling "syntax" binds to ActionKind.SYNTAX_QUERY; every other
    # certified wire kind is spelled identically to its enum value.
    expected_value = {"syntax": "syntax_query"}.get(kind, kind)
    assert request.kind.value == expected_value


def test_graph_revision_is_the_bound_workspace_revision_not_the_graph_build_commit(repo):
    root, graph_db = repo
    with sqlite3.connect(graph_db) as conn:
        conn.execute("UPDATE project_meta SET value='producer-build-commit' "
                     "WHERE key='git_commit'")
    bound = build_action_request(
        _wire("callers", {"symbol": "helper"}), repo_root=root,
        configuration={"graph_db": str(graph_db), "graph_source_revision": "ws-rev-9"},
    )
    assert bound.repository_snapshot.revisions.graph == "ws-rev-9"
    unbound = build_action_request(
        _wire("callers", {"symbol": "helper"}), repo_root=root,
        configuration={"graph_db": str(graph_db)},
    )
    # No EngineState authority: the vector names that fact instead of
    # borrowing an identity the graph could trivially match.
    assert unbound.repository_snapshot.revisions.graph == GRAPH_REVISION_UNBOUND


def test_stale_graph_revision_is_reported_not_answered_as_current(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(
        root, graph_db, "callers", {"symbol": "helper"},
        graph_source_revision="a-newer-workspace-revision",
    )
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "AUGMENT"
    assert "graph_revision_mismatch" in payload["evidence"]["omissions"]


# ------------------------------------------------ dispatch per kind


def test_exact_literal_search_dispatches_replace_with_matches(repo):
    root, graph_db = repo
    # Scope to source files: graph.db lives in the root and its binary pages
    # would add an oversized binary line to the evidence payload.
    _, result, payload = _dispatch(
        root,
        graph_db,
        "exact_literal_search",
        {"literal": "helper()", "paths": ["main.py", "pkg"]},
    )
    answer = _assert_exact_replace(
        result, payload, "deterministic_query.exact_literal_search"
    )
    hits = {(row["path"], row["line"]) for row in answer["matches"]}
    assert hits == {("pkg/pipeline.py", 5), ("pkg/util.py", 1)}
    # The honesty envelope names the snapshot's working-tree identity, not
    # the repr of the whole snapshot mapping.
    assert payload["honesty"]["source_revision"] == (
        payload["action_request"]["repository_snapshot"]["working_tree_sha256"]
    )


def test_syntax_query_dispatches_replace_for_certified_extension(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(root, graph_db, "syntax", {"path": "pkg/util.py"})
    answer = _assert_exact_replace(result, payload, "deterministic_query.syntax_query")
    assert answer["path"] == "pkg/util.py"
    assert answer["verdict"] == "ok"


def test_definition_returns_the_graph_recorded_definition(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(root, graph_db, "definition", {"symbol": "helper"})
    answer = _assert_partial_augment(result, payload, "deterministic_query.definition")
    assert answer["definition_count"] == 1
    (definition,) = answer["definitions"]
    assert definition["qualified_name"] == "pkg.util.helper"
    assert definition["file_path"] == "pkg/util.py"
    assert definition["start_line"] == 1


def test_references_groups_incoming_edges_by_type(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(root, graph_db, "references", {"symbol": "helper"})
    answer = _assert_partial_augment(result, payload, "deterministic_query.references")
    assert answer["resolved_nodes"] == 1
    assert answer["reference_count"] == 2
    by_type = answer["references_by_type"]
    assert [r["name"] for r in by_type["CALLS"]] == ["run_pipeline"]
    assert [r["name"] for r in by_type["IMPORTS"]] == ["run_pipeline"]
    assert by_type["CALLS"][0]["trust_tier"] == "CERTIFIED"


def test_callers_walks_incoming_calls_edges_depth_banded(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(
        root, graph_db, "callers", {"symbol": "helper", "depth": 2}
    )
    answer = _assert_partial_augment(result, payload, "deterministic_query.callers")
    assert answer["resolved_nodes"] == 1
    assert answer["caller_count"] == 2
    assert [c["name"] for c in answer["callers_by_depth"]["1"]] == ["run_pipeline"]
    assert [c["name"] for c in answer["callers_by_depth"]["2"]] == ["main"]
    assert answer["callers_by_depth"]["1"][0]["file_path"] == "pkg/pipeline.py"


def test_callers_alias_find_callers_dispatches_as_callers(repo):
    root, graph_db = repo
    request, result, payload = _dispatch(
        root, graph_db, "find_callers", {"symbol": "helper", "depth": 1}
    )
    answer = _assert_partial_augment(result, payload, "deterministic_query.callers")
    assert [c["name"] for c in answer["callers_by_depth"]["1"]] == ["run_pipeline"]
    # The canonical request records the normalized kind, not the alias.
    assert payload["action_request"]["kind"] == "callers"
    assert request.kind.value == "callers"


def test_symbol_context_returns_360_degree_view(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(
        root, graph_db, "symbol_context", {"symbol": "run_pipeline"}
    )
    answer = _assert_partial_augment(
        result, payload, "deterministic_query.symbol_context"
    )
    assert answer["definition"]["name"] == "run_pipeline"
    assert answer["definition"]["file_path"] == "pkg/pipeline.py"
    assert [c["name"] for c in answer["callers"]] == ["main"]
    assert [c["name"] for c in answer["callees"]] == ["helper"]
    assert answer["flows"] == ["main -> emit"]


def test_processes_lists_the_detected_entry_to_terminal_flow(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(root, graph_db, "processes", {})
    answer = _assert_partial_augment(result, payload, "deterministic_query.processes")
    assert answer["process_count"] == 1
    (process,) = answer["processes"]
    assert process["label"] == "main -> emit"
    assert process["entry_kind"] == "declared_main"
    assert process["step_count"] == 3
    assert process["certified_ratio"] == 1.0
    assert process["steps"] == [
        "main (main.py:4)",
        "run_pipeline (pkg/pipeline.py:4)",
        "helper (pkg/util.py:1)",
        "emit (pkg/util.py:5)",
    ]


def test_processes_concept_filter_matches_flow_members(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(
        root, graph_db, "processes", {"concept": "helper"}
    )
    answer = _assert_partial_augment(result, payload, "deterministic_query.processes")
    assert answer["matched"] == 1
    assert answer["processes"][0]["label"] == "main -> emit"


def test_patch_impact_is_conservatively_incomplete_and_augments(repo):
    root, graph_db = repo
    before = _FIXTURE_FILES["pkg/util.py"]
    after = before.replace("return 42", "return 43")
    _, result, payload = _dispatch(
        root,
        graph_db,
        "patch_impact",
        {"edited_files": {"pkg/util.py": {"before": before, "after": after}}},
    )
    # patch_impact is certified and dispatched, but its producer is explicitly
    # conservative: semantic impact can never claim complete coverage, so the
    # interception mode is AUGMENT, never REPLACE.
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "AUGMENT"
    assert payload["evidence"]["producer"] == "deterministic_query.patch_impact"
    assert payload["evidence"]["semantics"] == "incomplete"
    assert "semantic_impact_not_complete" in payload["evidence"]["omissions"]
    answer = payload["direct_answer"]
    assert answer["files"][0]["path"] == "pkg/util.py"
    assert "impact" in answer


def test_verification_status_green_is_execution_specific(repo):
    root, graph_db = repo
    worktree = miniswe_typed_actions._file_snapshot(root)
    plan = {
        "patch_revision": worktree,
        "graph_revision": GRAPH_REVISION,
        "changed_entities": ["helper"],
        "obligations": [],
        "checks": [
            {
                "kind": "unit",
                "command": ["python", "-m", "pytest"],
                "selection_basis": "covering",
                "covered_entities": ["helper"],
                "expected_cost": "low",
                "confidence": "high",
                "targets": ["tests/test_util.py"],
            }
        ],
        "edited_files": ["pkg/util.py"],
    }
    check_result = {
        "kind": "unit",
        "selection_basis": "covering",
        "executed": True,
        "verdict": "pass",
        "graph_revision": GRAPH_REVISION,
        "patch_revision": worktree,
        "covered_entities": ["helper"],
        "covered_obligations": [],
        "attribution_requirement": "none",
        "attribution_satisfied": False,
        "detail": {},
    }
    _, result, payload = _dispatch(
        root,
        graph_db,
        "verification_status",
        {"plan": plan, "result": check_result},
    )
    # EXECUTION_SPECIFIC evidence always requires the raw diagnostics, so the
    # correct decision is AUGMENT even for a green verdict.
    assert payload["decision"]["mode"] == "AUGMENT"
    assert payload["evidence"]["producer"] == "deterministic_query.verification_status"
    assert payload["evidence"]["semantics"] == "execution_specific"
    assert payload["evidence"]["omissions"] == []
    assert "RAW_DIAGNOSTICS_REQUIRED" in payload["decision"]["reason_codes"]
    answer = payload["direct_answer"]
    assert answer["green"] is True
    assert answer["status"] == "green"


@pytest.mark.parametrize(
    ("kind", "arguments"),
    [
        ("definition", {"symbol": "not_in_graph"}),
        ("references", {"symbol": "not_in_graph"}),
        ("callers", {"symbol": "not_in_graph"}),
        ("symbol_context", {"symbol": "not_in_graph"}),
    ],
)
def test_unresolved_symbol_abstains_with_named_omission(repo, kind, arguments):
    """An unknown symbol must produce INCOMPLETE evidence with a named
    omission — never a fabricated definition, caller list, or REPLACE."""
    root, graph_db = repo
    _, result, payload = _dispatch(root, graph_db, kind, arguments)
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "AUGMENT"
    assert payload["evidence"]["semantics"] == "incomplete"
    assert "symbol_not_found" in payload["evidence"]["omissions"]
    assert payload["evidence"]["producer"] == f"deterministic_query.{kind}"


def test_uncertified_kind_passes_through_with_typed_kind_removed(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(
        root, graph_db, "totally_bogus_kind", {"symbol": "helper"}
    )
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "PASS_THROUGH"
    assert payload["evidence"]["semantics"] == "incomplete"
    assert payload["evidence"]["omissions"] == ["typed_kind_removed"]
    assert payload["evidence"]["producer"] == "gt-harness.certification_gate.v1"


def test_syntax_gate_blocks_uncertified_extension(repo):
    root, graph_db = repo
    (root / "mod.java").write_text("class Mod {}\n", encoding="utf-8")
    _, result, payload = _dispatch(root, graph_db, "syntax", {"path": "mod.java"})
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "PASS_THROUGH"
    assert payload["evidence"]["omissions"] == ["syntax_language_removed"]


def test_missing_graph_abstains_incomplete_not_replace(tmp_path: Path):
    """A certified graph kind without graph.db must abstain honestly."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _, result, payload = _dispatch(
        tmp_path, tmp_path / "graph.db", "callers", {"symbol": "helper"}
    )
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "AUGMENT"
    assert payload["evidence"]["semantics"] == "incomplete"
    assert "graph_unavailable" in payload["evidence"]["omissions"]


# ------------------------------------------------------ bounded output


def _fan_in_repo(root: Path, callers: int) -> Path:
    """``helper`` referenced by ``callers`` symbols over three edge types.

    Producers cap each list they return (rename keeps 50 rows per edge type),
    so no single list is large; the dict answer is still far over 16 KiB once
    it is carried in its three model-visible copies.
    """
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    nodes = [(1, "Function", "helper", "pkg.util.helper", "pkg/util.py", 1, 2,
              "def helper()", "int", 1, 0, "python", None)]
    edges = []
    for index in range(callers):
        node_id = index + 2
        name = f"caller_with_a_deliberately_long_name_{index:04d}"
        path = f"pkg/callers/module_{index:04d}.py"
        nodes.append((node_id, "Function", name, f"pkg.callers.{name}", path, 1, 2,
                      f"def {name}()", "None", 1, 0, "python", None))
        edge_type = ("CALLS", "IMPORTS", "REFERENCES")[index % 3]
        edges.append((index + 1, node_id, 1, edge_type, 2, path, "import", "CERTIFIED", 1.0))
    graph_db = root / "graph.db"
    _write_graph(graph_db, nodes, edges)
    return graph_db


def test_dict_answer_is_bounded_and_every_projection_agrees(tmp_path):
    graph_db = _fan_in_repo(tmp_path, 180)
    _, result, payload = _dispatch(
        tmp_path, graph_db, "rename", {"symbol": "helper", "new_name": "assist"}
    )
    assert len(result["output"].encode("utf-8")) <= QUERY_RESULT_MAX_BYTES
    omissions = payload["evidence"]["omissions"]
    assert "query_result_byte_limit" in omissions
    truncated = [item for item in omissions
                 if item.startswith("query_result_truncated:answer.edit_sites_by_type.")]
    assert truncated, omissions
    answer = payload["direct_answer"]
    for item in truncated:
        field, counts = item.removeprefix("query_result_truncated:answer.").rsplit(":", 1)
        kept, total = (int(value) for value in counts.split("/"))
        assert total == 50 and kept < total
        assert len(answer["edit_sites_by_type"][field.split(".", 1)[1]]) == kept
    # The three model-visible copies of the answer stay identical.
    assert json.loads(payload["evidence"]["direct_answer_json"]) == answer
    assert payload["honesty"]["payload"] == answer
    assert payload["honesty"]["completeness"] == "incomplete"
    assert payload["decision"]["mode"] == "AUGMENT"
    assert "QUERY_RESULT_TRUNCATED" in payload["decision"]["reason_codes"]
    assert result["returncode"] == 2


def test_dict_bounding_is_deterministic(tmp_path):
    graph_db = _fan_in_repo(tmp_path, 180)
    outputs = {
        _dispatch(tmp_path, graph_db, "rename", {"symbol": "helper"},
                  tool_call_id="same")[1]["output"]
        for _ in range(2)
    }
    assert len(outputs) == 1


def test_repository_wide_literal_search_bounds_its_file_manifest(tmp_path):
    """exact_literal_search returns a dict whose files_observed lists every
    file in scope; on a real repository that alone dwarfs 16 KiB."""
    for index in range(250):
        (tmp_path / f"module_{index:03d}.py").write_text(
            f"value_{index} = {index}\n", encoding="utf-8"
        )
    (tmp_path / "hit.py").write_text("needle = 1\n", encoding="utf-8")
    _, result, payload = _dispatch(
        tmp_path, tmp_path / "graph.db", "exact_literal_search",
        {"literal": "needle", "paths": ["."]},
    )
    assert len(result["output"].encode("utf-8")) <= QUERY_RESULT_MAX_BYTES
    answer = payload["direct_answer"]
    assert [row["path"] for row in answer["matches"]] == ["hit.py"]
    assert any(item.startswith("query_result_truncated:answer.files_observed:")
               for item in payload["evidence"]["omissions"])
    assert payload["decision"]["mode"] == "AUGMENT"


def test_literal_search_respects_match_and_line_caps(tmp_path):
    long_line = "needle " + "x" * 1000
    (tmp_path / "many.py").write_text(
        "\n".join(f"needle_{index} = {index}" for index in range(30)) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "long.py").write_text(long_line + "\n", encoding="utf-8")
    _, result, payload = _dispatch(
        tmp_path, tmp_path / "graph.db", "exact_literal_search",
        {"literal": "needle", "paths": ["long.py", "many.py"]},
    )
    answer = payload["direct_answer"]
    assert len(answer["matches"]) == 20
    assert all(len(row["line_text"].encode("utf-8")) <= 256 for row in answer["matches"])
    omissions = set(payload["evidence"]["omissions"])
    assert {"query_match_limit", "query_line_limit"} <= omissions
    assert payload["decision"]["mode"] == "AUGMENT"
    assert result["returncode"] == 2
    assert json.loads(payload["evidence"]["direct_answer_json"]) == answer


def test_bound_withholds_an_answer_that_cannot_fit():
    result = {
        "schema": "gt.compiled_observation.v1",
        "evidence": {"omissions": [], "direct_answer_json": "{}"},
        "direct_answer": {"blob": "x" * 40_000},
        "decision": {"mode": "REPLACE", "reason_codes": []},
        "honesty": {"payload": {"blob": "x" * 40_000}},
    }
    bounded, added = bound_compiled_observation(result, max_bytes=16_384)
    assert bounded["direct_answer"] is None
    assert bounded["honesty"]["payload"] is None
    assert "query_result_unbounded_payload" in added
    assert len(json.dumps(bounded)) <= 16_384


# ------------------------------------------------------ why_this_edge


def test_why_this_edge_reads_the_edge_from_the_graph(repo):
    root, graph_db = repo
    _, result, payload = _dispatch(root, graph_db, "why_this_edge", {"edge_id": "2"})
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "AUGMENT"
    assert payload["evidence"]["semantics"] == "incomplete"
    answer = payload["direct_answer"]
    assert answer["edge"]["type"] == "CALLS"
    assert answer["source"]["name"] == "run_pipeline"
    assert answer["target"]["name"] == "helper"


def test_why_this_edge_never_certifies_caller_supplied_facts(repo):
    root, graph_db = repo
    invented = {
        "edge_id": "edge-that-does-not-exist",
        "callsite_id": "call-1",
        "edge_kind": "SELECTED_TARGET",
        "target_id": "target-a",
        "candidate_count": 1,
        "candidates": [{"target_id": "target-a", "flow_witnesses": []}],
        "flow_witnesses": {"target-a": []},
        "source_revision": "src",
        "graph_revision": "graph",
        "completion_identity": "build",
        "complete": True,
    }
    _, result, payload = _dispatch(root, graph_db, "why_this_edge", invented)
    assert result["returncode"] == 2
    assert payload["decision"]["mode"] == "PASS_THROUGH"
    assert payload["evidence"]["omissions"] == ["abstention:edge_not_in_graph"]
    assert payload["direct_answer"] is None
    assert "exact" not in {payload["evidence"]["semantics"]}
