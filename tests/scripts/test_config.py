"""model provider 配置单元测试。

覆盖：ProviderConfig.api_protocol 默认值与显式设置、
base_url 的 ${VAR} 环境变量展开、ModelRegistry 按协议选择子类（已知/未知）、
OpenAICompletionsProvider payload 结构与流式解析聚合。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncio

from agent.core.config import ModelConfig, ModelSection, ProviderConfig, load_config
from agent.core.model import (
    ModelProvider,
    ModelRegistry,
    OpenAICompletionsProvider,
    StreamChunk,
    SystemMessage,
    ToolCall,
)

ENV_KEY = "DEEPSEEK_BASE_URL"


async def test_provider_api_protocol_default() -> None:
    """未配置时默认 openai_completions，显式配置生效。"""
    provider = ProviderConfig(base_url="https://x")
    assert provider.api_protocol == "openai_completions"

    provider = ProviderConfig(base_url="https://x", api_protocol="openai_responses")
    assert provider.api_protocol == "openai_responses"


async def test_load_config_base_url_env_expand() -> None:
    """config.yml 中 base_url 的 ${VAR} 从环境变量展开。"""
    prev = os.environ.get(ENV_KEY)
    os.environ[ENV_KEY] = "https://env-test.example.com"
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yml", delete=False, encoding="utf-8"
        ) as f:
            f.write(
                "model:\n"
                "  providers:\n"
                "    p:\n"
                f"      base_url: ${{{ENV_KEY}}}\n"
            )
            path = f.name
        config = load_config(path)
        assert config.model.providers["p"].base_url == "https://env-test.example.com"
    finally:
        Path(path).unlink(missing_ok=True)
        if prev is None:
            os.environ.pop(ENV_KEY, None)
        else:
            os.environ[ENV_KEY] = prev


async def test_registry_resolves_openai_completions() -> None:
    """已知协议解析为对应子类，默认即 openai_completions，chat 端点为 /chat/completions。"""
    section = ModelSection(
        providers={"p": ProviderConfig(
            base_url="https://x",
            api_key="k",
            models={"m": ModelConfig(name="m")},
        )},
        alias=ModelSection.Alias(main="p:m"),
    )
    registry = ModelRegistry(section)
    try:
        provider = registry._resolve("p:m")
        assert isinstance(provider, ModelProvider)
        assert isinstance(provider, OpenAICompletionsProvider)
        assert provider.chat_endpoint == "/chat/completions"
    finally:
        await registry.close()


async def test_registry_rejects_unknown_protocol() -> None:
    """未知协议在解析时快速失败。"""
    section = ModelSection(
        providers={"p": ProviderConfig(
            base_url="https://x",
            api_key="k",
            api_protocol="bogus",
            models={"m": ModelConfig(name="m")},
        )},
        alias=ModelSection.Alias(main="p:m"),
    )
    registry = ModelRegistry(section)
    try:
        registry._resolve("p:m")
    except ValueError:
        return
    raise AssertionError("expected ValueError for unsupported api_protocol")


async def test_protocol_build_payload() -> None:
    """payload 结构：model/messages/stream/tools 字段齐全，stream 按参数切换。"""
    provider = OpenAICompletionsProvider(base_url="https://x", api_key="k", model_name="m")
    messages = [SystemMessage(content="hi")]

    payload = provider.build_payload(messages, None, False)
    assert payload == {"model": "m", "messages": [{"role": "system", "content": "hi"}], "stream": False}
    assert "tools" not in payload

    payload = provider.build_payload(messages, [{"name": "t"}], True)
    assert payload["stream"] is True
    assert payload["tools"] == [{"type": "function", "function": {"name": "t"}}]


class _FakeResp:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


async def test_protocol_parse_stream_aggregates() -> None:
    """流式解析：text 累积、tool_calls 按 index 聚合、finish_reason 就位、on_chunk 触发。"""
    provider = OpenAICompletionsProvider(base_url="https://x", api_key="k", model_name="m")
    lines = [
        'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\":\\"a\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    ]
    chunks = []

    async def on_chunk(chunk: StreamChunk) -> None:
        chunks.append(chunk)

    response = await provider.parse_stream(_FakeResp(lines), on_chunk)
    assert response.text == "hi"
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls == [ToolCall(id="call_1", name="read_file", arguments={"path": "a"})]
    assert chunks == [StreamChunk(text="hi")]

    # 空行与非 data 行被忽略
    response = await provider.parse_stream(_FakeResp(["\n", "event: ping\n", "data: [DONE]"]), on_chunk)
    assert response.text is None
    assert response.tool_calls is None


async def main() -> None:
    results = {}
    scenarios = [
        ("provider_api_protocol_default", test_provider_api_protocol_default),
        ("load_config_base_url_env_expand", test_load_config_base_url_env_expand),
        ("registry_resolves_openai_completions", test_registry_resolves_openai_completions),
        ("registry_rejects_unknown_protocol", test_registry_rejects_unknown_protocol),
        ("protocol_build_payload", test_protocol_build_payload),
        ("protocol_parse_stream_aggregates", test_protocol_parse_stream_aggregates),
    ]
    for name, fn in scenarios:
        try:
            await fn()
            results[name] = True
            print(f"  {name}: PASS")
        except Exception as e:
            print(f"  {name}: FAIL - {e}")
            results[name] = False
            import traceback
            traceback.print_exc()

    passed = sum(1 for v in results.values() if v)
    print(f"\nPassed: {passed}/{len(results)}")
    return passed == len(results)


if __name__ == "__main__":
    asyncio.run(main())
