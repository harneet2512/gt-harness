"""Tests for cloud.server.auth — GitHub OAuth and JWT."""
from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.server.auth import _pending_states, auth_router, verify_jwt


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
