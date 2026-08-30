from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "generate_gt_finalstand.py"
SPEC_NAMES = (
    "bash",
    "c",
    "cpp",
    "csharp",
    "css",
    "cue",
    "elm",
    "elixir",
    "go",
    "groovy",
    "hcl",
    "html",
    "java",
    "javascript",
    "kotlin",
    "lua",
    "markdown",
    "ocaml",
    "php",
    "protobuf",
    "python",
    "ruby",
    "rust",
    "scala",
    "sql",
    "svelte",
    "swift",
    "toml",
    "typescript",
    "yaml",
)


def _run(source_root: Path) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment["GROUNDTRUTH_ROOT"] = str(source_root)
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


with tempfile.TemporaryDirectory(prefix="gt-missing-dependency-") as missing_dir:
    missing = Path(missing_dir)
    missing_result = _run(missing)
    missing_output = missing_result.stdout.decode("utf-8", "replace")
    if missing_result.returncode == 0 or "groundtruth_dependency_unavailable" not in missing_output:
        print("FAIL: missing dependency did not fail closed")
        raise SystemExit(1)

with tempfile.TemporaryDirectory(prefix="gt-pinned-present-") as present_dir:
    present = Path(present_dir)
    specs = present / "gt-index" / "internal" / "specs"
    specs.mkdir(parents=True)
    for name in SPEC_NAMES:
        (specs / f"{name}.go").write_text("// pinned fixture\n", encoding="utf-8")
    compatibility = (
        present
        / "src"
        / "groundtruth"
        / "runtime"
        / "generated_language_operation_compatibility.json"
    )
    compatibility.parent.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "gt_finalstand" / "language_operation_compatibility.json",
        compatibility,
    )
    present_result = _run(present)
    if present_result.returncode != 0:
        print("FAIL: pinned-present dependency did not pass generator check")
        raise SystemExit(1)

print("PASS: missing dependency fails closed; pinned-present dependency passes")
