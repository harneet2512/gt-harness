"""Deterministic public-surface discovery from checked-out repository files.

This resolver never invents an entrypoint.  It returns only existing regular
files named by a package manifest or by a language's conventional module/crate
surface, and it keeps the reason so provider delivery can distinguish the
inspection obligation from graph-proven edit authority.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BUNDLER_INPUT = re.compile(
    r"\binput\s*:\s*(?:path\.resolve\([^,]+,\s*)?['\"]([^'\"]+)['\"]"
)
_BUNDLER_CONFIGS = (
    "rollup.config.js",
    "rollup.config.mjs",
    "rollup.config.cjs",
    "rollup.config.ts",
)


@dataclass(frozen=True, slots=True)
class PublicSurfaceCandidate:
    path: str
    reason: str


def _manifest_paths(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(
            item
            for key in sorted(value)
            for item in _manifest_paths(value[key])
        )
    if isinstance(value, list):
        return tuple(item for row in value for item in _manifest_paths(row))
    return ()


class PublicSurfaceResolver:
    """Resolve bounded, existing language and manifest entrypoints."""

    def __init__(self, root: str | Path, *, maximum: int = 4) -> None:
        self.root = Path(root).resolve()
        self.maximum = max(1, min(int(maximum), 8))

    def _relative_file(self, candidate: Path) -> str:
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError):
            return ""
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            return ""
        return resolved.relative_to(self.root).as_posix()

    def _anchor(self, path: str) -> Path | None:
        try:
            candidate = (self.root / str(path or "").replace("\\", "/")).resolve()
        except (OSError, RuntimeError):
            return None
        if not candidate.is_relative_to(self.root) or not candidate.is_file():
            return None
        return candidate

    def resolve(self, anchor_paths: tuple[str, ...]) -> tuple[PublicSurfaceCandidate, ...]:
        anchors = tuple(
            anchor
            for path in dict.fromkeys(anchor_paths)
            if (anchor := self._anchor(path)) is not None
        )
        if not anchors:
            return ()
        rows: list[PublicSurfaceCandidate] = []

        manifests: set[Path] = set()
        for anchor in anchors:
            current = anchor.parent
            while current.is_relative_to(self.root):
                manifest = current / "package.json"
                if manifest.is_file():
                    manifests.add(manifest)
                if current == self.root:
                    break
                current = current.parent
        for manifest in sorted(manifests, key=lambda path: path.as_posix().casefold()):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            declared: list[str] = []
            for key in ("exports", "main", "module", "types", "typings"):
                declared.extend(_manifest_paths(payload.get(key)))
            for raw in declared:
                if not raw or raw.startswith(("node:", "http:", "https:")):
                    continue
                path = self._relative_file(manifest.parent / raw)
                if path and path not in {item.path for item in rows}:
                    rows.append(PublicSurfaceCandidate(path, "package_manifest_entrypoint"))
            for config_name in _BUNDLER_CONFIGS:
                config = manifest.parent / config_name
                try:
                    body = config.read_text(encoding="utf-8")
                except (FileNotFoundError, OSError, UnicodeError):
                    continue
                if len(body) > 1_000_000:
                    continue
                for match in _BUNDLER_INPUT.finditer(body):
                    raw = match.group(1)
                    if any(marker in raw for marker in ("*", "${")):
                        continue
                    path = self._relative_file(manifest.parent / raw)
                    if path and path not in {item.path for item in rows}:
                        rows.append(
                            PublicSurfaceCandidate(
                                path, "bundler_source_entrypoint"
                            )
                        )

        for anchor in anchors:
            suffix = anchor.suffix.casefold()
            if suffix in {".py", ".pyi"}:
                current = anchor.parent
                while current.is_relative_to(self.root):
                    for name in ("__init__.py", "__init__.pyi"):
                        path = self._relative_file(current / name)
                        if path and path not in {item.path for item in rows}:
                            rows.append(
                                PublicSurfaceCandidate(path, "python_package_surface")
                            )
                    if current == self.root:
                        break
                    current = current.parent
            elif suffix == ".rs":
                current = anchor.parent
                while current.is_relative_to(self.root):
                    for name in ("lib.rs", "mod.rs"):
                        path = self._relative_file(current / name)
                        if path and path not in {item.path for item in rows}:
                            rows.append(
                                PublicSurfaceCandidate(
                                    path, "rust_crate_or_module_surface"
                                )
                            )
                    if current == self.root:
                        break
                    current = current.parent

        anchor_relatives = {
            anchor.relative_to(self.root).as_posix() for anchor in anchors
        }
        unique = {
            (item.path, item.reason): item
            for item in rows
            if item.path not in anchor_relatives
        }
        return tuple(unique[key] for key in sorted(unique))[: self.maximum]


__all__ = ["PublicSurfaceCandidate", "PublicSurfaceResolver"]
