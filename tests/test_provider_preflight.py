from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import provider_preflight

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "provider_route.v1.json"


def test_provider_route_is_valid_without_network(tmp_path: Path) -> None:
    receipt = provider_preflight.run(
        manifest=MANIFEST,
        output=tmp_path / "receipt.json",
        source_sha="a" * 40,
        live=False,
    )
    assert receipt["status"] == "PASS"
    assert receipt["model"] == "meta/muse-spark-1.2-contributor"
    assert receipt["provider_inference_calls"] == 0
    assert receipt["provider_inference_attempts"] == 0
    assert receipt["provider_ready"] is False
    assert receipt["account_amounts_recorded"] is False


def test_live_preflight_checks_key_limit_and_exact_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "canary-not-a-real-key")

    def fake_get(url: str, api_key: str) -> dict[str, object]:
        assert api_key == "canary-not-a-real-key"
        if url.endswith("/key"):
            return {"data": {"limit_remaining": 1}}
        return {"data": [{"id": "meta/muse-spark-1.2-contributor"}]}

    monkeypatch.setattr(provider_preflight, "_get_json", fake_get)
    monkeypatch.setattr(
        provider_preflight,
        "_post_json",
        lambda url, key, body: (
            {"choices": [{"message": {"content": "OK"}}]}
            if url.endswith("/chat/completions")
            and key == "canary-not-a-real-key"
            and body["model"] == "meta/muse-spark-1.2-contributor"
            and body["max_completion_tokens"] == 16
            and "max_tokens" not in body
            else {}
        ),
    )
    receipt = provider_preflight.run(
        manifest=MANIFEST,
        output=tmp_path / "receipt.json",
        source_sha="b" * 40,
        live=True,
    )
    assert all(receipt["checks"].values())
    assert receipt["provider_inference_calls"] == 1
    assert receipt["provider_inference_attempts"] == 1
    assert receipt["provider_ready"] is True
    assert "canary-not-a-real-key" not in json.dumps(receipt)


def test_live_preflight_fails_before_matrix_when_key_cannot_fund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        provider_preflight,
        "_get_json",
        lambda _url, _key: {"data": {"limit_remaining": 0}},
    )
    route, _ = provider_preflight.load_route(MANIFEST)
    with pytest.raises(RuntimeError, match="provider_key_cannot_fund_run"):
        provider_preflight.probe(route, "canary-not-a-real-key")


def test_live_failure_is_written_as_redacted_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "canary-not-a-real-key")
    monkeypatch.setattr(
        provider_preflight,
        "_get_json",
        lambda _url, _key: {"data": {"limit_remaining": 0}},
    )
    output = tmp_path / "receipt.json"
    receipt = provider_preflight.run(
        manifest=MANIFEST,
        output=output,
        source_sha="c" * 40,
        live=True,
    )
    assert receipt["status"] == "FAIL"
    assert receipt["error_code"] == "provider_key_cannot_fund_run"
    assert receipt["provider_inference_calls"] == 0
    assert receipt["provider_inference_attempts"] == 0
    assert "canary-not-a-real-key" not in output.read_text(encoding="utf-8")


def test_canary_http_failure_preserves_completed_checks_and_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "canary-not-a-real-key")

    def fake_get(url: str, _api_key: str) -> dict[str, object]:
        if url.endswith("/key"):
            return {"data": {"limit_remaining": 1}}
        return {"data": [{"id": "meta/muse-spark-1.2-contributor"}]}

    monkeypatch.setattr(provider_preflight, "_get_json", fake_get)
    monkeypatch.setattr(
        provider_preflight,
        "_post_json",
        lambda _url, _key, _body: (_ for _ in ()).throw(
            provider_preflight.ProviderPreflightError("provider_canary_http_400")
        ),
    )
    receipt = provider_preflight.run(
        manifest=MANIFEST,
        output=tmp_path / "receipt.json",
        source_sha="d" * 40,
        live=True,
    )
    assert receipt["status"] == "FAIL"
    assert receipt["error_code"] == "provider_canary_http_400"
    assert receipt["checks"] == {
        "credential_valid": True,
        "key_limit_available": True,
        "model_visible": True,
        "model_canary_served": False,
    }
    assert receipt["provider_inference_attempts"] == 1
    assert receipt["provider_inference_calls"] == 0
