"""OpenAI-compatible chat completions over ``httpx`` (Grok by default; spec 3.1, item 2).

Forced tool through ``tools`` + ``tool_choice``; the answer is read from
``choices[0].message.tool_calls[0].function.arguments``. If the provider rejects forced tools
the adapter falls back to ``response_format`` (JSON schema); if that is rejected too,
``forced_tool`` turns off and the chain moves on. No cache markers are sent.
Request and response shapes: https://docs.x.ai/developers/rest-api-reference/inference/chat-completions
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from core.llm.base import (
    Capabilities,
    ForcedToolUnsupportedError,
    LLMResponse,
    ProviderRejectedError,
    Tier,
    ToolSpec,
)
from core.llm.http_base import (
    CircuitBreaker,
    HttpProviderBase,
    HttpStatusError,
    TransportError,
    parse_retry_after,
)

TOOL_REJECTION_WORDS = ("tool", "function", "response_format", "json_schema", "schema")


class EgressHostBlockedError(ProviderRejectedError):
    """A request tried to reach a host that is not in ``EGRESS_ALLOWED_HOSTS``."""


def host_guard(allowed: frozenset[str] | None, provider: str):  # noqa: ANN201
    """An ``httpx`` request hook that refuses hosts outside the allowlist."""

    def check(request: httpx.Request) -> None:
        host = (request.url.host or "").lower()
        if allowed is not None and host not in allowed:
            raise EgressHostBlockedError(
                "Blocked: this AI service is not on the list of allowed hosts.", provider
            )

    return check


def raise_for_status(response: httpx.Response) -> None:
    if response.status_code >= 400:
        response.read()
        raise HttpStatusError(
            response.status_code,
            response.text[:2000],
            parse_retry_after(response.headers.get("retry-after")),
        )


class OpenAICompatProvider(HttpProviderBase):
    """``/chat/completions`` client. ``name`` is ``grok`` (xAI) or ``custom``."""

    def __init__(
        self,
        *,
        name: str = "grok",
        models: dict[Tier, str],
        api_key: str,
        api_base: str,
        timeout_s: float = 25.0,
        max_retries: int = 2,
        breaker: CircuitBreaker | None = None,
        allowed_hosts: frozenset[str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=None,  # noqa: ANN001
    ) -> None:
        kwargs = {"sleep": sleep} if sleep is not None else {}
        super().__init__(
            models=models, api_key=api_key, api_base=api_base, timeout_s=timeout_s,
            max_retries=max_retries, breaker=breaker, **kwargs,
        )
        self.name = name
        # xAI deprecated ``max_tokens``; generic OpenAI-compatible servers still expect it.
        self._limit_field = "max_completion_tokens" if name == "grok" else "max_tokens"
        self._forced = True
        self._schema_format = True
        self._client = httpx.Client(
            base_url=self.api_base,
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
            headers={"Authorization": f"Bearer {self._api_key}"},
            transport=transport,
            event_hooks={"request": [host_guard(allowed_hosts, name)]},
        )

    @property
    def capabilities(self) -> Capabilities:  # type: ignore[override]
        return Capabilities(
            forced_tool=self._forced or self._schema_format,
            tool_loop=True,
            streaming=True,
            cache_markers=False,
        )

    # ---------------------------------------------------------------- wire mapping

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise TransportError("timeout") from exc
        except httpx.TransportError as exc:
            raise TransportError("connection") from exc
        raise_for_status(response)
        return response.json()

    def _send(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        tool: ToolSpec | None,
        max_tokens: int,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            self._limit_field: max_tokens,
        }
        if tool is None:
            return self._parse(self._post(body), None)
        if self._forced:
            forced = {
                **body,
                "tools": [{"type": "function", "function": {
                    "name": tool.name, "description": tool.description,
                    "parameters": tool.schema,
                }}],
                "tool_choice": {"type": "function", "function": {"name": tool.name}},
            }
            try:
                return self._parse(self._post(forced), tool)
            except HttpStatusError as exc:
                if not _rejects_tools(exc):
                    raise
                self._forced = False
        if self._schema_format:
            schema_body = {
                **body,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": tool.name, "schema": tool.schema, "strict": True,
                }},
            }
            try:
                return self._parse(self._post(schema_body), tool, from_content=True)
            except HttpStatusError as exc:
                if not _rejects_tools(exc):
                    raise
                self._schema_format = False
        raise ForcedToolUnsupportedError(
            "This AI service does not support structured answers.", self.name
        )

    @staticmethod
    def _parse(
        data: dict[str, Any], tool: ToolSpec | None, from_content: bool = False
    ) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0].get("message") or {}
        usage = data.get("usage") or {}
        text = choice.get("content") or ""
        args: dict[str, Any] | None = None
        if tool is not None:
            raw = None
            calls = choice.get("tool_calls") or []
            if calls and not from_content:
                raw = (calls[0].get("function") or {}).get("arguments")
            elif text:
                raw = text
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    args = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    args = None
            elif isinstance(raw, dict):
                args = raw
        return LLMResponse(
            text=text,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            tool_args=args,
            model=str(data.get("model") or ""),
        )

    def _send_stream(
        self, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> Iterator[str]:
        body = {"model": model, "messages": messages, "temperature": temperature,
                self._limit_field: max_tokens, "stream": True}
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as response:
                raise_for_status(response)
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = ((event.get("choices") or [{}])[0].get("delta") or {})
                    if delta.get("content"):
                        yield str(delta["content"])
        except httpx.TimeoutException as exc:
            raise TransportError("timeout") from exc
        except httpx.TransportError as exc:
            raise TransportError("connection") from exc

    def list_models(self) -> list[str]:
        try:
            response = self._client.get("/models")
        except httpx.HTTPError as exc:
            raise TransportError("connection") from exc
        raise_for_status(response)
        return [str(item.get("id")) for item in response.json().get("data", []) if item.get("id")]


def _rejects_tools(exc: HttpStatusError) -> bool:
    body = exc.body.lower()
    return exc.status in (400, 422) and any(word in body for word in TOOL_REJECTION_WORDS)
