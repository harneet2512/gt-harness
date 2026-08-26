"""Run Ruff over the exact prerelease source and verification closure."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from gt_harness.product_certification import load_product_surface

ROOT = Path(__file__).resolve().parents[1]
CERTIFICATION_SCRIPTS = (
    "scripts/failure_campaign.py",
    "scripts/graph_lifecycle_campaign.py",
    "scripts/graph_truth_audit.py",
    "scripts/harness_real_repository_campaign.py",
    "scripts/language_lifecycle_matrix.py",
    "scripts/lint_product_surface.py",
    "scripts/localization_truth_gate.py",
    "scripts/product_repository_matrix.py",
    "scripts/replay_smoke20_localization.py",
    "scripts/verify_gt_harness.py",
    "scripts/verify_product_surface.py",
)


def _module_path(module: str) -> str:
    return module.replace(".", "/") + ".py"


def lint_paths() -> tuple[str, ...]:
    """Return the deterministic source and test closure enforced for release."""

    surface = load_product_surface(ROOT)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = project["tool"]["pytest"]["ini_options"]["python_files"]
    paths = {_module_path(module) for module in surface.python_modules}
    paths.update(CERTIFICATION_SCRIPTS)
    for pattern in patterns:
        paths.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob(pattern))
    missing = sorted(path for path in paths if not (ROOT / path).is_file())
    if missing:
        raise FileNotFoundError(f"release lint paths are missing: {', '.join(missing)}")
    return tuple(sorted(paths))


def main() -> int:
    return subprocess.call(
        [sys.executable, "-m", "ruff", "check", *lint_paths()],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
