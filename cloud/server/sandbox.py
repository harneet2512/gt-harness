"""Per-session Docker sandboxes with an allow-listed egress policy.

``SANDBOX_MODE=docker`` gives every session its own long-lived container
(``gt-sandbox-<session_id>``) started right after the clone and removed on
``close()``. The session workspace is bind-mounted at ``/workspace``, so the
server keeps writing ``.gt_state/`` and running GT indexing in-process against
the *same* files the agent edits — the container is an execution jail, not a
copy of the tree.

Two properties this module exists to guarantee:

1. **Isolation.** Commands run as uid 1000 (``agent``) inside a container with
   ``--memory``/``--cpus``/``--pids-limit`` caps, a tmpfs ``/tmp``,
   ``no-new-privileges``, and **no Docker socket**. Only an allow-listed set of
   environment variables crosses the boundary; provider credentials never can
   (``is_sensitive_env_name`` is applied on top of the allow-list).

2. **Egress policy.** The sandbox network (``gt-sandbox-net``) is created
   ``--internal``: it has no route off the host and no external DNS. The only
   way out is the egress proxy container, which sandboxes reach through
   ``HTTP_PROXY``/``HTTPS_PROXY`` and which serves only the hosts in
   :func:`egress_allow_list` — git endpoints, plus package registries when
   ``SANDBOX_ALLOW_REGISTRIES=1``. The model API is *not* on that list: model
   calls happen in the server process, never in a sandbox.

Everything here shells out to the ``docker`` CLI; there is deliberately no
docker SDK dependency.

The default allow-list is duplicated (with a pointer back here) in
``cloud/sandbox/proxy/proxy.py``, because that file ships in its own image and
cannot import this package. ``tests/test_cloud_sandbox.py`` asserts the two
lists stay identical.
"""
from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterable
from typing import Any

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig

from .environment import is_sensitive_env_name, scrub_sensitive_mapping

__all__ = [
    "DockerSandboxEnvironment",
    "SandboxEnvironmentConfig",
    "SandboxError",
    "container_name",
    "docker_available",
    "egress_allow_list",
    "ensure_running",
    "is_docker_mode",
    "list_sandboxes",
    "prepare_workspace",
    "reap_sandboxes",
    "remove_sandbox",
    "run_argv",
    "sandbox_env",
    "sandbox_mode",
    "start_sandbox",
]

#: container name prefix; the session id is appended verbatim
CONTAINER_PREFIX = "gt-sandbox-"
#: label carrying the session id, so orphans are identifiable by humans too
SESSION_LABEL = "gt.sandbox.session"
#: where the workspace is bind-mounted, and the agent's working directory
SANDBOX_WORKDIR = "/workspace"
SANDBOX_USER = "agent"
SANDBOX_UID = 1000

DEFAULT_IMAGE = "gt-sandbox:latest"
DEFAULT_NETWORK = "gt-sandbox-net"
DEFAULT_PROXY_CONTAINER = "gt-egress-proxy"
DEFAULT_PROXY_IMAGE = "gt-egress-proxy:latest"
DEFAULT_PROXY_PORT = 3128

DEFAULT_MEMORY = "2g"
DEFAULT_CPUS = "2"
DEFAULT_PIDS_LIMIT = "512"
DEFAULT_TMPFS_SIZE = "512m"

#: timeout for `docker` management verbs (run/rm/inspect/network)
DOCKER_TIMEOUT = 180
#: how long the client-side backstop waits past the in-container `timeout`
TIMEOUT_GRACE_SECONDS = 10
#: GNU `timeout` exit codes: 124 (TERM) and 137 (128+KILL)
TIMEOUT_EXIT_CODES = (124, 137)

#: git endpoints — always reachable, this is what a coding agent needs
GIT_ALLOW = (
    "github.com",
    "*.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
)
#: package registries — reachable when SANDBOX_ALLOW_REGISTRIES is truthy
REGISTRY_ALLOW = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
)

#: the only environment variable NAMES that may exist inside a sandbox exec,
#: before SANDBOX_ENV_PASSTHROUGH adds to them
BASE_ENV_ALLOW = ("PATH", "HOME", "LANG", "TERM")
#: proxy plumbing, set on the container and repeated on every exec
PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
)
#: PATH and HOME come from the IMAGE, never from the server process: the server
#: is a different filesystem and a different user, and leaking its HOME would
#: point pip/npm at /root.
SANDBOX_DEFAULT_ENV = {
    "HOME": f"/home/{SANDBOX_USER}",
    "LANG": "C.UTF-8",
    "TERM": "xterm-256color",
}
NO_PROXY_VALUE = "localhost,127.0.0.1"


class SandboxError(RuntimeError):
    """A sandbox could not be created, found, or driven. Sessions fail closed."""


# -- configuration ------------------------------------------------------------


def sandbox_mode() -> str:
    return os.environ.get("SANDBOX_MODE", "local").strip().lower() or "local"


def is_docker_mode() -> bool:
    return sandbox_mode() == "docker"


def docker_binary() -> str:
    return os.environ.get("DOCKER_BINARY", "docker")


def image_name() -> str:
    return os.environ.get("SANDBOX_IMAGE", DEFAULT_IMAGE)


def network_name() -> str:
    return os.environ.get("SANDBOX_NETWORK", DEFAULT_NETWORK)


def proxy_container() -> str:
    return os.environ.get("SANDBOX_PROXY_CONTAINER", DEFAULT_PROXY_CONTAINER)


def proxy_image() -> str:
    return os.environ.get("SANDBOX_PROXY_IMAGE", DEFAULT_PROXY_IMAGE)


def proxy_url() -> str:
    return os.environ.get(
        "SANDBOX_PROXY_URL", f"http://{proxy_container()}:{DEFAULT_PROXY_PORT}"
    )


def allow_registries() -> bool:
    return os.environ.get("SANDBOX_ALLOW_REGISTRIES", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "",
    }


def egress_allow_list() -> list[str]:
    """Every host pattern a sandbox may reach, in policy order.

    Git endpoints always; registries under ``SANDBOX_ALLOW_REGISTRIES`` (on by
    default, because agents run tests that install packages); extra hosts from
    ``SANDBOX_EGRESS_ALLOW``. The model API is never on this list.
    """
    hosts = list(GIT_ALLOW)
    if allow_registries():
        hosts += list(REGISTRY_ALLOW)
    extra = os.environ.get("SANDBOX_EGRESS_ALLOW", "")
    hosts += [item.strip() for item in extra.split(",") if item.strip()]
    seen: dict[str, None] = {}
    for host in hosts:
        seen.setdefault(host.lower(), None)
    return list(seen)


def passthrough_names() -> tuple[str, ...]:
    raw = os.environ.get("SANDBOX_ENV_PASSTHROUGH", "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def is_allowed_env_name(name: str) -> bool:
    """Allow-list first, credential scrub second — a secret can never pass."""
    allowed = set(BASE_ENV_ALLOW) | set(PROXY_ENV_NAMES) | set(passthrough_names())
    return name in allowed and not is_sensitive_env_name(name)


def proxy_env() -> dict[str, str]:
    url = proxy_url()
    return {
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "http_proxy": url,
        "https_proxy": url,
        "NO_PROXY": NO_PROXY_VALUE,
        "no_proxy": NO_PROXY_VALUE,
    }


def sandbox_env() -> dict[str, str]:
    """The environment a sandbox command sees: defaults, proxy, passthrough."""
    env = dict(SANDBOX_DEFAULT_ENV)
    env |= proxy_env()
    for name in passthrough_names():
        value = os.environ.get(name)
        if value is not None and is_allowed_env_name(name):
            env[name] = value
    return {key: value for key, value in env.items() if is_allowed_env_name(key)}


def container_name(session_id: str) -> str:
    return f"{CONTAINER_PREFIX}{session_id}"


# -- docker CLI ---------------------------------------------------------------


def _docker(
    *args: str, timeout: int = DOCKER_TIMEOUT, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - fixed binary, list argv, no shell
        [docker_binary(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:2000]
        raise SandboxError(f"docker {' '.join(args[:2])} failed: {detail}")
    return result


def docker_available() -> bool:
    try:
        return _docker("version", "--format", "{{.Server.Version}}",
                       timeout=30, check=False).returncode == 0
    except Exception:  # noqa: BLE001 - a missing binary is just "unavailable"
        return False


def ensure_network(network: str | None = None) -> str:
    """Create the ``--internal`` sandbox network if it is not there yet.

    ``--internal`` is the egress policy's foundation: containers on it have no
    default route off the host and no external DNS, so the proxy is the only
    way out and nothing can bypass it by dialling an IP directly.
    """
    network = network or network_name()
    exists = _docker(
        "network", "inspect", network, "--format", "{{.Name}}", check=False
    )
    if exists.returncode == 0:
        return network
    create = _docker(
        "network", "create", "--internal", "--driver", "bridge", network, check=False
    )
    if create.returncode != 0 and "already exists" not in (create.stderr or ""):
        raise SandboxError(
            f"could not create sandbox network {network}: "
            f"{(create.stderr or '').strip()[:500]}"
        )
    return network


def ensure_proxy(network: str | None = None) -> str:
    """Make sure the egress proxy is up and attached to the sandbox network.

    A compose deployment already runs it (service ``egress-proxy``), and this is
    then a no-op. Outside compose — local dev, the integration tests — it starts
    one from ``SANDBOX_PROXY_IMAGE``. If it cannot, sandboxes have no egress at
    all, which is the safe direction.
    """
    network = network or network_name()
    name = proxy_container()
    running = _docker(
        "inspect", "-f", "{{.State.Running}}", name, check=False, timeout=60
    )
    if running.returncode == 0 and running.stdout.strip() == "true":
        return name
    if running.returncode == 0:
        _docker("rm", "-f", name, check=False, timeout=60)
    argv = [
        "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "--network", network,
        "--network-alias", "egress-proxy",
        # Same variable names compose uses: EGRESS_ALLOW is the *extra* hosts,
        # the git/registry defaults live in the proxy image itself.
        "-e", f"EGRESS_ALLOW={os.environ.get('SANDBOX_EGRESS_ALLOW', '')}",
        "-e", f"EGRESS_ALLOW_REGISTRIES={'1' if allow_registries() else '0'}",
        "-e", f"EGRESS_PROXY_PORT={DEFAULT_PROXY_PORT}",
        proxy_image(),
    ]
    started = _docker(*argv, check=False)
    if started.returncode != 0:
        raise SandboxError(
            f"could not start egress proxy {name}: "
            f"{(started.stderr or '').strip()[:500]}"
        )
    # The proxy also needs the default bridge, or it has nowhere to forward to.
    _docker("network", "connect", "bridge", name, check=False, timeout=60)
    return name


def run_argv(
    session_id: str,
    workspace: str,
    *,
    image: str | None = None,
    network: str | None = None,
) -> list[str]:
    """The exact ``docker run`` argv for a session sandbox (pure, so it is tested).

    ``workspace`` must be a **host** path: the daemon resolves bind mounts, not
    the server container. See ``cloud/docker-compose.yml`` — the workspaces
    directory is mounted into the server at the same absolute path so this is
    true even though the server itself runs in a container.
    """
    return [
        docker_binary(), "run", "-d",
        "--name", container_name(session_id),
        "--label", f"{SESSION_LABEL}={session_id}",
        "--network", network or network_name(),
        "--memory", os.environ.get("SANDBOX_MEMORY", DEFAULT_MEMORY),
        "--cpus", os.environ.get("SANDBOX_CPUS", DEFAULT_CPUS),
        "--pids-limit", os.environ.get("SANDBOX_PIDS_LIMIT", DEFAULT_PIDS_LIMIT),
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,exec,size={os.environ.get('SANDBOX_TMPFS_SIZE', DEFAULT_TMPFS_SIZE)}",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "-v", f"{workspace}:{SANDBOX_WORKDIR}",
        "-w", SANDBOX_WORKDIR,
        *_env_flags(proxy_env()),
        image or image_name(),
    ]


def prepare_workspace(workspace: str) -> None:
    """Make the clone writable by uid 1000, without handing ownership over.

    The two sides of the bind mount run as different users: the server (root in
    its container) clones and indexes, the sandbox edits as uid 1000. Of the
    two ways to reconcile that, this is the world-writable one — chmod, not
    chown — chosen because:

    * it needs no capability, so the sandbox can keep ``--cap-drop ALL`` (which
      takes ``CAP_CHOWN`` away from root inside the container as well);
    * ownership stays with the server, so its own ``git diff`` never trips
      git's "dubious ownership" check on a tree owned by somebody else.

    ``a+rwX`` semantics: directories always get ``x``, files only if they were
    executable already. Best effort and POSIX-only — on Docker Desktop the
    bind mount synthesises ownership and permissions anyway.
    """
    if os.name != "posix" or not os.path.isdir(workspace):
        return
    for root, _dirs, files in os.walk(workspace):
        _add_mode(root, 0o777)
        for name in files:
            _add_mode(os.path.join(root, name), 0o666)


def _add_mode(path: str, bits: int) -> None:
    try:
        current = os.stat(path).st_mode
        wanted = current | bits
        if wanted != current:
            os.chmod(path, wanted & 0o7777)
    except OSError:  # noqa: PERF203 - a symlink or a race is not fatal
        pass


def _env_flags(env: dict[str, str]) -> list[str]:
    flags: list[str] = []
    for key, value in env.items():
        flags += ["-e", f"{key}={value}"]
    return flags


def start_sandbox(
    session_id: str,
    workspace: str,
    *,
    image: str | None = None,
    network: str | None = None,
) -> dict[str, str]:
    """Start (or restart) this session's sandbox. Raises :class:`SandboxError`.

    Returns ``{"container", "image", "image_digest"}`` for the lifecycle event.
    """
    image = image or image_name()
    network = ensure_network(network)
    ensure_proxy(network)
    name = container_name(session_id)
    remove_sandbox(session_id)
    prepare_workspace(workspace)
    started = subprocess.run(  # noqa: S603 - fixed binary, list argv, no shell
        run_argv(session_id, workspace, image=image, network=network),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=DOCKER_TIMEOUT,
    )
    if started.returncode != 0:
        raise SandboxError(
            f"could not start sandbox {name}: "
            f"{(started.stderr or started.stdout or '').strip()[:1000]}"
        )
    ready = _docker(
        "exec", "-u", SANDBOX_USER, name, "true", check=False, timeout=60
    )
    if ready.returncode != 0:
        raise SandboxError(
            f"sandbox {name} started but is not executable: "
            f"{(ready.stderr or '').strip()[:500]}"
        )
    digest = _docker("image", "inspect", "-f", "{{.Id}}", image, check=False)
    return {
        "container": name,
        "image": image,
        "image_digest": digest.stdout.strip() if digest.returncode == 0 else "",
    }


def ensure_running(session_id: str) -> str:
    """The container name, if this session's sandbox is up. Otherwise raise."""
    name = container_name(session_id)
    state = _docker("inspect", "-f", "{{.State.Running}}", name, check=False)
    if state.returncode != 0 or state.stdout.strip() != "true":
        raise SandboxError(
            f"sandbox {name} is not running; the session cannot execute commands"
        )
    return name


def remove_sandbox(session_id: str) -> bool:
    """Best effort ``docker rm -f``. Never raises: close() must always finish."""
    try:
        result = _docker(
            "rm", "-f", container_name(session_id), check=False, timeout=120
        )
    except Exception:  # noqa: BLE001 - docker gone / hung is not a close failure
        return False
    return result.returncode == 0


def list_sandboxes() -> list[str]:
    """Session ids of every sandbox container on this host, running or not."""
    result = _docker(
        "ps", "-a", "--filter", f"name=^{CONTAINER_PREFIX}",
        "--format", "{{.Names}}", check=False,
    )
    if result.returncode != 0:
        return []
    return [
        line.strip()[len(CONTAINER_PREFIX):]
        for line in result.stdout.splitlines()
        if line.strip().startswith(CONTAINER_PREFIX)
    ]


def reap_sandboxes(keep: Iterable[str]) -> list[str]:
    """Remove sandboxes whose session is gone. Returns the reaped session ids."""
    live = set(keep)
    reaped: list[str] = []
    try:
        existing = list_sandboxes()
    except Exception:  # noqa: BLE001 - startup must not die on a docker hiccup
        return reaped
    for session_id in existing:
        if session_id not in live and remove_sandbox(session_id):
            reaped.append(session_id)
    return reaped


# -- the environment ----------------------------------------------------------


class SandboxEnvironmentConfig(LocalEnvironmentConfig):
    """``LocalEnvironmentConfig`` plus the container this environment drives."""

    container: str = ""
    image: str = ""


class DockerSandboxEnvironment(LocalEnvironment):
    """``CloudLocalEnvironment``'s contract, executed inside a session sandbox.

    Same dict out of ``execute`` (``output``/``returncode``/``exception_info``,
    plus ``extra`` on failure), same ``Submitted`` propagation, same *uncapped*
    output — the 4000-character cap lives in the event emitter, not here.

    Timeouts are enforced twice: GNU ``timeout --signal=KILL`` inside the
    container kills the process tree the exec created (``pkill -g`` on the
    exec's pgid is unreliable across docker exec), and the client-side
    ``communicate`` timeout is a backstop for a wedged daemon.
    """

    def __init__(
        self,
        *,
        container: str,
        image: str = "",
        config_class: type = SandboxEnvironmentConfig,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("cwd", SANDBOX_WORKDIR)
        super().__init__(
            config_class=config_class, container=container, image=image, **kwargs
        )
        self._uname_cache: tuple[str, str, str] | None = None

    # -- credential isolation -------------------------------------------------

    def execution_env(self) -> dict[str, str]:
        env = sandbox_env()
        env |= {
            key: value
            for key, value in self.config.env.items()
            if is_allowed_env_name(key)
        }
        return env

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        system, release, machine = self._uname()
        base: dict[str, Any] = {
            **self.config.model_dump(),
            "system": system,
            "release": release,
            "machine": machine,
            "node": self.config.container,
            "version": release,
            "processor": "",
        }
        return scrub_sensitive_mapping(base | self.execution_env() | kwargs)

    # -- execution ------------------------------------------------------------

    def exec_argv(
        self, command: str, cwd: str = "", timeout: int | None = None
    ) -> list[str]:
        """``docker exec`` argv. ``command`` stays one argv element, verbatim."""
        limit = int(timeout or self.config.timeout)
        argv = [
            docker_binary(), "exec", "-i",
            "-u", SANDBOX_USER,
            "-w", cwd or self.config.cwd or SANDBOX_WORKDIR,
            *_env_flags(self.execution_env()),
            self.config.container,
            "timeout", "--signal=KILL", f"{limit}s", "bash", "-c", command,
        ]
        return argv

    def execute(
        self,
        action: dict,
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        command = action.get("command", "")
        limit = int(timeout or self.config.timeout)
        try:
            result = self._run(command, cwd, limit)
            output = {
                "output": result.stdout,
                "returncode": result.returncode,
                "exception_info": "",
            }
        except Exception as exc:  # noqa: BLE001 - mirrors CloudLocalEnvironment
            raw_output = getattr(exc, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace")
                if isinstance(raw_output, bytes)
                else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": (
                    f"An error occurred while executing the command: {exc}"
                ),
                "extra": {
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                },
            }
        self._check_finished(output)
        return output

    def _run(
        self, command: str, cwd: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        process = subprocess.Popen(  # noqa: S603 - fixed binary, list argv
            self.exec_argv(command, cwd, timeout),
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            stdout, _ = process.communicate(timeout=timeout + TIMEOUT_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            # The in-container `timeout` did not fire (wedged daemon, lost exec).
            process.kill()
            stdout, _ = process.communicate()
            self._kill_agent_processes()
            raise subprocess.TimeoutExpired(command, timeout, output=stdout) from exc
        elapsed = time.monotonic() - started
        if process.returncode in TIMEOUT_EXIT_CODES and elapsed >= timeout:
            # `timeout --signal=KILL` reports 137 (or 124); the elapsed check
            # keeps a command that genuinely exits 137 out of this branch.
            raise subprocess.TimeoutExpired(command, timeout, output=stdout)
        return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)

    def _kill_agent_processes(self) -> None:
        """Backstop cleanup: the container is single-tenant, so this is safe."""
        try:
            _docker(
                "exec", "-u", "0", self.config.container,
                "pkill", "-KILL", "-u", str(SANDBOX_UID),
                check=False, timeout=60,
            )
        except Exception:  # noqa: BLE001 - best effort
            pass

    def _uname(self) -> tuple[str, str, str]:
        """The *container's* platform, for the brief. Cached; never fatal."""
        if self._uname_cache is None:
            parts: list[str] = []
            try:
                result = _docker(
                    "exec", "-u", SANDBOX_USER, self.config.container,
                    "uname", "-s", "-r", "-m", check=False, timeout=60,
                )
                if result.returncode == 0:
                    parts = result.stdout.split()
            except Exception:  # noqa: BLE001 - fall through to the default
                parts = []
            self._uname_cache = (
                (parts[0], parts[1], parts[2])
                if len(parts) == 3
                else ("Linux", "", "x86_64")
            )
        return self._uname_cache
