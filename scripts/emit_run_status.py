"""Emit a compact run-status markdown file from a live GT-harness run directory.

Read-only observer: scans the run's event journals (events.jsonl,
provider_events.jsonl) and trajectory under --root and renders a markdown
summary suitable for mid-run inspection. Never emits raw payloads, env values,
or file contents -- counts, hashes, and timestamps only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
from collections import Counter


def _parse_ts(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _count_journal(path: str, counts: Counter, revisions: set, last_events: list) -> str | None:
    last_ts = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = event.get("event") or "?"
                counts[name] += 1
                ts = _parse_ts(event.get("timestamp_utc", ""))
                if ts:
                    last_ts = ts if last_ts is None or ts > last_ts else last_ts
                    last_events.append((ts, event.get("sequence"), name))
                if name == "repository_snapshot":
                    rev = event.get("repository_revision")
                    if rev:
                        revisions.add(rev)
    except OSError:
        pass
    return last_ts


def _trajectory_iterations(root: str) -> int | None:
    candidates = sorted(
        glob.glob(os.path.join(root, "**", "*trajectory*.json"), recursive=True),
        key=os.path.getmtime,
    )
    for path in reversed(candidates):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            for key in ("trajectory", "steps", "messages", "actions"):
                value = data.get(key)
                if isinstance(value, list):
                    return len(value)
    return None


def _delivery_count(root: str) -> int | None:
    total = 0
    found = False
    for deliveries_dir in glob.glob(
        os.path.join(root, "**", "deliveries"), recursive=True
    ):
        if not os.path.isdir(deliveries_dir):
            continue
        found = True
        total += sum(
            1
            for entry in os.listdir(deliveries_dir)
            if entry.endswith(".json")
        )
    return total if found else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="run output root (results dir)")
    parser.add_argument("--task", default="", help="task id")
    parser.add_argument("--run-id", default="", help="workflow run id")
    parser.add_argument("--output", required=True, help="markdown output path")
    parser.add_argument(
        "--budget-seconds", type=int, default=0, help="outer agent budget seconds"
    )
    parser.add_argument(
        "--stdout-log",
        default="",
        help="optional pier stdout log; its tail is appended for live visibility",
    )
    args = parser.parse_args()

    counts: Counter = Counter()
    revisions: set = set()
    last_events: list = []
    last_ts = None
    first_ts = None

    for path in glob.glob(os.path.join(args.root, "**", "*.jsonl"), recursive=True):
        ts = _count_journal(path, counts, revisions, last_events)
        if ts and (last_ts is None or ts > last_ts):
            last_ts = ts
        # first timestamp: cheap rescan of earliest line timestamps
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts0 = _parse_ts(event.get("timestamp_utc", ""))
                    if ts0 and (first_ts is None or ts0 < first_ts):
                        first_ts = ts0
        except OSError:
            pass

    iterations = _trajectory_iterations(args.root)
    deliveries_on_disk = _delivery_count(args.root)

    now = dt.datetime.now(dt.timezone.utc)
    elapsed = (now - first_ts).total_seconds() if first_ts else None
    idle = (now - last_ts).total_seconds() if last_ts else None

    producer_outcomes = Counter()
    # outcomes are recorded as separate entered/returned_* events of the same kind
    producer_outcomes["producer_invocation"] = counts.get("producer_invocation", 0)

    lines = []
    lines.append(f"# run-status — run {args.run_id} task `{args.task}`")
    lines.append("")
    lines.append(f"- generated_utc: {now.isoformat(timespec='seconds')}")
    lines.append(f"- journal_root: `{args.root}`")
    lines.append(f"- first_event_utc: {first_ts.isoformat(timespec='seconds') if first_ts else 'n/a'}")
    lines.append(f"- last_event_utc: {last_ts.isoformat(timespec='seconds') if last_ts else 'n/a'}")
    if elapsed is not None:
        lines.append(f"- elapsed_seconds: {int(elapsed)}")
    if args.budget_seconds:
        remaining = int(args.budget_seconds - elapsed) if elapsed is not None else args.budget_seconds
        lines.append(f"- budget_seconds: {args.budget_seconds} (remaining ~{remaining})")
    if idle is not None:
        lines.append(f"- idle_seconds_since_last_event: {int(idle)}")
    if iterations is not None:
        lines.append(f"- trajectory_iterations: {iterations}")
    if deliveries_on_disk is not None:
        lines.append(f"- delivery_files: {deliveries_on_disk}")
    lines.append("")

    lines.append("## event counts")
    lines.append("")
    lines.append("| event | count |")
    lines.append("|---|---|")
    for name, count in counts.most_common(40):
        lines.append(f"| {name} | {count} |")
    lines.append("")

    lines.append("## graph / producer")
    lines.append("")
    lines.append(f"- graph_publication: {counts.get('graph_publication', 0)}")
    lines.append(f"- distinct repository revisions: {len(revisions)}")
    lines.append(f"- repository_snapshot: {counts.get('repository_snapshot', 0)}")
    lines.append(f"- graph_refresh: {counts.get('graph_refresh', 0)}")
    lines.append(f"- producer_invocation: {counts.get('producer_invocation', 0)}")
    lines.append(f"- provider_delivery: {counts.get('provider_delivery', 0)}")
    lines.append(f"- provider_response: {counts.get('provider_response', 0)}")
    lines.append(f"- receipt: {counts.get('receipt', 0)}")
    lines.append("")

    lines.append("## last 15 events")
    lines.append("")
    lines.append("| seq | event | utc |")
    lines.append("|---|---|---|")
    for ts, seq, name in sorted(last_events)[-15:]:
        lines.append(f"| {seq} | {name} | {ts.isoformat(timespec='seconds')} |")
    lines.append("")

    if args.stdout_log:
        try:
            with open(args.stdout_log, "r", encoding="utf-8", errors="replace") as handle:
                tail = handle.read().splitlines()[-40:]
        except OSError:
            tail = []
        if tail:
            lines.append("## pier stdout tail")
            lines.append("")
            lines.append("```")
            lines.extend(tail)
            lines.append("```")
            lines.append("")

    payload = "\n".join(lines) + "\n"
    with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
