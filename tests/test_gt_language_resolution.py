from __future__ import annotations

import sqlite3
from pathlib import Path

import gt_engine.indexer as indexer_module
from gt_engine.central_runtime import ChangeOrigin, classify_change
from gt_engine.indexer import (
    IndexBuildReceipt,
    IndexBuildStatus,
    _graph_parser_failures,
    inspect_source_coverage,
)
from gt_engine.language_registry import (
    LanguageResolutionStatus,
    candidate_capabilities,
    resolve_language,
)

COQ_SOURCE = """\
Require Import Arith.
Theorem plus_comm : forall n m : nat, n + m = m + n.
Proof. intros; apply Nat.add_comm. Qed.
"""

VERILOG_SOURCE = """\
module adder(input wire a, input wire b, output wire y);
  assign y = a ^ b;
endmodule
"""


def test_v_suffix_is_resolved_by_bounded_content_not_extension_guess() -> None:
    coq = resolve_language("partial_proof.v", COQ_SOURCE)
    verilog = resolve_language("adder.v", VERILOG_SOURCE)
    unknown = resolve_language("unknown.v", "(* no language-bearing declaration *)\n")

    assert coq.status is LanguageResolutionStatus.RESOLVED
    assert coq.capability is not None and coq.capability.name == "coq"
    assert coq.reason_code == "content_signature_coq"
    assert verilog.status is LanguageResolutionStatus.RESOLVED
    assert verilog.capability is not None and verilog.capability.name == "verilog"
    assert verilog.reason_code == "content_signature_verilog"
    assert unknown.status is LanguageResolutionStatus.AMBIGUOUS
    assert {item.name for item in unknown.candidates} == {"coq", "verilog"}
    assert unknown.capability is None


def test_v_language_signatures_ignore_commented_declarations() -> None:
    commented_coq = resolve_language(
        "commented.v",
        "(* outer\nTheorem fake : True. (* nested *) exact I.\n*)\n",
    )
    commented_verilog = resolve_language(
        "commented.v",
        "// module fake;\n/* module also_fake; endmodule */\n",
    )

    assert commented_coq.status is LanguageResolutionStatus.AMBIGUOUS
    assert commented_verilog.status is LanguageResolutionStatus.AMBIGUOUS


def test_benchmark_required_languages_have_structural_capabilities() -> None:
    fixtures = {
        "model.stan": ("stan", "parameters { real mu; }\n"),
        "solution.sparql": ("sparql", "SELECT ?s WHERE { ?s ?p ?o . }\n"),
        "university_graph.ttl": ("turtle", "@prefix ex: <https://example/> .\n"),
        "input.tex": ("latex", "\\documentclass{article}\n"),
        "apply_macros.vim": ("vim", "function! Apply()\nendfunction\n"),
        "benchmark-site.conf": ("nginx", "server { listen 8080; }\n"),
        "text.gcode": ("gcode", "G1 X1 Y1\n"),
        "worker.m": (
            "objective_c",
            "@interface Worker : NSObject\n@end\n@implementation Worker\n- (void)run { }\n@end\n",
        ),
    }

    for path, (expected, content) in fixtures.items():
        resolution = resolve_language(path, content)
        assert resolution.status is LanguageResolutionStatus.RESOLVED, path
        assert resolution.capability is not None
        assert resolution.capability.name == expected
        assert resolution.capability.validation_relevant is True
        assert resolution.capability.structural_index is True


def test_candidate_lookup_preserves_ambiguous_source_without_claiming_language() -> None:
    candidates = candidate_capabilities("proof.v")

    assert {item.name for item in candidates} == {"coq", "verilog"}
    classified = classify_change("proof.v")
    assert classified.origin is ChangeOrigin.MODEL_AUTHORED
    assert classified.validation_relevant is True


def test_basename_and_shebang_source_identities_are_not_extension_blind() -> None:
    fixtures = {
        "Makefile": ("make", "all: build\nbuild:\n\t@true\n"),
        "Dockerfile": ("dockerfile", "FROM python:3.12 AS runtime\n"),
        "CMakeLists.txt": ("cmake", "project(example)\nadd_executable(app main.c)\n"),
        "meson.build": ("meson", "project('example', 'c')\n"),
        "configure.ac": ("autotools", "AC_INIT([example], [1.0])\n"),
        "script": ("python", "#!/usr/bin/env python3\nprint('ok')\n"),
    }

    for path, (expected, content) in fixtures.items():
        resolution = resolve_language(path, content)
        assert resolution.status is LanguageResolutionStatus.RESOLVED, path
        assert resolution.capability is not None
        assert resolution.capability.name == expected


def test_source_coverage_fails_closed_for_ambiguous_v_and_resolves_real_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "proof.v").write_text(
        "(* deliberately lacks a proof or module declaration *)\n", encoding="utf-8"
    )
    ambiguous = inspect_source_coverage(tmp_path)

    assert ambiguous.source_files == 1
    assert ambiguous.indexable_files == 0
    assert ambiguous.status is IndexBuildStatus.UNSUPPORTED_LANGUAGE
    assert ambiguous.ambiguous_paths == ("proof.v",)

    (tmp_path / "proof.v").write_text(COQ_SOURCE, encoding="utf-8")
    fixtures = {
        "model.stan": "parameters { real mu; }\n",
        "solution.sparql": "SELECT ?s WHERE { ?s ?p ?o . }\n",
        "university_graph.ttl": "@prefix ex: <https://example/> .\n",
        "input.tex": "\\documentclass{article}\n",
        "apply_macros.vim": "function! Apply()\nendfunction\n",
        "benchmark-site.conf": "server { listen 8080; }\n",
        "text.gcode": "G1 X1 Y1\n",
    }
    for name, content in fixtures.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    resolved = inspect_source_coverage(tmp_path)

    assert resolved.source_files == 8
    assert resolved.indexable_files == 8
    assert resolved.status is IndexBuildStatus.AVAILABLE
    assert resolved.ambiguous_paths == ()
    assert resolved.unsupported_paths == ()


def test_graph_parser_telemetry_is_required_for_complete_coverage(tmp_path: Path) -> None:
    graph = tmp_path / "graph.db"
    connection = sqlite3.connect(graph)
    connection.execute("CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)")
    connection.commit()
    connection.close()

    assert _graph_parser_failures(graph) == -1
    assert (
        IndexBuildReceipt(
            status=IndexBuildStatus.AVAILABLE,
            source_files=1,
            indexable_files=1,
            parser_failures=-1,
        ).coverage_complete
        is False
    )

    connection = sqlite3.connect(graph)
    connection.execute("INSERT INTO project_meta VALUES ('parse_failures', '0')")
    connection.commit()
    connection.close()

    assert _graph_parser_failures(graph) == 0


def test_coverage_reads_content_only_when_language_identity_requires_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "proof.v").write_text(COQ_SOURCE, encoding="utf-8")
    (tmp_path / "runner").write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
    (tmp_path / "dataset.csv").write_text("value\n1\n", encoding="utf-8")

    observed_reads: list[str] = []
    original = indexer_module._read_language_prefix

    def record_read(path, limit=65_536):
        observed_reads.append(Path(path).name)
        return original(path, limit)

    monkeypatch.setattr(indexer_module, "_read_language_prefix", record_read)
    coverage = inspect_source_coverage(tmp_path)

    assert coverage.status is IndexBuildStatus.AVAILABLE
    assert set(observed_reads) == {"proof.v", "runner"}
