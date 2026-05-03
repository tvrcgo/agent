from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import JobContext
from agent.core.llm import Message, ToolCall

if TYPE_CHECKING:
    from agent.core.config import Config

logger = logging.getLogger(__name__)


@dataclass
class _SessionState:
    messages: list[Message] = field(default_factory=list)
    system_prompt: Message | None = None
    last_prompt_tokens: int = 0
    cold_loaded: bool = False


class SessionPlugin(Plugin):
    """Per-session message store with JSONL persistence and auto-compression."""

    name = "session"

    def __init__(self) -> None:
        self._base_path = Path("./data/sessions")
        self._sessions: dict[str, _SessionState] = {}
        self._default_state: _SessionState | None = None
        self._max_load_messages: int = 100
        self._max_tokens: int = 65536
        self._compress_threshold: float = 0.9
        self._compress_keep_recent: int = 10

    def register(self, registry: PluginRegistry) -> None:
        registry.on("on_connect", self._on_connect)
        registry.on("before_job", self._on_before_job)
        registry.on("before_llm", self._on_before_llm)
        registry.on("after_llm", self._on_after_llm)
        registry.on("after_tool", self._on_after_tool)
        registry.on("command:compress", self._on_compress)

    def init(self, config: Config) -> None:
        self._max_load_messages = config.agent.max_load_messages
        self._max_tokens = config.agent.max_tokens
        self._compress_threshold = config.agent.compress_threshold
        self._compress_keep_recent = config.agent.compress_keep_recent
        self._base_path.mkdir(parents=True, exist_ok=True)

        self._default_state = _SessionState()
        prompt_path = Path(config.agent.system_prompt_path)
        if prompt_path.exists():
            self._default_state.system_prompt = Message(role="system", content=prompt_path.read_text(encoding="utf-8"))

        logger.info("SessionPlugin initialized, persistence_path=%s", self._base_path)

    def shutdown(self) -> None:
        self._sessions.clear()
        logger.info("SessionPlugin shut down")


    async def _on_connect(self, ctx: JobContext) -> None:
        self._get_or_load(ctx.session_id)

    async def _on_before_job(self, ctx: JobContext) -> None:
        content = ctx.data.get("content", "")
        if not content:
            return
        state = self._get_or_load(ctx.session_id)
        state.messages.append(Message(role="user", content=content))
        self._append(ctx.session_id, {"role": "user", "content": content})

    async def _on_before_llm(self, ctx: JobContext) -> None:
        state = self._get_or_load(ctx.session_id)

        # Consume queued messages from mid-job user input
        queue = ctx.data.pop("queue_messages", None)
        if queue:
            for content in queue:
                state.messages.append(Message(role="user", content=content))
                self._append(ctx.session_id, {"role": "user", "content": content})

        if self._needs_compression(state):
            if state.cold_loaded:
                logger.warning(
                    "Session %s needs compaction right after cold start — "
                    "consider increasing max_load_messages (currently %d)",
                    ctx.session_id, self._max_load_messages,
                )
                state.cold_loaded = False
            await self._compress(ctx, state)
        ctx.data["messages"] = self._get_messages(state)

    async def _on_after_llm(self, ctx: JobContext) -> None:
        response = ctx.data.get("response")
        if response is None:
            return
        state = self._get_or_load(ctx.session_id)
        state.messages.append(Message(
            role="assistant",
            content=response.text,
            thinking=response.thinking,
            tool_calls=response.tool_calls,
        ))
        if response.usage:
            state.last_prompt_tokens = response.usage.prompt_tokens
        self._append(ctx.session_id, self._response_to_dict(response))

    async def _on_after_tool(self, ctx: JobContext) -> None:
        tool_call = ctx.data.get("tool_call")
        result = ctx.data.get("result", "")
        if tool_call:
            state = self._get_or_load(ctx.session_id)
            state.messages.append(Message(role="tool", content=result, tool_call_id=tool_call.id))
            self._append(ctx.session_id, {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call.id,
            })

    async def _on_compress(self, ctx: JobContext) -> None:
        state = self._get_or_load(ctx.session_id)
        await self._compress(ctx, state)


    def _get_or_load(self, session_id: str | None) -> _SessionState:
        if session_id and session_id not in self._sessions:
            self._sessions[session_id] = self._load_session(session_id)
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self._default_state or _SessionState()

    def _load_session(self, session_id: str) -> _SessionState:
        path = self._session_file(session_id)
        if not path.exists():
            return _SessionState()
        try:
            messages = []
            for line in self._tail_read(path):
                line = line.strip()
                if line:
                    messages.append(self._deserialize_message(json.loads(line)))
            state = _SessionState()
            for msg in messages:
                if msg.role == "system":
                    state.system_prompt = msg
                else:
                    state.messages.append(msg)
            state.cold_loaded = True
            logger.info("Session %s loaded from disk (%d messages)", session_id, len(messages))
            return state
        except Exception:
            logger.warning("Failed to load session %s, starting fresh", session_id, exc_info=True)
        return _SessionState()


    def _append(self, session_id: str | None, msg_dict: dict) -> None:
        if not session_id:
            return
        path = self._session_file(session_id)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg_dict, ensure_ascii=False) + "\n")
        except Exception:
            logger.warning("Failed to append to session file %s", session_id, exc_info=True)

    def _session_file(self, session_id: str) -> Path:
        return self._base_path / f"{session_id}.jsonl"

    def _tail_read(self, path: Path) -> list[str]:
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return []
                n = self._max_load_messages
                lines: list[str] = []
                pos = size
                while pos > 0:
                    chunk_size = min(4096, pos)
                    pos -= chunk_size
                    f.seek(pos)
                    chunk = f.read(chunk_size).decode("utf-8", errors="replace")
                    chunk_lines = chunk.split("\n")
                    if lines:
                        chunk_lines[-1] = chunk_lines[-1] + lines[0]
                        lines = chunk_lines + lines[1:]
                    else:
                        lines = chunk_lines
                    non_empty = [l for l in lines if l]
                    if len(non_empty) > n:
                        return non_empty[-n:]
                return [l for l in lines if l][-n:]
        except Exception:
            logger.warning("Failed to tail-read %s", path, exc_info=True)
            return []


    @staticmethod
    def _get_messages(state: _SessionState) -> list[Message]:
        msgs: list[Message] = []
        if state.system_prompt:
            msgs.append(state.system_prompt)
        msgs.extend(state.messages)
        return msgs

    def _estimate_tokens(self, state: _SessionState) -> int:
        if state.last_prompt_tokens > 0:
            return state.last_prompt_tokens
        total_chars = 0
        for msg in self._get_messages(state):
            if msg.content:
                total_chars += len(msg.content)
            if msg.thinking:
                total_chars += len(msg.thinking)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total_chars += len(tc.name) + len(str(tc.arguments))
        return total_chars // 4

    def _needs_compression(self, state: _SessionState) -> bool:
        return self._estimate_tokens(state) >= int(self._max_tokens * self._compress_threshold)

    async def _compress(self, ctx: JobContext, state: _SessionState) -> None:
        llm = ctx.llm
        if llm is None:
            logger.warning("No llm in context, skipping compression")
            return

        all_messages = self._get_messages(state)
        keep_recent = self._compress_keep_recent
        if len(all_messages) <= keep_recent + 1:
            return

        old = all_messages[1:-keep_recent]
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
            if not response.text:
                return
            summary = response.text
        except Exception:
            logger.warning("Compression LLM call failed", exc_info=True)
            return

        state.messages = [
            Message(role="user", content=f"[Previous conversation summary]\n{summary}"),
        ] + state.messages[-keep_recent:]
        state.last_prompt_tokens = 0
        logger.info("Session compressed: %d messages -> summary + %d recent",
                     len(old), keep_recent)


    @staticmethod
    def _response_to_dict(response: Any) -> dict[str, Any]:
        d: dict[str, Any] = {"role": "assistant"}
        if response.text:
            d["content"] = response.text
        if response.thinking:
            d["thinking"] = response.thinking
        if response.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]
        return d

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
