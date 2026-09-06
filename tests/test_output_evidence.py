import base64
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment


def test_environment_recovers_fact_beyond_preview(tmp_path):
    script = tmp_path / "emit.py"
    payload = b"x" * 25000 + b"\nNEEDED_FACT=731\n" + b"y" * 25000
    script.write_text(f"import sys; sys.stdout.buffer.write({payload!r})")
    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=5)
    result = env.execute({"command": f'"{sys.executable}" "{script}"'})
    assert "gt-evidence read" in result["output"]
    assert "NEEDED_FACT" not in result["output"]
    ref = result["extra"]["output_artifact"]
    assert ref["sha256"] == hashlib.sha256(payload).hexdigest()
    assert "raw_output" not in result["extra"]
    from gt_engine.output_evidence import EvidenceStore
    store = EvidenceStore(ref["root"])
    page = store.read(ref["sha256"], 25000, 100)
    assert "NEEDED_FACT=731" in page["text"]
    rebuilt = bytearray()
    offset = 0
    while True:
        page = store.read(ref["sha256"], offset, 8192)
        rebuilt.extend(page["text"].encode() if page["encoding"] == "utf-8"
                       else base64.b64decode(page["base64"]))
        if page["continuation_offset"] is None:
            break
        offset = page["continuation_offset"]
    assert bytes(rebuilt) == payload


def test_binary_pages_cli_and_corruption(tmp_path):
    from gt_engine.output_evidence import EvidenceStore
    store = EvidenceStore(tmp_path / "evidence")
    source = tmp_path / "raw"
    source.write_bytes(b"\xff\xfe" + b"a" * 9000)
    ref = store.publish(source)
    page = store.read(ref["sha256"], 0, 8192)
    assert page["encoding"] == "base64"
    assert len(base64.b64decode(page["base64"])) == 8192
    with pytest.raises(ValueError):
        store.read(ref["sha256"], 0, 8193)
    proc = subprocess.run([sys.executable, "-m", "gt_engine.output_evidence", "read",
                           ref["sha256"], "0", "8192"],
                          env=os.environ | {"GT_EVIDENCE_ROOT": str(store.root)},
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == page
    store.path(ref["sha256"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="digest"):
        store.read(ref["sha256"], 0, 1)


def test_timeout_preserves_actual_bytes(tmp_path):
    script = tmp_path / "wait.py"
    script.write_text("import sys,time; sys.stdout.buffer.write(b'partial\\xff'); sys.stdout.flush(); time.sleep(30)")
    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=1)
    result = env.execute({"command": f'"{sys.executable}" "{script}"'})
    assert result["extra"]["timed_out"] is True
    from gt_engine.output_evidence import EvidenceStore
    ref = result["extra"]["output_artifact"]
    assert EvidenceStore(ref["root"]).bytes(ref["sha256"]) == b"partial\xff"
    assert result["returncode"] != 0


def test_task_scripts_package_cannot_shadow_installed_command_worker(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "__init__.py").write_text("raise RuntimeError('task code imported by harness')")
    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=5)
    result = env.execute({"command": "echo actual-command"})
    assert result["returncode"] == 0
    assert result["output"].strip() == "actual-command"


def test_relative_command_workspace_is_resolved_once(tmp_path, monkeypatch):
    (tmp_path / "task").mkdir()
    monkeypatch.chdir(tmp_path)
    env = CredentialIsolatedLocalEnvironment(cwd="task", timeout=5)
    result = env.execute({"command": "echo edited > actual-edit.txt"})
    assert result["returncode"] == 0
    assert (tmp_path / "task" / "actual-edit.txt").read_text().strip() == "edited"


@pytest.mark.skipif(os.name != "posix", reason="production Linux process ownership")
def test_detached_stdout_is_captured_before_immutable_publication(tmp_path):
    from gt_engine.output_evidence import EvidenceStore
    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=3)
    result = env.execute({"command": "setsid sh -c 'sleep 0.2; printf LATE' &"})
    assert result["returncode"] == 0
    assert result["output"] == "LATE"
    reference = result["extra"]["output_artifact"]
    assert EvidenceStore(reference["root"]).bytes(reference["sha256"]) == b"LATE"


@pytest.mark.skipif(os.name != "posix", reason="production Linux native background semantics")
def test_redirected_background_service_survives_successful_action(tmp_path):
    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=3)
    result = env.execute({"command": "sh -c 'sleep 0.2; echo ready > service-ready' >/dev/null 2>&1 & echo launched"})
    assert result["returncode"] == 0
    assert result["output"].strip() == "launched"
    deadline = time.monotonic() + 3
    while not (tmp_path / "service-ready").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (tmp_path / "service-ready").read_text().strip() == "ready"


@pytest.mark.skipif(shutil.which("gt-evidence") is None, reason="installed wheel CLI required")
def test_installed_bash_page_is_complete_and_drives_next_edit(tmp_path):
    script = tmp_path / "emit.py"
    script.write_text("import sys; sys.stdout.write('x'*25000+'NEEDED_FACT=731\\n'+'y'*25000)")
    env = CredentialIsolatedLocalEnvironment(cwd=str(tmp_path), timeout=5)
    observation = env.execute({"command": f'"{sys.executable}" "{script}"'})
    ref = observation["extra"]["output_artifact"]
    recovered = env.execute({"command": f"gt-evidence read {ref['sha256']} 25000 8192"})
    assert recovered["returncode"] == 0
    page = json.loads(recovered["output"])
    assert page["returned_length"] == 8192
    value = int(page["text"].splitlines()[0].split("=")[1])
    edit = tmp_path / "edit.py"
    edit.write_text(f"from pathlib import Path; Path('answer.txt').write_text('{value}')")
    result = env.execute({"command": f'"{sys.executable}" "{edit}"'})
    assert result["returncode"] == 0
    assert (tmp_path / "answer.txt").read_text() == "731"


def test_execution_evidence_uses_original_binary_output():
    from gt_engine.runtime_observation import compile_execution_evidence
    raw = b"FAIL\xff"
    result = compile_execution_evidence(
        command="pytest", output=raw.decode("utf-8", "replace"), raw_output=raw,
        returncode=-9, timed_out=True, action_id=1, repository_revision="source",
    )
    assert result.raw_output_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.outcome == "timeout"


def test_execution_journal_references_the_capture_blob_without_copy(tmp_path):
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.output_evidence import EvidenceStore
    from gt_engine.runtime_observation import compile_execution_evidence
    adapter = MiniSweAdapter(task_id="task", state_dir=tmp_path, predicates=[])
    store = EvidenceStore(adapter.store.root / "output_evidence")
    spool = store.root / "pending"
    spool.write_bytes(b"FAIL\xff")
    ref = store.publish(spool)
    evidence = compile_execution_evidence(
        command="pytest", output="FAIL�", raw_output=b"FAIL\xff", returncode=1,
        action_id=1, repository_revision="source",
        output_artifact_path=str(store.path(ref["sha256"])),
    )
    adapter.record_execution_evidence(evidence)
    row = json.loads(adapter.store.path.read_text().splitlines()[-1])
    assert row["raw_blob"] == "output_evidence/" + ref["sha256"]
    assert not (adapter.store.root / "raw_execution_output").exists()


def test_transport_preview_does_not_replace_complete_gt_analysis(tmp_path):
    from gt_engine.miniswe_runtime import _observation_output
    from gt_engine.output_evidence import EvidenceStore
    from gt_engine.runtime_observation import compile_execution_evidence

    store = EvidenceStore(tmp_path / "evidence")
    source = tmp_path / "stdout"
    complete = "progress\n" * 5000 + "25 passed in 1.00s\n"
    source.write_bytes(complete.encode())
    reference = store.publish(source)
    result = {"output": store.preview(reference), "returncode": 0,
              "extra": {"output_artifact": reference}}
    assert "25 passed" not in result["output"]
    analyzed = _observation_output(result)
    assert analyzed == complete
    verification = compile_execution_evidence(
        command="pytest", output=analyzed, returncode=0, action_id=1,
        repository_revision="source", output_artifact=reference,
        output_artifact_path=str(store.path(reference["sha256"])),
    )
    assert verification.outcome == "pass"


def test_hidden_failure_keeps_canonical_precedence_over_earlier_pass(tmp_path):
    from gt_engine.miniswe_runtime import _observation_output
    from gt_engine.output_evidence import EvidenceStore
    from gt_engine.runtime_observation import compile_execution_evidence

    store = EvidenceStore(tmp_path / "evidence")
    source = tmp_path / "stdout"
    complete = "25 passed\n" + "progress\n" * 5000 + "1 failed\n"
    source.write_bytes(complete.encode())
    reference = store.publish(source)
    result = {"output": store.preview(reference), "returncode": 0,
              "extra": {"output_artifact": reference}}
    analyzed = _observation_output(result)
    assert "1 failed" in analyzed
    verification = compile_execution_evidence(
        command="pytest", output=analyzed, returncode=0, action_id=1,
        repository_revision="source", output_artifact=reference,
        output_artifact_path=str(store.path(reference["sha256"])),
    )
    assert verification.outcome == "fail"


def test_artifact_analysis_streams_complete_output_without_bytes_load(
    tmp_path, monkeypatch
):
    from gt_engine.output_evidence import EvidenceStore
    from gt_engine.runtime_observation import (
        classify_execution_outcome,
        compile_execution_evidence,
    )

    store = EvidenceStore(tmp_path / "evidence")
    source = tmp_path / "stdout"
    source.write_bytes(b"25 passed\n" + b"x" * 2_000_000 + b"\n1 failed\n")
    reference = store.publish(source)
    preview = store.preview(reference)
    monkeypatch.setattr(
        EvidenceStore,
        "bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("complete artifact must not be loaded into memory")
        ),
    )

    evidence = compile_execution_evidence(
        command="pytest",
        output=preview,
        returncode=1,
        action_id=1,
        repository_revision="source",
        output_artifact=reference,
        output_artifact_path=str(store.path(reference["sha256"])),
    )

    assert evidence is not None
    assert evidence.outcome == "fail"
    assert evidence.raw_output is None
    assert evidence.raw_output_sha256 == reference["sha256"]
    assert classify_execution_outcome(
        "pytest", preview, 1, output_artifact=reference
    ) == "fail"


def test_artifact_stream_corrupt_tail_fails_before_evidence_is_returned(tmp_path):
    from gt_engine.output_evidence import EvidenceStore
    from gt_engine.runtime_observation import (
        classify_execution_outcome,
        compile_execution_evidence,
    )

    store = EvidenceStore(tmp_path / "evidence")
    source = tmp_path / "stdout"
    source.write_bytes(b"25 passed\n" + b"x" * 100_000)
    reference = store.publish(source)
    with store.path(reference["sha256"]).open("r+b") as corrupt:
        corrupt.seek(-1, os.SEEK_END)
        corrupt.write(b"y")

    with pytest.raises(ValueError, match="artifact digest mismatch"):
        compile_execution_evidence(
            command="pytest",
            output="25 passed\n[preview]",
            returncode=0,
            action_id=1,
            repository_revision="source",
            output_artifact=reference,
            output_artifact_path=str(store.path(reference["sha256"])),
        )
    with pytest.raises(ValueError, match="artifact digest mismatch"):
        classify_execution_outcome(
            "pytest", "25 passed\n[preview]", 124, output_artifact=reference
        )


def test_early_classifier_return_still_validates_artifact_tail(tmp_path, monkeypatch):
    import groundtruth.runtime.patterns as patterns

    from gt_engine.output_evidence import EvidenceStore
    from gt_engine.runtime_observation import compile_execution_evidence

    store = EvidenceStore(tmp_path / "evidence")
    source = tmp_path / "stdout"
    source.write_bytes(b"25 passed\n" + b"x" * 100_000)
    reference = store.publish(source)
    with store.path(reference["sha256"]).open("r+b") as corrupt:
        corrupt.seek(-1, os.SEEK_END)
        corrupt.write(b"y")

    def early_result(command, chunks, returncode, **kwargs):
        next(iter(chunks))
        return "pass", "command"

    monkeypatch.setattr(patterns, "classify_test_observation_stream", early_result)
    with pytest.raises(ValueError, match="artifact digest mismatch"):
        compile_execution_evidence(
            command="pytest",
            output="25 passed\n[preview]",
            returncode=0,
            action_id=1,
            repository_revision="source",
            output_artifact=reference,
        )


@pytest.mark.skipif(os.name != "posix", reason="production Linux signal boundary")
def test_real_interruption_preserves_capture_and_propagates(tmp_path):
    worker = tmp_path / "worker.py"
    evidence = tmp_path / "state"
    worker.write_text(
        "from scripts.miniswe_gt_run import CredentialIsolatedLocalEnvironment, _install_termination_guard\n"
        "_install_termination_guard()\n"
        f"env = CredentialIsolatedLocalEnvironment(cwd={str(tmp_path)!r}, evidence_root={str(evidence)!r})\n"
        "env.execute({'command': 'echo partial; echo repaired > repair.txt; sleep 60'})\n"
        "raise AssertionError('interruption was swallowed')\n"
    )
    with (tmp_path / "worker.log").open("wb") as log:
        child = subprocess.Popen([sys.executable, str(worker)], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while not (tmp_path / "repair.txt").exists() and time.monotonic() < deadline:
                if child.poll() is not None:
                    pytest.fail((tmp_path / "worker.log").read_text())
                time.sleep(0.05)
            assert (tmp_path / "repair.txt").read_text().strip() == "repaired"
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert child.returncode != 0
    receipt, = evidence.glob("*.receipt.json")
    terminal = json.loads(receipt.read_text())
    assert terminal["status"] == "interrupted"
    assert terminal["returncode"] != 0
    from gt_engine.output_evidence import EvidenceStore
    assert EvidenceStore(evidence).bytes(terminal["output_artifact"]["sha256"]) == b"partial\n"
    assert "RunnerTerminationRequested" in (tmp_path / "worker.log").read_text()
