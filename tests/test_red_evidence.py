from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.red_evidence import CaptureError, capture, encode_receipt, verify


class CanonicalRedEvidenceTests(unittest.TestCase):
    def _root(self, parent: Path, name: str) -> Path:
        root = parent / name
        root.mkdir()
        (root / "source.txt").write_text("immutable source\n", encoding="utf-8")
        (root / "fixture.py").write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "sys.stdout.write('\\x1b[31mRED root=' + str(Path.cwd()) + "
            "' after 0.123s\\r\\n')\n"
            "raise SystemExit(7)\n",
            encoding="utf-8",
        )
        return root

    def _capture(self, root: Path) -> tuple[dict, bytes]:
        return capture(
            root=root,
            sources=["source.txt"],
            fixtures=["fixture.py"],
            command=["python", "fixture.py"],
            toolchain_command=["python", "--version"],
        )

    def test_independent_roots_emit_byte_identical_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            first, first_bytes = self._capture(self._root(parent, "first"))
            second, second_bytes = self._capture(self._root(parent, "second"))

        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first["diagnostic"]["text"], "RED root=<ROOT> after <DURATION>\n")
        self.assertEqual(first["command"]["exit_code"], 7)
        self.assertEqual(first["normalizer_version"], "red-normalizer.v1")
        self.assertEqual(
            first["output_grammar"],
            "utf8-lf-ansi-stripped-root-duration-redacted.v1",
        )

    def test_verifier_replays_and_rejects_input_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            receipt, receipt_bytes = self._capture(root)
            receipt_path = root / "receipt.json"
            receipt_path.write_bytes(receipt_bytes)
            expected = receipt["receipt_sha256"]

            report = verify(
                root=root,
                receipt_path=receipt_path,
                expected_receipt_sha256=expected,
                replay=True,
            )
            self.assertEqual(report["status"], "pass")

            source = root / "source.txt"
            source.write_text("mutated source\n", encoding="utf-8")
            source_report = verify(
                root=root,
                receipt_path=receipt_path,
                expected_receipt_sha256=expected,
            )
            self.assertIn("source_hash_mismatch:source.txt", source_report["errors"])
            source.write_text("immutable source\n", encoding="utf-8")

            fixture = root / "fixture.py"
            original_fixture = fixture.read_text(encoding="utf-8")
            fixture.write_text(original_fixture + "# mutation\n", encoding="utf-8")
            fixture_report = verify(
                root=root,
                receipt_path=receipt_path,
                expected_receipt_sha256=expected,
            )
            self.assertIn("fixture_hash_mismatch:fixture.py", fixture_report["errors"])

    def test_external_digest_rejects_every_decisive_receipt_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            receipt, _ = self._capture(root)
            expected = receipt["receipt_sha256"]
            mutations = {
                "diagnostic": lambda row: row["diagnostic"].update(text="different\n"),
                "exit": lambda row: row["command"].update(exit_code=8),
                "toolchain": lambda row: row["toolchain"].update(text="different\n"),
                "normalizer": lambda row: row.update(normalizer_version="red-normalizer.v2"),
                "grammar": lambda row: row.update(output_grammar="other-grammar.v1"),
                "source-hash": lambda row: row["sources"][0].update(sha256="0" * 64),
                "fixture-hash": lambda row: row["fixtures"][0].update(sha256="0" * 64),
            }
            for name, mutate in mutations.items():
                changed = copy.deepcopy(receipt)
                mutate(changed)
                changed["receipt_sha256"] = "0" * 64
                changed_bytes = encode_receipt(changed)
                path = root / f"{name}.json"
                path.write_bytes(changed_bytes)
                report = verify(
                    root=root,
                    receipt_path=path,
                    expected_receipt_sha256=expected,
                )
                self.assertEqual(report["status"], "fail", name)
                self.assertIn("unexpected_receipt_sha256", report["errors"], name)

    def test_capture_rejects_success_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            with self.assertRaisesRegex(CaptureError, "command_did_not_fail"):
                capture(
                    root=root,
                    sources=["source.txt"],
                    fixtures=["fixture.py"],
                    command=["python", "-c", "raise SystemExit(0)"],
                    toolchain_command=["python", "--version"],
                )
            with self.assertRaisesRegex(CaptureError, "path_outside_root"):
                capture(
                    root=root,
                    sources=["../outside.txt"],
                    fixtures=["fixture.py"],
                    command=["python", "fixture.py"],
                    toolchain_command=["python", "--version"],
                )

    def test_receipt_is_strict_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            receipt, receipt_bytes = self._capture(root)
            duplicate = receipt_bytes.decode().replace(
                '{\n  "command":', '{\n  "schema": "contradictory",\n  "command":', 1
            )
            path = root / "duplicate.json"
            path.write_text(duplicate, encoding="utf-8")
            report = verify(
                root=root,
                receipt_path=path,
                expected_receipt_sha256=receipt["receipt_sha256"],
            )
            self.assertEqual(report["errors"], ["receipt:duplicate_key"])

    def test_cli_captures_and_replays_the_same_receipt(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        cli = repository / "scripts" / "red_evidence.py"
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(Path(directory), "root")
            receipt_path = root / "receipt.json"
            captured = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "capture",
                    "--root",
                    str(root),
                    "--source",
                    "source.txt",
                    "--fixture",
                    "fixture.py",
                    "--command-json",
                    '["python","fixture.py"]',
                    "--toolchain-command-json",
                    '["python","--version"]',
                    "--output",
                    str(receipt_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(captured.returncode, 0, captured.stderr)
            receipt_sha256 = json.loads(captured.stdout)["receipt_sha256"]
            verified = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "verify",
                    "--root",
                    str(root),
                    "--receipt",
                    str(receipt_path),
                    "--expected-receipt-sha256",
                    receipt_sha256,
                    "--replay",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(json.loads(verified.stdout)["status"], "pass")


if __name__ == "__main__":
    unittest.main()
