from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent.core.skill import ToolDefinition


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
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    thinking: str | None = None


@dataclass
class LLMResponse:
    text: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None


class OpenAIProvider:
    """OpenAI-compatible LLM client (OpenAI, DeepSeek, vLLM, Ollama, etc.)."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=300.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._format_messages(messages),
        }

        if tools:
            payload["tools"] = self._format_tools(tools)

        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_response(data)

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

    def _format_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
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

        return LLMResponse(
            text=text,
            thinking=thinking,
            tool_calls=tool_calls,
            usage=usage,
        )
