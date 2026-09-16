"""Live LSP-tier health watcher — same verdict inputs as the reporter, mid-run.

The capability reporter computes lsp_promotion from the event journal; that
read is valid at ANY point in the run because the journal is append-only.
This tool is that read, streaming: feed it a journal file, a ``GT_EVENT|``
tee stream (``GT_JOURNAL_TEE=1`` mirrors every committed journal row to
stderr), or a whole downloaded artifact tree, and it reconstructs per-task
tier state plus the degradation predictors the paid postmortems exposed:

- TIER_PARTIAL:      the adopted graph was minted by a salvage that skipped
                     rows (provably partial coverage, live - not at seal)
- RACE_LOSS_STREAK:  consecutive obsolete terminals; the churn defer is armed
                     and the next leg is racing a window, not running
- NO_LEG_ON_FINAL:   the last adopted graph revision was never offered a leg
- SCHEDULED_NO_TERMINAL: a leg is in flight right now
- AMEND_FAILURES:    producer amend deaths observed this run
- CAPABILITY_CRASH:  a *_unavailable event carrying an exception class —
                     the capability crashed, not abstained (gate-one read
                     nine TypeError localizations as graceful)
- SEAL_*:            the seal-time convergence outcome once close runs

Usage:
    lsp_watch.py --artifacts D:/tmp/gate1-healed-34904339448
    lsp_watch.py --journal path/to/events.jsonl [--follow]
    lsp_watch.py --run 34907273607 [--interval 60]     # polls gh run --log
    tail -f events.jsonl | lsp_watch.py --stdin

Stdlib only. Exit code is 0; this is an observer, never a gate.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

TEE_PREFIX = "GT_EVENT|"

# A reason that opens with an exception class name (``TypeError:``,
# ``sqlite3.OperationalError:``) is a crash the typed layer caught - not a
# designed unavailability like ``graph_snapshot_not_current``.
_EXCEPTION_REASON = re.compile(r"^[\w.]*Error\b")

_TERMINAL_DISPOSITIONS = (
    "published", "obsolete", "obsolete_after_certification",
    "not_publishable", "no_edge_mutations", "identity_mismatch",
    "certifier_exception", "certification_failed",
)


def _rows_from_line(line: str) -> dict | None:
    line = line.strip()
    if line.startswith(TEE_PREFIX):
        line = line[len(TEE_PREFIX):]
    if not line.startswith("{"):
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row.get("event"), str) else None


class TaskHealth:
    """One task's LSP/graph state, accumulated from journal rows."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.events = 0
        self.legs_scheduled: list[str] = []
        self.terminals: list[dict] = []
        self.salvages: list[dict] = []
        self.publications: list[dict] = []
        self.churn_backoffs = 0
        self.amend_failures = 0
        self.capability_crashes = 0
        self.seal_rows: list[dict] = []
        self.sealed = False
        self.last_ts = ""

    def feed(self, row: dict) -> None:
        self.events += 1
        self.last_ts = str(row.get("timestamp_utc") or "")
        event = row["event"]
        if event == "lsp_promotion_scheduled":
            self.legs_scheduled.append(str(row.get("graph_revision") or ""))
        elif event == "lsp_promotion_terminal":
            self.terminals.append(row)
        elif event == "lsp_salvage":
            self.salvages.append(row)
        elif event == "graph_publication":
            self.publications.append(row)
        elif event == "lsp_churn_backoff":
            self.churn_backoffs += 1
        elif event == "lsp_seal_convergence":
            self.seal_rows.append(row)
        elif event == "session_closed":
            self.sealed = True
        elif event == "graph_refresh_failed" or (
            event == "index_unavailable"
        ):
            pass
        if "amend_failed" in str(row.get("reason") or row.get("error") or ""):
            self.amend_failures += 1
        # A *_unavailable event whose reason is an exception class name is a
        # crash the typed wrapper caught, not a designed abstention. Gate-one
        # sealed with nine semantic_localization_unavailable TypeErrors that
        # read as graceful; they are a capability failure the strict gate
        # must count.
        if event.endswith("_unavailable") and _EXCEPTION_REASON.match(
            str(row.get("reason") or row.get("error") or "")
        ):
            self.capability_crashes += 1

    # -- derived state -----------------------------------------------------

    @property
    def adopted(self) -> str:
        return (
            str(self.publications[-1].get("graph_sha256") or "")
            if self.publications else ""
        )

    def _dispositions(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.terminals:
            key = str(row.get("disposition") or "?")
            out[key] = out.get(key, 0) + 1
        return out

    def flags(self) -> list[str]:
        flags: list[str] = []
        adopted = self.adopted
        salvage_for_adopted = next(
            (
                row for row in reversed(self.salvages)
                if str(row.get("outcome") or "") == "published"
                and str(row.get("graph_revision") or "") == adopted
            ),
            None,
        )
        if salvage_for_adopted is not None:
            skipped = int(salvage_for_adopted.get("skipped_stale") or 0) + int(
                salvage_for_adopted.get("skipped_diverged") or 0
            )
            if skipped:
                flags.append(f"TIER_PARTIAL({skipped}_skipped)")
        recent = [str(t.get("disposition") or "") for t in self.terminals[-2:]]
        if len(recent) >= 2 and all(
            d in {"obsolete", "obsolete_after_certification"} for d in recent
        ):
            flags.append(f"RACE_LOSS_STREAK(x{self.churn_backoffs} backoffs)")
        terminal_bases = {
            str(t.get("input_graph_revision") or "") for t in self.terminals
        } | {
            str(s.get("graph_revision") or "")
            for s in self.salvages
            if str(s.get("outcome") or "") == "published"
        }
        if adopted and adopted not in terminal_bases:
            flags.append("NO_LEG_ON_FINAL")
        if len(self.legs_scheduled) > len(self.terminals):
            flags.append("SCHEDULED_NO_TERMINAL")
        if self.amend_failures:
            flags.append(f"AMEND_FAILURES({self.amend_failures})")
        if self.capability_crashes:
            flags.append(f"CAPABILITY_CRASH({self.capability_crashes})")
        if self.seal_rows:
            flags.append(
                "SEAL_" + str(self.seal_rows[-1].get("outcome") or "?").upper()
            )
        if self.sealed:
            flags.append("SEALED")
        return flags

    def fail_flags(self) -> list[str]:
        """Hard-fail flags for a sealed task journal.

        Used by ``--strict`` fleet gates: a cohort scan exits nonzero when
        any sealed task froze a partial tier, lost a scheduled leg, broke
        the producer pipeline, or sealed on a graph that demanded a leg but
        never completed one (post seal-convergence, that means the bounded
        seal window lapsed or the machinery never ran).
        """
        out: list[str] = []
        for flag in self.flags():
            if flag.startswith((
                "TIER_PARTIAL", "SCHEDULED_NO_TERMINAL", "AMEND_FAILURES",
                "CAPABILITY_CRASH",
            )):
                out.append(flag)
        seal_outcome = str(self.seal_rows[-1].get("outcome") or "") if self.seal_rows else ""
        if (
            "NO_LEG_ON_FINAL" in self.flags()
            and self.legs_scheduled
            and seal_outcome not in {"terminated"}
        ):
            out.append(f"NO_LEG_ON_FINAL(seal={seal_outcome or 'absent'})")
        return out

    def render(self) -> str:
        d = self._dispositions()
        disp = ",".join(f"{k}={v}" for k, v in sorted(d.items())) or "-"
        sal = [
            f"{s.get('applied', 0)}a/{int(s.get('skipped_stale') or 0) + int(s.get('skipped_diverged') or 0)}s:{s.get('outcome', '?')}"
            for s in self.salvages[-3:]
        ]
        flag_list = self.flags()
        return (
            f"{self.name}\n"
            f"  events={self.events} pubs={len(self.publications)} "
            f"legs={len(self.legs_scheduled)} terminals=[{disp}] "
            f"salvage=[{'; '.join(sal) or '-'}] backoff={self.churn_backoffs}\n"
            f"  adopted={self.adopted[:12] or '-'} "
            f"flags: {', '.join(flag_list) if flag_list else 'clean'} "
            f"({self.last_ts[:19]})"
        )


def _iter_journal(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            row = _rows_from_line(line)
            if row is not None:
                yield row


def _scan_artifacts(root: Path) -> list[TaskHealth]:
    healths: list[TaskHealth] = []
    seen_heads: set[str] = set()
    for journal in sorted(root.rglob("events.jsonl")):
        rows = list(_iter_journal(journal))
        # Artifact trees carry the same journal at agent/ and
        # gt-state/<task>/ paths - dedupe on the chain head so a 20-task
        # cohort prints 20 rows, not 40.
        head = str(rows[-1].get("event_hash") or "") if rows else ""
        if head and head in seen_heads:
            continue
        seen_heads.add(head)
        health = TaskHealth(str(journal.parent.name or journal.parent))
        for row in rows:
            health.feed(row)
        healths.append(health)
    return healths


def _run_logs(run_id: str) -> list[str]:
    try:
        out = subprocess.run(
            ["gh", "run", "view", run_id, "--log"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [
        line for line in (out.stdout or "").splitlines()
        if TEE_PREFIX in line
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--journal", type=Path)
    source.add_argument("--artifacts", type=Path)
    source.add_argument("--run")
    source.add_argument("--stdin", action="store_true")
    parser.add_argument("--follow", action="store_true",
                        help="tail the journal file (local/container use)")
    parser.add_argument("--interval", type=float, default=60.0,
                        help="poll seconds for --run/--follow")
    parser.add_argument("--strict", action="store_true",
                        help="fleet gate: exit 1 if any sealed task carries a "
                             "hard-fail flag (TIER_PARTIAL / SCHEDULED_NO_TERMINAL / "
                             "AMEND_FAILURES / unconverged NO_LEG_ON_FINAL)")
    args = parser.parse_args()

    if args.artifacts:
        healths = _scan_artifacts(args.artifacts)
        if not healths:
            print(f"no events.jsonl under {args.artifacts}", file=sys.stderr)
            return 1
        failures: list[tuple[str, list[str]]] = []
        for health in healths:
            print(health.render())
            hard = health.fail_flags()
            if hard:
                failures.append((health.name, hard))
        if args.strict:
            if failures:
                print("\nFLEET GATE: FAIL")
                for name, hard in failures:
                    print(f"  {name}: {', '.join(hard)}")
                return 1
            print("\nFLEET GATE: PASS")
        return 0

    if args.stdin:
        health = TaskHealth("stdin")
        for line in sys.stdin:
            row = _rows_from_line(line)
            if row is not None:
                health.feed(row)
        print(health.render())
        return 0

    if args.run:
        health = TaskHealth(f"run-{args.run}")
        seen = 0
        while True:
            lines = _run_logs(args.run)
            for line in lines[seen:]:
                row = _rows_from_line(line)
                if row is not None:
                    health.feed(row)
            seen = len(lines)
            print(health.render(), flush=True)
            status = subprocess.run(
                ["gh", "run", "view", args.run, "--json", "status",
                 "-q", ".status"],
                capture_output=True, text=True, timeout=30,
            )
            if (status.stdout or "").strip() == "completed":
                break
            time.sleep(args.interval)
        return 0

    # --journal
    health = TaskHealth(str(args.journal.parent.name))
    if not args.follow:
        for row in _iter_journal(args.journal):
            health.feed(row)
        print(health.render())
        return 0
    offset = 0
    while True:
        size = args.journal.stat().st_size
        if size >= offset:
            with args.journal.open(encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                for line in fh:
                    row = _rows_from_line(line)
                    if row is not None:
                        health.feed(row)
                offset = fh.tell()
            print(health.render(), flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
