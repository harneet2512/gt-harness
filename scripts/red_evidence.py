"""Canonical, replayable capture for Go build-failure (RED) evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from scripts.red_evidence_go import (
        GoGrammarError,
        canonicalize_go_red,
        validate_canonical_body,
    )
except ModuleNotFoundError:  # Direct `python scripts/red_evidence.py` execution.
    from red_evidence_go import GoGrammarError, canonicalize_go_red, validate_canonical_body

SCHEMA = "gt.red_evidence.receipt.v2"
VERIFY_SCHEMA = "gt.red_evidence.verify.v2"
NORMALIZER_VERSION = "go-build-red-normalizer.v2"
EXACT_TEXT_NORMALIZER_VERSION = "exact-text-normalizer.v1"
OUTPUT_GRAMMAR = "go-build-diagnostic-lines.v1"
EXACT_TEXT_GRAMMAR = "exact-text-failure.v1"
STREAM_MODEL = "merged-stdout-stderr.v1"
ENVIRONMENT_POLICY = "closed-go-red-environment.v1"
PREPARED_ENVIRONMENT_POLICY = "prepared-go-red-environment.v1"
FIXED_ENVIRONMENT = {
    "CGO_ENABLED": "0",
    "GOENV": "off",
    "GOTOOLCHAIN": "local",
    "GOPROXY": "off",
    "GOSUMDB": "off",
    "GOWORK": "off",
    "LANG": "C",
    "LC_ALL": "C",
    "NO_COLOR": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONIOENCODING": "utf-8",
    "TERM": "dumb",
    "TZ": "UTC",
}
WORK_ENVIRONMENT_KEYS = ("GOCACHE", "GOMODCACHE", "GOPATH", "HOME", "TEMP", "TMP", "TMPDIR")
SYSTEM_ENVIRONMENT_KEYS = ("SYSTEMROOT", "WINDIR")
HEX64 = re.compile(r"[0-9a-f]{64}")


class CaptureError(ValueError):
    """A requested capture cannot produce authoritative RED evidence."""


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class CaptureResult:
    receipt: dict[str, Any]
    receipt_bytes: bytes
    canonical_bytes: bytes
    raw_bytes: bytes


@dataclass(frozen=True)
class _Observation:
    exit_code: int
    raw: bytes
    executable: dict[str, Any]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _invalid_string(value: Any) -> bool:
    if isinstance(value, str):
        return "\0" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if isinstance(value, dict):
        return any(_invalid_string(key) or _invalid_string(nested) for key, nested in value.items())
    if isinstance(value, list):
        return any(_invalid_string(nested) for nested in value)
    return False


def _canonical_bytes(value: Any) -> bytes:
    if _invalid_string(value):
        raise CaptureError("invalid_unicode_or_nul")
    try:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return rendered.encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise CaptureError("invalid_json_value") from exc


def _receipt_payload(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "receipt_sha256"}


def _receipt_sha256(receipt: dict[str, Any]) -> str:
    return _sha256(_canonical_bytes(_receipt_payload(receipt)))


def encode_receipt(receipt: dict[str, Any]) -> bytes:
    _canonical_bytes(receipt)
    return (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _argv(value: Sequence[str], name: str) -> list[str]:
    try:
        result = list(value)
    except TypeError as exc:
        raise CaptureError(f"invalid_{name}") from exc
    if not result or any(
        not isinstance(item, str) or not item or _invalid_string(item) for item in result
    ):
        raise CaptureError(f"invalid_{name}")
    return result


def _closed_environment(
    work: Path,
    executable_paths: Sequence[Path],
    *,
    cgo_enabled: str | None = None,
    cache_seed: Path | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    directories = {
        "GOCACHE": work / "go-cache",
        "GOMODCACHE": work / "go-mod-cache",
        "GOPATH": work / "go-path",
        "HOME": work / "home",
        "TEMP": work / "tmp",
        "TMP": work / "tmp",
        "TMPDIR": work / "tmp",
    }
    if cache_seed is not None:
        for name in ("GOCACHE", "GOMODCACHE", "GOPATH"):
            directories[name] = cache_seed / name
    for directory in set(directories.values()):
        directory.mkdir(parents=True, exist_ok=True)
    environment = dict(FIXED_ENVIRONMENT)
    environment.update({key: str(value.resolve()) for key, value in directories.items()})
    if cgo_enabled is not None:
        environment["CGO_ENABLED"] = cgo_enabled
    tool_directories = list(dict.fromkeys(str(path.parent.resolve()) for path in executable_paths))
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT", os.environ.get("WINDIR", ""))
        if system_root:
            tool_directories.append(str((Path(system_root) / "System32").resolve()))
    else:
        tool_directories.extend(["/usr/bin", "/bin"])
    environment["PATH"] = os.pathsep.join(dict.fromkeys(tool_directories))
    for key in SYSTEM_ENVIRONMENT_KEYS:
        environment[key] = os.environ.get(key, "")
    logical = dict(FIXED_ENVIRONMENT)
    logical.update({key: f"<WORK>/{key.lower()}" for key in WORK_ENVIRONMENT_KEYS})
    if cgo_enabled is not None:
        logical["CGO_ENABLED"] = cgo_enabled
    logical["PATH"] = "<RESOLVED_TOOL_DIRS>"
    for key in SYSTEM_ENVIRONMENT_KEYS:
        logical[key] = "<SYSTEM_ROOT>" if environment[key] else ""
    return dict(sorted(environment.items())), dict(sorted(logical.items()))


def _resolve_executable(argv0: str, root: Path) -> Path:
    candidate = Path(argv0)
    if candidate.is_absolute() or any(separator in argv0 for separator in ("/", "\\")):
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    else:
        selected = shutil.which(argv0)
        if selected is None:
            raise CaptureError("executable_not_found")
        resolved = Path(selected).resolve()
    if not resolved.is_file():
        raise CaptureError("executable_not_file")
    return resolved


def _executable_identity(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"name": path.name, "sha256": _sha256(data), "size": len(data)}


def _observe(
    argv: Sequence[str],
    root: Path,
    environment: dict[str, str],
    executable_path: Path | None = None,
) -> _Observation:
    checked = _argv(argv, "argv")
    executable_path = executable_path or _resolve_executable(checked[0], root)
    try:
        completed = subprocess.run(
            [str(executable_path), *checked[1:]],
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except (OSError, ValueError) as exc:
        raise CaptureError(f"command_start_failed:{type(exc).__name__}") from exc
    return _Observation(
        completed.returncode, completed.stdout, _executable_identity(executable_path)
    )


def _runner_provenance(
    *,
    image_label: str | None = None,
    image_version: str | None = None,
    architecture: str | None = None,
) -> dict[str, str]:
    os_release_path = Path("/etc/os-release")
    if os_release_path.is_file():
        os_release = os_release_path.read_text(encoding="utf-8")
    else:
        os_release = f"system={platform.system()}\nrelease={platform.release()}\n"
    return {
        "architecture": architecture or os.environ.get("RUNNER_ARCH") or platform.machine(),
        "image_label": image_label
        or os.environ.get("RUNNER_IMAGE")
        or f"local-{platform.system()}",
        "image_version": image_version or os.environ.get("ImageVersion") or "local",
        "os_release_sha256": _sha256(os_release.encode("utf-8")),
        "os_release_text": os_release,
    }


def _input_entry(root: Path, logical_path: str) -> dict[str, Any]:
    if not logical_path or Path(logical_path).is_absolute() or _invalid_string(logical_path):
        raise CaptureError(f"path_outside_root:{logical_path}")
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


def _validate_command_inputs(root: Path, argv: Sequence[str], declared: set[str]) -> None:
    for argument in argv[1:]:
        if argument.startswith("-") or argument in {".", "./...", "..."}:
            continue
        token = argument[1:] if argument.startswith("@") else argument
        candidate = Path(token)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if ".." in Path(token).parts:
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise CaptureError(f"command_input_outside_root:{argument}") from exc
        if not resolved.exists():
            continue
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise CaptureError(f"command_input_outside_root:{argument}") from exc
        if resolved.is_file() and relative not in declared:
            raise CaptureError(f"unbound_command_input:{relative}")


def _entries_unchanged(root: Path, before: list[dict[str, Any]], kind: str) -> None:
    for entry in before:
        try:
            after = _input_entry(root, entry["path"])
        except CaptureError as exc:
            raise CaptureError(f"{kind}_mutated_during_capture:{entry['path']}") from exc
        if after != entry:
            raise CaptureError(f"{kind}_mutated_during_capture:{entry['path']}")


def _strict_text(raw: bytes, name: str) -> str:
    try:
        return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise CaptureError(f"invalid_utf8:{name}") from exc


def capture(
    *,
    root: str | Path,
    sources: Sequence[str],
    fixtures: Sequence[str],
    command: Sequence[str],
    toolchain_command: Sequence[str],
    expected_source_path: str,
    expected_diagnostic: str,
    expected_match_count: int = 1,
    runner_image: str | None = None,
    runner_image_version: str | None = None,
    runner_architecture: str | None = None,
    output_grammar: str = OUTPUT_GRAMMAR,
    cgo_enabled: str | None = None,
    cache_seed: str | Path | None = None,
) -> CaptureResult:
    """Execute a real failing Go command and seal body plus provenance."""

    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise CaptureError("root_not_directory")
    source_entries = _input_entries(resolved_root, sources, "sources")
    fixture_entries = _input_entries(resolved_root, fixtures, "fixtures")
    source_paths = {entry["path"] for entry in source_entries}
    fixture_paths = {entry["path"] for entry in fixture_entries}
    if source_paths & fixture_paths:
        raise CaptureError("source_fixture_overlap")
    command_argv = _argv(command, "command")
    toolchain_argv = _argv(toolchain_command, "toolchain_command")
    _validate_command_inputs(resolved_root, command_argv, source_paths | fixture_paths)
    expected_source = _input_entry(resolved_root, expected_source_path)["path"]
    if expected_source not in source_paths:
        raise CaptureError("expected_source_not_declared")
    if (
        not isinstance(expected_diagnostic, str)
        or not expected_diagnostic
        or _invalid_string(expected_diagnostic)
    ):
        raise CaptureError("invalid_expected_diagnostic")
    if (
        not isinstance(expected_match_count, int)
        or isinstance(expected_match_count, bool)
        or expected_match_count < 1
    ):
        raise CaptureError("invalid_expected_match_count")
    command_executable = _resolve_executable(command_argv[0], resolved_root)
    toolchain_executable = _resolve_executable(toolchain_argv[0], resolved_root)

    with tempfile.TemporaryDirectory(prefix="gt-red-") as directory:
        executable_paths = [command_executable, toolchain_executable, Path(sys.executable)]
        if cgo_enabled == "1":
            executable_paths.append(_resolve_executable("gcc", resolved_root))
        environment, logical_environment = _closed_environment(
            Path(directory),
            executable_paths,
            cgo_enabled=cgo_enabled,
            cache_seed=Path(cache_seed).resolve() if cache_seed is not None else None,
        )
        toolchain = _observe(
            toolchain_argv, resolved_root, environment, executable_path=toolchain_executable
        )
        if toolchain.exit_code != 0:
            raise CaptureError(f"toolchain_command_failed:{toolchain.exit_code}")
        toolchain_text = _strict_text(toolchain.raw, "toolchain")
        command_observation = _observe(
            command_argv, resolved_root, environment, executable_path=command_executable
        )
        if command_observation.exit_code == 0:
            raise CaptureError("command_did_not_fail")
        if output_grammar == EXACT_TEXT_GRAMMAR:
            normalized = _strict_text(command_observation.raw, "command")
            expected_text = _strict_text(expected_diagnostic.encode("utf-8"), "expected_diagnostic")
            if normalized != expected_text:
                raise CaptureError("exact_text_mismatch")
            canonical_body = normalized.encode("utf-8")
            package_banner = ""
            package_outcome = "exact_text_failure"
            matched_diagnostics = [normalized]
        elif output_grammar == OUTPUT_GRAMMAR:
            try:
                canonical = canonicalize_go_red(
                    command_observation.raw,
                    root=resolved_root,
                    expected_source_path=expected_source,
                    expected_diagnostic=expected_diagnostic,
                    expected_match_count=expected_match_count,
                )
            except GoGrammarError as exc:
                raise CaptureError(str(exc)) from exc
            canonical_body = canonical.body
            package_banner = canonical.package_banner
            package_outcome = canonical.package_outcome
            matched_diagnostics = list(canonical.matched_diagnostics)
        else:
            raise CaptureError("unsupported_output_grammar")
        _entries_unchanged(resolved_root, source_entries, "source")
        _entries_unchanged(resolved_root, fixture_entries, "fixture")

        toolchain_bytes = toolchain_text.encode("utf-8")
        receipt: dict[str, Any] = {
            "schema": SCHEMA,
            "normalizer_version": (
                EXACT_TEXT_NORMALIZER_VERSION
                if output_grammar == EXACT_TEXT_GRAMMAR
                else NORMALIZER_VERSION
            ),
            "output_grammar": output_grammar,
            "stream_model": STREAM_MODEL,
            "environment_policy": (
                PREPARED_ENVIRONMENT_POLICY if cgo_enabled is not None else ENVIRONMENT_POLICY
            ),
            "environment": logical_environment,
            "runner": _runner_provenance(
                image_label=runner_image,
                image_version=runner_image_version,
                architecture=runner_architecture,
            ),
            "capture_runtime": {
                "python_version": platform.python_version(),
                "executable": _executable_identity(Path(sys.executable).resolve()),
            },
            "sources": source_entries,
            "fixtures": fixture_entries,
            "matcher": {
                "source_path": expected_source,
                "substring": expected_diagnostic,
                "expected_count": expected_match_count,
            },
            "command": {
                "argv": command_argv,
                "cwd": ".",
                "exit_code": command_observation.exit_code,
                "executable": command_observation.executable,
            },
            "toolchain": {
                "argv": toolchain_argv,
                "cwd": ".",
                "exit_code": toolchain.exit_code,
                "text": toolchain_text,
                "sha256": _sha256(toolchain_bytes),
                "size": len(toolchain_bytes),
                "executable": toolchain.executable,
            },
            "raw_output": {
                "sha256": _sha256(command_observation.raw),
                "size": len(command_observation.raw),
            },
            "diagnostic": {
                "sha256": _sha256(canonical_body),
                "size": len(canonical_body),
                "package_banner": package_banner,
                "package_outcome": package_outcome,
                "matched_diagnostics": matched_diagnostics,
            },
        }
        receipt["receipt_sha256"] = _receipt_sha256(receipt)
        return CaptureResult(
            receipt, encode_receipt(receipt), canonical_body, command_observation.raw
        )


def _valid_executable(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"name", "sha256", "size"}
        and isinstance(value["name"], str)
        and bool(value["name"])
        and isinstance(value["sha256"], str)
        and bool(HEX64.fullmatch(value["sha256"]))
        and isinstance(value["size"], int)
        and not isinstance(value["size"], bool)
        and value["size"] > 0
    )


def _validate_entries(root: Path, value: Any, kind: str, errors: list[str]) -> set[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{kind}:missing")
        return set()
    paths: list[str] = []
    for entry in value:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256", "size"}
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("sha256"), str)
            or not HEX64.fullmatch(entry.get("sha256", ""))
            or not isinstance(entry.get("size"), int)
            or isinstance(entry.get("size"), bool)
            or entry.get("size", -1) < 0
        ):
            errors.append(f"{kind}:invalid_entry")
            continue
        paths.append(entry["path"])
        try:
            actual = _input_entry(root, entry["path"])
        except CaptureError as exc:
            errors.append(f"{kind}:{exc}")
            continue
        if actual["sha256"] != entry["sha256"]:
            errors.append(f"{kind}_hash_mismatch:{entry['path']}")
        if actual["size"] != entry["size"]:
            errors.append(f"{kind}_size_mismatch:{entry['path']}")
    if paths != sorted(paths):
        errors.append(f"{kind}:not_sorted")
    if len(paths) != len(set(paths)):
        errors.append(f"{kind}:duplicate")
    return set(paths)


def _validate_receipt(
    root: Path, receipt: dict[str, Any], canonical_bytes: bytes, errors: list[str]
) -> None:
    required = {
        "schema",
        "normalizer_version",
        "output_grammar",
        "stream_model",
        "environment_policy",
        "environment",
        "runner",
        "capture_runtime",
        "sources",
        "fixtures",
        "matcher",
        "command",
        "toolchain",
        "raw_output",
        "diagnostic",
        "receipt_sha256",
    }
    if set(receipt) != required:
        errors.append("receipt:invalid_fields")
    for field, expected in (("schema", SCHEMA), ("stream_model", STREAM_MODEL)):
        if receipt.get(field) != expected:
            errors.append(f"unexpected_{field}")
    if receipt.get("output_grammar") not in {OUTPUT_GRAMMAR, EXACT_TEXT_GRAMMAR}:
        errors.append("unexpected_output_grammar")
    expected_normalizer = (
        EXACT_TEXT_NORMALIZER_VERSION
        if receipt.get("output_grammar") == EXACT_TEXT_GRAMMAR
        else NORMALIZER_VERSION
    )
    if receipt.get("normalizer_version") != expected_normalizer:
        errors.append("unexpected_normalizer_version")
    policy = receipt.get("environment_policy")
    if policy not in {ENVIRONMENT_POLICY, PREPARED_ENVIRONMENT_POLICY}:
        errors.append("unexpected_environment_policy")
    environment = receipt.get("environment")
    environment_keys = (
        set(FIXED_ENVIRONMENT)
        | set(WORK_ENVIRONMENT_KEYS)
        | {
            *SYSTEM_ENVIRONMENT_KEYS,
            "PATH",
        }
    )
    if not isinstance(environment, dict) or set(environment) != environment_keys:
        errors.append("environment:invalid")
    else:
        expected_environment = dict(FIXED_ENVIRONMENT)
        if policy == PREPARED_ENVIRONMENT_POLICY:
            expected_environment["CGO_ENABLED"] = "1"
        for key, expected in expected_environment.items():
            if environment.get(key) != expected:
                errors.append(f"environment:unexpected_{key.lower()}")
        if any(environment.get(key) != f"<WORK>/{key.lower()}" for key in WORK_ENVIRONMENT_KEYS):
            errors.append("environment:invalid_work_path")
        if environment.get("PATH") != "<RESOLVED_TOOL_DIRS>":
            errors.append("environment:invalid_path")
        if any(
            environment.get(key) not in {"", "<SYSTEM_ROOT>"} for key in SYSTEM_ENVIRONMENT_KEYS
        ):
            errors.append("environment:invalid_system_path")
    runner = receipt.get("runner")
    if (
        not isinstance(runner, dict)
        or set(runner)
        != {"architecture", "image_label", "image_version", "os_release_sha256", "os_release_text"}
        or any(not isinstance(runner.get(key), str) or not runner[key] for key in runner)
        or not HEX64.fullmatch(runner.get("os_release_sha256", ""))
    ):
        errors.append("runner:invalid")
    elif _sha256(runner["os_release_text"].encode("utf-8")) != runner["os_release_sha256"]:
        errors.append("runner:os_release_identity_mismatch")
    runtime = receipt.get("capture_runtime")
    if (
        not isinstance(runtime, dict)
        or set(runtime) != {"python_version", "executable"}
        or not isinstance(runtime.get("python_version"), str)
        or not runtime["python_version"]
        or not _valid_executable(runtime.get("executable"))
    ):
        errors.append("capture_runtime:invalid")
    source_paths = _validate_entries(root, receipt.get("sources"), "source", errors)
    fixture_paths = _validate_entries(root, receipt.get("fixtures"), "fixture", errors)
    if source_paths & fixture_paths:
        errors.append("source_fixture_overlap")
    matcher = receipt.get("matcher")
    matcher_valid = (
        isinstance(matcher, dict)
        and set(matcher) == {"source_path", "substring", "expected_count"}
        and isinstance(matcher.get("source_path"), str)
        and matcher.get("source_path") in source_paths
        and isinstance(matcher.get("substring"), str)
        and bool(matcher.get("substring"))
        and not _invalid_string(matcher.get("substring"))
        and isinstance(matcher.get("expected_count"), int)
        and not isinstance(matcher.get("expected_count"), bool)
        and matcher.get("expected_count", 0) >= 1
    )
    if not matcher_valid:
        errors.append("matcher:invalid")

    command = receipt.get("command")
    if not isinstance(command, dict) or set(command) != {"argv", "cwd", "exit_code", "executable"}:
        errors.append("command:invalid")
    else:
        try:
            _argv(command.get("argv"), "command")
        except CaptureError:
            errors.append("command:invalid_argv")
        if command.get("cwd") != ".":
            errors.append("command:invalid_cwd")
        if (
            not isinstance(command.get("exit_code"), int)
            or isinstance(command.get("exit_code"), bool)
            or command.get("exit_code") == 0
        ):
            errors.append("command:exit_must_be_nonzero")
        if not _valid_executable(command.get("executable")):
            errors.append("command:invalid_executable")

    toolchain = receipt.get("toolchain")
    toolchain_fields = {"argv", "cwd", "exit_code", "text", "sha256", "size", "executable"}
    if not isinstance(toolchain, dict) or set(toolchain) != toolchain_fields:
        errors.append("toolchain:invalid")
    else:
        try:
            _argv(toolchain.get("argv"), "toolchain_command")
        except CaptureError:
            errors.append("toolchain:invalid_argv")
        text = toolchain.get("text")
        encoded = (
            text.encode("utf-8") if isinstance(text, str) and not _invalid_string(text) else None
        )
        if toolchain.get("cwd") != ".":
            errors.append("toolchain:invalid_cwd")
        if toolchain.get("exit_code") != 0:
            errors.append("toolchain:exit_must_be_zero")
        if encoded is None:
            errors.append("toolchain:invalid_text")
        elif toolchain.get("sha256") != _sha256(encoded) or toolchain.get("size") != len(encoded):
            errors.append("toolchain:identity_mismatch")
        if not _valid_executable(toolchain.get("executable")):
            errors.append("toolchain:invalid_executable")

    raw = receipt.get("raw_output")
    if (
        not isinstance(raw, dict)
        or set(raw) != {"sha256", "size"}
        or not isinstance(raw.get("sha256"), str)
        or not HEX64.fullmatch(raw.get("sha256", ""))
        or not isinstance(raw.get("size"), int)
        or isinstance(raw.get("size"), bool)
        or raw.get("size", -1) < 0
    ):
        errors.append("raw_output:invalid")
    diagnostic = receipt.get("diagnostic")
    diagnostic_fields = {
        "sha256",
        "size",
        "package_banner",
        "package_outcome",
        "matched_diagnostics",
    }
    if not isinstance(diagnostic, dict) or set(diagnostic) != diagnostic_fields:
        errors.append("diagnostic:invalid")
    else:
        if diagnostic.get("sha256") != _sha256(canonical_bytes):
            errors.append("diagnostic_hash_mismatch")
        if diagnostic.get("size") != len(canonical_bytes):
            errors.append("diagnostic_size_mismatch")
        if receipt.get("output_grammar") == EXACT_TEXT_GRAMMAR:
            if diagnostic.get("package_banner") != "":
                errors.append("diagnostic_package_mismatch")
            if diagnostic.get("package_outcome") != "exact_text_failure":
                errors.append("diagnostic_outcome_mismatch")
            if diagnostic.get("matched_diagnostics") != [
                canonical_bytes.decode("utf-8", errors="replace")
            ]:
                errors.append("diagnostic_match_mismatch")
        if matcher_valid and receipt.get("output_grammar") == OUTPUT_GRAMMAR:
            try:
                banner, matched = validate_canonical_body(
                    canonical_bytes,
                    expected_source_path=matcher["source_path"],
                    expected_diagnostic=matcher["substring"],
                    expected_match_count=matcher["expected_count"],
                )
            except (GoGrammarError, TypeError, AttributeError) as exc:
                errors.append(str(exc))
            else:
                if diagnostic.get("package_banner") != banner:
                    errors.append("diagnostic_package_mismatch")
                if diagnostic.get("package_outcome") != "build_failed":
                    errors.append("diagnostic_outcome_mismatch")
                if diagnostic.get("matched_diagnostics") != list(matched):
                    errors.append("diagnostic_match_mismatch")


def _report(errors: Sequence[str], receipt_sha256: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": VERIFY_SCHEMA,
        "status": "fail" if errors else "pass",
        "errors": sorted(set(errors)),
    }
    if receipt_sha256 is not None:
        report["receipt_sha256"] = receipt_sha256
    return report


def verify(
    *,
    root: str | Path,
    evidence_dir: str | Path,
    expected_receipt_sha256: str,
    replay: bool = False,
    prepared_cache: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the closed schema; optionally add independent execution proof."""

    resolved_root = Path(root).resolve()
    evidence = Path(evidence_dir)
    try:
        names = {item.name for item in evidence.iterdir()}
        if names != {"canonical.txt", "raw.log", "raw.sha256", "receipt.json"}:
            return _report(["evidence_directory:invalid_entries"])
        receipt = json.loads(
            (evidence / "receipt.json").read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
        )
        canonical_bytes = (evidence / "canonical.txt").read_bytes()
        raw_bytes = (evidence / "raw.log").read_bytes()
        raw_digest_text = (evidence / "raw.sha256").read_text(encoding="ascii")
    except _DuplicateKey:
        return _report(["receipt:duplicate_key"])
    except UnicodeError:
        return _report(["receipt:invalid_unicode"])
    except (OSError, json.JSONDecodeError):
        return _report(["receipt:invalid_json_or_sidecar"])
    if not isinstance(receipt, dict) or _invalid_string(receipt):
        return _report(["receipt:invalid_schema_or_string"])
    try:
        computed = _receipt_sha256(receipt)
    except CaptureError as exc:
        return _report([f"receipt:{exc}"])
    errors: list[str] = []
    if receipt.get("receipt_sha256") != computed:
        errors.append("receipt_sha256_mismatch")
    if not isinstance(expected_receipt_sha256, str) or not HEX64.fullmatch(expected_receipt_sha256):
        errors.append("expected_receipt_sha256:invalid")
    elif expected_receipt_sha256 != computed:
        errors.append("unexpected_receipt_sha256")
    _validate_receipt(resolved_root, receipt, canonical_bytes, errors)
    raw_row = receipt.get("raw_output")
    raw_sha = _sha256(raw_bytes)
    expected_raw_sidecar = f"{raw_sha}  raw.log\n"
    if raw_digest_text != expected_raw_sidecar:
        errors.append("raw_sha256_sidecar_mismatch")
    if not isinstance(raw_row, dict) or raw_row.get("sha256") != raw_sha:
        errors.append("raw_output_hash_mismatch")
    if not isinstance(raw_row, dict) or raw_row.get("size") != len(raw_bytes):
        errors.append("raw_output_size_mismatch")

    matcher = receipt.get("matcher")
    if isinstance(matcher, dict) and set(matcher) == {
        "source_path",
        "substring",
        "expected_count",
    }:
        if receipt.get("output_grammar") == EXACT_TEXT_GRAMMAR:
            try:
                if _strict_text(raw_bytes, "raw_output") != matcher["substring"]:
                    errors.append("raw_output:exact_text_mismatch")
            except CaptureError as exc:
                errors.append(f"raw_output:{exc}")
            try:
                normalized_raw = _strict_text(raw_bytes, "raw_output").encode("utf-8")
            except CaptureError as exc:
                errors.append(f"raw_output:{exc}")
            else:
                if normalized_raw != canonical_bytes:
                    errors.append("raw_output_canonical_mismatch")
        else:
            try:
                raw_parse = canonicalize_go_red(
                    raw_bytes,
                    root=resolved_root,
                    expected_source_path=matcher["source_path"],
                    expected_diagnostic=matcher["substring"],
                    expected_match_count=matcher["expected_count"],
                )
            except (GoGrammarError, TypeError, AttributeError) as exc:
                errors.append(f"raw_output:{exc}")
            else:
                if raw_parse.body != canonical_bytes:
                    errors.append("raw_output_canonical_mismatch")

    for section in ("command", "toolchain"):
        row = receipt.get(section)
        if not isinstance(row, dict) or not isinstance(row.get("argv"), list):
            continue
        try:
            selected = _resolve_executable(row["argv"][0], resolved_root)
            selected_identity = _executable_identity(selected)
        except (CaptureError, IndexError, TypeError):
            errors.append(f"{section}:selected_executable_unavailable")
        else:
            if selected_identity != row.get("executable"):
                errors.append(f"{section}:selected_executable_mismatch")
    runtime = receipt.get("capture_runtime")
    if isinstance(runtime, dict):
        if runtime.get("python_version") != platform.python_version():
            errors.append("capture_runtime:python_version_mismatch")

    if replay and not errors:
        command = receipt["command"]
        toolchain = receipt["toolchain"]
        if (
            receipt.get("environment_policy") == PREPARED_ENVIRONMENT_POLICY
            and prepared_cache is None
        ):
            errors.append("replay:prepared_cache_required")
        if errors:
            return _report(errors, computed)
        with tempfile.TemporaryDirectory(prefix="gt-red-verify-") as directory:
            try:
                command_executable = _resolve_executable(command["argv"][0], resolved_root)
                toolchain_executable = _resolve_executable(toolchain["argv"][0], resolved_root)
                executable_paths = [command_executable, toolchain_executable, Path(sys.executable)]
                if receipt["environment_policy"] == PREPARED_ENVIRONMENT_POLICY:
                    executable_paths.append(_resolve_executable("gcc", resolved_root))
                environment, logical_environment = _closed_environment(
                    Path(directory),
                    executable_paths,
                    cgo_enabled=(
                        "1"
                        if receipt["environment_policy"] == PREPARED_ENVIRONMENT_POLICY
                        else None
                    ),
                    cache_seed=(
                        Path(prepared_cache).resolve() if prepared_cache is not None else None
                    ),
                )
                observed_toolchain = _observe(
                    toolchain["argv"], resolved_root, environment, toolchain_executable
                )
                observed_command = _observe(
                    command["argv"], resolved_root, environment, command_executable
                )
                if receipt["output_grammar"] == EXACT_TEXT_GRAMMAR:
                    observed_text = _strict_text(observed_command.raw, "replay_output")
                    parsed_body = observed_text.encode("utf-8")
                else:
                    parsed = canonicalize_go_red(
                        observed_command.raw,
                        root=resolved_root,
                        expected_source_path=receipt["matcher"]["source_path"],
                        expected_diagnostic=receipt["matcher"]["substring"],
                        expected_match_count=receipt["matcher"]["expected_count"],
                    )
                    parsed_body = parsed.body
                observed_toolchain_text = _strict_text(observed_toolchain.raw, "toolchain")
            except (CaptureError, GoGrammarError) as exc:
                errors.append(f"replay:{exc}")
            else:
                if logical_environment != receipt["environment"]:
                    errors.append("replay:environment_policy_mismatch")
                if observed_toolchain.exit_code != 0:
                    errors.append("replay:toolchain_exit_mismatch")
                if observed_toolchain_text != toolchain["text"]:
                    errors.append("replay:toolchain_text_mismatch")
                if observed_toolchain.executable != toolchain["executable"]:
                    errors.append("replay:toolchain_executable_mismatch")
                if observed_command.exit_code != command["exit_code"]:
                    errors.append("replay:command_exit_mismatch")
                if observed_command.executable != command["executable"]:
                    errors.append("replay:command_executable_mismatch")
                if _sha256(observed_command.raw) != receipt["raw_output"]["sha256"]:
                    errors.append("replay:raw_output_mismatch")
                if parsed_body != canonical_bytes:
                    errors.append("replay:canonical_mismatch")
    return _report(errors, computed)


def _json_argv(value: str, name: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a JSON string array") from exc
    if not isinstance(parsed, list):
        raise argparse.ArgumentTypeError(f"{name} must be a JSON string array")
    try:
        return _argv(parsed, name)
    except CaptureError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a JSON string array") from exc


def publish_evidence_directory(
    *, evidence_dir: str | Path, root: str | Path, inputs: Sequence[str], result: CaptureResult
) -> None:
    """Publish one complete, immutable evidence directory with a single rename."""

    target = Path(evidence_dir)
    resolved_root = Path(root).resolve()
    target_parent = target.parent.resolve()
    resolved_target = target_parent / target.name
    if target.exists() or target.is_symlink():
        raise CaptureError("evidence_directory_exists")
    for logical in inputs:
        input_path = (resolved_root / logical).resolve()
        if input_path == resolved_target or resolved_target in input_path.parents:
            raise CaptureError("output_overlaps_input")
    target_parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target_parent))
    files = {
        "canonical.txt": result.canonical_bytes,
        "raw.log": result.raw_bytes,
        "raw.sha256": f"{_sha256(result.raw_bytes)}  raw.log\n".encode("ascii"),
        "receipt.json": result.receipt_bytes,
    }
    try:
        for name, content in files.items():
            with (temporary / name).open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, resolved_target)
    except OSError as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise CaptureError(f"publication_failed:{type(exc).__name__}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--root", required=True)
    capture_parser.add_argument("--source", action="append", required=True)
    capture_parser.add_argument("--fixture", action="append", required=True)
    capture_parser.add_argument("--command-json", required=True)
    capture_parser.add_argument("--toolchain-command-json", required=True)
    capture_parser.add_argument("--expected-source-path", required=True)
    capture_parser.add_argument("--expected-diagnostic", required=True)
    capture_parser.add_argument("--expected-match-count", type=int, default=1)
    capture_parser.add_argument("--runner-image")
    capture_parser.add_argument("--runner-image-version")
    capture_parser.add_argument("--runner-architecture")
    capture_parser.add_argument("--output-grammar", default=OUTPUT_GRAMMAR)
    capture_parser.add_argument("--evidence-dir", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--root", required=True)
    verify_parser.add_argument("--evidence-dir", required=True)
    verify_parser.add_argument("--expected-receipt-sha256", required=True)
    verify_parser.add_argument("--replay", action="store_true")
    verify_parser.add_argument("--prepared-cache")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.action == "capture":
            root = Path(args.root).resolve()
            result = capture(
                root=root,
                sources=args.source,
                fixtures=args.fixture,
                command=_json_argv(args.command_json, "command-json"),
                toolchain_command=_json_argv(args.toolchain_command_json, "toolchain-command-json"),
                expected_source_path=args.expected_source_path,
                expected_diagnostic=args.expected_diagnostic,
                expected_match_count=args.expected_match_count,
                runner_image=args.runner_image,
                runner_image_version=args.runner_image_version,
                runner_architecture=args.runner_architecture,
                output_grammar=args.output_grammar,
            )
            publish_evidence_directory(
                evidence_dir=args.evidence_dir,
                root=root,
                inputs=[*args.source, *args.fixture],
                result=result,
            )
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "status": "captured",
                        "receipt_sha256": result.receipt["receipt_sha256"],
                        "canonical_sha256": result.receipt["diagnostic"]["sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        report = verify(
            root=args.root,
            evidence_dir=args.evidence_dir,
            expected_receipt_sha256=args.expected_receipt_sha256,
            replay=args.replay,
            prepared_cache=args.prepared_cache,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "pass" else 1
    except (CaptureError, GoGrammarError, argparse.ArgumentTypeError, OSError, UnicodeError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "fail", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
