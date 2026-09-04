"""GitHub OAuth authentication for the cloud coding agent."""
from __future__ import annotations

import os
import secrets
import time
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, Cookie, HTTPException, Response
from fastapi.responses import RedirectResponse

auth_router = APIRouter(prefix="/auth", tags=["auth"])

_GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
_GITHUB_USER = "https://api.github.com/user"

_pending_states: dict[str, float] = {}


def _client_id() -> str:
    return os.environ.get("GITHUB_CLIENT_ID", "")


def _client_secret() -> str:
    return os.environ.get("GITHUB_CLIENT_SECRET", "")


def _jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "dev-secret-change-me")


def _allowed_logins() -> set[str] | None:
    raw = os.environ.get("ALLOWED_GITHUB_LOGINS", "").strip()
    if not raw:
        return None
    return {login.strip() for login in raw.split(",") if login.strip()}


@auth_router.get("/login")
async def login() -> RedirectResponse:
    if not _client_id():
        raise HTTPException(500, "GITHUB_CLIENT_ID not configured")
    state = secrets.token_urlsafe(32)
    _pending_states[state] = time.time()
    _cleanup_stale_states()
    url = (
        f"{_GITHUB_AUTHORIZE}?client_id={_client_id()}"
        f"&state={state}&scope=read:user"
    )
    return RedirectResponse(url)


@auth_router.get("/callback")
async def callback(code: str, state: str) -> RedirectResponse:
    if state not in _pending_states:
        raise HTTPException(400, "invalid or expired state")
    del _pending_states[state]

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            _GITHUB_TOKEN,
            json={
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(400, f"GitHub token exchange failed: {token_data.get('error', 'unknown')}")

        user_resp = await client.get(
            _GITHUB_USER,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_data = user_resp.json()

    login = user_data.get("login", "")
    allowed = _allowed_logins()
    if allowed and login not in allowed:
        raise HTTPException(403, f"user {login} not in ALLOWED_GITHUB_LOGINS")

    app_jwt = jwt.encode(
        {
            "sub": str(user_data.get("id", "")),
            "login": login,
            "name": user_data.get("name", ""),
            "avatar_url": user_data.get("avatar_url", ""),
            "exp": int(time.time()) + 86400 * 7,
        },
        _jwt_secret(),
        algorithm="HS256",
    )

    response = RedirectResponse("/")
    response.set_cookie(
        "session",
        app_jwt,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,
    )
    return response


@auth_router.get("/me")
async def me(session: str | None = Cookie(None)) -> dict[str, Any]:
    user = verify_jwt(session)
    return user


@auth_router.post("/logout")
async def logout() -> Response:
    response = Response(status_code=200)
    response.delete_cookie("session")
    return response


def verify_jwt(token: str | None) -> dict[str, Any]:
    if not token:
        raise HTTPException(401, "not authenticated")
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid session")


def _cleanup_stale_states() -> None:
    cutoff = time.time() - 600
    stale = [k for k, v in _pending_states.items() if v < cutoff]
    for k in stale:
        del _pending_states[k]
