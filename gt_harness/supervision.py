"""Fail-closed finalization for product processes killed outside Python."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_TERMINATION_KINDS = {
    "TIMEOUT",
    "CANCELLED",
    "PROCESS_EXIT",
    "PROVIDER_TRANSPORT",
    "PRODUCT_ERROR",
}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _trajectory_usage(path: Path) -> tuple[int | None, int, int, int]:
    if not path.is_file():
        return None, 0, 0, 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    info = payload.get("info") if isinstance(payload, dict) else None
    stats = info.get("model_stats") if isinstance(info, dict) else None
    calls = stats.get("api_calls") if isinstance(stats, dict) else None
    provider_calls = int(calls) if calls is not None else None
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    messages = payload.get("messages") if isinstance(payload, dict) else None
    for message in messages if isinstance(messages, list) else ():
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        extra = message.get("extra")
        response = extra.get("response") if isinstance(extra, dict) else None
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            continue
        input_tokens += int(usage.get("prompt_tokens") or 0)
        output_tokens += int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached_tokens += int(details.get("cached_tokens") or 0)
    return provider_calls, input_tokens, output_tokens, cached_tokens


def finalize_nonterminal_receipt(
    receipt_path: Path,
    *,
    trajectory_path: Path,
    return_code: int,
    supervisor: str,
    termination_kind: str | None = None,
) -> bool:
    """Convert only a durable RUNNING checkpoint into an evidenced ERROR."""

    if not receipt_path.is_file():
        return False
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("status") != "RUNNING":
        return False

    completed = _now()
    inferred_kind = str(termination_kind or "").strip().upper()
    if not inferred_kind:
        supervisor_key = supervisor.casefold()
        inferred_kind = (
            "TIMEOUT"
            if return_code == 124 or "timeout" in supervisor_key
            else "CANCELLED"
            if "cancel" in supervisor_key
            else "PROVIDER_TRANSPORT"
            if "provider" in supervisor_key
            else "PROCESS_EXIT"
        )
    if inferred_kind not in _TERMINATION_KINDS:
        raise ValueError(f"unsupported termination kind: {inferred_kind}")
    error = f"SUPERVISOR:product_process_exit_{return_code}"
    provider_calls, input_tokens, output_tokens, cached_tokens = _trajectory_usage(trajectory_path)
    if provider_calls is not None:
        receipt["provider_calls"] = provider_calls
        receipt["input_tokens"] = input_tokens
        receipt["output_tokens"] = output_tokens
        receipt["cached_tokens"] = cached_tokens

    transcript = receipt.get("transcript")
    if not isinstance(transcript, list):
        transcript = []
    transcript.append(
        {
            "type": "supervisor_error",
            "message": error,
            "return_code": return_code,
            "supervisor": supervisor,
        }
    )
    receipt["transcript"] = transcript

    treatment = receipt.get("treatment_receipt")
    if isinstance(treatment, dict):
        errors = treatment.get("errors")
        if not isinstance(errors, list):
            errors = []
        if error not in errors:
            errors.append(error)
        treatment["errors"] = errors
        treatment["treatment_status"] = "FAILED"

    receipt.update(
        {
            "status": "ERROR",
            "error_type": "ProductProcessExitError",
            "error": error,
            "completed": completed,
            "resolved": None,
            "stop_reason": "supervisor_product_exit",
            "supervisor_finalization": {
                "schema": "gt.supervisor_finalization.v1",
                "completed": completed,
                "return_code": return_code,
                "supervisor": supervisor,
                "trajectory_present": trajectory_path.is_file(),
            },
            "termination": {
                "schema": "gt.termination.v1",
                "kind": inferred_kind,
                "authority": supervisor,
                "return_code": return_code,
                "provider_calls_observed": provider_calls,
                "trajectory_present": trajectory_path.is_file(),
                "completed": completed,
            },
        }
    )
    _write_json_atomic(receipt_path, receipt)
    return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--return-code", type=int, required=True)
    parser.add_argument("--supervisor", required=True)
    parser.add_argument("--termination-kind", choices=sorted(_TERMINATION_KINDS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    finalize_nonterminal_receipt(
        args.receipt,
        trajectory_path=args.trajectory,
        return_code=args.return_code,
        supervisor=args.supervisor,
        termination_kind=args.termination_kind,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through adapter
    raise SystemExit(main())
