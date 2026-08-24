from __future__ import annotations

from pathlib import Path

from gt_engine.public_surface import PublicSurfaceResolver


def test_resolver_reads_existing_package_entrypoint_without_inventing_build_output(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "container.ts").write_text(
        "export interface Container {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "awilix.ts").write_text(
        "export { Container } from './container'\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        '{"exports":{".":"./src/awilix.ts","./missing":"./lib/missing.js"}}',
        encoding="utf-8",
    )

    candidates = PublicSurfaceResolver(tmp_path).resolve(("src/container.ts",))

    assert [(item.path, item.reason) for item in candidates] == [
        ("src/awilix.ts", "package_manifest_entrypoint")
    ]


def test_resolver_finds_existing_rust_crate_root_for_anchor(tmp_path: Path) -> None:
    source = tmp_path / "core" / "engine" / "src"
    source.mkdir(parents=True)
    (source / "job.rs").write_text("pub fn run_jobs() {}\n", encoding="utf-8")
    (source / "lib.rs").write_text("pub mod job;\n", encoding="utf-8")

    candidates = PublicSurfaceResolver(tmp_path).resolve(("core/engine/src/job.rs",))

    assert [(item.path, item.reason) for item in candidates] == [
        ("core/engine/src/lib.rs", "rust_crate_or_module_surface")
    ]


def test_resolver_uses_existing_rollup_source_entry_when_manifest_points_to_build(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "container.ts").write_text(
        "export function register() {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "awilix.ts").write_text(
        "export { register } from './container'\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        '{"main":"lib/awilix.js","module":"lib/awilix.module.mjs"}',
        encoding="utf-8",
    )
    (tmp_path / "rollup.config.mjs").write_text(
        "export default [{ input: 'src/awilix.ts' }]\n",
        encoding="utf-8",
    )

    candidates = PublicSurfaceResolver(tmp_path).resolve(("src/container.ts",))

    assert [(item.path, item.reason) for item in candidates] == [
        ("src/awilix.ts", "bundler_source_entrypoint")
    ]
