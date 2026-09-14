"""Installed-wheel language-matrix invariants.

These exercise the certified wheel as the benchmark installs it — not the
source tree. A capability that only works in the gsrc checkout but not in the
vendored wheel is exactly the defect class the identity gate exists to catch.

Covers: runner/pass/fail recognition per language, adapter-emitted command
bindability, validation-observation classification per language, and the
prose-counterfeit boundary.
"""
from __future__ import annotations

import shlex

import pytest

from groundtruth.runtime.patterns import (
    TEST_FAIL_RE,
    TEST_PASS_RE,
    TEST_RUNNER_RE,
    ValidationKind,
    classify_validation_observation,
)


def test_runner_positive_matrix_binds_on_installed_wheel():
    """Every runner family the binder admits must match on the installed
    wheel — path-prefixed wrappers included (the ./mvnw class)."""
    for cmd in (
        "pytest -q", "python -m pytest", "go test ./...", "cargo test",
        "cargo nextest run", "npm test", "yarn test", "pnpm test",
        "bun test", "deno test", "node --test",
        "npx jest", "node_modules/.bin/jest", "npx vitest run", "npx mocha",
        "bundle exec rspec", "rspec spec/",
        "vendor/bin/phpunit", "phpunit", "composer test",
        "mvn test", "./mvnw test", "gradle test", "./gradlew test",
        "sbt test", "sbt 'testOnly *x*'", "mix test", "dotnet test",
        "bazel test //...", "nx test", "turbo run test",
        "make test", "make check", "ctest", "rake test",
        "sudo pytest -q", "python manage.py test",
    ):
        assert TEST_RUNNER_RE.search(cmd), f"installed wheel rejects runner: {cmd}"


def test_runner_negative_matrix_never_binds():
    for cmd in (
        "mvn package", "gradle build", "dotnet build", "sbt compile",
        "mix compile", "composer install", "turbo build", "nx build",
        "npm run build", "go build ./...", "cargo build",
        "cat test.log", "echo pytest", "grep FAILED out.txt",
    ):
        assert not TEST_RUNNER_RE.search(cmd), f"build/install bound as test: {cmd}"


def test_pass_fail_markers_per_language_on_installed_wheel():
    greens = (
        "5 passed in 1.2s", "PASSED", "ok\tpkg/foo\t0.012s",
        "BUILD SUCCESS", "OK (5 tests)", "Tests: 5 passed",
        "Passed: 5", "5 examples, 0 failures", "5 pass", "[success]",
    )
    reds = (
        "2 failed", "FAILED", "FAIL pkg/foo", "3 errors",
        "Failed: 3", "2 examples, 1 failure", "2 fail",
        "Failures: 2", "Errors: 1",
    )
    for out in greens:
        assert TEST_PASS_RE.search(out), f"green marker missed: {out}"
    for out in reds:
        assert TEST_FAIL_RE.search(out), f"red marker missed: {out}"


def test_adapter_emitted_commands_bind_on_installed_wheel(tmp_path):
    """The adapter->binder round-trip on the installed wheel: every command
    an adapter emits must be binder-admissible (./mvnw regression guard)."""
    from groundtruth.runtime.repo_adapters import (
        GoRepoAdapter,
        JavaRepoAdapter,
        JavaScriptRepoAdapter,
        PythonRepoAdapter,
        RustRepoAdapter,
    )

    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    (tmp_path / "go.mod").write_text("module example.com/x\n")
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    (tmp_path / "mvnw").write_text("#!/bin/sh\n")
    (tmp_path / "gradlew").write_text("#!/bin/sh\n")

    emitted = []
    for adapter in (
        PythonRepoAdapter(), JavaScriptRepoAdapter(), GoRepoAdapter(),
        RustRepoAdapter(), JavaRepoAdapter(),
    ):
        emitted.extend(adapter.test_commands(tmp_path))
    assert emitted
    for argv in emitted:
        assert TEST_RUNNER_RE.search(shlex.join(argv)), (
            f"adapter emitted unbindable command: {argv}"
        )


def test_validation_observation_per_language_on_installed_wheel():
    """Static-check and compiler-check invocations must classify — an
    invocation GT cannot name is invisible evidence."""
    cases = [
        ("mypy src/", ValidationKind.STATIC_CHECK),
        ("bundle exec rubocop", ValidationKind.STATIC_CHECK),
        ("vendor/bin/phpstan analyse", ValidationKind.STATIC_CHECK),
        ("node_modules/.bin/eslint src/", ValidationKind.STATIC_CHECK),
        ("go vet ./...", ValidationKind.STATIC_CHECK),
        ("cargo clippy", ValidationKind.STATIC_CHECK),
        ("checkstyle -c checks.xml src/", ValidationKind.STATIC_CHECK),
        ("detekt --input src/", ValidationKind.STATIC_CHECK),
        ("mix credo", ValidationKind.STATIC_CHECK),
        ("clang-tidy x.cpp", ValidationKind.STATIC_CHECK),
        ("dotnet format --verify-no-changes", ValidationKind.STATIC_CHECK),
        ("go build ./...", ValidationKind.COMPILER_CHECK),
        ("cargo check", ValidationKind.COMPILER_CHECK),
        ("./mvnw compile", ValidationKind.COMPILER_CHECK),
        ("./gradlew assembleDebug", ValidationKind.COMPILER_CHECK),
        ("dotnet build", ValidationKind.COMPILER_CHECK),
        ("sbt compile", ValidationKind.COMPILER_CHECK),
        ("mix compile", ValidationKind.COMPILER_CHECK),
        ("bazel build //...", ValidationKind.COMPILER_CHECK),
        ("g++ -O2 x.cpp", ValidationKind.COMPILER_CHECK),
        ("javac Main.java", ValidationKind.COMPILER_CHECK),
        ("python -c 'import x'", ValidationKind.RUNTIME_PROBE),
    ]
    for cmd, kind in cases:
        obs = classify_validation_observation(cmd, "", 0)
        assert obs.kind is kind, f"{cmd} -> {obs.kind}, want {kind}"


def test_env_fail_markers_per_language_on_installed_wheel():
    """Missing-dep/toolchain failures must read env_fail, not fail — a `fail`
    outcome accuses the hypothesis when the environment is at fault."""
    from groundtruth.runtime.patterns import ENV_FAIL_RE

    cases = (
        "ModuleNotFoundError: No module named 'x'",            # python
        "cannot find package \"x/y\" in any of:",               # go
        "missing go.sum entry for module",                      # go
        "Error: Cannot find module 'left-pad'",                 # node
        "MODULE_NOT_FOUND",                                     # node
        "error: no matching package named `serde` found",       # cargo
        "can't find crate for `alloc`",                         # cargo
        "java.lang.ClassNotFoundException: com.x.Y",            # java
        "Could not resolve dependencies for project",           # maven
        "LoadError: cannot load such file -- x",                # ruby
        "Could not find gem 'rails'",                           # bundler
        "PHP Fatal error:  Uncaught Error: Class \"X\" not found",  # php
        "error NU1101: Unable to find package X",               # dotnet
        "error MSB3644: The reference assemblies for .NETFramework",  # sdk missing
        "error: no such module 'Alamofire'",                    # swift
        "** (Mix.Error) Could not compile dependency",          # elixir
        "fatal error: missing.h: No such file or directory",    # c/c++
        "undefined reference to `main'",                        # linker
        "command not found: deno",                              # missing tool
    )
    for out in cases:
        assert ENV_FAIL_RE.search(out), f"env marker missed: {out}"


def test_runner_output_name_parsing_per_language_on_installed_wheel():
    """smoke20 fd: a count-only baseline can never prove conservation. The
    wheel must recover test NAMES from each runner's native output so
    conservation is provable upstream of compare_results."""
    from groundtruth.runtime.test_runner import (
        _parse_failing_test_names,
        _parse_passing_test_names,
        _parse_test_output,
    )

    cargo_out = (
        "running 3 tests\n"
        "test sorting_test ... ok\n"
        "test merge_test ... ok\n"
        "test dedup_test ... FAILED\n"
        "test result: FAILED. 2 passed; 1 failed; 0 ignored\n"
    )
    counts = _parse_test_output(cargo_out, ["cargo", "test"])
    assert counts["passed"] == 2 and counts["failed"] == 1
    assert _parse_passing_test_names(cargo_out) == ["sorting_test", "merge_test"]
    assert _parse_failing_test_names(cargo_out) == ["dedup_test"]

    go_out = "--- PASS: TestSort (0.01s)\n--- FAIL: TestMerge (0.01s)\nFAIL\tpkg/x\t0.02s\n"
    counts = _parse_test_output(go_out, ["go", "test", "./..."])
    assert counts["passed"] == 1 and counts["failed"] == 1
    assert "TestSort" in _parse_passing_test_names(go_out)
    assert "TestMerge" in _parse_failing_test_names(go_out)

    dotnet_out = "Passed MyApp.Tests.Sort.Asc [15 ms]\nFailed MyApp.Tests.Sort.Desc [3 ms]\nFailed!  - Failed: 1, Passed: 1\n"
    counts = _parse_test_output(dotnet_out, ["dotnet", "test"])
    assert counts["passed"] == 1 and counts["failed"] == 1
    assert _parse_passing_test_names(dotnet_out) == ["MyApp.Tests.Sort.Asc"]
    assert _parse_failing_test_names(dotnet_out) == ["MyApp.Tests.Sort.Desc"]

    jest_out = "  ✓ sorting asc (5 ms)\n  ✕ sorting desc\nTests: 1 failed, 1 passed\n"
    counts = _parse_test_output(jest_out, ["npx", "jest"])
    assert counts["passed"] == 1 and counts["failed"] == 1
    assert _parse_passing_test_names(jest_out) == ["sorting asc"]
    assert _parse_failing_test_names(jest_out) == ["sorting desc"]

    bun_out = " 5 pass\n 1 fail\n"
    counts = _parse_test_output(bun_out, ["bun", "test"])
    assert counts["passed"] == 5 and counts["failed"] == 1

    dotnet_summary = "Passed: 5\nFailed: 2\n"
    counts = _parse_test_output(dotnet_summary, ["dotnet", "test"])
    assert counts["passed"] == 5 and counts["failed"] == 2

    mix_out = "12 tests, 2 failures"
    counts = _parse_test_output(mix_out, ["mix", "test"])
    assert counts["passed"] == 10 and counts["failed"] == 2

    phpunit_out = "OK (7 tests, 14 assertions)"
    counts = _parse_test_output(phpunit_out, ["vendor/bin/phpunit"])
    assert counts["passed"] == 7

    bazel_out = "//pkg:sort_test PASSED in 0.4s\n//pkg:merge_test FAILED in 0.2s\n"
    counts = _parse_test_output(bazel_out, ["bazel", "test", "//..."])
    assert counts["passed"] == 1 and counts["failed"] == 1

    sbt_out = "[info] Tests: succeeded 8, failed 1\n"
    counts = _parse_test_output(sbt_out, ["sbt", "test"])
    assert counts["passed"] == 8 and counts["failed"] == 1


def test_validation_outcome_semantics_on_installed_wheel():
    # code-attributed diagnostic -> fail
    obs = classify_validation_observation(
        "gcc x.c", "x.c:5:10: error: expected ';'", 1
    )
    assert obs.outcome == "fail" and obs.disconfirming
    # fatal error missing header -> env truth, never disconfirming
    obs = classify_validation_observation(
        "gcc x.c", "x.c:5:10: fatal error: missing.h", 1
    )
    assert obs.outcome == "env_fail" and not obs.disconfirming
    # viewing a diagnostic log is never a validation act
    obs = classify_validation_observation(
        "cat build.log", "x.c:5:10: error: bad", 1
    )
    assert obs.kind is ValidationKind.NONE
