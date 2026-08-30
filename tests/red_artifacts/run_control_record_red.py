from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import scripts.linear_control_record  # noqa: F401
except ModuleNotFoundError as exc:
    if exc.name != "scripts.linear_control_record":
        raise
    sys.stdout.buffer.write(b"RED: scripts.linear_control_record is absent\n")
    raise SystemExit(1) from None

raise SystemExit(0)
