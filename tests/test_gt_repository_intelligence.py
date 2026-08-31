"""Executable, source-grounded proof for the HAR-36 Q&A boundary.

The Q&A proof intentionally lives beside its frozen questions.  It calls the
same indexing and graph-projection entrypoints used by the gateway, then
persists the exact source blobs and citations that support each answer.  A
source or persisted-answer mutation is an abstention, never a stale answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from gt_engine.graph_context import (
    build_graph_projection,
    graph_revision,
    graph_surface_receipt,
)
from gt_engine.indexer import IndexBuildStatus, ensure_index_with_receipt
from gt_engine.task_contract import Obligation, TaskContract


class SourceProofError(ValueError):
    """Raised when a frozen Q&A input or persisted proof is no longer valid."""


@dataclass(frozen=True)
class FrozenSourceQuestion:
    question_id: str
    prompt: str
    source_path: str
    symbol: str
    production_entrypoint: str


@dataclass(frozen=True)
class FrozenSourceSnapshot:
    source_revision: str
    file_hashes: tuple[tuple[str, str], ...]


_QUESTIONS = (
    FrozenSourceQuestion(
        "Q1",
        "How is a repository graph built and certified?",
        "gt_engine/indexer.py",
        "ensure_index_with_receipt",
        "gt_engine.indexer.ensure_index_with_receipt",
    ),
    FrozenSourceQuestion(
        "Q2",
        "How are graph-backed source facts queried?",
        "gt_engine/graph_context.py",
        "build_graph_projection",
        "gt_engine.graph_context.build_graph_projection",
    ),
    FrozenSourceQuestion(
        "Q3",
        "Where does the normal task-start runtime seam consume the graph?",
        "gt_engine/bridge.py",
        "task_start",
        "gt_engine.bridge.GTBridge.task_start",
    ),
    FrozenSourceQuestion(
        "Q4",
        "How is graph freshness and revision identity represented?",
        "gt_engine/graph_lease.py",
        "GraphLease",
        "gt_engine.graph_lease.GraphLease",
    ),
    FrozenSourceQuestion(
        "Q5",
        "How are feature lifecycle rows summarized and checked?",
        "gt_engine/attribution.py",
        "summarize_features",
        "gt_engine.attribution.summarize_features",
    ),
    FrozenSourceQuestion(
        "Q6",
        "How are replay bytes loaded from a persisted proof bundle?",
        "gt_engine/replay_bundle.py",
        "load_replay_bundle",
        "gt_engine.replay_bundle.load_replay_bundle",
    ),
    FrozenSourceQuestion(
        "Q7",
        "How are graph-grounded facts ranked for a decision boundary?",
        "gt_engine/graph_evidence.py",
        "rank_graph_evidence",
        "gt_engine.graph_evidence.rank_graph_evidence",
    ),
    FrozenSourceQuestion(
        "Q8",
        "How are runtime hooks installed at the production boundary?",
        "gt_engine/miniswe_runtime.py",
        "install_runtime_hooks",
        "gt_engine.miniswe_runtime.install_runtime_hooks",
    ),
)

_PROOF_SCHEMA = "gt.source_qa.proof.v1"


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _snapshot(root: Path) -> FrozenSourceSnapshot:
    hashes: list[tuple[str, str]] = []
    for question in _QUESTIONS:
        relative = Path(question.source_path)
        path = root / relative
        try:
            blob = path.read_bytes()
        except OSError as exc:
            raise SourceProofError(f"pinned source unreadable: {relative}") from exc
        hashes.append((question.source_path, _sha256(blob)))
    ordered = tuple(sorted(hashes))
    return FrozenSourceSnapshot(_sha256(_canonical(ordered)), ordered)


def _copy_frozen_sources(root: Path) -> None:
    checkout = Path(__file__).resolve().parents[1]
    for question in _QUESTIONS:
        source = checkout / question.source_path
        target = root / question.source_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _citation(root: Path, question: FrozenSourceQuestion) -> dict[str, object]:
    relative = Path(question.source_path)
    path = root / relative
    blob = path.read_bytes()
    text = blob.decode("utf-8")
    lines = text.splitlines()
    pattern = re.compile(
        rf"^\s*(?:class|def)\s+{re.escape(question.symbol)}\b"
    )
    matches = [index for index, line in enumerate(lines, 1) if pattern.search(line)]
    if len(matches) != 1:
        raise SourceProofError(
            f"citation is not uniquely resolvable: {question.source_path}:"
            f"{question.symbol}"
        )
    start = matches[0]
    return {
        "path": question.source_path,
        "symbol": question.symbol,
        "line_start": start,
        "line_end": min(len(lines), start + 3),
        "blob_sha256": _sha256(blob),
    }


def _question_contract(question: FrozenSourceQuestion) -> TaskContract:
    return TaskContract(
        role="source_qa",
        obligations=(
            Obligation(
                f"har36-{question.question_id.lower()}",
                question.prompt,
                "HAR-36 frozen question",
                (question.source_path, question.symbol),
            ),
        ),
    )


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_verified_proof(
    proof_path: Path, *, root: Path, expected: FrozenSourceSnapshot
) -> dict[str, object]:
    try:
        payload = json.loads(proof_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceProofError("persisted Q&A proof is unreadable") from exc
    if payload.get("schema") != _PROOF_SCHEMA:
        raise SourceProofError("persisted Q&A proof schema mismatch")
    if payload.get("source_revision") != expected.source_revision:
        raise SourceProofError("persisted Q&A source revision mismatch")
    if tuple(map(tuple, payload.get("source_files", ()))) != expected.file_hashes:
        raise SourceProofError("persisted Q&A source file hash mismatch")
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != len(_QUESTIONS):
        raise SourceProofError("persisted Q&A answer set is incomplete")
    semantic = [
        {
            "question_id": answer.get("question_id"),
            "prompt": answer.get("prompt"),
            "entrypoint": answer.get("entrypoint"),
            "status": answer.get("status"),
            "citation": answer.get("citation"),
        }
        for answer in answers
    ]
    if payload.get("semantic_sha256") != _sha256(_canonical(semantic)):
        raise SourceProofError("persisted Q&A semantic digest mismatch")
    for question, answer in zip(_QUESTIONS, answers, strict=True):
        if answer.get("question_id") != question.question_id:
            raise SourceProofError("persisted Q&A question order mismatch")
        if answer.get("entrypoint") != question.production_entrypoint:
            raise SourceProofError("persisted Q&A entrypoint mismatch")
        if answer.get("citation") != _citation(root, question):
            raise SourceProofError("persisted Q&A citation no longer resolves")
    return payload


def _execute_questions(
    root: Path,
    state_dir: Path,
    expected: FrozenSourceSnapshot,
    *,
    graph_db: Path | None = None,
) -> Path:
    current = _snapshot(root)
    if current != expected:
        raise SourceProofError("pinned source revision mismatch")

    if graph_db is None:
        receipt = ensure_index_with_receipt(
            root,
            state_dir=state_dir / "graph",
            source_revision=expected.source_revision,
        )
        if receipt.status is not IndexBuildStatus.BUILT or not receipt.graph_db:
            raise SourceProofError(
                f"production index entrypoint did not build: {receipt}"
            )
        graph = Path(receipt.graph_db)
    else:
        graph = graph_db
        graph_revision_value = graph_revision(str(graph))
    if graph_db is None:
        graph_revision_value = receipt.graph_revision
    surface_receipt = graph_surface_receipt(str(graph))
    if not surface_receipt.get("available"):
        raise SourceProofError("production graph projection has no readable database")

    answers: list[dict[str, object]] = []
    for question in _QUESTIONS:
        projection = build_graph_projection(
            str(graph), _question_contract(question), limit=8
        )
        if not projection.revision or not projection.semantic_facts:
            raise SourceProofError(
                f"graph projection did not produce evidence for {question.question_id}"
            )
        answers.append(
            {
                "question_id": question.question_id,
                "prompt": question.prompt,
                "entrypoint": question.production_entrypoint,
                "status": "ANSWERED",
                "citation": _citation(root, question),
                "graph_revision": projection.revision,
            }
        )

    semantic = [
        {
            "question_id": answer["question_id"],
            "prompt": answer["prompt"],
            "entrypoint": answer["entrypoint"],
            "status": answer["status"],
            "citation": answer["citation"],
        }
        for answer in answers
    ]
    proof = {
        "schema": _PROOF_SCHEMA,
        "source_revision": expected.source_revision,
        "source_files": expected.file_hashes,
        "graph": {
            "path": str(graph),
            "graph_revision": graph_revision_value,
            "surface_receipt": surface_receipt,
        },
        "answers": answers,
        "semantic_sha256": _sha256(_canonical(semantic)),
    }
    proof_path = state_dir / "har36-source-qa.json"
    _write_atomic(proof_path, _canonical(proof) + b"\n")
    _read_verified_proof(proof_path, root=root, expected=expected)
    return proof_path


def test_frozen_questions_execute_through_production_graph_path_and_replay(tmp_path):
    root = tmp_path / "frozen-source"
    state = tmp_path / "state"
    _copy_frozen_sources(root)
    expected = _snapshot(root)

    proof_path = _execute_questions(root, state, expected)
    first = _read_verified_proof(proof_path, root=root, expected=expected)
    first_graph = Path(first["graph"]["path"])
    _execute_questions(root, state, expected, graph_db=first_graph)
    second = _read_verified_proof(proof_path, root=root, expected=expected)

    def semantic(payload):
        return payload["semantic_sha256"]

    assert semantic(first) == semantic(second)
    assert len(first["answers"]) == 8
    assert all(answer["status"] == "ANSWERED" for answer in first["answers"])


def test_frozen_question_proof_abstains_on_source_mutation(tmp_path):
    root = tmp_path / "frozen-source"
    state = tmp_path / "state"
    _copy_frozen_sources(root)
    expected = _snapshot(root)
    proof_path = _execute_questions(root, state, expected)
    before = proof_path.read_bytes()

    source = root / "gt_engine" / "graph_context.py"
    source.write_bytes(source.read_bytes() + b"\n# mutation after freeze\n")

    with pytest.raises(SourceProofError, match="pinned source revision mismatch"):
        _execute_questions(root, state, expected)
    assert proof_path.read_bytes() == before


def test_persisted_question_mutation_is_rejected(tmp_path):
    root = tmp_path / "frozen-source"
    state = tmp_path / "state"
    _copy_frozen_sources(root)
    expected = _snapshot(root)
    proof_path = _execute_questions(root, state, expected)
    payload = json.loads(proof_path.read_text(encoding="utf-8"))
    payload["answers"][0]["status"] = "ANSWERED_WITHOUT_CITATION"
    proof_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceProofError, match="semantic digest mismatch"):
        _read_verified_proof(proof_path, root=root, expected=expected)
