from __future__ import annotations

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
result = subprocess.run(
    [sys.executable, "scripts/generate_gt_finalstand.py", "--check"],
    cwd=root,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
if result.returncode:
    sys.stdout.buffer.write(b"RED: fresh-checkout finalstand inventory drift\n")
    raise SystemExit(1)
raise SystemExit(0)
