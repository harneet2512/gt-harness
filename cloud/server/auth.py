"""GitHub OAuth authentication for the cloud coding agent."""
from __future__ import annotations

import os
import secrets
import time
from typing import Annotated, Any

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from fastapi.responses import RedirectResponse

auth_router = APIRouter(prefix="/auth", tags=["auth"])

_GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
_GITHUB_USER = "https://api.github.com/user"

_pending_states: dict[str, float] = {}

#: How long a signed-in session lasts. Was 7 days, which is a long time for a
#: token that cannot be revoked: removing somebody from
#: ``ALLOWED_GITHUB_LOGINS`` did nothing until it expired (HAR-84 G-10).
DEFAULT_JWT_TTL_SECONDS = 86400


def jwt_ttl_seconds() -> int:
    try:
        return max(60, int(os.environ.get("JWT_TTL_SECONDS", DEFAULT_JWT_TTL_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_JWT_TTL_SECONDS


def _client_id() -> str:
    return os.environ.get("GITHUB_CLIENT_ID", "")


def _client_secret() -> str:
    return os.environ.get("GITHUB_CLIENT_SECRET", "")


def _jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "dev-secret-change-me")


def _ui_origin() -> str:
    return os.environ.get("UI_ORIGIN", "").strip() or "/"


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

    ttl = jwt_ttl_seconds()
    issued_at = int(time.time())
    app_jwt = jwt.encode(
        {
            "sub": str(user_data.get("id", "")),
            "login": login,
            "name": user_data.get("name", ""),
            "avatar_url": user_data.get("avatar_url", ""),
            "iat": issued_at,
            "exp": issued_at + ttl,
        },
        _jwt_secret(),
        algorithm="HS256",
    )

    response = RedirectResponse(_ui_origin())
    response.set_cookie(
        "session",
        app_jwt,
        httponly=True,
        samesite="lax",
        max_age=ttl,
    )
    return response


def bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <jwt>`` header."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


async def require_user(
    session: str | None = Cookie(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Auth dependency for every /api route: bearer header or session cookie.

    There is no "auth disabled" mode — a request with neither credential is 401.
    The allow-list is re-checked on **every** request, not only at
    ``/auth/callback``: a token signed with ``JWT_SECRET`` for a login that was
    never allowed (or has since been removed) used to read and write every
    session in the deployment (HAR-84 G-10).
    """
    user = verify_jwt(bearer_token(authorization) or session)
    allowed = _allowed_logins()
    if allowed is not None and str(user.get("login") or "") not in allowed:
        raise HTTPException(403, "user is not allowed to use this deployment")
    return user


@auth_router.get("/me")
async def me(user: Annotated[dict[str, Any], Depends(require_user)]) -> dict[str, Any]:
    """Who am I — accepts the same credentials as every /api route."""
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
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(401, "session expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "invalid session") from exc


def _cleanup_stale_states() -> None:
    cutoff = time.time() - 600
    stale = [k for k, v in _pending_states.items() if v < cutoff]
    for k in stale:
        del _pending_states[k]
