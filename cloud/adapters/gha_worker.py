"""The runner-side half of a ``/gha`` turn — this executes in GitHub Actions.

Environment (all set by the workflow, which got them as dispatch inputs):

  ``GT_INGEST_URL``    where this agent's events post.
  ``GT_INGEST_TOKEN``  the scoped credential for that endpoint.
  ``GT_TASK``          the task text.
  ``GT_TARGET_REPO``   ``https://github.com/owner/name`` — what to work on.
  ``GT_TARGET_REF``    branch; default ``main``.
  ``GT_MODEL``         the model; default from the product's allowlist.
  ``OPENROUTER_API_KEY`` / whatever the model needs — repo secret, never
                       an input.

It clones the target, drives mini-swe-agent one step at a time, and
streams each step's command + result through the ingest contract — the
same frames any external agent sends, so the city shows it working.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

URL = os.environ.get("GT_INGEST_URL", "").strip()
TOKEN = os.environ.get("GT_INGEST_TOKEN", "").strip()
TASK = os.environ.get("GT_TASK", "").strip()
TARGET = os.environ.get("GT_TARGET_REPO", "").strip()
REF = os.environ.get("GT_TARGET_REF", "main").strip() or "main"
MODEL = os.environ.get("GT_MODEL", "openrouter/meta/muse-spark-1.2-contributor").strip()

WORKDIR = os.path.abspath("target")
STEP_LIMIT = int(os.environ.get("GT_STEP_LIMIT", "60"))

_pending: list[dict] = []
_last_flush = 0.0


def _post(events: list[dict]) -> None:
    if not events:
        return
    req = urllib.request.Request(
        URL,
        data=json.dumps({"events": events}).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:  # noqa: BLE001 — the run matters more than a frame
        print(f"ingest dropped a batch: {exc}", file=sys.stderr)


def emit(event: dict) -> None:
    """Batch events and flush at most every ~2s — the ingest contract's
    rate limit is per minute, not per second."""
    global _last_flush
    event.setdefault("ts", time.time())
    _pending.append(event)
    if len(_pending) >= 8 or time.time() - _last_flush > 2.0:
        _post(list(_pending))
        _pending.clear()
        _last_flush = time.time()


def flush() -> None:
    _post(list(_pending))
    _pending.clear()


def clone() -> bool:
    emit({"type": "status", "state": "working", "activity": f"cloning {TARGET}"})
    emit({"type": "tool_call", "name": "bash",
          "command": f"git clone --depth 1 --branch {REF} {TARGET} target"})
    r = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", REF, TARGET, "target"],
        capture_output=True, text=True, timeout=600,
    )
    emit({"type": "tool_result", "name": "bash", "ok": r.returncode == 0,
          "output": (r.stderr or r.stdout)[-2000:]})
    return r.returncode == 0


def run_agent() -> tuple[bool, str]:
    """mini-swe-agent's DefaultAgent, driven step by step so every action
    reaches the parent's stream as it happens."""
    from minisweagent.agents.default import AgentConfig, DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_model import LitellmModel

    env = LocalEnvironment(cwd=WORKDIR, timeout=120)
    model = LitellmModel(model_name=MODEL, model_kwargs={"temperature": 0.0})
    agent = DefaultAgent(
        model, env,
        config_class=AgentConfig,
        step_limit=STEP_LIMIT,
        cost_limit=4.0,
        output_path="agent_output.json",
    )
    agent.extra_template_vars["task"] = TASK
    agent.add_messages(
        model.format_message(
            role="system",
            content=agent._render_template(agent.config.system_template),
        ),
        model.format_message(
            role="user",
            content=agent._render_template(agent.config.instance_template),
        ),
    )
    emit({"type": "status", "state": "working", "activity": "agent running"})
    steps = 0
    last_reported = 0
    while steps < STEP_LIMIT:
        try:
            before = len(agent.messages)
            agent.step()
            steps += 1
        except Exception as exc:  # noqa: BLE001
            return False, f"agent raised: {exc}"
        # Report what the step did: the action's command becomes the
        # tool_call, its observation the tool_result.
        for msg in agent.messages[before:]:
            role = msg.get("role")
            if role == "assistant":
                actions = (msg.get("extra") or {}).get("actions") or []
                for a in actions:
                    emit({"type": "tool_call", "name": "bash",
                          "command": str(a.get("action", ""))[:800],
                          "activity": "running a command"})
            elif role in ("user", "tool", "observation"):
                emit({"type": "tool_result", "name": "bash", "ok": True,
                      "output": str(msg.get("content", ""))[-1500:]})
            elif role == "exit":
                break
        if time.time() - last_reported > 8:
            emit({"type": "status", "state": "working",
                  "activity": f"step {steps}"})
            last_reported = time.time()
        if agent.messages and agent.messages[-1].get("role") == "exit":
            break
    extra = agent.messages[-1].get("extra", {}) if agent.messages else {}
    submission = str(extra.get("submission") or "")
    return True, submission or "agent finished without a submission"


def finish(status: str, summary: str) -> None:
    emit({"type": "status", "state": "done" if status == "done" else "error",
          "note": summary[:400]})
    flush()
    req = urllib.request.Request(
        URL.replace("/events", "/finish"),
        data=json.dumps({"status": status, "summary": summary[:2000]}).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:  # noqa: BLE001
        print(f"finish not delivered: {exc}", file=sys.stderr)


def main() -> int:
    if not (URL and TOKEN and TASK and TARGET):
        print("missing GT_INGEST_URL / GT_INGEST_TOKEN / GT_TASK / GT_TARGET_REPO",
              file=sys.stderr)
        return 2
    if not clone():
        finish("error", f"could not clone {TARGET}@{REF}")
        return 1
    try:
        ok, summary = run_agent()
    except Exception as exc:  # noqa: BLE001
        ok, summary = False, f"worker failed before the agent could: {exc}"
    finish("done" if ok else "error", summary)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
