"""GitHub Actions as the compute substrate.

``/gha <task>`` hands a turn to a runner we do not host: the product's
own ``agent-turn.yml`` on GitHub's Actions pool. The run registers as an
ordinary external agent — same row, same ingest contract, same card —
so the city shows it working like any other agent, and the work itself
never touches this process.

Configuration, all env:

  ``GHA_TOKEN``    a PAT (or fine-grained token) with ``actions:write``
                   on the repo that hosts the workflow. The one secret
                   this path needs.
  ``GHA_REPO``     ``owner/name`` — the repo whose Actions run it.
  ``GHA_WORKFLOW`` the workflow filename; default ``agent-turn.yml``.
  ``GHA_REF``      the ref the workflow file lives on; default
                   ``cloud/internal-harness``.

Every one of these is operator config — nothing is inferred, and a
missing piece fails the dispatch rather than guessing.
"""

from __future__ import annotations

import os

import httpx


class GhaUnavailable(RuntimeError):
    """The dispatch could not be made — configuration or GitHub refused."""


def _cfg() -> tuple[str, str, str, str]:
    return (
        os.environ.get("GHA_REPO", "").strip(),
        os.environ.get("GHA_WORKFLOW", "agent-turn.yml").strip(),
        os.environ.get("GHA_REF", "cloud/internal-harness").strip(),
        os.environ.get("GHA_TOKEN", "").strip(),
    )


async def dispatch_run(
    *,
    session_id: str,
    agent_id: str,
    task: str,
    repo: str,
    ref: str,
    ingest_url: str,
    ingest_token: str,
) -> None:
    """One ``workflow_dispatch`` — the run becomes the agent.

    The ingest token travels as a workflow input: it is scoped to exactly
    this agent, short-lived, and revocable by closing the session — the
    least powerful credential that can do the job.
    """
    slug, workflow, gha_ref, token = _cfg()
    if not token:
        raise GhaUnavailable("GHA_TOKEN is not set")
    if not slug:
        raise GhaUnavailable("GHA_REPO is not set — no repo hosts the agent workflow")
    inputs = {
        "session_id": session_id,
        "agent_id": agent_id,
        "task": task,
        "repo": repo,
        "ref": ref or "main",
        "ingest_url": ingest_url,
        "ingest_token": ingest_token,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(
                f"https://api.github.com/repos/{slug}/actions/workflows/{workflow}/dispatches",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"ref": gha_ref, "inputs": inputs},
            )
        except httpx.HTTPError as exc:
            raise GhaUnavailable(f"GitHub unreachable: {exc}") from exc
    if r.status_code != 204:
        detail = ""
        try:
            detail = r.json().get("message", "")
        except Exception:
            detail = r.text[:200]
        raise GhaUnavailable(f"GitHub refused the dispatch ({r.status_code}): {detail}")
