from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .config import ModelSection

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class Message:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    thinking: str | None = None


@dataclass
class ModelResponse:
    text: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None


class ModelProvider:

    def __init__(self, base_url: str, api_key: str, model_name: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=300.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": self._format_messages(messages),
        }

        if tools:
            payload["tools"] = self._format_tools(tools)

        try:
            resp = await self._http.post("/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("LLM HTTP %s: %s", e.response.status_code, body)
            raise
        except Exception:
            logger.exception("LLM request failed")
            raise

        return self._parse_response(resp.json())

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            m: dict[str, Any] = {"role": msg.role}
            if msg.content:
                m["content"] = msg.content
            if msg.thinking:
                m["reasoning_content"] = msg.thinking
            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                m["tool_call_id"] = msg.tool_call_id
            formatted.append(m)
        return formatted

    def _format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"type": "function", "function": t} for t in tools]

    def _parse_response(self, data: dict[str, Any]) -> ModelResponse:
        choice = data["choices"][0]
        message = choice["message"]

        text: str | None = message.get("content")
        thinking: str | None = None
        tool_calls: list[ToolCall] | None = None

        # Some providers return reasoning/thinking in a separate field
        if "reasoning_content" in message:
            thinking = message["reasoning_content"]

        if message.get("tool_calls"):
            tool_calls = []
            for tc in message["tool_calls"]:
                fn = tc["function"]
                args = fn.get("arguments", "{}")
                tool_calls.append(
                    ToolCall(
                        id=tc["id"],
                        name=fn["name"],
                        arguments=json.loads(args) if isinstance(args, str) else args,
                    )
                )

        usage: Usage | None = None
        if "usage" in data:
            u = data["usage"]
            usage = Usage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
            )

        return ModelResponse(
            text=text,
            thinking=thinking,
            tool_calls=tool_calls,
            usage=usage,
        )


class ModelRegistry:

    def __init__(self, config: ModelSection) -> None:
        self._config = config
        self._providers: dict[str, ModelProvider] = {}

    async def close(self) -> None:
        for provider in self._providers.values():
            await provider.close()
        self._providers.clear()

    def get(self, scene: str) -> ModelProvider:
        model_ref = getattr(self._config, scene, None) or self._config.main
        return self._resolve(model_ref)

    def _resolve(self, ref: str) -> ModelProvider:
        if ref not in self._providers:
            provider_name, model_name = ref.split(":")
            provider = self._config.providers[provider_name]
            model = provider.models[model_name]
            self._providers[ref] = ModelProvider(
                base_url=provider.base_url,
                api_key=provider.api_key,
                model_name=model.name,
            )
        return self._providers[ref]
