"""Session runner — clones a repo, builds the mini-SWE agent, and runs it."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .events import EventBus
from .steerable_agent import SteerableAgent
from .store import SessionStore

_STATE_DIRNAME = ".gt_state"


class SessionRunner:
    def __init__(self, store: SessionStore, event_bus: EventBus) -> None:
        self._store = store
        self._event_bus = event_bus
        self._agents: dict[str, SteerableAgent] = {}
        self._max_concurrent = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "3"))
        self._running_count = 0

    def get_agent(self, session_id: str) -> SteerableAgent | None:
        return self._agents.get(session_id)

    async def launch(
        self,
        session_id: str,
        *,
        repo: str,
        ref: str,
        task: str,
        model: str,
        gt_mode: str,
        step_limit: int,
        temperature: float,
    ) -> None:
        if self._running_count >= self._max_concurrent:
            raise RuntimeError(
                f"max concurrent sessions ({self._max_concurrent}) reached"
            )
        loop = asyncio.get_running_loop()
        asyncio.ensure_future(
            asyncio.to_thread(
                self._run_blocking,
                session_id,
                repo=repo,
                ref=ref,
                task=task,
                model=model,
                gt_mode=gt_mode,
                step_limit=step_limit,
                temperature=temperature,
                loop=loop,
            )
        )

    def _run_blocking(
        self,
        session_id: str,
        *,
        repo: str,
        ref: str,
        task: str,
        model: str,
        gt_mode: str,
        step_limit: int,
        temperature: float,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._running_count += 1
        workdir = tempfile.mkdtemp(prefix=f"cloud-{session_id}-")
        try:
            self._emit_sync(loop, session_id, {
                "type": "lifecycle",
                "data": {"status": "cloning", "repo": repo, "ref": ref},
            })
            asyncio.run_coroutine_threadsafe(
                self._store.update_status(session_id, "running"),
                loop,
            ).result(timeout=10)

            clone_result = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", ref, repo, workdir],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if clone_result.returncode != 0:
                self._emit_sync(loop, session_id, {
                    "type": "error",
                    "data": {"error": f"git clone failed: {clone_result.stderr}"},
                })
                asyncio.run_coroutine_threadsafe(
                    self._store.update_status(session_id, "failed"),
                    loop,
                ).result(timeout=10)
                return

            self._emit_sync(loop, session_id, {
                "type": "lifecycle",
                "data": {"status": "building_agent"},
            })

            agent = self._build_agent(
                task=task,
                model=model,
                cwd=workdir,
                gt_mode=gt_mode,
                step_limit=step_limit,
                temperature=temperature,
                session_id=session_id,
                loop=loop,
            )
            self._agents[session_id] = agent

            self._emit_sync(loop, session_id, {
                "type": "lifecycle",
                "data": {"status": "running"},
            })

            result = agent.run(task)

            patch = self._extract_patch(workdir)
            trajectory = self._extract_trajectory(agent)
            terminal = _classify_terminal_simple(result)

            session_result = {
                "patch": patch,
                "receipt": {
                    "exit_status": result.get("exit_status", ""),
                    "submission": result.get("submission", ""),
                    "n_calls": agent.n_calls,
                    "cost": agent.cost,
                    "terminal_outcome": terminal,
                },
                "trajectory": trajectory,
                "terminal_outcome": terminal,
            }

            asyncio.run_coroutine_threadsafe(
                self._store.store_result(session_id, session_result),
                loop,
            ).result(timeout=10)

            final_status = _TERMINAL_TO_STATUS.get(
                terminal, "failed" if terminal in _FAILURE_TERMINALS else "completed"
            )
            asyncio.run_coroutine_threadsafe(
                self._store.update_status(
                    session_id,
                    final_status,
                    steps=agent.n_calls,
                    cost=agent.cost,
                ),
                loop,
            ).result(timeout=10)

        except Exception as exc:
            self._emit_sync(loop, session_id, {
                "type": "error",
                "data": {"error": f"{type(exc).__name__}: {exc}"},
            })
            try:
                asyncio.run_coroutine_threadsafe(
                    self._store.update_status(session_id, "failed"),
                    loop,
                ).result(timeout=10)
            except Exception:
                pass
        finally:
            self._agents.pop(session_id, None)
            self._running_count -= 1
            self._event_bus.finish(session_id)
            try:
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception:
                pass

    def _build_agent(
        self,
        *,
        task: str,
        model: str,
        cwd: str,
        gt_mode: str,
        step_limit: int,
        temperature: float,
        session_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> SteerableAgent:
        import yaml
        from minisweagent.agents.default import AgentConfig
        from minisweagent.config import builtin_config_dir
        from minisweagent.models.litellm_model import LitellmModel

        from .environment import CloudLocalEnvironment, LocalEnvironmentConfig

        config = yaml.safe_load((builtin_config_dir / "mini.yaml").read_text())
        agent_cfg = config["agent"]
        system_template = str(agent_cfg["system_template"])
        instance_template = str(agent_cfg["instance_template"])

        model_kwargs: dict[str, Any] = {"temperature": temperature, "num_retries": 0}
        model_name = model
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url:
            if not model_name.startswith("openai/"):
                model_name = f"openai/{model_name}"
            model_kwargs["api_base"] = base_url

        gt_off = gt_mode == "off"

        if gt_off:
            model_obj = LitellmModel(model_name=model_name, model_kwargs=model_kwargs)
        else:
            try:
                from gt_engine.miniswe_typed_actions import GroundTruthLitellmModel
                model_obj = GroundTruthLitellmModel(
                    model_name=model_name, model_kwargs=model_kwargs
                )
            except ImportError:
                model_obj = LitellmModel(model_name=model_name, model_kwargs=model_kwargs)
                gt_off = True

        env_obj = CloudLocalEnvironment(
            config_class=LocalEnvironmentConfig,
            cwd=cwd,
            timeout=30,
        )

        state_dir = os.path.join(cwd, _STATE_DIRNAME)
        os.makedirs(state_dir, exist_ok=True)
        output_path = os.path.join(state_dir, "trajectory.json")

        def event_callback(event: dict) -> None:
            self._emit_sync(loop, session_id, event)

        agent = SteerableAgent(
            model_obj,
            env_obj,
            event_callback=event_callback,
            config_class=AgentConfig,
            system_template=system_template,
            instance_template=instance_template,
            step_limit=step_limit,
            output_path=Path(output_path),
        )

        if not gt_off:
            try:
                from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
                from gt_engine.indexer import ensure_index_with_receipt
                from gt_engine.miniswe_controller import Predicate
                from gt_engine.miniswe_integration import MiniSweAdapter
                from gt_engine.miniswe_runtime import install_runtime_hooks
                from gt_engine.task_contract import extract_task_contract
                from gt_engine.verification_contract import compile_obligation_predicates

                index_receipt = ensure_index_with_receipt(cwd, state_dir=state_dir)
                graph_db = index_receipt.graph_db if index_receipt.available else None

                contract = extract_task_contract(task)
                compiled = compile_obligation_predicates(contract)
                predicates = tuple(
                    Predicate(item.predicate_id, co.text)
                    for co in contract.obligations
                    for item in (compiled[co.obligation_id],)
                )

                import hashlib
                task_id = hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]

                adapter = MiniSweAdapter(
                    task_id=task_id,
                    state_dir=state_dir,
                    predicates=predicates,
                    contract=contract,
                    repo_root=cwd,
                    graph_db=graph_db,
                    issue_text=task,
                    requested_model=model,
                    resolved_model=model_name,
                )
                adapter.initial_index_receipt = index_receipt

                session = GTSession(
                    GTSessionConfig(
                        task_id=task_id,
                        repo_root=cwd,
                        state_dir=state_dir,
                        graph_db=graph_db,
                        capabilities=(),
                        issue_text=task,
                        mode=GTMode(gt_mode),
                    ),
                    engine=adapter,
                )

                install_runtime_hooks(agent, session)

                self._emit_sync(loop, session_id, {
                    "type": "lifecycle",
                    "data": {
                        "status": "gt_ready",
                        "graph_db": str(graph_db or ""),
                        "gt_mode": gt_mode,
                    },
                })
            except Exception as exc:
                self._emit_sync(loop, session_id, {
                    "type": "lifecycle",
                    "data": {
                        "status": "gt_unavailable",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                })

        return agent

    def _emit_sync(
        self,
        loop: asyncio.AbstractEventLoop,
        session_id: str,
        event: dict,
    ) -> None:
        event.setdefault("timestamp", time.time())
        asyncio.run_coroutine_threadsafe(
            self._event_bus.publish(session_id, event),
            loop,
        )

    @staticmethod
    def _extract_patch(workdir: str) -> str | None:
        """Diff of the workspace, including files the agent newly created.

        A plain `git diff` misses untracked files, so mark everything
        intent-to-add first. Harness scratch (`.gt_state/`) is excluded so the
        trajectory file never leaks into the patch.
        """
        pathspec = [".", f":(exclude){_STATE_DIRNAME}"]
        try:
            subprocess.run(
                ["git", "add", "-A", "-N", "--", *pathspec],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            result = subprocess.run(
                ["git", "diff", "--", *pathspec],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            diff = result.stdout.strip()
            return diff if diff else None
        except Exception:
            return None

    @staticmethod
    def _extract_trajectory(agent: SteerableAgent) -> dict:
        try:
            data = agent.serialize()
            return {
                "messages": data.get("messages", []),
                "info": data.get("info", {}),
            }
        except Exception:
            return {"messages": list(agent.messages)}


# A user stop is a distinct terminal state: neither a success nor a failure.
_TERMINAL_TO_STATUS = {"user_stopped": "stopped"}

_FAILURE_TERMINALS = {
    "internal_error",
    "setup_error",
    "provider_failed",
    "provider_model_mismatch",
    "timeout",
}


def _classify_terminal_simple(result: dict) -> str:
    exit_status = str(result.get("exit_status", ""))
    if "Submitted" in exit_status:
        return "submitted"
    if "LimitsExceeded" in exit_status:
        return "budget_exhausted"
    if "UserStopped" in exit_status:
        return "user_stopped"
    if "Timeout" in exit_status or "TimeExceeded" in exit_status:
        return "timeout"
    if result.get("submission"):
        return "submitted"
    return "completed"
