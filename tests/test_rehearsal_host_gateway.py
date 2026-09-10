"""The synthetic transport has to be reachable from somewhere other than one laptop.

The installed full-flow rehearsal serves its own provider in-process and points
the agent at it. `eval.pier_gt_harness_adapter.network_allowlist` accepts only
`host.docker.internal` or `127.0.0.1` for that endpoint, and Pier puts the task
container on an `internal` network whose egress all goes through a squid proxy
-- so the name has to resolve in the PROXY container, and `127.0.0.1` there is
the proxy itself.

Docker Desktop publishes `host.docker.internal` to every container. Linux does
not. That single missing name is why the rehearsal had exactly one reproducer,
on one Windows workstation, through a 9p mount that deadlocked two runs in
`p9_client_rpc` with nothing about the rehearsal changed.

The failure mode this file exists for is that the override goes missing and the
rehearsal fails on DNS in CI, which looks identical to the rehearsal failing on
its subject. It is checked here rather than only in the workflow because a
workflow proves it for one run of one branch; this proves it for the code.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pier = pytest.importorskip("pier", reason="datacurve-pier is not installed")

from eval.pier_filtered_docker import PierFilteredDockerEnvironment  # noqa: E402


class _Stub(PierFilteredDockerEnvironment):
    """Pier's __init__ needs a task on disk; only the override is under test.

    This subclasses the REAL class and sets only the two attributes the
    override reads, so `super()._docker_compose_paths` resolves through the
    genuine descriptor. An earlier version of these tests defined its own
    method and called it directly. Every assertion passed while the shipped
    code was broken: the override was written as a method over Pier's
    PROPERTY, and Pier's `for path in self._docker_compose_paths` got a bound
    method and killed every trial in setup with `TypeError: 'method' object is
    not iterable`. The stub has to touch the descriptor or it is testing
    itself.
    """

    def __init__(self, proxy_compose: Path | None) -> None:
        self._egress_proxy_compose_path = proxy_compose


@pytest.fixture
def base_paths(monkeypatch):
    """Stand in for DockerEnvironment's own property, kept a property."""
    paths = [Path("base.yaml")]
    monkeypatch.setattr(
        "pier.environments.docker.docker.DockerEnvironment._docker_compose_paths",
        property(lambda self: list(paths)),
        raising=True,
    )
    return paths


def test_the_override_is_a_property_like_the_one_it_replaces():
    """The defect the first version of this file could not see.

    Checked on the class, before any instance exists, because the symptom
    appears deep inside Pier's setup and reads as an environment fault.
    """
    from pier.environments.docker.docker import DockerEnvironment

    assert isinstance(
        PierFilteredDockerEnvironment.__dict__["_docker_compose_paths"], property)
    assert isinstance(
        DockerEnvironment.__dict__["_docker_compose_paths"], property)


def test_the_proxy_is_given_a_route_to_the_host(tmp_path, base_paths):
    from pier.environments.agent_setup import EGRESS_PROXY_SERVICE

    proxy = tmp_path / "docker-compose-egress-proxy.json"
    proxy.write_text("{}", encoding="utf-8")

    paths = _Stub(proxy)._docker_compose_paths

    override = Path(paths[-1])
    assert override.name == "docker-compose-egress-host-gateway.json"
    # Last, or it does not merge onto the proxy service pier defined.
    assert len(paths) == 2
    service = json.loads(override.read_text(encoding="utf-8"))["services"][EGRESS_PROXY_SERVICE]
    assert service["extra_hosts"] == ["host.docker.internal:host-gateway"]


def test_the_override_names_the_service_pier_actually_creates(tmp_path, base_paths):
    """A hand-typed service name would merge into a second, unused service.

    Compose does not object to an override for a service nobody declared -- it
    creates one. The rehearsal would then start a stray container and the proxy
    would still have no route to the host, which is a silent version of the
    exact failure this override exists to prevent.
    """
    from pier.environments.agent_setup import EGRESS_PROXY_SERVICE

    proxy = tmp_path / "docker-compose-egress-proxy.json"
    proxy.write_text("{}", encoding="utf-8")
    override = json.loads(
        Path(_Stub(proxy)._docker_compose_paths[-1]).read_text(encoding="utf-8"))

    assert set(override["services"]) == {EGRESS_PROXY_SERVICE}


def test_a_task_with_no_filtered_egress_gets_no_override(base_paths):
    """No proxy, no service to merge onto.

    Compose fails a merge onto a service that does not exist in any earlier
    file, so writing this unconditionally would break every unfiltered task
    rather than harmlessly do nothing.
    """
    assert _Stub(None)._docker_compose_paths == [Path("base.yaml")]
