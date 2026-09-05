"""ConversationalAgent — one mini-swe transcript that spans many chat turns.

Subclasses mini-swe-agent's ``DefaultAgent``. The differences that matter:

* **Persistent memory.** ``messages`` is built once by ``begin_session()`` and
  then grows for the life of the session. The agent's memory is its real
  trajectory, not a summary.
* **A turn ends when the agent talks to the user.** A model response with no
  command block reaches us as ``FormatError``; if it carries text, that text is
  the reply and the turn is over (``reply``/``question``). Only an empty
  response is treated as a genuine format error.
* **Steering and stop** are drained/checked at the top of every step, so a user
  message that lands mid-turn is answered in context.
* **Bounded context.** Old tool observations are collapsed once the transcript
  crosses ``MAX_CONTEXT_CHARS``; user messages and agent replies are never
  touched.
* **Tool frames come from the environment boundary.** ``env`` is wrapped in
  :class:`_EmittingEnvironment`, so ``tool_call``/``tool_result`` fire for every
  command no matter who runs it. That matters because
  ``gt_engine.miniswe_runtime.install_runtime_hooks`` *replaces*
  ``agent.execute_actions`` with a GT version that calls ``env.execute`` itself
  and never delegates back — emission anchored in ``execute_actions`` would
  never be reached under GT.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.exceptions import (
    FormatError,
    InterruptAgentFlow,
    LimitsExceeded,
    Submitted,
)

DEFAULT_MAX_CONTEXT_CHARS = 240_000
KEEP_RECENT_OBSERVATIONS = 20
MIN_TRUNCATABLE_CHARS = 64
#: per-turn wall-clock budget, in seconds. ``0`` disables it.
DEFAULT_TURN_WALL_SECONDS = 900

STOPPED_REPLY = "Stopped."
STEP_LIMIT_REPLY = (
    "I used the step budget for this turn without finishing. Where I am: "
    "{thought}. Say 'continue' to keep going."
)
TIME_LIMIT_REPLY = (
    "I used the time budget for this turn ({minutes} min) without finishing. "
    "Where I am: {thought}. Say 'continue' to keep going."
)
FORMAT_ERROR_REPLY = (
    "I could not produce a valid command after several attempts, so I stopped "
    "this turn. Tell me how you would like me to proceed."
)

#: finish reasons that end a turn (mirrors ``Message.meta.finish_reason``)
REPLY = "reply"
QUESTION = "question"
STEP_LIMIT = "step_limit"
TIME_LIMIT = "time_limit"
STOPPED = "stopped"
SUBMITTED = "submitted"
ERROR = "error"


def turn_wall_seconds() -> int:
    """The default per-turn wall-clock budget, from ``TURN_WALL_SECONDS``.

    Cost is always ``0.0`` under ``MSWEA_COST_TRACKING=ignore_errors``, so
    steps were the only budget a turn had. A step is not a unit of time: one
    ``pytest`` invocation can outlast fifty ``grep``s. This is the other half.
    """
    try:
        value = int(os.environ.get("TURN_WALL_SECONDS", DEFAULT_TURN_WALL_SECONDS))
    except ValueError:
        return DEFAULT_TURN_WALL_SECONDS
    return max(0, value)


def format_minutes(seconds: float) -> str:
    """``seconds`` as the minute figure quoted back to the user."""
    minutes = seconds / 60.0
    return f"{minutes:.0f}" if minutes >= 1 else f"{minutes:.2f}"


@dataclass(frozen=True)
class TurnResult:
    """What one agent turn produced. ``n_calls``/``cost`` are per-turn deltas."""

    finish_reason: str
    reply: str
    n_calls: int
    cost: float
    #: how long this turn took, start to finish
    wall_seconds: float = 0.0


@dataclass(frozen=True)
class Steering:
    """A user message delivered while a turn was already running."""

    message_id: str
    content: str


def _text_of(content: Any) -> str:
    """Flatten a message body (str, or multimodal parts) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "".join(parts)
    return "" if content is None else str(content)


def _is_observation(message: dict) -> bool:
    """True for messages produced by ``format_observation_messages``."""
    extra = message.get("extra") or {}
    if message.get("role") == "tool":
        return True
    return message.get("role") == "user" and (
        "raw_output" in extra or extra.get("context_truncated") is True
    )


def is_question(text: str) -> bool:
    """Simple heuristic: the agent is asking the user something."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    last_line = stripped.splitlines()[-1].strip()
    return last_line.endswith("?")


def assistant_message_from_format_error(exc: FormatError) -> dict | None:
    """The model's text-only response, if that is why parsing failed.

    ``FormatError`` is raised by the model wrapper when a response carries no
    tool call. Two shapes are handled: a wrapper that puts the assistant
    message in ``exc.messages`` directly, and mini-swe's stock ``LitellmModel``,
    which puts only the format-error observation there and stashes the raw
    provider response under ``messages[0]["extra"]["response"]``.

    Returns ``None`` when the response really is malformed (no text at all, or
    text alongside actions), which the caller must treat as a format error.
    """
    messages = list(getattr(exc, "messages", ()) or ())
    for message in messages:
        if message.get("role") != "assistant":
            continue
        extra = message.get("extra") or {}
        content = _text_of(message.get("content"))
        if content.strip() and not extra.get("actions"):
            return {"role": "assistant", "content": content, "extra": dict(extra)}
        return None

    if not messages:
        return None
    error_extra = messages[0].get("extra") or {}
    response = error_extra.get("response")
    if not isinstance(response, dict):
        return None
    choices = response.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    raw = choices[0].get("message")
    if not isinstance(raw, dict):
        return None
    if raw.get("tool_calls"):
        return None
    content = _text_of(raw.get("content"))
    if not content.strip():
        return None
    return {
        "role": "assistant",
        "content": content,
        "extra": {"cost": error_extra.get("cost", 0.0)},
    }


def _submitted_output(exc: Submitted) -> dict[str, Any]:
    """The observation a ``Submitted`` marker leaves in place of a command run."""
    return {
        "output": _text_of(exc.messages[0].get("content")) if exc.messages else "",
        "returncode": 0,
        "exception_info": "",
    }


class _EmittingEnvironment:
    """The session environment, with ``tool_call``/``tool_result`` around it.

    Every attribute other than ``execute`` is delegated to the real
    environment — including ``serialize`` and ``get_template_vars``, which
    ``DefaultAgent`` calls and which must return the real environment's data,
    not the proxy's.

    Emission lives here rather than in ``execute_actions`` because GT's runtime
    hook replaces ``execute_actions`` wholesale and drives ``env.execute``
    directly. The environment is the one seam both paths share.
    """

    __slots__ = ("_agent", "_env")

    def __init__(self, env: Any, agent: ConversationalAgent) -> None:
        object.__setattr__(self, "_env", env)
        object.__setattr__(self, "_agent", agent)

    @property
    def wrapped(self) -> Any:
        """The real environment, for callers that must bypass the proxy."""
        return self._env

    def __getattr__(self, name: str) -> Any:
        return getattr(self._env, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._env, name, value)

    def __repr__(self) -> str:
        return f"_EmittingEnvironment({self._env!r})"

    def execute(self, action: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        agent = self._agent
        command = (
            str(action.get("command", "")) if isinstance(action, dict) else str(action)
        )
        step = agent.current_step
        agent.emit_event(
            "tool_call",
            {"command": command, "step": step, "n_calls": agent.n_calls},
        )
        submitted: Submitted | None = None
        try:
            output = self._env.execute(action, *args, **kwargs)
        except Submitted as exc:
            # The result frame is emitted first and the marker propagates after,
            # or a submitting turn would leave a call with no matching result.
            submitted = exc
            output = _submitted_output(exc)
        if not isinstance(output, dict):  # pragma: no cover - defensive
            output = {"output": str(output), "returncode": 0, "exception_info": ""}
        agent.emit_event(
            "tool_result",
            {
                "command": command,
                "output": _text_of(output.get("output"))[:4000],
                "returncode": output.get("returncode", -1),
                "is_error": output.get("returncode", -1) != 0,
                "step": step,
            },
        )
        if submitted is not None:
            raise submitted
        return output


class ConversationalAgent(DefaultAgent):
    """A ``DefaultAgent`` whose transcript outlives a single task."""

    def __init__(
        self,
        model: Any,
        env: Any,
        *,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        config_class: type = AgentConfig,
        max_context_chars: int | None = None,
        wall_seconds: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model, env, config_class=config_class, **kwargs)
        # Set before the wrap: the proxy reads `_event_callback` and
        # `_current_step` off this instance on its very first call.
        self._event_callback = event_callback
        self._current_step = 0
        self.env = _EmittingEnvironment(env, self)
        self._steering_queue: queue.Queue[Steering] = queue.Queue()
        self._stop_event = threading.Event()
        #: set by the wall-clock watchdog when this turn ran out of time
        self._deadline_event = threading.Event()
        self._deadline_timer: threading.Timer | None = None
        self._wall_seconds = (
            max(0, int(wall_seconds))
            if wall_seconds is not None
            else turn_wall_seconds()
        )
        self._session_started = False
        self._turn_id: str | None = None
        self._turn_start_calls = 0
        #: turn whose failure already produced an ``agent_error`` event
        self.last_error_turn_id: str | None = None
        self._max_context_chars = (
            max_context_chars
            if max_context_chars is not None
            else int(os.environ.get("MAX_CONTEXT_CHARS", DEFAULT_MAX_CONTEXT_CHARS))
        )

    # -- session lifecycle ----------------------------------------------------

    @property
    def session_started(self) -> bool:
        return self._session_started

    def begin_session(self, **template_vars: Any) -> None:
        """Seed the transcript with the system message and the session brief."""
        if self._session_started:
            return
        self.extra_template_vars |= template_vars
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
        self._session_started = True

    def restore(self, messages: list[dict], **template_vars: Any) -> None:
        """Rebuild the agent's memory from a persisted transcript."""
        self.extra_template_vars |= template_vars
        self.messages = [dict(m) for m in messages]
        self._session_started = bool(self.messages)

    # -- steering / stop ------------------------------------------------------

    def queue_steering(self, message_id: str, content: str) -> None:
        self._steering_queue.put(Steering(message_id=message_id, content=content))

    def request_stop(self) -> None:
        """End the turn at the next boundary, and kill the command in flight.

        Without the interrupt a stop is only honoured once the running command
        returns, so ``sleep 120`` keeps the user waiting two minutes for a
        button they already pressed. The killed command yields a
        returncode-137 observation and the loop reaches the boundary at once.
        """
        self._stop_event.set()
        self._interrupt_env()

    def _interrupt_env(self) -> None:
        """Kill whatever command is in flight, if the environment allows it."""
        interrupt = getattr(self.env, "interrupt", None)
        if not callable(interrupt):
            return
        try:
            interrupt()
        except Exception:  # noqa: BLE001 - ending a turn must never raise
            pass

    @property
    def wall_seconds(self) -> int:
        """This session's per-turn wall-clock budget. ``0`` means unbounded."""
        return self._wall_seconds

    @property
    def time_limit_reached(self) -> bool:
        return self._deadline_event.is_set()

    def _arm_deadline(self) -> None:
        """Start the watchdog that ends a turn that overruns its wall budget.

        A timer rather than a loop check alone, because the check only runs at
        a step boundary: a single ``sleep 600`` would otherwise blow the budget
        by ten minutes before anyone looked. The watchdog interrupts the
        command exactly the way ``request_stop`` does, and the boundary check
        below then ends the turn.
        """
        self._deadline_event.clear()
        self._disarm_deadline()
        if self._wall_seconds <= 0:
            return
        timer = threading.Timer(self._wall_seconds, self._on_deadline)
        timer.daemon = True
        self._deadline_timer = timer
        timer.start()

    def _disarm_deadline(self) -> None:
        timer, self._deadline_timer = self._deadline_timer, None
        if timer is not None:
            timer.cancel()

    def _on_deadline(self) -> None:
        self._deadline_event.set()
        self._interrupt_env()

    def take_pending_steering(self) -> list[Steering]:
        """Messages that arrived after the last drain, for a follow-up turn."""
        items: list[Steering] = []
        while True:
            try:
                items.append(self._steering_queue.get_nowait())
            except queue.Empty:
                return items

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    # -- the turn loop --------------------------------------------------------

    def run_turn(self, user_text: str, *, turn_id: str) -> TurnResult:
        """Run one turn on the shared transcript and return how it ended."""
        self.begin_session()
        self._turn_id = turn_id
        self.n_consecutive_format_errors = 0
        self._turn_start_calls = self.n_calls
        start_cost = self.cost
        started = time.monotonic()
        budget = max(0, int(self.config.step_limit))
        self._arm_deadline()

        self.add_messages(self.model.format_message(role="user", content=user_text))

        finish_reason = ""
        reply = ""
        while True:
            if self._stop_event.is_set():
                finish_reason, reply = STOPPED, STOPPED_REPLY
                break
            self._drain_steering()
            if budget and (self.n_calls - self._turn_start_calls) >= budget:
                finish_reason, reply = STEP_LIMIT, self._step_limit_reply()
                break
            if self._out_of_time(started):
                finish_reason, reply = TIME_LIMIT, self._time_limit_reply()
                break

            self._truncate_context()
            try:
                self.step()
                self.n_consecutive_format_errors = 0
            except FormatError as exc:
                outcome = self._handle_format_error(exc)
                if outcome is not None:
                    finish_reason, reply = outcome
                    break
            except Submitted as exc:
                finish_reason = SUBMITTED
                reply = _text_of(exc.messages[0].get("content")) if exc.messages else ""
                break
            except LimitsExceeded:
                finish_reason, reply = STEP_LIMIT, self._step_limit_reply()
                break
            except InterruptAgentFlow as exc:
                self.add_messages(*exc.messages)
            except Exception as exc:
                self.last_error_turn_id = turn_id
                self._emit(
                    "agent_error", {"error": f"{type(exc).__name__}: {exc}"}
                )
                # The turn dies here; the watchdog must not outlive it and
                # interrupt whatever runs next.
                self._disarm_deadline()
                raise
            finally:
                self._save_quietly()

        if finish_reason in {STOPPED, STEP_LIMIT, TIME_LIMIT, SUBMITTED, ERROR} and reply:
            self.add_messages(
                self.model.format_message(role="assistant", content=reply)
            )
        self._save_quietly()
        self._turn_id = None
        self._disarm_deadline()
        self._deadline_event.clear()
        # Cleared only now, so a stop requested before the worker thread got
        # going (or during the turn) is honoured exactly once.
        self._stop_event.clear()
        return TurnResult(
            finish_reason=finish_reason,
            reply=reply,
            n_calls=self.n_calls - self._turn_start_calls,
            cost=round(self.cost - start_cost, 10),
            wall_seconds=round(time.monotonic() - started, 3),
        )

    def _out_of_time(self, started: float) -> bool:
        """True once this turn has spent its wall-clock budget.

        Both signals matter: the watchdog fires while a command is running,
        and the elapsed check catches a turn that spent its budget on model
        latency alone, where no command was ever in flight to interrupt.
        """
        if self._wall_seconds <= 0:
            return False
        if self._deadline_event.is_set():
            return True
        return (time.monotonic() - started) >= self._wall_seconds

    def _handle_format_error(self, exc: FormatError) -> tuple[str, str] | None:
        """Text-only response -> the turn's reply; otherwise a real format error."""
        # The call was billed before parsing failed, so query() never charged it.
        self.cost += (exc.messages[0].get("extra", {}) or {}).get("cost", 0.0)

        assistant = assistant_message_from_format_error(exc)
        if assistant is not None:
            self.add_messages(assistant)
            content = _text_of(assistant.get("content"))
            # The reply is a model call like any other, but it never reached
            # query() — FormatError is how a text-only response arrives. Emit
            # the frame query() would have emitted, so a UI counting assistant
            # frames matches turn_finished.n_calls instead of trailing it by
            # one. n_calls was already incremented by DefaultAgent.query()
            # before the model raised, and the cost was just added above.
            self._current_step = self.n_calls
            self._emit(
                "assistant",
                {
                    "content": content,
                    "actions": [],
                    "step": self._current_step,
                    "n_calls": self.n_calls,
                    "cost": self.cost,
                    "is_reply": True,
                },
            )
            return (QUESTION if is_question(content) else REPLY), content

        self.n_consecutive_format_errors += 1
        self.add_messages(*exc.messages)
        limit = self.config.max_consecutive_format_errors
        if 0 < limit <= self.n_consecutive_format_errors:
            return ERROR, FORMAT_ERROR_REPLY
        return None

    def _drain_steering(self) -> None:
        while True:
            try:
                item = self._steering_queue.get_nowait()
            except queue.Empty:
                return
            self.add_messages(
                self.model.format_message(role="user", content=item.content)
            )
            self._emit(
                "steering",
                {"message_id": item.message_id, "content": item.content},
            )

    def _step_limit_reply(self) -> str:
        return STEP_LIMIT_REPLY.format(thought=self._last_thought())

    def _time_limit_reply(self) -> str:
        return TIME_LIMIT_REPLY.format(
            minutes=format_minutes(self._wall_seconds), thought=self._last_thought()
        )

    def _last_thought(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") != "assistant":
                continue
            text = _text_of(message.get("content")).strip()
            if text:
                return text[:500]
        return "no progress recorded yet"

    def _save_quietly(self) -> None:
        try:
            self.save(self.config.output_path)
        except Exception:  # noqa: BLE001 - trajectory writing must never fail a turn
            pass

    # -- context bounding -----------------------------------------------------

    def _truncate_context(self) -> None:
        """Collapse the oldest tool observations once the transcript is too big."""
        limit = self._max_context_chars
        if limit <= 0:
            return
        total = sum(len(_text_of(m.get("content"))) for m in self.messages)
        if total <= limit:
            return

        indices = [i for i, m in enumerate(self.messages) if _is_observation(m)]
        for index in indices[: max(0, len(indices) - KEEP_RECENT_OBSERVATIONS)]:
            if total <= limit:
                return
            message = self.messages[index]
            extra = dict(message.get("extra") or {})
            if extra.get("context_truncated"):
                continue
            size = len(_text_of(message.get("content")))
            if size < MIN_TRUNCATABLE_CHARS:
                continue
            placeholder = f"[truncated {size} chars]"
            extra["context_truncated"] = True
            if "raw_output" in extra:
                extra["raw_output"] = ""
            self.messages[index] = {
                **message,
                "content": placeholder,
                "extra": extra,
            }
            total -= size - len(placeholder)

    # -- DefaultAgent seams ---------------------------------------------------

    def query(self) -> dict:
        """Per-turn step budget, plus an ``assistant`` event for every response."""
        limit, cost_limit = self.config.step_limit, self.config.cost_limit
        try:
            # DefaultAgent.query() compares against the cumulative n_calls; the
            # budget here is per turn, so shift it. run_turn() owns the real
            # check — this only keeps the base class from firing early.
            self.config.step_limit = 0 if limit <= 0 else limit + self._turn_start_calls
            self.config.cost_limit = 0
            message = super().query()
        finally:
            self.config.step_limit = limit
            self.config.cost_limit = cost_limit

        self._current_step = self.n_calls
        self._emit(
            "assistant",
            {
                "content": _text_of(message.get("content")),
                "actions": [
                    str(a.get("command", a))
                    for a in (message.get("extra", {}) or {}).get("actions", [])
                ],
                "step": self._current_step,
                "n_calls": self.n_calls,
                "cost": self.cost,
            },
        )
        return message

    def execute_actions(self, message: dict) -> list[dict]:
        """Run each action against the environment and keep the observations.

        The ``tool_call``/``tool_result`` frames are *not* emitted here — they
        come from :class:`_EmittingEnvironment`, which fires for GT's own
        ``execute_actions`` replacement too.

        ``Submitted`` (the legacy ``COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT``
        marker) is caught so the observation messages are still appended before
        it propagates — otherwise the transcript would end on an assistant tool
        call with no matching result, which no provider will accept on the next
        turn.
        """
        actions = (message.get("extra", {}) or {}).get("actions", [])
        outputs: list[dict] = []
        submitted: Submitted | None = None
        for action in actions:
            try:
                output = self.env.execute(action)
            except Submitted as exc:
                submitted = exc
                output = _submitted_output(exc)
            outputs.append(output)
            if submitted is not None:
                break

        added = self.add_messages(
            *self.model.format_observation_messages(
                message, outputs, self.get_template_vars()
            )
        )
        if submitted is not None:
            raise submitted
        return added

    # -- events ---------------------------------------------------------------

    @property
    def current_step(self) -> int:
        """Step index of the assistant message currently being executed."""
        return self._current_step

    def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish one frame. Public so :class:`_EmittingEnvironment` can use it."""
        self._emit(event_type, data)

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self._event_callback is None:
            return
        self._event_callback(
            {
                "type": event_type,
                "timestamp": time.time(),
                "data": {"turn_id": self._turn_id, **data},
            }
        )
