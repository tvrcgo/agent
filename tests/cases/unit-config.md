# 单元测试 — Config / Model Provider

## 测试范围

- ProviderConfig.api_protocol 默认值与显式设置
- config.yml 中 base_url 的 `${VAR}` 环境变量展开
- ModelRegistry 按 api_protocol 选择子类（已知协议、未知协议快速失败）
- OpenAICompletionsProvider payload 结构与 parse_stream 流式解析聚合

## 测试用例

### 1. ProviderConfig 协议默认值

```python
from agent.core.config import ProviderConfig

def test_provider_api_protocol_default():
    provider = ProviderConfig(base_url="https://x")
    assert provider.api_protocol == "openai_completions"

    provider = ProviderConfig(base_url="https://x", api_protocol="openai_responses")
    assert provider.api_protocol == "openai_responses"
```

### 2. base_url 从环境变量读

```python
import os
from agent.core.config import load_config

def test_load_config_base_url_env_expand(tmp_path):
    os.environ["DEEPSEEK_BASE_URL"] = "https://env-test.example.com"
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(
        "model:\n"
        "  providers:\n"
        "    p:\n"
        "      base_url: ${DEEPSEEK_BASE_URL}\n"
    )
    config = load_config(str(cfg_path))
    assert config.model.providers["p"].base_url == "https://env-test.example.com"
```

### 3. 已知协议解析为子类

```python
from agent.core.config import ModelConfig, ModelSection, ProviderConfig
from agent.core.model import ModelProvider, ModelRegistry, OpenAICompletionsProvider

def test_registry_resolves_openai_completions():
    section = ModelSection(
        providers={"p": ProviderConfig(
            base_url="https://x", api_key="k",
            models={"m": ModelConfig(name="m")},
        )},
        alias=ModelSection.Alias(main="p:m"),
    )
    registry = ModelRegistry(section)
    provider = registry._resolve("p:m")
    assert isinstance(provider, ModelProvider)
    assert isinstance(provider, OpenAICompletionsProvider)
    assert provider.chat_endpoint == "/chat/completions"
```

### 4. 未知协议快速失败

```python
from agent.core.config import ModelConfig, ModelSection, ProviderConfig
from agent.core.model import ModelRegistry

def test_registry_rejects_unknown_protocol():
    section = ModelSection(
        providers={"p": ProviderConfig(
            base_url="https://x", api_key="k", api_protocol="bogus",
            models={"m": ModelConfig(name="m")},
        )},
        alias=ModelSection.Alias(main="p:m"),
    )
    try:
        ModelRegistry(section)._resolve("p:m")
    except ValueError:
        return
    raise AssertionError("expected ValueError")
```

### 5. 协议 payload 结构

```python
from agent.core.model import OpenAICompletionsProvider, SystemMessage

def test_protocol_build_payload():
    provider = OpenAICompletionsProvider(base_url="https://x", api_key="k", model_name="m")
    messages = [SystemMessage(content="hi")]

    payload = provider.build_payload(messages, None, False)
    assert payload == {"model": "m", "messages": [{"role": "system", "content": "hi"}], "stream": False}
    assert "tools" not in payload

    payload = provider.build_payload(messages, [{"name": "t"}], True)
    assert payload["stream"] is True
    assert payload["tools"] == [{"type": "function", "function": {"name": "t"}}]
```

### 6. 协议 parse_stream 聚合

```python
from agent.core.model import OpenAICompletionsProvider, StreamChunk, ToolCall

class FakeResp:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

async def test_protocol_parse_stream_aggregates():
    provider = OpenAICompletionsProvider(base_url="https://x", api_key="k", model_name="m")
    lines = [
        'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\":\\"a\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    ]
    chunks = []

    async def on_chunk(chunk):
        chunks.append(chunk)

    response = await provider.parse_stream(FakeResp(lines), on_chunk)
    assert response.text == "hi"
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls == [ToolCall(id="call_1", name="read_file", arguments={"path": "a"})]
    assert chunks == [StreamChunk(text="hi")]
```

---

## 运行

```bash
python tests/scripts/test_config.py
```
