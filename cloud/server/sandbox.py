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

import contextlib
import os
import subprocess
import time
from collections.abc import Iterable
from typing import Any

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig

from .environment import (
    InterruptGuard,
    interrupted_observation,
    is_sensitive_env_name,
    scrub_sensitive_mapping,
)

__all__ = [
    "DockerSandboxEnvironment",
    "SandboxEnvironmentConfig",
    "SandboxError",
    "container_name",
    "docker_available",
    "egress_allow_list",
    "ensure_running",
    "is_docker_mode",
    "is_exec_failure",
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

#: `docker exec` itself failed to start the process. Not the command's exit
#: code — the runtime's. See :func:`is_exec_failure`.
EXEC_FAILURE_RETURNCODE = 128
#: runc's wording when it cannot spawn the exec's helper process, which is what
#: a container out of pids looks like from outside (HAR-84 G-03)
EXEC_FAILURE_MARKERS = (
    "oci runtime exec failed",
    "unable to spawn stage-1",
    "is not running",
    "no such container",
)
#: what the agent is told when its sandbox could not be brought back
SANDBOX_UNAVAILABLE_OUTPUT = (
    "sandbox unavailable: the session container stopped responding and could "
    "not be recreated"
)
#: 128 + SIGKILL. Reported for a command the kernel killed, and for the
#: synthetic observation above, so the transcript never carries runc's text.
KILLED_RETURNCODE = 137
#: what an otherwise silent rc-137 means, since the kernel says nothing
OOM_KILLED_OUTPUT = (
    "[killed: the command hit the container memory limit "
    "(SANDBOX_MEMORY) or was killed from outside]"
)

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
        # tini as pid 1. `sleep infinity` never reaps, so a workload that
        # forks and dies leaves zombies until the container is out of pids and
        # every later `docker exec` fails with rc 128 (HAR-84 G-03).
        "--init",
        # A daemon restart stops every sandbox; without this the container is
        # `Exited (255)` forever and the session can never run again
        # (HAR-84 G-04 / D-19).
        "--restart", "unless-stopped",
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
            path = os.path.join(root, name)
            # a+rwX: every file becomes rw for everyone; a file that was
            # executable for anyone stays executable for everyone, so the
            # sandbox user can still run the repository's own scripts.
            _add_mode(path, 0o666 | (0o111 if _is_executable(path) else 0))


def _is_executable(path: str) -> bool:
    try:
        return bool(os.stat(path).st_mode & 0o111)
    except OSError:
        return False


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


def ensure_running(session_id: str, workspace: str | None = None) -> str:
    """The container name, with this session's sandbox actually up.

    A *stopped but present* container is started rather than declared dead
    (HAR-84 G-04): after a docker daemon restart every sandbox is
    ``Exited (255)`` while the workspace, the clone and the transcript are all
    still on disk, so the session only needs ``docker start``. Only a container
    that is gone, or that will not come back, is a :class:`SandboxError`.
    """
    name = container_name(session_id)
    state = _docker("inspect", "-f", "{{.State.Running}}", name, check=False)
    if state.returncode != 0:
        raise SandboxError(
            f"sandbox {name} no longer exists; the session cannot execute commands"
        )
    if state.stdout.strip() == "true":
        return name

    # Present but stopped. The bind mount's permissions are re-applied first:
    # a workspace the server re-cloned or re-indexed since may have files uid
    # 1000 cannot write.
    if workspace:
        prepare_workspace(workspace)
    started = _docker("start", name, check=False)
    if started.returncode != 0:
        raise SandboxError(
            f"sandbox {name} is stopped and would not start: "
            f"{(started.stderr or '').strip()[:500]}"
        )
    ready = _docker("exec", "-u", SANDBOX_USER, name, "true", check=False, timeout=60)
    if ready.returncode != 0:
        raise SandboxError(
            f"sandbox {name} restarted but is not executable: "
            f"{(ready.stderr or '').strip()[:500]}"
        )
    return name


def is_exec_failure(returncode: int, output: str) -> bool:
    """True when ``docker exec`` itself failed, rather than the command in it.

    Two shapes. A container out of pids answers rc **128** with runc's
    ``OCI runtime exec failed``; a container that is merely *stopped* answers
    rc **1** with nothing but the daemon's own error line. rc alone decides
    neither — ``git`` exits 128 and half the world exits 1 — so the runtime's
    wording has to be there too, and for the rc-1 shape it has to be the
    *whole* output, not something a command happened to print.
    """
    lowered = (output or "").strip().lower()
    if not lowered or returncode == 0:
        return False
    if returncode == EXEC_FAILURE_RETURNCODE:
        return any(marker in lowered for marker in EXEC_FAILURE_MARKERS)
    return lowered.startswith("error response from daemon:") and any(
        marker in lowered for marker in EXEC_FAILURE_MARKERS
    )


def _is_exec_failure_output(output: dict) -> bool:
    """:func:`is_exec_failure`, applied to an ``execute`` result dict."""
    try:
        returncode = int(output.get("returncode", 0) or 0)
    except (TypeError, ValueError):
        return False
    return is_exec_failure(returncode, str(output.get("output") or ""))


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
    #: identity of the session, so a dead container can be recreated in place
    session_id: str = ""
    #: HOST path of the workspace, for that same recreation (the daemon
    #: resolves a bind mount on the host, not inside the server container)
    workspace: str = ""


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
        on_restart: Any = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("cwd", SANDBOX_WORKDIR)
        super().__init__(
            config_class=config_class, container=container, image=image, **kwargs
        )
        self._uname_cache: tuple[str, str, str] | None = None
        #: called with the new container name after a successful recreation,
        #: so the session can publish ``lifecycle sandbox_restarted``
        self._on_restart = on_restart
        #: set once the sandbox could not be brought back. The turn ends at
        #: the next step boundary rather than running on against a dead jail.
        self.unavailable = False
        # Killing the local `docker exec` client is not enough: the process it
        # started keeps running inside the container. The guard therefore kills
        # the client *and* reaps the agent user's processes in the container.
        self._guard = InterruptGuard(on_interrupt=self._kill_agent_processes)

    # -- stop -----------------------------------------------------------------

    def interrupt(self) -> None:
        """Kill the command in flight inside the container. Never raises."""
        self._guard.request()

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
        self._guard.arm()
        try:
            output = self._execute_with_recovery(command, cwd, limit)
        finally:
            interrupted = self._guard.disarm()
        if interrupted:
            output = interrupted_observation(str(output.get("output") or ""))
        elif output.get("returncode") == KILLED_RETURNCODE and not str(
            output.get("output") or ""
        ).strip():
            # rc 137 with no text at all is what an OOM kill looks like from
            # here; without this the agent has to guess (HAR-84 G-20).
            output = {**output, "output": OOM_KILLED_OUTPUT}
        self._check_finished(output)
        return output

    def _execute_with_recovery(self, command: str, cwd: str, limit: int) -> dict:
        """Run the command; recreate the sandbox once if the *exec* failed.

        A container out of pids (or one the daemon restarted underneath us)
        answers every ``docker exec`` with runc's ``OCI runtime exec failed``
        and rc 128. That is a sandbox-health failure, not a command result:
        pasting it into the transcript as ordinary output leaves the session
        looking ``idle`` and permanently unusable (HAR-84 G-03).
        """
        output = self._execute_once(command, cwd, limit)
        if not _is_exec_failure_output(output):
            return output
        if not self._recreate():
            return self._unavailable_output()
        output = self._execute_once(command, cwd, limit)
        if _is_exec_failure_output(output):
            return self._unavailable_output()
        return output

    def _unavailable_output(self) -> dict[str, Any]:
        self.unavailable = True
        return {
            "output": SANDBOX_UNAVAILABLE_OUTPUT,
            "returncode": KILLED_RETURNCODE,
            "exception_info": SANDBOX_UNAVAILABLE_OUTPUT,
        }

    def _recreate(self) -> bool:
        """``docker rm -f`` + a fresh ``docker run`` on the same workspace."""
        session_id = str(getattr(self.config, "session_id", "") or "")
        workspace = str(getattr(self.config, "workspace", "") or "")
        if not session_id or not workspace:
            return False
        try:
            remove_sandbox(session_id)
            info = start_sandbox(session_id, workspace)
        except Exception:  # noqa: BLE001 - a failed rescue is "unavailable"
            return False
        self._uname_cache = None
        with contextlib.suppress(Exception):
            self.config.container = info["container"]
        if callable(self._on_restart):
            with contextlib.suppress(Exception):
                self._on_restart(info["container"])
        return True

    def _execute_once(self, command: str, cwd: str, limit: int) -> dict[str, Any]:
        try:
            result = self._run(command, cwd, limit)
            return {
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
            return {
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
        self._guard.adopt(process)
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
