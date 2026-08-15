"""Language-depth audit: catch spec<->vendored-grammar mismatches BEFORE a
provider-free CI cycle, and enforce the fixture-gate certification set.

Two parts:

1. Spec<->grammar audit (fail-closed when the vendored grammar node tables are
   available). Every FunctionNodes/CallNodes entry in a caller-capable spec
   must be a node type the VENDORED grammar actually emits. A mismatch means
   the parser can never extract definitions or calls for that language ->
   guaranteed zero CALLS edges (the C/C++ declarator and elm/ocaml name bugs
   were exactly this class). The vendored grammars are located via
   ``go env GOMODCACHE`` (or the ``GT_INDEX_MODCACHE`` env override); when the
   module is not downloaded the audit prints a skip note instead of failing.

2. Fixture-gate certification cross-check (always, fail-closed). Every
   caller-capable structural registry language must be edge-certified in
   ``scripts/verify_gt_index_runtime.py``'s expected_call_languages, and any
   language that cannot reach Python depth must not claim caller_support.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "vendor" / "gt-index-src" / "internal" / "specs"

sys.path.insert(0, str(REPO_ROOT))

from gt_engine.language_registry import LANGUAGE_CAPABILITIES  # noqa: E402

_RE_FN = re.compile(r"FunctionNodes:\s*(\[\]string\{[^}]*\})")
_RE_CN = re.compile(r"CallNodes:\s*(\[\]string\{[^}]*\})")

# Adapter languages have no per-language grammar dir in the smacker module.
_SPECIAL = {"r", "verilog", "cobol", "scheme", "red", "povray"}


def _parse_list(raw: str) -> list[str]:
    body = raw[len("[]string{") : -1]
    return [x.strip().strip('"') for x in body.split(",") if x.strip()]


def _smacker_module_dirs() -> list[Path]:
    overrides = [os.environ.get("GT_INDEX_MODCACHE") or ""]
    if not overrides[0]:
        try:
            mod_cache = subprocess.check_output(
                ["go", "env", "GOMODCACHE"], text=True
            ).strip()
            overrides.append(mod_cache)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    for base in overrides:
        if not base:
            continue
        base_path = Path(base)
        if not base_path.is_dir():
            continue
        for mod in sorted(base_path.glob("github.com/smacker/go-tree-sitter@*")):
            if mod.is_dir():
                yield mod
    return


def _grammar_has_node(module_dirs: list[Path], lang: str, node: str) -> bool:
    if lang in _SPECIAL:
        return True
    for mod in module_dirs:
        lang_root = mod / lang
        if not lang_root.is_dir():
            continue
        # Some grammars live in a sub-package (e.g. typescript/typescript),
        # so search the whole language subtree for a parser table.
        for parser_file in lang_root.rglob("parser.c"):
            if f'"{node}"' in parser_file.read_text(
                encoding="utf-8", errors="replace"
            ):
                return True
        for parser_file in lang_root.rglob("parser.cc"):
            if f'"{node}"' in parser_file.read_text(
                encoding="utf-8", errors="replace"
            ):
                return True
    return False


# Grammar-name override: some specs bind a DIFFERENT grammar than the
# language dir implies (e.g. groovy.go uses java.GetLanguage() as a fallback,
# so its FunctionNodes/CallNodes must be checked against the java grammar).
_GRAMMAR_DIR_OVERRIDES = {
    "groovy": "java",
    "golang": "golang",
}


def _grammar_dir_for(lang: str) -> str:
    return _GRAMMAR_DIR_OVERRIDES.get(lang, lang)


def audit_spec_grammar() -> tuple[int, list[str]]:
    """Return (spec_count, mismatch_lines). Fail-closed when grammars found."""
    module_dirs = list(_smacker_module_dirs())
    if not module_dirs:
        print("AUDIT_SKIP vendored grammar module cache not found (set GT_INDEX_MODCACHE)")
        return 0, []
    mismatches: list[str] = []
    count = 0
    for spec_file in sorted(SPECS_DIR.glob("*.go")):
        if spec_file.name.endswith("_test.go") or spec_file.name in {
            "spec.go", "benchmark_structured.go", "povray.go", "red.go",
        }:
            continue
        txt = spec_file.read_text(encoding="utf-8")
        fn = _RE_FN.search(txt)
        cn = _RE_CN.search(txt)
        if not cn or not cn.group(1).strip() or cn.group(1) == "[]string{}":
            continue  # not caller-capable
        lang = spec_file.stem
        grammar_dir = _grammar_dir_for(lang)
        count += 1
        fns = _parse_list(fn.group(1)) if fn else []
        cns = _parse_list(cn.group(1))
        for entry in fns:
            if not _grammar_has_node(module_dirs, grammar_dir, entry):
                mismatches.append(f"{lang}: FunctionNodes {entry!r} absent from vendored grammar")
        for entry in cns:
            if not _grammar_has_node(module_dirs, grammar_dir, entry):
                mismatches.append(f"{lang}: CallNodes {entry!r} absent from vendored grammar")
    for line in sorted(mismatches):
        print("AUDIT_MISMATCH", line)
    return count, mismatches


def audit_fixture_gate_certification() -> tuple[list[str], list[str]]:
    """Cross-check registry caller-capable set against the fixture gate."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from verify_gt_index_runtime import (  # noqa: E402
        _STRUCTURAL_FIXTURES,
        EXPECTED_CALL_LANGUAGES,
    )

    fixture_spec_names = {
        "bash" if name == "shell" else name for name in _STRUCTURAL_FIXTURES
    }
    registry_caller_capable = {
        "bash" if c.name == "shell" else c.name
        for c in LANGUAGE_CAPABILITIES
        if c.structural_index and c.caller_support
    }
    unverified_real = sorted(
        (fixture_spec_names & registry_caller_capable) - EXPECTED_CALL_LANGUAGES
    )
    unverified_all = sorted(
        registry_caller_capable - EXPECTED_CALL_LANGUAGES - fixture_spec_names
    )
    for line in unverified_real:
        print("GATE_UNVERIFIED_FIXTURE", line)
    for line in unverified_all:
        print("GATE_UNVERIFIED", line)
    return unverified_real, unverified_all


def main() -> int:
    _count, mismatches = audit_spec_grammar()
    unverified_real, unverified_all = audit_fixture_gate_certification()
    if mismatches:
        print(f"LANGUAGE_DEPTH_AUDIT_FAILED {len(mismatches)} spec/grammar mismatches")
        return 1
    if unverified_real or unverified_all:
        print(
            "LANGUAGE_DEPTH_AUDIT_FAILED "
            f"unverified_real={unverified_real} unverified={unverified_all}"
        )
        return 1
    print("LANGUAGE_DEPTH_AUDIT_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
