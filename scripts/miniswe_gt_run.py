"""Run pinned Mini-SWE-Agent 2.x with the GT lifecycle adapter (or GT-off).

--gt-off builds the stock Mini-SWE agent only. Every gt_engine import is
lazy (inside build_agent, behind the flag) so a GT-off run never imports
gt_engine/groundtruth and the container needs no groundtruth wheel.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # runtime-safe: never imported when running
    from gt_engine.miniswe_integration import MiniSweAdapter

# Must precede the minisweagent import: LitellmModelConfig.cost_tracking's
# default is evaluated at class definition time. Routing via openai/ + api_base
# means litellm has no price for the gateway's model id (openai/deepseek-v4-flash
# is not in model_cost), and without ignore_errors every trial aborts. Token
# counts stay in the trajectory (extra.response.usage); cost derives from them
# at freeze time. Identical for both arms.
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")

from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.config import builtin_config_dir
from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig
from minisweagent.models.litellm_model import LitellmModel


def _templates() -> tuple[str, str]:
    import yaml

    config = yaml.safe_load((builtin_config_dir / "mini.yaml").read_text())
    agent = config["agent"]
    return str(agent["system_template"]), str(agent["instance_template"])


def build_agent(
    *,
    task: str,
    model: str,
    cwd: str,
    state_dir: str,
    output: str | None,
    temperature: float,
    gt_off: bool,
) -> tuple[DefaultAgent, MiniSweAdapter | None]:
    system_template, instance_template = _templates()
    model_name = model
    model_kwargs: dict = {"temperature": temperature}
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        # litellm refuses a bare model name ("LLM Provider NOT provided") and
        # needs an explicit provider prefix + api_base. Route through the
        # gateway deterministically so the served model id (what -m passes,
        # e.g. "deepseek-v4-flash") maps to the same endpoint on every litellm
        # version. Identical code path for GT-off and GT-on arms.
        if "/" not in model_name:
            model_name = f"openai/{model_name}"
        model_kwargs["api_base"] = base_url
    model_obj = LitellmModel(
        model_name=model_name,
        model_kwargs=model_kwargs,
    )
    env_obj = LocalEnvironment(
        config_class=LocalEnvironmentConfig,
        cwd=cwd,
    )
    agent = DefaultAgent(
        model_obj,
        env_obj,
        config_class=AgentConfig,
        system_template=system_template,
        instance_template=instance_template,
        step_limit=100,
        output_path=Path(output) if output else None,
    )
    if gt_off:
        return agent, None
    import hashlib

    from gt_engine.miniswe_controller import Predicate
    from gt_engine.miniswe_integration import MiniSweAdapter
    from gt_engine.miniswe_runtime import install_runtime_hooks
    from gt_engine.task_contract import extract_task_contract
    from gt_engine.verification_contract import compile_obligation_predicates

    contract = extract_task_contract(task)
    compiled = compile_obligation_predicates(contract)
    predicates = tuple(
        Predicate(item.predicate_id, contract_obligation.text)
        for contract_obligation in contract.obligations
        for item in (compiled[contract_obligation.obligation_id],)
    )
    adapter = MiniSweAdapter(
        task_id=hashlib.sha256(task.encode("utf-8")).hexdigest()[:16],
        state_dir=state_dir,
        predicates=predicates,
        contract=contract,
    )
    install_runtime_hooks(agent, adapter)
    return agent, adapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--state-dir", default=".gt-state")
    parser.add_argument("--output")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--gt-off", action="store_true")
    args = parser.parse_args()
    agent, adapter = build_agent(
        task=args.task,
        model=args.model,
        cwd=args.cwd,
        state_dir=args.state_dir,
        output=args.output,
        temperature=args.temperature,
        gt_off=args.gt_off,
    )
    result = agent.run(args.task)
    if args.gt_off:
        print(
            json.dumps(
                {
                    "result": result,
                    "stats": {"n_calls": agent.n_calls, "cost": agent.cost},
                },
                sort_keys=True,
            )
        )
    else:
        print(json.dumps({"result": result, "gt": adapter.final_state()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
