"""TB2 host route: the ``exec_transport_failure`` offline reproduction.

``tests/fixtures/capability_matrix.json`` records the surface
``eval.gt_central_agent exec transport + host-only probes`` with
``covering_tests: []`` and ``status: "open - separate repo track"``.  Per
``D:\\gt-context-plan\\docs\\AUDIT-ABILITY-SPEC.md`` section 4 ("TB2: inspect
its host-owned execution and observation route separately.  Do not inherit
Mini-SWE in-container proof.  Its host transport/journal blockers remain open
until their own offline reproductions pass") this module is that
reproduction.  It is an audit pass: nothing under ``eval/`` or ``gt_engine/``
is changed, and the failures it records are the deliverable.

Transport under test.  Every host-side command is one fresh, stateless
``docker compose exec``: ``self._host_executions.exec(...)`` ->
``HostExecutionRecorder.exec`` -> harbor ``DockerEnvironment.exec``.  The
model's action, the ``find -printf`` workspace manifest, and the first-action
red-test probe all ride the same wire, so a single transport fault costs the
agent's action channel and GroundTruth's whole observation channel at once.

Seam.  ``tests/test_gt_central_agent.py:880`` drives ``MiniSweCentralAgent``
in process through a fake Harbor environment exposing one async
``exec(...) -> ExecResult`` plus model injection via ``agent._model_factory``
(consumed at ``eval/gt_central_agent.py:3733``).  That module cannot be
imported here: ``D:\\gt-cloud\\scripts\\__init__.py`` is a regular package on
``sys.path`` and shadows this repo's namespace ``scripts`` package, so
``from scripts.central_bootstrap_canary import ...`` at its line 95 raises
``ModuleNotFoundError``.  ``_Environment`` below is therefore a faithful local
copy of that stub rather than a subclass of it; the shadowing is an
environment defect reported separately, not a property of the route.

No provider call, no benchmark dispatch, no Docker.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext

from eval.gt_central_agent import MiniSweCentralAgent
from gt_engine.central_runtime import WorkspaceSnapshot
from gt_engine.host_execution import HostExecCategory

SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

MODEL_ACTION = "model_action"
WORKSPACE_MANIFEST = "workspace_manifest"


class _Fault:
    """One transport fault mode.

    ``raises`` marks the modes where the transport itself dies; the others
    return a real ``ExecResult`` and act as controls (a container OOM-killing
    the *command* is a genuine command result, not a transport fault).
    """

    def __init__(self, name: str, *, raises: bool):
        self.name = name
        self.raises = raises

    def __repr__(self) -> str:  # pragma: no cover - test id readability only
        return f"_Fault({self.name!r})"

    async def apply(self) -> ExecResult:
        if self.name == "runtime_error":
            raise RuntimeError("docker compose exec failed")
        if self.name == "timeout_error":
            raise TimeoutError()
        if self.name == "docker_timeout_string":
            # What harbor actually raises when the `docker compose` client is
            # killed: a RuntimeError, never a TimeoutError.
            raise RuntimeError("Command timed out after 30 seconds")
        if self.name == "never_returns":
            await asyncio.wait_for(asyncio.Event().wait(), timeout=0.05)
        if self.name == "return_code_137":
            return ExecResult(stdout="", return_code=137)
        if self.name == "invalid_utf8":
            return ExecResult(
                stdout=b"\xff\xfe corrupted\n".decode("utf-8", "surrogateescape"),
                return_code=0,
            )
        if self.name == "two_megabyte_output":
            return ExecResult(stdout="x" * (2 * 1024 * 1024), return_code=0)
        raise AssertionError(f"unknown fault mode {self.name!r}")


FAULTS = (
    _Fault("runtime_error", raises=True),
    _Fault("timeout_error", raises=True),
    _Fault("docker_timeout_string", raises=True),
    _Fault("never_returns", raises=True),
    _Fault("return_code_137", raises=False),
    _Fault("invalid_utf8", raises=False),
    _Fault("two_megabyte_output", raises=False),
)
FAULT_IDS = tuple(fault.name for fault in FAULTS)
RAISING = tuple(fault for fault in FAULTS if fault.raises)
RAISING_IDS = tuple(fault.name for fault in RAISING)


class _Environment:
    """Mirror of the fake Harbor environment at tests/test_gt_central_agent.py:880."""

    default_user = "root"

    def __init__(self):
        self.commands: list[tuple[str, dict | None]] = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.commands.append((command, env))
        if command == "pwd -P":
            return ExecResult(stdout="/app\n", return_code=0)
        if command.startswith("uname "):
            return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
        if "-printf" in command:
            return ExecResult(stdout="", return_code=0)
        if command == SUBMIT:
            return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
        return ExecResult(stdout="", return_code=0)


def _category_of(command: str) -> str:
    if "-printf" in command:
        return WORKSPACE_MANIFEST
    if command == "pwd -P" or command.startswith("uname "):
        return "system"
    return MODEL_ACTION


class _FaultingEnvironment(_Environment):
    """``exec`` fails from the Nth call of one host-exec category onwards."""

    def __init__(self, fault: _Fault, target: str, *, nth: int = 1):
        super().__init__()
        self.fault = fault
        self.target = target
        self.nth = nth
        self.matched = 0

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        if _category_of(command) == self.target:
            self.matched += 1
            if self.matched >= self.nth:
                self.commands.append((command, env))
                return await self.fault.apply()
        return await super().exec(command, cwd, env, timeout_sec, user)


class _GenuineFailureEnvironment(_Environment):
    """A command that genuinely exits -1 and prints the transport's own text.

    Nothing stops a real container command from doing exactly this, which is
    why it is the correct control for assertion (2).
    """

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        if _category_of(command) == MODEL_ACTION:
            self.commands.append((command, env))
            return ExecResult(stdout="", stderr=self.text, return_code=-1)
        return await super().exec(command, cwd, env, timeout_sec, user)


class _ScriptedModel:
    """Mirror of the injected model at tests/test_gt_central_agent.py:1341."""

    config = type("Config", (), {"model_name": "test"})()
    tools = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]

    def __init__(self, commands):
        self.script = list(commands)
        self.index = 0
        self.observed: list[str] = []
        self.observed_history: list[list[str]] = []
        self.tool_outputs: list[dict] = []

    def format_message(self, **kwargs):
        return kwargs

    def get_template_vars(self):
        return {"observation_template": "{{ output.output }}", "format_error_template": "error"}

    def query(self, messages):
        self.observed = [str(item.get("content") or "") for item in messages]
        self.observed_history.append(list(self.observed))
        command = self.script[min(self.index, len(self.script) - 1)]
        self.index += 1
        return {
            "role": "assistant",
            "content": "act",
            "extra": {
                "actions": [{"command": command, "tool_call_id": "call-1"}],
                "response": {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
                },
                "cost": 0.0,
            },
        }

    def format_observation_messages(self, message, outputs, template_vars=None):
        self.tool_outputs.append(dict(outputs[0]))
        return [{"role": "tool", "content": outputs[0]["output"]}]


class _Run:
    def __init__(self, logs_dir: Path, model: _ScriptedModel, error: BaseException | None):
        self.logs_dir = logs_dir
        self.model = model
        self.error = error

    @property
    def receipt(self) -> dict:
        path = self.logs_dir / "central_receipt.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def host_exec_receipts(self) -> list[dict]:
        return list((self.receipt.get("host_execution") or {}).get("receipts") or [])

    @property
    def feature_receipts(self) -> list[dict]:
        return list((self.receipt.get("features") or {}).get("receipts") or [])

    @property
    def exit_status(self) -> str:
        path = self.logs_dir / "miniswe_trajectory.json"
        if not path.is_file():
            return ""
        document = json.loads(path.read_text(encoding="utf-8"))
        return str((document.get("info") or {}).get("exit_status") or "")

    @property
    def model_facing(self) -> list[tuple[str, object]]:
        return [(row["output"], row["returncode"]) for row in self.model.tool_outputs]


async def _drive(logs_dir: Path, environment, *, script=("ls -la", SUBMIT, SUBMIT)) -> _Run:
    model = _ScriptedModel(script)
    agent = MiniSweCentralAgent(
        logs_dir=logs_dir,
        model_name="test",
        integration_mode="active",
        policy_mode="certified_active",
        step_limit=4,
    )
    agent._model_factory = lambda: model
    error: BaseException | None = None
    try:
        await agent.run("Fix the reported defect and submit.", environment, AgentContext())
    except Exception as exc:  # noqa: BLE001 - the terminal is the observation
        error = exc
    return _Run(logs_dir, model, error)


# ---------------------------------------------------------------------------
# Red-test probe fixtures (shape copied from tests/test_gt_red_test_probe.py).
# ---------------------------------------------------------------------------


def _probe_snapshot() -> WorkspaceSnapshot:
    entry = SimpleNamespace(
        kind="f",
        size=10,
        mtime=0.0,
        ctime=0.0,
        link_target=None,
        digest="a" * 64,
        content="def save_user():\n    pass\n",
    )
    return WorkspaceSnapshot(
        revision="workspace-1",
        entries={"src/service.py": entry, "tests/test_service.py": entry},
        healthy=True,
        reason="",
        elapsed_seconds=0.1,
    )


def _probe_agent(tmp_path: Path) -> MiniSweCentralAgent:
    return MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        policy_mode="certified_active",
        enable_first_action_red_test=True,
        enable_preemptive_retrieval=False,
        enable_context_frontier=False,
        enable_completion_controller=False,
        enable_repository_intelligence=False,
        enable_feature_guidance=False,
    )


async def _drive_probe(agent: MiniSweCentralAgent, environment) -> dict:
    return await agent._run_first_action_red_test(
        environment,
        explicit_checks=("pytest tests/test_service.py -q",),
        snapshot=_probe_snapshot(),
        task_deliverables=(),
        source_revision="workspace-1",
        graph_source_revision="graph-source",
        deadline=None,
    )


# ---------------------------------------------------------------------------
# Assertion (1): the receipt distinguishes a transport failure from a command
# failure, and the distinction survives into central_receipt.json.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", FAULTS, ids=FAULT_IDS)
@pytest.mark.parametrize("target", [MODEL_ACTION, WORKSPACE_MANIFEST])
async def test_transport_fault_is_recoverable_from_central_receipt(tmp_path, fault, target):
    run = await _drive(tmp_path, _FaultingEnvironment(fault, target))
    rows = [row for row in run.host_exec_receipts if row["category"] == target]
    assert rows, f"no {target} host-exec receipt was written at all"

    transport = [row for row in rows if row["return_code"] is None and row["exception_type"]]
    if fault.raises:
        assert transport, (
            f"{fault.name}/{target}: the transport died but no HostExecReceipt carries "
            "return_code=None with a non-empty exception_type"
        )
        assert all(row["executed"] is True and row["cache_hit"] is False for row in transport)
    else:
        assert not transport, f"{fault.name}/{target}: a real ExecResult was booked as transport"
        assert any(row["return_code"] is not None for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", RAISING, ids=RAISING_IDS)
async def test_red_test_probe_transport_fault_is_recorded(tmp_path, fault):
    """RED_TEST_PROBE leg of the same matrix.

    The probe's in-loop gate at ``eval/gt_central_agent.py:4077-4086`` needs
    ``substrate_ready`` plus a complete graph receipt, which no offline
    fixture on this host can produce (no ``gt-index`` binary), so the probe is
    driven directly the way ``tests/test_gt_red_test_probe.py`` drives it.
    The receipt is asserted on the recorder rather than on
    ``central_receipt.json``: ``run()`` installs a fresh
    ``HostExecutionRecorder`` at ``eval/gt_central_agent.py:3723``, so a
    directly driven probe never reaches the run's own receipt file.
    """
    agent = _probe_agent(tmp_path)
    receipt = await _drive_probe(agent, _FaultingEnvironment(fault, MODEL_ACTION))

    assert receipt["status"] == "failed_open"
    rows = [
        row
        for row in agent._host_executions.receipts
        if row.category is HostExecCategory.RED_TEST_PROBE
    ]
    assert rows, "the red-test probe transport fault produced no host-exec receipt"
    assert rows[-1].return_code is None
    assert rows[-1].exception_type


# ---------------------------------------------------------------------------
# Assertion (2): the model-facing observation for a transport fault is not
# byte-identical to a genuine return_code=-1 command result.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_transport_fault_indistinguishable: eval/gt_central_agent.py:8452-8457 "
        "converts any transport exception into ExecResult(stdout='', "
        "stderr=f'{type(exc).__name__}: {exc}', return_code=-1), which is a shape a "
        "real command can produce.  A container OOM-kill is byte-identical to a "
        "failed command in the model's context; the distinction survives only in "
        "HostExecReceipt, which never reaches the trajectory."
    ),
)
@pytest.mark.asyncio
async def test_transport_fault_observation_differs_from_command_failure(tmp_path):
    text = "RuntimeError: docker compose exec failed"
    transport = await _drive(
        tmp_path / "transport",
        _FaultingEnvironment(_Fault("runtime_error", raises=True), MODEL_ACTION),
    )
    genuine = await _drive(tmp_path / "genuine", _GenuineFailureEnvironment(text))

    assert transport.model_facing, "no model-facing observation was produced"
    assert transport.model_facing != genuine.model_facing


@pytest.mark.asyncio
async def test_transport_fault_observation_current_truth_is_identical(tmp_path):
    """GREEN companion: pin the collision so a fix has to move this row."""
    text = "RuntimeError: docker compose exec failed"
    transport = await _drive(
        tmp_path / "transport",
        _FaultingEnvironment(_Fault("runtime_error", raises=True), MODEL_ACTION),
    )
    genuine = await _drive(tmp_path / "genuine", _GenuineFailureEnvironment(text))

    assert transport.model_facing == genuine.model_facing
    assert transport.model_facing[0] == (text, -1)


# ---------------------------------------------------------------------------
# Assertion (3): the docker-shaped timeout string must classify as a timeout.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_exec_timeout_dead_branch: `except TimeoutError` at "
        "eval/gt_central_agent.py:3285 is dead code on the real transport.  "
        "Harbor's DockerEnvironment raises RuntimeError('Command timed out after N "
        "seconds'), so the probe falls through to the generic handler at :3294 and "
        "reports probe_error:RuntimeError with timeout=False.  The existing "
        "coverage (tests/test_gt_red_test_probe.py:257) raises a synthetic "
        "TimeoutError, which is why the dead branch looks covered."
    ),
)
@pytest.mark.asyncio
async def test_docker_shaped_timeout_is_classified_as_probe_timeout(tmp_path):
    agent = _probe_agent(tmp_path)
    receipt = await _drive_probe(
        agent, _FaultingEnvironment(_Fault("docker_timeout_string", raises=True), MODEL_ACTION)
    )

    assert receipt["status"] == "failed_open"
    assert receipt["reason_codes"] == ["probe_timeout"]
    assert receipt["timeout"] is True


@pytest.mark.asyncio
async def test_docker_shaped_timeout_current_truth_is_probe_error(tmp_path):
    """GREEN companion to the xfail above: pin today's behaviour."""
    agent = _probe_agent(tmp_path)
    receipt = await _drive_probe(
        agent, _FaultingEnvironment(_Fault("docker_timeout_string", raises=True), MODEL_ACTION)
    )

    assert receipt["reason_codes"] == ["probe_error:RuntimeError"]
    assert receipt["timeout"] is False


# ---------------------------------------------------------------------------
# Assertion (4): no FeatureReceipt is DELIVERED from an action whose transport
# failed.
# ---------------------------------------------------------------------------


def _delivered_from(run: _Run, action_ids: set[int]) -> list[dict]:
    return [
        row
        for row in run.feature_receipts
        if row.get("decision") == "DELIVERED" and int(row.get("action") or 0) in action_ids
    ]


def _failed_action_ids(run: _Run, target: str) -> set[int]:
    return {
        int(row["action_id"])
        for row in run.host_exec_receipts
        if row["category"] == target and row["return_code"] is None and row["exception_type"]
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_delivered_from_failed_transport: with every model action's transport "
        "dead, eval/gt_central_agent.py:8452-8457 hands the loop a synthetic "
        "return_code=-1 result, the submit boundary still fires, and "
        "CentralFeatureRuntime._emit (gt_engine/central_runtime.py:2397) stamps "
        "GT_CERT_DELIVERY DELIVERED on the very action whose command never reached "
        "the container."
    ),
)
@pytest.mark.asyncio
@pytest.mark.parametrize("fault", RAISING, ids=RAISING_IDS)
async def test_no_feature_receipt_delivered_from_failed_model_action(tmp_path, fault):
    run = await _drive(tmp_path, _FaultingEnvironment(fault, MODEL_ACTION))
    failed = _failed_action_ids(run, MODEL_ACTION)
    assert failed, "the fault never reached a model action"
    assert _delivered_from(run, failed) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", RAISING, ids=RAISING_IDS)
async def test_no_feature_receipt_delivered_from_failed_workspace_manifest(tmp_path, fault):
    """The sensor leg holds today: a dead manifest downgrades the row to PASS."""
    run = await _drive(tmp_path, _FaultingEnvironment(fault, WORKSPACE_MANIFEST))
    failed = _failed_action_ids(run, WORKSPACE_MANIFEST)
    assert failed, "the fault never reached a workspace manifest probe"
    assert _delivered_from(run, failed) == []


# ---------------------------------------------------------------------------
# Assertion (5): the loop terminates with a diagnosable terminal, never
# ``Submitted``.
# ---------------------------------------------------------------------------


_DIAGNOSABLE_XFAIL = pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_dead_container_submits_clean: a permanently dead transport still "
        "reaches exit_status='Submitted'.  eval/gt_central_agent.py:8452-8457 "
        "swallows the fault into a -1 result, so neither the submit sentinel check "
        "nor the terminal classifier ever learns that no command ran; the run "
        "reports a clean submission from a container it never reached."
    ),
)


_OOM_XFAIL = pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_persistent_sigkill_submits_clean: every action returning 137 is the "
        "container's OOM-killer speaking, and the loop books each one as an "
        "ordinary failed command.  Nothing in eval/gt_central_agent.py treats a "
        "persistent SIGKILL exit as a container-health signal, so the run still "
        "ends Submitted.  This is the shape the 2026-07-18 TB2 run logged on a "
        "QEMU task and the shape the four GT-off AgentTimeoutError tasks sit in."
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "target"),
    [
        pytest.param(fault, target, id=f"{fault.name}-{target}", marks=_DIAGNOSABLE_XFAIL)
        for fault in FAULTS
        if fault.raises
        for target in (MODEL_ACTION, WORKSPACE_MANIFEST)
    ]
    + [
        pytest.param(
            _Fault("return_code_137", raises=False),
            target,
            id=f"return_code_137-{target}",
            marks=_OOM_XFAIL,
        )
        for target in (MODEL_ACTION, WORKSPACE_MANIFEST)
    ]
    + [
        pytest.param(
            _Fault("two_megabyte_output", raises=False),
            MODEL_ACTION,
            id="two_megabyte_output-model_action",
        ),
        pytest.param(
            _Fault("invalid_utf8", raises=False),
            MODEL_ACTION,
            id="invalid_utf8-model_action",
        ),
    ],
)
async def test_transport_failure_terminates_diagnosably(tmp_path, fault, target):
    run = await _drive(tmp_path, _FaultingEnvironment(fault, target))
    assert run.exit_status, "the run left no terminal status at all"
    assert run.exit_status != "Submitted", (
        f"{fault.name}/{target}: the loop reported a clean submission after a transport fault"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "tb2_surrogate_output_crashes_run: container output that is not valid UTF-8 "
        "reaches the artifact writers as lone surrogates and _atomic_write_text(..., "
        "encoding='utf-8') raises UnicodeEncodeError out of MiniSweCentralAgent.run. "
        "The run dies mid-flight: central_receipt.json is written without the "
        "submit-boundary feature rows.  Reading one binary file in the container is "
        "enough to trigger it."
    ),
)
@pytest.mark.asyncio
async def test_invalid_utf8_output_does_not_abort_the_run(tmp_path):
    run = await _drive(
        tmp_path, _FaultingEnvironment(_Fault("invalid_utf8", raises=False), MODEL_ACTION)
    )
    assert run.error is None, f"agent.run raised {run.error!r}"


@pytest.mark.asyncio
async def test_invalid_utf8_output_current_truth_is_a_unicode_crash(tmp_path):
    """GREEN companion: pin today's behaviour so a fix has to move this row."""
    run = await _drive(
        tmp_path, _FaultingEnvironment(_Fault("invalid_utf8", raises=False), MODEL_ACTION)
    )
    assert isinstance(run.error, UnicodeEncodeError)
    assert run.exit_status == "UnicodeEncodeError"
