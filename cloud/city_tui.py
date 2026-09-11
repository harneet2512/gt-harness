"""The city, in the terminal — real isometric 3D.

`python -m cloud.city_tui <session-id>` renders the same repository-city
the web canvas draws — districts as neighborhoods, buildings sized by
real file metrics, agents moving between what they touch, the commons
at the middle — as a true raster image. The terminal is the display:
every `▀` cell is two framebuffer pixels, top in the foreground color,
bottom in the background. 24-bit color, no GPU, no browser.

How it renders: an orthographic isometric camera projects every box in
the world to a quad on screen; boxes draw back-to-front (painter's
algorithm, sorted by x+z). Each visible face gets its own shade — top
lit, east mid, south dark — which is what makes it read as 3D at 80
columns.

Four panes, one session, same ontology as the web view:

  left   — the roster: primary + workers, what each is doing
  center — the city:   one island, neighborhoods by color, agents moving
  right  — the inspector: the file under the cursor, or the agent's last
  bottom — the feed:   the session's own event stream, newest first

Mouse: click a building to inspect it (SGR reporting, works in Windows
Terminal / modern xterm). Arrow keys rotate the city 45° a step.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text


# ------------------------------------------------------------------ #
# palette — muted, the same quiet city the browser draws
# ------------------------------------------------------------------ #

DISTRICT_HUES = [
    (109, 127, 150),   # steel
    (138, 127, 168),   # mauve
    (125, 144, 120),   # sage
    (160, 140, 111),   # sand
    (111, 144, 138),   # teal
    (160, 125, 138),   # rose
    (122, 139, 160),   # slate blue
    (148, 134, 106),   # olive
    (139, 131, 152),   # violet-grey
    (110, 138, 125),   # moss
]
PAPER = (247, 248, 250)
GRID = (224, 228, 234)
DECK = (233, 235, 238)
SHORE = (196, 200, 207)
COMMONS = (169, 198, 162)
TREE = (110, 150, 105)
ROUTE = (196, 204, 214)
EDITED = (206, 160, 96)
ACTIVE = (227, 213, 162)
AGENT_COLORS = [
    (201, 160, 107), (143, 176, 201), (176, 147, 201),
    (143, 201, 166), (201, 143, 160), (168, 201, 143),
]
INK = "#a8adb4"
DIM = "#6b7077"


def _hash(text: str) -> int:
    """A stable identity hash — districts keep their color across runs."""
    h = 0
    for ch in text:
        h = (h * 31 + ord(ch)) & 0x7FFFFFFF
    return h


def _hue(name: str) -> tuple[int, int, int]:
    return DISTRICT_HUES[_hash(name) % len(DISTRICT_HUES)]


def _mix(c: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    """Toward white by k — face shading lives on the same hue."""
    return tuple(min(255, round(v + (255 - v) * k)) for v in c)  # type: ignore


def _dark(c: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(round(v * (1 - k)) for v in c)  # type: ignore


# ------------------------------------------------------------------ #
# http — operator-local, mints from the same secret the server reads
# ------------------------------------------------------------------ #


def _env_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if secret:
        return secret
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("JWT_SECRET="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "dev-secret-change-me"


def _token(login: str) -> str:
    import jwt  # type: ignore

    now = int(time.time())
    return jwt.encode(
        {"sub": login, "login": login, "name": login, "avatar_url": "",
         "iat": now, "exp": now + 3600},
        _env_secret(), algorithm="HS256",
    )


def _get(host: str, token: str, path: str) -> Any:
    req = urllib.request.Request(
        f"{host}{path}", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001 — a TUI degrades, it does not die
        return {"__error__": str(e)}


# ------------------------------------------------------------------ #
# the world model — same ontology as the web city
# ------------------------------------------------------------------ #


@dataclass
class Building:
    path: str
    size: int
    district: str
    x: float          # world units
    z: float
    w: float          # footprint
    d: float
    h: float          # height
    edited: bool = False
    touched_at: float = 0.0


@dataclass
class District:
    name: str
    files: list[Building] = field(default_factory=list)
    x: float = 0.0
    z: float = 0.0
    w: float = 0.0
    d: float = 0.0


@dataclass
class TuiCity:
    districts: list[District]
    buildings: dict[str, Building]
    commons: tuple[float, float]
    bounds: tuple[float, float, float, float]
    island: tuple[float, float, float, float]  # cx, cz, halfw, halfd


def build_city(graph: dict[str, Any], edited: set[str]) -> TuiCity:
    """Districts as neighborhoods on one island — the same shelf the web
    city uses, pulled tight so the gaps read as avenues."""
    nodes = graph.get("nodes") or []
    by_dir: dict[str, list[Building]] = {}
    sizes = [n.get("size", 0) for n in nodes]
    hi = max(sizes) if sizes else 1
    for n in nodes:
        path = n.get("path") or n.get("id") or ""
        if not path:
            continue
        name = n.get("dir") or "root"
        frac = math.log1p(n.get("size", 0)) / math.log1p(max(1, hi))
        w = 2.6 + frac * 2.4
        h = 3.0 + frac * 20.0
        b = Building(path=path, size=n.get("size", 0), district=name,
                     x=0.0, z=0.0, w=w, d=w, h=h, edited=path in edited)
        by_dir.setdefault(name, []).append(b)
    districts = [District(name=k, files=sorted(v, key=lambda b: -b.size))
                 for k, v in sorted(by_dir.items(), key=lambda kv: -len(kv[1]))]

    # city blocks: shelf-pack, one avenue between neighborhoods.
    x = z = 0.0
    row_h = 0.0
    total_w = max(30.0, math.sqrt(sum(len(d.files) for d in districts)) * 3.4)
    for d in districts:
        cols = max(2, math.ceil(math.sqrt(len(d.files)) * 1.15))
        rows = max(1, math.ceil(len(d.files) / cols))
        dw, dh = cols * 3.4, rows * 3.4
        if x + dw > total_w:
            x, z, row_h = 0.0, z + row_h + 5.0, 0.0
        d.x, d.z, d.w, d.h = x, z, dw, dh
        row_h = max(row_h, dh)
        x += dw + 3.6                          # the avenue
        for i, b in enumerate(d.files):
            b.x = d.x + 1.7 + (i % cols) * 3.4
            b.z = d.z + 1.7 + (i // cols) * 3.4
    max_x = max((d.x + d.w for d in districts), default=0) + 4
    max_z = max((d.z + d.h for d in districts), default=0) + 4
    commons = (max_x * 0.62, max_z * 0.5)
    buildings = {b.path: b for d in districts for b in d.files}
    island = (max_x / 2, max_z / 2, max_x / 2 + 4, max_z / 2 + 4)
    return TuiCity(districts, buildings, commons, (0, 0, max_x, max_z), island)


# ------------------------------------------------------------------ #
# the rasterizer — boxes into a framebuffer, framebuffer into cells
# ------------------------------------------------------------------ #


class Camera:
    """Fixed orthographic isometric; `yaw` rotates the world in 45° steps."""
    def __init__(self) -> None:
        self.step = 0
        self.scale = 1.0
        self.ox = 0.0
        self.oy = 0.0

    @property
    def yaw(self) -> float:
        return math.pi / 4 + self.step * math.pi / 4

    def project(self, x: float, y: float, z: float) -> tuple[float, float]:
        ca, sa = math.cos(self.yaw), math.sin(self.yaw)
        rx, rz = x * ca - z * sa, x * sa + z * ca
        return (rx * self.scale + self.ox,
                (rz * 0.5 - y) * self.scale + self.oy)


def _quad(fb: list[list[tuple[int, int, int] | None]], W: int, H: int,
          pts: list[tuple[float, float]], color: tuple[int, int, int]) -> None:
    """Scanline-fill a projected quad. The whole world is quads — this is
    the only raster primitive the renderer needs."""
    ys = [p[1] for p in pts]
    y0, y1 = max(0, int(min(ys))), min(H - 1, int(max(ys)) + 1)
    for y in range(y0, y1):
        xs: list[float] = []
        for i in range(4):
            x1, yy1 = pts[i]
            x2, yy2 = pts[(i + 1) % 4]
            if (yy1 <= y < yy2) or (yy2 <= y < yy1):
                t = (y - yy1) / (yy2 - yy1)
                xs.append(x1 + t * (x2 - x1))
        if len(xs) >= 2:
            xs.sort()
            for x in range(max(0, int(xs[0])), min(W, int(xs[-1]) + 1)):
                fb[y][x] = color


def _box(fb: list[list[tuple[int, int, int] | None]], W: int, H: int,
         cam: Camera, x: float, z: float, w: float, d: float, h: float,
         color: tuple[int, int, int], y0: float = 0.0) -> None:
    """One building: three visible faces, each its own shade."""
    c = [cam.project(x, y0, z), cam.project(x + w, y0, z),
         cam.project(x + w, y0, z + d), cam.project(x, y0, z + d)]
    t = [cam.project(x, y0 + h, z), cam.project(x + w, y0 + h, z),
         cam.project(x + w, y0 + h, z + d), cam.project(x, y0 + h, z + d)]
    _quad(fb, W, H, [c[1], c[2], t[2], t[1]], _dark(color, 0.28))   # east face
    _quad(fb, W, H, [c[2], c[3], t[3], t[2]], _dark(color, 0.14))   # south face
    _quad(fb, W, H, t, _mix(color, 0.22))                            # roof


def _ground(fb: list[list[tuple[int, int, int] | None]], W: int, H: int,
            cam: Camera, x0: float, z0: float, x1: float, z1: float,
            color: tuple[int, int, int], y: float = 0.0) -> None:
    pts = [cam.project(x0, y, z0), cam.project(x1, y, z0),
           cam.project(x1, y, z1), cam.project(x0, y, z1)]
    _quad(fb, W, H, pts, color)


def render(city: TuiCity, cam: Camera, W: int, H: int,
           agents: list[tuple[float, float, tuple[int, int, int]]],
           pulses: dict[str, float], now: float) -> Text:
    """Draw the whole world into a framebuffer, then emit it as `▀` cells."""
    fb: list[list[tuple[int, int, int] | None]] = [[None] * W for _ in range(H)]

    # paper + drafting grid.
    for y in range(H):
        for x in range(W):
            fb[y][x] = GRID if (x % 10 == 0 or y % 6 == 0) else PAPER

    # the island + shore.
    icx, icz, ihw, ihd = city.island
    _ground(fb, W, H, cam, icx - ihw - 1, icz - ihd - 1,
            icx + ihw + 1, icz + ihd + 1, SHORE, y=-0.4)
    _ground(fb, W, H, cam, icx - ihw, icz - ihd, icx + ihw, icz + ihd, DECK)

    # neighborhood washes — the faintest tint on the deck; the buildings
    # carry the identity, the ground only whispers the border.
    for d in city.districts:
        _ground(fb, W, H, cam, d.x, d.z, d.x + d.w, d.z + d.h,
                _mix(_hue(d.name), 0.9), y=0.05)

    # the commons — the park at the root.
    cx, cz = city.commons
    _ground(fb, W, H, cam, cx - 5, cz - 3.4, cx + 5, cz + 3.4, COMMONS, y=0.06)
    for d in city.districts:   # routes: dependency lines to the commons
        x0, z0 = d.x + d.w / 2, d.z + d.h / 2
        steps = int(max(abs(cx - x0), abs(cz - z0)))
        for s in range(steps + 1):
            gx = x0 + (cx - x0) * s / max(1, steps)
            gz = z0 + (cz - z0) * s / max(1, steps)
            _ground(fb, W, H, cam, gx - 0.35, gz - 0.35,
                    gx + 0.35, gz + 0.35, ROUTE, y=0.07)

    # trees — small green boxes, street rows + commons grove.
    trees: list[tuple[float, float]] = []
    for d in city.districts:
        for i in range(6):
            trees.append((d.x + 1 + i * max(1.0, d.w / 6), d.z + d.h + 0.8))
    for i in range(8):
        a = i / 8 * math.pi * 2
        trees.append((cx + math.cos(a) * 3.6, cz + math.sin(a) * 2.4))
    for tx, tz in trees:
        _box(fb, W, H, cam, tx - 0.4, tz - 0.4, 0.8, 0.8, 1.6, TREE)

    # buildings — painter order: back to front.
    order = sorted(city.buildings.values(),
                   key=lambda b: b.x * math.cos(cam.yaw) + b.z * math.sin(cam.yaw))
    for b in order:
        color = EDITED if b.edited else _hue(b.district)
        t = pulses.get(b.path)
        if t is not None and now - t < 2.5:
            color = _mix(color, 0.5 + 0.4 * (1 - (now - t) / 2.5))
        _box(fb, W, H, cam, b.x - b.w / 2, b.z - b.w / 2, b.w, b.d, b.h, color)

    # agents — a lit marker over where each one last worked.
    for ax, az, color in agents:
        _box(fb, W, H, cam, ax - 0.6, az - 0.6, 1.2, 1.2, 1.2, color, y0=6.5)

    # emit: two framebuffer rows per terminal row, `▀` fg=top, bg=bottom.
    out = Text()
    for y in range(0, H - 1, 2):
        for x in range(W):
            top = fb[y][x] or PAPER
            bot = fb[y + 1][x] or PAPER
            out.append("▀", style=f"rgb({top[0]},{top[1]},{top[2]}) on rgb({bot[0]},{bot[1]},{bot[2]})")
        out.append("\n")
    return out


def pick(city: TuiCity, cam: Camera, sx: float, sy: float) -> Building | None:
    """Which building's silhouette holds this screen pixel — frontmost wins."""
    best: Building | None = None
    best_depth = -1e9
    for b in city.buildings.values():
        pts = [cam.project(b.x - b.w / 2, 0, b.z - b.d / 2),
               cam.project(b.x + b.w / 2, 0, b.z + b.d / 2),
               cam.project(b.x + b.w / 2, b.h, b.z + b.d / 2),
               cam.project(b.x - b.w / 2, b.h, b.z - b.d / 2)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if min(xs) <= sx <= max(xs) and min(ys) <= sy <= max(ys):
            depth = b.x * math.cos(cam.yaw) + b.z * math.sin(cam.yaw)
            if depth > best_depth:
                best, best_depth = b, depth
    return best


# ------------------------------------------------------------------ #
# mouse — SGR 1006 reporting on a reader thread
# ------------------------------------------------------------------ #


class Mouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.lock = threading.Lock()

    def start(self) -> None:
        sys.stdout.write("\x1b[?1006h\x1b[?1002h\x1b[?1000h")
        sys.stdout.flush()
        threading.Thread(target=self._read, daemon=True).start()

    def stop(self) -> None:
        sys.stdout.write("\x1b[?1006l\x1b[?1002l\x1b[?1000l")
        sys.stdout.flush()

    def _read(self) -> None:
        buf = ""
        while True:
            ch = sys.stdin.read(1)
            if not ch:
                return
            buf += ch
            if buf.endswith("M") or buf.endswith("m"):
                if buf.startswith("\x1b[<"):
                    try:
                        body = buf[3:-1]
                        b, x, y = body.split(";")
                        if int(b) == 0 and buf.endswith("M"):
                            with self.lock:
                                self.clicks.append((int(x), int(y)))
                    except ValueError:
                        pass
                buf = ""
            elif len(buf) > 16 or not buf.startswith("\x1b"):
                buf = ""

    def take(self) -> list[tuple[int, int]]:
        with self.lock:
            out, self.clicks = self.clicks, []
            return out


# ------------------------------------------------------------------ #
# panels — the same chrome the web view has
# ------------------------------------------------------------------ #


def _roster_panel(agents: list[dict[str, Any]]) -> Text:
    out = Text()
    for i, a in enumerate(agents):
        c = AGENT_COLORS[i % len(AGENT_COLORS)]
        name = (a.get("label") or a.get("task") or a.get("id", "?"))[:16]
        out.append(f"◆ {name}\n", style=f"rgb({c[0]},{c[1]},{c[2]})")
        out.append(f"  {a.get('status','')}", style=DIM)
        act = (a.get("activity") or "")[:14]
        if act:
            out.append(f" · {act}", style=DIM)
        out.append("\n")
    return out


def _inspector_panel(b: Building | None, session: dict[str, Any]) -> Text:
    out = Text()
    if b:
        out.append(f"{b.path}\n\n", style=INK)
        out.append(f"size      {b.size:,} B\n", style=DIM)
        out.append(f"district  {b.district}\n", style=DIM)
        out.append(f"height    {b.h:.1f}\n", style=DIM)
        if b.edited:
            out.append("state     edited\n", style=f"rgb{EDITED}")
    else:
        out.append("click a building\n", style=DIM)
    out.append("\n")
    out.append(f"status   {session.get('status','?')}\n", style=DIM)
    out.append(f"turns    {session.get('turns',0)}\n", style=DIM)
    out.append(f"steps    {session.get('steps',0)}\n", style=DIM)
    out.append("←/→      rotate\n", style=DIM)
    return out


def _feed_panel(messages: list[dict[str, Any]]) -> Text:
    out = Text()
    for m in messages[-5:]:
        role = m.get("role", "?")
        text = (m.get("content") or "").split("\n")[0][:90]
        mark = "›" if role == "agent" else "·"
        out.append(f"{mark} {text}\n", style=DIM if role == "assistant" else INK)
    return out


# ------------------------------------------------------------------ #
# main loop
# ------------------------------------------------------------------ #


def run(session_id: str, host: str, login: str) -> None:
    console = Console()
    token = _token(login)
    city: TuiCity | None = None
    cam = Camera()
    mouse = Mouse()
    pulses: dict[str, float] = {}
    selected: str | None = None
    pane_offset = 24  # roster width + border, where the city pane starts

    def frame() -> Layout:
        nonlocal city, selected
        session = _get(host, token, f"/api/sessions/{session_id}")
        if isinstance(session, dict) and session.get("__error__"):
            return Layout(Panel(Text(f"cannot reach session: {session['__error__']}")))
        agents = _get(host, token, f"/api/sessions/{session_id}/agents") or []
        messages = _get(host, token, f"/api/sessions/{session_id}/messages") or []
        diff = _get(host, token, f"/api/sessions/{session_id}/diff") or {}
        graph = _get(host, token, f"/api/sessions/{session_id}/graph") or {}

        edited = {f["path"] for f in diff.get("files", []) if isinstance(f, dict)}
        if city is None or len(city.buildings) != len(graph.get("nodes", [])):
            city = build_city(graph, edited)
            # Fit by projection: put the island's center on the screen's,
            # biased slightly low so the skyline has room to rise.
            _, _, w, h = city.bounds
            cam_w = max(30, console.size.width - 50)
            cam_h = max(10, console.size.height - 12) * 2
            span = max(w, h * 1.6)
            cam.scale = min(cam_w / (span * 1.15), cam_h / (span * 0.65))
            cam.ox = 0.0
            cam.oy = 0.0
            px, py = cam.project(city.island[0], 0, city.island[1])
            cam.ox = cam_w / 2 - px
            cam.oy = cam_h * 0.55 - py
        else:
            for p in edited:
                if p in city.buildings:
                    city.buildings[p].edited = True

        agents_pos: list[tuple[float, float, tuple[int, int, int]]] = []
        focus: Building | None = None
        for i, a in enumerate(agents if isinstance(agents, list) else []):
            color = AGENT_COLORS[i % len(AGENT_COLORS)]
            files = a.get("files") or []
            path = files[-1] if files else None
            if path and path in city.buildings:
                b = city.buildings[path]
                agents_pos.append((b.x, b.z, color))
                pulses[path] = time.time()
                focus = b
            else:
                agents_pos.append((city.commons[0] + i * 2.4 - 2, city.commons[1], color))

        # clicks → pick the building under the pixel (cell → 2 rows/px).
        cam_w = max(30, console.size.width - 50)
        cam_h = max(10, console.size.height - 12) * 2
        for mx, my in mouse.take():
            b = pick(city, cam, (mx - pane_offset) * 1.0, (my - 2) * 2.0)
            selected = b.path if b else selected

        cam_w = max(30, console.size.width - 50)
        cam_h = max(10, console.size.height - 12) * 2
        shown = city.buildings.get(selected, focus) if selected else focus
        lay = Layout()
        lay.split_column(Layout(name="body", ratio=1),
                         Layout(name="feed", size=7))
        lay["body"].split_row(
            Layout(Panel(_roster_panel(agents if isinstance(agents, list) else []),
                         border_style=DIM, title="roster"), size=22),
            Layout(Panel(render(city, cam, cam_w, cam_h, agents_pos,
                                pulses, time.time()),
                         border_style=DIM,
                         title=f"{session.get('repo','?')}@{session.get('ref','')}"),
                   ratio=1),
            Layout(Panel(_inspector_panel(shown, session),
                         border_style=DIM, title="inspector"), size=26),
        )
        lay["feed"].update(Panel(
            _feed_panel(messages if isinstance(messages, list) else []),
            border_style=DIM, title="feed"))
        return lay

    mouse.start()
    try:
        with Live(frame(), console=console, refresh_per_second=4, screen=True) as live:
            while True:
                time.sleep(0.25)
                live.update(frame())
    finally:
        mouse.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="The session's city — isometric, in the terminal.")
    ap.add_argument("session_id")
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    ap.add_argument("--login", default="harneet2512")
    args = ap.parse_args()
    run(args.session_id, args.host, args.login)


if __name__ == "__main__":
    main()
