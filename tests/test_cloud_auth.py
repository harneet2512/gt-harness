"""Tests for cloud.server.auth — GitHub OAuth and JWT."""
from __future__ import annotations

import os
import time

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from cloud.server.auth import auth_router, jwt_ttl_seconds, require_user, verify_jwt


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("ALLOWED_GITHUB_LOGINS", "")


def test_login_redirects_to_github(client: TestClient) -> None:
    resp = client.get("/auth/login")
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert "github.com/login/oauth/authorize" in location
    assert "client_id=test-client-id" in location
    assert "scope=read:user" in location
    assert "state=" in location


def test_login_fails_without_client_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "")
    resp = client.get("/auth/login")
    assert resp.status_code == 500


def test_callback_rejects_invalid_state(client: TestClient) -> None:
    resp = client.get("/auth/callback?code=test-code&state=bogus-state")
    assert resp.status_code == 400
    assert "invalid" in resp.json()["detail"].lower()


def test_verify_jwt_valid() -> None:
    token = jwt.encode(
        {"sub": "123", "login": "testuser", "exp": int(time.time()) + 3600},
        "test-jwt-secret",
        algorithm="HS256",
    )
    os.environ["JWT_SECRET"] = "test-jwt-secret"
    payload = verify_jwt(token)
    assert payload["sub"] == "123"
    assert payload["login"] == "testuser"


def test_verify_jwt_expired() -> None:
    from fastapi import HTTPException

    token = jwt.encode(
        {"sub": "123", "login": "testuser", "exp": int(time.time()) - 3600},
        "test-jwt-secret",
        algorithm="HS256",
    )
    os.environ["JWT_SECRET"] = "test-jwt-secret"
    with pytest.raises(HTTPException) as exc_info:
        verify_jwt(token)
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_verify_jwt_none_token() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        verify_jwt(None)
    assert exc_info.value.status_code == 401


def test_verify_jwt_invalid_token() -> None:
    from fastapi import HTTPException

    os.environ["JWT_SECRET"] = "test-jwt-secret"
    with pytest.raises(HTTPException) as exc_info:
        verify_jwt("not-a-real-jwt")
    assert exc_info.value.status_code == 401


def test_me_returns_user_data(client: TestClient) -> None:
    token = jwt.encode(
        {
            "sub": "42",
            "login": "devuser",
            "name": "Dev User",
            "exp": int(time.time()) + 3600,
        },
        "test-jwt-secret",
        algorithm="HS256",
    )
    resp = client.get("/auth/me", cookies={"session": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["login"] == "devuser"
    assert data["sub"] == "42"


def test_me_rejects_missing_cookie(client: TestClient) -> None:
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_logout_clears_cookie(client: TestClient) -> None:
    resp = client.post("/auth/logout")
    assert resp.status_code == 200
    cookie_header = resp.headers.get("set-cookie", "")
    assert "session=" in cookie_header


# --------------------------------------------------------------------------
# HAR-84 G-10: the allow-list is enforced on every request, not once at login
# --------------------------------------------------------------------------
def _token(login: str, secret: str = "test-jwt-secret", ttl: int = 3600) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": "1234", "login": login, "iat": now, "exp": now + ttl},
        secret,
        algorithm="HS256",
    )


@pytest.mark.anyio
async def test_a_valid_jwt_for_an_unlisted_login_is_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signed with JWT_SECRET, arbitrary `sub`, login never allow-listed.

    It used to read and write every session in the deployment.
    """
    monkeypatch.setenv("ALLOWED_GITHUB_LOGINS", "harneet2512, someone-else")
    with pytest.raises(HTTPException) as caught:
        await require_user(session=None, authorization=f"Bearer {_token('eve')}")
    assert caught.value.status_code == 403


@pytest.mark.anyio
async def test_an_allow_listed_login_still_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_LOGINS", "harneet2512, someone-else")
    user = await require_user(
        session=None, authorization=f"Bearer {_token('harneet2512')}"
    )
    assert user["login"] == "harneet2512"


@pytest.mark.anyio
async def test_no_allow_list_means_any_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_LOGINS", "")
    user = await require_user(session=None, authorization=f"Bearer {_token('anyone')}")
    assert user["login"] == "anyone"


@pytest.mark.anyio
async def test_a_forged_signature_is_still_401_before_the_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_LOGINS", "harneet2512")
    forged = _token("harneet2512", secret="not-the-secret")
    with pytest.raises(HTTPException) as caught:
        await require_user(session=None, authorization=f"Bearer {forged}")
    assert caught.value.status_code == 401


def test_the_default_token_lifetime_is_a_day_not_a_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JWT_TTL_SECONDS", raising=False)
    assert jwt_ttl_seconds() == 86400


def test_the_token_lifetime_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_TTL_SECONDS", "3600")
    assert jwt_ttl_seconds() == 3600
    monkeypatch.setenv("JWT_TTL_SECONDS", "nonsense")
    assert jwt_ttl_seconds() == 86400
