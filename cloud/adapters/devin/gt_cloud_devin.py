#!/usr/bin/env python3
"""Watch Devin sessions and report them to the GT cloud UI.

Devin is a hosted agent: there is no hook to install and no transcript file to
tail. What it exposes is the v3 sessions API — status, status_detail, ACU
spend, pull requests and child sessions — so this adapter polls it and maps
what it learns onto the same contract the Claude Code and Codex adapters speak.

    GT_CLOUD_ORIGIN=https://your-server GT_CLOUD_SESSION=<id> \
    GT_CLOUD_TOKEN=<user-jwt> \
    DEVIN_API_KEY=... DEVIN_ORG_ID=org-... \
    python -m cloud.adapters.devin.gt_cloud_devin devin-abc123

``--all`` follows every live session in the org instead of named ones.

What maps and what does not, honestly:

- ``status``/``status_detail`` -> ``status`` events (working / idle / done /
  error) with an activity line; terminal states call ``finish``.
- ``child_session_ids`` -> real nested agents: each child registers under its
  parent's ingest token (no extra credential) and is polled the same way.
- ``pull_requests`` -> an ``assistant`` event naming the PR when it appears.
- ``acus_consumed`` -> surfaced in the status ``note`` as "ACU n.n" — never
  passed off as tokens, because ACUs are compute units, not tokens.
- There are no tool calls and no file paths: a Devin card cannot land on a
  building, so it works from its dock and its activity line does the talking.

Field names read from the v3 OpenAPI at docs.devin.ai (SessionResponse).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

if __package__:
    from ..gt_cloud_bridge import (
        Bridge,
        BridgeConfig,
        _get_json,
        debug,
        truncate,
    )
else:  # running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gt_cloud_bridge import (  # type: ignore[no-redef]
        Bridge,
        BridgeConfig,
        _get_json,
        debug,
        truncate,
    )

DEFAULT_API_BASE = "https://api.devin.ai/v3"
DEFAULT_POLL_SECONDS = 4.0
MAX_DEPTH = 4
#: consecutive API failures before the card says the API is unreachable
_FAILURE_NOTE_AFTER = 3
#: ACU deltas smaller than this are not worth a status frame
_ACU_EPSILON = 0.05

TERMINAL_STATUSES = {"exit", "error", "suspended"}

#: (status, status_detail) -> (contract state, activity line)
_STATE_MAP: dict[tuple[str, str | None], tuple[str, str]] = {
    ("new", None): ("working", "Starting"),
    ("claimed", None): ("working", "Starting"),
    ("resuming", None): ("working", "Resuming"),
    ("running", "working"): ("working", "Working"),
    ("running", "waiting_for_user"): ("idle", "Waiting for you"),
    ("running", "waiting_for_approval"): ("idle", "Waiting for approval"),
    ("running", "finished"): ("done", "Finished"),
    ("error", None): ("error", "Error"),
}


def _map_state(status: str, detail: str | None) -> tuple[str, str]:
    mapped = _STATE_MAP.get((status, detail)) or _STATE_MAP.get((status, None))
    if mapped is not None:
        return mapped
    # A suspended session's detail names the reason — keep it on the card.
    if status == "suspended":
        reason = (detail or "suspended").replace("_", " ")
        return "idle", f"Suspended: {reason}"
    if status in TERMINAL_STATUSES:
        return ("error" if status == "error" else "done"), status.capitalize()
    return "working", status.replace("_", " ").capitalize()


def _finish_status(status: str) -> str:
    return "error" if status == "error" else "done"


class DevinClient:
    """Thin v3 client. Every method returns parsed JSON or None; never raises."""

    def __init__(
        self,
        api_key: str,
        org_id: str,
        *,
        base: str = DEFAULT_API_BASE,
        timeout: float = 15.0,
    ) -> None:
        self.base = base.rstrip("/")
        self.org = org_id
        self.timeout = timeout
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def session(self, devin_id: str) -> dict[str, Any] | None:
        response = _get_json(
            f"{self.base}/organizations/{self.org}/sessions/{devin_id}",
            self.headers,
            self.timeout,
        )
        if not response.ok:
            debug(f"GET session {devin_id} -> {response.status}")
            return None
        data = response.json()
        return data if isinstance(data, dict) else None

    def sessions(self, first: int = 50) -> list[dict[str, Any]]:
        """The org's most recent sessions (one page is enough for a fleet)."""
        response = _get_json(
            f"{self.base}/organizations/{self.org}/sessions?first={first}",
            self.headers,
            self.timeout,
        )
        if not response.ok:
            debug(f"list sessions -> {response.status}")
            return []
        data = response.json()
        items = data.get("items") if isinstance(data, dict) else None
        return [i for i in items or [] if isinstance(i, dict)]


def _session_id_of(row: dict[str, Any]) -> str:
    return str(row.get("session_id") or row.get("devin_id") or "")


class DevinWatcher:
    """Polls watched Devin sessions and every child session they spawn."""

    def __init__(
        self,
        client: DevinClient,
        config: BridgeConfig | None = None,
        follow_children: bool = True,
    ) -> None:
        self.client = client
        self.config = config or BridgeConfig.from_env()
        self.follow_children = follow_children
        self.bridges: dict[str, Bridge] = {}
        self.depth: dict[str, int] = {}
        self.seen_prs: dict[str, set[str]] = {}
        self.last: dict[str, tuple[str, str | None, float]] = {}
        self.seen_output: set[str] = set()
        self.failures = 0

    def attach(
        self, devin_id: str, row: dict[str, Any], parent_agent: str | None, depth: int
    ) -> Bridge | None:
        if devin_id in self.bridges or devin_id in self.depth:
            return self.bridges.get(devin_id)
        title = str(row.get("title") or "") or devin_id
        bridge = Bridge(
            agent_kind="devin",
            label=f"devin · {truncate(title, 120)}",
            task=title,
            parent_agent_id=parent_agent,
            state_key=f"devin:{devin_id}",
            config=self.config,
        )
        self.depth[devin_id] = depth
        if not bridge.start():
            return None
        self.bridges[devin_id] = bridge
        self.seen_prs[devin_id] = set()
        return bridge

    def _poll_agent(self, devin_id: str) -> None:
        bridge = self.bridges.get(devin_id)
        if bridge is None or not bridge.enabled:
            return
        row = self.client.session(devin_id)
        if row is None:
            self.failures += 1
            if self.failures == _FAILURE_NOTE_AFTER:
                bridge.status("idle", note="Devin API unreachable; still polling")
            return
        self.failures = 0
        status = str(row.get("status") or "")
        raw_detail = row.get("status_detail")
        detail = str(raw_detail) if raw_detail else None
        try:
            acus = float(row.get("acus_consumed") or 0.0)
        except (TypeError, ValueError):
            acus = 0.0

        key = (status, detail, acus)
        if key != self.last.get(devin_id):
            state, activity = _map_state(status, detail)
            note = f"ACU {acus:.2f}" if acus > _ACU_EPSILON else None
            bridge.status(state, note=note, activity=activity)
            self.last[devin_id] = key

        for pr in row.get("pull_requests") or []:
            url = ""
            if isinstance(pr, dict):
                url = str(pr.get("pr_url") or pr.get("url") or "")
            if url and url not in self.seen_prs[devin_id]:
                self.seen_prs[devin_id].add(url)
                bridge.assistant(f"Opened pull request: {url}")

        output = row.get("structured_output")
        if isinstance(output, dict) and output and devin_id not in self.seen_output:
            self.seen_output.add(devin_id)
            bridge.assistant(truncate(json.dumps(output, indent=2, default=str), 4000))

        if self.follow_children and self.depth.get(devin_id, 0) < MAX_DEPTH:
            for child in row.get("child_session_ids") or []:
                child_id = str(child)
                if child_id not in self.depth:
                    self.attach(
                        child_id,
                        {},
                        parent_agent=str(bridge.agent_id),
                        depth=self.depth.get(devin_id, 0) + 1,
                    )

        if status in TERMINAL_STATUSES:
            bridge.finish(_finish_status(status), f"devin {status}")
            self.bridges.pop(devin_id, None)

    def poll_watched(self) -> None:
        for devin_id in list(self.bridges):
            self._poll_agent(devin_id)

    def discover_org(self) -> list[str]:
        """Live sessions in the org, for ``--all`` mode."""
        live = [
            _session_id_of(row)
            for row in self.client.sessions()
            if str(row.get("status") or "") not in TERMINAL_STATUSES
        ]
        return [s for s in live if s]

    def run(
        self,
        roots: list[str] | None = None,
        *,
        poll: float = DEFAULT_POLL_SECONDS,
        watch_org: bool = False,
    ) -> None:
        try:
            while True:
                if watch_org:
                    wanted = self.discover_org()
                else:
                    wanted = list(roots or [])
                for devin_id in wanted:
                    if devin_id not in self.depth:
                        self.attach(devin_id, {}, parent_agent=None, depth=0)
                self.poll_watched()
                # An explicit watch ends when every watched session ended;
                # --all runs until interrupted (the fleet never "finishes").
                if not watch_org and not self.bridges and self.depth:
                    break
                if not watch_org and not self.depth:
                    # Nothing ever attached — no point polling an empty set.
                    break
                time.sleep(poll)
        except KeyboardInterrupt:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "sessions",
        nargs="*",
        help="devin session ids to watch (devin-…); omit with --all",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="follow every live session in the org",
    )
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument(
        "--api-base",
        default=os.environ.get("DEVIN_API_BASE") or DEFAULT_API_BASE,
    )
    parser.add_argument("--no-children", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    api_key = os.environ.get("DEVIN_API_KEY") or ""
    org_id = os.environ.get("DEVIN_ORG_ID") or ""
    if not api_key or not org_id:
        print("set DEVIN_API_KEY and DEVIN_ORG_ID", file=sys.stderr)
        return 2
    if not args.all and not args.sessions:
        print("name a devin-… session to watch, or pass --all", file=sys.stderr)
        return 2
    client = DevinClient(api_key, org_id, base=args.api_base)
    watcher = DevinWatcher(client, follow_children=not args.no_children)
    watcher.run(
        None if args.all else list(args.sessions),
        poll=args.poll,
        watch_org=args.all,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
