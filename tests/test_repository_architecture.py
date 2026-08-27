from __future__ import annotations

import tomllib
from pathlib import Path

from gt_engine.repository_architecture import (
    ArchitectureLinkKind,
    ArchitectureNodeKind,
    ProjectionLimits,
    project_repository_architecture,
    select_architecture_facts,
)

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_discovery_is_single_pass_and_fact_selection_is_task_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    import gt_engine.repository_architecture as architecture

    (tmp_path / "src").mkdir()
    (tmp_path / "package.json").write_text(
        '{"name":"sample","exports":{".":"./src/index.ts"},'
        '"scripts":{"test":"vitest run"}}',
        encoding="utf-8",
    )
    (tmp_path / "go.mod").write_text("module example.test/sample\n", encoding="utf-8")
    real_walk = architecture.os.walk
    calls = 0

    def counted_walk(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr(architecture.os, "walk", counted_walk)
    projection = project_repository_architecture(tmp_path, source_revision="source-one-pass")
    facts = select_architecture_facts(
        projection,
        task="update the exported index surface and its test target",
        anchor_paths=("src/index.ts",),
    )

    assert calls == 1
    assert facts
    assert all(fact.claim_id.startswith("gt-architecture-") for fact in facts)
    assert any(fact.path == "src/index.ts" for fact in facts)
    assert facts == select_architecture_facts(
        projection,
        task="update the exported index surface and its test target",
        anchor_paths=("src/index.ts",),
    )


def test_python_manifests_project_packages_entrypoints_build_and_tests(tmp_path: Path) -> None:
    (tmp_path / "src" / "sample").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "sample-app"
dependencies = ["httpx>=0.27", "rich"]

[project.scripts]
sample = "sample.cli:main"

[build-system]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "tox.ini").write_text(
        "[tox]\nenvlist = py312, lint\n",
        encoding="utf-8",
    )

    projection = project_repository_architecture(
        tmp_path,
        source_revision="source-python-1",
    )

    assert projection.source_revision == "source-python-1"
    assert projection.manifest_revision
    assert [receipt.path for receipt in projection.manifests] == [
        "pyproject.toml",
        "tox.ini",
    ]
    assert any(
        node.kind is ArchitectureNodeKind.PACKAGE and node.name == "sample-app" and node.path == "."
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.ENTRY_POINT
        and node.name == "sample"
        and node.detail == "sample.cli:main"
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.BUILD_TARGET
        and node.name == "sample-app"
        and node.detail == "hatchling.build"
        for node in projection.nodes
    )
    test_names = {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.TEST_TARGET
    }
    assert test_names == {"pytest:tests", "tox:lint", "tox:py312"}
    dependency_names = {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.DEPENDENCY
    }
    assert dependency_names == {"httpx", "rich"}
    assert any(link.kind is ArchitectureLinkKind.DEPENDS_ON for link in projection.links)
    assert any(link.kind is ArchitectureLinkKind.TESTS for link in projection.links)
    assert projection.limitations == ()


def test_javascript_workspace_projects_public_build_test_and_dependency_links(
    tmp_path: Path,
) -> None:
    (tmp_path / "packages" / "core" / "src").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        """
{
  "name": "workspace-root",
  "private": true,
  "workspaces": ["packages/*"],
  "scripts": {"build": "tsc -b", "test": "vitest run"},
  "bin": {"workspace": "src/cli.ts"},
  "exports": {".": "./src/index.ts"},
  "dependencies": {"kleur": "^4.1.5"},
  "jest": {"testMatch": ["**/*.spec.ts"]}
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "packages" / "core" / "package.json").write_text(
        """
{
  "name": "@workspace/core",
  "main": "src/index.ts",
  "scripts": {"build": "tsc", "test": "jest"},
  "devDependencies": {"typescript": "^5.0.0"}
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"references":[{"path":"packages/core"}]}',
        encoding="utf-8",
    )
    (tmp_path / "vitest.config.ts").write_text(
        "export default { test: { globals: true } }\n",
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-js-1")

    packages = {
        (node.name, node.path)
        for node in projection.nodes
        if node.kind is ArchitectureNodeKind.PACKAGE
    }
    assert packages == {("workspace-root", "."), ("@workspace/core", "packages/core")}
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.BUILD_TARGET
    } == {"npm:build", "npm:build:@workspace/core"}
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.TEST_TARGET
    } == {
        "jest:package-config",
        "npm:test",
        "npm:test:@workspace/core",
        "vitest:vitest.config.ts",
    }
    assert any(
        node.kind is ArchitectureNodeKind.ENTRY_POINT and node.name == "workspace"
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.PUBLIC_SURFACE and node.detail == "./src/index.ts"
        for node in projection.nodes
    )
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.DEPENDENCY
    } == {"kleur", "typescript"}
    assert any(
        link.kind is ArchitectureLinkKind.CONTAINS and link.detail == "workspace"
        for link in projection.links
    )
    assert projection.limitations == ("dynamic_test_config_not_interpreted:vitest.config.ts",)


def test_go_workspace_projects_modules_targets_and_internal_external_dependencies(
    tmp_path: Path,
) -> None:
    (tmp_path / "cmd").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "go.work").write_text(
        "go 1.22\nuse (\n  ./cmd\n  ./lib\n)\n",
        encoding="utf-8",
    )
    (tmp_path / "cmd" / "go.mod").write_text(
        "module example.test/cmd\n\ngo 1.22\nrequire example.test/lib v0.0.0\n",
        encoding="utf-8",
    )
    (tmp_path / "lib" / "go.mod").write_text(
        "module example.test/lib\n\ngo 1.22\nrequire (\n  golang.org/x/sync v0.8.0\n)\n",
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-go-1")

    assert {
        (node.name, node.path)
        for node in projection.nodes
        if node.kind is ArchitectureNodeKind.PACKAGE
    } == {("example.test/cmd", "cmd"), ("example.test/lib", "lib")}
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.BUILD_TARGET
    } == {"go:build:example.test/cmd", "go:build:example.test/lib"}
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.TEST_TARGET
    } == {"go:test:example.test/cmd", "go:test:example.test/lib"}
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.DEPENDENCY
    } == {"golang.org/x/sync"}
    assert any(
        link.kind is ArchitectureLinkKind.DEPENDS_ON and link.detail == "go.mod internal require"
        for link in projection.links
    )
    assert (
        sum(
            link.kind is ArchitectureLinkKind.CONTAINS and link.detail == "go.work use"
            for link in projection.links
        )
        == 2
    )
    assert projection.limitations == ("go_entrypoints_not_declared_by_static_manifests",)


def test_cargo_workspace_projects_crates_surfaces_targets_and_path_dependencies(
    tmp_path: Path,
) -> None:
    (tmp_path / "crates" / "core" / "src").mkdir(parents=True)
    (tmp_path / "crates" / "cli" / "src").mkdir(parents=True)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/core", "crates/cli"]\n',
        encoding="utf-8",
    )
    (tmp_path / "crates" / "core" / "Cargo.toml").write_text(
        """
[package]
name = "sample-core"
version = "0.1.0"

[lib]
path = "src/lib.rs"

[dependencies]
serde = "1"

[dev-dependencies]
proptest = "1"
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "crates" / "cli" / "Cargo.toml").write_text(
        """
[package]
name = "sample-cli"
version = "0.1.0"

[[bin]]
name = "sample"
path = "src/main.rs"

[dependencies]
sample-core = { path = "../core" }
""".strip(),
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-rust-1")

    assert {
        (node.name, node.path)
        for node in projection.nodes
        if node.kind is ArchitectureNodeKind.PACKAGE
    } == {("sample-cli", "crates/cli"), ("sample-core", "crates/core")}
    assert any(
        node.kind is ArchitectureNodeKind.PUBLIC_SURFACE and node.path == "crates/core/src/lib.rs"
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.ENTRY_POINT
        and node.name == "sample"
        and node.path == "crates/cli/src/main.rs"
        for node in projection.nodes
    )
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.DEPENDENCY
    } == {"proptest", "serde"}
    assert any(
        link.kind is ArchitectureLinkKind.DEPENDS_ON and link.detail == "cargo path dependency"
        for link in projection.links
    )
    assert (
        sum(
            link.kind is ArchitectureLinkKind.CONTAINS and link.detail == "cargo workspace member"
            for link in projection.links
        )
        == 2
    )
    assert projection.limitations == ()


def test_maven_reactor_projects_modules_artifacts_entrypoints_and_dependency_links(
    tmp_path: Path,
) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "pom.xml").write_text(
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example.test</groupId><artifactId>sample-parent</artifactId><version>1</version>
  <packaging>pom</packaging>
  <modules><module>core</module><module>app</module></modules>
</project>
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "core" / "pom.xml").write_text(
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent><groupId>example.test</groupId><artifactId>sample-parent</artifactId><version>1</version></parent>
  <artifactId>sample-core</artifactId>
  <dependencies>
    <dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId><version>2.17.2</version></dependency>
  </dependencies>
</project>
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "app" / "pom.xml").write_text(
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent><groupId>example.test</groupId><artifactId>sample-parent</artifactId><version>1</version></parent>
  <artifactId>sample-app</artifactId>
  <properties><exec.mainClass>example.test.Main</exec.mainClass></properties>
  <dependencies>
    <dependency><groupId>example.test</groupId><artifactId>sample-core</artifactId><version>1</version></dependency>
  </dependencies>
</project>
""".strip(),
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-maven-1")

    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.PACKAGE
    } == {"example.test:sample-app", "example.test:sample-core"}
    assert any(
        node.kind is ArchitectureNodeKind.ENTRY_POINT and node.name == "example.test.Main"
        for node in projection.nodes
    )
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.DEPENDENCY
    } == {"com.fasterxml.jackson.core:jackson-databind"}
    assert any(
        link.kind is ArchitectureLinkKind.DEPENDS_ON and link.detail == "maven reactor dependency"
        for link in projection.links
    )
    assert (
        sum(
            link.kind is ArchitectureLinkKind.CONTAINS and link.detail == "maven module"
            for link in projection.links
        )
        == 2
    )
    assert projection.limitations == ()


def test_gradle_settings_and_build_files_project_static_multi_project_architecture(
    tmp_path: Path,
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'sample'\ninclude ':app', ':lib'\n",
        encoding="utf-8",
    )
    (tmp_path / "app" / "build.gradle").write_text(
        """
plugins { id 'application' }
application { mainClass = 'example.test.Main' }
dependencies {
  implementation project(':lib')
  testImplementation 'org.junit.jupiter:junit-jupiter:5.10.0'
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "lib" / "build.gradle").write_text(
        """
plugins { id 'java-library' }
dependencies { api 'com.google.guava:guava:33.2.1-jre' }
""".strip(),
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-gradle-1")

    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.PACKAGE
    } == {"sample:app", "sample:lib"}
    assert any(
        node.kind is ArchitectureNodeKind.ENTRY_POINT and node.name == "example.test.Main"
        for node in projection.nodes
    )
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.DEPENDENCY
    } == {"com.google.guava:guava", "org.junit.jupiter:junit-jupiter"}
    assert any(
        link.kind is ArchitectureLinkKind.DEPENDS_ON and link.detail == "gradle project dependency"
        for link in projection.links
    )
    assert (
        sum(
            link.kind is ArchitectureLinkKind.CONTAINS and link.detail == "gradle include"
            for link in projection.links
        )
        == 2
    )
    assert projection.limitations == ("gradle_dynamic_expressions_not_interpreted",)


def test_setup_py_is_parsed_as_ast_without_execution_and_pytest_ini_is_linked(
    tmp_path: Path,
) -> None:
    (tmp_path / "setup.py").write_text(
        """
from setuptools import setup
raise RuntimeError("must never execute")
setup(
    name="legacy-package",
    py_modules=["legacy"],
    install_requires=["requests>=2"],
    entry_points={"console_scripts": ["legacy=legacy:main"]},
)
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests integration_tests\n",
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-setup-1")

    assert any(
        node.kind is ArchitectureNodeKind.PACKAGE and node.name == "legacy-package"
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.PUBLIC_SURFACE and node.detail == "legacy"
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.ENTRY_POINT
        and node.name == "legacy"
        and node.detail == "legacy:main"
        for node in projection.nodes
    )
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.TEST_TARGET
    } == {"pytest:integration_tests", "pytest:tests"}
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.DEPENDENCY
    } == {"requests"}
    assert projection.limitations == ("setup_py_dynamic_expressions_not_interpreted",)


def test_setup_cfg_projects_literal_package_metadata_and_embedded_pytest_target(
    tmp_path: Path,
) -> None:
    (tmp_path / "setup.cfg").write_text(
        """
[metadata]
name = configured-package

[options]
py_modules = configured
install_requires =
    click>=8

[options.entry_points]
console_scripts =
    configured = configured:main

[tool:pytest]
testpaths = tests
""".strip(),
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-cfg-1")

    assert any(
        node.kind is ArchitectureNodeKind.PACKAGE and node.name == "configured-package"
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.ENTRY_POINT and node.name == "configured"
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.PUBLIC_SURFACE and node.detail == "configured"
        for node in projection.nodes
    )
    assert any(
        node.kind is ArchitectureNodeKind.TEST_TARGET and node.name == "pytest:tests"
        for node in projection.nodes
    )
    assert projection.limitations == ()


def test_declared_public_path_cannot_escape_repository(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"bounded","exports":{".":"../../outside.js"}}',
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-safe-1")

    assert not any(node.kind is ArchitectureNodeKind.PUBLIC_SURFACE for node in projection.nodes)
    assert projection.limitations == (
        "declared_path_outside_repository:package.json:../../outside.js",
    )


def test_architecture_projection_is_in_product_and_wheel_surfaces() -> None:
    product = tomllib.loads((ROOT / "production-surface.toml").read_text(encoding="utf-8"))
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "gt_engine.repository_architecture" in product["python_modules"]
    assert (
        "gt_engine/repository_architecture.py"
        in package["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"]
    )
    assert (
        "test_repository_architecture.py"
        in package["tool"]["pytest"]["ini_options"]["python_files"]
    )


def test_setup_call_nested_in_dead_code_is_not_architectural_evidence(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text(
        "if False:\n    setup(name='invented-package')\n",
        encoding="utf-8",
    )

    projection = project_repository_architecture(tmp_path, source_revision="source-dead-1")

    assert not any(node.kind is ArchitectureNodeKind.PACKAGE for node in projection.nodes)
    assert projection.limitations == (
        "setup_py_dynamic_expressions_not_interpreted",
        "setup_py_setup_call_missing",
    )


def test_commented_gradle_literals_do_not_create_architectural_facts(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "ghost").mkdir()
    (tmp_path / "settings.gradle").write_text(
        """
rootProject.name = 'bounded'
/*
include ':ghost'
*/
include ':app'
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "app" / "build.gradle").write_text(
        """
dependencies {
  // implementation project(':ghost')
  /* api 'fake:dependency:1' */
  implementation 'real:dependency:1'
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "ghost" / "build.gradle").write_text("plugins {}\n", encoding="utf-8")

    projection = project_repository_architecture(tmp_path, source_revision="source-comments-1")

    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.PACKAGE
    } == {"bounded:app"}
    assert {
        node.name for node in projection.nodes if node.kind is ArchitectureNodeKind.DEPENDENCY
    } == {"real:dependency"}


def test_projection_is_deterministic_and_limits_are_explicit(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"bounded","dependencies":{"z":"1","a":"1","m":"1"},'
        '"scripts":{"build":"builder","test":"tester"}}',
        encoding="utf-8",
    )

    first = project_repository_architecture(tmp_path, source_revision="source-limits-1")
    second = project_repository_architecture(tmp_path, source_revision="source-limits-1")
    node_bounded = project_repository_architecture(
        tmp_path,
        source_revision="source-limits-1",
        limits=ProjectionLimits(maximum_nodes=2),
    )
    link_bounded = project_repository_architecture(
        tmp_path,
        source_revision="source-limits-1",
        limits=ProjectionLimits(maximum_links=1),
    )

    assert first == second
    assert len(node_bounded.nodes) == 2
    assert "node_limit_reached" in node_bounded.limitations
    assert len(link_bounded.links) == 1
    assert "link_limit_reached" in link_bounded.limitations
