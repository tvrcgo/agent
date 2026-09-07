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


@dataclass
class StreamEvent:
    """协议从一条流式事件中解释出的增量。"""

    text: str = ""
    thinking: str = ""
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ModelProvider:
    """模型提供者通用基类：HTTP 生命周期与调度骨架，协议差异由子类实现。

    子类职责：chat_endpoint、build_payload、parse_response、parse_stream。
    """

    chat_endpoint = ""

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

    def build_payload(
        self,
        messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def parse_response(self, data: dict[str, Any]) -> ModelResponse:
        raise NotImplementedError

    async def parse_stream(
        self,
        resp: httpx.Response,
        on_chunk: Callable[[StreamChunk], Coroutine[Any, Any, None]] | None,
    ) -> ModelResponse:
        raise NotImplementedError

    async def chat(
        self,
        messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        payload = self.build_payload(messages, tools, False)

        try:
            resp = await self._http.post(self.chat_endpoint, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            logger.error("Model HTTP %s: %s", e.response.status_code, body)
            raise
        except Exception:
            logger.exception("Model request failed")
            raise

        return self.parse_response(resp.json())

    async def chat_stream(
        self,
        messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult],
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[StreamChunk], Coroutine[Any, Any, None]] | None = None,
    ) -> ModelResponse:
        payload = self.build_payload(messages, tools, True)

        try:
            async with self._http.stream(
                "POST", self.chat_endpoint, json=payload
            ) as resp:
                if resp.status_code >= 400:
                    # 错误 body 必须在流上下文中 read（退出上下文后流已关闭）
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                    logger.error("Model HTTP %s: %s", resp.status_code, body)
                    resp.raise_for_status()
                return await self.parse_stream(resp, on_chunk)
        except httpx.HTTPStatusError as e:
            logger.error("Model HTTP %s (status body logged above if streamable)", e.response.status_code)
            raise
        except Exception:
            logger.exception("Model stream request failed")
            raise


class OpenAICompletionsProvider(ModelProvider):
    """OpenAI chat completions 协议实现。"""

    chat_endpoint = "/chat/completions"

    def build_payload(
        self,
        messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": self.format_messages(messages),
            "stream": stream,
        }
        if tools:
            payload["tools"] = self.format_tools(tools)
        return payload

    def format_messages(
        self,
        messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult],
    ) -> list[dict[str, Any]]:
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

    def format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"type": "function", "function": t} for t in tools]

    def parse_response(self, data: dict[str, Any]) -> ModelResponse:
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

    def parse_stream_event(self, data: str) -> StreamEvent | None:
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return None

        choices = obj.get("choices", [])
        if not choices:
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        event = StreamEvent()

        if delta.get("content"):
            event.text = delta["content"]
        if delta.get("reasoning_content"):
            event.thinking = delta["reasoning_content"]
        if choice.get("finish_reason"):
            event.finish_reason = choice["finish_reason"]
        if delta.get("tool_calls"):
            event.tool_calls = delta["tool_calls"]

        if event.text or event.thinking or event.finish_reason or event.tool_calls:
            return event
        return None

    async def parse_stream(
        self,
        resp: httpx.Response,
        on_chunk: Callable[[StreamChunk], Coroutine[Any, Any, None]] | None,
    ) -> ModelResponse:
        response = ModelResponse()
        tool_calls_map: dict[str, dict[str, Any]] = {}
        tool_calls_order: list[str] = []

        async for line in resp.aiter_lines():
            if not line or line == "data: [DONE]":
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            event = self.parse_stream_event(data)
            if event is None:
                continue
            if event.text:
                response.text = (response.text or "") + event.text
            if event.thinking:
                response.thinking = (response.thinking or "") + event.thinking
            if event.text or event.thinking:
                if on_chunk:
                    await on_chunk(StreamChunk(text=event.text, thinking=event.thinking))
            if event.finish_reason:
                response.finish_reason = event.finish_reason
            if event.tool_calls:
                for tc in event.tool_calls:
                    idx = tc.get("index", 0)
                    key = f"idx_{idx}"
                    tc_id = tc.get("id")
                    if key not in tool_calls_map:
                        tool_calls_map[key] = {
                            "id": tc_id or key,
                            "function": {"name": "", "arguments": ""},
                        }
                        tool_calls_order.append(key)
                    if tc_id:
                        tool_calls_map[key]["id"] = tc_id
                    fn = tc.get("function", {})
                    if fn:
                        if "name" in fn:
                            tool_calls_map[key]["function"]["name"] += fn["name"]
                        if "arguments" in fn:
                            tool_calls_map[key]["function"]["arguments"] += fn["arguments"]

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


class ModelRegistry:

    _provider_types = {
        "openai_completions": OpenAICompletionsProvider,
    }

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
            provider_config = self._config.providers[provider_name]
            provider_class = self._provider_types.get(provider_config.api_protocol)
            if provider_class is None:
                raise ValueError(f"unsupported api_protocol: {provider_config.api_protocol}")
            model = provider_config.models[model_name]
            self._providers[ref] = provider_class(
                base_url=provider_config.base_url,
                api_key=provider_config.api_key,
                model_name=model.name,
            )
        return self._providers[ref]
