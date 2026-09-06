"""Bind installed dense assets to metadata at the pinned publisher revision."""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

from gt_engine.dense_runtime import QUERY_PREFIX, model_identity
from gt_harness.canonical_io import atomic_json, atomic_write


def verify(model_dir: Path, output: Path) -> dict:
    identity = model_identity()
    repository, revision = str(identity["model_id"]).split("#", 1)[0].split("@", 1)
    api_url = f"https://huggingface.co/api/models/{repository}/revision/{revision}?blobs=true"
    base_url = f"https://huggingface.co/{repository}/resolve/{revision}/"

    def fetch(url: str, name: str) -> bytes:
        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read()
        atomic_write(output.parent / name, raw)
        return raw

    metadata_raw = fetch(api_url, "publisher-revision.json")
    metadata = json.loads(metadata_raw)
    if metadata.get("sha") != revision:
        raise ValueError("publisher_revision_mismatch")
    files = {row["rfilename"]: row for row in metadata["siblings"]}
    model = files["onnx/model.onnx"]
    with (model_dir / "model.onnx").open("rb") as stream:
        model_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if model["lfs"]["sha256"] != model_digest or model_digest != identity["model_sha256"]:
        raise ValueError("publisher_model_identity_mismatch")
    if (model_dir / "model.onnx").stat().st_size != model["size"]:
        raise ValueError("publisher_model_size_mismatch")
    tokenizer = fetch(base_url + "tokenizer.json", "publisher-tokenizer.json")
    tokenizer_digest = hashlib.sha256(tokenizer).hexdigest()
    tokenizer_git_blob = hashlib.sha1(
        f"blob {len(tokenizer)}\0".encode() + tokenizer, usedforsecurity=False,
    ).hexdigest()
    if (tokenizer != (model_dir / "tokenizer.json").read_bytes()
            or tokenizer_digest != identity["tokenizer_sha256"]
            or tokenizer_git_blob != files["tokenizer.json"]["blobId"]):
        raise ValueError("publisher_tokenizer_identity_mismatch")
    readme = fetch(base_url + "README.md", "publisher-README.md")
    documentation = readme.decode("utf-8")
    if ("use the CLS token" not in documentation or "just on the query" not in documentation
            or QUERY_PREFIX not in documentation
            or "query_embeddings = model(**query_tokens)[0][:, 0]" not in documentation
            or "document_embeddings = model(**document_tokens)[0][:, 0]" not in documentation):
        raise ValueError("publisher_recipe_evidence_missing")
    receipt = {
        "schema": "gt.dense_publisher_identity.v1", "status": "verified",
        "repository": repository, "revision": revision, "metadata_url": api_url,
        "metadata_sha256": hashlib.sha256(metadata_raw).hexdigest(),
        "readme_url": base_url + "README.md",
        "readme_sha256": hashlib.sha256(readme).hexdigest(),
        "model_sha256": model_digest, "model_bytes": model["size"],
        "tokenizer_sha256": tokenizer_digest, "tokenizer_git_blob": tokenizer_git_blob,
        "recipe_id": identity["recipe_id"], "pooling": "CLS",
        "query_only_prefix": QUERY_PREFIX,
        "scope": "publisher_asset_and_documented_recipe_identity_not_runtime_quality",
    }
    atomic_json(output, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.model_dir, args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
