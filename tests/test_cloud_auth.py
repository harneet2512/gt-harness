"""Tests for cloud.server.auth — GitHub OAuth and JWT."""
from __future__ import annotations

import os
import time

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from cloud.server import auth as auth_module
from cloud.server import link_token
from cloud.server.auth import (
    auth_router,
    issue_ingest_token,
    issue_link_token,
    jwt_ttl_seconds,
    require_ingest,
    require_user,
    verify_jwt,
)


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
    # The sign-in expired, not the workspace: "session expired" reads like a
    # session the reaper collected, which is a different thing entirely.
    assert exc_info.value.detail == "sign-in expired; sign in again"


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


# --------------------------------------------------------------------------
# operator sign-in links (host-independent front door)
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _forget_redeemed_links():
    """Redeemed ``jti``s are process state; no test may inherit another's."""
    auth_module._redeemed_links.clear()
    yield
    auth_module._redeemed_links.clear()


def _link(client: TestClient, token: str):
    return client.get(f"/auth/link?t={token}")


def _mint(login: str = "tester", **claims) -> str:
    """A link token, with individual claims overridden for the bad cases."""
    issued_at = int(time.time())
    payload = {
        "login": login,
        "scope": "link",
        "jti": f"jti-{issued_at}-{login}",
        "iat": issued_at,
        "exp": issued_at + 600,
    }
    payload.update(claims)
    return jwt.encode(payload, "test-jwt-secret", algorithm="HS256")


def test_a_minted_link_signs_you_in(client: TestClient) -> None:
    """The door that does not depend on the hostname."""
    token = issue_link_token("tester")

    resp = _link(client, token)

    assert resp.status_code == 307
    assert resp.headers["location"] == "/"
    cookie = resp.cookies.get("session")
    assert cookie, "the link set the same cookie /auth/callback sets"
    claims = jwt.decode(cookie, "test-jwt-secret", algorithms=["HS256"])
    assert claims["login"] == "tester"
    # the cookie is a SIGN-IN, so it carries no scope for require_user to refuse
    assert "scope" not in claims


def test_the_cookie_a_link_sets_is_accepted_by_require_user(
    client: TestClient,
) -> None:
    resp = _link(client, issue_link_token("tester"))

    me = client.get("/auth/me", cookies={"session": resp.cookies["session"]})

    assert me.status_code == 200, me.text
    assert me.json()["login"] == "tester"


def test_a_link_is_single_use(client: TestClient) -> None:
    """It will end up in a chat log; a replay is a refusal, not a session."""
    token = issue_link_token("tester")
    assert _link(client, token).status_code == 307

    replay = _link(client, token)

    assert replay.status_code == 400, replay.text
    assert "already been used" in replay.json()["detail"]


def test_an_expired_link_is_refused(client: TestClient) -> None:
    issued_at = int(time.time()) - 3600
    token = _mint(iat=issued_at, exp=issued_at + 600)

    resp = _link(client, token)

    assert resp.status_code == 401, resp.text


def test_a_link_that_lives_too_long_is_refused(client: TestClient) -> None:
    """The ceiling is enforced at redemption, not only at minting."""
    issued_at = int(time.time())
    token = _mint(iat=issued_at, exp=issued_at + 86_400)

    resp = _link(client, token)

    assert resp.status_code == 400, resp.text
    assert "too long" in resp.json()["detail"]
    assert auth_module.link_ttl_seconds() <= auth_module.MAX_LINK_TTL_SECONDS


def test_a_link_for_an_unlisted_login_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link minted last week for somebody since removed is worth nothing."""
    token = issue_link_token("stranger")
    monkeypatch.setenv("ALLOWED_GITHUB_LOGINS", "tester,someone-else")

    resp = _link(client, token)

    assert resp.status_code == 403, resp.text
    assert "ALLOWED_GITHUB_LOGINS" in resp.json()["detail"]
    # ...and refusing it did not burn the jti, but nothing was signed in either
    assert "session" not in resp.cookies


def test_a_session_or_ingest_token_is_not_a_sign_in_link(
    client: TestClient,
) -> None:
    session_token = jwt.encode(
        {"sub": "1", "login": "tester", "exp": int(time.time()) + 3600},
        "test-jwt-secret",
        algorithm="HS256",
    )
    ingest_token = issue_ingest_token("agent1", "session1")

    assert _link(client, session_token).status_code == 400
    assert _link(client, ingest_token).status_code == 400
    assert "not a sign-in link" in _link(client, ingest_token).json()["detail"]


def test_a_link_without_a_jti_cannot_be_redeemed(client: TestClient) -> None:
    """No jti, no single-use guarantee — so no sign-in."""
    resp = _link(client, _mint(jti=""))

    assert resp.status_code == 400, resp.text
    assert "single use" in resp.json()["detail"]


@pytest.mark.anyio
async def test_a_link_token_cannot_be_used_as_a_credential() -> None:
    """The other direction: redeeming a link makes a session, it is not one."""
    token = issue_link_token("tester")

    with pytest.raises(HTTPException) as user_exc:
        await require_user(session=None, authorization=f"Bearer {token}")
    with pytest.raises(HTTPException) as ingest_exc:
        await require_ingest("agent1", authorization=f"Bearer {token}")

    assert user_exc.value.status_code == 401
    assert "link token" in user_exc.value.detail
    assert ingest_exc.value.status_code == 401


def test_redeemed_link_ids_do_not_accumulate_forever(client: TestClient) -> None:
    """Same discipline as _pending_states: the set is swept, not grown."""
    assert _link(client, issue_link_token("tester")).status_code == 307
    assert len(auth_module._redeemed_links) == 1
    for jti in list(auth_module._redeemed_links):
        auth_module._redeemed_links[jti] = (
            time.time() - auth_module.MAX_LINK_TTL_SECONDS - 1
        )

    assert _link(client, issue_link_token("tester")).status_code == 307

    assert len(auth_module._redeemed_links) == 1, "the stale id was swept"


def test_the_operator_entry_point_prints_the_url_and_nothing_else(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gt.example.test/")

    assert link_token.main(["tester"]) == 0

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1, captured.out
    url = lines[0]
    assert url.startswith("https://gt.example.test/auth/link?t=")
    # the secret never leaves the process, in either stream
    assert "test-jwt-secret" not in captured.out
    assert "test-jwt-secret" not in captured.err
    claims = jwt.decode(url.split("t=", 1)[1], "test-jwt-secret", algorithms=["HS256"])
    assert claims["login"] == "tester" and claims["scope"] == "link"


def test_the_operator_entry_point_refuses_a_missing_login(capsys) -> None:
    assert link_token.main([]) == 2
    assert link_token.main(["   "]) == 2
    assert capsys.readouterr().out == "", "usage goes to stderr, not stdout"
