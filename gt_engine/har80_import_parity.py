"""Route-B import parity for the certified Groundtruth Python wheel.

HAR-80 deliberately separates the certified surfaces: the vendored wheel owns
the Python runtime imported by ``gt_engine`` while the Groundtruth source tree
owns the ``gt-index`` binary and framework overlays.  This module keeps the
runtime import contract explicit and rejects accidental source-tree imports.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

SCHEMA = "gt.har80.import_parity.v1"

# Statically enumerated from every groundtruth.* import in gt_engine.
GT_ENGINE_IMPORTS = (
    "groundtruth._binary",
    "groundtruth.pretask.spec",
    "groundtruth.runtime",
    "groundtruth.runtime.adapters.miniswe",
    "groundtruth.runtime.covering_runner",
    "groundtruth.runtime.deterministic_queries",
    "groundtruth.runtime.edit_check",
    "groundtruth.runtime.episode_state",
    "groundtruth.runtime.evidence_envelope",
    "groundtruth.runtime.gateway",
    "groundtruth.runtime.hypothesis_ledger",
    "groundtruth.runtime.miniswe_provider_boundary",
    "groundtruth.runtime.native_render",
    "groundtruth.runtime.patch_auditor",
    "groundtruth.runtime.patterns",
    "groundtruth.runtime.reasoning_runtime",
    "groundtruth.runtime.rl_profile",
    "groundtruth.runtime.submit_gate",
    "groundtruth.runtime.terminal_evidence",
    "groundtruth.runtime.verification_plan",
)


def check_import_parity(*, source_root: str | os.PathLike[str] | None = None) -> dict[str, object]:
    """Import every statically enumerated module and report its origin.

    ``source_root`` is the checkout used for the certified binary.  Under route
    B, no Python runtime module may resolve from that source checkout.
    """

    source = Path(source_root).resolve() if source_root else None
    origins: dict[str, str] = {}
    errors: list[str] = []
    for name in GT_ENGINE_IMPORTS:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # pragma: no cover - exercised by failure probes
            errors.append(f"{name}: import failed: {type(exc).__name__}: {exc}")
            continue
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str) or not origin:
            errors.append(f"{name}: missing module origin")
            continue
        resolved = Path(origin).resolve()
        origins[name] = str(resolved)
        if source is not None:
            try:
                resolved.relative_to(source)
            except ValueError:
                pass
            else:
                errors.append(f"{name}: resolved from uncertified source tree {resolved}")
    return {
        "schema": SCHEMA,
        "modules": list(GT_ENGINE_IMPORTS),
        "origins": origins,
        "errors": errors,
        "passed": not errors and len(origins) == len(GT_ENGINE_IMPORTS),
        "python_runtime_certifier": "vendor/groundtruth_mcp-*.whl",
        "source_certifiers": ["gt-index binary", "framework overlays"],
    }
