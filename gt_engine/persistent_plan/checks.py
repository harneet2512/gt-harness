"""Explicit check specifications and revision-bound plan evidence.

Shell transcripts are audit artifacts, never executable specifications. Passing
a bound check is CHECK_PASSED, not a proof of arbitrary requested behavior.
"""
from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    argv: tuple[str, ...]
    cwd: str
    protocol: str
    requirement_ids: tuple[str, ...]
    selected_test_ids: tuple[str, ...] = ()
    test_source_digest: str = ""
    environment_sha256: str = ""
    test_source_paths: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict, repo_root: str) -> CheckSpec:
        argv = value.get("argv")
        if not isinstance(argv, (list, tuple)) or not argv or any(
            not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv
        ):
            raise ValueError("check argv must be a nonempty string array")
        executable = Path(argv[0]).name.lower().removesuffix(".exe")
        if executable in {"sh", "bash", "zsh", "cmd", "powershell", "pwsh", "env"}:
            raise ValueError("shell/wrapper check executables are not admissible")
        if (executable.startswith(("python", "node", "ruby", "perl")) and
                any(arg in {"-c", "--eval", "-e", "-Command", "-EncodedCommand"} for arg in argv[1:])):
            raise ValueError("inline program execution is not an automatic check")
        from groundtruth.runtime.patterns import TEST_RUNNER_RE

        if not TEST_RUNNER_RE.match(shlex.join((executable, *argv[1:]))):
            raise ValueError("automatic check requires a canonical test-runner invocation")
        root = Path(repo_root).resolve()
        cwd = (root / str(value.get("cwd", "."))).resolve()
        if cwd != root and root not in cwd.parents:
            raise ValueError("check cwd escapes repository")
        rows = value.get("requirement_ids", ())
        tests = value.get("selected_test_ids", ())
        source_paths = value.get("test_source_paths", ())
        for values in (rows, tests, source_paths):
            if not isinstance(values, (list, tuple)) or any(not isinstance(x, str) or not x for x in values):
                raise ValueError("check identities must be string arrays")
        if not rows:
            raise ValueError("check has no requirement binding")
        material = {
            "argv": list(argv), "cwd": cwd.relative_to(root).as_posix(),
            "protocol": str(value.get("protocol", "")),
            "requirement_ids": sorted(set(rows)), "selected_test_ids": sorted(set(tests)),
            "test_source_digest": str(value.get("test_source_digest", "")),
            "environment_sha256": str(value.get("environment_sha256", "")),
            "test_source_paths": sorted(set(source_paths)),
        }
        # One execution can support several explicit requirement bindings.
        # Binding another row must not schedule the identical command twice.
        identity = {key: item for key, item in material.items() if key != "requirement_ids"}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if value.get("check_id") not in (None, "", digest):
            raise ValueError("check identity mismatch")
        return cls(digest, tuple(argv), material["cwd"], material["protocol"],
                   tuple(material["requirement_ids"]), tuple(material["selected_test_ids"]),
                   material["test_source_digest"], material["environment_sha256"],
                   tuple(material["test_source_paths"]))

    @property
    def command(self) -> str:
        return shlex.join(self.argv)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CheckObservation:
    check_id: str
    state: str
    source_revision: str
    environment_sha256: str
    capture_complete: bool
    test_ids: tuple[str, ...] = ()
    binding_basis: str = "explicit_check_spec"
    test_source_digest: str = ""


def validation_source_digest(spec: CheckSpec, snapshot) -> str:
    """Bind declared test source/configuration; result dependencies remain workspace-wide."""
    if not snapshot.complete:
        return ""
    root = Path(snapshot.root)
    scopes = list(spec.test_source_paths)
    if not scopes:
        for arg in spec.argv[1:]:
            candidate = (root / spec.cwd / arg.split("::", 1)[0]).resolve()
            if root in candidate.parents:
                relative = candidate.relative_to(root).as_posix()
                if any(f.path == relative or f.path.startswith(relative + "/") for f in snapshot.files):
                    scopes.append(relative)
    if not scopes:
        return ""
    selected = []
    for scope in scopes:
        path = (root / scope).resolve()
        if path != root and root not in path.parents:
            return ""
        relative = path.relative_to(root).as_posix()
        files = [f for f in snapshot.files if relative == "." or f.path == relative
                 or f.path.startswith(relative + "/")]
        if not files or any(f.kind != "file" for f in files):
            return ""
        selected.extend((f.path, f.sha256) for f in files)
    selected.extend((f.path, f.sha256) for f in snapshot.files
                    if Path(f.path).suffix.lower() in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".lock"})
    return hashlib.sha256(json.dumps(sorted(set(selected)), separators=(",", ":")).encode()).hexdigest()


def classify_bound_check(spec: CheckSpec, execution, *, before_revision: str,
                         after_revision: str, capture_complete: bool,
                         test_ids: tuple[str, ...] = (),
                         test_source_digest: str = "") -> CheckObservation:
    state = "UNVERIFIED"
    environment = getattr(execution, "environment_sha256", "")
    if (execution is not None and capture_complete and before_revision
            and spec.test_source_digest and test_source_digest == spec.test_source_digest
            and before_revision == after_revision == execution.repository_revision
            and environment and (not spec.environment_sha256 or environment == spec.environment_sha256)
            and execution.command_sha256 == hashlib.sha256(spec.command.encode()).hexdigest()
            and (not spec.protocol or execution.protocol == spec.protocol)
            and not execution.timed_out):
        if execution.outcome in {"fail", "env_fail"}:
            state = "CHECK_FAILED"
        elif (execution.outcome == "pass" and execution.returncode == 0
              and test_ids and set(spec.selected_test_ids).issubset(test_ids)):
            state = "CHECK_PASSED"
    return CheckObservation(spec.check_id, state, after_revision, environment,
                            capture_complete, test_ids, test_source_digest=test_source_digest)
