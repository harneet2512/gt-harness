from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "generate_gt_finalstand.py"
FIXTURE_ROOT = ROOT / "tests" / "red_artifacts" / "groundtruth_fixture"
GROUNDTRUTH_SOURCE_SHA = "5817206811265f9b166296662c6d2f21231fd92e"
GROUNDTRUTH_SOURCE_TREE = "a6e344542112a01a41c1835e41dce6346abfe2c6"
SOURCE_HASHES = {
    "bash.go": "00e19beaeb8998e91bc359530f410bff9d867824690ab76067734adb138a8404",
    "c.go": "85bedc31770b17e9a7732b9c18d413ae15ff82c332b57e92c4ed28bbf26b38c8",
    "cpp.go": "aee001bf64bb572214ade5ab800f78924cd3efb0db2876b2cd41ac6f27c52921",
    "csharp.go": "abbbb129ada9bd694cef29872eb73a118ea71c5fd8a7b2b7772e74a3793e6393",
    "css.go": "52087a332b230bb1432341b015aa8db6820e95dee5b8d5d3ae21d93529d769e7",
    "cue.go": "e396643e5b58d61407c3645a3725786de9faae248c9c0a0a6687a56919ee9326",
    "elixir.go": "b4de503f977b307f5efbe0b8b8b3adac48b31c5b549e511732c324270ea84292",
    "elm.go": "232b0275e9ea9604ff040f87aa8014104c9296c913c790f4cb0a406e2f02f262",
    "golang.go": "f39051ef693e97090a30ff8406c4e55f2370785709bde6ec43515cc1377d3142",
    "groovy.go": "96b99893f3b1438c5b1b687bb6fa091aa10a4c63c0a8323b74b979c40f21cd46",
    "hcl.go": "8be8a42de74e400ae08136f5a196af3c1f1c3f81df20197842dd291d386fd98e",
    "html.go": "5c379e7d2348d2f117e247df2c264fda7ae9f16d3ae679489b166b83f1b90387",
    "java.go": "afccae4dcfd0d4eb56ff4164ff576876c45f354ce2d2a5fd865644cddd0f56ff",
    "javascript.go": "fd673db7be2bb9078db463fc7967fada169bffc8a7c8d58528b1e1030a33db49",
    "kotlin.go": "cbd35d6540bc2e143d502cd657f97eaad687457fc1f0a0c4139b0fad17d21c00",
    "lua.go": "efac5bbbbc1d4ec6c4266c7802ec6d14b3659702d1d4077f5a2b0dbd660e26e2",
    "markdown.go": "4d3d01e49a94dca6496f06bb18763f933b3a4af31885efc859e272a66c792250",
    "ocaml.go": "9e60799eb26def8ace60b659d345a9d21254ff85249039776afe56dab72b0a50",
    "php.go": "931edce4c9c25aa93e53f28d2a0d89b2f0553b8302a44e9e95ce022b903683d9",
    "protobuf.go": "a3ba5c361670cd159a57314e0507a6bcca9127e8b8346a76fa9672b4ee62753e",
    "python.go": "b27ac8b9205af064c8b0c72b7e305b59db1d66968e23184a5e5817e05a4c7cbf",
    "ruby.go": "15704486346d7849c5ce271d6e3cc6f8f3d81cfecca8e19d05495aa3cdc389e1",
    "rust.go": "3c8da25a4af028fd4687ef60df8929d18732d89fcb8533e90a53ccc4ea2bc3a7",
    "scala.go": "5eb8c1865f7b8bf26faad9e89fde54c96057d75a416a3dc51de69552efaa4cc8",
    "sql.go": "82f384c760eab58a4ca0535a173696170913b50ea03975dfb9b0b63a39813109",
    "spec.go": "68b901b9f1b5a0a6cfffee5ff0330966567ae4194c48aca03ad30f541c56ab96",
    "svelte.go": "794bd4d3a2c971fe8f5eec0c32249fc5085919072c721db4bca6731e24c0a565",
    "swift.go": "4f7a9f145bed0b5d4303248741e703303c821c2620ebf0fa8aa3a0e5e0acfa69",
    "toml.go": "91d14e6986690e26bc8e26bab6dbfcec953e55b9d183f27917e9e56201e4fcc4",
    "typescript.go": "301a42c3c0fdb710406376147aeded5ae426893f25ccd4dbdc88b6a1be8f215b",
    "yaml.go": "e4a6499910ae7a99438090461c7164140dabfdb73835f6fa5daaedcb9ed3dbb9",
}
SPEC_NAMES = tuple(
    "go" if filename == "golang.go" else Path(filename).stem
    for filename in SOURCE_HASHES
    if filename != "spec.go"
)


def _fixture_error(source_root: Path) -> str | None:
    spec_root = source_root / "gt-index" / "internal" / "specs"
    actual = {path.name for path in spec_root.glob("*.go")}
    if actual != set(SOURCE_HASHES):
        return f"fixture file set differs: {sorted(actual)}"
    for filename, expected_hash in SOURCE_HASHES.items():
        path = spec_root / filename
        actual_hash = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            return f"fixture hash mismatch: {filename}"
        if filename != "spec.go":
            identity = "go" if filename == "golang.go" else Path(filename).stem
            source = path.read_text(encoding="utf-8")
            if "Register(&Spec{" not in source or not re.search(
                rf'Name:\s*"{identity}"', source
            ):
                return f"fixture semantic identity mismatch: {filename}"
    return None


def _run(source_root: Path) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment["GROUNDTRUTH_ROOT"] = str(source_root)
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


with tempfile.TemporaryDirectory(prefix="gt-missing-dependency-") as missing_dir:
    missing = Path(missing_dir)
    missing_result = _run(missing)
    missing_output = missing_result.stdout.decode("utf-8", "replace")
    if missing_result.returncode == 0 or "groundtruth_dependency_unavailable" not in missing_output:
        print("FAIL: missing dependency did not fail closed")
        raise SystemExit(1)

with tempfile.TemporaryDirectory(prefix="gt-pinned-present-") as present_dir:
    present = Path(present_dir)
    shutil.copytree(FIXTURE_ROOT / "gt-index", present / "gt-index")
    fixture_error = _fixture_error(present)
    if fixture_error is not None:
        print(f"FAIL: accepted Groundtruth fixture rejected: {fixture_error}")
        raise SystemExit(1)
    with tempfile.TemporaryDirectory(prefix="gt-mutated-source-") as mutated_dir:
        mutated = Path(mutated_dir)
        shutil.copytree(FIXTURE_ROOT / "gt-index", mutated / "gt-index")
        mutated_source = mutated / "gt-index" / "internal" / "specs" / "bash.go"
        mutated_source.write_bytes(mutated_source.read_bytes() + b"// mutation\n")
        if _fixture_error(mutated) is None:
            print("FAIL: mutated Groundtruth source was accepted")
            raise SystemExit(1)
    compatibility = (
        present
        / "src"
        / "groundtruth"
        / "runtime"
        / "generated_language_operation_compatibility.json"
    )
    compatibility.parent.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "gt_finalstand" / "language_operation_compatibility.json",
        compatibility,
    )
    present_result = _run(present)
    if present_result.returncode != 0:
        print("FAIL: pinned-present dependency did not pass generator check")
        raise SystemExit(1)

print(
    "PASS: accepted Groundtruth source fixture and mutated-source rejection; "
    "missing dependency fails closed; pinned-present dependency passes"
)
