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
import os
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
        cgroup_line = next(
            (
                line
                for line in Path(f"/proc/{pid_text}/cgroup")
                .read_text(encoding="ascii")
                .splitlines()
                if line.startswith("0::")
            ),
            "",
        )
        if not cgroup_line:
            raise RuntimeError("task container cgroup v2 identity unavailable")
        cgroup_root = Path("/sys/fs/cgroup").resolve()
        cgroup_path = (cgroup_root / cgroup_line[3:].lstrip("/")).resolve()
        if os.path.commonpath((cgroup_root, cgroup_path)) != str(cgroup_root):
            raise RuntimeError("task container cgroup escaped host root")
        events: dict[str, int] = {}
        for line in (cgroup_path / "memory.events").read_text(encoding="ascii").splitlines():
            name, value = line.split(maxsplit=1)
            events[name] = int(value)
        return {
            "schema": "gt.host_cgroup_snapshot.v1",
            "container_id_sha256": hashlib.sha256(container_id.encode("ascii")).hexdigest(),
            "cgroup_path_sha256": hashlib.sha256(str(cgroup_path).encode("utf-8")).hexdigest(),
            "current": self._read_cgroup_integer(cgroup_path / "memory.current"),
            "max": self._read_cgroup_integer(cgroup_path / "memory.max"),
            "peak": self._read_cgroup_integer(cgroup_path / "memory.peak"),
            "oom": events.get("oom"),
            "oom_kill": events.get("oom_kill"),
        }


# Historical import compatibility only; active workflows use the provider-neutral name.
PierDeepSeekDockerEnvironment = PierFilteredDockerEnvironment

__all__ = ["PierDeepSeekDockerEnvironment", "PierFilteredDockerEnvironment"]
