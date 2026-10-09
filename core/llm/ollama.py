"""Ollama for ``local`` mode (spec 3.1, item 3): ``/api/chat`` on a private address.

Structured answers use Ollama's ``format`` field with the tool's JSON schema.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from core.llm.base import Capabilities, LLMResponse, Tier, ToolSpec
from core.llm.http_base import CircuitBreaker, HttpProviderBase, TransportError
from core.llm.openai_compat import raise_for_status


class OllamaProvider(HttpProviderBase):
    """Local model server; nothing leaves the private network."""

    name = "ollama"
    external = False
    capabilities = Capabilities(forced_tool=True, tool_loop=False, streaming=True)

    def __init__(
        self,
        *,
        models: dict[Tier, str],
        api_base: str,
        timeout_s: float = 25.0,
        max_retries: int = 1,
        breaker: CircuitBreaker | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            models=models, api_key="", api_base=api_base, timeout_s=timeout_s,
            max_retries=max_retries, breaker=breaker,
        )
        self._client = httpx.Client(base_url=self.api_base, timeout=timeout_s, transport=transport)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise TransportError(type(exc).__name__) from exc
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
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if tool is not None:
            body["format"] = tool.schema
        data = self._post(body)
        text = str((data.get("message") or {}).get("content") or "")
        args = None
        if tool is not None:
            try:
                parsed = json.loads(text)
                args = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                args = None
        return LLMResponse(
            text=text,
            tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0),
            tool_args=args,
            model=str(data.get("model") or model),
        )

    def _send_stream(
        self, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> Iterator[str]:
        body = {"model": model, "messages": messages, "stream": True,
                "options": {"temperature": temperature, "num_predict": max_tokens}}
        try:
            with self._client.stream("POST", "/api/chat", json=body) as response:
                raise_for_status(response)
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    chunk = (event.get("message") or {}).get("content")
                    if chunk:
                        yield str(chunk)
                    if event.get("done"):
                        return
        except httpx.HTTPError as exc:
            raise TransportError(type(exc).__name__) from exc

    def list_models(self) -> list[str]:
        try:
            response = self._client.get("/api/tags")
        except httpx.HTTPError as exc:
            raise TransportError(type(exc).__name__) from exc
        raise_for_status(response)
        return [str(m.get("name")) for m in response.json().get("models", []) if m.get("name")]
