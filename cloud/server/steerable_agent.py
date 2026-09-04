"""SteerableAgent — DefaultAgent subclass with mid-run steering, stop, and event streaming.

Subclasses mini-swe-agent's DefaultAgent to add:
- Steering queue: user messages injected at step boundaries
- Stop event: graceful termination between steps
- Event callback: fires for every model response and tool execution (→ SSE)
"""
from __future__ import annotations

import queue
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.exceptions import FormatError, InterruptAgentFlow


class SteerableAgent(DefaultAgent):

    def __init__(
        self,
        model: Any,
        env: Any,
        *,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        config_class: type = AgentConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(model, env, config_class=config_class, **kwargs)
        self._steering_queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._event_callback = event_callback

    def _emit(self, event: dict[str, Any]) -> None:
        if self._event_callback is not None:
            event.setdefault("timestamp", time.time())
            self._event_callback(event)

    def run(self, task: str = "", **kwargs: Any) -> dict:
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        self.add_messages(
            self.model.format_message(
                role="system",
                content=self._render_template(self.config.system_template),
            ),
            self.model.format_message(
                role="user",
                content=self._render_template(self.config.instance_template),
            ),
        )
        self._emit({"type": "lifecycle", "status": "running"})

        while True:
            # --- STOP CHECK ---
            if self._stop_event.is_set():
                self.add_messages(
                    {
                        "role": "exit",
                        "content": "UserStopped",
                        "extra": {
                            "exit_status": "UserStopped",
                            "submission": "",
                        },
                    }
                )
                break

            # --- STEERING INJECTION ---
            while True:
                try:
                    msg = self._steering_queue.get_nowait()
                except queue.Empty:
                    break
                self.add_messages(
                    self.model.format_message(role="user", content=msg)
                )
                self._emit({"type": "steering", "content": msg})

            # --- STEP ---
            try:
                self.step()
                self.n_consecutive_format_errors = 0
            except FormatError as e:
                self.cost += e.messages[0].get("extra", {}).get("cost", 0.0)
                self.n_consecutive_format_errors += 1
                if (
                    0
                    < self.config.max_consecutive_format_errors
                    <= self.n_consecutive_format_errors
                ):
                    self.add_messages(
                        *e.messages,
                        {
                            "role": "exit",
                            "content": "RepeatedFormatError",
                            "extra": {
                                "exit_status": "RepeatedFormatError",
                                "submission": "",
                            },
                        },
                    )
                else:
                    self.add_messages(*e.messages)
            except InterruptAgentFlow as e:
                self.add_messages(*e.messages)
            except Exception as e:
                self.handle_uncaught_exception(e)
                self._emit({
                    "type": "error",
                    "error": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc(),
                })
                raise
            finally:
                self.save(self.config.output_path)

            if self.messages[-1].get("role") == "exit":
                break

        exit_extra = self.messages[-1].get("extra", {})
        exit_status = str(exit_extra.get("exit_status", ""))
        # Exactly one terminal lifecycle event, and it must not contradict the
        # reason the loop ended: a user stop is "stopped", not "completed".
        self._emit({
            "type": "lifecycle",
            "status": "stopped" if exit_status == "UserStopped" else "completed",
            "exit_status": exit_status,
            "n_calls": self.n_calls,
            "cost": self.cost,
        })
        return exit_extra

    def query(self) -> dict:
        message = super().query()
        self._emit({
            "type": "assistant",
            "content": message.get("content", ""),
            "actions": [
                a.get("command", str(a))
                for a in message.get("extra", {}).get("actions", [])
            ],
            "n_calls": self.n_calls,
            "cost": self.cost,
        })
        return message

    def execute_actions(self, message: dict) -> list[dict]:
        actions = message.get("extra", {}).get("actions", [])
        outputs = []
        for action in actions:
            command = action.get("command", "")
            self._emit({
                "type": "tool_call",
                "command": command,
                "n_calls": self.n_calls,
            })
            output = self.env.execute(action)
            outputs.append(output)
            self._emit({
                "type": "tool_result",
                "command": command,
                "output": str(output.get("output", ""))[:4000],
                "returncode": output.get("returncode", -1),
                "is_error": output.get("returncode", -1) != 0,
            })
        return self.add_messages(
            *self.model.format_observation_messages(
                message, outputs, self.get_template_vars()
            )
        )
