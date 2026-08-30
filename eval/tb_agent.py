"""Terminal-Bench 2.0 adapter (Harbor installed agent).

Uploads the local nano-harness checkout into each task container, installs it
with uv, and runs `nano run "<instruction>"` inside. Harbor owns the sandbox,
timeouts, and grading; we own nothing but the agent.

Usage (host needs Docker running):

    pip install harbor
    export ANTHROPIC_API_KEY=...
    harbor run -d terminal-bench/terminal-bench-2 \
        --agent-import-path eval.tb_agent:NanoAgent \
        -m anthropic/claude-opus-4-7 \
        -n 4 --jobs-dir results/terminal-bench

Smoke test on a few tasks first: add `-t hello-world` (repeatable flag).
Results land in results/terminal-bench/<timestamp>/result.json; per-task agent
stdout in <task>/agent/nano.txt.

GT arm: `--agent-import-path eval.tb_agent:GTNanoAgent` (see the class
docstring; needs vendor/groundtruth_mcp-*.whl and a Linux gt-index binary at
vendor/gt-index-linux-amd64 or $GT_INDEX_BINARY_HOST — the tb2_gt.yml
workflow stages both). Baseline NanoAgent is untouched by GT.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    EnvVar,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from eval._env import UTF8_ENV, provider_env

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REMOTE_DIR = "/installed-agent/nano-harness"
# GT arm: container path for the Linux gt-index binary (staged by the workflow
# into vendor/, uploaded by GTNanoAgent.install; /installed-agent exists after
# BaseInstalledAgent.setup()). find_binary() honors $GT_INDEX_BINARY first.
_REMOTE_GT_BINARY = "/installed-agent/gt-index"
_VENDOR_DIR = _REPO_ROOT / "vendor"
_GT_STAGED_SOURCE_CLEANUP = (
    "test \"$(readlink -f /installed-agent/nano-harness)\" = "
    "\"/installed-agent/nano-harness\" && "
    "rm -rf -- /installed-agent/nano-harness"
)

# Task images vary (debian, alpine, ...); make sure curl exists, then let uv
# bring its own Python so we never depend on the image's python3.
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


class NanoAgent(BaseInstalledAgent):
    """nano-harness as a Terminal-Bench 2.0 agent. The loop, tools, and system
    prompt are exactly what `nano run` ships locally — no benchmark forks."""

    @staticmethod
    def name() -> str:
        return "nano"

    def get_version_command(self) -> str | None:
        return (
            '"$HOME/.local/bin/uv" tool run --from /installed-agent/nano-harness '
            "python -c \"import nano; print(nano.__version__)\""
        )

    async def install(self, environment: BaseEnvironment) -> None:
        # GT container plumbing lives in GTNanoAgent below — this baseline
        # install stays frozen (no gt_engine upload, no groundtruth package,
        # no binary, no --gt-root) so baseline runs remain reproducible.
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

    def _run_command(self, instruction: str, model: str, extra_args: str = "") -> str:
        # `|| true`: a partial run (max_iterations) may still pass the tests —
        # never let the agent's exit code abort the trial before grading.
        # extra_args, when non-empty, must end with a space.
        temperature = (os.environ.get("NANO_TEMPERATURE") or "").strip()
        temperature_arg = (
            f"--temperature {shlex.quote(temperature)} " if temperature else ""
        )
        return (
            f'"$HOME/.local/bin/nano" run {shlex.quote(instruction)} '
            f"--model {shlex.quote(model)} --max-iterations 100 "
            f"{temperature_arg}{extra_args}"
            "</dev/null 2>&1 | tee /logs/agent/nano.txt || true"
        )

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        model, env = self._model_and_env()
        await self.exec_as_agent(
            environment, self._run_command(instruction, model), env=env
        )


class GTNanoAgent(NanoAgent):
    """nano-harness + GroundTruth as a Terminal-Bench 2.0 agent.

    Same loop and model routing as :class:`NanoAgent`; additionally the task
    container gets gt_engine/, the ``groundtruth`` package (vendored wheel —
    see below), and a Linux ``gt-index`` binary, and nano runs with
    ``--gt-root "$PWD"`` (the container's WORKDIR = the task's working
    directory; harbor exec commands run through ``sh -c`` with no explicit
    cwd, so ``$PWD`` resolves IN-CONTAINER, which is exactly what we want).

    Why a vendored wheel (``vendor/groundtruth_mcp-*.whl``), not a git URL:
    the public github.com/harneet2512/groundtruth repo does NOT contain the
    gateway runtime (no runtime/gateway.py, evidence_envelope.py, rl_profile.py,
    native_render.py, adapters/miniswe.py on branch gt-trial, checked
    2026-07-29), and no release ships them either (release v1.1.0 does not
    exist; only gt-runtime-models / pipeline-assets-v1). The wheel is built
    from the local D:\\Groundtruth checkout gt_engine was developed against.

    Why an uploaded binary + $GT_INDEX_BINARY, not _binary.py auto-download:
    same finding — there are no gt-index release assets to download, and the
    downloader targets the nonexistent v1.1.0 tag. The workflow builds
    linux-amd64 from the separately checked-out pinned Groundtruth producer
    (CGO + ``-tags sqlite_fts5``); install() uploads the externally built
    artifact and run() pins ``GT_INDEX_BINARY`` so find_binary() never touches
    the network.

    GT flags: run() forwards every host ``GT_*`` env var into the container
    and resolves ``GT_RL_PROFILE`` via the ``gt_profile`` kwarg (harbor
    ``-ak gt_profile=...``) > host env > default "2" (GT production law).
    Baselines cannot be contaminated: nothing here runs unless harbor is
    pointed at ``eval.tb_agent:GTNanoAgent`` explicitly.
    """

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
        return "nano-gt"

    def get_version_command(self) -> str | None:
        # The staged checkout is intentionally removed after the non-editable
        # uv installation, so version discovery uses the installed venv.
        return (
            '"$HOME/.local/share/uv/tools/nano-harness/bin/python" '
            "-c \"import nano; print(nano.__version__)\""
        )

    @staticmethod
    def _gt_wheel() -> Path:
        wheels = sorted(_VENDOR_DIR.glob("groundtruth_mcp-*.whl"))
        if not wheels:
            raise FileNotFoundError(
                f"GT arm needs the vendored groundtruth wheel in {_VENDOR_DIR} "
                "(build with: pip wheel --no-deps -w vendor D:\\Groundtruth)"
            )
        return wheels[-1]  # highest version wins if several are present

    @staticmethod
    def _gt_binary_host() -> Path:
        override = os.environ.get("GT_INDEX_BINARY_HOST", "")
        path = Path(override) if override else Path(os.environ.get("GT_INDEX_BINARY", ""))
        if not path.is_file():
            raise FileNotFoundError(
                f"GT arm needs the pinned external Linux gt-index binary at {path} "
                "(set GT_INDEX_BINARY_HOST or run scripts/build_external_gt_index.sh)"
            )
        return path

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
        # that one leg abstains. Install both into nano's OWN tool venv.
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
        # The task model runs as root and can otherwise discover this checkout
        # with a broad `find /`, then waste its trajectory reverse-engineering
        # GT's refusal logic instead of solving /app. uv installed a regular
        # wheel copy (not editable), and the smoke above proved that copy plus
        # the indexer before this exact, guarded removal.
        await self.exec_as_root(environment, _GT_STAGED_SOURCE_CLEANUP)

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
        # Keep mutable GT state completely outside the graded repository. The
        # task container is isolated per trial, so this private tmp location
        # cannot collide across the five concurrent tasks.
        env["GT_STATE_DIR"] = "/tmp/.nano-gt-state"
        # --gt-root "$PWD": single point of GT activation. Deliberately NOT
        # shlex-quoted — the $PWD must survive into the container's shell and
        # expand THERE (harbor prefixes `set -o pipefail; ` and execs the
        # string through the container shell with no explicit cwd, i.e. the
        # image WORKDIR = the TB task's working directory). Double quotes keep
        # paths with spaces intact while still allowing the expansion.
        command = self._run_command(
            instruction,
            model,
            extra_args=(
                '--gt-root "$PWD" '
                f"--time-budget-seconds "
                f"{shlex.quote(os.environ.get('GT_AGENT_TIMEOUT_SECONDS', '1800'))} "
            ),
        )
        await self.exec_as_agent(environment, command, env=env)
