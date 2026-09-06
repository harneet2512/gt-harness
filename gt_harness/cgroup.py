"""Read the actual host memory controller on unified or hybrid Linux hosts."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def memory_snapshot(pid: int, *, proc_root: Path = Path("/proc")) -> dict[str, object]:
    memberships = {}
    for line in (proc_root / str(pid) / "cgroup").read_text(encoding="ascii").splitlines():
        _, controllers, member = line.split(":", 2)
        memberships[controllers] = member
    candidates = []
    for line in (proc_root / "self" / "mountinfo").read_text(encoding="ascii").splitlines():
        before, after = line.split(" - ", 1)
        fields, filesystem = before.split(), after.split()
        if filesystem[0] == "cgroup2" and "" in memberships:
            candidates.append((2, fields, memberships[""]))
        elif filesystem[0] == "cgroup" and "memory" in filesystem[2].split(","):
            member = next((value for key, value in memberships.items()
                           if "memory" in key.split(",")), None)
            if member is not None:
                candidates.append((1, fields, member))
    for version, fields, member in sorted(candidates, reverse=True):
        mount_root = Path(_unescape(fields[3]))
        mountpoint = Path(_unescape(fields[4])).resolve()
        try:
            relative = Path(member).relative_to(mount_root)
        except ValueError:
            continue
        directory = (mountpoint / relative).resolve()
        if directory != mountpoint and mountpoint not in directory.parents:
            raise ValueError("memory controller path escaped mount")
        usage = "memory.current" if version == 2 else "memory.usage_in_bytes"
        if not (directory / usage).is_file():
            continue

        def integer(name: str, root: Path = directory) -> int | None:
            text = (root / name).read_text(encoding="ascii").strip()
            return None if text == "max" else int(text)

        counter_file = "memory.events" if version == 2 else "memory.oom_control"
        counters = dict(line.split() for line in
                        (directory / counter_file).read_text(encoding="ascii").splitlines())
        return {
            "cgroup_version": version,
            "cgroup_path_sha256": hashlib.sha256(str(directory).encode()).hexdigest(),
            "current": integer(usage),
            "max": integer("memory.max" if version == 2 else "memory.limit_in_bytes"),
            "peak": integer("memory.peak" if version == 2 else "memory.max_usage_in_bytes"),
            "oom": int(counters["oom"]) if version == 2 and "oom" in counters else None,
            "oom_kill": int(counters["oom_kill"]) if "oom_kill" in counters else None,
        }
    raise RuntimeError("host memory controller unavailable for task process")
