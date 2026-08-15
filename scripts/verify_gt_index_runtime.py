#!/usr/bin/env python3
"""Provider-free proof that the paid host can build and query a GT graph."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.indexer import IndexBuildStatus  # noqa: E402
from gt_engine.language_registry import LANGUAGE_CAPABILITIES  # noqa: E402
from gt_engine.repository_intelligence import inspect_repository  # noqa: E402

_STRUCTURAL_FIXTURES = {
    "coq": (
        "Require Import Arith.\n"
        "Theorem helper : forall n : nat, n = n.\nProof. reflexivity. Qed.\n"
        "Theorem target : forall n : nat, n = n.\nProof. intro n. apply helper. Qed.\n"
    ),
    "stan": (
        "functions {\n"
        "  real helper(real x) { return x; }\n"
        "  real target(real x) { return helper(x); }\n"
        "}\n"
    ),
    "sparql": "PREFIX ex: <https://example.test/>\nSELECT ?s WHERE { ?s ex:name ?name . }\n",
    "turtle": "@prefix ex: <https://example.test/> .\nex:University a ex:Organization .\n",
    "latex": (
        "\\documentclass{article}\n"
        "\\newcommand{\\helper}[1]{#1}\n"
        "\\newcommand{\\target}[1]{\\helper{#1}}\n"
    ),
    "vim": (
        "function! Helper()\nendfunction\n"
        "function! Target()\n  call Helper()\nendfunction\n"
    ),
    "nginx": "upstream backend { server 127.0.0.1:8080; }\nserver { listen 8080; }\n",
    "gcode": "O1000\nM98 P2000\nM30\nO2000\nM99\n",
    "make": "all: build\nbuild:\n\t@true\n",
    "dockerfile": "FROM python:3.12 AS runtime\n",
    "cmake": (
        "function(helper)\nendfunction()\n"
        "function(target)\n  helper()\nendfunction()\n"
    ),
    "meson": "project('example', 'c')\nexecutable('app', 'main.c')\n",
    "autotools": "AC_INIT([example], [1.0])\nAC_CONFIG_FILES([Makefile])\n",
    "verilog": (
        "module target(input value, output out);\n"
        "  assign out = value;\n"
        "endmodule\n"
        "module caller(input value, output out);\n"
        "  target instance(.value(value), .out(out));\n"
        "endmodule\n"
    ),
    "r": (
        "r_target <- function(value) { value + 1 }\n"
        "r_caller <- function() { r_target(1) }\n"
    ),
    "red": (
        ";redcode-94\nstart mov 0, 1\n      jmp finish\n"
        "finish dat 0, 0\n      end start\n"
    ),
    "povray": (
        "#macro Helper()\nsphere { <0,0,0>, 1 }\n#end\n"
        "#macro Thing()\nHelper()\n#end\nThing()\n"
    ),
    # C/C++: real functions + a same-file literal call. These certify the
    # declarator-name fix (the old NameField "declarator" text was
    # `target(int value)` so the resolver's bare-callee lookup could never
    # match, silently producing zero CALLS edges on every C/C++ task). The
    # fixture MUST assert clean node names AND a directed CERTIFIED edge.
    "c": (
        "int target(int value) { return value + 1; }\n"
        "int caller() { return target(1); }\n"
    ),
    "cpp": (
        "int target(int value) { return value + 1; }\n"
        "int caller() { return target(1); }\n"
    ),
    "javascript": (
        "function target(value) { return value + 1; }\n"
        "function caller() { return target(1); }\n"
    ),
    "rust": (
        "fn target(value: i64) -> i64 { value + 1 }\n"
        "fn caller() -> i64 { target(1) }\n"
    ),
    # Bash/shell: a bare command that names an in-file function resolves to
    # that function (bash function lookup precedes PATH lookup). `return` and
    # other builtin/external commands resolve to nothing and must NOT create a
    # certified edge — the negative-control assertion below enforces this.
    "shell": (
        "target() { return 0; }\n"
        "caller() { target; }\n"
    ),
    # Python-depth parity for the remaining caller-capable grammars. Each is a
    # plain `target`/`caller` pair with a same-file literal call so the fixture
    # gate certifies a directed CALLS edge exactly like Python. elm/ocaml names
    # require the grammar-scoped parser fixes (function_declaration_left /
    # value_name descent) — without them the fixture gate fails closed.
    "go": (
        "package fixture\n"
        "func target(value int) int { return value + 1 }\n"
        "func caller() int { return target(1) }\n"
    ),
    "java": (
        "class Fixture {\n"
        "  int target(int value) { return value + 1; }\n"
        "  int caller() { return target(1); }\n"
        "}\n"
    ),
    "csharp": (
        "class Fixture {\n"
        "  int target(int value) { return value + 1; }\n"
        "  int caller() { return target(1); }\n"
        "}\n"
    ),
    "php": (
        "<?php\n"
        "function target($value) { return $value + 1; }\n"
        "function caller() { return target(1); }\n"
    ),
    "swift": (
        "func target(value: Int) -> Int { return value + 1 }\n"
        "func caller() -> Int { return target(value: 1) }\n"
    ),
    "kotlin": (
        "fun target(value: Int): Int { return value + 1 }\n"
        "fun caller(): Int { return target(1) }\n"
    ),
    "scala": (
        "def target(value: Int): Int = value + 1\n"
        "def caller(): Int = target(1)\n"
    ),
    "ruby": (
        "def target(value)\n  value + 1\nend\n"
        "def caller\n  target(1)\nend\n"
    ),
    "typescript": (
        "function target(value: number): number { return value + 1; }\n"
        "function caller(): number { return target(1); }\n"
    ),
    "elm": (
        "target value =\n    value + 1\n\n"
        "caller =\n    target 1\n"
    ),
    "ocaml": (
        "let target value = value + 1\n"
        "let caller () = target 1\n"
    ),
}

# The certified caller-capable set. Every entry must produce a directed
# CERTIFIED CALLS edge from the fixture graph. Module-level so the language
# depth audit can cross-check it against the registry caller-capable set.
EXPECTED_CALL_LANGUAGES = frozenset(
    {
        "python",
        "scheme",
        "cobol",
        "r",
        "verilog",
        "red",
        "povray",
        "coq",
        "stan",
        "latex",
        "vim",
        "gcode",
        "make",
        "cmake",
        # TB2.0-relevant: C/C++ are the regression proof for the declarator-name
        # fix; bash proves same-file literal command->function resolution.
        "c",
        "cpp",
        "javascript",
        "rust",
        "bash",
        # Python-depth parity: every remaining caller-capable structural registry
        # grammar. elm/ocaml depend on the grammar-scoped name-extraction fixes;
        # a regression fails the gate.
        "go",
        "java",
        "csharp",
        "php",
        "swift",
        "kotlin",
        "scala",
        "ruby",
        "typescript",
        "elm",
        "ocaml",
    }
)


def verify() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="gt-index-fixture-") as root_dir:
        root = Path(root_dir)
        (root / "fixture.py").write_text(
            "def target(value: int) -> int:\n"
            "    return value + 1\n\n"
            "def caller() -> int:\n"
            "    return target(1)\n",
            encoding="utf-8",
        )
        # These two fixtures certify the parser-backed languages that are easy
        # to accidentally advertise in the host registry while leaving the
        # shipped gt-index binary unable to produce symbols.  Keep them small,
        # source-only, and deterministic: the graph gate must prove the actual
        # binary, not just the Python capability table.
        (root / "fixture.scm").write_text(
            "(define (target value) (+ value 1))\n"
            "(define (caller) (target 1))\n",
            encoding="utf-8",
        )
        (root / "fixture.cbl").write_text(
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. FIXTURE.\n"
            "       PROCEDURE DIVISION.\n"
            "       MAIN-PARA.\n"
            "           PERFORM HELPER-PARA.\n"
            "           STOP RUN.\n"
            "       HELPER-PARA.\n"
            "           DISPLAY \"ok\".\n",
            encoding="utf-8",
        )
        (root / "fixture.r").write_text(
            _STRUCTURAL_FIXTURES["r"],
            encoding="utf-8",
        )
        (root / "fixture.v").write_text(
            "module target(input value, output out);\n"
            "  assign out = value;\n"
            "endmodule\n"
            "module caller(input value, output out);\n"
            "  target instance(.value(value), .out(out));\n"
            "endmodule\n",
            encoding="utf-8",
        )
        (root / "fixture.red").write_text(
            _STRUCTURAL_FIXTURES["red"],
            encoding="utf-8",
        )
        (root / "fixture.pov").write_text(
            _STRUCTURAL_FIXTURES["povray"],
            encoding="utf-8",
        )
        # Exercise every parser that the host registry advertises.  These
        # files are deliberately separate from the semantic Python/COBOL/
        # Scheme fixtures above: file_hashes proves binary language dispatch,
        # while the named fixtures prove definitions and CALLS edges.
        language_root = root / "language_fixtures"
        explicit_fixture_languages = {
            "python",
            "scheme",
            "cobol",
            "r",
            "verilog",
            "red",
            "povray",
        }
        for capability in LANGUAGE_CAPABILITIES:
            if (
                not capability.structural_index
                or capability.name in explicit_fixture_languages
            ):
                continue
            fixture_name = (
                capability.basenames[0]
                if capability.basenames
                else f"fixture_{capability.name}{capability.suffixes[0]}"
            )
            (language_root / fixture_name).parent.mkdir(
                parents=True, exist_ok=True
            )
            (language_root / fixture_name).write_text(
                _STRUCTURAL_FIXTURES.get(
                    capability.name, "/* parser coverage fixture */\n"
                ),
                encoding="utf-8",
            )
        source_revision = "fixture-source-r0"
        evidence = inspect_repository(
            root,
            "Change target so caller uses the indexed definition.",
            state_dir=root / ".state",
            source_revision=source_revision,
        )
        receipt = evidence.index
        if receipt is None:
            raise RuntimeError("repository evidence did not retain index receipt")
        if receipt.status is not IndexBuildStatus.AVAILABLE or not receipt.graph_db:
            raise RuntimeError(
                "repository index unavailable: "
                f"status={receipt.status.value} error={receipt.error_type or 'none'}"
            )
        graph = Path(receipt.graph_db)
        manifest_path = graph.with_suffix(".manifest.json")
        if not manifest_path.is_file():
            raise RuntimeError("graph certification manifest missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("graph_sha256") != receipt.graph_revision:
            raise RuntimeError("graph certification revision mismatch")
        if manifest.get("source_revision") != source_revision:
            raise RuntimeError("graph/source revision binding missing")
        connection = sqlite3.connect(f"file:{graph.resolve().as_posix()}?mode=ro", uri=True)
        try:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            definition_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM nodes WHERE name IN ('target','caller')"
                ).fetchone()[0]
            )
            language_counts = dict(
                connection.execute(
                    "SELECT language, COUNT(*) FROM nodes GROUP BY language"
                ).fetchall()
            )
            language_file_counts = dict(
                connection.execute(
                    "SELECT language, COUNT(*) FROM file_hashes GROUP BY language"
                ).fetchall()
            )
            cobol_count = int(language_counts.get("cobol", 0))
            scheme_count = int(language_counts.get("scheme", 0))
            r_count = int(language_counts.get("r", 0))
            verilog_count = int(language_counts.get("verilog", 0))
            red_count = int(language_counts.get("red", 0))
            povray_count = int(language_counts.get("povray", 0))
            benchmark_structured_counts = {
                language: int(language_counts.get(language, 0))
                for language in (
                    "coq",
                    "stan",
                    "sparql",
                    "turtle",
                    "latex",
                    "vim",
                    "nginx",
                    "gcode",
                    "make",
                    "dockerfile",
                    "cmake",
                    "meson",
                    "autotools",
                )
            }
            call_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM edges e "
                    "JOIN nodes src ON src.id=e.source_id "
                    "JOIN nodes tgt ON tgt.id=e.target_id "
                    "WHERE e.type='CALLS' AND src.name='caller' AND tgt.name='target'"
                ).fetchone()[0]
            )
            call_language_counts = dict(
                connection.execute(
                    "SELECT src.language,COUNT(*) FROM edges e "
                    "JOIN nodes src ON src.id=e.source_id "
                    "WHERE e.type='CALLS' "
                    "AND COALESCE(e.confidence,0)>=0.95 "
                    "AND COALESCE(e.trust_tier,'')='CERTIFIED' "
                    "AND COALESCE(e.candidate_count,0)=1 "
                    "GROUP BY src.language"
                ).fetchall()
            )
            call_edge_quality = [
                {
                    "language": str(row[0]),
                    "confidence": float(row[1]),
                    "trust_tier": str(row[2]),
                    "candidate_count": int(row[3]),
                    "count": int(row[4]),
                }
                for row in connection.execute(
                    "SELECT src.language,COALESCE(e.confidence,0),"
                    "COALESCE(e.trust_tier,''),COALESCE(e.candidate_count,0),COUNT(*) "
                    "FROM edges e JOIN nodes src ON src.id=e.source_id "
                    "WHERE e.type='CALLS' GROUP BY src.language,e.confidence,"
                    "e.trust_tier,e.candidate_count ORDER BY src.language"
                ).fetchall()
            ]
            # Captured inside the try so the connection is still open; the
            # checks themselves run after the connection is closed.
            poisoned_names = connection.execute(
                "SELECT DISTINCT language,name FROM nodes "
                "WHERE name LIKE '%(%' OR name LIKE '%)%' ORDER BY language LIMIT 20"
            ).fetchall()
            bash_external_edges = connection.execute(
                "SELECT COUNT(*) FROM edges e JOIN nodes src ON src.id=e.source_id "
                "JOIN nodes tgt ON tgt.id=e.target_id "
                "WHERE e.type='CALLS' AND src.language='bash' AND "
                "tgt.name IN ('return','echo','cat')"
            ).fetchone()[0]
        finally:
            connection.close()
        if quick_check.lower() != "ok":
            raise RuntimeError(f"graph quick_check failed: {quick_check}")
        if definition_count < 2:
            raise RuntimeError(f"fixture definitions missing: {definition_count}")
        if call_count < 1:
            raise RuntimeError(f"directed CALLS edge missing: {call_count}")
        expected_call_languages = EXPECTED_CALL_LANGUAGES
        missing_call_languages = sorted(
            expected_call_languages - set(call_language_counts)
        )
        if missing_call_languages:
            raise RuntimeError(
                "certified caller languages missing directed edges: "
                + ", ".join(missing_call_languages)
                + f" quality={call_edge_quality}"
            )
        # Name-sanity invariant: a definition node name must never carry
        # signature text (the C/C++ declarator regression stored names like
        # `target(int value)`, silently killing every CALLS edge). Fail closed
        # if ANY fixture language emits a poisoned name.
        if poisoned_names:
            raise RuntimeError(
                "definition node names contain signature text (declarator "
                "regression): " + repr(poisoned_names)
            )
        # Negative control: the bash fixture must NOT certify a builtin/external
        # command (`return`) as a call to a defined function. Only `caller->target`
        # is certified for bash.
        if bash_external_edges:
            raise RuntimeError(
                f"bash fixture certified an external/builtin command edge: {bash_external_edges}"
            )
        if not receipt.schema_valid or receipt.node_count < 2:
            raise RuntimeError("index receipt did not certify the graph schema/nodes")
        if receipt.source_files != receipt.indexable_files:
            raise RuntimeError("fixture source coverage was not complete")
        if receipt.parser_failures != 0:
            raise RuntimeError(
                f"fixture parser failures were not zero: {receipt.parser_failures}"
            )
        if receipt.ambiguous_paths or receipt.unsupported_paths:
            raise RuntimeError(
                "fixture language resolution was incomplete: "
                f"ambiguous={receipt.ambiguous_paths} unsupported={receipt.unsupported_paths}"
            )
        if cobol_count < 1 or scheme_count < 2:
            raise RuntimeError(
                "certified language parser coverage missing: "
                f"cobol={cobol_count} scheme={scheme_count}"
            )
        if r_count < 2 or verilog_count < 2:
            raise RuntimeError(
                "native language parser coverage missing: "
                f"r={r_count} verilog={verilog_count}"
            )
        if red_count < 1 or povray_count < 1:
            raise RuntimeError(
                "structured language parser coverage missing: "
                f"red={red_count} povray={povray_count}"
            )
        missing_structured_nodes = sorted(
            language
            for language, count in benchmark_structured_counts.items()
            if count < 1
        )
        if missing_structured_nodes:
            raise RuntimeError(
                "benchmark structured parser nodes missing: "
                + ", ".join(missing_structured_nodes)
            )
        expected_languages = {
            "bash" if capability.name == "shell" else capability.name
            for capability in LANGUAGE_CAPABILITIES
            if capability.structural_index
        }
        missing_languages = sorted(expected_languages - set(language_file_counts))
        if missing_languages:
            raise RuntimeError(
                "registered parser languages missing from binary: "
                + ", ".join(missing_languages)
            )
        # Fail-closed depth parity: every caller-capable structural registry
        # language that ships a REAL fixture (not the comment-only placeholder)
        # must be edge-certified in expected_call_languages. This is the seam
        # through which the C/C++ declarator regression escaped: C had file-level
        # coverage but no directed-edge proof. Adding a real fixture now REQUIRES
        # certifying its CALLS edge, so a future regression cannot go silent.
        fixture_spec_names = {
            "bash" if name == "shell" else name for name in _STRUCTURAL_FIXTURES
        }
        registry_caller_capable = {
            "bash" if capability.name == "shell" else capability.name
            for capability in LANGUAGE_CAPABILITIES
            if capability.structural_index and capability.caller_support
        }
        unverified_real_fixtures = sorted(
            (fixture_spec_names & registry_caller_capable) - expected_call_languages
        )
        if unverified_real_fixtures:
            raise RuntimeError(
                "caller-capable fixture languages not edge-certified: "
                + ", ".join(unverified_real_fixtures)
            )
        unverified_caller_languages = sorted(
            registry_caller_capable - expected_call_languages - fixture_spec_names
        )
        return {
            "status": receipt.status.value,
            "graph_revision": receipt.graph_revision,
            "binary_sha256": receipt.binary_sha256,
            "elapsed_ms": receipt.elapsed_ms,
            "schema_valid": receipt.schema_valid,
            "source_files": receipt.source_files,
            "indexable_files": receipt.indexable_files,
            "node_count": receipt.node_count,
            "edge_count": receipt.edge_count,
            "fts_tables": list(receipt.fts_tables),
            "definition_count": definition_count,
            "call_count": call_count,
            "call_language_counts": call_language_counts,
            "call_edge_quality": call_edge_quality,
            "language_counts": language_counts,
            "language_file_counts": language_file_counts,
            "benchmark_structured_counts": benchmark_structured_counts,
            "source_revision": source_revision,
            "resolution_reason_counts": dict(receipt.resolution_reason_counts),
            "parser_failures": receipt.parser_failures,
            "frontier_anchors": len(evidence.anchors),
            "unverified_caller_languages": unverified_caller_languages,
        }


def main() -> int:
    try:
        result = verify()
    except Exception as exc:  # noqa: BLE001 - CLI must expose one fail-closed result
        print(f"REPOSITORY_SUBSTRATE_FAILED {type(exc).__name__}: {exc}")
        return 1
    print("REPOSITORY_SUBSTRATE_PROVEN")
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
