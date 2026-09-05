import pytest

from gt_engine.feature_matrix import _disposition_from_evidence, _run_evidence


@pytest.mark.parametrize("body, expected", [
    ("assert True", "WITNESSED"),
    ("pytest.skip('unavailable')", "not_run"),
    ("pytest.xfail('unfinished')", "not_run"),
    ("assert False", "not_run"),
])
def test_real_pytest_outcomes_control_witness(tmp_path, body, expected):
    path = tmp_path / "test_probe.py"
    path.write_text(f"import pytest\ndef test_probe():\n    {body}\n", encoding="utf-8")
    evidence = _run_evidence(("test_probe.py::test_probe",), repo_root=tmp_path)
    assert _disposition_from_evidence({"positive": evidence, "negative": evidence}) == expected


def test_exit_zero_without_execution_receipt_is_not_a_witness():
    evidence = {"exit_code": 0, "node_ids": ["test_probe.py::test_probe"]}
    assert _disposition_from_evidence({"positive": evidence, "negative": evidence}) == "not_run"


@pytest.mark.parametrize("source, expected", [
    ("@pytest.mark.parametrize('value', [1, 2])\ndef test_probe(value):\n    assert value > 0\n", "WITNESSED"),
    ("@pytest.mark.parametrize('value', [1, 2])\ndef test_probe(value):\n    if value == 2: pytest.skip('missing')\n", "not_run"),
    ("@pytest.mark.xfail(strict=False)\ndef test_probe():\n    assert True\n", "not_run"),
    ("@pytest.fixture(autouse=True)\ndef fixture():\n    yield\n    assert False\ndef test_probe():\n    assert True\n", "not_run"),
])
def test_real_parameter_and_phase_boundaries(tmp_path, source, expected):
    (tmp_path / "test_probe.py").write_text("import pytest\n" + source, encoding="utf-8")
    evidence = _run_evidence(("test_probe.py::test_probe",), repo_root=tmp_path)
    assert _disposition_from_evidence({"positive": evidence, "negative": evidence}) == expected


def test_missing_requested_node_cannot_borrow_another_pass(tmp_path):
    (tmp_path / "test_probe.py").write_text("def test_probe():\n    assert True\n", encoding="utf-8")
    evidence = _run_evidence(("test_probe.py::test_probe",), repo_root=tmp_path)
    evidence["node_ids"].append("test_probe.py::test_missing")
    assert _disposition_from_evidence({"positive": evidence, "negative": evidence}) == "not_run"
