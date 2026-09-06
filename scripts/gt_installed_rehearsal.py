"""Synthetic-transport rehearsal through Harbor's installed agent and verifier."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class RehearsalTransport(BaseHTTPRequestHandler):
    requests: list[dict] = []
    commands: list[str] = []
    bootstrap_requests: list[dict] = []
    interrupt_at_ordinal: int | None = None
    provider_wait_started = threading.Event()
    release_provider_wait = threading.Event()

    def do_POST(self):  # noqa: N802
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.requests.append(request)
        # F10 companion: the select_catalog bootstrap is a GT-internal turn that
        # precedes the agent's first action. It must be answered on its own terms
        # and must NOT consume a scenario ordinal, or every later canned response
        # is served to the wrong agent step and the whole repair scenario shifts.
        if self._is_select_catalog_request(request):
            self.bootstrap_requests.append(request)
            self._respond_select_catalog(len(self.bootstrap_requests) - 1)
            return
        ordinal = len(self.requests) - 1 - len(self.bootstrap_requests)
        if ordinal == 0:
            command = "python3 -m unittest -v"
        elif ordinal == 1:
            command = "cat calculator.py test_calculator.py"
        elif ordinal == 2:
            command = "python3 -c \"print('x'*25000); print('REPAIR_OPERATOR=+'); print('y'*25000)\""
        elif ordinal == 3:
            observed = json.dumps(request["messages"][-1])
            reference = re.search(r"gt-evidence read ([0-9a-f]{64})", observed)
            if reference is None:
                self.send_error(422, "recoverable artifact absent")
                return
            command = f"gt-evidence read {reference.group(1)} 25001 128"
        elif ordinal == 4:
            observed = json.dumps(request["messages"][-1])
            if "REPAIR_OPERATOR=+" not in observed:
                self.send_error(422, "needed evidence was not recovered")
                return
            command = "python3 -c \"from pathlib import Path; p=Path('calculator.py'); p.write_text(p.read_text().replace('left - right', 'left + right'))\""
        elif ordinal == 5:
            command = "cat calculator.py"
        elif ordinal == 6:
            # Provider wait is part of the synthetic transport. Real background
            # graph work continues while the agent awaits this response.
            if self.interrupt_at_ordinal == ordinal:
                self.provider_wait_started.set()
                self.release_provider_wait.wait(timeout=600)
                # The interruption scenario never invents a provider response.
                # Releasing only lets ThreadingHTTPServer shut down after the
                # real client process has been terminated.
                self.close_connection = True
                return
            time.sleep(3)
            command = "python3 -B -m unittest -v"
        elif ordinal == 7:
            command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        else:
            self.send_error(422, "unexpected continuation")
            return
        self.commands.append(command)
        payload = json.dumps({
            "id": f"synthetic-transport-{ordinal}", "object": "chat.completion",
            "model": "synthetic-transport", "created": 0,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "Synthetic transport rehearsal action.",
                "tool_calls": [{"id": f"synthetic-call-{ordinal}", "type": "function",
                                "function": {"name": "bash", "arguments": json.dumps({"command": command})}}],
            }}],
            # Synthetic values exercise the wire schema; they are never billed
            # usage evidence. The rehearsal receipt labels the entire result.
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                      "prompt_tokens_details": {"cached_tokens": 0}},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def _is_select_catalog_request(request: dict) -> bool:
        """A bootstrap turn is identified by its offered tool, not by position."""
        for tool in request.get("tools") or ():
            function = tool.get("function") if isinstance(tool, dict) else None
            if isinstance(function, dict) and function.get("name") == "select_catalog":
                return True
        return False

    def _respond_select_catalog(self, ordinal: int) -> None:
        """Answer the bootstrap with a real tool call over the offered catalog."""
        request = self.bootstrap_requests[ordinal]
        visible = re.findall(r"\"id\": *\"(focus-[0-9a-f]+)\"", json.dumps(request))
        arguments = json.dumps({"ids": visible[:1]})
        payload = json.dumps({
            "id": f"synthetic-bootstrap-{ordinal}", "object": "chat.completion",
            "model": "synthetic-transport", "created": 0,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "Synthetic transport catalog selection.",
                "tool_calls": [{"id": f"synthetic-bootstrap-call-{ordinal}",
                                "type": "function",
                                "function": {"name": "select_catalog",
                                             "arguments": arguments}}],
            }}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                      "prompt_tokens_details": {"cached_tokens": 0}},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def _process_tree_script(*, proc_root: str = "/proc", send_signal: bool = True) -> str:
    return r'''import json, os, signal
root = ''' + repr(proc_root) + r'''
def processes():
    found = {}
    for name in os.listdir(root):
        if not name.isdigit():
            continue
        try:
            raw = open(f"{root}/{name}/cmdline", "rb").read()
            argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
            stat = open(f"{root}/{name}/stat", "r", encoding="ascii").read().rsplit(") ", 1)[1].split()
            found[int(name)] = {"pid": int(name), "ppid": int(stat[1]), "start_ticks": int(stat[19]), "argv": argv}
        except (FileNotFoundError, ProcessLookupError):
            pass
        except PermissionError:
            raise SystemExit("process_probe_unreadable")
        except (OSError, UnicodeError, ValueError, IndexError):
            raise SystemExit("process_probe_malformed")
    return found
rows = processes()
matches = []
for row in rows.values():
    argv = row["argv"]
    if "scripts.miniswe_supervisor" not in argv or "--task-id" not in argv:
        continue
    index = argv.index("--task-id")
    if index + 1 < len(argv) and argv[index + 1] == "synthetic-repair":
        matches.append(row)
if len(matches) != 1:
    raise SystemExit(f"expected_one_supervisor:{len(matches)}")
supervisor = matches[0]
owned = {supervisor["pid"]}
changed = True
while changed:
    changed = False
    for row in rows.values():
        if row["ppid"] in owned and row["pid"] not in owned:
            owned.add(row["pid"]); changed = True
identities = [{"pid": rows[pid]["pid"], "start_ticks": rows[pid]["start_ticks"]} for pid in sorted(owned)]
baseline = [{"pid": row["pid"], "start_ticks": row["start_ticks"]} for row in sorted(rows.values(), key=lambda item: item["pid"])]
if ''' + repr(send_signal) + r''':
    os.kill(supervisor["pid"], signal.SIGTERM)
print(json.dumps({"schema": "gt.rehearsal_interruption_trigger.v1", "signal": "SIGTERM", "selector_count": 1, "supervisor": {"pid": supervisor["pid"], "start_ticks": supervisor["start_ticks"]}, "process_tree": identities, "baseline_processes": baseline}, sort_keys=True))
'''


def _identity_scan_script(
    identities: list[dict], baseline: list[dict], *, proc_root: str = "/proc"
) -> str:
    encoded = json.dumps(identities, separators=(",", ":"))
    baseline_encoded = json.dumps(baseline, separators=(",", ":"))
    return r'''import json, os
expected = json.loads(''' + repr(encoded) + r''')
baseline = json.loads(''' + repr(baseline_encoded) + r''')
root = ''' + repr(proc_root) + r'''
rows = {}
for name in os.listdir(root):
    if not name.isdigit():
        continue
    try:
        stat = open(f"{root}/{name}/stat", "r", encoding="ascii").read().rsplit(") ", 1)[1].split()
        rows[int(name)] = {"pid": int(name), "ppid": int(stat[1]), "start_ticks": int(stat[19])}
    except (FileNotFoundError, ProcessLookupError):
        pass
    except PermissionError:
        raise SystemExit("process_probe_unreadable")
    except (OSError, UnicodeError, ValueError, IndexError):
        raise SystemExit("process_probe_malformed")
control = set()
pid = os.getpid()
while pid in rows and pid not in control:
    control.add(pid)
    pid = rows[pid]["ppid"]
survivors = []
for identity in expected:
    pid = int(identity["pid"])
    row = rows.get(pid)
    if row is not None and row["start_ticks"] == int(identity["start_ticks"]):
        survivors.append(identity)
baseline_identities = {(int(item["pid"]), int(item["start_ticks"])) for item in baseline}
new_processes = [
    {"pid": row["pid"], "start_ticks": row["start_ticks"]}
    for row in sorted(rows.values(), key=lambda item: item["pid"])
    if row["pid"] not in control
    and (row["pid"], row["start_ticks"]) not in baseline_identities
]
control_ancestry = [
    {"pid": rows[pid]["pid"], "start_ticks": rows[pid]["start_ticks"]}
    for pid in sorted(control)
]
print(json.dumps({"schema": "gt.rehearsal_process_teardown.v1", "checked": len(expected), "baseline_checked": len(baseline), "survivors": survivors, "new_processes": new_processes, "control_probe_ancestry": control_ancestry}, sort_keys=True))
'''


async def _exec_environment_python(environment, source: str) -> dict:
    result = await environment.exec(
        f"python3 -c {shlex.quote(source)}", timeout_sec=30, user="root"
    )
    if result.return_code != 0:
        raise RuntimeError(
            f"task environment process probe failed ({result.return_code}): "
            f"{result.stderr or result.stdout}"
        )
    value = json.loads(str(result.stdout or "").strip())
    if not isinstance(value, dict):
        raise ValueError("task environment process probe returned a non-object")
    return value


def _interruption_issues(
    *, report: dict, product: dict, trajectory: dict, trigger: dict,
    teardown: dict, runtime_errors: list[str], request_count: int,
) -> list[str]:
    """Assess only interruption facts; verifier reward is deliberately absent."""
    issues: list[str] = []
    supervisor = report.get("supervisor")
    if report.get("terminal") != "timeout" or report.get("exit_code") != 3:
        issues.append("terminal_not_interrupted")
    if not isinstance(supervisor, dict) or supervisor.get("reason") != "supervisor_termination":
        issues.append("supervisor_termination_missing")
    if product.get("status") != "ERROR" or product.get("terminal") != "timeout":
        issues.append("runtime_receipt_not_interrupted")
    if product.get("research_valid") is not False:
        issues.append("interruption_research_claim")
    if trigger.get("selector_count") != 1 or trigger.get("signal") != "SIGTERM":
        issues.append("signal_trigger_unbound")
    process_tree = trigger.get("process_tree")
    baseline = trigger.get("baseline_processes")
    if not isinstance(process_tree, list) or not process_tree:
        issues.append("process_tree_unbound")
    if not isinstance(baseline, list) or not baseline:
        issues.append("process_baseline_unbound")
    supervisor_identity = trigger.get("supervisor")
    if not isinstance(supervisor_identity, dict) or supervisor_identity not in (process_tree or []):
        issues.append("supervisor_identity_unbound")
    if teardown.get("checked") != len(process_tree or []) or teardown.get("survivors") != []:
        issues.append("descendants_survived")
    if (teardown.get("baseline_checked") != len(baseline or [])
            or teardown.get("new_processes") != []):
        issues.append("late_descendants_survived")
    if request_count != 7:
        issues.append("interruption_request_count")
    if (trajectory.get("info") or {}).get("exit_status") == "Submitted":
        issues.append("interruption_reported_submitted")
    # The outer supervisor deliberately overwrites the worker's completed-form
    # receipt with issue_runtime_receipt_failure. The verifier must report this
    # exact absence-of-claims shape; any missing or additional finding is drift.
    expected_errors = [
        "synthetic_transport_not_paid_evidence",
        "product_not_completed",
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
    ]
    if runtime_errors != expected_errors:
        issues.append("unexpected_runtime_receipt_errors")
    return issues


def _is_exact_repair_patch(payload: bytes) -> bool:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    changed = [
        line for line in text.splitlines()
        if line[:1] in {"+", "-"} and not line.startswith(("+++", "---"))
    ]
    return (
        text.count("diff --git ") == 1
        and text.startswith("diff --git a/calculator.py b/calculator.py\n")
        and changed == ["-    return left - right", "+    return left + right"]
    )


def _pre_repair_source_stable(events: list[dict], commands: list[str]) -> bool:
    """Bind the first real test to the sole later source edit by action identity."""
    if len(commands) < 5:
        return False
    first_command = hashlib.sha256(commands[0].encode()).hexdigest()
    first_checks = [
        row for row in events
        if row.get("event") == "execution_evidence"
        and row.get("command_sha256") == first_command
    ]
    if len(first_checks) != 1 or not isinstance(first_checks[0].get("action_id"), int):
        return False
    # Provider ordinal zero produced this first action. The actual repair is
    # provider ordinal four, so the witnessed action-id offset binds its row.
    repair_action = first_checks[0]["action_id"] + 4
    transactions = [row for row in events if row.get("event") == "edit_transaction"]
    premature = [
        row for row in transactions
        if int(row.get("action_index", -1)) < repair_action and row.get("changed_paths")
    ]
    repairs = [
        row for row in transactions
        if row.get("action_index") == repair_action
        and row.get("changed_paths") == ["calculator.py"]
    ]
    return (
        not premature
        and len(repairs) == 1
        and first_checks[0].get("repository_revision") == repairs[0].get("pre_revision")
        and repairs[0].get("pre_revision") != repairs[0].get("post_revision")
    )


def shape_mismatch(kind: str, predicate: str, **observed_expected: Any) -> dict[str, Any] | None:
    """Name a shape the auditor cannot audit, or return None when it can.

    Pairs are (observed, expected). A predicate that declines because the run
    did not match the shape it knows must say so: silently returning False is
    indistinguishable from having audited the run and found a violation, and a
    blocked verdict that gives no reason is indistinguishable from a considered
    refusal. This is diagnostic only - every caller still fails closed.
    """
    fields: dict[str, Any] = {}
    mismatched = False
    for name, (observed, expected) in observed_expected.items():
        fields[name] = observed
        fields[f"expected_{name}"] = expected
        if observed != expected:
            mismatched = True
    if not mismatched:
        return None
    return {"predicate": predicate, "reason": kind, **fields}


def write_fixture(root: Path, image: str) -> None:
    (root / "environment").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "instruction.md").write_text(
        "Repair calculator.add so addition returns the sum. Run the tests and submit.\n"
    )
    (root / "task.toml").write_text(
        'version = "1.0"\n[agent]\ntimeout_sec = 600\n'
        '[verifier]\ntimeout_sec = 120\n[environment]\nbuild_timeout_sec = 300\n'
    )
    (root / "environment" / "calculator.py").write_text(
        "def add(left, right):\n    return left - right\n"
    )
    (root / "environment" / "test_calculator.py").write_text(
        "import unittest\nfrom calculator import add\n"
        "class Addition(unittest.TestCase):\n"
        "    def test_sum(self):\n        self.assertEqual(add(2, 3), 5)\n"
    )
    (root / "environment" / "Dockerfile").write_text(
        f"FROM {image}\nUSER root\nWORKDIR /rehearsal\n"
        "COPY calculator.py test_calculator.py ./\n"
        "RUN git init -q && git add . && git -c user.name=Fixture "
        "-c user.email=fixture@example.invalid -c core.hooksPath= commit -qm initial\n"
    )
    (root / "tests" / "test.sh").write_text(
        "#!/bin/bash\nset -eu\nmkdir -p /logs/verifier\n"
        "sha256sum /logs/artifacts/model.patch > /logs/verifier/patch.sha256\n"
        "mkdir /tmp/rehearsal-verifier\n"
        "cp /tests/calculator.py /tests/test_calculator.py /tmp/rehearsal-verifier/\n"
        "cd /tmp/rehearsal-verifier\n"
        "git init -q\n"
        "git apply /logs/artifacts/model.patch\n"
        "if PYTHONPATH=$PWD python3 -B /tests/verify.py; then echo 1 > /logs/verifier/reward.txt; "
        "else echo 0 > /logs/verifier/reward.txt; fi\n"
    )
    for name in ("calculator.py", "test_calculator.py"):
        (root / "tests" / name).write_bytes((root / "environment" / name).read_bytes())
    (root / "tests" / "verify.py").write_text(
        "import sys\nsys.path.pop(0)\nfrom calculator import add\n"
        "assert add(2, 3) == 5\nassert add(-4, 2) == -2\nassert add(0, 7) == 7\n"
    )


async def run(args) -> dict:
    from pier.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig, TrialConfig
    from pier.trial.hooks import TrialEvent
    from pier.trial.trial import Trial

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    fixture = root / "fixture"
    write_fixture(fixture, args.image)
    forced_interruption = args.scenario == "forced-interruption"
    handler = type("RunTransport", (RehearsalTransport,), {
        "requests": [], "commands": [], "bootstrap_requests": [],
        "interrupt_at_ordinal": 6 if forced_interruption else None,
        "provider_wait_started": threading.Event(),
        "release_provider_wait": threading.Event(),
    })
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = dict(
        OPENAI_BASE_URL=args.transport_url, OPENAI_API_KEY="synthetic-transport-only",
        GT_RESOURCE_ATTESTATION_KEY=hashlib.sha256(b"synthetic-transport-only").hexdigest(),
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        GT_RETRIEVAL_MODE="hybrid_required",
        GT_PROVIDER_CONTEXT_WINDOW_TOKENS="65536",
        GT_PROVIDER_RESERVED_OUTPUT_TOKENS="2048",
        GT_PROVIDER_CONTEXT_WINDOW_SOURCE="synthetic_transport_contract",
    )
    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)
    receipt = {"schema": "gt.installed_rehearsal.v1", "synthetic_transport": True,
               "paid_smoke_eligible": False, "status": "FAILED", "image": args.image,
               "scenario": args.scenario}
    trial_task = None
    wait_task = None
    try:
        trial = await Trial.create(TrialConfig(
            task=TaskConfig(path=fixture), trials_dir=root / "trials", trial_name="repair",
            environment=EnvironmentConfig(
                import_path="eval.pier_filtered_docker:PierFilteredDockerEnvironment",
            ),
            agent=AgentConfig(import_path="eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe246Agent",
                              model_name="synthetic-transport", kwargs={
                                  "task_id": "synthetic-repair", "product_source_sha": args.source_sha,
                                  "time_budget_seconds": 600, "max_iterations": 9,
                                  "synthetic_transport_url": args.transport_url,
                              }),
        ))
        interruption: dict[str, dict] = {}

        async def observe_teardown(_event) -> None:
            if not forced_interruption:
                return
            trigger = interruption.get("trigger") or {}
            identities = trigger.get("process_tree")
            baseline = trigger.get("baseline_processes")
            if (not isinstance(identities, list) or not identities
                    or not isinstance(baseline, list) or not baseline):
                raise RuntimeError("interruption process tree unavailable")
            interruption["teardown"] = await _exec_environment_python(
                trial._environment, _identity_scan_script(identities, baseline)
            )

        trial.add_hook(TrialEvent.VERIFICATION_START, observe_teardown)
        trial_task = asyncio.create_task(trial.run())
        if forced_interruption:
            wait_task = asyncio.create_task(asyncio.to_thread(
                handler.provider_wait_started.wait, 600
            ))
            done, _pending = await asyncio.wait(
                {trial_task, wait_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if trial_task in done:
                await trial_task
                raise RuntimeError("trial ended before interruption point")
            if not wait_task.result():
                raise TimeoutError("provider interruption point was not reached")
            interruption["trigger"] = await _exec_environment_python(
                trial._environment, _process_tree_script()
            )
        result = await trial_task
        receipt["trial_result"] = result.model_dump(mode="json")
        from scripts.gt_audit import audit_run

        audits = audit_run(root / "trials")
        receipt["audit"] = [asdict(item) for item in audits]
        from gt_harness.runtime_receipts import verify_runtime_receipt

        product = root / "trials" / "repair" / "agent" / "gt-run.json"
        receipt["runtime_receipt_errors"] = verify_runtime_receipt(product)
        product_receipt = json.loads(product.read_text())
        report = json.loads(product.with_name("miniswe_report.json").read_text())
        trajectory = json.loads(product.with_name("miniswe_trajectory.json").read_text())
        executed = [message for message in trajectory.get("messages", [])
                    if message.get("role") == "tool"]
        receipt["reproduction_verified"] = (
            len(executed) == 7
            and executed[0].get("extra", {}).get("returncode") == 1
            and "FAILED" in executed[0].get("content", "")
            and executed[6].get("extra", {}).get("returncode") == 0
            and "OK" in executed[6].get("content", "")
        )
        state = product.parent / "gt-state" / "synthetic-repair"
        events = [json.loads(line) for line in (state / "events.jsonl").read_text().splitlines()]
        checks = [row for row in events if row.get("event") == "execution_evidence"]
        receipt["pre_repair_source_stable"] = _pre_repair_source_stable(
            events, handler.commands
        )
        receipt["execution_evidence_verified"] = False
        # F11: a predicate that declines to evaluate must say so. Returning
        # False for "the run did not have the shape I know how to audit" is
        # indistinguishable from "I audited it and it violated", and the caller
        # cannot tell an unproven property from a broken one.
        receipt["predicates_not_evaluated"] = []
        # Counts are derived, never asserted. A GT-internal bootstrap turn adds a
        # provider request without adding an agent step, so a hardcoded total
        # silently switches this predicate off instead of failing it, and a
        # predicate that declines to evaluate is indistinguishable from one that
        # found a violation.
        agent_requests = len(handler.requests) - len(handler.bootstrap_requests)
        entry = shape_mismatch(
            "unaudited_run_shape", "execution_evidence_verified",
            checks=(len(checks), 2),
            agent_requests=(agent_requests, len(handler.commands)),
        )
        if entry is not None:
            entry["bootstrap_requests"] = len(handler.bootstrap_requests)
            receipt["predicates_not_evaluated"].append(entry)
        else:
            chain_valid = True
            # Bind by scenario position, never by command-string identity: two
            # steps issuing the same command would make an identity lookup
            # return the first match and audit the wrong request. The digest
            # comparison below then VERIFIES the binding instead of forming it.
            for row, step_ordinal, outcome, returncode in (
                (checks[0], 0, "fail", 1),
                (checks[1], 6, "pass", 0),
            ):
                request_index = step_ordinal + 1 + len(handler.bootstrap_requests)
                if step_ordinal >= len(handler.commands) or request_index >= len(handler.requests):
                    chain_valid = False
                    break
                command = handler.commands[step_ordinal]
                blob = (state / "execution_evidence" / f"{row['artifact_sha256']}.json").read_bytes()
                payload = json.loads(blob)
                raw = (state / row["raw_blob"]).read_bytes()
                admitted = []
                for message in handler.requests[request_index].get("messages", []):
                    content = str(message.get("content") or "")
                    if "[GT_EXECUTION_EVIDENCE]\n" in content:
                        suffix = content.split("[GT_EXECUTION_EVIDENCE]\n", 1)[1]
                        admitted.append(json.JSONDecoder().raw_decode(suffix)[0])
                chain_valid &= (
                    hashlib.sha256(blob).hexdigest() == row["artifact_sha256"]
                    and payload["command_sha256"] == hashlib.sha256(command.encode()).hexdigest()
                    and payload["outcome"] == outcome and payload["returncode"] == returncode
                    and payload["protocol"] == "unittest"
                    and payload["raw_output_sha256"] == hashlib.sha256(raw).hexdigest()
                    and payload["raw_output_bytes"] == len(raw)
                    and payload in admitted
                )
            receipt["execution_evidence_verified"] = bool(
                chain_valid and checks[0]["repository_revision"] != checks[1]["repository_revision"]
            )
        publications = [row for row in events if row.get("event") == "graph_publication"]
        refresh_entry = shape_mismatch(
            "unaudited_run_shape", "native_graph_refresh_verified",
            checks=(len(checks), 2),
        )
        if refresh_entry is not None:
            refresh_entry["publications"] = len(publications)
            refresh_entry["minimum_publications"] = 2
            receipt["predicates_not_evaluated"].append(refresh_entry)
        receipt["native_graph_refresh_verified"] = (
            len(publications) >= 2 and len(checks) == 2
            and publications[-1]["repository_revision"] == checks[-1]["repository_revision"]
            and publications[0]["graph_sha256"] != publications[-1]["graph_sha256"]
        )
        patches = list((root / "trials").rglob("model.patch"))
        verifier_hashes = list((root / "trials").rglob("patch.sha256"))
        if len(patches) == 1 and len(verifier_hashes) == 1:
            digest = hashlib.sha256(patches[0].read_bytes()).hexdigest()
            receipt["patch_sha256"] = digest
            receipt["verifier_patch_matches"] = verifier_hashes[0].read_text().split()[0] == digest
            receipt["exact_repair_patch"] = _is_exact_repair_patch(patches[0].read_bytes())
        verified = result.verifier_result
        rewards = getattr(verified, "rewards", None) if verified is not None else None
        if forced_interruption:
            receipt["interruption"] = interruption
            receipt["fixture_patch_gradable"] = bool(
                isinstance(rewards, dict) and rewards.get("reward") == 1
            )
            issues = _interruption_issues(
                report=report,
                product=product_receipt,
                trajectory=trajectory,
                trigger=interruption.get("trigger") or {},
                teardown=interruption.get("teardown") or {},
                runtime_errors=receipt["runtime_receipt_errors"],
                request_count=len(handler.requests),
            )
            if not receipt.get("verifier_patch_matches"):
                issues.append("verifier_patch_identity_mismatch")
            if not receipt.get("exact_repair_patch"):
                issues.append("repair_patch_not_exact")
            if not receipt["fixture_patch_gradable"]:
                issues.append("repair_patch_not_gradable")
            if not receipt["pre_repair_source_stable"]:
                issues.append("pre_repair_source_changed")
            if len(handler.commands) != 6:
                issues.append("blocked_provider_returned_action")
            receipt["interruption_issues"] = issues
            if not issues:
                receipt["status"] = "VERIFIED_SYNTHETIC_INTERRUPTION"
        else:
            # F11: a blocked verdict must say why. The scenario step count and
            # audit arity are the scenario's own definition, so the literals
            # stay - what changes is that a mismatch names observed vs expected
            # instead of failing mute. Diagnostic only: the gate below still
            # fails closed on every mismatch.
            verdict_entry = shape_mismatch(
                "acceptance_shape_mismatch", "status",
                commands=(len(handler.commands), 8),
                audits=(len(audits), 1),
            )
            if verdict_entry is not None:
                receipt["acceptance_shape_mismatch"] = verdict_entry
        if forced_interruption:
            pass
        elif (result.exception_info is None and isinstance(rewards, dict)
                and rewards.get("reward") == 1 and receipt.get("verifier_patch_matches")
                # F11: an unevaluated predicate must block acceptance. Without
                # this, a run whose shape the auditor did not recognise reaches
                # the same verdict as one it audited and passed.
                and not receipt.get("predicates_not_evaluated")
                and not receipt.get("acceptance_shape_mismatch")
                and len(handler.commands) == 8 and len(audits) == 1
                and audits[0].verdict == "GREEN-delivered" and audits[0].synthetic_transport
                and receipt["reproduction_verified"]
                and receipt["execution_evidence_verified"]
                and receipt["native_graph_refresh_verified"]
                and receipt["pre_repair_source_stable"]
                and receipt["runtime_receipt_errors"] == ["synthetic_transport_not_paid_evidence"]
                and trajectory.get("info", {}).get("exit_status") == "Submitted"):
            receipt["status"] = "VERIFIED_SYNTHETIC_REPAIR"
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        handler.release_provider_wait.set()
        handler.provider_wait_started.set()
        if wait_task is not None and not wait_task.done():
            await asyncio.gather(wait_task, return_exceptions=True)
        if trial_task is not None and not trial_task.done():
            trial_task.cancel()
            await asyncio.gather(trial_task, return_exceptions=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        receipt["transport_requests"] = len(handler.requests)
        (root / "synthetic-requests.json").write_text(json.dumps(handler.requests))
        (root / "rehearsal.json").write_text(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--transport-url", default="http://host.docker.internal:80/v1")
    parser.add_argument(
        "--scenario", choices=("repair", "forced-interruption"), default="repair"
    )
    args = parser.parse_args()
    receipt = asyncio.run(run(args))
    print(json.dumps(receipt, indent=2))
    accepted = {
        "repair": "VERIFIED_SYNTHETIC_REPAIR",
        "forced-interruption": "VERIFIED_SYNTHETIC_INTERRUPTION",
    }
    return 0 if receipt["status"] == accepted[args.scenario] else 1


if __name__ == "__main__":
    raise SystemExit(main())
