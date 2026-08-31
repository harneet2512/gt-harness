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
import platform
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
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
    source_head: str
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
    checkout = Path(__file__).resolve().parents[1]
    try:
        head = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceProofError("source repository head is unavailable") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise SourceProofError("source repository head is malformed")
    return FrozenSourceSnapshot(_sha256(_canonical(ordered)), head, ordered)


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


def _source_answer(root: Path, question: FrozenSourceQuestion) -> str:
    citation = _citation(root, question)
    lines = (root / question.source_path).read_text(encoding="utf-8").splitlines()
    start = int(citation["line_start"]) - 1
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line and len(line) - len(line.lstrip()) <= indent and re.match(
            r"\s*(?:class|def)\s+", line
        ):
            end = index
            break
    excerpt = lines[start:end]
    answer = " ".join(line.strip() for line in excerpt if line.strip())
    if not answer:
        raise SourceProofError(f"source answer is empty: {question.question_id}")
    return answer


def _fact_records(projection) -> list[dict[str, object]]:
    return [
        {
            "surface": fact.surface,
            "node_id": fact.node_id,
            "file_path": fact.file_path,
            "symbol": fact.symbol,
            "kind": fact.kind,
            "value": fact.value,
            "line": fact.line,
            "confidence": fact.confidence,
            "revision": fact.revision,
        }
        for fact in projection.semantic_facts
    ]


def _coverage(facts: list[dict[str, object]], citation: dict[str, object]) -> dict[str, object]:
    return {
        "fact_count": len(facts),
        "surface_count": len({fact["surface"] for fact in facts}),
        "node_count": len({fact["node_id"] for fact in facts}),
        "citation_lines": [citation["line_start"], citation["line_end"]],
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


def _producer_environment() -> dict[str, str]:
    """Identify the exact gt-index binary and runner environment in use."""
    try:
        from groundtruth._binary import find_binary

        from gt_engine.indexer import _seed_binary_env

        _seed_binary_env()
        binary = Path(find_binary()).resolve()
        binary_sha256 = _sha256(binary.read_bytes())
    except (OSError, RuntimeError, ImportError) as exc:
        raise SourceProofError("pinned gt-index producer is unavailable") from exc
    return {
        "producer": "gt-index",
        "binary_path": str(binary),
        "binary_sha256": binary_sha256,
        "gt_index_binary": os.environ.get("GT_INDEX_BINARY", ""),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "implementation": sys.implementation.name,
    }


@contextmanager
def _observe_gt_index() -> list[dict[str, object]]:
    """Observe the subprocess actually launched by the production indexer."""
    from groundtruth import _binary

    observed: list[dict[str, object]] = []
    original_run = _binary.subprocess.run

    def run_and_record(command, *args, **kwargs):
        try:
            result = original_run(command, *args, **kwargs)
        except Exception as exc:
            observed.append(
                {
                    "command": tuple(map(str, command)),
                    "exit_code": None,
                    "exception": type(exc).__name__,
                }
            )
            raise
        observed.append(
            {
                "command": tuple(map(str, command)),
                "exit_code": int(result.returncode),
                "stdout_sha256": _sha256(
                    result.stdout if isinstance(result.stdout, bytes)
                    else str(result.stdout or "").encode("utf-8")
                ),
                "stderr_sha256": _sha256(
                    result.stderr if isinstance(result.stderr, bytes)
                    else str(result.stderr or "").encode("utf-8")
                ),
            }
        )
        return result

    _binary.subprocess.run = run_and_record
    try:
        yield observed
    finally:
        _binary.subprocess.run = original_run


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
    if payload.get("source_revision") != expected.source_head:
        raise SourceProofError("persisted Q&A source revision mismatch")
    if payload.get("source_snapshot_sha256") != expected.source_revision:
        raise SourceProofError("persisted Q&A source snapshot mismatch")
    if payload.get("source_head") != expected.source_head:
        raise SourceProofError("persisted Q&A source head mismatch")
    if tuple(map(tuple, payload.get("source_files", ()))) != expected.file_hashes:
        raise SourceProofError("persisted Q&A source file hash mismatch")
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != len(_QUESTIONS):
        raise SourceProofError("persisted Q&A answer set is incomplete")
    persisted_semantic = [
        {
            "question_id": answer.get("question_id"),
            "prompt": answer.get("prompt"),
            "entrypoint": answer.get("entrypoint"),
            "status": answer.get("status"),
            "answer": answer.get("answer"),
            "citation": answer.get("citation"),
            "evidence": answer.get("evidence"),
            "coverage": answer.get("coverage"),
            "graph_revision": answer.get("graph_revision"),
            "abstention_reason": answer.get("abstention_reason"),
        }
        for answer in answers
    ]
    if payload.get("semantic_sha256") != _sha256(_canonical(persisted_semantic)):
        raise SourceProofError("persisted Q&A semantic digest mismatch")
    graph = payload.get("graph")
    if not isinstance(graph, dict):
        raise SourceProofError("persisted Q&A graph record is missing")
    graph_path = graph.get("path")
    graph_revision_value = graph.get("graph_revision")
    surface_receipt = graph.get("surface_receipt")
    if not isinstance(graph_path, str) or not isinstance(graph_revision_value, str):
        raise SourceProofError("persisted Q&A graph identity is malformed")
    if graph_revision(graph_path) != graph_revision_value:
        raise SourceProofError("persisted Q&A graph revision mismatch")
    if graph_surface_receipt(graph_path) != surface_receipt:
        raise SourceProofError("persisted Q&A graph surface receipt mismatch")
    producer = payload.get("producer_environment")
    if not isinstance(producer, dict) or not producer.get("binary_sha256"):
        raise SourceProofError("persisted Q&A producer identity is missing")
    observed = payload.get("observed_commands")
    if not isinstance(observed, list) or not observed:
        raise SourceProofError("persisted Q&A observed command status is missing")
    for command in observed:
        if not isinstance(command, dict) or "exit_code" not in command:
            raise SourceProofError("persisted Q&A command exit status is missing")

    semantic = []
    for question, answer in zip(_QUESTIONS, answers, strict=True):
        citation = _citation(root, question)
        projection = build_graph_projection(
            graph_path, _question_contract(question), limit=8
        )
        facts = _fact_records(projection)
        expected_status = "ANSWERED" if facts else "ABSTAINED"
        expected_reason = None if facts else "graph_facts_unavailable"
        if answer.get("answer") != (_source_answer(root, question) if facts else ""):
            raise SourceProofError("persisted Q&A answer is not source-derived")
        if answer.get("evidence") != facts:
            raise SourceProofError("persisted Q&A evidence does not match graph facts")
        if answer.get("coverage") != _coverage(facts, citation):
            raise SourceProofError("persisted Q&A coverage mismatch")
        if answer.get("status") != expected_status:
            raise SourceProofError("persisted Q&A status mismatch")
        if answer.get("abstention_reason") != expected_reason:
            raise SourceProofError("persisted Q&A abstention mismatch")
        semantic.append(
            {
                "question_id": answer.get("question_id"),
                "prompt": answer.get("prompt"),
                "entrypoint": answer.get("entrypoint"),
                "status": answer.get("status"),
                "answer": answer.get("answer"),
                "citation": answer.get("citation"),
                "evidence": answer.get("evidence"),
                "coverage": answer.get("coverage"),
                "graph_revision": answer.get("graph_revision"),
                "abstention_reason": answer.get("abstention_reason"),
            }
        )
    if payload.get("semantic_sha256") != _sha256(_canonical(semantic)):
        raise SourceProofError("persisted Q&A semantic digest mismatch")
    for question, answer in zip(_QUESTIONS, answers, strict=True):
        if answer.get("question_id") != question.question_id:
            raise SourceProofError("persisted Q&A question order mismatch")
        if answer.get("prompt") != question.prompt:
            raise SourceProofError("persisted Q&A prompt mismatch")
        if answer.get("entrypoint") != question.production_entrypoint:
            raise SourceProofError("persisted Q&A entrypoint mismatch")
        if answer.get("citation") != _citation(root, question):
            raise SourceProofError("persisted Q&A citation no longer resolves")
        if answer.get("graph_revision") != graph_revision_value:
            raise SourceProofError("persisted Q&A answer graph revision mismatch")
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

    producer_environment = _producer_environment()
    observed_commands: list[dict[str, object]]
    if graph_db is None:
        with _observe_gt_index() as observed_commands:
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
        observed_commands = [{"command": "replay", "exit_code": 0}]
    graph_revision_value = graph_revision(str(graph))
    surface_receipt = graph_surface_receipt(str(graph))
    if not surface_receipt.get("available"):
        raise SourceProofError("production graph projection has no readable database")

    answers: list[dict[str, object]] = []
    for question in _QUESTIONS:
        projection = build_graph_projection(
            str(graph), _question_contract(question), limit=8
        )
        facts = _fact_records(projection)
        citation = _citation(root, question)
        answered = bool(projection.revision and facts)
        answers.append(
            {
                "question_id": question.question_id,
                "prompt": question.prompt,
                "entrypoint": question.production_entrypoint,
                "status": "ANSWERED" if answered else "ABSTAINED",
                "answer": _source_answer(root, question) if answered else "",
                "citation": citation,
                "evidence": facts,
                "coverage": _coverage(facts, citation),
                "graph_revision": projection.revision,
                "abstention_reason": None if answered else "graph_facts_unavailable",
            }
        )

    semantic = [
        {
            "question_id": answer["question_id"],
            "prompt": answer["prompt"],
            "entrypoint": answer["entrypoint"],
            "status": answer["status"],
            "answer": answer["answer"],
            "citation": answer["citation"],
            "evidence": answer["evidence"],
            "coverage": answer["coverage"],
            "graph_revision": answer["graph_revision"],
            "abstention_reason": answer["abstention_reason"],
        }
        for answer in answers
    ]
    proof = {
        "schema": _PROOF_SCHEMA,
        "source_revision": expected.source_head,
        "source_snapshot_sha256": expected.source_revision,
        "source_head": expected.source_head,
        "source_files": expected.file_hashes,
        "graph": {
            "path": str(graph),
            "graph_revision": graph_revision_value,
            "surface_receipt": surface_receipt,
        },
        "producer_environment": producer_environment,
        "observed_commands": observed_commands,
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
    assert all(answer["answer"] for answer in first["answers"])
    assert all(answer["evidence"] for answer in first["answers"])
    assert all(answer["coverage"]["fact_count"] > 0 for answer in first["answers"])


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


def test_persisted_prompt_mutation_is_rejected_even_with_recomputed_digest(tmp_path):
    root = tmp_path / "frozen-source"
    state = tmp_path / "state"
    _copy_frozen_sources(root)
    expected = _snapshot(root)
    proof_path = _execute_questions(root, state, expected)
    payload = json.loads(proof_path.read_text(encoding="utf-8"))
    payload["answers"][0]["prompt"] = "altered prompt"
    semantic = [
        {
            key: answer.get(key)
            for key in (
                "question_id",
                "prompt",
                "entrypoint",
                "status",
                "answer",
                "citation",
                "evidence",
                "coverage",
                "graph_revision",
                "abstention_reason",
            )
        }
        for answer in payload["answers"]
    ]
    payload["semantic_sha256"] = _sha256(_canonical(semantic))
    proof_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceProofError, match="prompt mismatch"):
        _read_verified_proof(proof_path, root=root, expected=expected)
