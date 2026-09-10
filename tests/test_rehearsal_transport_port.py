"""The rehearsal's transport port has to be one Pier's proxy will carry.

The agent never talks to the host directly. Pier puts it on an `internal`
network and every request goes through a squid proxy whose configuration Pier
generates itself (`pier.environments.agent_setup.squid_bootstrap_command`).
That configuration contains:

    acl Safe_ports port 80 443
    http_access deny !Safe_ports

So the synthetic transport is reachable on port 80 or 443 and on nothing else,
whatever the allowlist says about the hostname.

Measured, not deduced: run 34500788937 pointed the transport at port 8080 --
chosen because an unprivileged runner process cannot bind below 1024 -- and
every provider call came back as a squid error page with
`transport_requests: 0` on the server that never saw them. Nothing was wrong
with DNS; the workflow's own resolution probe passed in the same run. The port
was simply not one squid would proxy.

That is why this is a test and not a comment. The failure surfaces as
`litellm.APIError` inside the agent, three layers from the cause, and the
obvious next move -- pick another high port -- fails identically.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
pier_setup = pytest.importorskip(
    "pier.environments.agent_setup", reason="datacurve-pier is not installed")

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/installed_rehearsal.yml"


def _safe_ports() -> set[int]:
    """Read the ACL out of Pier's own generated config, never a copy of it."""
    config = pier_setup.squid_bootstrap_command()
    match = re.search(r"^acl\s+Safe_ports\s+port\s+(.+)$", config, re.MULTILINE)
    assert match, "pier's squid config no longer declares Safe_ports"
    return {int(token) for token in match.group(1).split()}


def _workflow_env() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]["rehearsal"]["env"]


@pytest.mark.skipif(not WORKFLOW.is_file(), reason="rehearsal workflow not present")
def test_the_rehearsal_port_is_one_the_proxy_will_carry():
    port = int(_workflow_env()["GT_REHEARSAL_PORT"])
    safe = _safe_ports()
    assert port in safe, (
        f"the rehearsal serves its synthetic transport on {port}, which pier's "
        f"squid proxy denies: Safe_ports is {sorted(safe)}. Every provider call "
        f"returns a squid error page and the transport records zero requests."
    )


@pytest.mark.skipif(not WORKFLOW.is_file(), reason="rehearsal workflow not present")
def test_the_workflow_can_actually_bind_the_port_it_chose():
    """A Safe_port below 1024 is unbindable by default; say so where it is set.

    The two constraints pull opposite ways -- squid allows only 80 and 443,
    and Linux reserves everything under 1024 for root -- so satisfying one by
    itself silently breaks the other. The workflow has to lower
    `ip_unprivileged_port_start`, and this fails if that step goes away.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    port = int(_workflow_env()["GT_REHEARSAL_PORT"])
    if port >= 1024:
        pytest.skip("an unprivileged process can bind this port unaided")
    assert "ip_unprivileged_port_start" in text, (
        f"port {port} is below 1024 and the workflow never lowers "
        "net.ipv4.ip_unprivileged_port_start, so the transport cannot bind"
    )


@pytest.mark.skipif(not WORKFLOW.is_file(), reason="rehearsal workflow not present")
def test_the_transport_url_names_the_port_the_server_listens_on():
    """One port, set once. Two places that must agree is one place to drift."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '--port "$GT_REHEARSAL_PORT"' in text
    assert 'host.docker.internal:${GT_REHEARSAL_PORT}/v1' in text
