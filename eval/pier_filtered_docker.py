"""Docker environment that applies Pier's filtered egress to agent traffic.

DeepSWE task files use the historical ``[agent].network_mode`` field, which
Pier 0.3.1 does not interpret.  The custom environment maps that immutable
task declaration to Pier's supported ``allow_internet=False`` environment
setting.  Pier then wires its egress proxy using the agent's explicit
``network_allowlist``; the verifier and task commands do not inherit the
agent process proxy.
"""

from __future__ import annotations

from pier.environments.docker.docker import DockerEnvironment
from pier.models.task.config import EnvironmentConfig


class PierFilteredDockerEnvironment(DockerEnvironment):
    """Docker runtime with allowlisted agent egress only."""

    def __init__(self, *, task_env_config: EnvironmentConfig, **kwargs):
        # Do not mutate the task model shared with the verifier.  The model
        # runner gets filtered egress; task/verifier commands remain isolated.
        if task_env_config.allow_internet:
            task_env_config = task_env_config.model_copy(
                update={"allow_internet": False}
            )
        super().__init__(task_env_config=task_env_config, **kwargs)


# Historical import compatibility only; active workflows use the provider-neutral name.
PierDeepSeekDockerEnvironment = PierFilteredDockerEnvironment

__all__ = ["PierDeepSeekDockerEnvironment", "PierFilteredDockerEnvironment"]
