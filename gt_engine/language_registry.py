"""One deterministic authority for authored-source language capabilities.

The central runtime, repository indexer, syntax probes, and legacy Mini-SWE
bridges historically carried different extension inventories.  A path may be
validation-relevant source even when the structural index has no parser for
its language.  Keeping those two facts separate prevents ``no source`` from
masking an unsupported-index failure.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class LanguageCapability:
    name: str
    suffixes: tuple[str, ...]
    basenames: tuple[str, ...] = ()
    validation_relevant: bool = True
    structural_required: bool = True
    structural_index: bool = True
    symbol_support: bool = True
    caller_support: bool = True
    syntax_probe: str | None = None
    structural_mode: str = "tree_sitter"


class LanguageResolutionStatus(StrEnum):
    """Replayable outcome of deterministic path/content language resolution."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    NON_SOURCE = "non_source"


@dataclass(frozen=True, slots=True)
class LanguageResolution:
    status: LanguageResolutionStatus
    capability: LanguageCapability | None
    candidates: tuple[LanguageCapability, ...]
    confidence: float
    reason_code: str


LANGUAGE_CAPABILITIES: tuple[LanguageCapability, ...] = (
    LanguageCapability("python", (".py", ".pyi"), syntax_probe="python"),
    LanguageCapability("javascript", (".js", ".jsx", ".mjs", ".cjs"), syntax_probe="node"),
    LanguageCapability("typescript", (".ts", ".tsx")),
    LanguageCapability("go", (".go",)),
    LanguageCapability("rust", (".rs",)),
    LanguageCapability("ruby", (".rb", ".rake"), syntax_probe="ruby"),
    LanguageCapability("java", (".java",)),
    LanguageCapability("kotlin", (".kt", ".kts")),
    LanguageCapability("csharp", (".cs",)),
    LanguageCapability("php", (".php",)),
    LanguageCapability("swift", (".swift",)),
    LanguageCapability("scala", (".scala", ".sc")),
    LanguageCapability("c", (".c", ".h")),
    LanguageCapability("cpp", (".cc", ".cpp", ".cxx", ".hpp", ".hxx")),
    # lua/elixir/groovy/svelte/cue/hcl are symbol-structural but their VENDORED
    # grammars cannot produce certified CALLS edges under the current specs:
    #   - lua: vendored grammar emits `function_statement`/`function_name`
    #     (no named fields); the spec's FunctionNodes (`function_declaration`/
    #     `function_definition_statement`) are absent.
    #   - elixir: spec BodyField "body" is absent (grammar `do_block`); the
    #     def keyword (`identifier "def"`) is the first call child, so names
    #     extract as "def".
    #   - groovy: spec FunctionNodes/CallNodes (`method_declaration`/
    #     `method_invocation`) are absent; the grammar uses `func` for both
    #     definitions and call sites, which is ambiguous.
    #   - svelte: `<script>` content is `raw_text`; `function_declaration`/
    #     `call_expression` never appear.
    #   - cue/hcl: grammars emit `call_expression`/`function_call` but NO
    #     definition nodes, so no edge target can exist.
    # caller_support=False keeps the claim honest; reaching Python depth for
    # these requires grammar-aware spec/parser work verified on the Linux
    # source-built indexer.
    LanguageCapability("lua", (".lua",), caller_support=False),
    LanguageCapability("elixir", (".ex", ".exs"), caller_support=False),
    LanguageCapability("ocaml", (".ml", ".mli")),
    LanguageCapability("shell", (".sh", ".bash"), syntax_probe="bash"),
    # css/html/protobuf/sql are structural (symbol extraction) but their
    # vendored grammars expose NO call nodes (specs/css.go CallNodes=[] etc.).
    # caller_support=False keeps the registry claim honest and the fixture
    # gate's caller-capable set machine-verifiable against the specs.
    LanguageCapability("css", (".css",), caller_support=False),
    LanguageCapability("cue", (".cue",), caller_support=False),
    LanguageCapability("elm", (".elm",)),
    LanguageCapability("groovy", (".groovy", ".gradle"), caller_support=False),
    LanguageCapability("hcl", (".tf", ".hcl"), caller_support=False),
    LanguageCapability("html", (".html", ".htm"), caller_support=False),
    LanguageCapability("protobuf", (".proto",), caller_support=False),
    LanguageCapability("sql", (".sql",), caller_support=False),
    LanguageCapability("svelte", (".svelte",), caller_support=False),
    LanguageCapability(
        "markdown",
        (".md",),
        structural_required=False,
        symbol_support=False,
        caller_support=False,
    ),
    LanguageCapability(
        "toml",
        (".toml",),
        symbol_support=False,
        caller_support=False,
    ),
    LanguageCapability(
        "yaml",
        (".yaml", ".yml"),
        symbol_support=False,
        caller_support=False,
    ),
    LanguageCapability(
        "configuration",
        (".json", ".xml", ".ini", ".cfg", ".conf"),
        structural_required=False,
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    # These are authored source and affect validation revisions. COBOL and
    # Scheme have pinned parser-backed gt-index grammars.
    LanguageCapability(
        "cobol",
        (".cob", ".cbl", ".cpy"),
        syntax_probe="cobc",
    ),
    LanguageCapability(
        "scheme",
        (".scm", ".ss"),
    ),
    # Terminal-Bench contains code-like inputs in these languages.  R and
    # Verilog use the pinned upstream Tree-sitter grammars shipped through the
    # gt-index Go module; Redcode/POV-Ray use bounded, grammar-scoped
    # structured adapters because no maintained Tree-sitter grammar exists.
    LanguageCapability(
        "r",
        (".r",),
    ),
    LanguageCapability(
        "verilog",
        (".v",),
        symbol_support=True,
        caller_support=True,
    ),
    # Coq/Rocq and Verilog conventionally share ``.v``.  Extension-only
    # dispatch is therefore unsound; resolve_language() requires a bounded,
    # language-bearing declaration before selecting either parser.
    LanguageCapability(
        "coq",
        (".v",),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "stan",
        (".stan",),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "sparql",
        (".sparql", ".rq"),
        symbol_support=False,
        caller_support=False,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "turtle",
        (".ttl",),
        symbol_support=True,
        caller_support=False,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "latex",
        (".tex", ".sty", ".cls"),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "vim",
        (".vim",),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "nginx",
        (".conf",),
        symbol_support=True,
        caller_support=False,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "gcode",
        (".gcode", ".nc", ".tap"),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "make",
        (".mk",),
        basenames=("Makefile", "makefile", "GNUmakefile"),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "dockerfile",
        (".dockerfile",),
        basenames=("Dockerfile", "Containerfile"),
        symbol_support=True,
        caller_support=False,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "cmake",
        (".cmake",),
        basenames=("CMakeLists.txt",),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "meson",
        (),
        basenames=("meson.build", "meson_options.txt"),
        symbol_support=True,
        caller_support=False,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "autotools",
        (".ac", ".am"),
        basenames=("configure.ac", "Makefile.am"),
        symbol_support=True,
        caller_support=False,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "red",
        (".red",),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "povray",
        (".pov",),
        symbol_support=True,
        caller_support=True,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "racket",
        (".rkt",),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    LanguageCapability(
        "objective_c",
        (".m", ".mm"),
        symbol_support=True,
        caller_support=False,
        structural_mode="bounded_adapter",
    ),
    LanguageCapability(
        "erlang",
        (".erl",),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    LanguageCapability(
        "haskell",
        (".hs",),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    LanguageCapability(
        "clojure",
        (".clj", ".cljs", ".cljc"),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    LanguageCapability(
        "dart",
        (".dart",),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    LanguageCapability(
        "zig",
        (".zig",),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    LanguageCapability(
        "perl",
        (".pl", ".pm"),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    LanguageCapability(
        "fsharp",
        (".fs", ".fsx"),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
    LanguageCapability(
        "visual_basic",
        (".vb",),
        structural_index=False,
        symbol_support=False,
        caller_support=False,
        structural_mode="none",
    ),
)

_BY_SUFFIX: dict[str, tuple[LanguageCapability, ...]] = {}
for _capability in LANGUAGE_CAPABILITIES:
    for _suffix in _capability.suffixes:
        _BY_SUFFIX[_suffix] = (*_BY_SUFFIX.get(_suffix, ()), _capability)

_BY_BASENAME: dict[str, tuple[LanguageCapability, ...]] = {}
for _capability in LANGUAGE_CAPABILITIES:
    for _basename in _capability.basenames:
        _key = _basename.lower()
        _BY_BASENAME[_key] = (*_BY_BASENAME.get(_key, ()), _capability)

VALIDATION_SOURCE_SUFFIXES = frozenset(
    suffix
    for capability in LANGUAGE_CAPABILITIES
    if capability.validation_relevant
    for suffix in capability.suffixes
)
INDEXABLE_SOURCE_SUFFIXES = frozenset(
    suffix
    for capability in LANGUAGE_CAPABILITIES
    if capability.structural_index
    for suffix in capability.suffixes
)
INDEX_REQUIRED_SOURCE_SUFFIXES = frozenset(
    suffix
    for capability in LANGUAGE_CAPABILITIES
    if capability.structural_required
    for suffix in capability.suffixes
)


_COQ_DECLARATION = re.compile(
    r"(?m)^\s*(?:From\s+[A-Za-z0-9_.']+\s+)?Require\s+(?:Import|Export)\b|"
    r"^\s*(?:Theorem|Lemma|Corollary|Proposition|Definition|Fixpoint|"
    r"CoFixpoint|Inductive|CoInductive|Record|Class|Instance|Module)\s+"
    r"[A-Za-z_][A-Za-z0-9_']*\b|^\s*(?:Proof|Qed|Defined|Admitted)\s*\.",
)
_VERILOG_DECLARATION = re.compile(
    r"(?m)^\s*(?:module|interface|package|program|primitive|checker)\s+"
    r"[A-Za-z_$][A-Za-z0-9_$]*\b|^\s*endmodule\b|^\s*(?:assign|always(?:_ff|_comb|_latch)?)\b",
)
_NGINX_DECLARATION = re.compile(
    r"(?m)^\s*(?:http|events|server|location|upstream|map|stream|mail)\b[^;\n]*\{|"
    r"^\s*(?:listen|server_name|proxy_pass|fastcgi_pass|uwsgi_pass|include)\s+[^;\n]+;",
)
_SHEBANGS = {
    "python": ("python", "python3"),
    "shell": ("sh", "bash", "dash", "zsh", "ksh"),
    "ruby": ("ruby",),
    "perl": ("perl",),
}


def _strip_nested_comments(content: str, opening: str, closing: str) -> str:
    output: list[str] = []
    depth = 0
    index = 0
    while index < len(content):
        if content.startswith(opening, index):
            depth += 1
            output.extend(" " * len(opening))
            index += len(opening)
            continue
        if depth and content.startswith(closing, index):
            depth -= 1
            output.extend(" " * len(closing))
            index += len(closing)
            continue
        character = content[index]
        output.append(character if not depth or character == "\n" else " ")
        index += 1
    return "".join(output)


def _strip_c_comments(content: str) -> str:
    output: list[str] = []
    index = 0
    in_block = False
    while index < len(content):
        if not in_block and content.startswith("//", index):
            newline = content.find("\n", index + 2)
            if newline < 0:
                output.extend(" " * (len(content) - index))
                break
            output.extend(" " * (newline - index))
            index = newline
            continue
        if not in_block and content.startswith("/*", index):
            in_block = True
            output.extend("  ")
            index += 2
            continue
        if in_block and content.startswith("*/", index):
            in_block = False
            output.extend("  ")
            index += 2
            continue
        character = content[index]
        output.append(character if not in_block or character == "\n" else " ")
        index += 1
    return "".join(output)


def candidate_capabilities(
    path: str | os.PathLike[str],
) -> tuple[LanguageCapability, ...]:
    """Return path-derived candidates without pretending ambiguity is resolved."""

    path_text = os.fspath(path)
    basename = os.path.basename(path_text).lower()
    suffix = os.path.splitext(path_text)[1].lower()
    candidates = (*_BY_SUFFIX.get(suffix, ()), *_BY_BASENAME.get(basename, ()))
    return tuple(dict.fromkeys(candidates))


def _shebang_capability(content_prefix: str) -> LanguageCapability | None:
    first = content_prefix.splitlines()[0].strip().lower() if content_prefix else ""
    if not first.startswith("#!"):
        return None
    executable = first.rsplit("/", 1)[-1].split()
    if executable and executable[0] == "env" and len(executable) > 1:
        executable = executable[1:]
    command = executable[0] if executable else ""
    for capability_name, commands in _SHEBANGS.items():
        if command in commands:
            return next(
                capability
                for capability in LANGUAGE_CAPABILITIES
                if capability.name == capability_name
            )
    return None


def resolve_language(
    path: str | os.PathLike[str],
    content_prefix: str | bytes | None = None,
) -> LanguageResolution:
    """Resolve authored-source identity using bounded deterministic evidence.

    ``content_prefix`` is evidence, never an invitation to guess.  Shared or
    broad extensions abstain when no language-bearing declaration is present.
    Callers should capture an ambiguous candidate as potential source but must
    not claim structural facts until resolution succeeds.
    """

    if isinstance(content_prefix, bytes):
        content = content_prefix[:65_536].decode("utf-8", "replace")
    else:
        content = (content_prefix or "")[:65_536]
    candidates = candidate_capabilities(path)
    if not candidates:
        shebang = _shebang_capability(content)
        if shebang is not None:
            return LanguageResolution(
                LanguageResolutionStatus.RESOLVED,
                shebang,
                (shebang,),
                0.99,
                "shebang_interpreter",
            )
        return LanguageResolution(
            LanguageResolutionStatus.NON_SOURCE,
            None,
            (),
            1.0,
            "no_source_identity",
        )
    names = {candidate.name for candidate in candidates}
    if names == {"coq", "verilog"}:
        coq = bool(_COQ_DECLARATION.search(_strip_nested_comments(content, "(*", "*)")))
        verilog = bool(_VERILOG_DECLARATION.search(_strip_c_comments(content)))
        if coq != verilog:
            name = "coq" if coq else "verilog"
            capability = next(item for item in candidates if item.name == name)
            return LanguageResolution(
                LanguageResolutionStatus.RESOLVED,
                capability,
                candidates,
                1.0,
                f"content_signature_{name}",
            )
        return LanguageResolution(
            LanguageResolutionStatus.AMBIGUOUS,
            None,
            candidates,
            0.0,
            "conflicting_content_signatures" if coq else "ambiguous_extension",
        )
    if names == {"configuration", "nginx"}:
        if _NGINX_DECLARATION.search(content):
            capability = next(item for item in candidates if item.name == "nginx")
            return LanguageResolution(
                LanguageResolutionStatus.RESOLVED,
                capability,
                candidates,
                1.0,
                "content_signature_nginx",
            )
        capability = next(item for item in candidates if item.name == "configuration")
        return LanguageResolution(
            LanguageResolutionStatus.RESOLVED,
            capability,
            candidates,
            0.95,
            "configuration_default_no_nginx_signature",
        )
    capability = candidates[0]
    return LanguageResolution(
        LanguageResolutionStatus.RESOLVED,
        capability,
        candidates,
        1.0,
        "unique_extension" if len(candidates) == 1 else "unique_candidate",
    )


def capability_for_path(
    path: str | os.PathLike[str], content_prefix: str | bytes | None = None
) -> LanguageCapability | None:
    return resolve_language(path, content_prefix).capability


def is_validation_source(
    path: str | os.PathLike[str], content_prefix: str | bytes | None = None
) -> bool:
    if content_prefix is None:
        # An ambiguous shared suffix is still authored validation-relevant
        # source.  Capture it, then let bounded content resolve or abstain.
        return any(capability.validation_relevant for capability in candidate_capabilities(path))
    resolution = resolve_language(path, content_prefix)
    return bool(resolution.capability and resolution.capability.validation_relevant)


def is_indexable_source(
    path: str | os.PathLike[str], content_prefix: str | bytes | None = None
) -> bool:
    resolution = resolve_language(path, content_prefix)
    return bool(resolution.capability and resolution.capability.structural_index)


def syntax_probe_command(path: str) -> str | None:
    capability = capability_for_path(path)
    probe = capability.syntax_probe if capability is not None else None
    quoted = shlex.quote(path)
    if probe == "python":
        return (
            "command -v python3 >/dev/null 2>&1 || exit 0; "
            f"PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile -- {quoted}"
        )
    if probe == "node":
        return f"command -v node >/dev/null 2>&1 || exit 0; node --check -- {quoted}"
    if probe == "bash":
        return f"command -v bash >/dev/null 2>&1 || exit 0; bash -n -- {quoted}"
    if probe == "ruby":
        return f"command -v ruby >/dev/null 2>&1 || exit 0; ruby -c -- {quoted}"
    if probe == "cobc":
        return f"command -v cobc >/dev/null 2>&1 || exit 0; cobc -fsyntax-only -- {quoted}"
    return None


__all__ = [
    "INDEXABLE_SOURCE_SUFFIXES",
    "INDEX_REQUIRED_SOURCE_SUFFIXES",
    "LANGUAGE_CAPABILITIES",
    "LanguageCapability",
    "LanguageResolution",
    "LanguageResolutionStatus",
    "VALIDATION_SOURCE_SUFFIXES",
    "candidate_capabilities",
    "capability_for_path",
    "is_indexable_source",
    "is_validation_source",
    "resolve_language",
    "syntax_probe_command",
]
