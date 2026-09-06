"""Mint a host-independent sign-in link. Operator entry point.

    python -m cloud.server.link_token <github-login>

Prints ONE line — the URL — on stdout, and nothing else, so it can be piped
or copied without carrying anything with it. Everything advisory goes to
stderr. The signing secret is never printed, in either stream.

Why this exists: ``/auth/callback`` is pinned to the one host registered with
the GitHub OAuth app. When that host stops resolving (a Codespaces tunnel id
that changes on every cold start is the case that forced it) the deployment is
unreachable through the front door although the server is healthy. A link is
minted with ``JWT_SECRET``, lives minutes, is single use, and is checked
against ``ALLOWED_GITHUB_LOGINS`` when it is redeemed rather than when it is
made — so a link for somebody since removed is worth nothing.
"""
from __future__ import annotations

import os
import sys

from .auth import issue_link_token, link_ttl_seconds

USAGE = "usage: python -m cloud.server.link_token <github-login>"


def link_base() -> str:
    """Where the deployment answers, for building an absolute URL.

    ``PUBLIC_BASE_URL`` first: it is the one setting that names the deployment
    as the OUTSIDE sees it. ``UI_ORIGIN`` is the fallback, and only when it is
    absolute — its default is ``/``, which is no help in a link somebody has
    to paste into a browser. Failing both, the path alone is printed, which is
    honest: the operator knows their own host.
    """
    base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return base
    origin = os.environ.get("UI_ORIGIN", "").strip().rstrip("/")
    if origin.startswith("http://") or origin.startswith("https://"):
        return origin
    return ""


def link_url(login: str) -> str:
    """The sign-in URL for ``login``. Contains a token, never the secret."""
    return f"{link_base()}/auth/link?t={issue_link_token(login)}"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not args[0].strip() or args[0].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2
    if not os.environ.get("JWT_SECRET", "").strip():
        # Not fatal — a dev box runs on the built-in default — but a link
        # minted with a secret the server does not share is a confusing
        # 401, so say so where it cannot pollute the URL.
        print(
            "warning: JWT_SECRET is not set; this link is signed with the "
            "development default and will only work on a server that also "
            "has no JWT_SECRET",
            file=sys.stderr,
        )
    print(
        f"note: single use, valid for {link_ttl_seconds()}s",
        file=sys.stderr,
    )
    print(link_url(args[0].strip()))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
