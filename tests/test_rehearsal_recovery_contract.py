"""The rehearsal's recovery step must match the bounded-preview contract.

Run 34668907595 RED'd on ``422 recoverable artifact absent``: the transport
required a ``gt-evidence read <sha>`` pointer in the observed message, but the
pointer-tax fix made previews render head+tail inline. The scenario is now
rebuilt on the real contract -- the marker sits in the elided middle, the
observation carries the truncation note, and recovery re-runs ranged. These
tests fail if either side of that contract drifts.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.gt_installed_rehearsal import RehearsalTransport  # noqa: E402
from gt_engine.output_evidence import EvidenceStore  # noqa: E402

GENERATOR = "print('x'*52000); print('REPAIR_OPERATOR=+'); print('y'*52000)"


def _post(port: int, request: dict) -> tuple[int, dict | str]:
    body = json.dumps(request).encode()
    http = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(http, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


@pytest.fixture
def transport():
    saved_commands, saved_requests = (
        RehearsalTransport.commands, RehearsalTransport.requests)
    RehearsalTransport.commands = ["c0", "c1", "c2"]
    RehearsalTransport.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RehearsalTransport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        RehearsalTransport.commands = saved_commands
        RehearsalTransport.requests = saved_requests


def _request_with_observation(observation: str) -> dict:
    return {"messages": [{"role": "user", "content": "task"},
                         {"role": "tool", "content": observation}]}


def test_generator_output_hides_the_marker_in_the_elided_middle(tmp_path):
    """Ordinal 2 must elide the fact; otherwise ordinal 3 is unrecoverable.

    Drives the real generator and the real preview path, not a reimplementation.
    """
    output = subprocess.run(
        [sys.executable, "-c", GENERATOR],
        check=True, capture_output=True).stdout
    spool = tmp_path / "spool"
    spool.write_bytes(output)
    reference = EvidenceStore(tmp_path / "cas").publish(spool)
    preview = EvidenceStore(tmp_path / "cas").preview(reference)
    assert "[output truncated:" in preview
    assert "REPAIR_OPERATOR=+" not in preview


def test_ordinal_three_serves_ranged_recovery_when_preview_is_bounded(transport):
    preview = ("x" * 100 + "\n[output truncated: 38484 of 104020 chars elided; "
               "use sed -n/head/tail for other ranges]\n" + "y" * 100)
    status, body = _post(transport, _request_with_observation(preview))
    assert status == 200
    command = json.loads(body["choices"][0]["message"]
                         ["tool_calls"][0]["function"]["arguments"])["command"]
    assert "sed -n" in command and "REPAIR_OPERATOR" in command


def test_ordinal_three_rejects_a_marker_leak(transport):
    """If the marker reached the observation, the elision never happened."""
    preview = ("x" * 100 + "\n[output truncated: 38484 of 104020 chars elided; "
               "use sed -n/head/tail for other ranges]\nREPAIR_OPERATOR=+\n"
               + "y" * 100)
    status, body = _post(transport, _request_with_observation(preview))
    assert status == 422
    assert "marker leaked" in body


def test_ordinal_three_rejects_an_unbounded_observation(transport):
    status, body = _post(transport, _request_with_observation("ordinary output"))
    assert status == 422
    assert "bounded preview note absent" in body
