from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import gt_engine.content_address  # noqa: F401
except ModuleNotFoundError as exc:
    if exc.name != "gt_engine.content_address":
        raise
    sys.stdout.buffer.write(b"RED: gt_engine.content_address is absent\n")
    raise SystemExit(1) from None

raise SystemExit(0)
