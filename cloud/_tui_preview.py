"""Render the TUI city's framebuffer to a PNG for headless inspection.

    python -m cloud._tui_preview <session-id> <out.png> [cols] [rows]

Fetches the live session's graph, builds the TUI city, calls the exact
same `_paint` the terminal renderer uses, and writes the framebuffer to
a PNG at 8x so it can be opened and judged without a terminal.
"""

from __future__ import annotations

import os
import sys
import time

sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main() -> int:
    session_id = sys.argv[1] if len(sys.argv) > 1 else "4586dae8e302"
    out = sys.argv[2] if len(sys.argv) > 2 else "D:/tmp/tui_preview.png"
    W = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    H = int(sys.argv[4]) if len(sys.argv) > 4 else 60

    for line in open(os.path.join(os.path.dirname(__file__), ".env")):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)
    import cloud.city_tui as T
    from PIL import Image

    tok = T._token("harneet2512")
    g = T._get("http://127.0.0.1:8000", tok, f"/api/sessions/{session_id}/graph")
    city = T.build_city(g, set())
    cam = T.Camera()

    # the same call render() makes — two sample beacons for scale.
    agents = [(city.commons[0], city.commons[1], 2.5, (201, 160, 107))]
    fb = T._paint(city, cam, W, H, agents, {}, time.time())

    img = Image.new("RGB", (W, H))
    px2 = img.load()
    for y in range(H):
        for x in range(W):
            px2[x, y] = fb[y][x] or T.PAPER
    img = img.resize((W * 8, H * 8), Image.NEAREST)
    img.save(out)
    print(f"saved {out} ({W}x{H}px, {len(city.buildings)} buildings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
