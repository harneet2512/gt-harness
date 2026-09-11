"""Upload a file as an ephemeral GitHub Actions v4 artifact mid-run.

Uses the Actions Results API with the runtime token that every workflow step
already carries (ACTIONS_RUNTIME_TOKEN / ACTIONS_RESULTS_URL). v4 artifacts are
queryable and downloadable via the REST API as soon as they are finalized --
while the job is still running -- which makes this a mid-run status channel.

Artifact names must be unique per run; callers should pass a per-tick suffix.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.request
import zipfile
import io


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _backend_ids(token: str) -> tuple[str, str]:
    try:
        payload = json.loads(_b64url_decode(token.split(".")[1]))
    except (IndexError, json.JSONDecodeError) as exc:
        raise SystemExit(f"runtime token is not a decodable JWT: {exc}")
    scp = payload.get("scp") or ""
    for scope in scp.split(" "):
        parts = scope.split(":")
        if parts and parts[0] == "Actions.Results" and len(parts) >= 3:
            return parts[1], parts[2]
    raise SystemExit("runtime token lacks an Actions.Results scope")


def _post_json(url: str, token: str, body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _put_blob(url: str, blob: bytes) -> None:
    request = urllib.request.Request(
        url,
        data=blob,
        headers={
            "Content-Type": "application/zip",
            "Content-Length": str(len(blob)),
            "x-ms-blob-type": "BlockBlob",
        },
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status not in (200, 201):
            raise SystemExit(f"blob upload failed: {response.status}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--name", required=True, help="unique artifact name")
    args = parser.parse_args()

    token = os.environ.get("ACTIONS_RUNTIME_TOKEN", "")
    results_url = os.environ.get("ACTIONS_RESULTS_URL", "")
    if not token or not results_url:
        print("runtime token/results url unavailable; skipping upload", file=sys.stderr)
        return 0

    try:
        with open(args.file, "rb") as handle:
            payload = handle.read()
    except OSError as exc:
        print(f"cannot read {args.file}: {exc}", file=sys.stderr)
        return 0

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(os.path.basename(args.file), payload)
    blob = buffer.getvalue()

    run_backend, job_backend = _backend_ids(token)
    base = results_url.rstrip("/") + "/twirp/github.actions.results.api.v1.ArtifactService"

    created = _post_json(
        base + "/CreateArtifact",
        token,
        {
            "workflow_run_backend_id": run_backend,
            "workflow_job_run_backend_id": job_backend,
            "name": args.name,
            "version": 4,
        },
    )
    if not created.get("ok"):
        print(f"CreateArtifact declined: {created}", file=sys.stderr)
        return 0
    signed_url = created.get("signed_upload_url") or created.get("signedUploadUrl")
    if not signed_url:
        print("CreateArtifact returned no upload url", file=sys.stderr)
        return 0

    _put_blob(signed_url, blob)

    finalized = _post_json(
        base + "/FinalizeArtifact",
        token,
        {
            "workflow_run_backend_id": run_backend,
            "workflow_job_run_backend_id": job_backend,
            "name": args.name,
            "size": len(blob),
            "hash": f"sha256:{hashlib.sha256(blob).hexdigest()}",
        },
    )
    if not finalized.get("ok"):
        print(f"FinalizeArtifact declined: {finalized}", file=sys.stderr)
        return 0
    print(f"uploaded {args.name} ({len(blob)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
