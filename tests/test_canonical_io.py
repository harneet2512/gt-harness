from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from gt_harness.canonical_io import atomic_json


def test_atomic_json_supports_concurrent_same_process_writers(tmp_path) -> None:
    destination = tmp_path / "receipt.json"
    payloads = [{"writer": index, "value": f"payload-{index}"} for index in range(32)]

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda payload: atomic_json(destination, payload), payloads))

    encoded = destination.read_bytes()
    assert encoded.endswith(b"\n")
    assert json.loads(encoded) in payloads
    assert not list(tmp_path.glob(".receipt.json.tmp.*"))
