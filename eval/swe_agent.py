"""SWE-bench Verified adapter (Harbor installed agent).

Harbor's hub hosts `swebench-verified@1.0` (500 tasks; also `swebench_multilingual@1.0`,
`swesmith@1.0`) as ordinary Harbor tasks: each task's environment is the official
`swebench/sweb.eval.x86_64.*` image with the repo checked out at /testbed, the
instruction is the raw GitHub issue text, and the verifier grades the CONTAINER STATE
in place — it resets the test files to the base commit, runs `git clean -fd`, applies
the official test patch, runs the suite, and scores with `swebench.harness.grading`.
There is no predictions JSONL and no patch file to submit: whatever the agent leaves
in /testbed *is* the submission.

Two consequences drive this adapter:

1. Because the verifier runs `git clean -fd` before grading, any NEW file the agent
   creates must be in the git index to survive. After the agent finishes we stage
   everything (`git add -A`) — gitignored junk (*.pyc, __pycache__) is skipped by
   git itself, and GT's per-task `.gt/` index dir is removed first so it is neither
   staged nor graded.
2. The staged diff (`git diff --cached`) is therefore the canonical "model patch"
   (it includes new-file contents, which plain `git diff` would miss); we save it to
   /logs/agent/model_patch.diff for failure-case analysis. Grading never reads it.

Usage (host needs Docker running):

    pip install harbor
    export ANTHROPIC_API_KEY=...
    # Baseline arm (stock nano, GT off):
    harbor run -d swebench-verified@1.0 \
        -a eval.swe_agent:NanoSweAgent \
        -m anthropic/claude-opus-4-8 \
        -l 5 -n 2 -o results/swebench --job-name swe-smoke -y
    # GT arm (full container plumbing — wheel, binary, gt_engine, smoke):
    #   -a eval.swe_agent:GTNanoSweAgent   (gt_root defaults ON to /testbed;
    #   needs vendor/groundtruth_mcp-*.whl plus the externally built pinned
    #   GT_INDEX_BINARY_HOST artifact —
    #   the swe_gt.yml workflow stages both, see eval/tb_agent.py:GTNanoAgent)

Results land in results/swebench/<job-name>/; per-task agent stdout in
<task>/agent/nano.txt, the staged model patch in <task>/agent/model_patch.diff,
and the verifier's swebench report in <task>/verifier/report.json.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    CliFlag,
    EnvVar,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from eval._env import UTF8_ENV, provider_env

# GT arm: one discovery path + one container binary path for BOTH benchmark
# adapters — eval/tb_agent.py is the source of truth (its GT plumbing is the
# arm proven live on TB2 run 30501483446).
from eval.tb_agent import _REMOTE_GT_BINARY
from eval.tb_agent import GTNanoAgent as _GTNanoTB

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REMOTE_DIR = "/installed-agent/nano-harness"
_WORKDIR = "/testbed"  # SWE-bench convention, baked into the task images/verifiers

# SWE-bench images are conda-based Ubuntu, but keep the tb_agent hardening: make
# sure curl exists, then let uv bring its own Python so we never depend on the
# image's python3 (the /testbed conda env is the TASK's env — do not touch it).
_ENSURE_CURL = (
    "command -v curl >/dev/null 2>&1 || { "
    "command -v apt-get >/dev/null && apt-get update && apt-get install -y curl; } || { "
    "command -v apk >/dev/null && apk add --no-cache curl bash; } || { "
    "command -v dnf >/dev/null && dnf install -y curl; } || { "
    "command -v yum >/dev/null && yum install -y curl; }"
)

def _install_nano_cmd(extra_uv_args: str = "") -> str:
    """The uv-tool install command. ``extra_uv_args`` (e.g. ``--with <wheel>``)
    lets the GT arm add packages into the SAME tool venv nano runs from;
    baseline passes nothing and gets the byte-identical historical command."""
    return (
        "set -eu; "
        "curl -LsSf https://astral.sh/uv/install.sh | sh && "
        f'"$HOME/.local/bin/uv" tool install --python 3.12 {extra_uv_args}{_REMOTE_DIR} && '
        '"$HOME/.local/bin/nano" --help >/dev/null'
    )


_INSTALL_NANO = _install_nano_cmd()

# Minimal, benchmark-agnostic task frame around the raw issue text. No task-id
# logic, no gold hints, no test names — the model gets exactly what a developer
# reading the issue would get, plus where the repo is and the no-test-edits rule.
_TASK_TEMPLATE = """\
You are working in a git repository checked out at {workdir}. The repository has \
an issue, reported below. Fix it by editing the source code.

<issue>
{issue}
</issue>

Guidelines:
- Explore the repository, locate the root cause, and make the smallest change that \
resolves the issue.
- Do NOT modify any test files. Your fix will be judged by the repository's own tests.
- You may run code or existing tests to verify your fix.
- Leave your changes in the working tree (no commit needed) when you are done."""

# Post-run snapshot, run best-effort even if the agent errored: drop GT's index
# dir, stage everything that survived .gitignore (new files must be in the index
# or the verifier's `git clean -fd` deletes them), and save the staged diff as
# the model patch for analysis.
_SNAPSHOT = (
    f"cd {_WORKDIR} && "
    "rm -rf .gt && "
    "git add -A -- . "
    "':(exclude,glob)**/*.pyc' ':(exclude,glob)**/__pycache__' ':(exclude)node_modules' "
    "2>/dev/null; "
    "git -c core.quotepath=false diff --cached > /logs/agent/model_patch.diff 2>/dev/null "
    "|| true"
)


class NanoSweAgent(BaseInstalledAgent):
    """nano-harness as a SWE-bench (Harbor) agent. The loop, tools, and system
    prompt are exactly what `nano run` ships locally — no benchmark forks.

    GT arm: use :class:`GTNanoSweAgent` below (full container plumbing;
    ``gt_root`` defaults ON to /testbed there). On THIS baseline class
    ``gt_root`` (agent kwarg ``--ak gt_root=/testbed`` or env ``NANO_GT_ROOT``)
    only appends the flag — without the GT payloads nano degrades fail-open to
    stock. Default is OFF — a run without the flag is byte-identical stock nano.
    """

    # gt_root rides Harbor's declarative flag machinery: --ak gt_root=/testbed on
    # the CLI, NANO_GT_ROOT as the env fallback, absent by default (baseline arm).
    CLI_FLAGS = [
        CliFlag(kwarg="gt_root", cli="--gt-root", type="str", env_fallback="NANO_GT_ROOT"),
    ]

    @staticmethod
    def name() -> str:
        return "nano-swe"

    def get_version_command(self) -> str | None:
        return (
            f'"$HOME/.local/bin/uv" tool run --from {_REMOTE_DIR} '
            "python -c \"import nano; print(nano.__version__)\""
        )

    async def install(self, environment: BaseEnvironment) -> None:
        # GT container plumbing lives in GTNanoSweAgent below — this baseline
        # install stays frozen (no gt_engine upload, no groundtruth package,
        # no binary) so baseline runs remain reproducible. A bare-baseline
        # `--ak gt_root=...` run degrades to stock nano inside the container
        # (nano's bridge import is fail-open) — see the marker line in
        # _run_command().
        await environment.upload_dir(_REPO_ROOT / "nano", f"{_REMOTE_DIR}/nano")
        await environment.upload_dir(_REPO_ROOT / "eval", f"{_REMOTE_DIR}/eval")
        await environment.upload_file(
            _REPO_ROOT / "pyproject.toml", f"{_REMOTE_DIR}/pyproject.toml"
        )
        await self.exec_as_root(
            environment, _ENSURE_CURL, env={"DEBIAN_FRONTEND": "noninteractive"}
        )
        # UTF8_ENV: task images run POSIX/C locales; force UTF-8 for nano's
        # Python at install AND run (arm-neutral - see eval/_env.py).
        await self.exec_as_agent(environment, _INSTALL_NANO, env=dict(UTF8_ENV))

    def _model_and_env(self) -> tuple[str, dict[str, str]]:
        # Harbor model names look like "anthropic/claude-opus-4-7"; nano's
        # provider routing wants the bare model name. Exception: when routing
        # through an OpenAI-compatible gateway (OPENAI_BASE_URL set), the full
        # "provider/name" string IS the gateway's model id - pass it through.
        model = self.model_name or "anthropic/claude-opus-4-7"
        if not os.environ.get("OPENAI_BASE_URL"):
            model = model.split("/", 1)[-1]
        # provider_env(): BOM/whitespace-sanitized credentials (a BOM-carrying
        # secret kills every request at the ASCII header encode - GHA run
        # 30496848157); UTF8_ENV: UTF-8 regardless of image locale.
        env = provider_env()
        env.update(UTF8_ENV)
        return model, env

    def _run_command(self, task: str, model: str, gt_flags: str) -> str:
        marker = (
            f"echo '[swe_agent] GT arm active ({gt_flags})'; " if gt_flags else ""
        )
        # `|| true` on the nano run: a partial run (max_iterations) may still
        # pass the tests — never let the agent's exit code abort the trial
        # before grading. The snapshot runs unconditionally for the same reason.
        return (
            f"cd {_WORKDIR} && {marker}"
            f'"$HOME/.local/bin/nano" run {shlex.quote(task)} '
            f"--model {shlex.quote(model)} --max-iterations 100 "
            f"{gt_flags} "
            "</dev/null 2>&1 | tee /logs/agent/nano.txt || true"
        )

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model, env = self._model_and_env()
        task = _TASK_TEMPLATE.format(workdir=_WORKDIR, issue=instruction)
        gt_flags = self.build_cli_flags()  # "" (baseline) or "--gt-root <path>"
        await self.exec_as_agent(
            environment, self._run_command(task, model, gt_flags), env=env
        )
        await self.exec_as_agent(environment, _SNAPSHOT)


class GTNanoSweAgent(NanoSweAgent):
    """nano-harness + GroundTruth as a SWE-bench (Harbor) agent.

    The container plumbing is :class:`eval.tb_agent.GTNanoAgent`'s, ported
    verbatim (that arm is proven live in containers — TB2 run 30501483446):
    upload gt_engine/ + the vendored ``groundtruth`` wheel + a Linux
    ``gt-index`` binary, install wheel+numpy into nano's OWN uv tool venv,
    fail-closed install smoke (gateway import + a real index run), forward
    every host ``GT_*`` var, resolve ``GT_RL_PROFILE`` kwarg > env > "2", pin
    ``GT_INDEX_BINARY`` to the staged binary. See GTNanoAgent's docstring for
    the wheel/binary provenance rationale (no public release assets exist).

    SWE-specific deltas, each with a reason:

    - ``--gt-root /testbed`` (literal, not ``"$PWD"``): SWE-bench bakes the
      repo location into the task images (WORKDIR /testbed) and the verifier
      grades /testbed in place — the root is a benchmark constant, and the
      run command already ``cd``'s there. The CliFlag stays, so
      ``--ak gt_root=...`` / ``NANO_GT_ROOT`` still override; only the
      default flips ON.
    - GT artifacts vs grading: gt_engine's delivery ledger goes to
      ``/logs/agent/gt_ledger.jsonl`` (bridge._ledger_path prefers /logs/agent,
      which exists in every harbor container) so it survives in the results
      artifact. GT's ``.gt/`` index dir DOES live in /testbed, but it is
      self-gitignored at creation (indexer.ensure_index writes ``.gt/.gitignore``
      before the first index run; the L6 mid-task re-index reuses the same
      path) and _SNAPSHOT ``rm -rf``'s it before ``git add -A`` — it can reach
      neither model_patch.diff nor the graded tree.

    Baselines cannot be contaminated: nothing here runs unless harbor is
    pointed at ``eval.swe_agent:GTNanoSweAgent`` explicitly.
    """

    # gt_root defaults ON to the SWE-bench workdir; kwarg/env still win.
    CLI_FLAGS = [
        CliFlag(
            kwarg="gt_root",
            cli="--gt-root",
            type="str",
            default=_WORKDIR,
            env_fallback="NANO_GT_ROOT",
        ),
    ]
    ENV_VARS = [
        EnvVar(
            kwarg="gt_profile",
            env="GT_RL_PROFILE",
            default="2",
            env_fallback="GT_RL_PROFILE",
        ),
    ]

    @staticmethod
    def name() -> str:
        return "nano-swe-gt"

    # Shared vendored-artifact discovery (fail-fast messages included) —
    # plain-function re-exports so classmethod-style calls work here too.
    _gt_wheel = staticmethod(_GTNanoTB._gt_wheel)
    _gt_binary_host = staticmethod(_GTNanoTB._gt_binary_host)

    async def install(self, environment: BaseEnvironment) -> None:
        # Fail fast (before any container work) if the GT artifacts are absent:
        # a GT run without an indexer or the groundtruth package silently
        # degrades to baseline behavior — a wasted paid run, not an experiment.
        wheel = self._gt_wheel()
        binary = self._gt_binary_host()

        await environment.upload_dir(_REPO_ROOT / "nano", f"{_REMOTE_DIR}/nano")
        await environment.upload_dir(_REPO_ROOT / "eval", f"{_REMOTE_DIR}/eval")
        await environment.upload_dir(
            _REPO_ROOT / "gt_engine", f"{_REMOTE_DIR}/gt_engine"
        )
        await environment.upload_file(
            _REPO_ROOT / "pyproject.toml", f"{_REMOTE_DIR}/pyproject.toml"
        )
        remote_wheel = f"{_REMOTE_DIR}/{wheel.name}"
        await environment.upload_file(wheel, remote_wheel)
        await environment.upload_file(binary, _REMOTE_GT_BINARY)

        await self.exec_as_root(
            environment, _ENSURE_CURL, env={"DEBIAN_FRONTEND": "noninteractive"}
        )
        await self.exec_as_root(environment, f"chmod 755 {_REMOTE_GT_BINARY}")
        # numpy: pyproject [gt] extra — the v1r brief leg needs it; without it
        # that one leg abstains. Install both into nano's OWN tool venv (never
        # the task's /testbed conda env).
        await self.exec_as_agent(
            environment,
            _install_nano_cmd(f"--with {shlex.quote(remote_wheel)} --with numpy "),
            env=dict(UTF8_ENV),
        )
        # Smoke the stack inside the container, fail-closed at install time:
        # (a) groundtruth + gt_engine import from nano's tool venv (default uv
        #     tool layout; we installed the tool ourselves two lines up),
        # (b) the staged binary actually indexes and links (CGO/libc check).
        await self.exec_as_agent(
            environment,
            "set -eu; "
            '"$HOME/.local/share/uv/tools/nano-harness/bin/python" -c '
            "'import groundtruth.runtime.gateway, gt_engine' && "
            f'"{_REMOTE_GT_BINARY}" -root {_REMOTE_DIR}/gt_engine '
            "-output /tmp/gt-install-smoke.db >/dev/null && "
            "test -s /tmp/gt-install-smoke.db && rm -f /tmp/gt-install-smoke.db",
            env=dict(UTF8_ENV),
        )

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model, env = self._model_and_env()
        # Forward the host's GT flag surface (the workflow's knobs), then let
        # harbor's descriptor resolution decide GT_RL_PROFILE (kwarg > host
        # env > default "2"), then pin the staged indexer binary.
        env.update({k: v for k, v in os.environ.items() if k.startswith("GT_")})
        env.update(self.resolve_env_vars())
        env.setdefault("GT_INDEX_BINARY", _REMOTE_GT_BINARY)
        task = _TASK_TEMPLATE.format(workdir=_WORKDIR, issue=instruction)
        gt_flags = self.build_cli_flags()  # "--gt-root /testbed" by default
        await self.exec_as_agent(
            environment, self._run_command(task, model, gt_flags), env=env
        )
        await self.exec_as_agent(environment, _SNAPSHOT)
