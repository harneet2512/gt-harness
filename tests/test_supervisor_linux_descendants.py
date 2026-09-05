"""Real process ownership tests; never emulate Linux process groups on Windows."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux subreaper/procfs")


@pytest.mark.parametrize("termination", ["deadline", "signal"])
def test_setsid_descendant_reaped_before_finalization(tmp_path, termination):
    marker = tmp_path / "descendant.json"
    final = tmp_path / "final.json"
    child_source = (
        "import os,signal,time,json,pathlib,sys\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'parent':os.getppid()}))\n"
        "while True: time.sleep(.1)\n"
    )
    worker_source = (
        "import subprocess,sys,time,pathlib\n"
        f"subprocess.Popen([sys.executable,'-c',{child_source!r},sys.argv[1]],start_new_session=True)\n"
        "pathlib.Path(sys.argv[2]).write_text('last real edit')\n"
        "while True: time.sleep(.1)\n"
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import json,sys,time,pathlib\n"
        "from scripts.miniswe_supervisor import supervise,_enable_linux_subreaper,_reap_owned_children\n"
        "_enable_linux_subreaper()\n"
        f"result=supervise([sys.executable,'-c',{worker_source!r},sys.argv[1],sys.argv[2]],"
        "deadline=time.monotonic()+4,termination_grace_seconds=.1)\n"
        "_reap_owned_children()\n"
        "pid=json.loads(pathlib.Path(sys.argv[1]).read_text())['pid']\n"
        "pathlib.Path(sys.argv[3]).write_text(json.dumps({'reason':result.reason,"
        "'descendant_gone':not pathlib.Path('/proc',str(pid)).exists(),"
        "'edit':pathlib.Path(sys.argv[2]).read_text()}))\n",
        encoding="utf-8",
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    process = subprocess.Popen(
        [sys.executable, str(driver), str(marker), str(tmp_path / "edit"), str(final)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    descendant = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), "worker failed before creating real descendant"
        descendant = json.loads(marker.read_text())["pid"]
        if termination == "signal":
            process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=12)
        assert process.returncode == 0, (stdout, stderr)
        result = json.loads(final.read_text())
        assert result["descendant_gone"] is True
        assert result["edit"] == "last real edit"
        assert result["reason"] == (
            "deadline_exceeded" if termination == "deadline" else "supervisor_termination")
        assert unrelated.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        # Only this test's positively identified descendant may need cleanup.
        if descendant is not None and Path("/proc", str(descendant)).exists():
            os.kill(descendant, signal.SIGKILL)
        unrelated.terminate()
        unrelated.wait(timeout=5)
