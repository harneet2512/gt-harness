"""Dynamic provider admission at the final, model-visible transport seam."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


class ProviderContextWindowUnavailable(RuntimeError):
    """The paid route has no authoritative model-window metadata."""

    code = "GT_PROVIDER_CONTEXT_WINDOW_UNAVAILABLE"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        request_tokens: int,
        request_bytes: int,
        context_window_tokens: int,
        reserved_output_tokens: int,
        metadata_source: str,
    ) -> None:
        self.request_tokens = int(request_tokens)
        self.request_bytes = int(request_bytes)
        self.context_window_tokens = int(context_window_tokens)
        self.reserved_output_tokens = int(reserved_output_tokens)
        self.input_budget_tokens = max(
            0, self.context_window_tokens - self.reserved_output_tokens
        )
        self.metadata_source = str(metadata_source or "")
        super().__init__(message)

    def to_dict(self) -> dict[str, int | str]:
        return {
            "request_tokens": self.request_tokens,
            "request_bytes": self.request_bytes,
            "context_window_tokens": self.context_window_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "input_budget_tokens": self.input_budget_tokens,
            "metadata_source": self.metadata_source,
        }


@dataclass(frozen=True)
class ProviderAdmission:
    """Auditable result of sizing one exact, provider-prepared request."""

    request_tokens: int
    request_bytes: int
    context_window_tokens: int
    reserved_output_tokens: int
    input_budget_tokens: int
    metadata_source: str

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


class ProviderRequestTooLarge(RuntimeError):
    """Deterministic local refusal; this exception must never be retried."""

    code = "GT_PROVIDER_REQUEST_TOO_LARGE"
    retryable = False

    def __init__(self, admission: ProviderAdmission):
        self.admission = admission
        for field, value in admission.to_dict().items():
            setattr(self, field, value)
        super().__init__(
            "provider request refused locally: "
            f"{admission.request_tokens} tokens exceeds the "
            f"{admission.input_budget_tokens} token input budget "
            f"({admission.context_window_tokens} context tokens minus "
            f"{admission.reserved_output_tokens} reserved output tokens; "
            f"source={admission.metadata_source})"
        )


def build_provider_request_envelope(
    *,
    messages: Any,
    model: str,
    model_kwargs: Mapping[str, Any] | None = None,
    tools: Any = None,
    call_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the final logical wire request with effective kwargs flattened."""

    payload: dict[str, Any] = {
        "model": str(model or ""),
        "messages": messages,
    }
    if tools is not None:
        payload["tools"] = tools
    payload.update(dict(model_kwargs or {}))
    payload.update(dict(call_kwargs or {}))
    return payload


def provider_request_bytes(payload: Mapping[str, Any]) -> int:
    """Return the exact canonical UTF-8 envelope size retained for audit."""

    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("provider request is not JSON serializable") from exc
    return len(encoded)


def provider_request_tokens(payload: Mapping[str, Any]) -> int:
    """Count tokens with LiteLLM's tokenizer for the selected provider model."""

    from litellm import token_counter

    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("provider request messages must be a list")
    value = token_counter(
        model=str(payload.get("model") or ""),
        messages=messages,
        tools=payload.get("tools"),
        tool_choice=payload.get("tool_choice"),
    )
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("provider tokenizer returned an invalid count")
    return value


def enforce_provider_request_limit(
    payload: Mapping[str, Any],
    *,
    context_window_tokens: int,
    reserved_output_tokens: int,
    metadata_source: str = "",
    token_counter: Callable[[Mapping[str, Any]], int] | None = None,
) -> ProviderAdmission:
    """Admit an exact prepared request against its live provider-model window."""

    context_window = int(context_window_tokens)
    reserved_output = int(reserved_output_tokens)
    source = str(metadata_source or "").strip()
    counter = token_counter or provider_request_tokens
    request_tokens = int(counter(payload))
    request_bytes = provider_request_bytes(payload)
    if request_tokens < 0:
        raise ValueError("provider request token count cannot be negative")
    if context_window < 1 or not source:
        raise ProviderContextWindowUnavailable(
            "authoritative provider context window is unavailable",
            request_tokens=request_tokens,
            request_bytes=request_bytes,
            context_window_tokens=context_window,
            reserved_output_tokens=reserved_output,
            metadata_source=source,
        )
    if reserved_output < 1 or reserved_output >= context_window:
        raise ProviderContextWindowUnavailable(
            "provider output reservation is invalid for the context window",
            request_tokens=request_tokens,
            request_bytes=request_bytes,
            context_window_tokens=context_window,
            reserved_output_tokens=reserved_output,
            metadata_source=source,
        )
    admission = ProviderAdmission(
        request_tokens=request_tokens,
        request_bytes=request_bytes,
        context_window_tokens=context_window,
        reserved_output_tokens=reserved_output,
        input_budget_tokens=context_window - reserved_output,
        metadata_source=source,
    )
    if admission.request_tokens > admission.input_budget_tokens:
        raise ProviderRequestTooLarge(admission)
    return admission


def render_and_admit_provider_request(
    *,
    messages: Sequence[Mapping[str, Any]],
    render_messages: Callable[
        [list[dict[str, Any]]], Sequence[Mapping[str, Any]]
    ],
    model: str,
    context_window_tokens: int,
    reserved_output_tokens: int,
    metadata_source: str,
    model_kwargs: Mapping[str, Any] | None = None,
    tools: Any = None,
    call_kwargs: Mapping[str, Any] | None = None,
    token_counter: Callable[[Mapping[str, Any]], int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], ProviderAdmission]:
    """Render first, then admit the exact envelope that can reach transport.

    The renderer receives a detached message list so an admission failure cannot
    partially mutate durable history. The returned messages are the only message
    value represented by ``payload`` and ``admission``.
    """

    detached = copy.deepcopy([dict(message) for message in messages])
    rendered_value = render_messages(detached)
    if not isinstance(rendered_value, Sequence) or isinstance(
        rendered_value, (str, bytes)
    ):
        raise ValueError("provider renderer must return a message sequence")
    rendered: list[dict[str, Any]] = []
    for message in rendered_value:
        if not isinstance(message, Mapping):
            raise ValueError("provider renderer returned a non-object message")
        rendered.append(dict(message))
    payload = build_provider_request_envelope(
        messages=rendered,
        model=model,
        model_kwargs=model_kwargs,
        tools=tools,
        call_kwargs=call_kwargs,
    )
    admission = enforce_provider_request_limit(
        payload,
        context_window_tokens=context_window_tokens,
        reserved_output_tokens=reserved_output_tokens,
        metadata_source=metadata_source,
        token_counter=token_counter,
    )
    return rendered, payload, admission


__all__ = [
    "ProviderAdmission",
    "ProviderContextWindowUnavailable",
    "ProviderRequestTooLarge",
    "build_provider_request_envelope",
    "enforce_provider_request_limit",
    "provider_request_bytes",
    "provider_request_tokens",
    "render_and_admit_provider_request",
]
