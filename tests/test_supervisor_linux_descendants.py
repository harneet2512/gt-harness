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


@pytest.mark.parametrize("termination", ["deadline", "signal"])
def test_main_conserves_killed_worker_patch_and_receipts(tmp_path, termination):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "GT Test", "GIT_COMMITTER_NAME": "GT Test",
           "GIT_AUTHOR_EMAIL": "gt-test@example.invalid",
           "GIT_COMMITTER_EMAIL": "gt-test@example.invalid"}
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True,
                              check=True).stdout
    git("init")
    (repo / "a.py").write_text("x = 1\n")
    git("add", ".")
    git("-c", "core.hooksPath=", "commit", "-m", "fixture")
    marker = tmp_path / "descendant"
    state = tmp_path / "state"
    state.mkdir()
    journal = state / "events.jsonl"
    journal.write_text('{"fixture":"real worker input"}\n')
    child = (
        "import os,signal,time,pathlib\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "while True: time.sleep(.1)\n"
    )
    worker = (
        "import subprocess,sys,time,pathlib\n"
        f"pathlib.Path({str(repo / 'a.py')!r}).write_text('x = 2\\n')\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True)\n"
        "while True: time.sleep(.1)\n"
    )
    driver = tmp_path / "main_driver.py"
    driver.write_text(
        "import sys\nfrom scripts import miniswe_supervisor as s\n"
        "original=s.supervise\n"
        # Only the test worker is substituted; main, teardown, Git export and
        # receipt issuance execute unchanged. No provider is needed for a hang.
        "def supervise(command,**kwargs):\n"
        f"    return original([sys.executable,'-c',{worker!r}],"
        "deadline=kwargs['deadline'],termination_grace_seconds=.1)\n"
        "s.supervise=supervise\nraise SystemExit(s.main())\n"
    )
    process = subprocess.Popen([
        sys.executable, str(driver), "--cwd", str(repo), "--state-dir", str(state),
        "--time-budget-seconds", "4", "--task-id", "fixture", "--model", "fixture/model",
        "--metrics", str(tmp_path / "report.json"), "--patch-output", str(tmp_path / "model.patch"),
        "--product-receipt", str(tmp_path / "product.json"),
        "--adapter-receipt", str(tmp_path / "adapter.json"),
    ], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    descendant = None
    try:
        limit = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < limit:
            time.sleep(.02)
        assert marker.exists()
        descendant = int(marker.read_text())
        if termination == "signal":
            process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 3, (stdout, stderr)
        assert not Path("/proc", str(descendant)).exists()
        assert "+x = 2" in (tmp_path / "model.patch").read_text()
        assert git("diff", "--cached") == b""
        report = json.loads((tmp_path / "report.json").read_text())
        import hashlib
        assert report["supervisor"]["conserved_journals"] == [{
            "path": "events.jsonl", "sha256": hashlib.sha256(journal.read_bytes()).hexdigest(),
            "bytes": len(journal.read_bytes()),
        }]
        for name in ("product.json", "adapter.json"):
            receipt = json.loads((tmp_path / name).read_text())
            assert receipt["status"] == "ERROR"
        product = json.loads((tmp_path / "product.json").read_text())
        assert product["provider_calls"] is None
        assert product["research_valid"] is False
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if descendant is not None and Path("/proc", str(descendant)).exists():
            os.kill(descendant, signal.SIGKILL)
