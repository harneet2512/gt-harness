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
SHORE = (186, 191, 199)
SHADE = (206, 210, 217)
COMMONS = (158, 190, 148)
COMMONS_DARK = (138, 172, 128)
TREE = (104, 146, 98)
ROUTE = (204, 210, 218)
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
    park: tuple[float, float, float, float]     # x0, z0, x1, z1
    bounds: tuple[float, float, float, float]
    island: tuple[float, float, float, float]  # cx, cz, halfw, halfd


# street grid — one lot per building; the gap between lots is the street.
LOT = 6.4        # intra-district pitch
AVE = 8.0        # avenue between districts


def build_city(graph: dict[str, Any], edited: set[str]) -> TuiCity:
    """Districts as neighborhoods on one island — the same shelf the web
    city uses, with a real park carved out for the commons at the root.

    Every file gets a whole lot (`LOT` wide) so towers never overlap;
    the lot is bigger than any footprint, so the gaps between towers
    read as streets. Districts shelf-pack into rows with avenues, and
    a park rectangle is reserved before packing so the commons is a
    green field with open sky around it — never buried under a block.
    """
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
        v = _hash(path)
        w = 3.4 + frac * 2.0                                     # 3.4..5.4 footprint
        d = w * (0.82 + ((v >> 16) % 71) / 71 * 0.40)            # slightly rectangular
        h = 4.5 + frac * 26.0 + ((v >> 6) % 89) / 89 * 3.0       # 4.5..33.5 skyline
        b = Building(path=path, size=n.get("size", 0), district=name,
                     x=0.0, z=0.0, w=w, d=d, h=h, edited=path in edited)
        by_dir.setdefault(name, []).append(b)
    districts = [District(name=k, files=sorted(v, key=lambda b: -b.size))
                 for k, v in sorted(by_dir.items(), key=lambda kv: -len(kv[1]))]
    for d in districts:
        cols = max(2, math.ceil(math.sqrt(len(d.files)) * 1.25))
        rows = max(1, math.ceil(len(d.files) / cols))
        d.w, d.h = cols * LOT, rows * LOT

    # shelf-pack the blocks into rows with avenues between them, then
    # center each row on the widest — the island reads as one town,
    # not a pile pushed into a corner.
    area = sum((d.w + AVE) * (d.h + AVE) for d in districts)
    total_w = max(48.0, math.sqrt(area) * 1.30)
    x = z = row_h = 0.0
    rows: list[list[District]] = [[]]
    for d in districts:
        if x + d.w > total_w:
            x, z, row_h = 0.0, z + row_h + AVE, 0.0
            rows.append([])
        d.x, d.z = x, z
        row_h = max(row_h, d.h)
        x += d.w + AVE
        rows[-1].append(d)
    city_w = max((r[-1].x + r[-1].w for r in rows if r), default=10.0)
    city_d = max((d.z + d.h for d in districts), default=0)
    for r in rows:                                   # center each shelf row
        if not r:
            continue
        off = (city_w - (r[-1].x + r[-1].w)) / 2
        for d in r:
            d.x += off
    for d in districts:
        cols = int(round(d.w / LOT))
        for i, b in enumerate(d.files):
            v = _hash(b.path)
            b.x = d.x + (i % cols + 0.5) * LOT + (((v >> 4) % 61) / 61 - 0.5) * 0.7
            b.z = d.z + (i // cols + 0.5) * LOT + (((v >> 10) % 61) / 61 - 0.5) * 0.7
        # downtown gradient + checkerboard undulation: towers rise toward
        # the middle of each block and alternate high/low so every roof
        # steps against its neighbors — a skyline, not a flat crust.
        dcx, dcz = d.x + d.w / 2, d.z + d.h / 2
        reach = max(1.0, math.hypot(d.w, d.h) / 2)
        for i, b in enumerate(d.files):
            r = math.hypot(b.x - dcx, b.z - dcz) / reach
            check = 0.80 + 0.40 * ((i % cols + i // cols) % 2)
            b.h *= (1.30 - 0.55 * min(1.0, r)) * check

    # the commons: a town square on the waterfront — a green rectangle
    # across the city's south edge, centered on the skyline, with an
    # open promenade between the last block and the grass.
    park_w = min(46.0, max(26.0, city_w * 0.50))
    park_d = park_w * 0.62
    px0 = city_w / 2 - park_w / 2
    pz0 = city_d + AVE * 0.8
    px1, pz1 = px0 + park_w, pz0 + park_d

    max_x = max(city_w, px1) + 2
    max_z = pz1 + 2
    commons = ((px0 + px1) / 2, (pz0 + pz1) / 2)
    buildings = {b.path: b for d in districts for b in d.files}
    island = (max_x / 2, max_z / 2, max_x / 2 + 2.5, max_z / 2 + 2.5)
    return TuiCity(districts, buildings, commons, (px0, pz0, px1, pz1),
                   (0, 0, max_x, max_z), island)


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
                (rz * 0.44 - y) * self.scale + self.oy)

    def fit(self, city: TuiCity, W: int, H: int) -> None:
        """Fill the pane with the island: project the bounding volume at the
        current yaw, scale it to the framebuffer, center it — valid at every
        rotation step and tall enough that no roof clips the frame."""
        self.scale, self.ox, self.oy = 1.0, 0.0, 0.0
        x0, z0, x1, z1 = city.bounds
        hmax = max((b.h for b in city.buildings.values()), default=20.0)
        pts = [self.project(xx, yy, zz)
               for xx in (x0, x1) for zz in (z0, z1) for yy in (0.0, hmax)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ex, ey = max(xs) - min(xs), max(ys) - min(ys)
        self.scale = min(W * 0.96 / max(1e-6, ex), H * 0.93 / max(1e-6, ey))
        self.ox = W / 2 - (min(xs) + max(xs)) / 2 * self.scale
        self.oy = H * 0.54 - (min(ys) + max(ys)) / 2 * self.scale


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
         color: tuple[int, int, int], y0: float = 0.0,
         roof: float = 0.38) -> None:
    """One building: three visible faces, each its own shade — the roof
    reads as sunlit, the east face falls into shade."""
    c = [cam.project(x, y0, z), cam.project(x + w, y0, z),
         cam.project(x + w, y0, z + d), cam.project(x, y0, z + d)]
    t = [cam.project(x, y0 + h, z), cam.project(x + w, y0 + h, z),
         cam.project(x + w, y0 + h, z + d), cam.project(x, y0 + h, z + d)]
    _quad(fb, W, H, [c[1], c[2], t[2], t[1]], _dark(color, 0.38))   # east face
    _quad(fb, W, H, [c[2], c[3], t[3], t[2]], _dark(color, 0.10))   # south face
    _quad(fb, W, H, t, _mix(color, roof))                            # lit roof


def _face_band(fb: list[list[tuple[int, int, int] | None]], W: int, H: int,
               cam: Camera, x: float, z: float, w: float, d: float,
               y: float, color: tuple[int, int, int]) -> None:
    """A floor line on the two lit faces — tall towers get striations that
    read as storeys instead of flat slabs."""
    s = 0.55
    _quad(fb, W, H, [cam.project(x + w, y - s, z), cam.project(x + w, y - s, z + d),
                     cam.project(x + w, y + s, z + d), cam.project(x + w, y + s, z)],
          _dark(color, 0.44))
    _quad(fb, W, H, [cam.project(x, y - s, z + d), cam.project(x + w, y - s, z + d),
                     cam.project(x + w, y + s, z + d), cam.project(x, y + s, z + d)],
          _dark(color, 0.24))


def _building(fb: list[list[tuple[int, int, int] | None]], W: int, H: int,
              cam: Camera, b: "Building", color: tuple[int, int, int]) -> None:
    """One file, one silhouette — the path hash picks slab / stepped /
    twin / crowned so a district reads as many buildings, not one mass."""
    x, z = b.x - b.w / 2, b.z - b.d / 2
    v = _hash(b.path)
    style = v % 4
    h = b.h
    # every roof gets its own brightness — packed towers separate at the
    # top even when their walls touch.
    roof = 0.32 + ((v >> 8) % 97) / 97 * 0.34
    # contact shadow: a faint skirt hugging the footprint — the crisp edge
    # that keeps neighboring towers from pouring into one mass.
    _ground(fb, W, H, cam, x - 0.30, z - 0.30, x + b.w + 0.30,
            z + b.d + 0.30, _dark(DECK, 0.10), y=0.09)
    if style == 1 and h > 10:
        # stepped: street slab, then a setback tower rising out of it.
        _box(fb, W, H, cam, x, z, b.w, b.d, h * 0.62, color, roof=roof)
        _box(fb, W, H, cam, x + b.w * 0.17, z + b.d * 0.17,
             b.w * 0.66, b.d * 0.66, h * 0.38, color, y0=h * 0.62, roof=roof)
    elif style == 2:
        # twins: a lower sibling beside the main tower on the same lot.
        _box(fb, W, H, cam, x, z, b.w * 0.42, b.d * 0.9, h * 0.82, color, roof=roof)
        _box(fb, W, H, cam, x + b.w * 0.58, z + b.d * 0.05,
             b.w * 0.42, b.d * 0.9, h, color, roof=roof)
    elif style == 3 and h > 9:
        # crowned: slab with a lit penthouse crown on the roof.
        _box(fb, W, H, cam, x, z, b.w, b.d, h, color, roof=roof)
        _box(fb, W, H, cam, x + b.w * 0.30, z + b.d * 0.30,
             b.w * 0.40, b.d * 0.40, h * 0.13 + 0.8, _mix(color, 0.30), y0=h)
    else:
        _box(fb, W, H, cam, x, z, b.w, b.d, h, color, roof=roof)
    if h > 16:                       # storeys on the tall ones
        floors = min(4, int(h / 8.0))
        for f in range(1, floors + 1):
            _face_band(fb, W, H, cam, x, z, b.w, b.d,
                       h * f / (floors + 1), color)


def _ground(fb: list[list[tuple[int, int, int] | None]], W: int, H: int,
            cam: Camera, x0: float, z0: float, x1: float, z1: float,
            color: tuple[int, int, int], y: float = 0.0) -> None:
    pts = [cam.project(x0, y, z0), cam.project(x1, y, z0),
           cam.project(x1, y, z1), cam.project(x0, y, z1)]
    _quad(fb, W, H, pts, color)


def _paint(city: TuiCity, cam: Camera, W: int, H: int,
           agents: list[tuple[float, float, float, tuple[int, int, int]]],
           pulses: dict[str, float], now: float
           ) -> list[list[tuple[int, int, int] | None]]:
    """Draw the whole world into a framebuffer and return it.

    Order is the image: paper → island → block plinths → park → routes →
    shadows → painter-sorted towers + trees → agent beacons. The preview
    harness calls this directly, so the PNG always matches the terminal.
    """
    cam.fit(city, W, H)
    fb: list[list[tuple[int, int, int] | None]] = [[None] * W for _ in range(H)]

    # paper + drafting grid.
    for y in range(H):
        for x in range(W):
            fb[y][x] = GRID if (x % 10 == 0 or y % 6 == 0) else PAPER

    # the island + shore — a darker rim so the deck reads as land.
    icx, icz, ihw, ihd = city.island
    _ground(fb, W, H, cam, icx - ihw - 1.2, icz - ihd - 1.2,
            icx + ihw + 1.2, icz + ihd + 1.2, SHORE, y=-0.4)
    _ground(fb, W, H, cam, icx - ihw, icz - ihd, icx + ihw, icz + ihd, DECK)

    # block plinths — a whisper of the district hue under each block;
    # the buildings carry the identity, the ground only hints the lot.
    for d in city.districts:
        _ground(fb, W, H, cam, d.x - 1.0, d.z - 1.0, d.x + d.w + 1.0,
                d.z + d.h + 1.0, _mix(_hue(d.name), 0.90), y=0.05)

    # the commons — a green field with a worn darker edge and a pale
    # plaza at its heart, open sky all around.
    px0, pz0, px1, pz1 = city.park
    cx, cz = city.commons
    _ground(fb, W, H, cam, px0 - 0.9, pz0 - 0.9, px1 + 0.9, pz1 + 0.9,
            COMMONS_DARK, y=0.06)
    _ground(fb, W, H, cam, px0, pz0, px1, pz1, COMMONS, y=0.065)
    _ground(fb, W, H, cam, cx - 2.6, cz - 2.2, cx + 2.6, cz + 2.2,
            _mix(COMMONS, 0.62), y=0.07)

    # routes — dotted desire paths from each neighborhood to the commons.
    for d in city.districts:
        x0, z0 = d.x + d.w / 2, d.z + d.h / 2
        dist = max(abs(cx - x0), abs(cz - z0))
        steps = int(dist / 2.2)
        for s in range(steps + 1):
            gx = x0 + (cx - x0) * s / max(1, steps)
            gz = z0 + (cz - z0) * s / max(1, steps)
            _ground(fb, W, H, cam, gx - 0.4, gz - 0.4,
                    gx + 0.4, gz + 0.4, ROUTE, y=0.075)

    # shadows — every tower throws a short soft shade toward the
    # south-east; this is what grounds the buildings on the deck.
    for b in city.buildings.values():
        _ground(fb, W, H, cam,
                b.x - b.w / 2 + 0.8, b.z - b.d / 2 + 1.0,
                b.x + b.w / 2 + 0.8 + b.h * 0.30, b.z + b.d / 2 + 1.0 + b.h * 0.22,
                SHADE, y=0.08)

    # the painter pass: towers and trees, back to front by depth.
    ca, sa = math.cos(cam.yaw), math.sin(cam.yaw)
    jobs: list[tuple[float, Any]] = []
    for b in city.buildings.values():
        jobs.append((b.x * ca + b.z * sa, b))
    for tx, tz in _trees(city):
        jobs.append((tx * ca + tz * sa, (tx, tz)))
    jobs.sort(key=lambda j: j[0])
    for _, job in jobs:
        if isinstance(job, Building):
            b = job
            color = EDITED if b.edited else _tint(b)
            t = pulses.get(b.path)
            if t is not None and now - t < 2.5:
                color = _mix(color, 0.5 + 0.4 * (1 - (now - t) / 2.5))
            _building(fb, W, H, cam, b, color)
        else:
            tx, tz = job
            _box(fb, W, H, cam, tx - 0.7, tz - 0.7, 1.4, 1.4, 1.8, TREE)

    # agents — a lit beacon hovering over each one's last file.
    for ax, az, ay, color in agents:
        _box(fb, W, H, cam, ax - 0.7, az - 0.7, 1.4, 1.4, 1.4, color, y0=ay)
    return fb


def _tint(b: Building) -> tuple[int, int, int]:
    """One district hue, but every building wears it at its own lightness —
    the block reads as many towers instead of one poured mass."""
    k = ((_hash(b.path) >> 2) % 97) / 97
    tone = 0.90 + k * 0.18                      # 0.90..1.08
    c = _hue(b.district)
    return tuple(min(255, round(v * tone)) for v in c)  # type: ignore


def _trees(city: TuiCity) -> list[tuple[float, float]]:
    """Street trees on the avenue in front of each block + a grove in the
    commons — all inside the shore, never adrift on the water."""
    icx, icz, ihw, ihd = city.island
    x0, z0, x1, z1 = icx - ihw, icz - ihd, icx + ihw, icz + ihd
    trees: list[tuple[float, float]] = []
    for d in city.districts:
        n = max(2, int(d.w / 7.0))
        for i in range(n):
            tx, tz = d.x + 2.0 + i * (d.w - 3.0) / max(1, n - 1), d.z + d.h + 2.6
            if x0 + 1 < tx < x1 - 1 and z0 + 1 < tz < z1 - 1:
                trees.append((tx, tz))
    px0, pz0, px1, pz1 = city.park
    for i in range(14):
        a = i / 14 * math.pi * 2
        trees.append((city.commons[0] + math.cos(a) * (px1 - px0) * 0.40,
                      city.commons[1] + math.sin(a) * (pz1 - pz0) * 0.38))
    # a loose scattering inside the lawn too — a grove, not just a ring.
    for i in range(6):
        trees.append((px0 + (px1 - px0) * (0.2 + 0.6 * ((i * 37) % 10) / 10),
                      pz0 + (pz1 - pz0) * (0.2 + 0.6 * ((i * 53) % 10) / 10)))
    return trees


def render(city: TuiCity, cam: Camera, W: int, H: int,
           agents: list[tuple[float, float, float, tuple[int, int, int]]],
           pulses: dict[str, float], now: float) -> Text:
    """Paint the world, then emit it as `▀` cells: two framebuffer rows per
    terminal row, top pixel in the fg, bottom in the bg."""
    fb = _paint(city, cam, W, H, agents, pulses, now)
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
        x0, x1 = b.x - b.w / 2, b.x + b.w / 2
        z0, z1 = b.z - b.d / 2, b.z + b.d / 2
        pts = [cam.project(xx, yy, zz)
               for xx in (x0, x1) for zz in (z0, z1) for yy in (0.0, b.h)]
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
        else:
            for p in edited:
                if p in city.buildings:
                    city.buildings[p].edited = True

        agents_pos: list[tuple[float, float, float, tuple[int, int, int]]] = []
        focus: Building | None = None
        for i, a in enumerate(agents if isinstance(agents, list) else []):
            color = AGENT_COLORS[i % len(AGENT_COLORS)]
            files = a.get("files") or []
            path = files[-1] if files else None
            if path and path in city.buildings:
                b = city.buildings[path]
                agents_pos.append((b.x, b.z, b.h + 1.6, color))
                pulses[path] = time.time()
                focus = b
            else:
                agents_pos.append((city.commons[0] + i * 2.4 - 2,
                                   city.commons[1], 2.5, color))

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
