from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Coroutine

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
class SystemMessage:
    role: str = "system"
    content: str | None = None


@dataclass
class UserMessage:
    role: str = "user"
    content: str | None = None


@dataclass
class AssistantMessage:
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    thinking: str | None = None

@dataclass
class ToolResult:
    role: str = "tool"
    content: str | None = None
    tool_call_id: str | None = None
    name: str | None = None
    error: str = ""

@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ModelResponse:
    text: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None


@dataclass
class StreamChunk:
    text: str = ""
    thinking: str = ""


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
        messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": self._format_messages(messages),
            "stream": False,
        }

        if tools:
            payload["tools"] = self._format_tools(tools)

        try:
            resp = await self._http.post("/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("Model HTTP %s: %s", e.response.status_code, body)
            raise
        except Exception:
            logger.exception("Model request failed")
            raise

        return self._parse_response(resp.json())

    async def chat_stream(
        self,
        messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult],
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[StreamChunk], Coroutine[Any, Any, None]] | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": self._format_messages(messages),
            "stream": True,
        }

        if tools:
            payload["tools"] = self._format_tools(tools)

        response = ModelResponse()
        tool_calls_map: dict[str, dict[str, Any]] = {}
        tool_calls_order: list[str] = []

        try:
            async with self._http.stream("POST", "/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or line == "data: [DONE]":
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    chunk = self._parse_stream_chunk(data)
                    if chunk:
                        if chunk.text:
                            response.text = (response.text or "") + chunk.text
                        if chunk.thinking:
                            response.thinking = (response.thinking or "") + chunk.thinking
                        if on_chunk:
                            await on_chunk(chunk)
                    try:
                        obj = json.loads(data)
                        choices = obj.get("choices", [])
                        if choices:
                            finish = choices[0].get("finish_reason")
                            if finish:
                                response.finish_reason = finish
                            delta = choices[0].get("delta", {})
                            if "tool_calls" in delta:
                                for tc in delta["tool_calls"]:
                                    idx = tc.get("index", 0)
                                    key = f"idx_{idx}"
                                    tc_id = tc.get("id")
                                    if key not in tool_calls_map:
                                        tool_calls_map[key] = {"id": tc_id or key, "function": {"name": "", "arguments": ""}}
                                        tool_calls_order.append(key)
                                    if tc_id:
                                        tool_calls_map[key]["id"] = tc_id
                                    fn = tc.get("function", {})
                                    if fn:
                                        if "name" in fn:
                                            tool_calls_map[key]["function"]["name"] += fn["name"]
                                        if "arguments" in fn:
                                            tool_calls_map[key]["function"]["arguments"] += fn["arguments"]
                    except json.JSONDecodeError:
                        pass
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("Model HTTP %s: %s", e.response.status_code, body)
            raise
        except Exception:
            logger.exception("Model stream request failed")
            raise

        if tool_calls_map:
            response.tool_calls = []
            for tc_id in tool_calls_order:
                tc_data = tool_calls_map[tc_id]
                args_str = tc_data["function"]["arguments"]
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {}
                response.tool_calls.append(ToolCall(
                    id=tc_data["id"],
                    name=tc_data["function"]["name"],
                    arguments=args,
                ))

        return response

    def _parse_stream_chunk(self, data: str) -> StreamChunk | None:
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return None

        choices = obj.get("choices", [])
        if not choices:
            return None

        delta = choices[0].get("delta", {})
        chunk = StreamChunk()

        if "content" in delta and delta["content"]:
            chunk.text = delta["content"]
        if "reasoning_content" in delta and delta["reasoning_content"]:
            chunk.thinking = delta["reasoning_content"]

        if chunk.text or chunk.thinking:
            return chunk
        return None

    def _format_messages(self, messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, AssistantMessage):
                m: dict[str, Any] = {"role": "assistant"}
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
                formatted.append(m)
            elif isinstance(msg, ToolResult):
                m: dict[str, Any] = {"role": "tool", "content": msg.content or ""}
                if msg.tool_call_id:
                    m["tool_call_id"] = msg.tool_call_id
                formatted.append(m)
            elif isinstance(msg, SystemMessage) or isinstance(msg, UserMessage):
                m: dict[str, Any] = {"role": msg.role}
                if msg.content:
                    m["content"] = msg.content
                formatted.append(m)
            else:
                logger.debug("Dropping unknown message role: %s", msg.role)
        return formatted

    def _format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"type": "function", "function": t} for t in tools]

    def _parse_response(self, data: dict[str, Any]) -> ModelResponse:
        choice = data["choices"][0]
        message = choice["message"]

        text: str | None = message.get("content")
        thinking: str | None = None
        tool_calls: list[ToolCall] | None = None
        finish_reason: str | None = choice.get("finish_reason")

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
            finish_reason=finish_reason,
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
        model_ref = getattr(self._config.alias, scene, None) or self._config.alias.main
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
