# Agent

Autonomous WebSocket agent with plugin lifecycle, per-session memory, and LLM-powered context compression.

## Quick Start

```bash
uv sync
export DEEPSEEK_API_KEY=sk-...
uv run python -m agent
```

Agent listens on `ws://localhost:8765`.

### Docker

```bash
DEEPSEEK_API_KEY=sk-... docker compose up -d
```

## WebSocket Protocol

### Client → Agent

```json
{"type": "chat",    "payload": {"content": "Analyze this codebase"}}
{"type": "command", "payload": {"action": "cancel"}}
{"type": "command", "payload": {"action": "compress"}}
```

### Agent → Client

| Type | Description |
|------|-------------|
| `thinking` | Agent's reasoning process |
| `message` | Final text output |
| `tool_call` | Tool invocation |
| `tool_result` | Tool execution result |
| `status` | State change (thinking/acting/idle/done) |
| `error` | Error report |

## Configuration

```yaml
model:
  provider: openai
  name: deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key: ${DEEPSEEK_API_KEY}

agent:
  max_concurrent_sessions: 10
  max_iterations: 100
  max_context_messages: 100
  max_tokens: 65536
  compress_threshold: 0.9
  compress_keep_recent: 10

ws:
  host: 0.0.0.0
  port: 8765

skills:
  modules:
    - agent.skills.websearch

plugins:
  modules:
    - agent.plugins.session
```

## Extending

Create a skill by subclassing `Skill`:

```python
from agent.core.skill import Skill, ToolDefinition

class MySkill(Skill):
    name = "my_skill"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="my_tool", description="...", parameters={...})]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        ...
```

Register via `config.yml` → `skills.modules`.

## Agent Definition

Edit `agent/AGENTS.md` to define the agent's identity, capabilities, and constraints. Injected as the system prompt.
