from __future__ import annotations

import sqlite3
from pathlib import Path

from gt_engine.context_frontier import (
    ContextFrontierKind,
    FactOrigin,
    FrontierDisposition,
    RepositoryFactTracker,
    compile_incremental_frontier,
)
from gt_engine.graph_context import build_graph_projection
from gt_engine.indexer import IndexBuildStatus, inspect_source_coverage
from gt_engine.language_registry import (
    INDEXABLE_SOURCE_SUFFIXES,
    VALIDATION_SOURCE_SUFFIXES,
    capability_for_path,
)
from gt_engine.preflight import MutationCertainty, adapt_proposed_action
from gt_engine.repository_intelligence import RepositoryIntelligenceStatus
from gt_engine.task_contract import extract_task_contract


def test_certified_language_registry_exposes_structural_support():
    cobol = capability_for_path("src/main.cob")
    python = capability_for_path("src/main.py")

    assert cobol is not None and cobol.validation_relevant is True
    assert cobol.structural_index is True
    assert python is not None and python.structural_index is True
    assert ".cob" in VALIDATION_SOURCE_SUFFIXES
    assert ".cob" in INDEXABLE_SOURCE_SUFFIXES


def test_structural_registry_matches_the_shipped_gt_index_spec_extensions():
    assert INDEXABLE_SOURCE_SUFFIXES == frozenset(
        {
            ".bash",
            ".ac",
            ".am",
            ".cbl",
            ".c",
            ".cc",
            ".cpp",
            ".cob",
            ".cs",
            ".cls",
            ".cmake",
            ".conf",
            ".css",
            ".cue",
            ".cxx",
            ".dockerfile",
            ".ex",
            ".exs",
            ".elm",
            ".go",
            ".gcode",
            ".gradle",
            ".groovy",
            ".h",
            ".hcl",
            ".hpp",
            ".htm",
            ".html",
            ".hxx",
            ".java",
            ".js",
            ".jsx",
            ".kt",
            ".kts",
            ".lua",
            ".m",
            ".mjs",
            ".ml",
            ".mli",
            ".mm",
            ".mk",
            ".nc",
            ".cjs",
            ".md",
            ".php",
            ".proto",
            ".py",
            ".pyi",
            ".pov",
            ".r",
            ".rake",
            ".rb",
            ".red",
            ".rq",
            ".rs",
            ".scm",
            ".sc",
            ".scala",
            ".sh",
            ".sparql",
            ".sql",
            ".stan",
            ".sty",
            ".svelte",
            ".swift",
            ".tf",
            ".toml",
            ".ts",
            ".tsx",
            ".tap",
            ".tex",
            ".ttl",
            ".v",
            ".vim",
            ".yaml",
            ".yml",
            ".cpy",
            ".ss",
        }
    )


def test_index_coverage_reports_certified_cobol_as_indexable(tmp_path: Path):
    (tmp_path / "main.cob").write_text("IDENTIFICATION DIVISION.\nPROGRAM-ID. HELLO.\n")

    coverage = inspect_source_coverage(tmp_path)

    assert coverage.source_files == 1
    assert coverage.indexable_files == 1
    assert coverage.unsupported_suffixes == ()
    assert coverage.status is IndexBuildStatus.AVAILABLE


def test_mixed_python_and_certified_cobol_source_is_complete(tmp_path: Path):
    (tmp_path / "app.py").write_text("def app():\n    return 1\n")
    (tmp_path / "legacy.cob").write_text(
        "IDENTIFICATION DIVISION.\nPROGRAM-ID. LEGACY.\n"
    )

    coverage = inspect_source_coverage(tmp_path)

    assert coverage.source_files == 2
    assert coverage.indexable_files == 2
    assert coverage.unsupported_suffixes == ()
    assert coverage.status is IndexBuildStatus.AVAILABLE


def test_racket_remains_explicitly_unsupported(tmp_path: Path):
    (tmp_path / "main.rkt").write_text("#lang racket\n(displayln \"hi\")\n")

    coverage = inspect_source_coverage(tmp_path)

    assert coverage.source_files == 1
    assert coverage.indexable_files == 0
    assert coverage.unsupported_suffixes == (".rkt",)
    assert coverage.status is IndexBuildStatus.UNSUPPORTED_LANGUAGE


def test_terminal_bench_code_like_suffixes_are_not_silently_ignored(tmp_path: Path):
    """All Terminal-Bench code-like suffixes are structurally accounted."""
    for suffix in (".r", ".red", ".pov"):
        path = tmp_path / f"fixture{suffix}"
        path.write_text("source fixture\n")
    (tmp_path / "fixture.v").write_text(
        "module fixture(input a, output b); assign b = a; endmodule\n"
    )

    coverage = inspect_source_coverage(tmp_path)

    assert coverage.source_files == 4
    assert coverage.indexable_files == 4
    assert coverage.unsupported_suffixes == ()
    assert coverage.status is IndexBuildStatus.AVAILABLE


def _graph_with_duplicate_fts_surfaces(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE nodes ("
            "id INTEGER PRIMARY KEY,label TEXT,name TEXT,qualified_name TEXT,"
            "file_path TEXT,start_line INTEGER,signature TEXT,language TEXT,is_test INTEGER)"
        )
        connection.execute(
            "INSERT INTO nodes VALUES (1,'function','greet','greet','src/greeter.py',7,"
            "'def greet(name: str) -> str','python',0)"
        )
        connection.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(name,file_path)")
        connection.execute(
            "INSERT INTO nodes_fts(rowid,name,file_path) VALUES (1,'greet','src/greeter.py')"
        )
        connection.execute("CREATE VIRTUAL TABLE symbol_content_fts USING fts5(content)")
        connection.execute(
            "INSERT INTO symbol_content_fts(rowid,content) VALUES (1,'greet uppercase greeting')"
        )
        connection.commit()
    finally:
        connection.close()


def test_graph_projection_uses_canonical_positive_lines_and_deduplicates_nodes(tmp_path: Path):
    graph = tmp_path / "graph.db"
    _graph_with_duplicate_fts_surfaces(graph)
    contract = extract_task_contract("Ensure greet returns uppercase text.")

    projection = build_graph_projection(str(graph), contract)

    matching = [fact for fact in projection.semantic_facts if fact.node_id == 1]
    assert len(matching) == 1
    assert matching[0].surface == "nodes_fts"
    assert matching[0].line == 7
    assert matching[0].value == "def greet(name: str) -> str"
    assert matching[0].semantic_certainty == 1.0
    # FTS rank is candidate ordering only.  It is not evidence that the hit is
    # decision-relevant; the action/task linker assigns that separately.
    assert matching[0].retrieval_relevance == 0.0


def test_compound_opaque_interpreter_is_never_claimed_read_only():
    proposal = adapt_proposed_action(
        {
            "command": (
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "Path('/app/app.py').write_text('x = 2\\n')\n"
                "PY\n"
                "pytest -q"
            )
        },
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )

    assert proposal.mutation_certainty is MutationCertainty.MAY_MUTATE
    assert proposal.has_opaque_segments is True
    assert proposal.parse_coverage < 1.0


def test_frontier_abstains_on_same_path_definition_after_model_reads_the_file():
    """After the model reads a file, a definition from that exact path is
    low-marginal: the model already possesses the source bytes.  Re-delivering
    it was the P1-005 waste observed in `largest-eigenval` and
    `fix-code-vulnerability`."""

    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (
            {
                "path": "src/greeter.py",
                "line": 7,
                "symbol": "greet",
                "retrieval_relevance": 1.0,
                "semantic_certainty": 1.0,
            },
        ),
        "definitions": (
            {
                "path": "src/greeter.py",
                "line": 7,
                "symbol": "greet",
                "signature": "def greet(name: str) -> str",
                "language": "python",
                "semantics": "graph_definition",
                "semantic_certainty": 1.0,
            },
        ),
        "references": (),
        "callers": (),
        "project_checks": ("pytest -q",),
    }
    messages = [
        {"role": "user", "content": "Change the greeting."},
        {
            "role": "assistant",
            "content": "",
            "extra": {"actions": [{"command": "sed -n '1,120p' src/greeter.py"}]},
        },
        {"role": "tool", "content": "def greet(name): ..."},
    ]

    decision = compile_incremental_frontier(evidence, messages, source_revision="s1")

    assert decision.disposition is FrontierDisposition.LOW_MARGINAL
    assert decision.facts == ()
    assert decision.accounting[0]["disposition"] == "low_marginal"
    assert decision.rendered == ""


def test_frontier_delivers_same_path_definition_before_any_model_read():
    """Without a prior read observation, a certified definition remains
    deliverable; only the exact read path is suppressed."""

    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (
            {
                "path": "src/greeter.py",
                "line": 7,
                "symbol": "greet",
                "retrieval_relevance": 1.0,
                "semantic_certainty": 1.0,
            },
        ),
        "definitions": (
            {
                "path": "src/greeter.py",
                "line": 7,
                "symbol": "greet",
                "signature": "def greet(name: str) -> str",
                "language": "python",
                "semantics": "graph_definition",
                "semantic_certainty": 1.0,
            },
        ),
        "references": (),
        "callers": (),
        "project_checks": ("pytest -q",),
    }
    messages = [
        {"role": "user", "content": "Change the greet function in src/greeter.py."},
    ]

    decision = compile_incremental_frontier(evidence, messages, source_revision="s1")

    assert decision.disposition is FrontierDisposition.SELECTED_FRONTIER
    assert decision.facts[0].kind is ContextFrontierKind.DEFINITION
    assert decision.facts[0].language == "python"
    assert "def greet(name: str) -> str" in decision.rendered
    assert decision.accounting[0]["disposition"] == "selected_frontier"


def test_frontier_still_delivers_cross_file_caller_after_model_reads_a_different_file():
    """Reading one file must not suppress a caller in a different file; the
    caller connects a distinct affected relation the model has not read."""

    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (),
        "definitions": (
            {
                "path": "src/greeter.py",
                "line": 7,
                "symbol": "greet",
                "signature": "def greet(name: str) -> str",
                "language": "python",
                "semantics": "graph_definition",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        "references": (),
        "callers": (
            {
                "caller_path": "src/main.py",
                "caller_line": 3,
                "caller": "main",
                "target": "greet",
                "language": "python",
                "semantics": "graph_caller",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        "project_checks": ("pytest -q",),
    }
    messages = [
        {"role": "user", "content": "Change the greeting."},
        {
            "role": "assistant",
            "content": "",
            "extra": {"actions": [{"command": "sed -n '1,120p' src/greeter.py"}]},
        },
        {"role": "tool", "content": "def greet(name): ..."},
    ]

    decision = compile_incremental_frontier(evidence, messages, source_revision="s1")

    assert decision.disposition is FrontierDisposition.SELECTED_FRONTIER
    assert decision.facts[0].kind is ContextFrontierKind.CALLER
    assert decision.facts[0].path == "src/main.py"
    assert any(
        row["disposition"] == "low_marginal" for row in decision.accounting
    )


def test_frontier_delivers_path_only_anchor_without_leaking_unrequested_symbol():
    """A path need may receive a file location, but not an unrelated symbol."""

    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (
            {
                "path": "legacy.cob",
                "line": 42,
                "symbol": "WRITE-RECORD",
                "surface": "nodes_fts",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        "definitions": (),
        "references": (),
        "callers": (),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Update the record writer in legacy.cob."}],
        source_revision="s1",
    )

    assert decision.disposition is FrontierDisposition.SELECTED_FRONTIER
    assert decision.facts[0].kind is ContextFrontierKind.FILE
    assert decision.facts[0].path == "legacy.cob"
    assert decision.facts[0].symbol == ""
    assert "legacy.cob:42" in decision.rendered


def test_frontier_never_exposes_internal_source_revision_identifier():
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "secret-internal-revision",
        "graph_revision": "g1",
        "anchors": ({
            "path": "src/app.py",
            "line": 4,
            "symbol": "run",
            "semantic_certainty": 1.0,
            "retrieval_relevance": 1.0,
        },),
        "definitions": ({
            "path": "src/app.py",
            "line": 4,
            "symbol": "run",
            "signature": "def run()",
            "semantic_certainty": 1.0,
            "retrieval_relevance": 1.0,
        },),
        "references": (),
        "callers": (),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Change run in src/app.py"}],
        source_revision="secret-internal-revision",
    )

    assert decision.disposition is FrontierDisposition.SELECTED_FRONTIER
    assert "secret-inter" not in decision.rendered


def test_definition_without_signature_is_represented_by_path_and_symbol():
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": ({
            "path": "src/app.py",
            "line": 4,
            "symbol": "run",
            "semantic_certainty": 1.0,
            "retrieval_relevance": 1.0,
        },),
        "definitions": ({
            "path": "src/app.py",
            "line": 4,
            "symbol": "run",
            "signature": "",
            "semantic_certainty": 1.0,
            "retrieval_relevance": 1.0,
        },),
        "references": (),
        "callers": (),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "tool", "content": "src/app.py:4:def run():"}],
        source_revision="s1",
    )

    assert decision.disposition is FrontierDisposition.REPRESENTED_MESSAGE
    assert decision.rendered == ""


def test_task_start_repository_fact_expires_instead_of_spilling_to_call_two():
    tracker = RepositoryFactTracker(task_start_source_paths=frozenset({"src/app.py"}))
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": ({
            "path": "src/app.py", "line": 4, "symbol": "run",
            "semantic_certainty": 1.0, "retrieval_relevance": 1.0,
        },),
        "definitions": (), "references": (), "callers": (),
    }

    first = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Change src/app.py"}],
        source_revision="s1",
        current_call=1,
        eligible_call=1,
        fact_tracker=tracker,
    )
    second = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Change src/app.py"}],
        source_revision="s1",
        current_call=2,
        eligible_call=2,
        fact_tracker=tracker,
    )

    assert first.disposition is FrontierDisposition.SELECTED_FRONTIER
    assert first.facts[0].provenance.origin is FactOrigin.TASK_START
    assert second.disposition is FrontierDisposition.EXPIRED_WINDOW
    assert second.accounting[0]["eligible_call"] == 1


def test_model_authored_graph_claim_is_controller_only_not_repository_guidance():
    tracker = RepositoryFactTracker(task_start_source_paths=frozenset({"src/app.py"}))
    tracker.record_model_authored_paths(("src/app.py",), action_id=3)
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s2",
        "graph_revision": "g2",
        "anchors": ({
            "path": "src/app.py", "line": 8, "symbol": "new_helper",
            "semantic_certainty": 1.0, "retrieval_relevance": 1.0,
        },),
        "definitions": (), "references": (), "callers": (),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "assistant", "content": "", "extra": {"actions": [
            {"command": "sed -i '8i def new_helper(): pass' src/app.py"}
        ]}}],
        source_revision="s2",
        current_call=4,
        eligible_call=4,
        evidence_action=3,
        fact_tracker=tracker,
    )

    assert decision.disposition is FrontierDisposition.CONTROLLER_ONLY
    assert decision.accounting[0]["origin"] == "model_authored"
    assert decision.rendered == ""


def test_unhealthy_repository_never_fabricates_a_frontier():
    decision = compile_incremental_frontier(
        {
            "status": RepositoryIntelligenceStatus.INDEX_UNAVAILABLE.value,
            "source_revision": "s1",
        },
        [{"role": "user", "content": "Fix it"}],
        source_revision="s1",
    )

    assert decision.disposition is FrontierDisposition.SUBSTRATE_FAILURE
    assert decision.facts == ()
    assert decision.rendered == ""


def test_frontier_budget_omits_complete_fact_instead_of_truncating_it():
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (
            {
                "path": "src/greeter.py",
                "line": 7,
                "symbol": "greet",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        "definitions": (
            {
                "path": "src/greeter.py",
                "line": 7,
                "symbol": "greet",
                "signature": "def greet(name: str) -> str",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        "references": (),
        "callers": (),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Change greet."}],
        source_revision="s1",
        max_chars=1,
    )

    assert decision.disposition is FrontierDisposition.FRONTIER_BUDGET
    assert decision.rendered == ""
    assert decision.candidate_count == decision.accounted_count == 1
    assert decision.accounting[0]["disposition"] == "frontier_budget"


def test_stale_repository_revision_is_rejected_before_delivery():
    decision = compile_incremental_frontier(
        {
            "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
            "available": True,
            "substrate_ready": True,
            "index_current": True,
            "intelligence_valid": True,
            "graph_revision": "g1",
            "source_revision": "s1",
        },
        [{"role": "user", "content": "Fix it"}],
        source_revision="s2",
    )

    assert decision.disposition is FrontierDisposition.STALE_SOURCE_REVISION
    assert decision.rendered == ""


def test_frontier_claim_identity_is_stable_across_source_revisions():
    def evidence(revision: str):
        return {
            "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
            "available": True,
            "substrate_ready": True,
            "index_current": True,
            "intelligence_valid": True,
            "source_revision": revision,
            "graph_revision": f"g-{revision}",
            "anchors": (
                {
                    "path": "src/greeter.py",
                    "line": 7,
                    "symbol": "greet",
                    "retrieval_relevance": 1.0,
                    "semantic_certainty": 1.0,
                },
            ),
            "definitions": (
                {
                    "path": "src/greeter.py",
                    "line": 7,
                    "symbol": "greet",
                    "signature": "def greet(name: str) -> str",
                    "semantic_certainty": 1.0,
                    "retrieval_relevance": 1.0,
                },
            ),
        }

    first = compile_incremental_frontier(
        evidence("s1"), [{"role": "user", "content": "Fix `greet`."}], source_revision="s1"
    )
    second = compile_incremental_frontier(
        evidence("s2"),
        [{"role": "user", "content": "Fix `greet`."}],
        source_revision="s2",
        delivered_claim_ids=frozenset({first.facts[0].claim_id}),
    )

    assert first.facts[0].claim_id
    assert second.disposition is FrontierDisposition.REPRESENTED_MESSAGE
    assert second.accounting[0]["claim_id"] == first.facts[0].claim_id


def test_frontier_claim_identity_is_stable_when_only_line_moves():
    def evidence(line: int):
        return {
            "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
            "available": True,
            "substrate_ready": True,
            "index_current": True,
            "intelligence_valid": True,
            "source_revision": "s1",
            "graph_revision": f"g-{line}",
            "anchors": (
                {
                    "path": "src/module.c",
                    "line": line,
                    "symbol": "PyModuleDef",
                    "retrieval_relevance": 1.0,
                    "semantic_certainty": 1.0,
                },
            ),
            "definitions": (
                {
                    "path": "src/module.c",
                    "line": line,
                    "symbol": "PyModuleDef",
                    "signature": "static struct PyModuleDef module_def",
                    "semantic_certainty": 1.0,
                    "retrieval_relevance": 1.0,
                },
            ),
        }

    messages = [
        {"role": "user", "content": "Update src/module.c."},
        {
            "role": "assistant",
            "content": "",
            "extra": {"actions": [{"command": "sed -n '1,80p' src/module.c"}]},
        },
        {"role": "tool", "content": "source opened"},
    ]
    first = compile_incremental_frontier(evidence(20), messages, source_revision="s1")
    second = compile_incremental_frontier(
        evidence(41),
        messages,
        source_revision="s1",
        delivered_claim_ids=frozenset({first.accounting[0]["claim_id"]}),
    )

    assert second.disposition is FrontierDisposition.REPRESENTED_MESSAGE
    assert second.accounting[0]["claim_id"] == first.accounting[0]["claim_id"]


def test_frontier_deduplicates_multiple_occurrences_of_one_semantic_claim():
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "callers": (
            {
                "caller_path": "interp.py",
                "caller_line": 331,
                "caller": "parse_expr",
                "target": "Pair",
                "confidence": 1.0,
                "retrieval_relevance": 1.0,
                "semantics": "graph_recorded",
                "language": "python",
            },
            {
                "caller_path": "interp.py",
                "caller_line": 530,
                "caller": "parse_expr",
                "target": "Pair",
                "confidence": 1.0,
                "retrieval_relevance": 1.0,
                "semantics": "graph_recorded",
                "language": "python",
            },
        ),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Update parse_expr in interp.py."}],
        source_revision="s1",
        max_facts=3,
    )

    assert decision.candidate_count == decision.accounted_count == 1
    assert len(decision.facts) == 1
    assert decision.facts[0].line == 331
    assert len({fact.claim_id for fact in decision.facts}) == 1


def test_frontier_abstains_from_generic_task_start_symbol_match():
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (
            {
                "path": "interpreter.py",
                "line": 14,
                "symbol": "Pair",
                "surface": "nodes_fts",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
                "relevance_reason_codes": ["exact_distinctive_subject"],
            },
        ),
        "definitions": (),
        "references": (),
        "callers": (),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Implement a Scheme evaluator with pairs."}],
        source_revision="s1",
    )

    assert decision.rendered == ""
    assert decision.disposition is FrontierDisposition.LOW_PRECISION
    assert decision.accounting[0]["disposition"] == "no_decision_anchor"


def test_task_path_alone_does_not_expose_unrequested_generic_definition():
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (),
        "definitions": (
            {
                "path": "src/module.c",
                "line": 20,
                "symbol": "PyModuleDef",
                "signature": "static struct PyModuleDef module_def",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Update src/module.c."}],
        source_revision="s1",
    )

    assert decision.rendered == ""
    assert decision.accounting[0]["disposition"] == "no_decision_anchor"


def test_malformed_structural_symbol_is_rejected_before_provider_delivery():
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (),
        "definitions": (
            {
                "path": "src/portfolio.c",
                "line": 7,
                "symbol": "* portfolio_risk_c(...) ",
                "signature": "double * portfolio_risk_c(...) ",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
    }

    decision = compile_incremental_frontier(
        evidence,
        [{"role": "user", "content": "Fix portfolio_risk_c in src/portfolio.c."}],
        source_revision="s1",
    )

    assert decision.rendered == ""
    assert decision.accounting[0]["disposition"] == "low_precision"


def test_frontier_rejects_out_of_range_relevance_instead_of_delivering():
    evidence = {
        "status": RepositoryIntelligenceStatus.HEALTHY_CURRENT.value,
        "available": True,
        "substrate_ready": True,
        "index_current": True,
        "intelligence_valid": True,
        "source_revision": "s1",
        "graph_revision": "g1",
        "anchors": (
            {
                "path": "bottle.py",
                "line": 10,
                "symbol": "app",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 3.5,
            },
        ),
        "definitions": (
            {
                "path": "bottle.py",
                "line": 10,
                "symbol": "app",
                "signature": "def app(self):",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 3.5,
            },
        ),
    }

    decision = compile_incremental_frontier(
        evidence, [{"role": "user", "content": "Fix header validation."}], source_revision="s1"
    )

    assert decision.disposition is FrontierDisposition.LOW_PRECISION
    assert decision.accounting[0]["disposition"] == "invalid_relevance"


def test_read_path_detection_is_literal_and_excludes_ranges_and_composites():
    from gt_engine.context_frontier import _already_read_paths

    messages = [
        {
            "role": "assistant",
            "content": "",
            "extra": {"actions": [{"command": "sed -n '1,120p' src/greeter.py"}]},
        },
        {
            "role": "assistant",
            "content": "",
            "extra": {"actions": [{"command": "cat /app/lib/util.py"}]},
        },
        {
            "role": "assistant",
            "content": "",
            "extra": {"actions": [{"command": "pytest -q && echo done"}]},
        },
    ]

    read_paths = _already_read_paths(messages)

    assert "src/greeter.py" in read_paths
    assert "lib/util.py" in read_paths
    assert "1,120p" not in read_paths
    assert "echo" not in read_paths
