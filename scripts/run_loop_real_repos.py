"""Drive the real run loop over the six benchmark repositories, provider-free.

`graph_transition_study.py` measures what a transition costs the PRODUCER.
`run_loop_graph_accounting.py` measures what it costs the RUN -- but only reads
journals that exist, and the only journals on disk are fixture-scale rehearsals.
The tracker item asks for blocked-versus-background time over the FIXED SIX
repository transitions, which requires the harness's own run loop to execute
those transitions on those repositories. This driver produces exactly those
journals without spending a provider token.

HOW IT WORKS, and why each piece is the shape it is:

* The run loop is the real one: `scripts/miniswe_gt_run.py` builds the pinned
  Mini-SWE-Agent 2.4.6 `DefaultAgent` with `GroundTruthLitellmModel`, the GT
  session, the MiniSweAdapter, the event journal and the graph coordinator --
  identical to what runs inside the task container. The only substitution is
  the transport: OPENAI_BASE_URL points at a loopback HTTP server that answers
  every chat-completion with a scripted bash command. Provider-free by
  construction: no credential is configured anywhere on this path.

* The script is a scenario, not a model: one pre-edit check, ONE transition,
  then idle waits until the background rebuild publishes (polled from the
  run's own journal between requests), a post-edit check, and submission. The
  wait phase exists because publication is adopted at the NEXT action's
  coordinator poll -- a run that submits the instant it edits would show a
  dark interval that never closed, which is a real shape but not the one this
  item measures.

* The transition is byte-identical to the producer study's: the largest
  parseable file (same `_largest_source` rule) gets the same probe appended.
  It is applied by a scripted bash command, not by the driver directly, so the
  loop sees it as a genuine agent edit: workspace diff -> edit_transaction ->
  graph_invalidated -> refresh scheduled -> background build ->
  graph_publication. Applying it out-of-band would leave nothing to measure.

* The check is external on purpose. `check/test_transition_probe.py` lives
  OUTSIDE the copied repository and asserts the probe symbol exists in the
  transition target. It fails before the edit and passes after -- the same
  fail->fix->pass shape the repair rehearsal audits -- while adding zero files
  to the tree the producer indexes, so the graphs stay comparable to the
  producer study's.

WHAT THIS IS NOT: it is not the Docker/Pier rehearsal (`gt_installed_rehearsal`
targets a container task fixture and its acceptance gate; nothing there accepts
a repository). It is not a benchmark -- the transport is canned, so trajectory
quality, task success and receipts are out of scope. What it produces is the
missing input: per-run `events.jsonl` journals over the real repositories that
`run_loop_graph_accounting.account_run_loop` already reads.

The journal lands at `<state-dir>/<task-id>/events.jsonl` inside each run
directory; the run receipt is `<run>/run.json`; the study manifest is
`<out>/run-loop-real-repos.json` carrying each run's accounting row verbatim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:  # `python -m scripts.run_loop_real_repos` first; direct path second
    from scripts.graph_transition_study import _largest_source
    from scripts.run_loop_graph_accounting import account_run_loop, read_journal
except ModuleNotFoundError:  # pragma: no cover - depends on invocation path
    from graph_transition_study import _largest_source  # type: ignore[no-redef]
    from run_loop_graph_accounting import (  # type: ignore[no-redef]
        account_run_loop,
        read_journal,
    )

# The fixed six, named by the tracker's benchmark-scale corpus. Paths are never
# hardcoded into the run: they come from --repos-root or explicit --repo
# name=path pairs, because the same driver runs inside the Linux suite
# container where the corpus is mounted elsewhere.
DEFAULT_REPOSITORIES = (
    "conan-io__conan-17132",
    "kedro-org__kedro-4580",
    "keras-team__keras-20396",
    "dynaconf__dynaconf-1238",
    "deepset-ai__haystack-8489",
    "matplotlib__matplotlib-29431",
)

SUBMIT_COMMAND = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

# A pre-edit check that fails and a post-edit check that passes is the same
# shape the repair rehearsal audits, produced here by one probe file held
# outside the repository under test.
PROBE_SOURCE = '''"""External transition check: fails before the scripted edit, passes after."""
import os
import unittest
from pathlib import Path


class TransitionProbe(unittest.TestCase):
    def test_transition_probe_landed(self):
        target = os.environ.get("GT_TRANSITION_TARGET", "")
        marker = os.environ.get("GT_TRANSITION_MARKER", "")
        self.assertTrue(target, "GT_TRANSITION_TARGET is not set")
        self.assertTrue(marker, "GT_TRANSITION_MARKER is not set")
        text = Path(target).read_text(encoding="utf-8", errors="replace")
        self.assertIn(marker, text)
'''


def transition_addition(suffix: str) -> str:
    """The producer study's transition, as text rather than a file mutation.

    Mirrors `graph_transition_study._apply_transition` exactly -- the same
    append for the same suffix -- because the run-loop measurement must edit
    the same bytes the producer study measured. `tests/test_run_loop_real_
    repos.py` asserts byte-parity against `_apply_transition` so this table
    cannot silently diverge.
    """
    suffix = suffix.lower()
    if suffix == ".py":
        return "\n\ndef gt_transition_probe():\n    return 1\n"
    if suffix == ".go":
        return "\n\nfunc GTTransitionProbe() int {\n\treturn 1\n}\n"
    if suffix == ".rs":
        return "\n\npub fn gt_transition_probe() -> i32 {\n    1\n}\n"
    if suffix in {".ts", ".tsx", ".js"}:
        return "\n\nexport function gtTransitionProbe() { return 1; }\n"
    if suffix == ".java":
        return "\n// gt_transition_probe\n"
    return "\n\ndef gt_transition_probe\n  1\nend\n"


def transition_marker(suffix: str) -> str:
    """The substring the external probe asserts. One stable token per suffix."""
    suffix = suffix.lower()
    if suffix == ".py":
        return "def gt_transition_probe"
    if suffix == ".go":
        return "func GTTransitionProbe"
    if suffix == ".rs":
        return "fn gt_transition_probe"
    if suffix in {".ts", ".tsx", ".js"}:
        return "function gtTransitionProbe"
    if suffix == ".java":
        return "gt_transition_probe"
    return "def gt_transition_probe"


def _tool_named(request: dict, name: str) -> dict | None:
    """Return the offered tool's function block, or None.

    Bootstrap turns are identified by the tool they offer, never by position:
    a GT-internal call that precedes the agent's first action must not consume
    a scenario ordinal, or every later canned response lands on the wrong step.
    """
    for tool in request.get("tools") or ():
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and function.get("name") == name:
            return function
    return None


class ScriptedTransitionTransport(BaseHTTPRequestHandler):
    """Loopback chat-completions server that plays the transition scenario.

    One canned command per agent step. GT-internal bootstrap calls
    (select_catalog, write_persistent_plan) are answered on their own terms and
    never consume an ordinal -- the same rule the installed rehearsal's
    transport follows, for the same reason.
    """

    requests: list[dict] = []
    commands: list[str] = []
    bootstrap_requests: list[dict] = []
    # Scenario fields, bound per run via type() below.
    journal_path: str = ""
    pre_check: str = ""
    edit_command: str = ""
    wait_command: str = ""
    post_check: str = ""
    submit_command: str = SUBMIT_COMMAND
    max_waits: int = 30
    plan_verification_command: str = ""
    waits_served: int = 0
    post_check_served: bool = False
    publication_baseline: int = 0

    def _publication_count(self) -> int:
        """graph_publication rows the run has already journaled.

        The journal is append-only and fsynced per row, so reading its tail
        between requests is a safe way to see a build the coordinator adopted
        at the last action boundary. An absent/unreadable journal is zero --
        which correctly keeps the run waiting rather than pretending a build
        it cannot see has published.
        """
        try:
            with open(self.journal_path, encoding="utf-8") as handle:
                return sum(
                    1
                    for line in handle
                    if '"graph_publication"' in line
                )
        except OSError:
            return 0

    def _next_command(self, ordinal: int) -> str:
        # Scenario state lives on the handler CLASS, not the instance: the
        # server constructs a fresh handler object per request, so assigning
        # to self here would write state nothing else ever reads.
        cls = type(self)
        if ordinal == 0:
            return cls.pre_check
        if ordinal == 1:
            # Snapshot the publication count BEFORE the edit lands: the wait
            # phase then ends on the first publication past this point, i.e.
            # the one adopting the rebuild of the edited tree.
            cls.publication_baseline = self._publication_count()
            return cls.edit_command
        if not cls.post_check_served:
            if (
                self._publication_count() > cls.publication_baseline
                or cls.waits_served >= cls.max_waits
            ):
                cls.post_check_served = True
                return cls.post_check
            cls.waits_served += 1
            return cls.wait_command
        # Once the check has re-run there is nothing left to do but submit.
        # Served repeatedly rather than failing at a fixed ordinal: the plan
        # gate may refuse a submission it has not yet seen justified, and a
        # bounded refusal must be able to concede instead of stranding the run.
        return cls.submit_command

    def do_POST(self):  # noqa: N802
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.requests.append(request)
        if _tool_named(request, "select_catalog") is not None:
            self.bootstrap_requests.append(request)
            self._respond_select_catalog(len(self.bootstrap_requests) - 1)
            return
        plan_tool = _tool_named(request, "write_persistent_plan")
        if plan_tool is not None:
            self.bootstrap_requests.append(request)
            row_ids = (
                plan_tool["parameters"]["properties"]["rows"]["items"]
                ["properties"]["row_id"]["enum"]
            )
            self._respond_bootstrap(
                len(self.bootstrap_requests) - 1, "write_persistent_plan", {
                    "understanding": (
                        "Scripted repository transition: run the external probe "
                        "check, apply the transition edit, re-run the check, "
                        "then submit."
                    ),
                    "rows": [{
                        "row_id": row_id,
                        "approach": (
                            "Append the scripted probe definition to the "
                            "selected file, then verify with the external "
                            "check."
                        ),
                        "anchors": [],
                        "verification_kind": "existing_test",
                        "verification_command": (
                            self.plan_verification_command or self.post_check
                        ),
                    } for row_id in row_ids],
                })
            return
        ordinal = len(self.commands)
        command = self._next_command(ordinal)
        self.commands.append(command)
        payload = json.dumps({
            "id": f"synthetic-transition-{ordinal}", "object": "chat.completion",
            "model": "synthetic-transport", "created": 0,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant",
                "content": "Synthetic transport transition step.",
                "tool_calls": [{"id": f"synthetic-call-{ordinal}", "type": "function",
                                "function": {"name": "bash", "arguments":
                                             json.dumps({"command": command})}}],
            }}],
            # Synthetic values exercise the wire schema; they are never billed
            # usage evidence and the run receipt labels the whole result.
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2,
                      "prompt_tokens_details": {"cached_tokens": 0}},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _respond_select_catalog(self, ordinal: int) -> None:
        """Answer the bootstrap with a real tool call over the offered catalog."""
        request = self.bootstrap_requests[ordinal]
        visible = re.findall(r"\"id\": *\"(focus-[0-9a-f]+)\"", json.dumps(request))
        self._respond_bootstrap(ordinal, "select_catalog", {"ids": visible[:1]})

    def _respond_bootstrap(self, ordinal: int, name: str, arguments: dict) -> None:
        payload = json.dumps({
            "id": f"synthetic-bootstrap-{ordinal}", "object": "chat.completion",
            "model": "synthetic-transport", "created": 0,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant",
                "content": "Synthetic transport bootstrap response.",
                "tool_calls": [{"id": f"synthetic-bootstrap-call-{ordinal}",
                                "type": "function",
                                "function": {"name": name,
                                             "arguments": json.dumps(arguments)}}],
            }}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2,
                      "prompt_tokens_details": {"cached_tokens": 0}},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def resolve_producer_binary(explicit: str | None = None) -> str:
    """The pinned gt-index, resolved the way the engine resolves it.

    Explicit flag first, then the environment, then the locations the
    engine's own resolution checks: PATH and the per-version cache the suite
    provisions at ~/.groundtruth/bin/<version>/gt-index.
    """
    candidates = [
        explicit or "",
        os.environ.get("GT_INDEX_BINARY", ""),
        shutil.which("gt-index") or "",
        str(Path.home() / ".groundtruth" / "bin" / "v1.1.0" / "gt-index"),
        str(Path.home() / ".groundtruth" / "bin" / "v1.1.0" / "gt-index.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return ""


def _check_command(python: str, check_dir: Path) -> str:
    return (
        f"{python} -m unittest discover "
        f"-s {shlex.quote(str(check_dir))} -t {shlex.quote(str(check_dir))} -v"
    )


def _edit_command(python: str, relative: str, addition: str) -> str:
    """Append the transition bytes exactly -- binary mode, no newline folding.

    `_apply_transition` opens the target in text-append mode; on POSIX that
    writes the literal bytes. A binary append reproduces those bytes on every
    platform this driver can run on, so the edited tree is the same tree the
    producer study measured regardless of host line-ending conventions.
    """
    return (
        f"{python} -c \"from pathlib import Path; "
        f"p = Path('{relative}'); "
        f"handle = p.open('ab'); handle.write({addition.encode()!r}); "
        f"handle.close(); print('gt transition applied to {relative}')\""
    )


def _wait_command(python: str, seconds: float) -> str:
    return f"{python} -c \"import time; time.sleep({seconds})\""


def drive_transition(
    *,
    repo_name: str,
    source: Path,
    run_root: Path,
    harness_root: Path,
    task_id: str,
    producer_binary: str,
    product_source_sha: str,
    python: str = "python3",
    wait_seconds: float = 20.0,
    max_waits: int = 45,
    command_timeout: int = 120,
    time_budget_seconds: int = 1500,
    step_limit: int | None = None,
    run_timeout_seconds: int = 0,
    groundtruth_wheel: Path | None = None,
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    """Run one scripted transition through the real loop; return its receipt.

    One repetition = one independent run: a fresh copy of the repository, a
    fresh state namespace, a fresh transport. The journal the accounting
    instrument reads is copied to ``run_root/events.jsonl``.
    """
    receipt: dict[str, Any] = {
        "schema": "gt.run_loop_transition_run.v1",
        "repo": repo_name,
        "task_id": task_id,
        "synthetic_transport": True,
        "paid_smoke_eligible": False,
    }
    work = run_root / "work"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(source, work, symlinks=True)
    target = _largest_source(work)
    if target is None:
        receipt["status"] = "no_parseable_source"
        return receipt
    relative = target.relative_to(work).as_posix()
    suffix = target.suffix.lower()
    addition = transition_addition(suffix)
    marker = transition_marker(suffix)
    receipt["transition"] = {
        "target": relative,
        "target_bytes": target.stat().st_size,
        "suffix": suffix,
        "marker": marker,
        "addition_sha256": hashlib.sha256(addition.encode()).hexdigest(),
    }

    check_dir = run_root / "check"
    check_dir.mkdir(parents=True, exist_ok=True)
    (check_dir / "test_transition_probe.py").write_text(
        PROBE_SOURCE, encoding="utf-8"
    )
    state_dir = run_root / "state"
    journal_source = state_dir / task_id / "events.jsonl"

    pre_check = _check_command(python, check_dir)
    handler = type("RunTransport", (ScriptedTransitionTransport,), {
        "requests": [], "commands": [], "bootstrap_requests": [],
        "journal_path": str(journal_source),
        "pre_check": pre_check,
        "edit_command": _edit_command(python, relative, addition),
        "wait_command": _wait_command(python, wait_seconds),
        "post_check": pre_check,
        "submit_command": SUBMIT_COMMAND,
        "max_waits": max_waits,
        "plan_verification_command": pre_check,
        "waits_served": 0, "post_check_served": False,
        "publication_baseline": 0,
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport_url = f"http://127.0.0.1:{server.server_address[1]}/v1"

    environment = dict(os.environ)
    environment.update({
        "OPENAI_BASE_URL": transport_url,
        "OPENAI_API_KEY": "synthetic-transport-only",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "MSWEA_COST_TRACKING": "ignore_errors",
        # The admission boundary computes its budget from these three; the
        # rehearsal sets the same contract for the same reason.
        "GT_PROVIDER_CONTEXT_WINDOW_TOKENS": "65536",
        "GT_PROVIDER_RESERVED_OUTPUT_TOKENS": "2048",
        "GT_PROVIDER_CONTEXT_WINDOW_SOURCE": "synthetic_transport_contract",
        "GT_TASK_ID": task_id,
        "GT_PRODUCT_SOURCE_SHA": product_source_sha,
        "GT_TRANSITION_TARGET": str(target.resolve()),
        "GT_TRANSITION_MARKER": marker,
        "GT_CHECK_DIR": str(check_dir.resolve()),
    })
    if producer_binary:
        environment["GT_INDEX_BINARY"] = producer_binary
    if groundtruth_wheel is not None and Path(groundtruth_wheel).is_file():
        # The certified producer-side package is a pinned wheel. A host whose
        # site-packages holds a different groundtruth build must not silently
        # run it: the wheel goes first on PYTHONPATH, so the run imports the
        # artifact the pin names rather than whatever happens to be installed.
        wheel_path = str(Path(groundtruth_wheel).resolve())
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            wheel_path + os.pathsep + existing if existing else wheel_path
        )
    if extra_env:
        environment.update(extra_env)

    steps = step_limit if step_limit is not None else max_waits + 8
    command = [
        sys.executable, "-m", "scripts.miniswe_gt_run",
        "--task", ("Run the transition probe check, apply the scripted "
                   "repository transition, re-run the probe check, and submit."),
        "--model", "synthetic-transport",
        "--cwd", str(work),
        "--state-dir", str(state_dir),
        "--output", str(run_root / "trajectory.json"),
        "--metrics", str(run_root / "report.json"),
        "--task-id", task_id,
        "--product-source-sha", product_source_sha,
        "--step-limit", str(steps),
        "--timeout", str(command_timeout),
        "--time-budget-seconds", str(time_budget_seconds),
        "--synthetic-transport",
        *(extra_args or ()),
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=str(harness_root), env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    deadline = run_timeout_seconds or (time_budget_seconds + 900)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=deadline)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate()
    elapsed = round(time.monotonic() - started, 3)
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)

    receipt["seconds"] = elapsed
    receipt["returncode"] = process.returncode
    receipt["timed_out"] = timed_out
    receipt["stderr_tail"] = (stderr or "")[-2000:]
    (run_root / "stdout.log").write_text(stdout or "", encoding="utf-8")
    (run_root / "stderr.log").write_text(stderr or "", encoding="utf-8")

    # The report the runner itself wrote, if it got that far.
    report_path = run_root / "report.json"
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            receipt["terminal"] = report.get("terminal")
            receipt["exit_code"] = report.get("exit_code")
        except ValueError:
            receipt["report_parse_error"] = True

    receipt["transport_requests"] = len(handler.requests)
    receipt["bootstrap_requests"] = len(handler.bootstrap_requests)
    receipt["commands"] = list(handler.commands)
    receipt["waits_served"] = handler.waits_served

    journal_out = run_root / "events.jsonl"
    if journal_source.is_file():
        shutil.copyfile(journal_source, journal_out)
        rows = read_journal(journal_out)
        receipt["journal_events"] = len(rows)
        # The instrument is the authority; its row is embedded verbatim so the
        # manifest reads the same numbers the instrument computes.
        receipt["accounting"] = account_run_loop(rows)
        try:
            from gt_engine.event_journal import verify_event_journal

            check = verify_event_journal(journal_out)
            receipt["journal_valid"] = bool(check.valid)
            receipt["journal_issues"] = list(check.issues)
        except Exception as exc:  # noqa: BLE001 - verification is observational
            receipt["journal_valid_error"] = f"{type(exc).__name__}: {exc}"
        events = {str(row.get("event") or "") for row in rows}
        receipt["edit_observed"] = "edit_transaction" in events
        receipt["graph_invalidated_seen"] = "graph_invalidated" in events
        receipt["graph_publication_seen"] = "graph_publication" in events
    else:
        receipt["journal_events"] = 0
        receipt["status"] = "journal_missing"
        return receipt
    receipt["status"] = (
        "timed_out" if timed_out
        else "submitted" if receipt.get("terminal") in {
            "submitted", "submitted_verified", "submitted_unverified"}
        else "ended"
    )
    return receipt


def _source_sha(harness_root: Path) -> str:
    """The 40-hex product source identity the run binds, or a content hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(harness_root),
            capture_output=True, text=True, timeout=15,
        )
        candidate = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", candidate):
            return candidate
    except (OSError, subprocess.SubprocessError):
        pass
    # Fallback must still be 40 lowercase hex: the identity gate refuses
    # anything else. The driver's own bytes stand in for an unresolvable tree.
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos-root", type=Path,
                        help="Directory holding the six benchmark checkouts")
    parser.add_argument("--repo", action="append", default=[],
                        help="Repository to run: a name under --repos-root, or "
                             "name=/abs/path. Repeatable. Defaults to the "
                             "tracker's fixed six under --repos-root.")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer-binary", default="",
                        help="Path to the pinned gt-index binary; default "
                             "resolves GT_INDEX_BINARY, PATH, and the "
                             "~/.groundtruth/bin cache.")
    parser.add_argument("--python", default="python3",
                        help="Interpreter the scripted commands invoke inside "
                             "the run environment")
    parser.add_argument("--wait-seconds", type=float, default=20.0,
                        help="Length of one idle action while the background "
                             "rebuild runs")
    parser.add_argument("--publish-timeout", type=float, default=900.0,
                        help="Ceiling on total idle waiting for the rebuild "
                             "to publish before the run submits anyway")
    parser.add_argument("--command-timeout", type=int, default=120)
    parser.add_argument("--time-budget-seconds", type=int, default=1500,
                        help="Per-run wall budget handed to the run loop")
    parser.add_argument("--run-timeout-seconds", type=int, default=0,
                        help="Hard driver-side kill; default is the run "
                             "budget plus setup slack")
    parser.add_argument("--groundtruth-wheel", type=Path, default=None,
                        help="Certified groundtruth wheel to prepend to "
                             "PYTHONPATH; defaults to the vendored pin when "
                             "present")
    parser.add_argument("--no-groundtruth-pin", action="store_true",
                        help="Do not prepend the vendored wheel to PYTHONPATH")
    parser.add_argument("--extra-arg", action="append", default=[],
                        help="Extra raw argument passed through to "
                             "miniswe_gt_run (repeatable)")
    args = parser.parse_args()

    repos: list[tuple[str, Path]] = []
    for spec in args.repo:
        name, separator, path = spec.partition("=")
        if separator:
            repos.append((name, Path(path)))
        else:
            if args.repos_root is None:
                parser.error("--repo NAME without =PATH requires --repos-root")
            repos.append((spec, args.repos_root / spec))
    if not repos:
        if args.repos_root is None:
            parser.error("the default six require --repos-root")
        repos = [(name, args.repos_root / name) for name in DEFAULT_REPOSITORIES]

    harness_root = Path(__file__).resolve().parents[1]
    producer = resolve_producer_binary(args.producer_binary or None)
    wheel = args.groundtruth_wheel
    if wheel is None and not args.no_groundtruth_pin:
        vendored = harness_root / "vendor" / "groundtruth_mcp-1.0.0-py3-none-any.whl"
        wheel = vendored if vendored.is_file() else None
    product_source_sha = _source_sha(harness_root)
    # Waits are fixed-length; the cap is a count of waits, derived from the
    # publish timeout so the flag a user sets is a time, not an ordinal.
    max_waits = max(1, int(args.publish_timeout // max(args.wait_seconds, 1)))

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": "gt.run_loop_real_repos.v1",
        "synthetic_transport": True,
        "paid_smoke_eligible": False,
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "harness_root": str(harness_root),
            "producer_binary": producer,
            "producer_binary_sha256": (
                hashlib.sha256(Path(producer).read_bytes()).hexdigest()
                if producer else ""
            ),
            "groundtruth_wheel": str(wheel) if wheel else "",
            "product_source_sha": product_source_sha,
        },
        "repetitions": args.repetitions,
        "repositories": [],
    }
    failures = 0
    for name, source in repos:
        if not source.is_dir():
            manifest["repositories"].append(
                {"repo": name, "status": "source_missing", "source": str(source)})
            failures += 1
            continue
        entry: dict[str, Any] = {"repo": name, "source": str(source), "runs": []}
        for repetition in range(args.repetitions):
            run_root = output / name / f"rep-{repetition}"
            run_root.mkdir(parents=True, exist_ok=True)
            task_id = f"run-loop-{name}-rep-{repetition}"[:120]
            run = drive_transition(
                repo_name=name,
                source=source,
                run_root=run_root,
                harness_root=harness_root,
                task_id=task_id,
                producer_binary=producer,
                product_source_sha=product_source_sha,
                python=args.python,
                wait_seconds=args.wait_seconds,
                max_waits=max_waits,
                command_timeout=args.command_timeout,
                time_budget_seconds=args.time_budget_seconds,
                run_timeout_seconds=args.run_timeout_seconds,
                groundtruth_wheel=wheel,
                extra_args=list(args.extra_arg),
            )
            run["repetition"] = repetition
            entry["runs"].append(run)
            if run.get("status") == "journal_missing":
                failures += 1
            (run_root / "run.json").write_text(
                json.dumps(run, indent=2, sort_keys=True), encoding="utf-8"
            )
        entry["journals"] = [
            str(output / name / f"rep-{index}" / "events.jsonl")
            for index, run in enumerate(entry["runs"])
            if (output / name / f"rep-{index}" / "events.jsonl").is_file()
        ]
        manifest["repositories"].append(entry)
    (output / "run-loop-real-repos.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
