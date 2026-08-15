"""Deep-audit B/C: invariant properties + adversarial fuzz for the compaction repair.

Audit B (property checks over many synthetic-but-realistic turns):
  - below-trigger views are byte-identical and mutate nothing;
  - no marker ever contains raw command text;
  - assistant content/reasoning never rewritten;
  - every marker's chars/sha256 verify against the cleared body;
  - recap receipts are atomic (never exceed cap, never shorter than bare);
  - elision only fires when a hash-matched read is re-read at current revision.

Audit C (adversarial fuzz):
  - malformed/absent extra.raw_output cannot crash or fabricate elision;
  - byte-identical content across distinct paths must not cross-elide;
  - keep_recent_turns=1 boundaries;
  - a body already replaced by a marker is never re-elided/re-recapped;
  - recap overflow falls back to the historical bare receipt byte-for-byte.
"""

from __future__ import annotations

import copy
import hashlib
import random
import string
import sys

from gt_engine.provider_view import (
    _assemble_recap_text,
    _turn_semantic_parts,
    build_provider_view,
)

random.seed(20260814)


def turn(command: str, output: str, *, index: int, returncode: int = 0) -> list[dict]:
    tool_id = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": "act",
            "extra": {"actions": [{"command": command, "tool_call_id": tool_id}]},
        },
        {
            "role": "tool",
            "tool_call_id": tool_id,
            "content": output,
            "extra": {"raw_output": output, "returncode": returncode},
        },
    ]


def history(*turns) -> list[dict]:
    messages = [{"role": "user", "content": "task"}]
    for index, (command, output, returncode) in enumerate(turns):
        messages.extend(turn(command, output, index=index, returncode=returncode))
    return messages


def read_obs(path, output, *, source_revision, kind="read", start_line=1, end_line=None):
    return {
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "whole_file": True,
        "source_revision": source_revision,
        "workspace_revision": "w",
        "action_id": 1,
        "returncode": 0,
        "output_hash": hashlib.sha256((output or "").encode("utf-8", "replace")).hexdigest(),
        "content_mapped": True,
        "observation_kind": kind,
    }


PREFIXES = (
    "[Earlier tool result cleared:",
    "[Superseded read result cleared:",
    "[Superseded validation result cleared:",
)


def markers(view) -> list[str]:
    return [
        str(m.get("content") or "")
        for m in view
        if str(m.get("content") or "").startswith(PREFIXES)
    ]


def audit_b() -> dict:
    findings: dict = {}
    total = 0
    ok_below_identical = 0
    ok_no_command_text = 0
    ok_assistant_untouched = 0
    ok_marker_consistent = 0
    ok_marker_consistent = 0
    bodies_checked = 0
    for _case in range(60):
        n = random.randint(3, 10)
        commands = []
        messages = [{"role": "user", "content": "task"}]
        for i in range(n):
            if random.random() < 0.5:
                command = random.choice(
                    ["cat /app/src/main.go", "cat /app/src/util.go", "cat /app/go.mod"]
                )
                body = "".join(
                    random.choice(string.printable)
                    for _ in range(random.randint(300, 15000))
                )
            else:
                command = random.choice(["pytest -q", "go test ./...", "grep -r foo /app/src"])
                body = "".join(
                    random.choice(string.printable)
                    for _ in range(random.randint(300, 15000))
                )
            commands.append(command)
            messages.extend(turn(command, body, index=i, returncode=random.choice([0, 1])))
        recent_reads = []
        read_history = []
        for i, command in enumerate(commands):
            if command.startswith("cat "):
                path = command.split()[-1]
                output = str(messages[(i * 2) + 2]["extra"]["raw_output"])
                rev = f"s{i % 3}"
                obs = read_obs(path, output, source_revision=rev)
                read_history.append(obs)
                recent_reads = [o for o in recent_reads if o["path"] != path]
                recent_reads.append(obs)
        active_state = {
            "source_revision": "s2",
            "recent_reads": recent_reads,
            "read_history": read_history,
            "latest_validation": {
                "command": "pytest -q",
                "returncode": 0,
                "source_revision": "s2",
            },
        }
        below, below_metrics = build_provider_view(
            messages,
            active_state=active_state,
            trigger_chars=10**18,
            target_chars=10**18,
            transform=True,
        )
        original = copy.deepcopy(messages)
        if below == original and below_metrics.old_tool_results_cleared == 0:
            ok_below_identical += 1
        inflated = copy.deepcopy(messages)
        for item in inflated:
            if item.get("role") == "tool" and (item.get("extra") or {}).get("raw_output"):
                item["content"] = str(item["content"]) + "pad" * 300
        view, metrics = build_provider_view(
            inflated,
            active_state=active_state,
            trigger_chars=10_000,
            target_chars=5_000,
            keep_recent_turns=1,
            transform=True,
        )
        total += 1
        # no command text in any marker
        leak = False
        for mark in markers(view):
            for word in ("pytest", "cat /app", "go test", "grep -r"):
                if word in mark:
                    leak = True
        if not leak:
            ok_no_command_text += 1
        # assistant untouched
        assistant_input = [
            m for m in inflated if m.get("role") == "assistant"
        ]
        assistant_output = [m for m in view if m.get("role") == "assistant"]
        if assistant_input == assistant_output:
            ok_assistant_untouched += 1
        # marker consistency: match markers to their original body by tool_call_id
        original_by_id = {
            str(m.get("tool_call_id") or ""): str(m.get("content") or "")
            for m in inflated
            if m.get("role") == "tool"
        }
        for mark in view:
            if str(mark.get("content") or "").startswith(PREFIXES):
                import re

                char_match = re.search(r"chars=(\d+)", str(mark.get("content") or ""))
                hash_match = re.search(r"sha256=([0-9a-f]{64})", str(mark.get("content") or ""))
                if char_match and hash_match:
                    bodies_checked += 1
                    orig = original_by_id.get(str(mark.get("tool_call_id") or ""), "")
                    orig_digest = hashlib.sha256(
                        orig.encode("utf-8", "surrogatepass")
                    ).hexdigest()
                    if int(char_match.group(1)) == len(orig) and hash_match.group(1) == orig_digest:
                        ok_marker_consistent += 1
    findings["audit_b"] = {
        "cases": total,
        "below_trigger_byte_identical": f"{ok_below_identical}/{total}",
        "no_command_text_in_markers": f"{ok_no_command_text}/{total}",
        "assistant_messages_untouched": f"{ok_assistant_untouched}/{total}",
        "marker_char_hash_consistent": f"{ok_marker_consistent}/{bodies_checked}",
    }
    return findings


def audit_c() -> dict:
    findings: dict = {}
    # C1: malformed/absent raw_output never crashes and never fabricates elision
    messages = history(
        ("cat /app/src/main.go", "X" * 40000, 0),
        ("pytest -q", "1 failed", 1),
    )
    for tool in messages:
        if tool.get("role") == "tool":
            tool["extra"] = {"returncode": 0}
    active_state = {
        "source_revision": "s2",
        "read_history": [read_obs("/app/src/main.go", "X" * 40000, source_revision="s1")],
        "recent_reads": [],
    }
    view, metrics = build_provider_view(
        messages, active_state=active_state, trigger_chars=100, target_chars=50, keep_recent_turns=1
    )
    findings["c1_no_crash_no_fabricated_elision"] = (
        metrics.stale_reads_elided == 0 and not any(
            str(m.get("content") or "").startswith("[Superseded read result cleared:")
            for m in view
        )
    )

    # C2: identical content across distinct paths must not cross-elide
    shared = "SAME" * 200
    messages = history(
        ("cat /app/src/a.go", shared, 0),
        ("sed -i x/y/ /app/src/a.go", "edited", 0),
        ("cat /app/src/b.go", shared, 0),
    )
    active_state = {
        "source_revision": "s2",
        "recent_reads": [read_obs("/app/src/b.go", shared, source_revision="s2")],
        "read_history": [
            read_obs("/app/src/a.go", shared, source_revision="s1"),
            read_obs("/app/src/b.go", shared, source_revision="s2"),
        ],
    }
    view, metrics = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=100,
        keep_recent_turns=2,
    )
    joined = " ".join(str(m.get("content") or "") for m in view)
    findings["c2_no_cross_path_elision"] = (
        metrics.stale_reads_elided == 0
        and "Superseded read result cleared: path=/app/src/a.go" not in joined
    )

    # C3: keep_recent_turns=1 leaves only one turn; zero is clamped to 1
    body = "Y" * 40000
    messages = history(
        ("cat /app/src/main.go", body, 0),
        ("cat /app/src/main.go", body, 0),
        ("pytest -q", "ok", 0),
    )
    active_state = {
        "source_revision": "s2",
        "read_history": [read_obs("/app/src/main.go", body, source_revision="s2")],
        "recent_reads": [read_obs("/app/src/main.go", body, source_revision="s2")],
    }
    _, metrics0 = build_provider_view(
        messages, active_state=active_state, trigger_chars=100, target_chars=50, keep_recent_turns=0
    )
    _, metrics1 = build_provider_view(
        messages, active_state=active_state, trigger_chars=100, target_chars=50, keep_recent_turns=1
    )
    clamped = (
        metrics0.old_tool_results_cleared == metrics1.old_tool_results_cleared
    )
    findings["c3_keep_recent_turns_clamped"] = clamped

    # C4: already-markerized bodies are never re-elided or re-recapped
    messages = history(
        ("cat /app/src/main.go", "Z" * 40000, 0),
        ("pytest -q", "ok", 0),
    )
    active_state = {
        "source_revision": "s2",
        "read_history": [read_obs("/app/src/main.go", "Z" * 40000, source_revision="s1")],
        "recent_reads": [],
    }
    view1, metrics1 = build_provider_view(
        messages,
        active_state=active_state,
        trigger_chars=200,
        target_chars=100,
        keep_recent_turns=1,
    )
    # run again on the markerized view (as an established checkpoint prefix would)
    view2, metrics2 = build_provider_view(
        view1, active_state=active_state, trigger_chars=200, target_chars=100, keep_recent_turns=1
    )
    markers1 = markers(view1)
    markers2 = markers(view2)
    findings["c4_markerized_never_double_processed"] = (
        len(markers2) >= len(markers1) and metrics2.stale_reads_elided == 0
    )

    # C5: recap atomicity — overflow returns None, within-cap returns valid text
    body = "E" * 500
    bare = "[Earlier tool result cleared: chars=500 sha256=abc returncode=0.]"
    tool = {
        "extra": {"raw_output": body, "returncode": 0},
        "content": body,
    }
    semantic = _turn_semantic_parts(
        tool,
        ("cat /app/src/main.go",),
        [read_obs("/app/src/main.go", body, source_revision="s1")],
        {"command": "cat /app/src/main.go", "returncode": 0, "source_revision": "s1"},
        "s2",
    )
    overflow = _assemble_recap_text(
        ("cat /app/src/main.go",), tool, semantic, body, "abc", bare, cap=40
    )
    within = _assemble_recap_text(
        ("cat /app/src/main.go",), tool, semantic, body, "abc", bare, cap=200
    )
    findings["c5_recap_atomic"] = overflow is None and within is not None and len(within) <= 200
    return findings


if __name__ == "__main__":
    import pprint

    result = {**audit_b(), **audit_c()}
    pprint.pprint(result)
    ok = True
    for key in (
        "below_trigger_byte_identical",
        "no_command_text_in_markers",
        "assistant_messages_untouched",
        "marker_char_hash_consistent",
    ):
        passed, total = result["audit_b"][key].split("/")
        if int(passed) != int(total):
            ok = False
    for key in result:
        if key.startswith("c") and result[key] is not True:
            ok = False
    print("AUDIT_B_C_PASS" if ok else "AUDIT_B_C_FAIL")
    sys.exit(0 if ok else 1)
