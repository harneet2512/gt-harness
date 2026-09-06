from __future__ import annotations

import hashlib
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from scripts.gt_installed_rehearsal import (
    RehearsalTransport,
    _identity_scan_script,
    _interruption_issues,
    _is_exact_repair_patch,
    _pre_repair_source_stable,
    _process_tree_script,
)


class InstalledRehearsalInterruptionTests(unittest.TestCase):
    def test_blocked_provider_request_releases_without_fabricating_response(self):
        handler = type("BlockedTransport", (RehearsalTransport,), {
            "requests": [{} for _ in range(6)],
            "commands": [],
            "interrupt_at_ordinal": 6,
            "provider_wait_started": threading.Event(),
            "release_provider_wait": threading.Event(),
        })
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        outcome: list[object] = []

        def request() -> None:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            try:
                body = json.dumps({"messages": []})
                connection.request("POST", "/v1/chat/completions", body=body,
                                   headers={"Content-Length": str(len(body))})
                outcome.append(connection.getresponse().status)
            except Exception as exc:  # the deliberate close has no HTTP response
                outcome.append(type(exc).__name__)
            finally:
                connection.close()

        client = threading.Thread(target=request)
        server_thread.start()
        client.start()
        try:
            self.assertTrue(handler.provider_wait_started.wait(timeout=2))
            self.assertTrue(client.is_alive())
            self.assertEqual(handler.commands, [])
            handler.release_provider_wait.set()
            client.join(timeout=2)
            self.assertFalse(client.is_alive())
            self.assertEqual(handler.commands, [])
            self.assertNotEqual(outcome, [200])
        finally:
            handler.release_provider_wait.set()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

    def test_interruption_assessment_requires_truthful_terminal_and_teardown(self):
        arguments = {
            "report": {
                "terminal": "timeout", "exit_code": 3,
                "supervisor": {"reason": "supervisor_termination"},
            },
            "product": {"status": "ERROR", "terminal": "timeout", "research_valid": False},
            "trajectory": {"info": {"exit_status": "RunnerTerminationRequested"}},
            "trigger": {
                "signal": "SIGTERM", "selector_count": 1,
                "supervisor": {"pid": 20, "start_ticks": 100},
                "process_tree": [{"pid": 20, "start_ticks": 100},
                                 {"pid": 21, "start_ticks": 101}],
                "baseline_processes": [
                    {"pid": 1, "start_ticks": 1},
                    {"pid": 20, "start_ticks": 100},
                    {"pid": 21, "start_ticks": 101},
                ],
            },
            "teardown": {"checked": 2, "baseline_checked": 3,
                         "survivors": [], "new_processes": []},
            "runtime_errors": [
                "synthetic_transport_not_paid_evidence", "product_not_completed",
                "product_provider_call_conservation_failed",
                "product_input_token_conservation_failed",
                "product_output_token_conservation_failed",
                "product_event_journal_digest_mismatch",
                "product_event_journal_conservation_failed",
                "product_provider_completed_calls_conservation_failed",
                "product_provider_failed_calls_conservation_failed",
                "product_input_tokens_conservation_failed",
                "product_output_tokens_conservation_failed",
                "product_cached_tokens_conservation_failed",
                "product_total_cost_conservation_failed",
                "treatment_provider_admission_census_mismatch",
                "treatment_receipt_missing",
            ],
            "request_count": 7,
        }
        self.assertEqual(_interruption_issues(**arguments), [])

        corruptions = (
            ("product", {"status": "COMPLETED", "terminal": "timeout", "research_valid": False},
             "runtime_receipt_not_interrupted"),
            ("teardown", {"checked": 2, "survivors": [{"pid": 21, "start_ticks": 101}]},
             "descendants_survived"),
            ("runtime_errors", ["synthetic_transport_not_paid_evidence"],
             "unexpected_runtime_receipt_errors"),
            ("trajectory", {"info": {"exit_status": "Submitted"}},
             "interruption_reported_submitted"),
        )
        for field, value, expected in corruptions:
            with self.subTest(field=field):
                changed = {**arguments, field: value}
                self.assertIn(expected, _interruption_issues(**changed))

    def test_exact_patch_allows_only_the_single_repair(self):
        patch = (
            b"diff --git a/calculator.py b/calculator.py\n"
            b"index 1111111..2222222 100644\n--- a/calculator.py\n+++ b/calculator.py\n"
            b"@@ -1,2 +1,2 @@\n def add(left, right):\n"
            b"-    return left - right\n+    return left + right\n"
        )
        self.assertTrue(_is_exact_repair_patch(patch))
        self.assertFalse(_is_exact_repair_patch(
            patch + b"diff --git a/extra.py b/extra.py\n"
        ))
        self.assertFalse(_is_exact_repair_patch(
            patch.replace(b"left + right", b"left * right")
        ))

    def test_pre_repair_stability_joins_command_to_action_and_revision(self):
        commands = ["python3 -m unittest -v", "cat", "large", "read", "repair"]
        command_sha = hashlib.sha256(commands[0].encode()).hexdigest()
        events = [
            {"event": "execution_evidence", "action_id": 1,
             "command_sha256": command_sha, "repository_revision": "before"},
            # Asynchronous publication counts are irrelevant to source stability.
            {"event": "graph_publication", "repository_revision": "before"},
            {"event": "edit_transaction", "action_index": 5,
             "changed_paths": ["calculator.py"], "pre_revision": "before",
             "post_revision": "after"},
        ]
        self.assertTrue(_pre_repair_source_stable(events, commands))
        events.insert(1, {
            "event": "edit_transaction", "action_index": 1,
            "changed_paths": ["__pycache__/calculator.pyc"],
            "pre_revision": "before", "post_revision": "cache",
        })
        self.assertFalse(_pre_repair_source_stable(events, commands))

    @staticmethod
    def _fake_process(root: Path, pid: int, ppid: int, start_ticks: int,
                      argv: list[str]) -> None:
        directory = root / str(pid)
        directory.mkdir()
        (directory / "cmdline").write_bytes(b"\0".join(item.encode() for item in argv) + b"\0")
        fields = ["S", str(ppid), *("0" for _ in range(17)), str(start_ticks)]
        (directory / "stat").write_text(f"{pid} (python) " + " ".join(fields))

    def test_process_probe_fails_closed_on_malformed_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / "30"
            process.mkdir()
            (process / "cmdline").write_bytes(
                b"python3\0-m\0scripts.miniswe_supervisor\0--task-id\0synthetic-repair\0"
            )
            (process / "stat").write_text("malformed")
            result = subprocess.run(
                [sys.executable, "-c", _process_tree_script(
                    proc_root=str(root), send_signal=False
                )], capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("process_probe_malformed", result.stderr)

    def test_teardown_probe_detects_process_started_after_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fake_process(root, 1, 0, 10, ["init"])
            baseline = [{"pid": 1, "start_ticks": 10}]
            self._fake_process(root, 40, 1, 400, ["late-child"])
            result = subprocess.run(
                [sys.executable, "-c", _identity_scan_script(
                    [], baseline, proc_root=str(root)
                )], capture_output=True, text=True, check=True,
            )
            observed = json.loads(result.stdout)
            self.assertEqual(
                observed["new_processes"], [{"pid": 40, "start_ticks": 400}]
            )


if __name__ == "__main__":
    unittest.main()
