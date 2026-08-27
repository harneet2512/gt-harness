from __future__ import annotations

from gt_engine.hybrid_retrieval import RetrievalIntent, RetrievalState, retrieval_query_terms
from gt_engine.task_contract import extract_task_contract, significant_tokens


def _token_signature(text: str) -> frozenset[str]:
    contract = extract_task_contract(text)
    return frozenset(
        token
        for obligation in contract.obligations
        for token in significant_tokens(obligation.text)
    )


def assert_paraphrase_preserves_anchors(original: str, paraphrase: str) -> None:
    left = _token_signature(original)
    right = _token_signature(paraphrase)
    assert left and right
    assert "parser" in left & right
    assert "core" in left & right


def assert_order_invariant(original: str, reordered: str) -> None:
    assert _token_signature(original) == _token_signature(reordered)
    left = retrieval_query_terms(
        RetrievalState(task_text=original, intent=RetrievalIntent.IMPLEMENTATION_CONTEXT)
    )
    right = retrieval_query_terms(
        RetrievalState(task_text=reordered, intent=RetrievalIntent.IMPLEMENTATION_CONTEXT)
    )
    assert set(left) == set(right)


def assert_distractor_monotonic(original: str, augmented: str) -> None:
    assert _token_signature(original).issubset(_token_signature(augmented))


def assert_homonym_is_scoped(task: str) -> None:
    state = RetrievalState(
        task_text=task,
        intent=RetrievalIntent.IMPLEMENTATION_CONTEXT,
        active_paths=("src/core.py",),
        active_symbols=("parse",),
    )
    query = state.query_text()
    assert "src/core.py" in query
    assert "parse" in query
    assert "tests/example.py" in query
