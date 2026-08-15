import json

from scripts.central_effect_audit import audit


def test_effect_audit_accepts_terminal_dispositions(tmp_path):
    path = tmp_path / "central_receipt.json"
    path.write_text(
        json.dumps(
            {
                "features": {
                    "effect_trace": [
                        {
                            "effect_id": "receipt-1",
                            "feature_id": "syntax_result",
                            "disposition": "provider_payload",
                        },
                        {
                            "effect_id": "receipt-2",
                            "feature_id": "GT_PATCH_DELTA",
                            "disposition": "audit_only",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    result = audit(path)
    assert result["valid"] is True
    assert result["provider_payload_effects"] == 1
    assert result["audit_only_effects"] == 1


def test_effect_audit_rejects_unknown_disposition(tmp_path):
    path = tmp_path / "central_receipt.json"
    path.write_text(
        json.dumps(
            {
                "features": {
                    "effect_trace": [
                        {
                            "effect_id": "receipt-1",
                            "feature_id": "syntax_result",
                            "disposition": "unknown",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    result = audit(path)
    assert result["valid"] is False
    assert result["unknown_effects"] == ["receipt-1"]
