# Agent Project

Autonomous WebSocket agent with plugin lifecycle, per-session memory, and LLM-powered context compression. OpenAI-compatible API (DeepSeek).

## Architecture

```
agent/
├── __main__.py          # Entrypoint: wires LLM + skills + plugins + loop + WS server
├── core/
│   ├── config.py         # Pydantic config, YAML loading, ${VAR} expansion
│   ├── llm.py            # OpenAIProvider: async httpx client, Message/LLMResponse types
│   ├── loop.py           # AgentLoop + JobAborted: Think→Act→Observe, per-session Task
│   ├── plugin.py         # Plugin ABC, PluginRegistry, PluginContext (mutable context bag)
│   ├── skill.py          # Skill ABC, SkillRegistry, ToolDefinition
│   └── ws.py             # WebSocketServer, ClientSession, typed message protocol
├── plugins/
│   ├── session.py        # SessionMemory (buffer+compression) + SessionPlugin (lifecycle)
│   └── confirm.py        # ConfirmPlugin: intercepts before_tool, blocks on user approval
├── skills/
│   ├── websearch.py      # Web search skill (Google first, multi-engine fallback)
│   └── confirm.py        # RequestConfirmationSkill: stub, real logic in ConfirmPlugin
└── AGENTS.md             # System prompt for the agent
```

## Data Flow

1. Client connects via WebSocket (with optional `?session_id=xxx`)
2. `AgentLoop` creates an `asyncio.Task` per session, runs the reasoning loop:
   - **Think:** emit `before_llm` hook → SessionPlugin checks compression, sets `ctx.data["messages"]` → LLM call
   - **Act:** `ToolCallEvent` → `before_tool` hook → `SkillRegistry.execute()` → `ToolResultEvent` → `after_tool` hook → loop back to Think
   - **Done:** no tool calls → emit `MessageEvent` → emit `on_complete` → persist session
3. SessionPlugin persists conversations as JSONL at `./data/sessions/<session_id>.jsonl`

## Key Design Points

### WebSocket Message Protocol
- Incoming: `chat` (user-typed text) and `command` (UI actions with `action` field)
- Outgoing wraps events in `{"type": "...", "timestamp": "...", "payload": {...}}`
- Session ID from query string, not from message body

### Context Management (three layers)
- **`max_context_messages`:** hard cap on messages sent to LLM (FIFO from tail)
- **Token-based compression:** at `max_tokens * compress_threshold` (90%), older messages are summarized via a separate LLM call, replaced with `[Previous conversation summary]` marker
- No `window_size` — storage is unbounded, only read is capped

### Plugin Lifecycle (8 lifecycle hooks + command hooks)
Lifecycle: `on_connect` → `before_job` → `before_llm` → `after_llm` → `before_tool` → skill execution → `after_tool` → ...(loop) → `on_complete`/`on_disconnect`

Commands: `command:<action>` hooks. The loop dispatches `CommandMessage` → `command:<action>` hook. Plugins respond to specific actions by registering `command:<action>` handlers. `command:cancel` is handled by the loop itself (core behavior).

Plugins communicate with the loop exclusively via `ctx.data` dict. SessionPlugin sets `ctx.data["messages"]`; the loop sets `ctx.data["response"]` and `ctx.data["tool_call"]`.

**Rule: loop.py is closed for modification.** All capability extensions — new commands, new behaviors, confirmation flows, context management — must be implemented as plugins via hooks. The only changes allowed in loop.py are bug fixes and hook-point additions. If you find yourself adding business logic to the loop, stop and redesign it as a plugin.

## Gotchas Fixed

1. **DeepSeek reasoning_content must be passed back.** If the API returns `reasoning_content` (thinking), it must be included in subsequent requests as `"reasoning_content"`, or the API returns 400. Handled in `_format_messages()`.

2. **Multi-session race condition.** `SessionPlugin` had a shared `_active_session_id` field that got overwritten when concurrent sessions interleaved hooks. Fixed by passing `session_id` explicitly via `_get_or_load(session_id)` instead of relying on mutable shared state.

3. **Empty string content causes 400.** Some APIs reject `"content": ""` in assistant messages with tool calls. `_format_messages()` uses truthy check (`if msg.content`) to omit empty content.

4. **Docker port publishing needs 0.0.0.0.** Container must bind `0.0.0.0` to accept forwarded connections. Local dev can use either address.

## Conventions

- No docstrings, no comments unless behavior is surprising
- Single file per concern, no premature abstractions
- Configuration: defaults in Pydantic model, overrides in `config.yml`
- Session storage: `./data/sessions/`, one JSONL file per session
- Default config reads system prompt from `agent/AGENTS.md`
- Test checklist in `tests/README.md`, integration tests in `tests/test_ws.py`
