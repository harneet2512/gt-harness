"""Content-addressed identity of the executable treatment runtime."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "missing"


@dataclass(frozen=True, slots=True)
class RuntimeVersionAttestation:
    python_version: str
    harbor_version: str
    miniswe_version: str
    gt_agent_version: str
    gt_source_revision: str
    index_binary_sha256: str
    provider_route_identity: str

    def as_dict(self) -> dict[str, str]:
        row = {key: str(value) for key, value in asdict(self).items()}
        row["schema"] = "gt.runtime_attestation.v1"
        row["attestation_sha256"] = self.sha256
        return row

    @property
    def sha256(self) -> str:
        payload = {key: str(value) for key, value in asdict(self).items()}
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def capture_runtime_attestation(
    *,
    gt_source_revision: str,
    provider_route_identity: str,
    index_binary_path: str | Path | None = None,
) -> RuntimeVersionAttestation:
    binary_hash = ""
    if index_binary_path:
        path = Path(index_binary_path)
        if path.is_file():
            binary_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return RuntimeVersionAttestation(
        python_version=platform.python_version(),
        harbor_version=_version("harbor"),
        miniswe_version=_version("mini-swe-agent"),
        gt_agent_version=_version("nano-harness"),
        gt_source_revision=str(gt_source_revision or ""),
        index_binary_sha256=binary_hash,
        provider_route_identity=str(provider_route_identity or ""),
    )


__all__ = ["RuntimeVersionAttestation", "capture_runtime_attestation"]
