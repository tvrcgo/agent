from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry, PluginContext
from agent.core.llm import Message, ToolCall

if TYPE_CHECKING:
    from agent.core.config import Config

logger = logging.getLogger(__name__)


class SessionMemory:
    """Sliding-window short-term memory with compression support.

    Keeps the most recent messages within window_size for storage,
    and limits context sent to LLM via max_context_messages.
    When estimated tokens exceed max_tokens * compress_threshold,
    older messages are compacted into a summary.
    """

    def __init__(
        self,
        window_size: int = 50,
        max_context_messages: int = 100,
        max_tokens: int = 65536,
        compress_threshold: float = 0.9,
        keep_recent: int = 10,
    ) -> None:
        self._window_size = window_size
        self._max_context_messages = max_context_messages
        self._max_tokens = max_tokens
        self._compress_threshold = compress_threshold
        self._keep_recent = keep_recent
        self._messages: list[Message] = []
        self._system_prompt: Message | None = None
        self._last_prompt_tokens: int = 0

    def set_system_prompt(self, content: str) -> None:
        self._system_prompt = Message(role="system", content=content)

    def add_user_message(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))
        self._trim()

    def add_assistant_message(
        self,
        content: str | None = None,
        thinking: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        self._messages.append(
            Message(
                role="assistant",
                content=content,
                thinking=thinking,
                tool_calls=tool_calls,
            )
        )
        self._trim()

    def add_tool_result(self, tool_call: ToolCall, result: str) -> None:
        self._messages.append(
            Message(role="tool", content=result, tool_call_id=tool_call.id)
        )
        self._trim()

    def get_messages(self) -> list[Message]:
        msgs: list[Message] = []
        if self._system_prompt:
            msgs.append(self._system_prompt)
        recent = self._messages[-self._max_context_messages:]
        msgs.extend(recent)
        return msgs

    @property
    def keep_recent(self) -> int:
        return self._keep_recent

    def add_message(self, msg: Message) -> None:
        """Append a pre-constructed message (used for deserialization). Does not trigger trim."""
        self._messages.append(msg)

    def clear(self) -> None:
        self._messages.clear()

    def _trim(self) -> None:
        if len(self._messages) > self._window_size:
            overflow = len(self._messages) - self._window_size
            self._messages = self._messages[overflow:]

    # --- Token estimation and compression ---

    def set_last_prompt_tokens(self, n: int) -> None:
        self._last_prompt_tokens = n

    def estimate_tokens(self) -> int:
        if self._last_prompt_tokens > 0:
            return self._last_prompt_tokens
        total_chars = 0
        for msg in self.get_messages():
            if msg.content:
                total_chars += len(msg.content)
            if msg.thinking:
                total_chars += len(msg.thinking)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total_chars += len(tc.name) + len(str(tc.arguments))
        return total_chars // 4

    def needs_compression(self) -> bool:
        return self.estimate_tokens() >= int(self._max_tokens * self._compress_threshold)

    def compact(self, summary: str) -> None:
        if len(self._messages) <= self._keep_recent:
            return
        split_point = len(self._messages) - self._keep_recent
        recent = self._messages[split_point:]
        summary_msg = Message(
            role="user",
            content=f"[Previous conversation summary]\n{summary}",
        )
        self._messages = [summary_msg] + recent
        self._last_prompt_tokens = 0


class SessionPlugin(Plugin):
    """Manages per-session context with file-based persistence.

    Registers hooks into the agent lifecycle to load/persist session data.
    Each session is stored as a JSONL file under ``memory_path/<session_id>.jsonl``.
    """

    name = "session"
    MEMORY_PATH = "/agent/memory"

    def __init__(self) -> None:
        self._base_path = Path(self.MEMORY_PATH)
        self._sessions: dict[str, SessionMemory] = {}
        self._active_session_id: str | None = None
        self._default_memory: SessionMemory | None = None
        self._window_size: int = 50
        self._max_context_messages: int = 100
        self._max_tokens: int = 65536
        self._compress_threshold: float = 0.9
        self._keep_recent: int = 10

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
        self._max_context_messages = config.agent.max_context_messages
        self._max_tokens = config.agent.max_tokens
        self._compress_threshold = config.agent.compress_threshold
        self._keep_recent = config.agent.keep_recent
        self._base_path.mkdir(parents=True, exist_ok=True)

        # Create default memory with system prompt
        self._default_memory = self._make_memory()
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
        """Provide messages to the loop via ctx.data, compressing if needed."""
        memory = self._get_active_memory()
        if memory.needs_compression():
            await self._compress(ctx, memory)
        ctx.data["messages"] = memory.get_messages()

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
        if response.usage:
            self._get_active_memory().set_last_prompt_tokens(response.usage.prompt_tokens)

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

    def _get_active_memory(self) -> SessionMemory:
        """Return the memory for the currently active session (or default)."""
        if self._active_session_id and self._active_session_id in self._sessions:
            return self._sessions[self._active_session_id]
        return self._default_memory or self._make_memory()

    def _make_memory(self) -> SessionMemory:
        return SessionMemory(
            window_size=self._window_size,
            max_context_messages=self._max_context_messages,
            max_tokens=self._max_tokens,
            compress_threshold=self._compress_threshold,
            keep_recent=self._keep_recent,
        )

    def persist_active(self) -> None:
        """Persist the currently active session to disk."""
        if self._active_session_id and self._active_session_id in self._sessions:
            self._save_session(
                self._active_session_id,
                self._sessions[self._active_session_id],
            )

    async def _compress(self, ctx: PluginContext, memory: SessionMemory) -> None:
        """Summarize older messages via LLM and compact the memory."""
        llm = ctx.llm
        if llm is None:
            logger.warning("No llm in context, skipping compression")
            return

        all_messages = memory.get_messages()
        # Skip system prompt (index 0), keep recent messages, compress the middle
        kr = memory.keep_recent
        if len(all_messages) <= kr + 1:
            return

        old = all_messages[1:-kr]  # between system prompt and recent
        if not old:
            return

        formatted = self._format_for_summary(old)
        summary_prompt = [
            Message(
                role="system",
                content=(
                    "You are a conversation summarizer. Summarize the following conversation "
                    "history concisely, preserving all key facts, decisions, actions taken, "
                    "and their results. Output only the summary, no preamble."
                ),
            ),
            Message(role="user", content=formatted),
        ]

        try:
            response = await llm.chat(summary_prompt, tools=None)
            if response.text:
                memory.compact(response.text)
                logger.info("Session compressed: %d messages -> summary + %d recent",
                            len(old), kr)
        except Exception:
            logger.warning("Compression failed, falling back to sliding window", exc_info=True)

    @staticmethod
    def _format_for_summary(messages: list[Message]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = msg.role
            content = msg.content or ""
            if role == "tool" and len(content) > 500:
                content = content[:500] + "..."
            if msg.tool_calls:
                tc_names = [tc.name for tc in msg.tool_calls]
                content = f"[called: {', '.join(tc_names)}] " + content
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    def _session_file(self, session_id: str) -> Path:
        return self._base_path / f"{session_id}.jsonl"

    def _load_session(self, session_id: str) -> SessionMemory:
        """Load a session from disk, or return a fresh SessionMemory."""
        path = self._session_file(session_id)
        if path.exists():
            try:
                messages = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        messages.append(self._deserialize_message(json.loads(line)))
                memory = self._make_memory()
                for msg in messages:
                    if msg.role == "system":
                        memory.set_system_prompt(msg.content or "")
                    else:
                        memory.add_message(msg)
                logger.info("Session %s loaded from disk (%d messages)", session_id, len(messages))
                return memory
            except Exception:
                logger.warning("Failed to load session %s, starting fresh", session_id, exc_info=True)
        return self._make_memory()

    def _save_session(self, session_id: str, memory: SessionMemory) -> None:
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
        from agent.core.llm import ToolCall

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