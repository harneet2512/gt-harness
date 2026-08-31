"""Generate machine-readable GroundTruth Phase II closeout inventories.

The generator deliberately consumes the accepted research inventory and the
native indexer's language-spec filenames.  It does not infer support from a
marketing list.  Run with ``--check`` to detect drift without rewriting files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
FINALSTAND = HARNESS_ROOT / "gt_finalstand"
GROUNDTRUTH_ROOT = Path(
    os.environ.get("GROUNDTRUTH_ROOT", str(HARNESS_ROOT.parent / "Groundtruth"))
).resolve()
INVENTORY_SOURCE = (
    HARNESS_ROOT
    / ".research"
    / "gt-deterministic-interface"
    / "data"
    / "feature_inventory.json"
)
SPEC_ROOT = GROUNDTRUTH_ROOT / "gt-index" / "internal" / "specs"
ROLE_SOURCE = FINALSTAND / "role_inventory_source.json"
COMPATIBILITY_UPSTREAM = (
    GROUNDTRUTH_ROOT
    / "src"
    / "groundtruth"
    / "runtime"
    / "generated_language_operation_compatibility.json"
)
COMPATIBILITY_SOURCE = FINALSTAND / "language_operation_compatibility.json"
BASELINE_SCHEMA = "gt.baseline_receipt.v1"

OPERATIONS = (
    "exact_literal_search",
    "definition",
    "references",
    "callers",
    "syntax",
    "patch_impact",
    "verification_status",
)
SYNTAX_CERTIFIED = {"go", "javascript", "python", "ruby", "typescript"}
SYNTAX_EXTENSIONS_BY_LANGUAGE = {
    "go": (".go",),
    "javascript": (".cjs", ".js", ".jsx", ".mjs"),
    "python": (".py", ".pyi"),
    "ruby": (".rb",),
    "typescript": (".ts", ".tsx"),
}
SPEC_FILE_ALIASES = {"golang": "go"}


def _csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _registered_languages() -> list[str]:
    excluded = {"spec", "manifest_test"}
    names = [
        SPEC_FILE_ALIASES.get(path.stem, path.stem)
        for path in SPEC_ROOT.glob("*.go")
        if path.stem not in excluded
    ]
    if len(names) != 30 or len(set(names)) != 30:
        raise RuntimeError(
            f"native language registry must resolve to 30 unique specs, got {len(names)}"
        )
    return sorted(names)


def _extract_role_source(payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "gt.finalstand.role_inventory_source.v1",
        "upstream_sha256": hashlib.sha256(INVENTORY_SOURCE.read_bytes()).hexdigest()
        if INVENTORY_SOURCE.is_file()
        else "unavailable_in_external_checkout",
        "counts": payload["counts"],
        "inventory": payload["inventory"],
        "cap_roles": payload["cap_roles"],
    }


def _role_source() -> dict[str, object]:
    if ROLE_SOURCE.is_file():
        frozen = json.loads(ROLE_SOURCE.read_text(encoding="utf-8"))
        if INVENTORY_SOURCE.is_file():
            upstream_payload = json.loads(INVENTORY_SOURCE.read_text(encoding="utf-8"))
            upstream = _extract_role_source(upstream_payload)
            if frozen != upstream:
                raise RuntimeError("frozen role inventory differs from accepted research inventory")
        return frozen
    if not INVENTORY_SOURCE.is_file():
        raise RuntimeError("no frozen or accepted-research role inventory is available")
    return _extract_role_source(json.loads(INVENTORY_SOURCE.read_text(encoding="utf-8")))


def _role_audit(payload: dict[str, object]) -> bytes:
    inventory = payload["inventory"]
    direct_rows = list(csv.DictReader((FINALSTAND / "direct_capabilities.csv").open(
        encoding="utf-8", newline=""
    )))
    direct = {row["capability"] for row in direct_rows}
    cap_roles = {
        identity: role
        for role, identities in payload["cap_roles"].items()
        for identity in identities
    }
    rows: list[dict[str, object]] = []
    for category in ("ACQ", "CAP", "FACT", "PERF"):
        for index, identity in enumerate(inventory[category], start=1):
            rows.append(
                {
                    "category": category,
                    "category_index": index,
                    "identity": identity,
                    "role": cap_roles.get(identity, category.lower()),
                    "direct_identity": "true" if identity in direct else "false",
                    "source": "gt_finalstand/role_inventory_source.json",
                }
            )
    return _csv_bytes(
        ("category", "category_index", "identity", "role", "direct_identity", "source"),
        rows,
    )


def _language_operations() -> bytes:
    declared = list(csv.DictReader((FINALSTAND / "language_support.csv").open(
        encoding="utf-8", newline=""
    )))
    display = {row["registry_identity"]: row["language"] for row in declared}
    compatibility = _compatibility_source()
    compatibility_rows = compatibility["rows"]
    registered = sorted({row["registry_identity"] for row in compatibility_rows})
    if len(registered) != 30:
        raise RuntimeError("GroundTruth compatibility authority must contain 30 languages")
    if set(display) != set(registered):
        raise RuntimeError(
            "language_support.csv differs from native specs: "
            f"missing={sorted(set(registered) - set(display))}, "
            f"extra={sorted(set(display) - set(registered))}"
        )

    by_pair = {
        (row["registry_identity"], row["operation"]): row["terminal_semantics"]
        for row in compatibility_rows
    }
    expected_pairs = {(language, operation) for language in registered for operation in OPERATIONS}
    if set(by_pair) != expected_pairs:
        raise RuntimeError("GroundTruth compatibility artifact is not the complete 30x7 product")

    rows: list[dict[str, object]] = []
    for language_id in registered:
        for operation in OPERATIONS:
            semantics = by_pair[(language_id, operation)]
            current_state = (
                "ADVERTISED_CERTIFIED"
                if semantics != "removed"
                else "REMOVED_FROM_ADVERTISED_SCHEMA"
            )
            if operation == "exact_literal_search":
                basis = "language-agnostic explicit-scope byte scanner; omissions prevent exactness"
            elif operation == "syntax":
                basis = "registered parse-only checker with positive-error and unavailable outcomes"
            elif operation == "verification_status":
                basis = (
                    "execution-specific verification contract bound to exact command and revision"
                )
            elif operation == "patch_impact":
                basis = (
                    "current producer is incomplete/partial and cannot satisfy its "
                    "advertised contract"
                )
            else:
                basis = "no language/configuration completeness certificate exists"
            rows.append(
                {
                    "language": display[language_id],
                    "registry_identity": language_id,
                    "operation": operation,
                    "terminal_semantics": semantics,
                    "current_state": current_state,
                    "certification_basis": basis,
                }
            )
    return _csv_bytes(
        (
            "language",
            "registry_identity",
            "operation",
            "terminal_semantics",
            "current_state",
            "certification_basis",
        ),
        rows,
    )


def _compatibility_source() -> dict[str, object]:
    if COMPATIBILITY_SOURCE.is_file():
        frozen = json.loads(COMPATIBILITY_SOURCE.read_text(encoding="utf-8"))
        if COMPATIBILITY_UPSTREAM.is_file():
            upstream = json.loads(COMPATIBILITY_UPSTREAM.read_text(encoding="utf-8"))
            if frozen != upstream:
                raise RuntimeError("frozen compatibility differs from GroundTruth authority")
        return frozen
    if not COMPATIBILITY_UPSTREAM.is_file():
        raise RuntimeError("GroundTruth language-operation compatibility is unavailable")
    return json.loads(COMPATIBILITY_UPSTREAM.read_text(encoding="utf-8"))


def _typed_capability_module(certification: bytes) -> bytes:
    rows = list(csv.DictReader(io.StringIO(certification.decode("utf-8"))))
    kind_order = (
        "exact_literal_search",
        "syntax",
        "patch_impact",
        "verification_status",
        "definition",
        "references",
        "callers",
    )
    certified = tuple(
        kind
        for kind in kind_order
        if any(
            row["operation"] == kind and row["terminal_semantics"] != "removed"
            for row in rows
        )
    )
    removed = tuple(kind for kind in kind_order if kind not in certified)
    syntax_languages = tuple(
        sorted(
            row["registry_identity"]
            for row in rows
            if row["operation"] == "syntax" and row["terminal_semantics"] == "exact"
        )
    )
    syntax_extensions = tuple(
        sorted(
            extension
            for language in syntax_languages
            for extension in SYNTAX_EXTENSIONS_BY_LANGUAGE[language]
        )
    )
    digest = hashlib.sha256(certification).hexdigest()
    compatibility = json.loads(COMPATIBILITY_SOURCE.read_text(encoding="utf-8"))
    manifest_sha256 = str(compatibility["source_manifest_sha256"])
    registered_languages = tuple(
        sorted({str(row["registry_identity"]) for row in compatibility["rows"]})
    )
    if len(registered_languages) != 30 or len(manifest_sha256) != 64:
        raise ValueError(
            "generated language manifest authority must contain 30 identities and a sha256"
        )

    def tuple_literal(values: tuple[str, ...]) -> str:
        return "(\n" + "".join(f"    {value!r},\n" for value in values) + ")"

    source = f'''"""Generated from gt_finalstand/language_operation_certification.csv.

Do not edit by hand. Run ``python scripts/generate_gt_finalstand.py``.
"""

CERTIFICATION_SCHEMA = "gt.typed_capability_certification.v1"
CERTIFICATION_SHA256 = "{digest}"
LANGUAGE_MANIFEST_SHA256 = "{manifest_sha256}"
REGISTERED_LANGUAGE_IDENTITIES = {tuple_literal(registered_languages)}
CERTIFIED_TYPED_KINDS = {tuple_literal(certified)}
REMOVED_TYPED_KINDS = {tuple_literal(removed)}
CERTIFIED_SYNTAX_LANGUAGES = {tuple_literal(syntax_languages)}
CERTIFIED_SYNTAX_EXTENSIONS = {tuple_literal(syntax_extensions)}
'''
    return source.encode("utf-8")


def _outputs() -> dict[Path, bytes]:
    role_source = _role_source()
    compatibility_source = _compatibility_source()
    language_operations = _language_operations()
    return {
        ROLE_SOURCE: (json.dumps(role_source, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        COMPATIBILITY_SOURCE: (
            json.dumps(compatibility_source, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8"),
        FINALSTAND / "role_audit.csv": _role_audit(role_source),
        FINALSTAND / "language_operation_certification.csv": language_operations,
        HARNESS_ROOT / "gt_engine" / "generated_typed_capabilities.py": (
            _typed_capability_module(language_operations)
        ),
    }


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8", "surrogatepass")


def build_baseline_receipt(spec: dict[str, object]) -> dict[str, object]:
    """Build a deterministic provider-free baseline receipt from explicit inputs."""
    required = (
        "repository", "source_revision", "environment", "commands", "suites",
        "fixtures", "results", "producer_identity", "graph_identity", "rollback",
    )
    missing = [name for name in required if name not in spec]
    if missing:
        raise ValueError("baseline receipt missing fields: " + ",".join(missing))
    source_revision = spec["source_revision"]
    if not isinstance(source_revision, str) or len(source_revision) != 40:
        raise ValueError("source_revision must be a full commit SHA")
    results = spec["results"]
    if not isinstance(results, dict):
        raise ValueError("results must be an object")
    if results.get("provider_calls") != 0:
        raise ValueError("baseline receipt requires provider_calls=0")
    payload = {
        "schema": BASELINE_SCHEMA,
        "repository": spec["repository"],
        "source_revision": source_revision,
        "environment": spec["environment"],
        "commands": spec["commands"],
        "suites": spec["suites"],
        "fixtures": spec["fixtures"],
        "results": results,
        "producer_identity": spec["producer_identity"],
        "graph_identity": spec["graph_identity"],
        "rollback": spec["rollback"],
    }
    payload["receipt_sha256"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return payload


def write_baseline_receipt(spec: dict[str, object], output: Path) -> Path:
    """Atomically write a validated baseline receipt without partial publication."""
    receipt = build_baseline_receipt(spec)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical_json(receipt))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def verify_baseline_receipt(receipt: dict[str, object]) -> bool:
    """Fail closed on digest, schema, required identity, or provider-call drift."""
    results = receipt.get("results")
    if receipt.get("schema") != BASELINE_SCHEMA or not isinstance(results, dict):
        return False
    if results.get("provider_calls") != 0:
        return False
    digest = receipt.get("receipt_sha256")
    if not isinstance(digest, str):
        return False
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return digest == hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files drift")
    parser.add_argument("--baseline-spec", type=Path,
                        help="write a provider-free baseline receipt from a JSON spec")
    parser.add_argument("--baseline-output", type=Path,
                        help="output path for --baseline-spec receipt")
    args = parser.parse_args()
    if args.baseline_spec:
        if not args.baseline_output:
            parser.error("--baseline-output is required with --baseline-spec")
        spec = json.loads(args.baseline_spec.read_text(encoding="utf-8"))
        write_baseline_receipt(spec, args.baseline_output)
        return 0
    if not SPEC_ROOT.is_dir() or not COMPATIBILITY_UPSTREAM.is_file():
        raise RuntimeError("groundtruth_dependency_unavailable")
    drift: list[str] = []
    for path, expected in _outputs().items():
        if args.check:
            if not path.is_file() or path.read_bytes() != expected:
                drift.append(str(path.relative_to(HARNESS_ROOT)))
        else:
            path.write_bytes(expected)
    if drift:
        raise SystemExit("generated finalstand inventory drift: " + ", ".join(drift))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
