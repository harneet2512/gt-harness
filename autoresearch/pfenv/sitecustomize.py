"""Autoresearch verify env: pin `scripts` to the gt-harness repo.

This dev box carries foreign editable installs (D:\\gt-cloud) whose regular
`scripts` package shadows this repo's namespace `scripts` directory. Python
auto-imports this module at interpreter startup whenever autoresearch/pfenv
is on PYTHONPATH, so every descendant process resolves `scripts.*` to the
gt-harness tree without modifying the repo.
"""
import sys
import types
from pathlib import Path

ROOT = Path(r"D:/gt-harness")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pkg = types.ModuleType("scripts")
pkg.__path__ = [str(ROOT / "scripts")]
sys.modules["scripts"] = pkg
