from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry, PluginContext
from agent.plugins.memory import ShortTermMemory
from agent.providers.base import Message

if TYPE_CHECKING:
    from agent.config import Config

logger = logging.getLogger(__name__)


class SessionPlugin(Plugin):
    """Manages per-session context with file-based persistence.

    Registers hooks into the agent lifecycle to load/persist session data.
    Each session is stored as a JSONL file under ``memory_path/<session_id>.jsonl``.
    """

    name = "session"
    MEMORY_PATH = "/agent/memory"

    def __init__(self) -> None:
        self._base_path = Path(self.MEMORY_PATH)
        self._sessions: dict[str, ShortTermMemory] = {}
        self._active_session_id: str | None = None
        self._default_memory: ShortTermMemory | None = None
        self._window_size: int = 50

    def register(self, registry: PluginRegistry) -> None:
        """Register lifecycle hooks."""
        registry.on("on_connect", self._on_connect)
        registry.on("on_message", self._on_message)
        registry.on("before_task", self._on_before_task)
        registry.on("before_llm", self._on_before_llm)
        registry.on("after_llm", self._on_after_llm)
        registry.on("after_tool", self._on_after_tool)
        registry.on("on_complete", self._on_complete)
        registry.on("on_disconnect", self._on_disconnect)

    def init(self, config: Config) -> None:
        """Initialize with config."""
        self._window_size = config.agent.window_size
        self._base_path.mkdir(parents=True, exist_ok=True)

        # Create default memory with system prompt
        self._default_memory = ShortTermMemory(window_size=self._window_size)
        prompt_path = Path(config.agent.system_prompt_path)
        if prompt_path.exists():
            self._default_memory.set_system_prompt(prompt_path.read_text(encoding="utf-8"))

        logger.info("SessionPlugin initialized, persistence_path=%s", self._base_path)

    def shutdown(self) -> None:
        """Persist all active sessions and clean up."""
        for sid, mem in self._sessions.items():
            self._save_session(sid, mem)
        self._sessions.clear()
        logger.info("SessionPlugin shut down, all sessions persisted")

    # --- Hook handlers ---

    async def _on_connect(self, ctx: PluginContext) -> None:
        """Load session data when a client connects."""
        self._activate(ctx.session_id)

    async def _on_message(self, ctx: PluginContext) -> None:
        """Append user message to session memory."""
        self._activate(ctx.session_id)
        content = ctx.data.get("message", "")
        if content:
            self._get_active_memory().add_user_message(content)

    async def _on_before_task(self, ctx: PluginContext) -> None:
        """Append task as user message and activate session."""
        self._activate(ctx.session_id)
        task = ctx.data.get("task", "")
        if task:
            self._get_active_memory().add_user_message(task)

    async def _on_before_llm(self, ctx: PluginContext) -> None:
        """Provide messages to the loop via ctx.data."""
        ctx.data["messages"] = self._get_active_memory().get_messages()

    async def _on_after_llm(self, ctx: PluginContext) -> None:
        """Record assistant response into session memory."""
        response = ctx.data.get("response")
        if response is None:
            return
        self._get_active_memory().add_assistant_message(
            content=response.text,
            thinking=response.thinking,
            tool_calls=response.tool_calls,
        )

    async def _on_after_tool(self, ctx: PluginContext) -> None:
        """Record tool result into session memory."""
        tool_call = ctx.data.get("tool_call")
        result = ctx.data.get("result", "")
        if tool_call:
            self._get_active_memory().add_tool_result(tool_call, result)

    async def _on_complete(self, ctx: PluginContext) -> None:
        """Persist session data when a task completes."""
        self.persist_active()

    async def _on_disconnect(self, ctx: PluginContext) -> None:
        """Persist session data when a client disconnects."""
        self.persist_active()

    # --- Internal ---

    def _activate(self, session_id: str | None) -> None:
        """Activate a session by ID."""
        if session_id is None:
            self._active_session_id = None
            return
        self._active_session_id = session_id
        if session_id not in self._sessions:
            self._sessions[session_id] = self._load_session(session_id)

    def _get_active_memory(self) -> ShortTermMemory:
        """Return the memory for the currently active session (or default)."""
        if self._active_session_id and self._active_session_id in self._sessions:
            return self._sessions[self._active_session_id]
        return self._default_memory or ShortTermMemory()

    def persist_active(self) -> None:
        """Persist the currently active session to disk."""
        if self._active_session_id and self._active_session_id in self._sessions:
            self._save_session(
                self._active_session_id,
                self._sessions[self._active_session_id],
            )

    def _session_file(self, session_id: str) -> Path:
        return self._base_path / f"{session_id}.jsonl"

    def _load_session(self, session_id: str) -> ShortTermMemory:
        """Load a session from disk, or return a fresh ShortTermMemory."""
        path = self._session_file(session_id)
        if path.exists():
            try:
                messages = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        messages.append(self._deserialize_message(json.loads(line)))
                memory = ShortTermMemory(window_size=self._window_size)
                for msg in messages:
                    if msg.role == "system":
                        memory.set_system_prompt(msg.content or "")
                    else:
                        memory._messages.append(msg)
                logger.info("Session %s loaded from disk (%d messages)", session_id, len(messages))
                return memory
            except Exception:
                logger.warning("Failed to load session %s, starting fresh", session_id, exc_info=True)
        return ShortTermMemory(window_size=self._window_size)

    def _save_session(self, session_id: str, memory: ShortTermMemory) -> None:
        """Save a session to disk as JSONL (one message per line)."""
        path = self._session_file(session_id)
        try:
            lines = [json.dumps(self._serialize_message(msg), ensure_ascii=False) for msg in memory.get_messages()]
            path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
            logger.debug("Session %s persisted (%d messages)", session_id, len(lines))
        except Exception:
            logger.warning("Failed to persist session %s", session_id, exc_info=True)

    @staticmethod
    def _serialize_message(msg: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": msg.role}
        if msg.content is not None:
            d["content"] = msg.content
        if msg.thinking is not None:
            d["thinking"] = msg.thinking
        if msg.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in msg.tool_calls
            ]
        if msg.tool_call_id is not None:
            d["tool_call_id"] = msg.tool_call_id
        return d

    @staticmethod
    def _deserialize_message(d: dict[str, Any]) -> Message:
        from agent.providers.base import ToolCall

        tool_calls = None
        if "tool_calls" in d:
            tool_calls = [
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", {}))
                for tc in d["tool_calls"]
            ]
        return Message(
            role=d["role"],
            content=d.get("content"),
            thinking=d.get("thinking"),
            tool_calls=tool_calls,
            tool_call_id=d.get("tool_call_id"),
        )