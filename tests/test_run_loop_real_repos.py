"""The run-loop driver must produce journals the accounting instrument reads.

`scripts/run_loop_real_repos.py` exists because the tracker item asks for
blocked-versus-background time over the fixed six repository transitions, and
the only run-loop journals on disk are fixture-scale rehearsals. These tests
prove the driver end to end on a small repository without spending a provider
token: the transport is a loopback HTTP server, the run is the real
`miniswe_gt_run` path, and the journal is the run's own.

Three properties matter, each a way the driver could silently produce nothing:

* The transport must answer GT-internal bootstrap calls without consuming an
  action ordinal -- a bootstrap that ate one would serve the edit where the
  pre-edit check belongs and the whole scenario shifts, which is the defect
  the installed rehearsal's transport already had and fixed.
* The transition must be byte-identical to the producer study's
  `_apply_transition`, or the run-loop numbers cannot be read against the
  producer numbers they accompany.
* The end-to-end run must leave a journal the instrument parses: real
  invalidation, real background build, real publication, real checks and
  snapshots -- on a tiny repository, because the six-repo sweep is a paid-time
  operation this test only has to prepare for, not perform.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from scripts import run_loop_real_repos as driver
from scripts.graph_transition_study import _apply_transition, _largest_source
from scripts.run_loop_graph_accounting import account_run_loop, read_journal

REPO_ROOT = Path(__file__).resolve().parents[1]


def _post(url: str, payload: dict) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _chat_request(tools: tuple[str, ...] = ("bash",)) -> dict:
    return {
        "model": "synthetic-transport",
        "messages": [{"role": "user", "content": "work"}],
        "tools": [
            {"type": "function", "function": {"name": name, "parameters": {}}}
            for name in tools
        ],
    }


def _make_transport(tmp_path: Path, journal_rows: list[dict] | None = None):
    journal = tmp_path / "events.jsonl"
    if journal_rows is not None:
        journal.write_text(
            "".join(json.dumps(row) + "\n" for row in journal_rows),
            encoding="utf-8",
        )
    handler = type("T", (driver.ScriptedTransitionTransport,), {
        "requests": [], "commands": [], "bootstrap_requests": [],
        "journal_path": str(journal),
        "pre_check": "check PRE", "edit_command": "edit TARGET",
        "wait_command": "wait IDLE", "post_check": "check POST",
        "submit_command": "submit NOW", "max_waits": 3,
        "waits_served": 0, "post_check_served": False,
        "publication_baseline": 0,
    })
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, journal, handler


def test_bootstraps_are_answered_without_consuming_action_ordinals(tmp_path):
    server, thread, _journal, handler = _make_transport(tmp_path)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
        status, _ = _post(url, _chat_request(("select_catalog",)))
        assert status == 200
        status, _ = _post(url, {
            **_chat_request(("write_persistent_plan",)),
            "tools": [{"type": "function", "function": {
                "name": "write_persistent_plan",
                "parameters": {"properties": {"rows": {"items": {
                    "properties": {"row_id": {"enum": ["r1", "r2"]}}}}}},
            }}],
        })
        assert status == 200
        # Neither bootstrap consumed a scenario ordinal.
        assert handler.commands == []
        status, body = _post(url, _chat_request())
        assert status == 200
        payload = json.loads(body)
        arguments = json.loads(
            payload["choices"][0]["message"]["tool_calls"][0]["function"]
            ["arguments"]
        )
        assert arguments["command"] == "check PRE"
        status, body = _post(url, _chat_request())
        arguments = json.loads(
            json.loads(body)["choices"][0]["message"]["tool_calls"][0]
            ["function"]["arguments"]
        )
        assert arguments["command"] == "edit TARGET"
        assert handler.commands == ["check PRE", "edit TARGET"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_wait_phase_ends_on_publication_not_on_a_fixed_ordinal(tmp_path):
    """The idle phase exists so the rebuild publishes before the run ends."""
    journal = tmp_path / "events.jsonl"
    journal.write_text(
        json.dumps({"event": "graph_publication"}) + "\n", encoding="utf-8"
    )
    server, thread, journal_path, _handler = _make_transport(tmp_path)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
        served = []
        for _ in range(6):
            status, body = _post(url, _chat_request())
            assert status == 200
            served.append(json.loads(
                json.loads(body)["choices"][0]["message"]["tool_calls"][0]
                ["function"]["arguments"]
            )["command"])
            if served[-1] == "wait IDLE" and len(served) == 3:
                # The rebuild publishes while the run is waiting; the NEXT
                # action must see it and move to the post-edit check.
                with journal_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps({"event": "graph_publication"}) + "\n"
                    )
        assert served == [
            "check PRE", "edit TARGET", "wait IDLE", "check POST",
            "submit NOW", "submit NOW",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_wait_phase_caps_out_when_no_publication_ever_lands(tmp_path):
    """A run whose build never finishes still submits rather than hanging."""
    server, thread, _journal, _handler = _make_transport(tmp_path)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
        served = []
        for _ in range(8):
            status, body = _post(url, _chat_request())
            assert status == 200
            served.append(json.loads(
                json.loads(body)["choices"][0]["message"]["tool_calls"][0]
                ["function"]["arguments"]
            )["command"])
        assert served == [
            "check PRE", "edit TARGET",
            "wait IDLE", "wait IDLE", "wait IDLE",
            "check POST", "submit NOW", "submit NOW",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("suffix", [".py", ".go", ".rs", ".ts", ".java", ".rb"])
def test_transition_addition_is_byte_identical_to_the_producer_study(
    tmp_path, suffix
):
    """The run-loop edit must be the edit the producer numbers describe.

    `_apply_transition` appends in text mode, so a Windows host folds the
    probe's newlines to CRLF while the study's actual measurement environment
    is POSIX. The property that must hold is that the same logical text is
    appended -- the driver's binary-mode append produces the study's POSIX
    bytes on every host -- so the comparison normalizes line endings rather
    than inheriting the host's fold.
    """
    target = tmp_path / f"target{suffix}"
    target.write_bytes(b"def existing():\n    return 0\n")
    before = target.read_bytes()
    _apply_transition(target)
    appended = target.read_bytes()[len(before):]
    assert appended.replace(b"\r\n", b"\n") == (
        driver.transition_addition(suffix).encode()
    )


def test_producer_binary_resolution_prefers_the_explicit_pin(tmp_path):
    binary = tmp_path / "gt-index"
    binary.write_bytes(b"stub")
    assert driver.resolve_producer_binary(str(binary)) == str(binary.resolve())


def _write_fixture_repo(root: Path) -> Path:
    """A small real repository: enough producer input for a genuine build."""
    repo = root / "fixture-repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "helpers.py").write_text(
        "def helper():\n    return 1\n\n\ndef other():\n    return helper()\n",
        encoding="utf-8",
    )
    (repo / "pkg" / "core.py").write_text(
        "from pkg.helpers import helper, other\n\n\n"
        + "\n\n".join(
            f"def feature_{index}():\n    return helper() + other() + {index}"
            for index in range(12)
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", "-c", "core.hooksPath=",
         "commit", "-qm", "initial"],
        cwd=repo, check=True,
    )
    return repo


def _producer_available() -> bool:
    if not driver.resolve_producer_binary():
        return False
    try:
        from groundtruth.runtime.patterns import (  # noqa: F401
            classify_test_observation,
        )
    except Exception:
        return False
    try:
        import minisweagent  # noqa: F401
    except Exception:
        return False
    return shutil.which("git") is not None


@pytest.mark.skipif(sys.platform != "linux", reason=(
    "the engine refuses to launch the producer off POSIX by design: "
    "indexer._has_verified_index_process_tree_guard requires a verifiable "
    "kill-on-close boundary that only the Linux cgroup/process-group path "
    "provides, so a Windows host produces a journal with no graph rows"
))
@pytest.mark.skipif(not _producer_available(), reason=(
    "needs the pinned gt-index binary, the certified groundtruth wheel "
    "(classify_test_observation), mini-swe-agent and git"
))
def test_drive_transition_produces_a_journal_the_instrument_reads(tmp_path):
    """One scripted transition through the real loop, end to end.

    The fixture is small so this runs in seconds, but nothing in the path is
    stubbed: the coordinator schedules a real rebuild, the producer binary
    does real work, and the journal is the same artifact a six-repository
    sweep writes at scale.
    """
    repo = _write_fixture_repo(tmp_path)
    target_before = _largest_source(repo)
    assert target_before is not None
    pristine = tmp_path / "pristine"
    shutil.copytree(repo, pristine)

    run_root = tmp_path / "run"
    run_root.mkdir()
    receipt = driver.drive_transition(
        repo_name="fixture",
        source=repo,
        run_root=run_root,
        harness_root=REPO_ROOT,
        task_id="run-loop-fixture-test",
        producer_binary=driver.resolve_producer_binary(),
        product_source_sha="0" * 40,
        python=sys.executable,
        wait_seconds=3.0,
        max_waits=20,
        command_timeout=60,
        time_budget_seconds=600,
        run_timeout_seconds=600,
        groundtruth_wheel=None,
    )

    journal = run_root / "events.jsonl"
    assert journal.is_file(), (
        f"no journal: rc={receipt.get('returncode')} "
        f"stderr_tail={receipt.get('stderr_tail')!r}"
    )
    rows = read_journal(journal)
    assert rows, "journal written but empty"
    accounting = account_run_loop(rows)
    assert accounting["status"] == "measured"
    assert accounting["schema"] == "gt.run_loop_graph_accounting.v1"
    # The transition happened and the loop saw it.
    assert receipt["edit_observed"] is True
    assert accounting["invalidations"] >= 1
    # The background build ran and was adopted while the run continued.
    assert receipt["graph_publication_seen"] is True
    assert accounting["publications"] >= 2, (
        "expected the task-start publication plus the post-edit adoption"
    )
    assert accounting["builds"] >= 1
    assert accounting["graph_dark_intervals"], "no dark interval recorded"
    assert accounting["ended_graph_dark"] is False
    # Snapshots bracket the actions; checks record the fail-then-pass probe.
    assert accounting["snapshots"] >= 3
    assert accounting["checks"] >= 2
    assert accounting["check_outcomes"].get("fail", 0) >= 1
    assert accounting["check_outcomes"].get("pass", 0) >= 1
    # The edited tree is byte-identical to the producer study's transition.
    edited = run_root / "work" / receipt["transition"]["target"]
    _apply_transition(pristine / receipt["transition"]["target"])
    assert edited.read_bytes() == (
        pristine / receipt["transition"]["target"]
    ).read_bytes()
    assert receipt["journal_valid"] is True
    assert receipt["status"] == "submitted"
