"""Claude through the official ``anthropic`` SDK (spec 3.1, item 1).

- Forced tool: ``tool_choice={"type": "tool", "name": ...}``. Some models reject forced tools
  with a 400 (verified in the tool-use docs on 2026-10-09: Opus 5.5 and Sonnet 5.5 do; Haiku 5.5
  does not). For those models the provider remembers the refusal and sends
  ``tool_choice={"type": "auto"}`` with ``strict: true`` instead.
- Cache blocks: the system prompt is sent as a text block with ``cache_control``.
- No ``temperature``: SDK 1.x removed it and current models reject non-default values.
- The SDK's own retries are off (``max_retries=0``); ``HttpProviderBase`` retries.

The SDK is imported when the provider is built, never at module import, so ``/health`` and
offline mode do not load it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from core.llm.base import Capabilities, LLMResponse, Tier, ToolSpec
from core.llm.http_base import (
    CircuitBreaker,
    HttpProviderBase,
    HttpStatusError,
    TransportError,
    parse_retry_after,
)
from core.llm.openai_compat import EgressHostBlockedError

FORCED_REJECTION_WORDS = ("tool_choice", "forced", "tool use")
# Error ``type`` -> HTTP status, from https://docs.anthropic.com/en/api/errors (2026-10-09).
ERROR_TYPE_STATUS = {
    "invalid_request_error": 400, "authentication_error": 401, "billing_error": 402,
    "permission_error": 403, "not_found_error": 404, "request_too_large": 413,
    "rate_limit_error": 429, "api_error": 500, "timeout_error": 504, "overloaded_error": 529,
}


def split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Anthropic takes the system prompt separately from the turns."""
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    turns = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]
    return system, turns


class AnthropicProvider(HttpProviderBase):
    """Messages API client."""

    name = "anthropic"

    def __init__(
        self,
        *,
        models: dict[Tier, str],
        api_key: str,
        api_base: str = "https://api.anthropic.com",
        timeout_s: float = 25.0,
        max_retries: int = 2,
        breaker: CircuitBreaker | None = None,
        allowed_hosts: frozenset[str] | None = None,
        transport: Any = None,
        sleep=None,  # noqa: ANN001
    ) -> None:
        kwargs = {"sleep": sleep} if sleep is not None else {}
        super().__init__(
            models=models, api_key=api_key, api_base=api_base, timeout_s=timeout_s,
            max_retries=max_retries, breaker=breaker, **kwargs,
        )
        import anthropic
        import httpx2

        # The SDK logs whole request bodies at DEBUG (also via ANTHROPIC_LOG=debug).
        for noisy in ("anthropic", "httpx2", "httpcore2"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        self._sdk = anthropic
        self._unforced: set[str] = set()

        def guard(request: httpx2.Request) -> None:
            host = (request.url.host or "").lower()
            if allowed_hosts is not None and host not in allowed_hosts:
                raise EgressHostBlockedError(
                    "Blocked: this AI service is not on the list of allowed hosts.", self.name
                )

        http_client = httpx2.Client(
            transport=transport,
            timeout=httpx2.Timeout(timeout_s, connect=min(10.0, timeout_s)),
            event_hooks={"request": [guard]},
        )
        self._client = anthropic.Anthropic(
            api_key=self._api_key,
            base_url=self.api_base,
            timeout=timeout_s,
            max_retries=0,
            http_client=http_client,
        )

    capabilities = Capabilities(
        forced_tool=True, tool_loop=True, streaming=True, cache_markers=True,
        max_context=1_000_000,
    )

    # ---------------------------------------------------------------- wire mapping

    def _request(self, model: str, messages: list[dict[str, str]], max_tokens: int,
                 tool: ToolSpec | None) -> dict[str, Any]:
        system, turns = split_system(messages)
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": turns}
        if system:
            body["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if tool is not None:
            definition: dict[str, Any] = {
                "name": tool.name, "description": tool.description, "input_schema": tool.schema,
            }
            if model in self._unforced:
                definition["strict"] = True
                body["tool_choice"] = {"type": "auto"}
                if turns and turns[-1]["role"] == "user":
                    turns[-1] = {
                        "role": "user",
                        "content": f"{turns[-1]['content']}\n\nAnswer by calling the "
                        f"`{tool.name}` tool.",
                    }
            else:
                body["tool_choice"] = {"type": "tool", "name": tool.name}
            body["tools"] = [definition]
        return body

    def _call(self, body: dict[str, Any]) -> Any:
        sdk = self._sdk
        try:
            return self._client.messages.create(**body)
        except sdk.APIStatusError as exc:
            raise HttpStatusError(
                exc.status_code,
                _error_text(exc),
                parse_retry_after(exc.response.headers.get("retry-after")),
            ) from None
        except sdk.APIConnectionError as exc:  # includes APITimeoutError
            if isinstance(exc.__cause__, EgressHostBlockedError):
                raise exc.__cause__ from None
            raise TransportError(type(exc).__name__) from exc

    def _send(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        tool: ToolSpec | None,
        max_tokens: int,
    ) -> LLMResponse:
        del temperature  # not accepted by the API for current models
        try:
            message = self._call(self._request(model, messages, max_tokens, tool))
        except HttpStatusError as exc:
            forced_refused = (
                tool is not None
                and model not in self._unforced
                and exc.status == 400
                and any(word in exc.body.lower() for word in FORCED_REJECTION_WORDS)
            )
            if not forced_refused:
                raise
            self._unforced.add(model)
            message = self._call(self._request(model, messages, max_tokens, tool))
        text_parts: list[str] = []
        args: dict[str, Any] | None = None
        for block in message.content:
            kind = getattr(block, "type", "")
            if kind == "text":
                text_parts.append(block.text)
            elif kind == "tool_use" and tool is not None and block.name == tool.name:
                args = dict(block.input) if isinstance(block.input, dict) else None
        usage = message.usage
        return LLMResponse(
            text="".join(text_parts),
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0)
            + int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
            tool_args=args,
            model=str(getattr(message, "model", "") or model),
        )

    def _send_stream(
        self, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> Iterator[str]:
        del temperature
        body = {**self._request(model, messages, max_tokens, None), "stream": True}
        sdk = self._sdk
        stream = self._call(body)
        try:
            for event in stream:
                if getattr(event, "type", "") != "content_block_delta":
                    continue
                delta = event.delta
                if getattr(delta, "type", "") == "text_delta":
                    yield delta.text
        except sdk.APIStatusError as exc:
            raise HttpStatusError(_stream_status(exc), _error_text(exc)) from None
        except sdk.APIConnectionError as exc:
            raise TransportError(type(exc).__name__) from exc

    def list_models(self) -> list[str]:
        sdk = self._sdk
        try:
            return [model.id for model in self._client.models.list(limit=100)]
        except sdk.APIStatusError as exc:
            raise HttpStatusError(exc.status_code, _error_text(exc)) from None
        except sdk.APIConnectionError as exc:
            raise TransportError(type(exc).__name__) from exc


def _stream_status(exc: Any) -> int:
    """HTTP status of a stream failure; a mid-stream ``event: error`` arrives with 200."""
    if exc.status_code >= 400:
        return int(exc.status_code)
    body = getattr(exc, "body", None)
    kind = (body.get("error") or {}).get("type", "") if isinstance(body, dict) else ""
    return ERROR_TYPE_STATUS.get(kind, 500)


def _error_text(exc: Any) -> str:
    """The provider's error message (used for classification only, never logged)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error") or {}
        return f"{error.get('type', '')} {error.get('message', '')}"
    return str(getattr(exc, "message", "") or "")
