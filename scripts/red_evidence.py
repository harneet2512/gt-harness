"""Canonical, replayable capture for failing (RED) command evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SCHEMA = "gt.red_evidence.v1"
VERIFY_SCHEMA = "gt.red_evidence_verification.v1"
NORMALIZER_VERSION = "red-normalizer.v1"
OUTPUT_GRAMMAR = "utf8-lf-ansi-stripped-root-duration-redacted.v1"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DURATION = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?:ms|s)(?![A-Za-z0-9_])")
CONTROLLED_ENVIRONMENT = {
    "NO_COLOR": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONIOENCODING": "utf-8",
}


class CaptureError(ValueError):
    """A requested capture cannot produce authoritative RED evidence."""


class _DuplicateKey(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _receipt_payload(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "receipt_sha256"}


def _receipt_sha256(receipt: dict[str, Any]) -> str:
    return _sha256(_canonical_bytes(_receipt_payload(receipt)))


def encode_receipt(receipt: dict[str, Any]) -> bytes:
    """Serialize a receipt deterministically without changing its fields."""

    return (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _argv(value: Sequence[str], name: str) -> list[str]:
    result = list(value)
    if not result or any(not isinstance(item, str) or not item for item in result):
        raise CaptureError(f"invalid_{name}")
    return result


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(CONTROLLED_ENVIRONMENT)
    return environment


def _normalize_output(output: bytes, root: Path) -> str:
    text = re.sub(r"\r+\n", "\n", output.decode("utf-8", "replace")).replace("\r", "\n")
    text = ANSI.sub("", text)
    text = DURATION.sub("<DURATION>", text)
    resolved = str(root.resolve())
    for spelling in sorted({resolved, resolved.replace("\\", "/")}, key=len, reverse=True):
        text = text.replace(spelling, "<ROOT>")
    return text


def _run(argv: Sequence[str], root: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            list(argv),
            cwd=root,
            env=_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise CaptureError(f"command_start_failed:{type(exc).__name__}") from exc
    return result.returncode, _normalize_output(result.stdout, root)


def _input_entry(root: Path, logical_path: str) -> dict[str, Any]:
    if not logical_path or Path(logical_path).is_absolute():
        raise CaptureError(f"path_outside_root:{logical_path}")
    root = root.resolve()
    path = (root / logical_path).resolve()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise CaptureError(f"path_outside_root:{logical_path}") from exc
    if not path.is_file():
        raise CaptureError(f"input_not_file:{relative}")
    data = path.read_bytes()
    return {"path": relative, "sha256": _sha256(data), "size": len(data)}


def _input_entries(root: Path, paths: Sequence[str], kind: str) -> list[dict[str, Any]]:
    if not paths:
        raise CaptureError(f"missing_{kind}")
    entries = [_input_entry(root, path) for path in paths]
    logical = [entry["path"] for entry in entries]
    if len(logical) != len(set(logical)):
        raise CaptureError(f"duplicate_{kind}")
    return sorted(entries, key=lambda entry: entry["path"])


def capture(
    *,
    root: str | Path,
    sources: Sequence[str],
    fixtures: Sequence[str],
    command: Sequence[str],
    toolchain_command: Sequence[str],
) -> tuple[dict[str, Any], bytes]:
    """Execute a failing command and return its deterministic sealed receipt."""

    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise CaptureError("root_not_directory")
    source_entries = _input_entries(resolved_root, sources, "sources")
    fixture_entries = _input_entries(resolved_root, fixtures, "fixtures")
    overlap = {entry["path"] for entry in source_entries} & {
        entry["path"] for entry in fixture_entries
    }
    if overlap:
        raise CaptureError("source_fixture_overlap")

    command_argv = _argv(command, "command")
    toolchain_argv = _argv(toolchain_command, "toolchain_command")
    toolchain_exit, toolchain_text = _run(toolchain_argv, resolved_root)
    if toolchain_exit != 0:
        raise CaptureError(f"toolchain_command_failed:{toolchain_exit}")
    exit_code, diagnostic_text = _run(command_argv, resolved_root)
    if exit_code == 0:
        raise CaptureError("command_did_not_fail")

    diagnostic_bytes = diagnostic_text.encode()
    toolchain_bytes = toolchain_text.encode()
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "normalizer_version": NORMALIZER_VERSION,
        "output_grammar": OUTPUT_GRAMMAR,
        "environment": dict(sorted(CONTROLLED_ENVIRONMENT.items())),
        "sources": source_entries,
        "fixtures": fixture_entries,
        "command": {"argv": command_argv, "cwd": ".", "exit_code": exit_code},
        "diagnostic": {
            "text": diagnostic_text,
            "sha256": _sha256(diagnostic_bytes),
            "size": len(diagnostic_bytes),
        },
        "toolchain": {
            "argv": toolchain_argv,
            "exit_code": toolchain_exit,
            "text": toolchain_text,
            "sha256": _sha256(toolchain_bytes),
            "size": len(toolchain_bytes),
        },
    }
    receipt["receipt_sha256"] = _receipt_sha256(receipt)
    return receipt, encode_receipt(receipt)


def _verify_entry(root: Path, entry: Any, kind: str, errors: list[str]) -> None:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        errors.append(f"{kind}:invalid_entry")
        return
    logical_path = entry["path"]
    try:
        actual = _input_entry(root, logical_path)
    except CaptureError as exc:
        errors.append(f"{kind}:{exc}")
        return
    if actual["sha256"] != entry.get("sha256"):
        errors.append(f"{kind}_hash_mismatch:{logical_path}")
    if actual["size"] != entry.get("size"):
        errors.append(f"{kind}_size_mismatch:{logical_path}")


def _verify_observation(
    name: str, expected: Any, exit_code: int, text: str, errors: list[str]
) -> None:
    if not isinstance(expected, dict):
        errors.append(f"{name}:invalid")
        return
    encoded = text.encode()
    if expected.get("exit_code") != exit_code:
        errors.append(f"{name}_exit_mismatch")
    if expected.get("text") != text:
        errors.append(f"{name}_text_mismatch")
    if expected.get("sha256") != _sha256(encoded):
        errors.append(f"{name}_hash_mismatch")
    if expected.get("size") != len(encoded):
        errors.append(f"{name}_size_mismatch")


def verify(
    *,
    root: str | Path,
    receipt_path: str | Path,
    expected_receipt_sha256: str,
    replay: bool = False,
) -> dict[str, Any]:
    """Return a typed deterministic report for a sealed receipt and its inputs."""

    resolved_root = Path(root).resolve()
    errors: list[str] = []
    try:
        receipt = json.loads(
            Path(receipt_path).read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except _DuplicateKey:
        return {"schema": VERIFY_SCHEMA, "status": "fail", "errors": ["receipt:duplicate_key"]}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema": VERIFY_SCHEMA, "status": "fail", "errors": ["receipt:invalid_json"]}
    if not isinstance(receipt, dict):
        return {"schema": VERIFY_SCHEMA, "status": "fail", "errors": ["receipt:invalid_schema"]}

    computed_receipt_sha256 = _receipt_sha256(receipt)
    declared_receipt_sha256 = receipt.get("receipt_sha256")
    if declared_receipt_sha256 != computed_receipt_sha256:
        errors.append("receipt_sha256_mismatch")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_receipt_sha256)
        or expected_receipt_sha256 != computed_receipt_sha256
    ):
        errors.append("unexpected_receipt_sha256")
    if receipt.get("schema") != SCHEMA:
        errors.append("unexpected_schema")
    if receipt.get("normalizer_version") != NORMALIZER_VERSION:
        errors.append("unexpected_normalizer_version")
    if receipt.get("output_grammar") != OUTPUT_GRAMMAR:
        errors.append("unexpected_output_grammar")
    if receipt.get("environment") != dict(sorted(CONTROLLED_ENVIRONMENT.items())):
        errors.append("unexpected_environment")

    for kind in ("sources", "fixtures"):
        entries = receipt.get(kind)
        if not isinstance(entries, list) or not entries:
            errors.append(f"{kind}:missing")
            continue
        for entry in entries:
            _verify_entry(resolved_root, entry, kind[:-1], errors)

    if replay:
        command = receipt.get("command")
        toolchain = receipt.get("toolchain")
        if not isinstance(command, dict) or not isinstance(command.get("argv"), list):
            errors.append("command:invalid")
        if not isinstance(toolchain, dict) or not isinstance(toolchain.get("argv"), list):
            errors.append("toolchain:invalid")
        if "command:invalid" not in errors:
            try:
                exit_code, text = _run(_argv(command["argv"], "command"), resolved_root)
                expected_diagnostic = dict(receipt.get("diagnostic") or {})
                expected_diagnostic["exit_code"] = command.get("exit_code")
                _verify_observation("diagnostic", expected_diagnostic, exit_code, text, errors)
            except CaptureError as exc:
                errors.append(f"command:{exc}")
        if "toolchain:invalid" not in errors:
            try:
                exit_code, text = _run(_argv(toolchain["argv"], "toolchain_command"), resolved_root)
                _verify_observation("toolchain", toolchain, exit_code, text, errors)
            except CaptureError as exc:
                errors.append(f"toolchain:{exc}")

    return {
        "schema": VERIFY_SCHEMA,
        "status": "pass" if not errors else "fail",
        "errors": sorted(set(errors)),
        "receipt_sha256": computed_receipt_sha256,
    }


def _json_argv(value: str, name: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a JSON string array") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise argparse.ArgumentTypeError(f"{name} must be a JSON string array")
    return parsed


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--root", required=True)
    capture_parser.add_argument("--source", action="append", required=True)
    capture_parser.add_argument("--fixture", action="append", required=True)
    capture_parser.add_argument("--command-json", required=True)
    capture_parser.add_argument("--toolchain-command-json", required=True)
    capture_parser.add_argument("--output", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--root", required=True)
    verify_parser.add_argument("--receipt", required=True)
    verify_parser.add_argument("--expected-receipt-sha256", required=True)
    verify_parser.add_argument("--replay", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "capture":
        try:
            receipt, content = capture(
                root=args.root,
                sources=args.source,
                fixtures=args.fixture,
                command=_json_argv(args.command_json, "command-json"),
                toolchain_command=_json_argv(args.toolchain_command_json, "toolchain-command-json"),
            )
            _write_atomic(Path(args.output), content)
        except (CaptureError, argparse.ArgumentTypeError) as exc:
            print(json.dumps({"schema": SCHEMA, "status": "fail", "error": str(exc)}))
            return 1
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "captured",
                    "receipt_sha256": receipt["receipt_sha256"],
                },
                sort_keys=True,
            )
        )
        return 0
    report = verify(
        root=args.root,
        receipt_path=args.receipt,
        expected_receipt_sha256=args.expected_receipt_sha256,
        replay=args.replay,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
