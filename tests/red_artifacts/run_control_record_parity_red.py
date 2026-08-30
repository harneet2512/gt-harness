from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="gt-no-sibling-") as missing:
    env = __import__("os").environ.copy()
    env["GROUNDTRUTH_ROOT"] = missing
    result = subprocess.run(
        [sys.executable, "scripts/generate_gt_finalstand.py", "--check"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
if result.returncode == 0:
    sys.stdout.buffer.write(b"RED: fresh-checkout finalstand inventory drift\n")
    raise SystemExit(1)
raise SystemExit(0)
