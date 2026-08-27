"""Deterministic repository architecture projected from static manifests.

The projection is deliberately correct-or-quiet.  It reads bounded manifest
bytes and returns only declarations that can be established without importing
project modules, executing build tools, or invoking a model.  Callers provide
the authoritative repository ``source_revision`` so every returned fact can be
joined to the graph generation that observed the same checkout.
"""

from __future__ import annotations

import ast
import configparser
import hashlib
import json
import os
import posixpath
import re
import tomllib
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ArchitectureNodeKind(StrEnum):
    COMPONENT = "component"
    PACKAGE = "package"
    PUBLIC_SURFACE = "public_surface"
    ENTRY_POINT = "entry_point"
    BUILD_TARGET = "build_target"
    TEST_TARGET = "test_target"
    DEPENDENCY = "dependency"


class ArchitectureLinkKind(StrEnum):
    CONTAINS = "contains"
    EXPOSES = "exposes"
    BUILDS = "builds"
    DEPENDS_ON = "depends_on"
    TESTS = "tests"


@dataclass(frozen=True, slots=True)
class ArchitectureNode:
    id: str
    kind: ArchitectureNodeKind
    name: str
    path: str
    language: str
    declared_by: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ArchitectureLink:
    kind: ArchitectureLinkKind
    source: str
    target: str
    declared_by: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ManifestReceipt:
    path: str
    sha256: str
    size_bytes: int
    parser: str


@dataclass(frozen=True, slots=True)
class RepositoryArchitectureProjection:
    schema: str
    source_revision: str
    manifest_revision: str
    nodes: tuple[ArchitectureNode, ...]
    links: tuple[ArchitectureLink, ...]
    manifests: tuple[ManifestReceipt, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchitectureFact:
    claim_id: str
    kind: ArchitectureNodeKind
    name: str
    path: str
    declared_by: str
    detail: str

    @property
    def rendered(self) -> str:
        location = self.path or "."
        detail = f" detail={self.detail}" if self.detail else ""
        return (
            f"{self.claim_id} kind={self.kind.value} name={self.name} "
            f"path={location} declared_by={self.declared_by}{detail}"
        )


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    maximum_manifests: int = 256
    maximum_manifest_bytes: int = 1_000_000
    maximum_nodes: int = 2_048
    maximum_links: int = 4_096


_DEPENDENCY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_GRADLE_ROOT_NAME = re.compile(r"\brootProject\.name\s*=\s*['\"]([^'\"]+)['\"]")
_GRADLE_INCLUDE = re.compile(r"(?m)^\s*include\s*(?:\(([^)]*)\)|([^\r\n]+))")
_GRADLE_QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")
_GRADLE_PROJECT_DEPENDENCY = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:\(\s*)?project\s*\(\s*['\"](:[^'\"]+)['\"]"
)
_GRADLE_EXTERNAL_DEPENDENCY = re.compile(
    r"\b(api|implementation|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly)"
    r"\s*(?:\(\s*)?['\"]([^:'\"]+):([^:'\"]+):([^'\"]+)['\"]"
)
_GRADLE_MAIN_CLASS = re.compile(r"\bmainClass(?:\.set\s*\(|\s*=\s*)['\"]([^'\"]+)['\"]")


def _node_id(kind: ArchitectureNodeKind, path: str, name: str, language: str) -> str:
    framed = f"{kind.value}\0{path}\0{name}\0{language}".encode("utf-8", "surrogatepass")
    return "gt-architecture-" + hashlib.sha256(framed).hexdigest()


class _ProjectionBuilder:
    def __init__(self, root: Path, source_revision: str, limits: ProjectionLimits) -> None:
        self.root = root
        self.source_revision = source_revision
        self.limits = limits
        self.nodes: dict[str, ArchitectureNode] = {}
        self.links: dict[tuple[str, str, str, str, str], ArchitectureLink] = {}
        self.manifests: dict[str, ManifestReceipt] = {}
        self.limitations: set[str] = set()
        self._discovered_manifests: tuple[str, ...] | None = None

    def read(self, relative: str, parser: str) -> bytes | None:
        relative = relative.replace("\\", "/").lstrip("./")
        if relative not in self.manifests and len(self.manifests) >= self.limits.maximum_manifests:
            self.limitations.add("manifest_limit_reached")
            return None
        path = self.root / relative
        if not path.is_file():
            return None
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root):
                self.limitations.add(f"unsafe_manifest_path:{relative}")
                return None
            size = resolved.stat().st_size
            if size > self.limits.maximum_manifest_bytes:
                self.limitations.add(f"manifest_too_large:{relative}")
                return None
            payload = resolved.read_bytes()
        except OSError as exc:
            self.limitations.add(f"manifest_unreadable:{relative}:{type(exc).__name__}")
            return None
        self.manifests[relative] = ManifestReceipt(
            path=relative,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            parser=parser,
        )
        return payload

    def discover(self, names: frozenset[str]) -> tuple[str, ...]:
        if self._discovered_manifests is not None:
            return tuple(
                row
                for row in self._discovered_manifests
                if Path(row).name.casefold() in names
            )
        rows: list[str] = []
        supported_names = frozenset(
            {
                "package.json",
                "tsconfig.json",
                "jest.config.js",
                "jest.config.cjs",
                "jest.config.mjs",
                "jest.config.ts",
                "vitest.config.js",
                "vitest.config.mjs",
                "vitest.config.ts",
                "go.mod",
                "go.work",
                "cargo.toml",
                "pom.xml",
                "build.gradle",
                "build.gradle.kts",
                "settings.gradle",
                "settings.gradle.kts",
            }
        )
        skipped = {
            ".git",
            ".hg",
            ".svn",
            ".tox",
            ".venv",
            "build",
            "dist",
            "node_modules",
            "target",
            "venv",
        }
        for current, directories, files in os.walk(self.root, followlinks=False):
            directories[:] = sorted(
                directory
                for directory in directories
                if directory not in skipped and not (Path(current) / directory).is_symlink()
            )
            base = Path(current)
            for name in sorted(files):
                if name.casefold() not in supported_names:
                    continue
                candidate = base / name
                if candidate.is_symlink():
                    continue
                try:
                    relative = candidate.relative_to(self.root).as_posix()
                except ValueError:
                    continue
                rows.append(relative)
                if len(rows) >= self.limits.maximum_manifests:
                    self.limitations.add("manifest_discovery_limit_reached")
                    self._discovered_manifests = tuple(rows)
                    return self.discover(names)
        self._discovered_manifests = tuple(rows)
        return self.discover(names)

    def node(
        self,
        kind: ArchitectureNodeKind,
        *,
        name: str,
        path: str,
        language: str,
        declared_by: str,
        detail: str = "",
    ) -> ArchitectureNode:
        node = ArchitectureNode(
            id=_node_id(kind, path, name, language),
            kind=kind,
            name=name,
            path=path,
            language=language,
            declared_by=declared_by,
            detail=detail,
        )
        self.nodes.setdefault(node.id, node)
        return self.nodes[node.id]

    def link(
        self,
        kind: ArchitectureLinkKind,
        source: ArchitectureNode,
        target: ArchitectureNode,
        *,
        declared_by: str,
        detail: str = "",
    ) -> None:
        link = ArchitectureLink(kind, source.id, target.id, declared_by, detail)
        key = (kind.value, source.id, target.id, declared_by, detail)
        self.links.setdefault(key, link)

    def finish(self) -> RepositoryArchitectureProjection:
        nodes = tuple(sorted(self.nodes.values(), key=lambda row: row.id))
        links = tuple(
            sorted(
                self.links.values(),
                key=lambda row: (
                    row.kind.value,
                    row.source,
                    row.target,
                    row.declared_by,
                    row.detail,
                ),
            )
        )
        if len(nodes) > self.limits.maximum_nodes:
            nodes = nodes[: self.limits.maximum_nodes]
            retained = {node.id for node in nodes}
            links = tuple(
                link for link in links if link.source in retained and link.target in retained
            )
            self.limitations.add("node_limit_reached")
        if len(links) > self.limits.maximum_links:
            links = links[: self.limits.maximum_links]
            self.limitations.add("link_limit_reached")
        manifests = tuple(sorted(self.manifests.values(), key=lambda row: row.path))
        digest = hashlib.sha256()
        for receipt in manifests:
            for value in (receipt.path, receipt.sha256, receipt.parser):
                encoded = value.encode("utf-8", "surrogatepass")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
        return RepositoryArchitectureProjection(
            schema="gt.repository_architecture.v1",
            source_revision=self.source_revision,
            manifest_revision=digest.hexdigest(),
            nodes=nodes,
            links=links,
            manifests=manifests,
            limitations=tuple(sorted(self.limitations)),
        )


def _dependency_name(specification: object) -> str:
    match = _DEPENDENCY_NAME.match(str(specification or "").strip())
    return match.group(0) if match else ""


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def _nested_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for row in value for item in _nested_strings(row))
    if isinstance(value, dict):
        return tuple(item for key in sorted(value) for item in _nested_strings(value[key]))
    return ()


def _project_pyproject(builder: _ProjectionBuilder) -> None:
    relative = "pyproject.toml"
    payload = builder.read(relative, "tomllib")
    if payload is None:
        return
    try:
        document = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
        return
    project = document.get("project")
    if not isinstance(project, dict):
        builder.limitations.add("pyproject_project_table_missing")
        return
    name = str(project.get("name") or "").strip()
    if not name:
        builder.limitations.add("pyproject_project_name_missing")
        return
    package = builder.node(
        ArchitectureNodeKind.PACKAGE,
        name=name,
        path=".",
        language="python",
        declared_by=relative,
    )
    for specification in _string_list(project.get("dependencies")):
        dependency_name = _dependency_name(specification)
        if not dependency_name:
            builder.limitations.add(f"python_dependency_unparsed:{specification}")
            continue
        dependency = builder.node(
            ArchitectureNodeKind.DEPENDENCY,
            name=dependency_name,
            path="",
            language="python",
            declared_by=relative,
            detail=specification,
        )
        builder.link(
            ArchitectureLinkKind.DEPENDS_ON,
            package,
            dependency,
            declared_by=relative,
            detail="project.dependencies",
        )
    scripts = project.get("scripts")
    if isinstance(scripts, dict):
        for script_name, target in sorted(scripts.items()):
            if not isinstance(target, str) or not target.strip():
                builder.limitations.add(f"python_entrypoint_unparsed:{script_name}")
                continue
            entrypoint = builder.node(
                ArchitectureNodeKind.ENTRY_POINT,
                name=str(script_name),
                path=".",
                language="python",
                declared_by=relative,
                detail=target.strip(),
            )
            builder.link(
                ArchitectureLinkKind.EXPOSES,
                package,
                entrypoint,
                declared_by=relative,
                detail="project.scripts",
            )
    build_system = document.get("build-system")
    if isinstance(build_system, dict):
        backend = str(build_system.get("build-backend") or "").strip()
        if backend:
            target = builder.node(
                ArchitectureNodeKind.BUILD_TARGET,
                name=name,
                path=".",
                language="python",
                declared_by=relative,
                detail=backend,
            )
            builder.link(
                ArchitectureLinkKind.BUILDS,
                target,
                package,
                declared_by=relative,
                detail="build-system.build-backend",
            )
    pytest = document.get("tool", {}).get("pytest", {}).get("ini_options", {})
    if isinstance(pytest, dict):
        for path in _string_list(pytest.get("testpaths")):
            target = builder.node(
                ArchitectureNodeKind.TEST_TARGET,
                name=f"pytest:{path}",
                path=path.replace("\\", "/").strip("/"),
                language="python",
                declared_by=relative,
            )
            builder.link(
                ArchitectureLinkKind.TESTS,
                target,
                package,
                declared_by=relative,
                detail="tool.pytest.ini_options.testpaths",
            )


def _python_package(builder: _ProjectionBuilder) -> ArchitectureNode | None:
    return next(
        (
            node
            for node in builder.nodes.values()
            if node.kind is ArchitectureNodeKind.PACKAGE and node.language == "python"
        ),
        None,
    )


def _project_setup_cfg(builder: _ProjectionBuilder) -> None:
    relative = "setup.cfg"
    payload = builder.read(relative, "configparser")
    if payload is None:
        return
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(payload.decode("utf-8"))
    except (UnicodeError, configparser.Error) as exc:
        builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
        return
    if _python_package(builder) is not None:
        builder.limitations.add("secondary_python_build_manifest_ignored:setup.cfg")
        return
    name = parser.get("metadata", "name", fallback="").strip()
    if not name:
        builder.limitations.add("setup_cfg_name_missing")
        return
    package = builder.node(
        ArchitectureNodeKind.PACKAGE,
        name=name,
        path=".",
        language="python",
        declared_by=relative,
    )
    build = builder.node(
        ArchitectureNodeKind.BUILD_TARGET,
        name=name,
        path=".",
        language="python",
        declared_by=relative,
        detail="setuptools.setup.cfg",
    )
    builder.link(
        ArchitectureLinkKind.BUILDS,
        build,
        package,
        declared_by=relative,
        detail="setup.cfg",
    )
    modules = parser.get("options", "py_modules", fallback="")
    for module in sorted({item for item in re.split(r"[,\s]+", modules) if item}):
        surface = builder.node(
            ArchitectureNodeKind.PUBLIC_SURFACE,
            name=module,
            path=module.replace(".", "/"),
            language="python",
            declared_by=relative,
            detail=module,
        )
        builder.link(
            ArchitectureLinkKind.EXPOSES,
            package,
            surface,
            declared_by=relative,
            detail="setup.cfg py_modules",
        )
    requirements = parser.get("options", "install_requires", fallback="")
    for specification in sorted(
        {item.strip() for item in requirements.replace(",", "\n").splitlines() if item.strip()}
    ):
        dependency_name = _dependency_name(specification)
        if not dependency_name:
            builder.limitations.add(f"python_dependency_unparsed:{specification}")
            continue
        dependency = builder.node(
            ArchitectureNodeKind.DEPENDENCY,
            name=dependency_name,
            path="",
            language="python",
            declared_by=relative,
            detail=specification,
        )
        builder.link(
            ArchitectureLinkKind.DEPENDS_ON,
            package,
            dependency,
            declared_by=relative,
            detail="setup.cfg install_requires",
        )
    entries = parser.get("options.entry_points", "console_scripts", fallback="")
    for row in sorted(item.strip() for item in entries.splitlines() if item.strip()):
        if "=" not in row:
            builder.limitations.add(f"python_entrypoint_unparsed:{row}")
            continue
        entry_name, target = (item.strip() for item in row.split("=", 1))
        entrypoint = builder.node(
            ArchitectureNodeKind.ENTRY_POINT,
            name=entry_name,
            path=".",
            language="python",
            declared_by=relative,
            detail=target,
        )
        builder.link(
            ArchitectureLinkKind.EXPOSES,
            package,
            entrypoint,
            declared_by=relative,
            detail="setup.cfg console_scripts",
        )
    testpaths = parser.get("tool:pytest", "testpaths", fallback="")
    for path in sorted({item for item in re.split(r"[,\s]+", testpaths) if item}):
        target = builder.node(
            ArchitectureNodeKind.TEST_TARGET,
            name=f"pytest:{path}",
            path=path.replace("\\", "/").strip("/"),
            language="python",
            declared_by=relative,
        )
        builder.link(
            ArchitectureLinkKind.TESTS,
            target,
            package,
            declared_by=relative,
            detail="setup.cfg tool:pytest",
        )


def _project_setup_py(builder: _ProjectionBuilder) -> None:
    relative = "setup.py"
    payload = builder.read(relative, "python-ast-literals")
    if payload is None:
        return
    builder.limitations.add("setup_py_dynamic_expressions_not_interpreted")
    if _python_package(builder) is not None:
        builder.limitations.add("secondary_python_build_manifest_ignored:setup.py")
        return
    try:
        tree = ast.parse(payload.decode("utf-8"), filename=relative)
    except (UnicodeError, SyntaxError) as exc:
        builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
        return
    calls = []
    for statement in tree.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            continue
        call = statement.value
        if (
            isinstance(call.func, ast.Name)
            and call.func.id == "setup"
            or isinstance(call.func, ast.Attribute)
            and call.func.attr == "setup"
        ):
            calls.append(call)
    if not calls:
        builder.limitations.add("setup_py_setup_call_missing")
        return
    if len(calls) > 1:
        builder.limitations.add("setup_py_multiple_setup_calls")
    values: dict[str, Any] = {}
    for keyword in sorted(calls, key=lambda row: (row.lineno, row.col_offset))[0].keywords:
        if keyword.arg is None:
            builder.limitations.add("setup_py_keyword_expansion_ignored")
            continue
        try:
            values[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            builder.limitations.add(f"setup_py_dynamic_keyword_ignored:{keyword.arg}")
    name = str(values.get("name") or "").strip()
    if not name:
        builder.limitations.add("setup_py_name_missing_or_dynamic")
        return
    package = builder.node(
        ArchitectureNodeKind.PACKAGE,
        name=name,
        path=".",
        language="python",
        declared_by=relative,
    )
    build = builder.node(
        ArchitectureNodeKind.BUILD_TARGET,
        name=name,
        path=".",
        language="python",
        declared_by=relative,
        detail="setuptools.setup",
    )
    builder.link(
        ArchitectureLinkKind.BUILDS,
        build,
        package,
        declared_by=relative,
        detail="setup call",
    )
    for module in _string_list(values.get("py_modules")) + _string_list(values.get("packages")):
        surface = builder.node(
            ArchitectureNodeKind.PUBLIC_SURFACE,
            name=module,
            path=module.replace(".", "/"),
            language="python",
            declared_by=relative,
            detail=module,
        )
        builder.link(
            ArchitectureLinkKind.EXPOSES,
            package,
            surface,
            declared_by=relative,
            detail="setup package declaration",
        )
    for specification in _string_list(values.get("install_requires")):
        dependency_name = _dependency_name(specification)
        if not dependency_name:
            builder.limitations.add(f"python_dependency_unparsed:{specification}")
            continue
        dependency = builder.node(
            ArchitectureNodeKind.DEPENDENCY,
            name=dependency_name,
            path="",
            language="python",
            declared_by=relative,
            detail=specification,
        )
        builder.link(
            ArchitectureLinkKind.DEPENDS_ON,
            package,
            dependency,
            declared_by=relative,
            detail="setup install_requires",
        )
    entry_points = values.get("entry_points")
    if isinstance(entry_points, dict):
        for row in _string_list(entry_points.get("console_scripts")):
            if "=" not in row:
                builder.limitations.add(f"python_entrypoint_unparsed:{row}")
                continue
            entry_name, target = (item.strip() for item in row.split("=", 1))
            if not entry_name or not target:
                builder.limitations.add(f"python_entrypoint_unparsed:{row}")
                continue
            entrypoint = builder.node(
                ArchitectureNodeKind.ENTRY_POINT,
                name=entry_name,
                path=".",
                language="python",
                declared_by=relative,
                detail=target,
            )
            builder.link(
                ArchitectureLinkKind.EXPOSES,
                package,
                entrypoint,
                declared_by=relative,
                detail="setup console_scripts",
            )


def _project_pytest_ini(builder: _ProjectionBuilder) -> None:
    relative = "pytest.ini"
    payload = builder.read(relative, "configparser")
    if payload is None:
        return
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(payload.decode("utf-8"))
    except (UnicodeError, configparser.Error) as exc:
        builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
        return
    package = _python_package(builder)
    if package is None:
        builder.limitations.add("pytest_package_boundary_unresolved")
        return
    testpaths = parser.get("pytest", "testpaths", fallback="")
    for path in sorted({item for item in re.split(r"[,\s]+", testpaths) if item}):
        target = builder.node(
            ArchitectureNodeKind.TEST_TARGET,
            name=f"pytest:{path}",
            path=path.replace("\\", "/").strip("/"),
            language="python",
            declared_by=relative,
        )
        builder.link(
            ArchitectureLinkKind.TESTS,
            target,
            package,
            declared_by=relative,
            detail="pytest testpaths",
        )


def _project_tox(builder: _ProjectionBuilder) -> None:
    relative = "tox.ini"
    payload = builder.read(relative, "configparser")
    if payload is None:
        return
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(payload.decode("utf-8"))
    except (UnicodeError, configparser.Error) as exc:
        builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
        return
    envlist = parser.get("tox", "envlist", fallback="")
    package = next(
        (node for node in builder.nodes.values() if node.kind is ArchitectureNodeKind.PACKAGE),
        None,
    )
    if package is None:
        builder.limitations.add("tox_package_boundary_unresolved")
        return
    for environment in sorted(
        {item.strip() for item in re.split(r"[,\s]+", envlist) if item.strip()}
    ):
        target = builder.node(
            ArchitectureNodeKind.TEST_TARGET,
            name=f"tox:{environment}",
            path=".",
            language="python",
            declared_by=relative,
        )
        builder.link(
            ArchitectureLinkKind.TESTS,
            target,
            package,
            declared_by=relative,
            detail="tox.envlist",
        )


def _package_directory(relative: str) -> str:
    parent = Path(relative).parent.as_posix()
    return "." if parent == "." else parent


def _joined_path(
    builder: _ProjectionBuilder,
    package_path: str,
    declared: str,
    *,
    declared_by: str,
) -> str:
    raw = str(declared or "").strip().replace("\\", "/")
    if not raw:
        return package_path
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or "\0" in raw:
        builder.limitations.add(f"declared_path_outside_repository:{declared_by}:{raw}")
        return ""
    base = "" if package_path == "." else package_path
    normalized = posixpath.normpath(posixpath.join(base, raw))
    if normalized == ".." or normalized.startswith("../"):
        builder.limitations.add(f"declared_path_outside_repository:{declared_by}:{raw}")
        return ""
    return "." if normalized in {"", "."} else normalized


def _project_javascript(builder: _ProjectionBuilder) -> None:
    packages: dict[str, tuple[ArchitectureNode, dict[str, Any]]] = {}
    for relative in builder.discover(frozenset({"package.json"})):
        payload = builder.read(relative, "json")
        if payload is None:
            continue
        try:
            document = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
            continue
        if not isinstance(document, dict):
            builder.limitations.add(f"package_json_object_required:{relative}")
            continue
        package_path = _package_directory(relative)
        name = str(document.get("name") or "").strip()
        if not name:
            builder.limitations.add(f"package_json_name_missing:{relative}")
            continue
        package = builder.node(
            ArchitectureNodeKind.PACKAGE,
            name=name,
            path=package_path,
            language="javascript",
            declared_by=relative,
        )
        packages[package_path] = (package, document)
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            dependencies = document.get(section)
            if not isinstance(dependencies, dict):
                continue
            for dependency_name, specification in sorted(dependencies.items()):
                if not isinstance(specification, str):
                    builder.limitations.add(
                        f"javascript_dependency_unparsed:{relative}:{dependency_name}"
                    )
                    continue
                dependency = builder.node(
                    ArchitectureNodeKind.DEPENDENCY,
                    name=str(dependency_name),
                    path="",
                    language="javascript",
                    declared_by=relative,
                    detail=specification,
                )
                builder.link(
                    ArchitectureLinkKind.DEPENDS_ON,
                    package,
                    dependency,
                    declared_by=relative,
                    detail=section,
                )
        for raw in _nested_strings(
            {
                key: document[key]
                for key in ("exports", "main", "module", "types", "typings")
                if key in document
            }
        ):
            if raw.startswith(("node:", "http:", "https:")):
                continue
            surface_path = _joined_path(builder, package_path, raw, declared_by=relative)
            if not surface_path:
                continue
            surface = builder.node(
                ArchitectureNodeKind.PUBLIC_SURFACE,
                name=raw,
                path=surface_path,
                language="javascript",
                declared_by=relative,
                detail=raw,
            )
            builder.link(
                ArchitectureLinkKind.EXPOSES,
                package,
                surface,
                declared_by=relative,
                detail="package public surface",
            )
        binary = document.get("bin")
        entries = {name: binary} if isinstance(binary, str) else binary
        if isinstance(entries, dict):
            for entry_name, target in sorted(entries.items()):
                if not isinstance(target, str):
                    builder.limitations.add(
                        f"javascript_entrypoint_unparsed:{relative}:{entry_name}"
                    )
                    continue
                entry_path = _joined_path(builder, package_path, target, declared_by=relative)
                if not entry_path:
                    continue
                entrypoint = builder.node(
                    ArchitectureNodeKind.ENTRY_POINT,
                    name=str(entry_name),
                    path=entry_path,
                    language="javascript",
                    declared_by=relative,
                    detail=target,
                )
                builder.link(
                    ArchitectureLinkKind.EXPOSES,
                    package,
                    entrypoint,
                    declared_by=relative,
                    detail="package.bin",
                )
        scripts = document.get("scripts")
        if isinstance(scripts, dict):
            for script_name, command in sorted(scripts.items()):
                if not isinstance(command, str):
                    builder.limitations.add(f"javascript_script_unparsed:{relative}:{script_name}")
                    continue
                suffix = "" if package_path == "." else f":{name}"
                if script_name == "build" or script_name.startswith("build:"):
                    target = builder.node(
                        ArchitectureNodeKind.BUILD_TARGET,
                        name=f"npm:{script_name}{suffix}",
                        path=package_path,
                        language="javascript",
                        declared_by=relative,
                        detail=command,
                    )
                    builder.link(
                        ArchitectureLinkKind.BUILDS,
                        target,
                        package,
                        declared_by=relative,
                        detail="package.scripts",
                    )
                if script_name == "test" or script_name.startswith("test:"):
                    target = builder.node(
                        ArchitectureNodeKind.TEST_TARGET,
                        name=f"npm:{script_name}{suffix}",
                        path=package_path,
                        language="javascript",
                        declared_by=relative,
                        detail=command,
                    )
                    builder.link(
                        ArchitectureLinkKind.TESTS,
                        target,
                        package,
                        declared_by=relative,
                        detail="package.scripts",
                    )
        if isinstance(document.get("jest"), dict):
            target = builder.node(
                ArchitectureNodeKind.TEST_TARGET,
                name="jest:package-config" if package_path == "." else f"jest:{name}",
                path=package_path,
                language="javascript",
                declared_by=relative,
                detail="package.jest",
            )
            builder.link(
                ArchitectureLinkKind.TESTS,
                target,
                package,
                declared_by=relative,
                detail="package.jest",
            )

    root = packages.get(".")
    if root is not None:
        root_package, root_document = root
        workspaces = root_document.get("workspaces")
        if isinstance(workspaces, dict):
            workspaces = workspaces.get("packages")
        patterns = _string_list(workspaces)
        for package_path, (package, _) in sorted(packages.items()):
            if package_path == ".":
                continue
            if any(Path(package_path).match(pattern) for pattern in patterns):
                builder.link(
                    ArchitectureLinkKind.CONTAINS,
                    root_package,
                    package,
                    declared_by="package.json",
                    detail="workspace",
                )

    for relative in builder.discover(frozenset({"tsconfig.json"})):
        payload = builder.read(relative, "json")
        if payload is None:
            continue
        try:
            document = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
            continue
        if not isinstance(document, dict):
            builder.limitations.add(f"tsconfig_object_required:{relative}")
            continue
        owner = packages.get(_package_directory(relative)) or root
        if owner is None:
            builder.limitations.add(f"tsconfig_package_boundary_unresolved:{relative}")
            continue
        for reference in document.get("references", ()):
            if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
                builder.limitations.add(f"tsconfig_reference_unparsed:{relative}")
                continue
            target_path = _joined_path(
                builder,
                _package_directory(relative),
                reference["path"],
                declared_by=relative,
            )
            if not target_path:
                continue
            target_path = Path(target_path).as_posix().rstrip("/") or "."
            target = packages.get(target_path)
            if target is not None:
                builder.link(
                    ArchitectureLinkKind.DEPENDS_ON,
                    owner[0],
                    target[0],
                    declared_by=relative,
                    detail="tsconfig.references",
                )

    config_names = frozenset(
        {
            "jest.config.js",
            "jest.config.json",
            "jest.config.mjs",
            "jest.config.ts",
            "vitest.config.js",
            "vitest.config.json",
            "vitest.config.mjs",
            "vitest.config.ts",
        }
    )
    for relative in builder.discover(config_names):
        suffix = Path(relative).suffix.casefold()
        payload = builder.read(relative, "json" if suffix == ".json" else "static-presence")
        if payload is None:
            continue
        package_path = _package_directory(relative)
        owner = packages.get(package_path) or root
        if owner is None:
            builder.limitations.add(f"test_config_package_boundary_unresolved:{relative}")
            continue
        runner = "jest" if Path(relative).name.casefold().startswith("jest") else "vitest"
        if suffix != ".json":
            builder.limitations.add(f"dynamic_test_config_not_interpreted:{relative}")
        else:
            try:
                json.loads(payload)
            except (UnicodeError, json.JSONDecodeError) as exc:
                builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
                continue
        target = builder.node(
            ArchitectureNodeKind.TEST_TARGET,
            name=f"{runner}:{Path(relative).name}",
            path=package_path,
            language="javascript",
            declared_by=relative,
        )
        builder.link(
            ArchitectureLinkKind.TESTS,
            target,
            owner[0],
            declared_by=relative,
            detail=f"{runner} configuration",
        )


def _go_directives(text: str, directive: str) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        if in_block:
            if line == ")":
                in_block = False
                continue
            rows.append(tuple(line.split()))
            continue
        if line == f"{directive} (":
            in_block = True
            continue
        prefix = f"{directive} "
        if line.startswith(prefix):
            rows.append(tuple(line[len(prefix) :].split()))
    return tuple(rows)


def _go_module_name(text: str) -> str:
    rows = _go_directives(text, "module")
    return rows[0][0] if rows and rows[0] else ""


def _safe_relative_directory(builder: _ProjectionBuilder, base: Path, raw: str) -> str:
    value = str(raw or "").strip().strip('"').replace("\\", "/")
    try:
        candidate = (base / value).resolve(strict=True)
    except (OSError, RuntimeError):
        return ""
    if not candidate.is_relative_to(builder.root) or not candidate.is_dir():
        return ""
    relative = candidate.relative_to(builder.root).as_posix()
    return relative or "."


def _project_go(builder: _ProjectionBuilder) -> None:
    module_documents: dict[str, tuple[ArchitectureNode, str, str]] = {}
    for relative in builder.discover(frozenset({"go.mod"})):
        payload = builder.read(relative, "go-directives")
        if payload is None:
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeError as exc:
            builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
            continue
        module_name = _go_module_name(text)
        if not module_name:
            builder.limitations.add(f"go_module_name_missing:{relative}")
            continue
        package_path = _package_directory(relative)
        package = builder.node(
            ArchitectureNodeKind.PACKAGE,
            name=module_name,
            path=package_path,
            language="go",
            declared_by=relative,
        )
        module_documents[module_name] = (package, text, relative)
        build = builder.node(
            ArchitectureNodeKind.BUILD_TARGET,
            name=f"go:build:{module_name}",
            path=package_path,
            language="go",
            declared_by=relative,
            detail="go build ./...",
        )
        test = builder.node(
            ArchitectureNodeKind.TEST_TARGET,
            name=f"go:test:{module_name}",
            path=package_path,
            language="go",
            declared_by=relative,
            detail="go test ./...",
        )
        builder.link(
            ArchitectureLinkKind.BUILDS,
            build,
            package,
            declared_by=relative,
            detail="conventional module target",
        )
        builder.link(
            ArchitectureLinkKind.TESTS,
            test,
            package,
            declared_by=relative,
            detail="conventional module target",
        )
    if module_documents:
        builder.limitations.add("go_entrypoints_not_declared_by_static_manifests")
    for _, (package, text, relative) in sorted(module_documents.items()):
        for fields in _go_directives(text, "require"):
            if not fields:
                continue
            dependency_name = fields[0]
            internal = module_documents.get(dependency_name)
            if internal is not None:
                builder.link(
                    ArchitectureLinkKind.DEPENDS_ON,
                    package,
                    internal[0],
                    declared_by=relative,
                    detail="go.mod internal require",
                )
                continue
            dependency = builder.node(
                ArchitectureNodeKind.DEPENDENCY,
                name=dependency_name,
                path="",
                language="go",
                declared_by=relative,
                detail=fields[1] if len(fields) > 1 else "",
            )
            builder.link(
                ArchitectureLinkKind.DEPENDS_ON,
                package,
                dependency,
                declared_by=relative,
                detail="go.mod require",
            )

    for relative in builder.discover(frozenset({"go.work"})):
        payload = builder.read(relative, "go-directives")
        if payload is None:
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeError as exc:
            builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
            continue
        component = builder.node(
            ArchitectureNodeKind.COMPONENT,
            name="go-workspace",
            path=_package_directory(relative),
            language="go",
            declared_by=relative,
        )
        base = (builder.root / relative).parent
        packages_by_path = {row[0].path: row[0] for row in module_documents.values()}
        for fields in _go_directives(text, "use"):
            if not fields:
                continue
            target_path = _safe_relative_directory(builder, base, fields[0])
            target = packages_by_path.get(target_path)
            if target is None:
                builder.limitations.add(f"go_work_use_unresolved:{relative}:{fields[0]}")
                continue
            builder.link(
                ArchitectureLinkKind.CONTAINS,
                component,
                target,
                declared_by=relative,
                detail="go.work use",
            )


def _project_rust(builder: _ProjectionBuilder) -> None:
    documents: dict[str, tuple[ArchitectureNode, dict[str, Any], str]] = {}
    workspaces: list[tuple[ArchitectureNode, dict[str, Any], str]] = []
    for relative in builder.discover(frozenset({"cargo.toml"})):
        payload = builder.read(relative, "tomllib")
        if payload is None:
            continue
        try:
            document = tomllib.loads(payload.decode("utf-8"))
        except (UnicodeError, tomllib.TOMLDecodeError) as exc:
            builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
            continue
        package_path = _package_directory(relative)
        workspace = document.get("workspace")
        if isinstance(workspace, dict):
            component = builder.node(
                ArchitectureNodeKind.COMPONENT,
                name=(
                    "cargo-workspace" if package_path == "." else f"cargo-workspace:{package_path}"
                ),
                path=package_path,
                language="rust",
                declared_by=relative,
            )
            workspaces.append((component, workspace, relative))
        package_table = document.get("package")
        if not isinstance(package_table, dict):
            continue
        name = str(package_table.get("name") or "").strip()
        if not name:
            builder.limitations.add(f"cargo_package_name_missing:{relative}")
            continue
        package = builder.node(
            ArchitectureNodeKind.PACKAGE,
            name=name,
            path=package_path,
            language="rust",
            declared_by=relative,
            detail=str(package_table.get("version") or ""),
        )
        documents[package_path] = (package, document, relative)
        build = builder.node(
            ArchitectureNodeKind.BUILD_TARGET,
            name=f"cargo:build:{name}",
            path=package_path,
            language="rust",
            declared_by=relative,
            detail=f"cargo build -p {name}",
        )
        test = builder.node(
            ArchitectureNodeKind.TEST_TARGET,
            name=f"cargo:test:{name}",
            path=package_path,
            language="rust",
            declared_by=relative,
            detail=f"cargo test -p {name}",
        )
        builder.link(
            ArchitectureLinkKind.BUILDS,
            build,
            package,
            declared_by=relative,
            detail="cargo package target",
        )
        builder.link(
            ArchitectureLinkKind.TESTS,
            test,
            package,
            declared_by=relative,
            detail="cargo package target",
        )
        library = document.get("lib")
        if isinstance(library, dict) and isinstance(library.get("path"), str):
            raw_path = library["path"]
            surface_path = _joined_path(builder, package_path, raw_path, declared_by=relative)
            if not surface_path:
                continue
            surface = builder.node(
                ArchitectureNodeKind.PUBLIC_SURFACE,
                name=str(library.get("name") or name),
                path=surface_path,
                language="rust",
                declared_by=relative,
                detail=raw_path,
            )
            builder.link(
                ArchitectureLinkKind.EXPOSES,
                package,
                surface,
                declared_by=relative,
                detail="cargo lib target",
            )
        binaries = document.get("bin")
        if isinstance(binaries, list):
            for index, row in enumerate(binaries):
                if not isinstance(row, dict):
                    builder.limitations.add(f"cargo_bin_unparsed:{relative}:{index}")
                    continue
                binary_name = str(row.get("name") or "").strip()
                raw_path = str(row.get("path") or "").strip()
                if not binary_name or not raw_path:
                    builder.limitations.add(f"cargo_bin_incomplete:{relative}:{index}")
                    continue
                entry_path = _joined_path(builder, package_path, raw_path, declared_by=relative)
                if not entry_path:
                    continue
                entrypoint = builder.node(
                    ArchitectureNodeKind.ENTRY_POINT,
                    name=binary_name,
                    path=entry_path,
                    language="rust",
                    declared_by=relative,
                    detail=raw_path,
                )
                builder.link(
                    ArchitectureLinkKind.EXPOSES,
                    package,
                    entrypoint,
                    declared_by=relative,
                    detail="cargo bin target",
                )

    for _package_path, (package, document, relative) in sorted(documents.items()):
        manifest_parent = (builder.root / relative).parent
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            dependencies = document.get(section)
            if not isinstance(dependencies, dict):
                continue
            for dependency_name, specification in sorted(dependencies.items()):
                internal: ArchitectureNode | None = None
                if isinstance(specification, dict) and isinstance(specification.get("path"), str):
                    target_path = _safe_relative_directory(
                        builder, manifest_parent, specification["path"]
                    )
                    target = documents.get(target_path)
                    internal = target[0] if target is not None else None
                    if internal is None:
                        builder.limitations.add(
                            f"cargo_path_dependency_unresolved:{relative}:{dependency_name}"
                        )
                if internal is not None:
                    builder.link(
                        ArchitectureLinkKind.DEPENDS_ON,
                        package,
                        internal,
                        declared_by=relative,
                        detail="cargo path dependency",
                    )
                    continue
                detail = (
                    specification
                    if isinstance(specification, str)
                    else str(specification.get("version") or "")
                    if isinstance(specification, dict)
                    else ""
                )
                dependency = builder.node(
                    ArchitectureNodeKind.DEPENDENCY,
                    name=str(dependency_name),
                    path="",
                    language="rust",
                    declared_by=relative,
                    detail=detail,
                )
                builder.link(
                    ArchitectureLinkKind.DEPENDS_ON,
                    package,
                    dependency,
                    declared_by=relative,
                    detail=f"cargo {section}",
                )

    for component, workspace, relative in workspaces:
        patterns = _string_list(workspace.get("members"))
        workspace_path = component.path
        for package_path, (package, _, _) in sorted(documents.items()):
            local_path = package_path
            if workspace_path != ".":
                prefix = workspace_path.rstrip("/") + "/"
                if not package_path.startswith(prefix):
                    continue
                local_path = package_path[len(prefix) :]
            if any(Path(local_path).match(pattern) for pattern in patterns):
                builder.link(
                    ArchitectureLinkKind.CONTAINS,
                    component,
                    package,
                    declared_by=relative,
                    detail="cargo workspace member",
                )


def _xml_text(element: ElementTree.Element, path: str) -> str:
    found = element.find(path)
    return str(found.text or "").strip() if found is not None else ""


def _project_maven(builder: _ProjectionBuilder) -> None:
    documents: dict[str, tuple[ElementTree.Element, str, str, str, str]] = {}
    for relative in builder.discover(frozenset({"pom.xml"})):
        payload = builder.read(relative, "xml.etree")
        if payload is None:
            continue
        upper = payload.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            builder.limitations.add(f"xml_declarations_rejected:{relative}")
            continue
        try:
            document = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
            continue
        artifact = _xml_text(document, "./{*}artifactId")
        group = _xml_text(document, "./{*}groupId") or _xml_text(document, "./{*}parent/{*}groupId")
        packaging = _xml_text(document, "./{*}packaging") or "jar"
        if not artifact:
            builder.limitations.add(f"maven_artifact_id_missing:{relative}")
            continue
        if not group:
            builder.limitations.add(f"maven_group_id_missing:{relative}")
            continue
        package_path = _package_directory(relative)
        documents[package_path] = (document, group, artifact, packaging, relative)

    packages: dict[str, ArchitectureNode] = {}
    coordinates: dict[str, ArchitectureNode] = {}
    components: list[tuple[ArchitectureNode, ElementTree.Element, str]] = []
    for package_path, (document, group, artifact, packaging, relative) in sorted(documents.items()):
        coordinate = f"{group}:{artifact}"
        if packaging == "pom":
            component = builder.node(
                ArchitectureNodeKind.COMPONENT,
                name=coordinate,
                path=package_path,
                language="java",
                declared_by=relative,
                detail="maven reactor",
            )
            components.append((component, document, relative))
            continue
        package = builder.node(
            ArchitectureNodeKind.PACKAGE,
            name=coordinate,
            path=package_path,
            language="java",
            declared_by=relative,
            detail=packaging,
        )
        packages[package_path] = package
        coordinates[coordinate] = package
        surface = builder.node(
            ArchitectureNodeKind.PUBLIC_SURFACE,
            name=coordinate,
            path=package_path,
            language="java",
            declared_by=relative,
            detail=f"maven artifact:{packaging}",
        )
        build = builder.node(
            ArchitectureNodeKind.BUILD_TARGET,
            name=f"maven:package:{coordinate}",
            path=package_path,
            language="java",
            declared_by=relative,
            detail="mvn package",
        )
        test = builder.node(
            ArchitectureNodeKind.TEST_TARGET,
            name=f"maven:test:{coordinate}",
            path=package_path,
            language="java",
            declared_by=relative,
            detail="mvn test",
        )
        builder.link(
            ArchitectureLinkKind.EXPOSES,
            package,
            surface,
            declared_by=relative,
            detail="maven artifact",
        )
        builder.link(
            ArchitectureLinkKind.BUILDS,
            build,
            package,
            declared_by=relative,
            detail="maven lifecycle",
        )
        builder.link(
            ArchitectureLinkKind.TESTS,
            test,
            package,
            declared_by=relative,
            detail="maven lifecycle",
        )
        main_class = _xml_text(document, "./{*}properties/{*}exec.mainClass")
        if main_class:
            entrypoint = builder.node(
                ArchitectureNodeKind.ENTRY_POINT,
                name=main_class,
                path=package_path,
                language="java",
                declared_by=relative,
                detail="exec.mainClass",
            )
            builder.link(
                ArchitectureLinkKind.EXPOSES,
                package,
                entrypoint,
                declared_by=relative,
                detail="maven main class",
            )

    for package_path, (document, _, _, _, relative) in sorted(documents.items()):
        package = packages.get(package_path)
        if package is None:
            continue
        for dependency_row in document.findall("./{*}dependencies/{*}dependency"):
            group = _xml_text(dependency_row, "./{*}groupId")
            artifact = _xml_text(dependency_row, "./{*}artifactId")
            if not group or not artifact:
                builder.limitations.add(f"maven_dependency_incomplete:{relative}")
                continue
            coordinate = f"{group}:{artifact}"
            internal = coordinates.get(coordinate)
            if internal is not None:
                builder.link(
                    ArchitectureLinkKind.DEPENDS_ON,
                    package,
                    internal,
                    declared_by=relative,
                    detail="maven reactor dependency",
                )
                continue
            version = _xml_text(dependency_row, "./{*}version")
            dependency = builder.node(
                ArchitectureNodeKind.DEPENDENCY,
                name=coordinate,
                path="",
                language="java",
                declared_by=relative,
                detail=version,
            )
            builder.link(
                ArchitectureLinkKind.DEPENDS_ON,
                package,
                dependency,
                declared_by=relative,
                detail="maven dependency",
            )

    for component, document, relative in components:
        base = (builder.root / relative).parent
        for module in document.findall("./{*}modules/{*}module"):
            raw = str(module.text or "").strip()
            target_path = _safe_relative_directory(builder, base, raw)
            target = packages.get(target_path)
            if target is None:
                builder.limitations.add(f"maven_module_unresolved:{relative}:{raw}")
                continue
            builder.link(
                ArchitectureLinkKind.CONTAINS,
                component,
                target,
                declared_by=relative,
                detail="maven module",
            )


def _strip_gradle_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    quote = ""
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quote:
            output.append(current)
            if current == "\\" and index + 1 < len(text):
                index += 1
                output.append(text[index])
            elif current == quote:
                quote = ""
            index += 1
            continue
        if current in {"'", '"'}:
            quote = current
            output.append(current)
            index += 1
            continue
        if current == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if current == "/" and following == "*":
            index += 2
            while index < len(text):
                if text[index] in "\r\n":
                    output.append(text[index])
                if text[index : index + 2] == "*/":
                    index += 2
                    break
                index += 1
            continue
        output.append(current)
        index += 1
    return "".join(output)


def _project_gradle(builder: _ProjectionBuilder) -> None:
    settings_names = frozenset({"settings.gradle", "settings.gradle.kts"})
    build_names = frozenset({"build.gradle", "build.gradle.kts"})
    build_paths = {
        _package_directory(relative): relative for relative in builder.discover(build_names)
    }
    settings_paths = builder.discover(settings_names)
    if build_paths or settings_paths:
        builder.limitations.add("gradle_dynamic_expressions_not_interpreted")
    packages: dict[str, tuple[ArchitectureNode, str, str]] = {}
    project_keys: dict[tuple[str, str], ArchitectureNode] = {}

    for relative in settings_paths:
        payload = builder.read(relative, "gradle-static-literals")
        if payload is None:
            continue
        try:
            text = _strip_gradle_comments(payload.decode("utf-8"))
        except UnicodeError as exc:
            builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
            continue
        root_match = _GRADLE_ROOT_NAME.search(text)
        root_name = root_match.group(1) if root_match else Path(builder.root).name
        workspace_path = _package_directory(relative)
        component = builder.node(
            ArchitectureNodeKind.COMPONENT,
            name=f"gradle:{root_name}",
            path=workspace_path,
            language="java",
            declared_by=relative,
            detail="gradle multi-project build",
        )
        included: set[str] = set()
        for match in _GRADLE_INCLUDE.finditer(text):
            arguments = match.group(1) or match.group(2) or ""
            included.update(_GRADLE_QUOTED.findall(arguments))
        for project_key in sorted(included):
            if not project_key.startswith(":"):
                builder.limitations.add(f"gradle_include_unparsed:{relative}:{project_key}")
                continue
            local = project_key.strip(":").replace(":", "/")
            package_path = _joined_path(builder, workspace_path, local, declared_by=relative)
            if not package_path:
                continue
            build_relative = build_paths.get(package_path)
            if build_relative is None:
                builder.limitations.add(f"gradle_build_file_missing:{relative}:{project_key}")
                continue
            coordinate = f"{root_name}:{project_key.strip(':').replace(':', ':')}"
            package = builder.node(
                ArchitectureNodeKind.PACKAGE,
                name=coordinate,
                path=package_path,
                language="java",
                declared_by=build_relative,
                detail=project_key,
            )
            packages[package_path] = (package, build_relative, project_key)
            project_keys[(workspace_path, project_key)] = package
            builder.link(
                ArchitectureLinkKind.CONTAINS,
                component,
                package,
                declared_by=relative,
                detail="gradle include",
            )

    for package_path, (package, relative, project_key) in sorted(packages.items()):
        payload = builder.read(relative, "gradle-static-literals")
        if payload is None:
            continue
        try:
            text = _strip_gradle_comments(payload.decode("utf-8"))
        except UnicodeError as exc:
            builder.limitations.add(f"manifest_parse_error:{relative}:{type(exc).__name__}")
            continue
        build = builder.node(
            ArchitectureNodeKind.BUILD_TARGET,
            name=f"gradle:build:{package.name}",
            path=package_path,
            language="java",
            declared_by=relative,
            detail=f"{project_key}:build",
        )
        test = builder.node(
            ArchitectureNodeKind.TEST_TARGET,
            name=f"gradle:test:{package.name}",
            path=package_path,
            language="java",
            declared_by=relative,
            detail=f"{project_key}:test",
        )
        surface = builder.node(
            ArchitectureNodeKind.PUBLIC_SURFACE,
            name=package.name,
            path=package_path,
            language="java",
            declared_by=relative,
            detail="gradle project artifact",
        )
        builder.link(
            ArchitectureLinkKind.BUILDS,
            build,
            package,
            declared_by=relative,
            detail="gradle build task",
        )
        builder.link(
            ArchitectureLinkKind.TESTS,
            test,
            package,
            declared_by=relative,
            detail="gradle test task",
        )
        builder.link(
            ArchitectureLinkKind.EXPOSES,
            package,
            surface,
            declared_by=relative,
            detail="gradle artifact",
        )
        main_match = _GRADLE_MAIN_CLASS.search(text)
        if main_match:
            entrypoint = builder.node(
                ArchitectureNodeKind.ENTRY_POINT,
                name=main_match.group(1),
                path=package_path,
                language="java",
                declared_by=relative,
                detail="application.mainClass",
            )
            builder.link(
                ArchitectureLinkKind.EXPOSES,
                package,
                entrypoint,
                declared_by=relative,
                detail="gradle application main class",
            )
        workspace_path = "."
        for candidate_workspace, _ in sorted(
            {(key[0], key[1]) for key in project_keys}, key=lambda row: len(row[0]), reverse=True
        ):
            if candidate_workspace == "." or package_path.startswith(
                candidate_workspace.rstrip("/") + "/"
            ):
                workspace_path = candidate_workspace
                break
        for match in _GRADLE_PROJECT_DEPENDENCY.finditer(text):
            target_key = match.group(2)
            target = project_keys.get((workspace_path, target_key))
            if target is None:
                builder.limitations.add(
                    f"gradle_project_dependency_unresolved:{relative}:{target_key}"
                )
                continue
            builder.link(
                ArchitectureLinkKind.DEPENDS_ON,
                package,
                target,
                declared_by=relative,
                detail="gradle project dependency",
            )
        for match in _GRADLE_EXTERNAL_DEPENDENCY.finditer(text):
            configuration, group, artifact, version = match.groups()
            dependency = builder.node(
                ArchitectureNodeKind.DEPENDENCY,
                name=f"{group}:{artifact}",
                path="",
                language="java",
                declared_by=relative,
                detail=version,
            )
            builder.link(
                ArchitectureLinkKind.DEPENDS_ON,
                package,
                dependency,
                declared_by=relative,
                detail=f"gradle {configuration}",
            )


def project_repository_architecture(
    root: str | Path,
    *,
    source_revision: str,
    limits: ProjectionLimits | None = None,
) -> RepositoryArchitectureProjection:
    """Return a bounded, source-bound architectural projection.

    The function has no subprocess, import, network, write, or model side
    effects.  ``source_revision`` must come from the caller's authoritative
    repository snapshot receipt.
    """

    repository = Path(root).resolve(strict=True)
    if not repository.is_dir():
        raise NotADirectoryError(repository)
    if not str(source_revision or "").strip():
        raise ValueError("source_revision is required")
    builder = _ProjectionBuilder(repository, str(source_revision), limits or ProjectionLimits())
    _project_pyproject(builder)
    _project_setup_cfg(builder)
    _project_setup_py(builder)
    _project_pytest_ini(builder)
    _project_tox(builder)
    _project_javascript(builder)
    _project_go(builder)
    _project_rust(builder)
    _project_maven(builder)
    _project_gradle(builder)
    return builder.finish()


_FACT_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.:@/-]{2,}")


def select_architecture_facts(
    projection: RepositoryArchitectureProjection,
    *,
    task: str,
    anchor_paths: tuple[str, ...] = (),
    limit: int = 8,
) -> tuple[ArchitectureFact, ...]:
    """Select bounded manifest facts relevant to a task or graph-localized path."""

    if limit <= 0:
        return ()
    query_tokens = {
        token.casefold()
        for token in _FACT_TOKEN.findall(task)
        if len(token) >= 3
    }
    normalized_anchors = tuple(
        path.replace("\\", "/").strip("/").casefold()
        for path in anchor_paths
        if path.strip()
    )

    def score(node: ArchitectureNode) -> tuple[int, int, str]:
        path = node.path.replace("\\", "/").strip("/").casefold()
        material = " ".join((node.name, node.path, node.detail)).casefold()
        token_hits = sum(token in material for token in query_tokens)
        path_hits = sum(
            bool(path and (anchor == path or anchor.startswith(path + "/")))
            for anchor in normalized_anchors
        )
        root_package = int(
            node.kind is ArchitectureNodeKind.PACKAGE
            and node.path in {"", "."}
            and bool(normalized_anchors)
        )
        value = path_hits * 20 + token_hits * 4 + root_package
        return value, token_hits + path_hits, node.id

    ranked = sorted(
        ((score(node), node) for node in projection.nodes),
        key=lambda row: (-row[0][0], -row[0][1], row[0][2]),
    )
    facts: list[ArchitectureFact] = []
    for (value, _hits, _node_id), node in ranked:
        if value <= 0:
            break
        material = "\0".join(
            (
                projection.source_revision,
                projection.manifest_revision,
                node.id,
            )
        ).encode()
        facts.append(
            ArchitectureFact(
                claim_id="gt-architecture-" + hashlib.sha256(material).hexdigest()[:24],
                kind=node.kind,
                name=node.name,
                path=node.path,
                declared_by=node.declared_by,
                detail=node.detail,
            )
        )
        if len(facts) >= limit:
            break
    return tuple(facts)


__all__ = [
    "ArchitectureLink",
    "ArchitectureLinkKind",
    "ArchitectureFact",
    "ArchitectureNode",
    "ArchitectureNodeKind",
    "ManifestReceipt",
    "ProjectionLimits",
    "RepositoryArchitectureProjection",
    "project_repository_architecture",
    "select_architecture_facts",
]
