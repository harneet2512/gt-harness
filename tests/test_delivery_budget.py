from __future__ import annotations

import hashlib
import json

import pytest

from gt_engine.delivery_budget import (
    DELIVERY_BYTE_LIMITS,
    MAX_TASK_DELIVERIES,
    PROMPT_CONTEXT_BYTE_LIMIT,
    TOTAL_DELIVERY_BYTE_LIMIT,
    delivery_byte_limit,
)
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter


def _adapter(tmp_path) -> MiniSweAdapter:
    return MiniSweAdapter(
        task_id="task",
        state_dir=tmp_path,
        predicates=[Predicate("p", "p")],
    )


def _events(adapter: MiniSweAdapter) -> list[dict]:
    return [
        json.loads(line) for line in adapter.store.path.read_text(encoding="utf-8").splitlines()
    ]


def test_delivery_policy_preserves_size_caps_and_uses_storm_backstop() -> None:
    assert DELIVERY_BYTE_LIMITS == {
        "sealed": 1_400,
        "context_contract": 2_000,
        "context_delta": 1_400,
    }
    assert PROMPT_CONTEXT_BYTE_LIMIT == 1_400
    assert delivery_byte_limit(lane="sealed", kind="localization") == 1_400
    assert delivery_byte_limit(lane="prompt", kind="context_contract") == 2_000
    assert delivery_byte_limit(lane="prompt", kind="context_delta") == 1_400
    assert TOTAL_DELIVERY_BYTE_LIMIT == 9_600
    assert MAX_TASK_DELIVERIES == 24


def test_first_sealed_delivery_cannot_borrow_contract_budget(tmp_path) -> None:
    adapter = _adapter(tmp_path)

    assert not adapter.admit_model_visible_delivery(
        lane="sealed",
        kind="localization",
        rendered="x" * 1_401,
        action_index=0,
        iteration=0,
        dedup_key="sealed-first",
    )
    refusal = [row for row in _events(adapter) if row["event"] == "delivery_refused"][0]
    assert refusal["reason"] == "delivery_byte_ceiling"
    assert refusal["per_delivery_limit"] == 1_400


def test_contract_can_use_full_two_thousand_byte_budget(tmp_path) -> None:
    adapter = _adapter(tmp_path)

    assert adapter.admit_model_visible_delivery(
        lane="prompt",
        kind="context_contract",
        rendered="x" * 2_000,
        action_index=0,
        iteration=0,
        dedup_key="contract",
    )


@pytest.mark.parametrize(
    ("lane", "kind", "limit"),
    (
        ("sealed", "localization", 1_400),
        ("prompt", "context_contract", 2_000),
        ("prompt", "context_delta", 1_400),
    ),
)
def test_one_byte_over_each_lane_type_cap_is_refused_and_journaled(
    tmp_path, lane: str, kind: str, limit: int
) -> None:
    adapter = _adapter(tmp_path)

    assert not adapter.admit_model_visible_delivery(
        lane=lane,
        kind=kind,
        rendered="x" * (limit + 1),
        action_index=0,
        iteration=0,
        dedup_key=f"over-{lane}-{kind}",
    )

    refusal = [row for row in _events(adapter) if row["event"] == "delivery_refused"][0]
    assert refusal["reason"] == "delivery_byte_ceiling"
    assert refusal["rendered_bytes"] == limit + 1
    assert refusal["per_delivery_limit"] == limit
    assert refusal["admitted_count"] == 0
    assert refusal["admitted_bytes"] == 0


def test_five_distinct_legitimate_deliveries_are_admitted(tmp_path) -> None:
    adapter = _adapter(tmp_path)

    admitted = [
        adapter.admit_model_visible_delivery(
            lane="prompt",
            kind="context_delta",
            rendered=f"distinct-{ordinal}",
            action_index=0,
            iteration=ordinal,
            dedup_key=f"legacy-iteration-key-{ordinal}",
        )
        for ordinal in range(5)
    ]

    assert admitted == [True] * 5


def test_fifth_same_boundary_claim_is_refused_and_journaled(
    tmp_path,
) -> None:
    adapter = _adapter(tmp_path)
    admitted = [
        adapter.admit_model_visible_delivery(
            lane="sealed",
            kind="recovery",
            rendered=f"{ordinal:02d}:" + ("x" * 296),
            action_index=ordinal,
            iteration=0,
            dedup_key=f"storm-{ordinal}",
        )
        for ordinal in range(5)
    ]

    assert admitted == ([True] * 4) + [False]
    refusals = [row for row in _events(adapter) if row["event"] == "delivery_refused"]
    assert len(refusals) == 1
    assert refusals[0]["reason"] == "boundary_claim_ceiling"
    assert refusals[0]["candidate_ordinal"] == 5
    assert refusals[0]["boundary_claim_limit"] == 4


def test_prompt_lane_drops_identical_bytes_across_iterations(tmp_path) -> None:
    adapter = _adapter(tmp_path)
    first = adapter.admit_model_visible_delivery(
        lane="prompt",
        kind="context_delta",
        rendered="identical prompt bytes",
        action_index=0,
        iteration=1,
        dedup_key="prompt:iteration:1",
    )
    adapter.bind_provider_payload({
        "messages": [{"role": "user", "content": "identical prompt bytes"}]
    })
    second = adapter.admit_model_visible_delivery(
        lane="prompt",
        kind="context_delta",
        rendered="identical prompt bytes",
        action_index=0,
        iteration=2,
        dedup_key="prompt:iteration:2",
    )

    assert first is True
    assert second is False
    rows = _events(adapter)
    deliveries = [row for row in rows if row["event"] == "context_addition_delivery"]
    assert len(deliveries) == 1
    identity = hashlib.sha256(b"identical prompt bytes").hexdigest()
    assert deliveries[0]["dedup_key"] == f"prompt:{identity}"
    assert deliveries[0]["delivery_identity"] == identity
    refused = [row for row in rows if row["event"] == "delivery_refused"]
    assert refused[-1]["reason"] == "duplicate_delivery_identity"


def test_total_budget_refusal_is_journaled_with_conservation_fields(tmp_path, monkeypatch) -> None:
    # Isolate byte accounting from the independently tested four-claim policy.
    monkeypatch.setattr("gt_engine.miniswe_integration.MAX_BOUNDARY_CLAIMS", 24)
    adapter = _adapter(tmp_path)
    admitted = [
        adapter.admit_model_visible_delivery(
            lane="sealed",
            kind="recovery",
            rendered=f"{ordinal}:" + (chr(65 + ordinal) * 1_298),
            action_index=ordinal,
            iteration=0,
            dedup_key=f"budget-{ordinal}",
        )
        for ordinal in range(8)
    ]

    assert admitted == ([True] * 7) + [False]
    refusal = [row for row in _events(adapter) if row["event"] == "delivery_refused"][0]
    assert refusal["reason"] == "request_delivery_byte_ceiling"
    assert refusal["admitted_count"] == 7
    assert refusal["admitted_bytes"] == 9_100
    assert refusal["request_byte_limit"] == 9_600


def test_refusal_reasons_match_what_the_runtime_can_emit():
    """The constant must be DERIVED from the emission, not typed beside it.

    A product test proved the co-change ceiling fires and a gate test proved
    the gate on a different reason; both passed forever while the pair was
    broken, because nothing asserted the two enumerations agree. The gate
    RAISES on an unlisted reason, so the missing entry failed receipt
    construction on a run where GT had correctly declined to over-deliver.

    Scope is asserted rather than assumed, because the first version of this
    test derived its answer one level short in two ways: it parsed a single
    file while claiming the emission domain is closed repo-wide, and it
    collected only `Assign`-with-a-string-`Constant`, silently skipping every
    other assignment form. A sixth reason returned from a helper -
    `reason = self._new_ceiling(...)` - would have left the five literals
    intact, the set equal, the build green, and the runtime emitting a value
    the gate raises on: the defect this test exists to prevent, reintroduced
    underneath it.
    """
    import ast
    from pathlib import Path as _Path

    from gt_engine.delivery_budget import DELIVERY_REFUSAL_REASONS

    root = _Path(__file__).resolve().parent.parent
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef)
    writers: list[tuple[str, ast.AST]] = []
    scanned = 0

    for directory in ("gt_engine", "scripts", "eval", "gt_harness"):
        for path in (root / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            scanned += 1
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", None) != "append" or not node.args:
                    continue
                first = node.args[0]
                if not (isinstance(first, ast.Constant)
                        and first.value == "delivery_refused"):
                    continue
                # Walk up to the enclosing def. A module-level or lambda write
                # would leave this None and must fail loudly rather than
                # vanish from the census.
                scope = parents.get(node)
                while scope is not None and not isinstance(scope, scopes):
                    scope = parents.get(scope)
                writers.append((path.relative_to(root).as_posix(), scope))

    assert scanned > 100, f"only {scanned} files parsed - the scan lost files"
    assert len(writers) == 1, (
        f"the emission domain is closed only while there is exactly one "
        f"delivery_refused writer; found {[w[0] for w in writers]}"
    )
    where, scope = writers[0]
    assert scope is not None, f"{where}: delivery_refused written outside any def"

    emitted: set[str] = set()
    offenders: list[str] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [node.target]
        else:
            continue
        names = [
            inner for target in targets for inner in ast.walk(target)
            if isinstance(inner, ast.Name) and inner.id == "reason"
        ]
        if not names:
            continue
        simple = (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if not simple:
            # The one shape that defeats this test, so it is the loudest thing
            # it reports rather than something it passes over.
            offenders.append(f"{where}:{node.lineno}")
        elif node.value.value:
            emitted.add(node.value.value)

    assert not offenders, (
        f"`reason` must be assigned a plain string literal so the emission "
        f"domain stays extractable; non-literal assignments at {offenders}"
    )
    assert emitted == set(DELIVERY_REFUSAL_REASONS), (
        f"runtime emits {sorted(emitted - set(DELIVERY_REFUSAL_REASONS))} that "
        f"the harness rejects, and allows "
        f"{sorted(set(DELIVERY_REFUSAL_REASONS) - emitted)} that nothing emits"
    )


def test_prompt_delivery_kinds_match_the_kind_the_session_can_produce():
    """The gate's prompt-kind set must track the expression that produces it.

    The same pair was hand-copied in four places, two of which raise, and the
    producer is a two-valued expression in gt_session. Being one expression
    from the emission makes a copy easy to VERIFY; it does not make it unable
    to DRIFT. A third prompt kind would have lost runs with nothing going red.
    """
    import ast
    from pathlib import Path as _Path

    from gt_engine.delivery_budget import PROMPT_DELIVERY_KINDS

    source = (
        _Path(__file__).resolve().parent.parent / "gt_engine" / "gt_session.py"
    ).read_text(encoding="utf-8")

    produced = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        if getattr(node.targets[0], "id", None) != "contract_kind":
            continue
        for inner in ast.walk(node.value):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                produced.add(inner.value)

    assert produced, "no contract_kind assignment found - the scan is broken"
    assert produced == set(PROMPT_DELIVERY_KINDS), (
        f"session produces {sorted(produced)} but the budget table and every "
        f"gate keyed on it allow {sorted(PROMPT_DELIVERY_KINDS)}"
    )
