"""Docker environment that applies Pier's filtered egress to agent traffic.

DeepSWE task files use the historical ``[agent].network_mode`` field, which
Pier 0.3.1 does not interpret.  The custom environment maps that immutable
task declaration to Pier's supported ``allow_internet=False`` environment
setting.  Pier then wires its egress proxy using the agent's explicit
``network_allowlist``; the verifier and task commands do not inherit the
agent process proxy.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from pier.environments.docker.docker import DockerEnvironment
from pier.models.task.config import EnvironmentConfig


class PierFilteredDockerEnvironment(DockerEnvironment):
    """Docker runtime with allowlisted agent egress only."""

    def __init__(self, *, task_env_config: EnvironmentConfig, **kwargs):
        # Do not mutate the task model shared with the verifier.  The model
        # runner gets filtered egress; task/verifier commands remain isolated.
        if task_env_config.allow_internet:
            task_env_config = task_env_config.model_copy(update={"allow_internet": False})
        super().__init__(task_env_config=task_env_config, **kwargs)

    # Pier puts `main` on an `internal` network and routes every byte of its
    # egress through the squid proxy, so the synthetic transport endpoint has to
    # resolve in the PROXY container, not the task container. Docker Desktop
    # publishes `host.docker.internal` to every container for free; Linux does
    # not, and `eval.pier_gt_harness_adapter` will only accept
    # `host.docker.internal` or `127.0.0.1` as that endpoint -- and 127.0.0.1
    # inside the proxy is the proxy. Without this the rehearsal cannot run
    # anywhere but a Docker Desktop workstation, which is how it came to have
    # exactly one reproducer.
    #
    # This grants no reachability of its own: squid still refuses every domain
    # the agent's `network_allowlist` does not name, so on the paid path -- whose
    # allowlist is openrouter.ai -- the alias is present and unusable.
    HOST_GATEWAY_ALIAS = "host.docker.internal:host-gateway"

    def _docker_compose_paths(self) -> list[Path]:
        paths = super()._docker_compose_paths()
        proxy_compose = getattr(self, "_egress_proxy_compose_path", None)
        if proxy_compose is None:
            # No proxy means no filtered egress to reach the host through, and
            # writing an override for a service that will not exist would fail
            # the compose merge rather than do nothing.
            return paths
        from pier.environments.agent_setup import EGRESS_PROXY_SERVICE

        override = Path(proxy_compose).with_name("docker-compose-egress-host-gateway.json")
        override.write_text(
            json.dumps(
                {"services": {EGRESS_PROXY_SERVICE: {
                    "extra_hosts": [self.HOST_GATEWAY_ALIAS]}}},
                indent=2,
            ),
            encoding="utf-8",
        )
        # Last, so it merges onto the proxy service pier just defined.
        paths.append(override)
        return paths

    @staticmethod
    def _read_cgroup_integer(path: Path) -> int | None:
        value = path.read_text(encoding="ascii").strip()
        return None if value == "max" else int(value)

    async def agent_resource_snapshot(self) -> dict[str, object]:
        """Read the task container's cgroup from the host, never from task code."""
        container = await self._run_docker_compose_command(["ps", "-q", "main"])
        container_id = str(container.stdout or "").strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise RuntimeError("task container identity unavailable")
        process = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format",
            "{{.State.Pid}}",
            container_id,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        if process.returncode != 0:
            raise RuntimeError("task container PID unavailable")
        pid_text = stdout.decode("ascii", errors="strict").strip()
        if not pid_text.isdigit() or int(pid_text) <= 0:
            raise RuntimeError("task container PID invalid")
        from gt_harness.cgroup import memory_snapshot

        return {
            "schema": "gt.host_cgroup_snapshot.v1",
            "container_id_sha256": hashlib.sha256(container_id.encode("ascii")).hexdigest(),
            **memory_snapshot(int(pid_text)),
        }


# Historical import compatibility only; active workflows use the provider-neutral name.
PierDeepSeekDockerEnvironment = PierFilteredDockerEnvironment

__all__ = ["PierDeepSeekDockerEnvironment", "PierFilteredDockerEnvironment"]
