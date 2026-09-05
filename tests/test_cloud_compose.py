"""The deployment's own contract: restart policies, healthchecks, ordering.

No fakes and no daemon — the compose file *is* the artefact under test. The
audit found the codespace down twice in one session because `server` and `ui`
were `restart: no` with no healthcheck, so a daemon restart left both
`Exited (255)` and the public URL serving 502 until somebody ran
`docker compose up -d` by hand (HAR-84 G-01, H-5, G-23).

Run: ``python -m pytest tests/test_cloud_compose.py -q``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "cloud" / "docker-compose.yml"
#: services that must survive a host or daemon restart on their own
LONG_LIVED = ("server", "ui", "egress-proxy")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", LONG_LIVED)
def test_every_long_lived_service_restarts_itself(compose: dict, service: str) -> None:
    policy = compose["services"][service].get("restart")
    assert policy == "unless-stopped", (
        f"{service} is `restart: {policy}` — a daemon restart leaves it "
        "Exited (255) and nothing brings it back"
    )


@pytest.mark.parametrize("service", LONG_LIVED)
def test_every_long_lived_service_has_a_healthcheck(
    compose: dict, service: str
) -> None:
    """A *wedged* server (as opposed to an exited one) is otherwise invisible."""
    healthcheck = compose["services"][service].get("healthcheck") or {}
    assert healthcheck.get("test"), f"{service} has no healthcheck"


def test_the_server_healthcheck_probes_its_own_liveness_endpoint(
    compose: dict,
) -> None:
    test = " ".join(compose["services"]["server"]["healthcheck"]["test"])
    assert "/health" in test
    assert "127.0.0.1:8000" in test


def test_the_ui_healthcheck_probes_nginx_itself(compose: dict) -> None:
    test = " ".join(compose["services"]["ui"]["healthcheck"]["test"])
    # busybox wget ships in nginx:alpine; curl does not.
    assert "wget" in test
    assert "127.0.0.1" in test


def test_the_ui_waits_for_a_healthy_server(compose: dict) -> None:
    """The UI proxies /api: coming up first is how a fresh deploy serves 502s."""
    depends = compose["services"]["ui"]["depends_on"]
    assert depends["server"]["condition"] == "service_healthy"


def test_the_build_only_sandbox_image_is_not_a_service(compose: dict) -> None:
    """It is never started, so a restart policy on it would be a lie."""
    sandbox_image = compose["services"]["sandbox-image"]
    assert sandbox_image.get("profiles") == ["build"]
    assert "restart" not in sandbox_image


def test_the_sandbox_network_is_still_internal(compose: dict) -> None:
    """The egress policy's foundation, asserted here so a compose edit cannot
    quietly give every sandbox a route off the host."""
    network = compose["networks"]["sandbox-internal"]
    assert network["internal"] is True
    assert network["name"] == "gt-sandbox-net"
