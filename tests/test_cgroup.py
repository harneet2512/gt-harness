
import pytest

from gt_harness.cgroup import memory_snapshot


@pytest.mark.parametrize("version", [1, 2])
def test_memory_controller_mount_and_unknown_counters(tmp_path, version):
    proc = tmp_path / "proc"
    (proc / "42").mkdir(parents=True)
    (proc / "self").mkdir()
    mount = tmp_path / "controller"
    (mount / "task").mkdir(parents=True)
    member = "0::/docker/task\n" if version == 2 else "0::/docker/task\n5:memory:/docker/task\n"
    (proc / "42" / "cgroup").write_text(member)
    filesystem = "cgroup2 cgroup2 rw" if version == 2 else "cgroup cgroup rw,memory"
    (proc / "self" / "mountinfo").write_text(f"1 0 0:1 /docker {mount.as_posix()} rw - {filesystem}\n")
    fields = ({"memory.current": "12", "memory.max": "max", "memory.peak": "24",
               "memory.events": "oom 2\noom_kill 1\n"} if version == 2 else
              {"memory.usage_in_bytes": "12", "memory.limit_in_bytes": "100",
               "memory.max_usage_in_bytes": "24", "memory.oom_control": "under_oom 0\noom_kill 1\n"})
    for name, value in fields.items():
        (mount / "task" / name).write_text(value)
    snapshot = memory_snapshot(42, proc_root=proc)
    assert snapshot["cgroup_version"] == version
    assert snapshot["current"] == 12
    assert snapshot["peak"] == 24
    assert snapshot["oom_kill"] == 1
    assert snapshot["oom"] == (2 if version == 2 else None)
    assert snapshot["max"] == (None if version == 2 else 100)
