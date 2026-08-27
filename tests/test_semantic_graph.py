from __future__ import annotations

from importlib.metadata import version

import pytest

import gt_engine.semantic_graph as semantic_graph_module
from gt_engine.hybrid_retrieval import EvidenceOrigin, RepositoryDocument
from gt_engine.semantic_graph import (
    SemanticFactKind,
    SemanticGraphStatus,
    compile_semantic_graph,
)


def _document(text: str) -> RepositoryDocument:
    return RepositoryDocument(
        path="src/tensor_parallel.py",
        symbol="parallel_linear",
        text=text,
        start_line=10,
        origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
        origin_revision="source-1",
    )


def test_python_semantic_graph_emits_exact_value_flow_and_linear_shape_contract() -> None:
    projection = compile_semantic_graph(
        (
            _document(
                """from torch.nn import functional as F

def parallel_linear(inputs: Tensor, weight: Tensor, bias: Tensor):
    local_weight = weight.transpose(0, 1)
    return F.linear(inputs, local_weight, bias)
"""
            ),
        ),
        source_revision="source-1",
        task="Fix the weight orientation in parallel_linear",
        anchor_paths=("src/tensor_parallel.py",),
        anchor_symbols=("parallel_linear",),
    )

    assert projection.status is SemanticGraphStatus.READY
    assert {fact.kind for fact in projection.facts} >= {
        SemanticFactKind.VALUE_FLOW,
        SemanticFactKind.SHAPE_CONSTRAINT,
    }
    rendered = "\n".join(fact.rendered for fact in projection.facts)
    assert "local_weight <- weight.transpose(0, 1)" in rendered
    assert "input[-1] == weight[-1]" in rendered
    assert "output[-1] == weight[-2]" in rendered
    assert projection.receipt.documents_attempted == 1
    assert projection.receipt.documents_indexed == 1
    assert projection.receipt.documents_failed == 0
    assert projection.receipt.facts_by_kind["shape_constraint"] >= 1


def test_semantic_graph_rejects_model_authored_echoes() -> None:
    authored = RepositoryDocument(
        path="src/generated_fix.py",
        symbol="fix",
        text="def fix(value):\n    answer = value\n    return answer\n",
        origin=EvidenceOrigin.MODEL_AUTHORED,
        origin_revision="source-2",
    )

    projection = compile_semantic_graph(
        (authored,),
        source_revision="source-2",
        task="Fix fix",
        anchor_paths=("src/generated_fix.py",),
    )

    assert projection.status is SemanticGraphStatus.ABSTAIN
    assert projection.facts == ()
    assert "model_authored_source_rejected" in projection.receipt.limitations


def test_semantic_graph_prioritizes_traceback_backward_slice() -> None:
    projection = compile_semantic_graph(
        (
            _document(
                """def parallel_linear(inputs, weight):
    oriented = weight.transpose(0, 1)
    result = inputs @ oriented
    return result
"""
            ),
        ),
        source_revision="source-1",
        task="Fix parallel_linear",
        anchor_paths=("src/tensor_parallel.py",),
        diagnostics=(
            'File "src/tensor_parallel.py", line 12, in parallel_linear',
            "RuntimeError: mat1 and mat2 shapes cannot be multiplied",
        ),
    )

    assert projection.facts
    assert projection.facts[0].diagnostic_relevant is True
    assert any(fact.subject == "oriented" for fact in projection.facts[:2])


def test_semantic_graph_does_not_guess_method_argument_flow_from_name_only() -> None:
    projection = compile_semantic_graph(
        (
            _document(
                """def transform(value, scale):
    return value * scale

def run(worker, data):
    return worker.transform(data, 2)
"""
            ),
        ),
        source_revision="source-1",
        task="Fix run",
        anchor_paths=("src/tensor_parallel.py",),
    )

    argument_facts = [
        fact for fact in projection.facts
        if fact.kind is SemanticFactKind.CALL_ARGUMENT_FLOW
    ]
    assert argument_facts == []


def test_semantic_graph_indexes_mixed_python_and_typescript_without_fake_limitations() -> None:
    javascript = RepositoryDocument(
        path="src/index.ts",
        symbol="run",
        text="export const run = (value: number) => value + 1;",
        origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
        origin_revision="source-1",
    )
    projection = compile_semantic_graph(
        (_document("value = source\n"), javascript),
        source_revision="source-1",
        task="Fix value",
        anchor_paths=("src/tensor_parallel.py", "src/index.ts"),
    )

    assert projection.status is SemanticGraphStatus.READY
    assert projection.receipt.documents_indexed == 2
    assert "semantic_language_unsupported" not in projection.receipt.limitations


@pytest.mark.parametrize(
    ("path", "source", "target", "returned"),
    (
        (
            "src/index.ts",
            "function run(value: number) { const result = value + 1; return result; }",
            "result",
            "result",
        ),
        (
            "pkg/run.go",
            "package p\nfunc run(value int) int { result := value + 1; return result }",
            "result",
            "result",
        ),
        (
            "src/lib.rs",
            "fn run(value: i32) -> i32 { let result = value + 1; return result; }",
            "result",
            "result",
        ),
    ),
)
def test_cross_language_semantic_slice_emits_exact_assignment_and_return(
    path: str, source: str, target: str, returned: str
) -> None:
    document = RepositoryDocument(
        path=path,
        symbol="run",
        text=source,
        start_line=1,
        origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
        origin_revision="source-1",
    )

    projection = compile_semantic_graph(
        (document,),
        source_revision="source-1",
        task="Change run result flow",
        anchor_paths=(path,),
        anchor_symbols=("run",),
    )

    assert projection.status is SemanticGraphStatus.READY
    assert any(
        fact.kind is SemanticFactKind.VALUE_FLOW and fact.subject == target
        for fact in projection.facts
    )
    assert any(
        fact.kind is SemanticFactKind.RETURN_FLOW and returned in fact.object
        for fact in projection.facts
    )


def test_tree_sitter_runtime_and_large_source_points_are_stable() -> None:
    # py-tree-sitter 0.26.0 returned borrowed Point.row/column references. Rows
    # above CPython's immortal-small-int range were freed and later surfaced as
    # unrelated objects or SIGSEGV. Keep the runtime contract explicit and
    # exercise a real point above that boundary.
    assert version("tree-sitter") == "0.25.2"
    padding = "\n".join(f"// line {line}" for line in range(1, 300))
    document = RepositoryDocument(
        path="pkg/large.go",
        symbol="run",
        text=(
            f"package p\n{padding}\n"
            "func run(value int) int { result := value + 1; return result }\n"
        ),
        start_line=1,
        origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
        origin_revision="source-large",
    )

    projection = compile_semantic_graph(
        (document,),
        source_revision="source-large",
        task="Change run result flow",
        anchor_paths=("pkg/large.go",),
        anchor_symbols=("run",),
    )

    assert projection.status is SemanticGraphStatus.READY
    assert projection.facts
    assert all(isinstance(fact.start_line, int) for fact in projection.facts)
    assert all(isinstance(fact.end_line, int) for fact in projection.facts)
    assert max(fact.start_line for fact in projection.facts) > 256
    assert projection.receipt.builder_version.endswith("tree-sitter-0.25.2")


def test_unsupported_tree_sitter_runtime_fails_before_parsing(monkeypatch) -> None:
    monkeypatch.setattr(
        semantic_graph_module,
        "_TREE_SITTER_RUNTIME_VERSION",
        "0.26.0",
    )

    projection = compile_semantic_graph(
        (
            RepositoryDocument(
                path="pkg/main.go",
                symbol="main",
                text="package main\nfunc main() {}\n",
                origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
                origin_revision="source-runtime-mismatch",
            ),
        ),
        source_revision="source-runtime-mismatch",
    )

    assert projection.status is SemanticGraphStatus.FAILED
    assert projection.receipt.documents_attempted == 0
    assert projection.receipt.limitations == (
        "unsupported_tree_sitter_runtime:expected=0.25.2:actual=0.26.0",
    )


def test_semantic_graph_deduplicates_same_fact_from_overlapping_symbol_documents() -> None:
    source = "def work():\n    return 1\n"
    documents = (
        RepositoryDocument(
            path="src/core.py",
            symbol="core",
            text=source,
            start_line=1,
            end_line=2,
            origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
            origin_revision="source-1",
        ),
        RepositoryDocument(
            path="src/core.py",
            symbol="work",
            text=source,
            start_line=1,
            end_line=3,
            origin=EvidenceOrigin.PREEXISTING_REPOSITORY,
            origin_revision="source-1",
        ),
    )

    projection = compile_semantic_graph(
        documents,
        source_revision="source-1",
        task="Inspect work",
        anchor_paths=("src/core.py",),
        anchor_symbols=("work",),
    )

    assert len(projection.facts) == len({fact.claim_id for fact in projection.facts})
    assert [fact.rendered for fact in projection.facts].count(
        "- Return flow src/core.py:2 (work): work.return <- 1"
    ) == 1
    assert projection.receipt.facts_by_kind["return_flow"] == 1
    assert projection.receipt.duplicate_facts_removed == 1
