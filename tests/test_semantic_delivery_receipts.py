import hashlib
import json
import shutil
import sqlite3

import pytest

from gt_engine.graph_utilisation import graph_utilisation
from gt_engine.indexer import _graph_phase_metadata
from gt_engine.output_evidence import EvidenceStore
from gt_engine.request_history import store_history_evidence
from gt_harness.product import groundtruth_release
from gt_harness.runtime_receipts import (
    _provider_delivery_receipts,
)
from gt_harness.runtime_receipts import (
    _semantic_graph_deliveries as _verify,
)


def _semantic_graph_deliveries(state, rows, deliveries):
    return _verify(state, rows, deliveries, task_id="fixture", product_source_sha="a" * 40)


def _fixture(tmp_path, mutation=None):
    state = tmp_path / "state"
    task = state / "task"
    task.mkdir(parents=True)
    graph = state / "graph.db"
    content_hash = hashlib.sha256(b"def add(a, b): return a + b\n").hexdigest()
    with sqlite3.connect(graph) as db:
        db.execute("CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE nodes (id, stable_id, label, name, qualified_name, "
                   "file_path, language, start_line, end_line, file_hash)")
        db.execute("INSERT INTO nodes VALUES (1, 'symbol-add', 'Function', 'add', 'add', "
                   "'calculator.py', 'python', 1, 1, ?)", (content_hash,))
    digest = hashlib.sha256(graph.read_bytes()).hexdigest()
    binding = {"repository_root_sha256": "b" * 64, "source_manifest_sha256": "c" * 64,
               "task_id": "fixture", "product_source_sha": "a" * 40,
               "identity_scope": "benchmark_bound"}
    producer = groundtruth_release()["producer_sha256"]
    resource = {"schema": "gt.index_resource.v1", **binding, "status": "completed", "exit_code": 0,
                "error_code": "", "memory_evidence": False, "producer_binary_sha256": producer}
    resource["evidence_sha256"] = hashlib.sha256(json.dumps(
        resource, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    resource_path = graph.with_name("index-resource.json")
    resource_path.write_text(json.dumps(resource))
    graph.with_name("graph.manifest.json").write_text(json.dumps({
        "schema": "gt.graph_certification.v1", **binding, **_graph_phase_metadata(graph),
        "graph_sha256": digest, "graph_bytes": graph.stat().st_size,
        "binary_certified": True, "binary_sha256": producer,
        "index_resource_sha256": hashlib.sha256(resource_path.read_bytes()).hexdigest(),
    }))

    def blob(namespace, value):
        raw = json.dumps(value).encode()
        identity = hashlib.sha256(raw).hexdigest()
        path = task / namespace / f"{identity}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        return identity

    snapshot = {"revision": "source-before-edit", "complete": True, "root_sha256": "b" * 64,
                "files": [{"path": "calculator.py", "sha256": content_hash}]}
    rows = [{"event": "repository_snapshot", "sequence": 1,
             "repository_revision": snapshot["revision"],
             "snapshot_sha256": blob("repository_snapshots", snapshot)}]
    (task / "events.jsonl").write_text(json.dumps(rows[0]) + "\n")
    item = {"stable_id": "symbol-add", "path": "calculator.py", "line": 1,
            "anchor": "calculator.py:1", "score": 0.5, "reasons": ["retrieval:dense"]}
    artifact = {
        "schema": "gt.semantic_localization.v1", "source_revision": snapshot["revision"],
        "graph_revision": digest, "items": [item],
        "ranking": {"graph_content_sha256": digest, "fused": [{
            "stable_id": "symbol-add", "rrf_score": 0.5, "contributing_sources": ["dense"],
            "provenance": {"stable_id": "symbol-add", "node_id": 1, "label": "Function",
                           "name": "add", "qualified_name": "add", "file_path": "calculator.py",
                           "language": "python", "start_line": 1, "end_line": 1,
                           "identity_origin": "stored"},
        }]},
    }
    if mutation:
        mutation(artifact)
    artifact_id = blob("localization_advisory", artifact)
    rendered = "[GT_EVIDENCE:localization]\ncalculator.py:1 score=0.50000000 reasons=retrieval:dense"
    reference = store_history_evidence(
        EvidenceStore(task / "output_evidence"), rendered.encode(),
        kind="decision_evidence",
    )
    unit_id = reference["sha256"]
    wrapper = "[GT_CONTEXT_UNIT] " + json.dumps({
        "action_index": 0,
        "historical": False,
        "source_revision": snapshot["revision"],
        "supersedes": [],
        "supersession_key": "localization:task",
        "unit_id": unit_id,
    }, sort_keys=True, separators=(",", ":")) + "\n" + rendered
    delivery_id = hashlib.sha256(wrapper.encode()).hexdigest()
    delivery_dir = task / "deliveries"
    delivery_dir.mkdir()
    (delivery_dir / f"{delivery_id}.json").write_bytes(wrapper.encode("utf-8"))
    rows.append({
        "event": "decision_context_unit_admitted", "sequence": 3,
        "delivery_identity": delivery_id, "unit_id": unit_id,
        "supersession_key": "localization:task",
        "artifact_reference": reference,
    })
    delivery = {"evidence_type": "localization", "artifact_sha256": artifact_id,
                "dedup_key": f"semantic-localization:{artifact_id}", "event_sequence": 2,
                "context_sha256": delivery_id, "delivery_identity": delivery_id,
                "context_byte_count": len(wrapper.encode())}
    return state, rows, [delivery]


def _replace_delivery(state, rows, deliveries, rendered):
    old_identity = deliveries[0]["delivery_identity"]
    identity = hashlib.sha256(rendered.encode()).hexdigest()
    path = state / "task" / "deliveries" / f"{identity}.json"
    path.write_bytes(rendered.encode())
    deliveries[0].update(
        context_sha256=identity,
        delivery_identity=identity,
        context_byte_count=len(rendered.encode()),
    )
    admitted = next(
        row for row in rows if row["event"] == "decision_context_unit_admitted"
    )
    assert admitted["delivery_identity"] == old_identity
    admitted["delivery_identity"] = identity


def test_primary_graph_localization_counts_without_counting_lexical_or_trace(tmp_path):
    state, rows, deliveries = _fixture(tmp_path)
    assert not graph_utilisation(deliveries)["graph_backed_delivery"]
    verified = _semantic_graph_deliveries(state, rows, deliveries)
    assert graph_utilisation(deliveries, verified_graph_deliveries=verified)["graph_backed_delivery"]
    deliveries[0]["dedup_key"] = "lexical-localization:ordinary"
    assert not _semantic_graph_deliveries(state, rows, deliveries)


def test_collected_evidence_uses_bound_runtime_root_without_reading_original(tmp_path):
    state, rows, deliveries = _fixture(tmp_path)
    rows.insert(0, {
        "event": "runtime_layout", "schema": "gt.event.v1",
        "layout_schema": "gt.runtime_layout.v1",
        "evidence_root": str((state / "task" / "output_evidence").resolve()),
    })
    collected = tmp_path / "collected"
    shutil.copytree(state, collected)
    # The original artifact must not be consulted after collection.
    reference = next(r for r in rows if r["event"] == "decision_context_unit_admitted")["artifact_reference"]
    (state / "task" / "output_evidence" / reference["sha256"]).write_bytes(b"unavailable original")
    assert _semantic_graph_deliveries(collected, rows, deliveries) == frozenset({deliveries[0]["delivery_identity"]})
    reference["root"] = str(tmp_path / "unrelated")
    with pytest.raises(ValueError, match="context_reference_invalid"):
        _semantic_graph_deliveries(collected, rows, deliveries)


@pytest.mark.parametrize(("mutation", "reason"), [
    (lambda a: a.update(source_revision="stale"), "source_revision_mismatch"),
    (lambda a: a.update(graph_revision="a" * 64), "certified_graph_missing"),
    (lambda a: a["ranking"].update(graph_content_sha256="b" * 64), "projection_revision_mismatch"),
    (lambda a: a["ranking"]["fused"][0]["provenance"].update(name="other"), "primary_source_mismatch"),
    (lambda a: a["items"][0].update(anchor="other.py:1"), "primary_source_mismatch"),
])
def test_rehashed_false_primary_evidence_is_rejected(tmp_path, mutation, reason):
    state, rows, deliveries = _fixture(tmp_path, mutation)
    with pytest.raises(ValueError, match=reason):
        _semantic_graph_deliveries(state, rows, deliveries)


def test_modified_blob_and_missing_source_witness_fail_closed(tmp_path):
    state, rows, deliveries = _fixture(tmp_path)
    with pytest.raises(ValueError, match="source_witness_missing"):
        _semantic_graph_deliveries(state, [], deliveries)
    blob = next((state / "task" / "localization_advisory").glob("*.json"))
    blob.write_bytes(blob.read_bytes() + b" ")
    with pytest.raises(ValueError, match="blob_integrity_failed"):
        _semantic_graph_deliveries(state, rows, deliveries)


def test_admitted_bytes_must_match_primary_artifact(tmp_path):
    state, rows, deliveries = _fixture(tmp_path)
    deliveries[0]["context_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="admitted_bytes_mismatch"):
        _semantic_graph_deliveries(state, rows, deliveries)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_root", "absent"])
def test_context_unit_reference_must_resolve_to_exact_canonical_bytes(tmp_path, damage):
    state, rows, deliveries = _fixture(tmp_path)
    admitted = next(
        row for row in rows if row["event"] == "decision_context_unit_admitted"
    )
    reference = admitted["artifact_reference"]
    artifact = state / "task" / "output_evidence" / reference["sha256"]
    if damage == "missing":
        artifact.unlink()
    elif damage == "corrupt":
        artifact.write_bytes(artifact.read_bytes() + b"spliced")
    elif damage == "wrong_root":
        reference["root"] = str(state / "other")
    else:
        admitted.pop("artifact_reference")

    with pytest.raises(ValueError, match="context_reference_(?:missing|invalid)"):
        _semantic_graph_deliveries(state, rows, deliveries)


def test_reference_only_localization_is_available_but_not_credited_as_consumed(tmp_path):
    state, rows, deliveries = _fixture(tmp_path)
    admitted = next(
        row for row in rows if row["event"] == "decision_context_unit_admitted"
    )
    reference = admitted["artifact_reference"]
    old_path = state / "task" / "deliveries" / f"{deliveries[0]['delivery_identity']}.json"
    header = old_path.read_text(encoding="utf-8").split("\n", 1)[0]
    visible = {
        key: reference[key] for key in (
            "schema", "sha256", "total_length", "encoding", "kind",
            "retrieval_command",
        )
    }
    rendered = header + "\n[GT_CONTEXT_UNIT_REFERENCE] " + json.dumps(
        visible, sort_keys=True, separators=(",", ":")
    )
    _replace_delivery(state, rows, deliveries, rendered)

    assert not _semantic_graph_deliveries(state, rows, deliveries)

    visible["sha256"] = "f" * 64
    spliced = header + "\n[GT_CONTEXT_UNIT_REFERENCE] " + json.dumps(
        visible, sort_keys=True, separators=(",", ":")
    )
    _replace_delivery(state, rows, deliveries, spliced)
    with pytest.raises(ValueError, match="context_reference_spliced"):
        _semantic_graph_deliveries(state, rows, deliveries)


def test_delivery_receipt_requires_identity_in_immediate_provider_request():
    identity = "b" * 64
    rows = [
        {
            "event": "evidence_delivery", "event_hash": "a" * 64,
            "sequence": 1, "lane": "sealed", "kind": "localization",
            "evidence_type": "localization", "dedup_key": "localization-1",
            "payload_sha256": identity, "delivery_identity": identity,
            "iteration": 1, "rendered_bytes": 10,
        },
        {
            "event": "receipt", "sequence": 2, "transition": "delivered",
            "dedup_key": "localization-1", "evidence_type": "localization",
            "payload_hash": identity, "iteration": 1,
        },
        {
            "event": "provider_delivery", "sequence": 3, "iteration": 2,
            "request_id": "immediate-unrelated", "delivery_ids": ["c" * 64],
        },
        {
            "event": "provider_delivery", "sequence": 4, "iteration": 3,
            "request_id": "later-containing", "delivery_ids": [identity],
        },
    ]

    with pytest.raises(ValueError, match="delivery_receipt_provider_join_failed"):
        _provider_delivery_receipts(rows)

    rows[2]["delivery_ids"] = [identity]
    receipt = _provider_delivery_receipts(rows)[0]
    assert receipt["provider_request_id"] == "immediate-unrelated"
    assert receipt["delivery_identity"] == identity


def test_delivery_receipt_identity_must_match_delivery_event():
    rows = [
        {
            "event": "evidence_delivery", "event_hash": "a" * 64,
            "sequence": 1, "lane": "sealed", "kind": "localization",
            "evidence_type": "localization", "dedup_key": "localization-1",
            "payload_sha256": "b" * 64, "delivery_identity": "b" * 64,
            "iteration": 1, "rendered_bytes": 10,
        },
        {
            "event": "receipt", "sequence": 2, "transition": "delivered",
            "dedup_key": "localization-1", "evidence_type": "localization",
            "payload_hash": "c" * 64, "iteration": 1,
        },
        {
            "event": "provider_delivery", "sequence": 3, "iteration": 2,
            "request_id": "request", "delivery_ids": ["c" * 64],
        },
    ]

    with pytest.raises(ValueError, match="delivery_receipt_identity_join_failed"):
        _provider_delivery_receipts(rows)


@pytest.mark.parametrize("damage", ["missing_resource", "corrupt_resource", "producer", "task", "release"])
def test_full_producer_certificate_is_required(tmp_path, damage):
    state, rows, deliveries = _fixture(tmp_path)
    resource = state / "index-resource.json"
    if damage == "missing_resource":
        resource.unlink()
    elif damage == "corrupt_resource":
        resource.write_bytes(resource.read_bytes() + b" ")
    else:
        path = state / "graph.manifest.json"
        manifest = json.loads(path.read_text())
        key = {"producer": "binary_sha256", "task": "task_id", "release": "product_source_sha"}[damage]
        manifest[key] = "wrong"
        path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="graph_integrity_failed"):
        _semantic_graph_deliveries(state, rows, deliveries)
