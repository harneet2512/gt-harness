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
#: ``jti`` of every sign-in link redeemed recently -> when. In memory, like
#: ``_pending_states``, and swept on the same discipline (see
#: ``_cleanup_redeemed_links``): a link is single use, and the set of used
#: ones must not grow without bound.
_redeemed_links: dict[str, float] = {}

#: How long a signed-in session lasts. Was 7 days, which is a long time for a
#: token that cannot be revoked: removing somebody from
#: ``ALLOWED_GITHUB_LOGINS`` did nothing until it expired (HAR-84 G-10).
DEFAULT_JWT_TTL_SECONDS = 86400

#: ``scope`` claim of an EXTERNAL AGENT's ingest token. A token carrying it is
#: accepted by the two ingest endpoints and by nothing else; a user token
#: (which carries no ``scope`` at all) is refused BY them. The two credentials
#: are signed with the same secret, so the scope check is the whole boundary:
#: without it, handing an adapter a token to push events would have handed it
#: every session in the deployment.
INGEST_SCOPE = "ingest"
#: ``scope`` claim of an operator-minted SIGN-IN LINK. Also refused by
#: ``require_user``: redeeming one produces the session cookie, it is not one.
LINK_SCOPE = "link"
#: scopes that are credentials for a machine, not for a person. A token
#: carrying any of them is refused everywhere a *user* is required.
_NON_USER_SCOPES = frozenset({INGEST_SCOPE, LINK_SCOPE})
#: how long a sign-in link lasts by default, and the ceiling it is clamped to.
#: It is a bearer URL that will end up in a chat message or a shell history,
#: so minutes, not hours — and the ceiling is enforced at redemption too.
DEFAULT_LINK_TTL_SECONDS = 600
MAX_LINK_TTL_SECONDS = 900
#: an external agent may be pushing events for days; a local Claude Code
#: session that has to re-register every hour is a worse deal than a token
#: that outlives it. Not stored anywhere — it is a stateless JWT.
DEFAULT_INGEST_TTL_SECONDS = 7 * 86400


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

    response = RedirectResponse(_ui_origin())
    _set_session_cookie(
        response,
        login=login,
        sub=str(user_data.get("id", "")),
        name=str(user_data.get("name", "") or ""),
        avatar_url=str(user_data.get("avatar_url", "") or ""),
    )
    return response


@auth_router.get("/link")
async def link(t: str) -> RedirectResponse:
    """Sign in with an operator-minted link, without touching the hostname.

    The OAuth round trip is pinned to ONE registered callback host. When that
    host stops resolving — a Codespaces tunnel id that changes on every cold
    start is the case that forced this — the deployment is unreachable through
    the front door while the server itself is perfectly healthy. This is the
    side door: a short-lived, single-use, signed link that only somebody
    holding ``JWT_SECRET`` can mint (see ``python -m cloud.server.link_token``).

    It is deliberately as narrow as a door can be: at most
    :data:`MAX_LINK_TTL_SECONDS`, one redemption per ``jti``, and the SAME
    allow-list check the OAuth callback does — enforced HERE, at redemption,
    because a link minted last week for somebody since removed must not work.
    """
    claims = verify_jwt(t)
    if str(claims.get("scope") or "") != LINK_SCOPE:
        # A session cookie or an ingest token is not a sign-in link. Refusing
        # them here is the other half of `require_user` refusing link tokens:
        # one secret signs all three, so the scope is the whole separation.
        raise HTTPException(400, "not a sign-in link")
    login = str(claims.get("login") or "")
    if not login:
        raise HTTPException(400, "sign-in link names no user")
    issued_at = int(claims.get("iat") or 0)
    expires_at = int(claims.get("exp") or 0)
    if expires_at - issued_at > MAX_LINK_TTL_SECONDS:
        # Re-checked at redemption, not only at minting: the ceiling is the
        # property that makes a leaked link survivable, so it is enforced by
        # the side that is under attack.
        raise HTTPException(400, "sign-in link lives too long")
    jti = str(claims.get("jti") or "")
    if not jti:
        raise HTTPException(400, "sign-in link is not single use")
    _cleanup_redeemed_links()
    if jti in _redeemed_links:
        # A replay is a refusal, never a second session: the link may well be
        # sitting in a chat log or a shell history by now.
        raise HTTPException(400, "sign-in link has already been used")
    allowed = _allowed_logins()
    if allowed is not None and login not in allowed:
        raise HTTPException(403, f"user {login} not in ALLOWED_GITHUB_LOGINS")
    _redeemed_links[jti] = time.time()

    response = RedirectResponse(_ui_origin())
    _set_session_cookie(response, login=login, name=login)
    return response


def _set_session_cookie(
    response: Response,
    *,
    login: str,
    sub: str = "",
    name: str = "",
    avatar_url: str = "",
) -> None:
    """Mint the sign-in cookie. One place, so both doors set the same one."""
    ttl = jwt_ttl_seconds()
    issued_at = int(time.time())
    app_jwt = jwt.encode(
        {
            "sub": sub or login,
            "login": login,
            "name": name,
            "avatar_url": avatar_url,
            "iat": issued_at,
            "exp": issued_at + ttl,
        },
        _jwt_secret(),
        algorithm="HS256",
    )
    response.set_cookie(
        "session",
        app_jwt,
        httponly=True,
        samesite="lax",
        max_age=ttl,
    )


def link_ttl_seconds() -> int:
    """How long a minted link lasts. Clamped: this is a bearer URL."""
    try:
        requested = int(os.environ.get("LINK_TTL_SECONDS", DEFAULT_LINK_TTL_SECONDS))
    except (TypeError, ValueError):
        requested = DEFAULT_LINK_TTL_SECONDS
    return max(60, min(MAX_LINK_TTL_SECONDS, requested))


def issue_link_token(login: str) -> str:
    """A single-use sign-in link token for ``login``. Operator use only."""
    if not login.strip():
        raise ValueError("login must not be blank")
    issued_at = int(time.time())
    return jwt.encode(
        {
            "login": login.strip(),
            "scope": LINK_SCOPE,
            # what makes it single use: the server remembers redeemed ids
            "jti": secrets.token_urlsafe(16),
            "iat": issued_at,
            "exp": issued_at + link_ttl_seconds(),
        },
        _jwt_secret(),
        algorithm="HS256",
    )


def _cleanup_redeemed_links() -> None:
    """Forget redemptions older than any link could still be valid for.

    The same discipline as ``_pending_states``: the set must not grow without
    bound, and a ``jti`` past the maximum TTL cannot be replayed anyway —
    ``verify_jwt`` rejects the token itself before we get here.
    """
    cutoff = time.time() - MAX_LINK_TTL_SECONDS
    for jti in [k for k, v in _redeemed_links.items() if v < cutoff]:
        del _redeemed_links[jti]


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
    scope = str(user.get("scope") or "")
    if scope in _NON_USER_SCOPES:
        # An ingest token proves an external agent is talking, not that a
        # person is; a link token is something you REDEEM for a sign-in, not
        # a sign-in. Neither may read or write sessions.
        raise HTTPException(401, f"an {scope} token cannot be used to sign in")
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


def ingest_ttl_seconds() -> int:
    try:
        return max(
            60, int(os.environ.get("INGEST_TTL_SECONDS", DEFAULT_INGEST_TTL_SECONDS))
        )
    except (TypeError, ValueError):
        return DEFAULT_INGEST_TTL_SECONDS


def issue_ingest_token(agent_id: str, session_id: str) -> str:
    """A stateless bearer token for one external agent's event stream.

    ``aid`` is re-checked against the path on every call, so a token for agent
    A cannot push events into agent B even though both are signed with the
    same secret.
    """
    issued_at = int(time.time())
    return jwt.encode(
        {
            "aid": agent_id,
            "sid": session_id,
            "scope": INGEST_SCOPE,
            "iat": issued_at,
            "exp": issued_at + ingest_ttl_seconds(),
        },
        _jwt_secret(),
        algorithm="HS256",
    )


async def require_ingest(
    agent_id: str, authorization: str | None = Header(None)
) -> dict[str, Any]:
    """Auth for the external-agent ingest routes: the ingest token, only.

    Deliberately NOT the cookie: an adapter posts a header, and accepting the
    browser's session cookie here would make every ingest endpoint reachable
    by a cross-site form post from a signed-in tab.
    """
    claims = verify_jwt(bearer_token(authorization))
    if str(claims.get("scope") or "") != INGEST_SCOPE:
        raise HTTPException(401, "an ingest token is required")
    if str(claims.get("aid") or "") != agent_id:
        raise HTTPException(401, "this ingest token is for a different agent")
    return claims


def verify_jwt(token: str | None) -> dict[str, Any]:
    if not token:
        raise HTTPException(401, "not authenticated")
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError as exc:
        # Not "session expired": a *session* here is a workspace, and the
        # reaper collects those. What has run out is the sign-in, and the
        # UI shows this string (HAR-84 P2-7).
        raise HTTPException(401, "sign-in expired; sign in again") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "invalid session") from exc


def _cleanup_stale_states() -> None:
    cutoff = time.time() - 600
    stale = [k for k, v in _pending_states.items() if v < cutoff]
    for k in stale:
        del _pending_states[k]
