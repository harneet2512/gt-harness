import copy
import json
from pathlib import Path

from gt_engine.producer_artifact import verify_producer_artifact
from gt_engine.indexer import verify_configured_producer_artifact


def test_shipped_producer_receipt_is_digest_bound(tmp_path: Path) -> None:
    receipt = json.loads(Path("gt_finalstand/receipts/producer_artifact.json").read_text())
    artifact = tmp_path / "producer.bin"
    artifact.write_bytes(b"producer")
    receipt["binary_path"] = str(artifact)
    receipt["binary_sha256"] = __import__("hashlib").sha256(b"producer").hexdigest()
    receipt["binary_bytes"] = artifact.stat().st_size
    body = dict(receipt)
    body.pop("receipt_digest_sha256")
    receipt["receipt_digest_sha256"] = __import__("hashlib").sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert verify_producer_artifact(receipt)[0]
    tampered = copy.deepcopy(receipt)
    tampered["source_commit"] = "0" * 40
    assert verify_producer_artifact(tampered)[1] == "receipt_digest"


def test_indexer_rejects_pinned_receipt_when_binary_changes(tmp_path: Path) -> None:
    receipt_path = Path("gt_finalstand/receipts/producer_artifact.json")
    receipt = json.loads(receipt_path.read_text())
    binary = tmp_path / "producer.bin"
    binary.write_bytes(b"tampered")
    ok, reason = verify_configured_producer_artifact(
        binary_path=binary, receipt_path=receipt_path
    )
    assert not ok
    assert reason == "binary_digest"
