from __future__ import annotations

from pathlib import Path

import pytest

from eval.miniswe_agent import _REMOTE_DENSE_MODEL_DIR, MiniSweAgent


def test_absent_model_is_a_setup_error(monkeypatch):
    """The embedder is mandatory capability, not an optional extra.

    This asserted the resolver returned None, and install() then skipped the
    upload, so a run could retrieve with no embedder and still report a
    normal result - GT measured with a capability switched off and nothing in
    the record saying so.
    """
    monkeypatch.delenv("GT_DENSE_MODEL_DIR", raising=False)
    with pytest.raises(FileNotFoundError, match="GT_DENSE_MODEL_DIR is required"):
        MiniSweAgent._dense_model_host()


def test_provisioned_model_resolves_on_the_host(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / "snowflake-arctic-embed-m"
    model_dir.mkdir()
    (model_dir / "model.onnx").write_bytes(b"onnx")
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(model_dir))

    assert MiniSweAgent._dense_model_host() == model_dir


def test_provisioned_but_missing_model_fails_loudly(monkeypatch, tmp_path: Path):
    """A configured model that is not there is a setup fault, not a silent skip."""

    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(tmp_path / "absent"))
    with pytest.raises(FileNotFoundError):
        MiniSweAgent._dense_model_host()


def test_runtime_reads_a_container_path_not_a_host_path():
    """HAR-81: the workflow exports a host path, GT reads it inside the task.

    Run 33708231670 recorded dense_index_ready query_ready=false with
    reason "KeyError:'GT_DENSE_MODEL_DIR'", because the host variable never
    crossed into the task environment.
    """

    assert _REMOTE_DENSE_MODEL_DIR.startswith("/installed-agent/")
