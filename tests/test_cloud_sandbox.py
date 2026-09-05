"""Per-session sandboxes and the egress policy.

Two layers, deliberately:

* **Unit** — no Docker needed. The `docker` argv the server constructs *is* the
  security boundary (which user, which limits, which environment variables,
  which mounts), so it is asserted directly, and the proxy's allow-list is
  imported from the file that ships in the proxy image.
* **Integration** — skipped cleanly when there is no daemon, real when there is
  one. They build both images, start a sandbox on a temporary workspace, and
  prove the bind mount, the uid, the timeout and the egress policy.

The first integration run pays for the sandbox image build (~2 min: Debian +
build-essential + node). Every run after that hits the layer cache and the
whole file finishes well inside 90 s.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud.server import environment as sb_env
from cloud.server import sandbox as sb

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_CONTEXT = REPO_ROOT / "cloud" / "sandbox"
PROXY_CONTEXT = SANDBOX_CONTEXT / "proxy"
PROXY_SOURCE = PROXY_CONTEXT / "proxy.py"

#: every SANDBOX_* knob, cleared before each unit test so the defaults are what
#: is under test and a developer's shell cannot change the answer
_SANDBOX_ENV_NAMES = (
    "SANDBOX_MODE",
    "SANDBOX_IMAGE",
    "SANDBOX_NETWORK",
    "SANDBOX_PROXY_CONTAINER",
    "SANDBOX_PROXY_IMAGE",
    "SANDBOX_PROXY_URL",
    "SANDBOX_ALLOW_REGISTRIES",
    "SANDBOX_EGRESS_ALLOW",
    "SANDBOX_ENV_PASSTHROUGH",
    "SANDBOX_MEMORY",
    "SANDBOX_CPUS",
    "SANDBOX_PIDS_LIMIT",
    "SANDBOX_TMPFS_SIZE",
    "DOCKER_BINARY",
)


def _load_proxy_module():
    """Import the proxy exactly as it ships — by path, not as a package."""
    spec = importlib.util.spec_from_file_location("gt_egress_proxy", PROXY_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gt_egress_proxy"] = module
    spec.loader.exec_module(module)
    return module


proxy = _load_proxy_module()


@pytest.fixture(autouse=True)
def clean_sandbox_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SANDBOX_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _env_pairs(argv: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for index, item in enumerate(argv):
        if item == "-e":
            key, _, value = argv[index + 1].partition("=")
            pairs[key] = value
    return pairs


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_sandbox_mode_defaults_to_local_so_nothing_changes_unasked() -> None:
    assert sb.sandbox_mode() == "local"
    assert sb.is_docker_mode() is False


def test_docker_mode_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "Docker")
    assert sb.is_docker_mode() is True


def test_container_name_is_session_scoped() -> None:
    assert sb.container_name("abc123") == "gt-sandbox-abc123"


def test_egress_allow_list_is_git_plus_registries_plus_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert set(sb.GIT_ALLOW) <= set(sb.egress_allow_list())
    assert "pypi.org" in sb.egress_allow_list()

    monkeypatch.setenv("SANDBOX_ALLOW_REGISTRIES", "0")
    assert "pypi.org" not in sb.egress_allow_list()

    monkeypatch.setenv("SANDBOX_EGRESS_ALLOW", "internal.example.com, *.corp.dev")
    allowed = sb.egress_allow_list()
    assert "internal.example.com" in allowed
    assert "*.corp.dev" in allowed


def test_the_model_api_is_never_on_the_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    allowed = sb.egress_allow_list()
    assert not any("openrouter" in host for host in allowed)
    assert not any("api.deepseek.com" == host for host in allowed)


# ---------------------------------------------------------------------------
# the environment a sandbox command sees
# ---------------------------------------------------------------------------


def test_exec_argv_runs_as_agent_under_an_in_container_timeout() -> None:
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)
    argv = env.exec_argv("ls -la")

    assert argv[:3] == ["docker", "exec", "-i"]
    assert _flag_value(argv, "-u") == "agent"
    assert _flag_value(argv, "-w") == "/workspace"
    assert argv[-7:] == [
        "gt-sandbox-s1", "timeout", "--signal=KILL", "30s", "bash", "-c", "ls -la",
    ]


def test_exec_argv_passes_the_command_verbatim_as_one_argument() -> None:
    command = "python - <<'EOF'\nprint('a b \"c\" $HOME')\nEOF\n"
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)
    argv = env.exec_argv(command)

    # No shell is involved anywhere, so the command needs no quoting and must
    # arrive byte-identical: quoting it would corrupt every heredoc an agent
    # writes.
    assert argv[-1] == command
    assert argv.count(command) == 1


def test_exec_argv_honours_a_per_call_cwd_and_timeout() -> None:
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)
    argv = env.exec_argv("true", cwd="/workspace/src", timeout=5)
    assert _flag_value(argv, "-w") == "/workspace/src"
    assert "5s" in argv


def test_exec_env_is_allow_listed_and_carries_the_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/server/only/bin")
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("HARNESS_FLAG", "on")
    monkeypatch.setenv("SANDBOX_ENV_PASSTHROUGH", "HARNESS_FLAG")

    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)
    pairs = _env_pairs(env.exec_argv("true"))

    assert pairs["HARNESS_FLAG"] == "on"
    # PATH and HOME come from the IMAGE, never from the server process: the
    # server is a different filesystem, and its HOME would point pip at /root.
    assert "PATH" not in pairs
    assert pairs["HOME"] == "/home/agent"
    assert pairs["HTTPS_PROXY"] == "http://gt-egress-proxy:3128"
    assert pairs["NO_PROXY"] == "localhost,127.0.0.1"


def test_exec_env_never_carries_a_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("VENDOR_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("SAFE_VALUE", "fine")
    # Even explicitly asking for a secret by name does not get one through.
    monkeypatch.setenv(
        "SANDBOX_ENV_PASSTHROUGH",
        "OPENAI_API_KEY,GITHUB_TOKEN,VENDOR_ACCESS_TOKEN,SAFE_VALUE",
    )

    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)
    argv = env.exec_argv("true")
    pairs = _env_pairs(argv)

    assert pairs.get("SAFE_VALUE") == "fine"
    assert "OPENAI_API_KEY" not in pairs
    assert "GITHUB_TOKEN" not in pairs
    assert "VENDOR_ACCESS_TOKEN" not in pairs
    assert "sk-live-secret" not in " ".join(argv)
    assert "ghp_secret" not in " ".join(argv)


def test_config_env_is_filtered_through_the_same_allow_list() -> None:
    env = sb.DockerSandboxEnvironment(
        container="gt-sandbox-s1",
        timeout=30,
        env={"TERM": "dumb", "SNEAKY": "x", "AWS_SECRET_ACCESS_KEY": "y"},
    )
    pairs = env.execution_env()
    assert pairs["TERM"] == "dumb"
    assert "SNEAKY" not in pairs
    assert "AWS_SECRET_ACCESS_KEY" not in pairs


# ---------------------------------------------------------------------------
# docker run: the isolation boundary
# ---------------------------------------------------------------------------


def test_run_argv_pins_resources_and_mounts_only_the_workspace() -> None:
    argv = sb.run_argv("s1", "/srv/gt-workspaces/s1")

    assert _flag_value(argv, "--name") == "gt-sandbox-s1"
    assert _flag_value(argv, "--network") == "gt-sandbox-net"
    assert _flag_value(argv, "--memory") == "2g"
    assert _flag_value(argv, "--cpus") == "2"
    assert _flag_value(argv, "--pids-limit") == "512"
    assert _flag_value(argv, "--security-opt") == "no-new-privileges"
    assert _flag_value(argv, "--cap-drop") == "ALL"
    assert _flag_value(argv, "--tmpfs").startswith("/tmp:rw,nosuid,nodev,exec,size=")
    assert argv.count("-v") == 1
    assert _flag_value(argv, "-v") == "/srv/gt-workspaces/s1:/workspace"
    assert _flag_value(argv, "-w") == "/workspace"
    assert argv[-1] == "gt-sandbox:latest"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_prepare_workspace_makes_the_clone_writable_without_giving_it_away(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    plain = workspace / "pkg" / "mod.py"
    plain.write_text("x\n", encoding="utf-8")
    script = workspace / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    plain.chmod(0o600)
    script.chmod(0o700)
    (workspace / "pkg").chmod(0o700)

    sb.prepare_workspace(str(workspace))

    # a+rwX: directories always executable, files only if they already were.
    assert (workspace / "pkg").stat().st_mode & 0o777 == 0o777
    assert plain.stat().st_mode & 0o777 == 0o666
    assert script.stat().st_mode & 0o777 == 0o777
    # Ownership is untouched, so the server's own git never sees a tree owned
    # by somebody else.
    assert plain.stat().st_uid == os.getuid()


def test_prepare_workspace_is_quiet_about_a_missing_tree(tmp_path: Path) -> None:
    sb.prepare_workspace(str(tmp_path / "nope"))


def test_run_argv_never_mounts_the_docker_socket() -> None:
    argv = sb.run_argv("s1", "/srv/gt-workspaces/s1")
    assert "docker.sock" not in " ".join(argv)
    assert "--privileged" not in argv


def test_run_argv_exports_the_proxy_to_the_whole_container() -> None:
    pairs = _env_pairs(sb.run_argv("s1", "/srv/gt-workspaces/s1"))
    assert pairs["HTTP_PROXY"] == pairs["https_proxy"] == "http://gt-egress-proxy:3128"


def test_run_argv_honours_the_resource_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MEMORY", "4g")
    monkeypatch.setenv("SANDBOX_CPUS", "1")
    monkeypatch.setenv("SANDBOX_PIDS_LIMIT", "128")
    argv = sb.run_argv("s1", "/tmp/ws")
    assert _flag_value(argv, "--memory") == "4g"
    assert _flag_value(argv, "--cpus") == "1"
    assert _flag_value(argv, "--pids-limit") == "128"


# ---------------------------------------------------------------------------
# execute(): the CloudLocalEnvironment contract, in a container
# ---------------------------------------------------------------------------


class _FakePopen:
    """Stands in for a `docker exec` child process."""

    def __init__(
        self,
        *,
        stdout: str = "",
        returncode: int = 0,
        delay: float = 0.0,
        raise_timeout: bool = False,
    ) -> None:
        self._stdout = stdout
        self.returncode = returncode
        self._delay = delay
        self._raise_timeout = raise_timeout
        self.argv: list[str] = []
        self.communicate_timeouts: list[float | None] = []
        self.killed = False

    def __call__(self, argv: list[str], **_kwargs: object) -> _FakePopen:
        self.argv = argv
        return self

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_timeouts.append(timeout)
        if self._delay:
            time.sleep(self._delay)
        if self._raise_timeout and not self.killed:
            raise subprocess.TimeoutExpired("docker exec", timeout or 0)
        return self._stdout, ""

    def kill(self) -> None:
        self.killed = True


def _patch_popen(monkeypatch: pytest.MonkeyPatch, fake: _FakePopen) -> None:
    monkeypatch.setattr(sb.subprocess, "Popen", fake)


def test_a_successful_command_returns_the_local_environment_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(stdout="hello\n", returncode=0)
    _patch_popen(monkeypatch, fake)
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)

    result = env.execute({"command": "echo hello"})

    assert result == {"output": "hello\n", "returncode": 0, "exception_info": ""}


def test_the_environment_does_not_truncate_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirrors CloudLocalEnvironment exactly: the model sees the full text, and
    # the 4000-character cap lives in the event emitter, not here.
    payload = "x" * 10_000
    _patch_popen(monkeypatch, _FakePopen(stdout=payload, returncode=0))
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)

    assert len(env.execute({"command": "cat big"})["output"]) == 10_000


def test_the_submit_marker_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minisweagent.exceptions import Submitted

    _patch_popen(
        monkeypatch,
        _FakePopen(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\ndone\n"),
    )
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)

    with pytest.raises(Submitted):
        env.execute({"command": "submit"})


def test_the_client_side_backstop_kills_a_wedged_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(stdout="partial", raise_timeout=True)
    _patch_popen(monkeypatch, fake)
    killed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        sb, "_docker", lambda *args, **kwargs: killed.append(args) or SimpleNamespace(
            returncode=0, stdout="", stderr=""
        )
    )
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)

    result = env.execute({"command": "sleep 999"})

    assert result["returncode"] == -1
    assert "timed out" in result["exception_info"]
    assert result["extra"]["exception_type"] == "TimeoutExpired"
    assert result["output"] == "partial"
    assert fake.killed is True
    # The backstop waits past the in-container timeout, never instead of it.
    assert fake.communicate_timeouts[0] == 30 + sb.TIMEOUT_GRACE_SECONDS
    assert any("pkill" in arg for call in killed for arg in call)


def test_an_in_container_kill_is_reported_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GNU `timeout --signal=KILL` reports 137 once the limit is reached.
    _patch_popen(monkeypatch, _FakePopen(stdout="", returncode=137, delay=1.05))
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=1)

    result = env.execute({"command": "sleep 999"})

    assert result["returncode"] == -1
    assert result["extra"]["exception_type"] == "TimeoutExpired"


def test_a_fast_137_is_not_mistaken_for_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A command that is itself SIGKILLed immediately is a real exit code, not a
    # timeout: the elapsed-time check is what tells them apart.
    _patch_popen(monkeypatch, _FakePopen(stdout="oom", returncode=137))
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)

    result = env.execute({"command": "hog"})

    assert result == {"output": "oom", "returncode": 137, "exception_info": ""}


def test_template_vars_describe_the_container_and_drop_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
    env = sb.DockerSandboxEnvironment(container="gt-sandbox-s1", timeout=30)
    env._uname_cache = ("Linux", "6.1.0-test", "x86_64")

    variables = env.get_template_vars()

    # prompts.py renders {{system}} {{release}} {{machine}} and {{cwd}}.
    assert variables["system"] == "Linux"
    assert variables["release"] == "6.1.0-test"
    assert variables["machine"] == "x86_64"
    assert variables["cwd"] == "/workspace"
    assert variables["container"] == "gt-sandbox-s1"
    assert "OPENAI_API_KEY" not in variables


def test_serialize_names_the_sandbox_environment() -> None:
    env = sb.DockerSandboxEnvironment(
        container="gt-sandbox-s1", image="gt-sandbox:latest", timeout=30
    )
    info = env.serialize()["info"]["config"]
    assert info["environment_type"].endswith("DockerSandboxEnvironment")
    assert info["environment"]["container"] == "gt-sandbox-s1"
    assert info["environment"]["image"] == "gt-sandbox:latest"


def test_remove_sandbox_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("docker is gone")

    monkeypatch.setattr(sb, "_docker", _boom)
    assert sb.remove_sandbox("s1") is False


def test_reap_sandboxes_keeps_live_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sb, "list_sandboxes", lambda: ["alive", "orphan"])
    removed: list[str] = []
    monkeypatch.setattr(
        sb, "remove_sandbox", lambda session_id: removed.append(session_id) or True
    )
    assert sb.reap_sandboxes({"alive"}) == ["orphan"]
    assert removed == ["orphan"]


# ---------------------------------------------------------------------------
# the egress policy, as the proxy image implements it
# ---------------------------------------------------------------------------


def test_the_proxy_allow_list_matches_the_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The proxy ships in its own image and cannot import cloud.server.sandbox,
    # so the two copies of the policy are pinned to each other here.
    assert proxy.DEFAULT_ALLOW == sb.GIT_ALLOW
    assert proxy.REGISTRY_ALLOW == sb.REGISTRY_ALLOW

    monkeypatch.setenv("EGRESS_ALLOW", "")
    monkeypatch.setenv("EGRESS_ALLOW_REGISTRIES", "1")
    monkeypatch.setenv("SANDBOX_EGRESS_ALLOW", "")
    monkeypatch.setenv("SANDBOX_ALLOW_REGISTRIES", "1")
    assert list(proxy.allow_list()) == sb.egress_allow_list()


def test_the_proxy_allows_git_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGRESS_ALLOW", raising=False)
    patterns = proxy.allow_list()
    for host in ("github.com", "api.github.com", "codeload.github.com",
                 "objects.githubusercontent.com", "GitHub.com", "github.com."):
        assert proxy.is_allowed(host, patterns), host


def test_the_proxy_blocks_the_model_api_and_everything_unlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EGRESS_ALLOW", raising=False)
    patterns = proxy.allow_list()
    for host in ("openrouter.ai", "api.openai.com", "api.deepseek.com",
                 "1.1.1.1", "169.254.169.254", "example.com",
                 "github.com.evil.test", "notgithub.com", ""):
        assert not proxy.is_allowed(host, patterns), host


def test_the_proxy_registry_hosts_follow_the_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EGRESS_ALLOW", raising=False)
    monkeypatch.setenv("EGRESS_ALLOW_REGISTRIES", "1")
    assert proxy.is_allowed("pypi.org", proxy.allow_list())
    monkeypatch.setenv("EGRESS_ALLOW_REGISTRIES", "0")
    assert not proxy.is_allowed("pypi.org", proxy.allow_list())
    assert proxy.is_allowed("github.com", proxy.allow_list())


def test_the_proxy_extra_hosts_support_wildcards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EGRESS_ALLOW", "*.corp.dev , internal.example.com")
    patterns = proxy.allow_list()
    assert proxy.is_allowed("git.corp.dev", patterns)
    assert proxy.is_allowed("internal.example.com", patterns)
    assert not proxy.is_allowed("corp.dev.evil.test", patterns)


def test_the_proxy_parses_authorities() -> None:
    assert proxy.split_host_port("github.com:443", 80) == ("github.com", 443)
    assert proxy.split_host_port("github.com", 80) == ("github.com", 80)
    assert proxy.split_host_port("[::1]:8080", 80) == ("::1", 8080)


# ---------------------------------------------------------------------------
# integration — real containers, skipped cleanly without a daemon
# ---------------------------------------------------------------------------


def _daemon_available() -> bool:
    if os.environ.get("GT_SANDBOX_SKIP_DOCKER"):
        return False
    try:
        probe = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


HAS_DOCKER = _daemon_available()
needs_docker = pytest.mark.skipif(
    not HAS_DOCKER, reason="no docker daemon available"
)


def _build_image(tag: str, context: Path) -> None:
    build = subprocess.run(
        ["docker", "build", "-t", tag, str(context)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert build.returncode == 0, (build.stdout + build.stderr)[-3000:]


@pytest.fixture(scope="session")
def sandbox_image() -> str:
    tag = os.environ.get("GT_SANDBOX_TEST_IMAGE", "gt-sandbox:test")
    _build_image(tag, SANDBOX_CONTEXT)
    return tag


@pytest.fixture(scope="session")
def egress_proxy_image() -> str:
    tag = os.environ.get("GT_SANDBOX_TEST_PROXY_IMAGE", "gt-egress-proxy:test")
    _build_image(tag, PROXY_CONTEXT)
    return tag


@pytest.fixture
def sandbox(
    tmp_path: Path,
    sandbox_image: str,
    egress_proxy_image: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """A real session sandbox on a temporary workspace, behind a real proxy."""
    suffix = uuid.uuid4().hex[:8]
    session_id = f"pytest{suffix}"
    network = f"gt-sandbox-net-test-{suffix}"
    proxy_name = f"gt-egress-proxy-test-{suffix}"
    workspace = tmp_path / "ws"
    workspace.mkdir()

    monkeypatch.setenv("SANDBOX_MODE", "docker")
    monkeypatch.setenv("SANDBOX_IMAGE", sandbox_image)
    monkeypatch.setenv("SANDBOX_NETWORK", network)
    monkeypatch.setenv("SANDBOX_PROXY_IMAGE", egress_proxy_image)
    monkeypatch.setenv("SANDBOX_PROXY_CONTAINER", proxy_name)
    monkeypatch.setenv("SANDBOX_PROXY_URL", f"http://{proxy_name}:3128")

    try:
        info = sb.start_sandbox(session_id, str(workspace))
        yield SimpleNamespace(
            session_id=session_id,
            workspace=workspace,
            info=info,
            env=sb.DockerSandboxEnvironment(
                container=info["container"], image=sandbox_image, timeout=30
            ),
        )
    finally:
        sb.remove_sandbox(session_id)
        for argv in (
            ["docker", "rm", "-f", proxy_name],
            ["docker", "network", "rm", network],
        ):
            subprocess.run(argv, capture_output=True, timeout=120)


@needs_docker
def test_the_sandbox_runs_as_uid_1000_in_its_own_container(sandbox) -> None:
    result = sandbox.env.execute({"command": "id -u; id -un; hostname; pwd"})
    lines = result["output"].split()

    assert result["returncode"] == 0
    assert lines[0] == "1000"
    assert lines[1] == "agent"
    assert lines[2] != os.environ.get("COMPUTERNAME", "").lower()
    assert lines[3] == "/workspace"
    assert sandbox.info["container"] == f"gt-sandbox-{sandbox.session_id}"
    assert sandbox.info["image_digest"].startswith("sha256:")


@needs_docker
def test_sandbox_writes_land_on_the_host_workspace(sandbox) -> None:
    result = sandbox.env.execute({"command": "echo hi > out.txt && ls -l out.txt"})

    assert result["returncode"] == 0
    # The bind mount is the point: the server computes the diff and runs GT
    # indexing against these very bytes.
    assert (sandbox.workspace / "out.txt").read_text(encoding="utf-8") == "hi\n"


@needs_docker
def test_the_host_sees_files_the_server_wrote_too(sandbox) -> None:
    (sandbox.workspace / "from_server.txt").write_text("server\n", encoding="utf-8")
    result = sandbox.env.execute({"command": "cat from_server.txt"})
    assert result["output"] == "server\n"


@needs_docker
def test_the_sandbox_has_no_docker_socket(sandbox) -> None:
    result = sandbox.env.execute(
        {"command": "test -S /var/run/docker.sock && echo LEAKED || echo none"}
    )
    assert result["output"].strip() == "none"


@needs_docker
def test_resource_limits_are_really_applied(sandbox) -> None:
    inspected = subprocess.run(
        ["docker", "inspect", "-f",
         "{{.HostConfig.Memory}} {{.HostConfig.PidsLimit}}",
         sandbox.info["container"]],
        capture_output=True, text=True, timeout=60,
    )
    memory, pids = inspected.stdout.split()
    assert int(memory) == 2 * 1024**3
    assert int(pids) == 512


@needs_docker
def test_interrupt_kills_the_command_in_flight(sandbox) -> None:
    """P2-4: a Stop must not wait for `sleep 120` to finish on its own."""
    result: dict[str, dict] = {}
    running = threading.Event()

    def run() -> None:
        running.set()
        result["out"] = sandbox.env.execute({"command": "sleep 120"}, timeout=300)

    worker = threading.Thread(target=run, daemon=True)
    started = time.monotonic()
    worker.start()
    assert running.wait(5.0)
    time.sleep(1.0)
    sandbox.env.interrupt()
    worker.join(30.0)
    elapsed = time.monotonic() - started

    assert not worker.is_alive(), "execute() never returned after interrupt()"
    assert elapsed < 20.0, f"the interrupt took {elapsed:.1f}s"
    assert result["out"]["returncode"] == sb_env.INTERRUPT_RETURNCODE
    assert result["out"]["exception_info"] == sb_env.INTERRUPT_MESSAGE

    # The container is still usable for the rest of the session.
    assert sandbox.env.execute({"command": "echo alive"})["output"].strip() == "alive"


@needs_docker
def test_a_command_that_overruns_its_timeout_is_killed(sandbox) -> None:
    started = time.monotonic()
    result = sandbox.env.execute({"command": "sleep 30"}, timeout=2)
    elapsed = time.monotonic() - started

    assert result["returncode"] == -1
    assert result["extra"]["exception_type"] == "TimeoutExpired"
    # Killed by the in-container `timeout`, well before the client backstop.
    assert elapsed < 2 + sb.TIMEOUT_GRACE_SECONDS


@needs_docker
def test_the_sandbox_has_no_route_off_net_without_the_proxy(sandbox) -> None:
    result = sandbox.env.execute(
        {"command": "curl -sS --noproxy '*' -m 8 -I https://github.com "
                    "&& echo REACHED || echo blocked"},
        timeout=40,
    )
    assert "REACHED" not in result["output"]
    assert "blocked" in result["output"]


@needs_docker
def test_the_egress_policy_allows_git_and_blocks_everything_else(sandbox) -> None:
    github = sandbox.env.execute(
        {"command": "curl -sS -o /dev/null -m 25 -w '%{http_code}' -I "
                    "https://github.com"},
        timeout=60,
    )
    if github["returncode"] != 0 or github["output"].strip() in {"000", ""}:
        pytest.skip(f"no outbound network from this host: {github['output'][:200]}")
    assert github["output"].strip() in {"200", "301", "302"}

    # The model API is not reachable from a sandbox at all: the CONNECT is
    # refused by the proxy before any connection is opened.
    model_api = sandbox.env.execute(
        {"command": "curl -sS -m 25 -I https://openrouter.ai"}, timeout=60
    )
    assert model_api["returncode"] != 0
    assert "403" in model_api["output"]

    # A bare IP cannot dodge the allow-list either.
    raw_ip = sandbox.env.execute(
        {"command": "curl -sS -o /dev/null -m 25 -w '%{http_code}' -I "
                    "http://1.1.1.1"},
        timeout=60,
    )
    assert raw_ip["output"].strip() == "403"


@needs_docker
def test_a_sandbox_is_removed_and_reaped_by_session_id(
    tmp_path: Path, sandbox_image: str, egress_proxy_image: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    session_id = f"pytest{suffix}"
    network = f"gt-sandbox-net-test-{suffix}"
    proxy_name = f"gt-egress-proxy-test-{suffix}"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("SANDBOX_IMAGE", sandbox_image)
    monkeypatch.setenv("SANDBOX_NETWORK", network)
    monkeypatch.setenv("SANDBOX_PROXY_IMAGE", egress_proxy_image)
    monkeypatch.setenv("SANDBOX_PROXY_CONTAINER", proxy_name)
    monkeypatch.setenv("SANDBOX_PROXY_URL", f"http://{proxy_name}:3128")
    try:
        sb.start_sandbox(session_id, str(workspace))
        assert session_id in sb.list_sandboxes()
        sb.ensure_running(session_id)

        assert sb.reap_sandboxes({session_id}) == []
        assert session_id in sb.list_sandboxes()

        assert sb.reap_sandboxes(set()) == [session_id]
        assert session_id not in sb.list_sandboxes()
        with pytest.raises(sb.SandboxError):
            sb.ensure_running(session_id)
    finally:
        sb.remove_sandbox(session_id)
        for argv in (
            ["docker", "rm", "-f", proxy_name],
            ["docker", "network", "rm", network],
        ):
            subprocess.run(argv, capture_output=True, timeout=120)
