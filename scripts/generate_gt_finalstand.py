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
import re
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
TERMINAL_BASELINE_SCHEMA = "gt.baseline_receipt.v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

# These are the last coordinator-pinned heads.  They are deliberately outside
# the receipt so a recomputed receipt digest cannot authorize a different tree.
TERMINAL_EXPECTED_HEADS = {
    "repository": "harneet2512/gt-harness",
    "repository_head": "666d3660901213497fccdae969efd93712061478",
    "groundtruth_head": "3a40cbc3111b085ae879f04ebec14c904432bdea",
}

# Final HAR-5 closure is a separate, explicitly pinned state.  The historical
# provisional snapshot above remains available for replay of older receipts;
# this anchor is the exact functional head pair used by the current closeout.
FINAL_TERMINAL_EXPECTED_HEADS = {
    "repository": "harneet2512/gt-harness",
    "repository_head": "7500657503c0107eadff8cb00ed70aba98beaa5b",
    "groundtruth_head": "f2863f8781edaeaef8787c515e36381cdbd692d5",
}

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


def _require_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _require_sha(value: object, name: str, *, commit: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    pattern = _COMMIT_RE if commit else _SHA256_RE
    if not pattern.fullmatch(value):
        raise ValueError(f"{name} must be a full {'commit SHA' if commit else 'sha256'}")
    return value


def _validate_terminal_spec(spec: dict[str, object]) -> None:
    required = (
        "repository", "source_revision", "head_state", "environment", "commands",
        "suites", "fixtures", "results", "producer_identity", "graph_identity",
        "rollback", "dependencies",
    )
    missing = [name for name in required if name not in spec]
    if missing:
        raise ValueError("terminal receipt missing fields: " + ",".join(missing))
    if spec["repository"] != TERMINAL_EXPECTED_HEADS["repository"]:
        raise ValueError("terminal receipt repository identity mismatch")
    _require_sha(spec["source_revision"], "source_revision", commit=True)

    head_state = _require_object(spec["head_state"], "head_state")
    if head_state.get("status") not in {"PROVISIONAL", "FINAL"}:
        raise ValueError("head_state.status must be PROVISIONAL or FINAL")
    _require_sha(head_state.get("repository_head"), "head_state.repository_head", commit=True)
    _require_sha(head_state.get("groundtruth_head"), "head_state.groundtruth_head", commit=True)
    functional_heads = head_state.get("functional_heads")
    if not isinstance(functional_heads, list) or not functional_heads:
        raise ValueError("head_state.functional_heads must be a non-empty list")
    for index, item in enumerate(functional_heads):
        row = _require_object(item, f"head_state.functional_heads[{index}]")
        _require_sha(row.get("sha"), f"functional_heads[{index}].sha", commit=True)
        if not isinstance(row.get("ticket"), str) or not row["ticket"]:
            raise ValueError(f"functional_heads[{index}].ticket is required")
        if row.get("state") not in {"FINAL", "PROVISIONAL", "UNVERIFIED"}:
            raise ValueError(f"functional_heads[{index}].state is invalid")
    if not isinstance(head_state.get("unresolved_dependencies"), list):
        raise ValueError("head_state.unresolved_dependencies must be a list")

    environment = _require_object(spec["environment"], "environment")
    if not isinstance(environment.get("platform"), str) or not environment["platform"]:
        raise ValueError("environment.platform is required")
    if not isinstance(environment.get("image_digest"), str):
        raise ValueError("environment.image_digest is required")
    for key in ("python", "sqlite", "go"):
        if not isinstance(environment.get(key), str) or not environment[key]:
            raise ValueError(f"environment.{key} is required")

    commands = spec["commands"]
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list")
    for index, command in enumerate(commands):
        row = _require_object(command, f"commands[{index}]")
        if not isinstance(row.get("command"), str) or not row["command"]:
            raise ValueError(f"commands[{index}].command is required")
        if row.get("exit_code") is not None and not isinstance(row.get("exit_code"), int):
            raise ValueError(f"commands[{index}].exit_code is required")
        if row.get("status") != "UNVERIFIED" and not isinstance(row.get("exit_code"), int):
            raise ValueError(f"commands[{index}].exit_code is required")
        for key in ("stdout_sha256", "stderr_sha256"):
            value = row.get(key)
            if value != "UNVERIFIED":
                _require_sha(value, f"commands[{index}].{key}")
        if row.get("status") not in {"PASS", "FAIL", "SKIPPED", "UNVERIFIED"}:
            raise ValueError(f"commands[{index}].status is invalid")

    suites = spec["suites"]
    if not isinstance(suites, list) or not suites:
        raise ValueError("suites must be a non-empty list")
    for index, suite in enumerate(suites):
        row = _require_object(suite, f"suites[{index}]")
        if not isinstance(row.get("name"), str) or not row["name"]:
            raise ValueError(f"suites[{index}].name is required")
        if not isinstance(row.get("command"), str) or not row["command"]:
            raise ValueError(f"suites[{index}].command is required")
        for key in ("collected", "passed", "failed", "skipped"):
            if not isinstance(row.get(key), int) or row[key] < 0:
                raise ValueError(f"suites[{index}].{key} must be a non-negative integer")

    fixtures = _require_object(spec["fixtures"], "fixtures")
    if not fixtures:
        raise ValueError("fixtures must not be empty")
    for name, fixture in fixtures.items():
        row = _require_object(fixture, f"fixtures.{name}")
        digest = row.get("sha256")
        if digest != "UNVERIFIED":
            _require_sha(digest, f"fixtures.{name}.sha256")
        if not isinstance(row.get("status"), str) or not row["status"]:
            raise ValueError(f"fixtures.{name}.status is required")

    producer = _require_object(spec["producer_identity"], "producer_identity")
    _require_sha(producer.get("source_revision"), "producer_identity.source_revision", commit=True)
    for key in ("binary_sha256", "toolchain_sha256"):
        if producer.get(key) != "UNVERIFIED":
            _require_sha(producer.get(key), f"producer_identity.{key}")
    graph = _require_object(spec["graph_identity"], "graph_identity")
    if not isinstance(graph.get("schema"), str) or not graph["schema"]:
        raise ValueError("graph_identity.schema is required")
    if graph.get("digest") != "UNVERIFIED":
        _require_sha(graph.get("digest"), "graph_identity.digest")
    rollback = _require_object(spec["rollback"], "rollback")
    if not isinstance(rollback.get("strategy"), str) or not rollback["strategy"]:
        raise ValueError("rollback.strategy is required")
    if rollback.get("prior_complete_sha256") != "UNVERIFIED":
        _require_sha(rollback.get("prior_complete_sha256"), "rollback.prior_complete_sha256")
    dependencies = _require_object(spec["dependencies"], "dependencies")
    if not isinstance(dependencies.get("status"), str) or not dependencies["status"]:
        raise ValueError("dependencies.status is required")
    results = _require_object(spec["results"], "results")
    if results.get("provider_calls") != 0 or results.get("benchmark_runs") != 0:
        raise ValueError("terminal receipt requires zero provider calls and benchmark runs")


def build_terminal_receipt(spec: dict[str, object]) -> dict[str, object]:
    """Build a pinned, provider-free receipt with an explicit provisional state."""
    _validate_terminal_spec(spec)
    payload = dict(spec)
    payload["schema"] = TERMINAL_BASELINE_SCHEMA
    payload["authorization"] = {
        "benchmark_ready": False,
        "reason": "explicit_user_approval_and_final_heads_required",
    }
    payload["receipt_sha256"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return payload


def terminal_receipt_spec() -> dict[str, object]:
    """Return the current provider-free terminal snapshot and its open debt."""
    return {
        "repository": TERMINAL_EXPECTED_HEADS["repository"],
        "source_revision": TERMINAL_EXPECTED_HEADS["repository_head"],
        "head_state": {
            "status": "PROVISIONAL",
            "repository_head": TERMINAL_EXPECTED_HEADS["repository_head"],
            "groundtruth_head": TERMINAL_EXPECTED_HEADS["groundtruth_head"],
            "functional_heads": [
                {
                    "ticket": "HAR-10",
                    "sha": "54ae74d410d9e99ed2e1f3e94153f284c48fb5cb",
                    "state": "FINAL",
                },
                {
                    "ticket": "HAR-35",
                    "sha": "499a0e6146549506e9ebbb789df27a6cbfae4189",
                    "state": "FINAL",
                },
                {
                    "ticket": "HAR-8",
                    "sha": "5d39a7708048139c3f189ed25b38dffe58c47449",
                    "state": "FINAL",
                },
                {
                    "ticket": "HAR-12",
                    "sha": "016bee8c7394979e190605f3077c40304035f20a",
                    "state": "FINAL",
                },
                {
                    "ticket": "HAR-38",
                    "sha": "fc7b17eb3c59bc0e9aaf9511d6ee4ff7061a74b2",
                    "state": "FINAL",
                },
                {
                    "ticket": "HAR-14",
                    "sha": "37f76ee9d4c6d99c3dd5972a682fd1fb65909bf5",
                    "state": "FINAL",
                },
                {
                    "ticket": "HAR-7",
                    "sha": "7dea95103d90a99d32ed808741c4bc6c9f1db2ef",
                    "state": "FINAL",
                },
                {
                    "ticket": "HAR-36",
                    "sha": "8695801ce67c07fbb17611eb453fad619f359100",
                    "state": "PROVISIONAL",
                },
                {
                    "ticket": "HAR-5",
                    "sha": "c269ecf54affd789088773a050e2990f25d3e299",
                    "state": "PROVISIONAL",
                },
            ],
            "unresolved_dependencies": ["seven reviewed-ready lines await owner fast-forward"],
        },
        "environment": {
            "platform": "Windows/AMD64",
            "image_digest": "UNVERIFIED",
            "python": "3.12.0",
            "sqlite": "3.42.0",
            "go": "1.26.7",
            "runner_identity": "UNVERIFIED",
        },
        "commands": [
            {
                "command": "python -m pytest -q tests/test_gt_finalstand.py -k baseline",
                "exit_code": 0,
                "status": "PASS",
                "stdout_sha256": "e39c7c8495c11b3781a4116112e8edaad58e79f9e534b4818722b9eae3cfa6a2",
                "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            {
                "command": "full provider-free suite on final merged heads",
                "exit_code": None,
                "status": "UNVERIFIED",
                "stdout_sha256": "UNVERIFIED",
                "stderr_sha256": "UNVERIFIED",
            },
        ],
        "suites": [
            {
                "name": "HAR-5 baseline collector",
                "command": "baseline",
                "collected": 2,
                "passed": 2,
                "failed": 0,
                "skipped": 0,
            },
            {
                "name": "final provider-free suite",
                "command": "full",
                "collected": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
            },
        ],
        "fixtures": {
            "baseline_spec": {"sha256": "UNVERIFIED", "status": "PROVISIONAL"},
            "task_dataset": {"sha256": "UNVERIFIED", "status": "NOT_RUN"},
        },
        "results": {"provider_calls": 0, "benchmark_runs": 0, "status": "PROVIDER_FREE"},
        "producer_identity": {
            "repository": "groundtruth",
            "source_revision": TERMINAL_EXPECTED_HEADS["groundtruth_head"],
            "binary_sha256": "UNVERIFIED",
            "toolchain_sha256": "UNVERIFIED",
        },
        "graph_identity": {"schema": "gt.graph.v1", "digest": "UNVERIFIED"},
        "rollback": {
            "strategy": "retain-prior-complete-receipt",
            "prior_complete_sha256": "UNVERIFIED",
        },
        "dependencies": {
            "status": "PROVISIONAL_WAITING_FOR_FINAL_FUNCTIONAL_HEADS",
            "required": ["all functional units", "HAR-36", "HAR-38"],
        },
    }


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
    if (
        receipt.get("schema") not in {BASELINE_SCHEMA, TERMINAL_BASELINE_SCHEMA}
        or not isinstance(results, dict)
    ):
        return False
    if results.get("provider_calls") != 0:
        return False
    digest = receipt.get("receipt_sha256")
    if not isinstance(digest, str):
        return False
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return digest == hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def verify_terminal_receipt(receipt: dict[str, object]) -> bool:
    """Verify the terminal schema and immutable coordinator head anchors."""
    if receipt.get("schema") != TERMINAL_BASELINE_SCHEMA:
        return False
    if (
        isinstance(receipt.get("head_state"), dict)
        and receipt["head_state"].get("status") == "FINAL"
    ):
        return verify_final_terminal_receipt(receipt)
    try:
        _validate_terminal_spec(receipt)
    except (TypeError, ValueError):
        return False
    expected = terminal_receipt_spec()
    if any(receipt.get(key) != value for key, value in expected.items()):
        return False
    authorization = receipt.get("authorization")
    if authorization != {
        "benchmark_ready": False,
        "reason": "explicit_user_approval_and_final_heads_required",
    }:
        return False
    return verify_baseline_receipt(receipt)


def verify_final_terminal_receipt(receipt: dict[str, object]) -> bool:
    """Verify the non-provisional HAR-5 receipt at the current functional heads.

    ``verify_terminal_receipt`` intentionally preserves the historical
    provisional coordinator snapshot.  Final closure has a stricter boundary:
    it must name the exact landed harness/GroundTruth pair and may not contain
    an ``UNVERIFIED`` sentinel anywhere in the evidence payload.
    """
    if receipt.get("schema") != TERMINAL_BASELINE_SCHEMA:
        return False
    try:
        _validate_terminal_spec(receipt)
    except (TypeError, ValueError):
        return False
    if receipt.get("repository") != FINAL_TERMINAL_EXPECTED_HEADS["repository"]:
        return False
    if receipt.get("source_revision") != FINAL_TERMINAL_EXPECTED_HEADS["repository_head"]:
        return False
    head_state = receipt.get("head_state")
    if not isinstance(head_state, dict):
        return False
    if (
        head_state.get("status") != "FINAL"
        or head_state.get("repository_head") != FINAL_TERMINAL_EXPECTED_HEADS["repository_head"]
        or head_state.get("groundtruth_head") != FINAL_TERMINAL_EXPECTED_HEADS["groundtruth_head"]
        or head_state.get("unresolved_dependencies") != []
    ):
        return False
    functional_heads = head_state.get("functional_heads")
    if not isinstance(functional_heads, list) or not functional_heads:
        return False
    if any(row.get("state") != "FINAL" for row in functional_heads if isinstance(row, dict)):
        return False
    producer = receipt.get("producer_identity")
    if not isinstance(producer, dict):
        return False
    if (
        producer.get("source_revision") != FINAL_TERMINAL_EXPECTED_HEADS["groundtruth_head"]
        or producer.get("repository") != "groundtruth"
    ):
        return False
    commands = receipt.get("commands")
    if not isinstance(commands, list) or any(
        not isinstance(row, dict)
        or row.get("status") == "UNVERIFIED"
        or not isinstance(row.get("exit_code"), int)
        for row in commands
    ):
        return False
    dependencies = receipt.get("dependencies")
    if not isinstance(dependencies, dict) or dependencies.get("status") != "FINAL":
        return False

    def contains_unverified(value: object) -> bool:
        if value == "UNVERIFIED":
            return True
        if isinstance(value, dict):
            return any(contains_unverified(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_unverified(item) for item in value)
        return False

    unsigned = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    if contains_unverified(unsigned):
        return False
    authorization = receipt.get("authorization")
    if authorization != {
        "benchmark_ready": False,
        "reason": "explicit_user_approval_and_final_heads_required",
    }:
        return False
    return verify_baseline_receipt(receipt)


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
