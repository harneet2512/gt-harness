from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.red_evidence import (
    EXACT_TEXT_GRAMMAR,
    CaptureError,
    _receipt_sha256,
    capture,
    encode_receipt,
    publish_evidence_directory,
    verify,
)

EXPECTED = "undefined: VTAFlowProof"
SOURCE = "proof_red_test.go"
GO_RAW = (
    "# example.invalid/redfixture [example.invalid/redfixture.test]\n"
    "./proof_red_test.go:6:6: undefined: VTAFlowProof\n"
    "FAIL\texample.invalid/redfixture [build failed]\n"
    "FAIL\n"
)
CANONICAL = (
    b"# example.invalid/redfixture [example.invalid/redfixture.test]\n"
    b"./proof_red_test.go:6:6: undefined: VTAFlowProof\n"
    b"PACKAGE_OUTCOME=build_failed\n"
)


class CanonicalRedEvidenceTests(unittest.TestCase):
    def _root(self, parent: Path, name: str, *, mutate: bool = False) -> Path:
        root = parent / name
        root.mkdir()
        (root / SOURCE).write_text("package redfixture\n", encoding="utf-8")
        mutation = f"Path({SOURCE!r}).write_text('mutated\\n')\n" if mutate else ""
        (root / "fixture.py").write_text(
            "from pathlib import Path\nimport sys\n"
            f"{mutation}sys.stdout.write({GO_RAW!r})\nraise SystemExit(7)\n",
            encoding="utf-8",
        )
        return root

    def test_exact_text_failure_profile_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "exact")
            expected = "CHA/RTA implementation missing\n"
            (root / "runner.py").write_text(
                "import sys\nsys.stdout.write(" + repr(expected) + ")\nraise SystemExit(1)\n",
                encoding="utf-8",
            )
            result = capture(
                root=root,
                sources=[SOURCE],
                fixtures=["fixture.py", "runner.py"],
                command=[sys.executable, "runner.py"],
                toolchain_command=[sys.executable, "--version"],
                expected_source_path=SOURCE,
                expected_diagnostic=expected,
                output_grammar=EXACT_TEXT_GRAMMAR,
            )
            evidence = Path(directory) / "evidence"
            publish_evidence_directory(
                evidence_dir=evidence,
                root=root,
                inputs=[SOURCE, "fixture.py", "runner.py"],
                result=result,
            )
            self.assertEqual(
                verify(
                    root=root,
                    evidence_dir=evidence,
                    expected_receipt_sha256=result.receipt["receipt_sha256"],
                )["status"],
                "pass",
            )

    def test_prepared_replay_requires_the_seed_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "prepared")
            expected = "CHA/RTA implementation missing\n"
            (root / "runner.py").write_text(
                "import sys\nsys.stdout.write(" + repr(expected) + ")\nraise SystemExit(1)\n",
                encoding="utf-8",
            )
            cache = Path(directory) / "cache"
            result = capture(
                root=root,
                sources=[SOURCE],
                fixtures=["fixture.py", "runner.py"],
                command=[sys.executable, "runner.py"],
                toolchain_command=[sys.executable, "--version"],
                expected_source_path=SOURCE,
                expected_diagnostic=expected,
                output_grammar=EXACT_TEXT_GRAMMAR,
                cgo_enabled="1",
                cache_seed=cache,
            )
            evidence = self._publish(root, result, "prepared-evidence")
            report = verify(
                root=root,
                evidence_dir=evidence,
                expected_receipt_sha256=result.receipt["receipt_sha256"],
                replay=True,
            )
            self.assertEqual(report["status"], "fail")
            self.assertIn("replay:prepared_cache_required", report["errors"])

    def _capture(self, root: Path):
        return capture(
            root=root,
            sources=[SOURCE],
            fixtures=["fixture.py"],
            command=[sys.executable, "fixture.py"],
            toolchain_command=[sys.executable, "--version"],
            expected_source_path=SOURCE,
            expected_diagnostic=EXPECTED,
        )

    def _publish(self, root: Path, result, name: str = "evidence") -> Path:
        evidence = root.parent / name
        publish_evidence_directory(
            evidence_dir=evidence,
            root=root,
            inputs=[SOURCE, "fixture.py"],
            result=result,
        )
        return evidence

    def test_independent_roots_emit_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            first = self._capture(self._root(parent, "first"))
            second = self._capture(self._root(parent, "second"))
        self.assertEqual(first.canonical_bytes, CANONICAL)
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first.receipt_bytes, second.receipt_bytes)

    def test_real_go_fixture_matches_observed_grammar(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        fixture = repository / "tests" / "fixtures" / "red_evidence_go"
        if shutil.which("go") is None:
            self.skipTest("Go is unavailable")
        result = capture(
            root=fixture,
            sources=[SOURCE],
            fixtures=["go.mod"],
            command=["go", "test", "-count=1", "-p=1", "./..."],
            toolchain_command=["go", "version"],
            expected_source_path=SOURCE,
            expected_diagnostic=EXPECTED,
        )
        self.assertEqual(result.canonical_bytes, CANONICAL)

    def test_directory_verify_replay_and_input_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            result = self._capture(root)
            evidence = self._publish(root, result)
            arguments = dict(
                root=root,
                evidence_dir=evidence,
                expected_receipt_sha256=result.receipt["receipt_sha256"],
            )
            self.assertEqual(verify(**arguments)["status"], "pass")
            self.assertEqual(verify(**arguments, replay=True)["status"], "pass")
            (root / SOURCE).write_text("mutated\n", encoding="utf-8")
            self.assertIn(f"source_hash_mismatch:{SOURCE}", verify(**arguments)["errors"])

    def test_raw_mutations_fail_with_unchanged_canonical(self) -> None:
        for filename in ("raw.log", "raw.sha256"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = self._root(Path(directory), "root")
                result = self._capture(root)
                evidence = self._publish(root, result)
                (evidence / filename).write_bytes((evidence / filename).read_bytes() + b"x")
                report = verify(
                    root=root,
                    evidence_dir=evidence,
                    expected_receipt_sha256=result.receipt["receipt_sha256"],
                )
                self.assertEqual(report["status"], "fail")

    def test_schema_and_external_digest_reject_decisive_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            result = self._capture(root)
            mutations = {
                "green": lambda row: row["command"].update(exit_code=0),
                "cwd": lambda row: row["command"].update(cwd="../../outside"),
                "missing": lambda row: row.pop("matcher"),
                "duplicate": lambda row: row["sources"].append(copy.deepcopy(row["sources"][0])),
                "overlap": lambda row: row["fixtures"].append(copy.deepcopy(row["sources"][0])),
                "normalizer": lambda row: row.update(normalizer_version="other"),
                "grammar": lambda row: row.update(output_grammar="other"),
                "source": lambda row: row["sources"][0].update(sha256="0" * 64),
                "fixture": lambda row: row["fixtures"][0].update(sha256="0" * 64),
                "toolchain": lambda row: row["toolchain"].update(text="different\n"),
                "executable": lambda row: row["command"]["executable"].update(sha256="0" * 64),
                "matcher": lambda row: row["matcher"].update(expected_count=2),
                "environment": lambda row: row["environment"].update(GOENV="ambient"),
            }
            original = result.receipt["receipt_sha256"]
            for name, mutate in mutations.items():
                changed = copy.deepcopy(result.receipt)
                mutate(changed)
                changed["receipt_sha256"] = _receipt_sha256(changed)
                evidence = self._publish(root, result, f"evidence-{name}")
                (evidence / "receipt.json").write_bytes(encode_receipt(changed))
                report = verify(root=root, evidence_dir=evidence, expected_receipt_sha256=original)
                self.assertEqual(report["status"], "fail", name)

    def test_malformed_nested_types_fail_closed_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            result = self._capture(root)
            mutations = (
                lambda row: row["matcher"].update(source_path=7),
                lambda row: row["matcher"].update(substring=7),
                lambda row: row["matcher"].update(expected_count="1"),
                lambda row: row["diagnostic"].update(matched_diagnostics=7),
                lambda row: row["command"].update(argv=[7]),
                lambda row: row["runner"].update(image_label=7),
            )
            for index, mutate in enumerate(mutations):
                with self.subTest(index=index):
                    changed = copy.deepcopy(result.receipt)
                    mutate(changed)
                    changed["receipt_sha256"] = _receipt_sha256(changed)
                    evidence = self._publish(root, result, f"nested-{index}")
                    (evidence / "receipt.json").write_bytes(encode_receipt(changed))
                    report = verify(
                        root=root,
                        evidence_dir=evidence,
                        expected_receipt_sha256=result.receipt["receipt_sha256"],
                    )
                    self.assertEqual(report["status"], "fail")

    def test_duplicate_json_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            result = self._capture(root)
            evidence = self._publish(root, result)
            receipt = (evidence / "receipt.json").read_text(encoding="utf-8")
            receipt = receipt.replace(
                '{\n  "capture_runtime"', '{\n  "schema": "duplicate",\n  "capture_runtime"'
            )
            (evidence / "receipt.json").write_text(receipt, encoding="utf-8")
            report = verify(
                root=root,
                evidence_dir=evidence,
                expected_receipt_sha256=result.receipt["receipt_sha256"],
            )
            self.assertEqual(report["errors"], ["receipt:duplicate_key"])

    def test_capture_rejects_mutation_outside_code_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            with self.assertRaisesRegex(CaptureError, "source_mutated_during_capture"):
                self._capture(self._root(parent, "mutating", mutate=True))
            outside = parent / "outside.py"
            outside.write_text("raise SystemExit(1)\n", encoding="utf-8")
            root = self._root(parent, "root")
            with self.assertRaisesRegex(CaptureError, "command_input_outside_root"):
                capture(
                    root=root,
                    sources=[SOURCE],
                    fixtures=["fixture.py"],
                    command=[sys.executable, "../outside.py"],
                    toolchain_command=[sys.executable, "--version"],
                    expected_source_path=SOURCE,
                    expected_diagnostic=EXPECTED,
                )
            link = root / "escape.py"
            try:
                link.symlink_to(outside)
            except OSError:
                return
            with self.assertRaisesRegex(CaptureError, "path_outside_root"):
                capture(
                    root=root,
                    sources=[SOURCE],
                    fixtures=["escape.py"],
                    command=[sys.executable, "fixture.py"],
                    toolchain_command=[sys.executable, "--version"],
                    expected_source_path=SOURCE,
                    expected_diagnostic=EXPECTED,
                )

    def test_atomic_directory_is_fresh_and_preserves_prior_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            result = self._capture(root)
            evidence = self._publish(root, result)
            before = {path.name: path.read_bytes() for path in evidence.iterdir()}
            with self.assertRaisesRegex(CaptureError, "evidence_directory_exists"):
                self._publish(root, result)
            self.assertEqual(before, {path.name: path.read_bytes() for path in evidence.iterdir()})
            with self.assertRaisesRegex(CaptureError, "evidence_directory_exists"):
                publish_evidence_directory(
                    evidence_dir=root, root=root, inputs=[SOURCE, "fixture.py"], result=result
                )

    def test_cli_round_trip_uses_one_directory(self) -> None:
        cli = Path(__file__).resolve().parents[1] / "scripts" / "red_evidence.py"
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            evidence = Path(directory) / "artifact"
            base = [
                sys.executable,
                str(cli),
                "capture",
                "--root",
                str(root),
                "--source",
                SOURCE,
                "--fixture",
                "fixture.py",
                "--command-json",
                json.dumps([sys.executable, "fixture.py"]),
                "--toolchain-command-json",
                json.dumps([sys.executable, "--version"]),
                "--expected-source-path",
                SOURCE,
                "--expected-diagnostic",
                EXPECTED,
                "--evidence-dir",
                str(evidence),
            ]
            captured = subprocess.run(base, capture_output=True, text=True, check=False)
            self.assertEqual(captured.returncode, 0, captured.stderr)
            digest = json.loads(captured.stdout)["receipt_sha256"]
            verified = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "verify",
                    "--root",
                    str(root),
                    "--evidence-dir",
                    str(evidence),
                    "--expected-receipt-sha256",
                    digest,
                    "--replay",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout)


if __name__ == "__main__":
    unittest.main()
