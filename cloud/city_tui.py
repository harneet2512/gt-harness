"""The city, in the terminal.

`python -m cloud.city_tui <session-id>` renders the same repository-city
the web canvas draws — districts as neighborhoods, buildings shaded by
real file metrics, agents as moving glyphs, the commons at the middle —
in a muted terminal palette with no browser and no GPU.

Four panes, one session:

  left   — the roster: primary + workers, what each is doing
  center — the city:   one island, neighborhoods by color, agents moving
  right  — the inspector: the last file an agent touched, its detail
  bottom — the feed:   the session's own event stream, newest first

Everything is polled; nothing is pushed. It is the honest view: a file
the agent touches lights once and settles, an edit keeps its mark.

Layout: `rich` is already a dependency; the city renders into a
character grid (one cell per file), districts packed shelf-wise like
city blocks with one-column avenues between them.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
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
# palette — muted, terminal-native, never loud
# ------------------------------------------------------------------ #

#: ten desaturated district hues; the district's name picks one by hash.
DISTRICT_HUES = [
    "#6d7f96",  # steel
    "#8a7fa8",  # mauve
    "#7d9078",  # sage
    "#a08c6f",  # sand
    "#6f908a",  # teal
    "#a07d8a",  # rose
    "#7a8ba0",  # slate blue
    "#94866a",  # olive
    "#8b8398",  # violet-grey
    "#6e8a7d",  # moss
]
#: height shading: a quiet step-ramp on the district hue.
SHADE = ["#5b6570", "#79848f", "#97a2ac"]  # dim → mid → lit
COMMONS = "#5f7a5f"
ROUTE = "#3d4750"
EDITED = "#b08a4f"
ACTIVE = "#e3d5a2"
INK = "#a8adb4"
DIM = "#6b7077"
AGENT_COLORS = ["#c9a06b", "#8fb0c9", "#b093c9", "#8fc9a6", "#c98fa0", "#a8c98f"]


def _hash(text: str) -> int:
    """A stable identity hash — districts keep their color across runs."""
    h = 0
    for ch in text:
        h = (h * 31 + ord(ch)) & 0x7FFFFFFF
    return h


def _hue(name: str) -> str:
    return DISTRICT_HUES[_hash(name) % len(DISTRICT_HUES)]


# ------------------------------------------------------------------ #
# http — operator-local, mints from the same secret the server reads
# ------------------------------------------------------------------ #


def _env_secret() -> str:
    """The server's JWT secret — from the environment, or the same
    ``cloud/.env`` the operator sources before launching the server."""
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
    """Mint a session JWT in-process. This runs where the server runs —
    same ``JWT_SECRET`` the link-token path uses."""
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
    except urllib.error.HTTPError as e:
        return {"__error__": e.code}
    except Exception as e:  # noqa: BLE001 — a TUI degrades, it does not die
        return {"__error__": str(e)}


# ------------------------------------------------------------------ #
# the city, terminal edition
# ------------------------------------------------------------------ #


@dataclass
class Building:
    path: str
    size: int
    district: str
    shade: int          # 0..2 — the size ramp
    edited: bool = False
    cx: int = 0         # canvas cell
    cz: int = 0
    touched_at: float = 0.0


@dataclass
class District:
    name: str
    files: list[Building] = field(default_factory=list)
    x: int = 0
    z: int = 0
    w: int = 0
    h: int = 0


@dataclass
class TuiCity:
    """The whole renderable state: buildings on a cell grid, agents on it."""
    districts: list[District]
    buildings: dict[str, Building]      # path -> Building
    cells: dict[tuple[int, int], str]   # (x,z) -> building path
    commons: tuple[int, int]            # the park's anchor cell
    bounds: tuple[int, int, int, int]   # min_x, min_z, w, h


def _shelf_layout(districts: list[District], total_w: int) -> None:
    """Pack districts left-to-right, wrapping — city blocks on one island.
    Each district is a rectangle; one column of avenue between them."""
    x = z = 0
    row_h = 0
    for d in districts:
        cols = max(1, math.ceil(math.sqrt(len(d.files))))
        rows = max(1, math.ceil(len(d.files) / cols))
        if x + cols > total_w:
            x = 0
            z += row_h + 2          # a two-line avenue between blocks
            row_h = 0
        d.x, d.z, d.w, d.h = x, z, cols, rows
        row_h = max(row_h, rows)
        x += cols + 1               # a one-column avenue


def build_city(graph: dict[str, Any], edited: set[str], width: int) -> TuiCity:
    nodes = graph.get("nodes") or []
    by_dir: dict[str, list[Building]] = {}
    sizes = [n.get("size", 0) for n in nodes]
    hi = max(sizes) if sizes else 1
    for n in nodes:
        path = n.get("path") or n.get("id") or ""
        if not path:
            continue
        name = n.get("dir") or "root"
        # log-scale: files span orders of magnitude; a linear ramp parks
        # nearly everything in the bottom bucket.
        frac = math.log1p(n.get("size", 0)) / math.log1p(max(1, hi))
        shade = 0 if frac < 0.35 else 1 if frac < 0.72 else 2
        b = Building(path=path, size=n.get("size", 0), district=name,
                     shade=shade, edited=path in edited)
        by_dir.setdefault(name, []).append(b)
    districts = [District(name=k, files=sorted(v, key=lambda b: -b.size))
                 for k, v in sorted(by_dir.items(), key=lambda kv: -len(kv[1]))]
    _shelf_layout(districts, max(22, int(width * 0.52)))

    cells: dict[tuple[int, int], str] = {}
    buildings: dict[str, Building] = {}
    max_z = 0
    max_x = 0
    for d in districts:
        for i, b in enumerate(d.files):
            b.cx = d.x + (i % d.w)
            b.cz = d.z + (i // d.w)
            cells[(b.cx, b.cz)] = b.path
            buildings[b.path] = b
        max_z = max(max_z, d.z + d.h)
        max_x = max(max_x, d.x + d.w)
    commons = (max_x // 2, max_z + 3)
    return TuiCity(districts, buildings, cells, commons, (0, 0, max_x, max_z + 6))


# ------------------------------------------------------------------ #
# frame
# ------------------------------------------------------------------ #


def _city_panel(city: TuiCity, agents_pos: list[tuple[int, int, str, str]],
                pulses: dict[str, float], width: int) -> Text:
    """Draw the island into a Text grid. One char per building; height is
    the shade ramp, edits are amber, an agent is a ◆ in its own color,
    a fresh touch is a lit cell that settles."""
    min_x, min_z, w, h = city.bounds
    grid = [[" "] * (w + 2) for _ in range(h + 6)]
    styles: dict[tuple[int, int], str] = {}
    now = time.time()

    # avenues stay blank; the commons is a green scatter of trees.
    cx, cz = city.commons
    for dx in range(-6, 7):
        for dz in range(-2, 3):
            gx, gz = cx + dx, cz + dz
            if 0 <= gz < len(grid) and 0 <= gx < len(grid[0]):
                grid[gz][gx] = "∙" if (dx * dx + dz * dz) % 3 else "·"
                styles[(gx, gz)] = COMMONS if grid[gz][gx] == "∙" else ROUTE

    # routes: dotted lines from each district's middle to the commons.
    for d in city.districts:
        x0, z0 = d.x + d.w // 2, d.z + d.h // 2
        steps = max(abs(cx - x0), abs(cz - z0), 1)
        for s in range(steps + 1):
            gx = x0 + round((cx - x0) * s / steps)
            gz = z0 + round((cz - z0) * s / steps)
            if 0 <= gz < len(grid) and 0 <= gx < len(grid[0]) and grid[gz][gx] == " ":
                grid[gz][gx] = "·"
                styles[(gx, gz)] = ROUTE

    # the buildings — size is texture: small files breathe, large ones stand.
    RAMP = ["░", "▒", "▓"]
    for (gx, gz), path in city.cells.items():
        b = city.buildings[path]
        if b.edited:
            grid[gz][gx], styles[(gx, gz)] = "▓", EDITED
        else:
            grid[gz][gx] = RAMP[b.shade]
            styles[(gx, gz)] = _hue(b.district)
        t = pulses.get(path)
        if t is not None and now - t < 2.5:
            styles[(gx, gz)] = ACTIVE
            grid[gz][gx] = "█"

    # the agents: a ◆ where each one last worked.
    for ax, az, color, _name in agents_pos:
        if 0 <= az < len(grid) and 0 <= ax < len(grid[0]):
            grid[az][ax], styles[(ax, az)] = "◆", f"bold {color}"

    out = Text()
    for z_i, row in enumerate(grid):
        for x_i, ch in enumerate(row):
            out.append(ch, style=styles.get((x_i, z_i), ""))
        out.append("\n")
    return out


def _roster_panel(agents: list[dict[str, Any]]) -> Text:
    out = Text()
    out.append("agents\n", style=f"bold {INK}")
    out.append("\n")
    for i, a in enumerate(agents):
        color = AGENT_COLORS[i % len(AGENT_COLORS)]
        name = a.get("label") or a.get("task") or a.get("id", "?")[:14]
        state = a.get("status", "")
        out.append(f" ◆ {name[:18]}\n", style=f"{color}")
        out.append(f"   {state}", style=DIM)
        act = (a.get("activity") or "")[:16]
        if act:
            out.append(f" · {act}", style=DIM)
        out.append("\n")
    return out


def _inspector_panel(city: TuiCity, focus: str | None, session: dict[str, Any]) -> Text:
    out = Text()
    out.append("inspector\n\n", style=f"bold {INK}")
    if focus and focus in city.buildings:
        b = city.buildings[focus]
        out.append(f"{b.path}\n\n", style=INK)
        out.append(f"size      {b.size} B\n", style=DIM)
        out.append(f"district  {b.district}\n", style=DIM)
        out.append(f"height    {'▁▂▃▅▆█'[b.shade * 2]}\n", style=DIM)
        if b.edited:
            out.append("state     edited\n", style=EDITED)
    else:
        out.append("no file in focus\n", style=DIM)
    out.append("\n")
    out.append(f"status   {session.get('status', '?')}\n", style=DIM)
    out.append(f"turns    {session.get('turns', 0)}\n", style=DIM)
    out.append(f"steps    {session.get('steps', 0)}\n", style=DIM)
    return out


def _feed_panel(messages: list[dict[str, Any]]) -> Text:
    out = Text()
    for m in messages[-6:]:
        role = m.get("role", "?")
        text = (m.get("content") or "").split("\n")[0][:78]
        style = DIM if role in ("assistant",) else INK
        mark = "›" if role == "agent" else "·"
        out.append(f"{mark} {text}\n", style=style)
    return out


# ------------------------------------------------------------------ #
# main loop
# ------------------------------------------------------------------ #


def run(session_id: str, host: str, login: str) -> None:
    console = Console()
    token = _token(login)
    city: TuiCity | None = None
    pulses: dict[str, float] = {}
    seen_steps: set[str] = set()

    def frame() -> Layout:
        nonlocal city
        session = _get(host, token, f"/api/sessions/{session_id}")
        if isinstance(session, dict) and session.get("__error__"):
            console.print(f"[red]cannot reach session: {session['__error__']}[/red]")
            raise SystemExit(2)
        agents = _get(host, token, f"/api/sessions/{session_id}/agents") or []
        messages = _get(host, token, f"/api/sessions/{session_id}/messages") or []
        diff = _get(host, token, f"/api/sessions/{session_id}/diff") or {}
        graph = _get(host, token, f"/api/sessions/{session_id}/graph") or {}

        edited = {f["path"] for f in diff.get("files", []) if isinstance(f, dict)}
        if city is None or len(city.buildings) != len(graph.get("nodes", [])):
            city = build_city(graph, edited, max(40, console.size.width - 34))
        else:
            for p in edited:
                if p in city.buildings:
                    city.buildings[p].edited = True

        # agent positions: last touched file → its cell; idle → the commons.
        agents_pos: list[tuple[int, int, str, str]] = []
        focus: str | None = None
        for i, a in enumerate(agents if isinstance(agents, list) else []):
            color = AGENT_COLORS[i % len(AGENT_COLORS)]
            files = a.get("files") or []
            path = files[-1] if files else None
            if path and path in city.buildings:
                b = city.buildings[path]
                agents_pos.append((b.cx, b.cz, color, a.get("label") or ""))
                pulses[path] = time.time()
                focus = path
            else:
                cx, cz = city.commons
                agents_pos.append((cx + i * 3 - 4, cz, color, a.get("label") or ""))

        lay = Layout()
        lay.split_column(
            Layout(name="body", ratio=1),
            Layout(name="feed", size=8),
        )
        lay["body"].split_row(
            Layout(Panel(_roster_panel(agents if isinstance(agents, list) else []),
                         border_style=DIM, title="roster"), size=22),
            Layout(Panel(_city_panel(city, agents_pos, pulses,
                                     console.size.width),
                         border_style=DIM,
                         title=f"{session.get('repo','?')}@{session.get('ref','')}"),
                   ratio=1),
            Layout(Panel(_inspector_panel(city, focus, session),
                         border_style=DIM, title="inspector"), size=24),
        )
        lay["feed"].update(
            Panel(_feed_panel(messages if isinstance(messages, list) else []),
                  border_style=DIM, title="feed"))
        return lay

    with Live(frame(), console=console, refresh_per_second=2, screen=True) as live:
        while True:
            time.sleep(0.5)
            live.update(frame())


def main() -> None:
    ap = argparse.ArgumentParser(description="The session's city, in the terminal.")
    ap.add_argument("session_id")
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    ap.add_argument("--login", default="harneet2512")
    args = ap.parse_args()
    run(args.session_id, args.host, args.login)


if __name__ == "__main__":
    main()
