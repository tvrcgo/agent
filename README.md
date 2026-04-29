# Agent

An agent framework.

## Architecture

```
core/
├── loop.py      # Autonomous reasoning loop (Think → Act → Observe)
├── ws.py        # WebSocket server with multi-type message protocol
└── plugin.py    # Plugin/Skill registration system
└── memory.py    # Sliding-window short-term memory

providers/
├── base.py      # LLM provider abstraction
└── openai.py    # OpenAI-compatible implementation

skills/
└── echo.py      # Example skill
```

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### Local Development

```bash
# Install dependencies
uv sync

# Set your API key
export OPENAI_API_KEY=sk-...

# Run the agent
uv run python -m agent
```

### Docker

```bash
# One-command deploy
OPENAI_API_KEY=sk-... docker compose up -d
```

The agent will listen on `ws://localhost:8765`.

## WebSocket Protocol

### Client → Agent

```json
{"type": "task", "payload": {"content": "Analyze this codebase and summarize the architecture"}}
{"type": "message", "payload": {"content": "Also check the test coverage"}}
{"type": "confirm_response", "payload": {"id": "xxx", "action_id": "approve"}}
{"type": "cancel", "payload": {}}
```

### Agent → Client

| Type | Description |
|------|-------------|
| `thinking` | Agent's reasoning process |
| `message` | Final text output |
| `tool_call` | Tool invocation |
| `tool_result` | Tool execution result |
| `confirm` | Request user confirmation |
| `status` | State change (thinking/acting/waiting/idle/done) |
| `error` | Error report |

## Configuration

Edit `config.yml`:

```yaml
model:
  provider: openai
  name: gpt-4o
  base_url: https://api.openai.com/v1
  api_key: ${OPENAI_API_KEY}

agent:
  window_size: 50
  max_iterations: 100

ws:
  host: 0.0.0.0
  port: 8765
```

## Agent Definition

Edit `AGENTS.md` to define the agent's identity, capabilities, and constraints. This file is injected as the system prompt.

## Extending

Create a new skill by subclassing `Skill`:

```python
from agent.core.plugin import Skill, ToolDefinition

class MySkill(Skill):
    name = "my_skill"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="my_tool", description="...", parameters={...})]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        ...
```

Register it in `__main__.py`:

```python
registry.register(MySkill())
```
