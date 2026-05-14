from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import AgentContext, Job, MessageEvent
from agent.core.model import Message, ToolCall


logger = logging.getLogger(__name__)


@dataclass
class _SessionState:
    system_prompt: Message
    messages: list[Message] = field(default_factory=list)
    last_prompt_tokens: int = 0
    cold_loaded: bool = False


class SessionPlugin(Plugin):
    name = "session"

    def __init__(self) -> None:
        self._base_path = Path("./data/sessions")
        self._sessions: dict[str, _SessionState] = {}
        self._system_prompt: Message | None = None
        self._max_load_messages: int = 100
        self._max_tokens: int = 65536
        self._compress_threshold: float = 0.9
        self._compress_keep_recent: int = 10

    def load(self, registry: PluginRegistry, config: dict = {}) -> None:
        registry.on("before_job", self._on_before_job)
        registry.on("before_llm", self._on_before_llm)
        registry.on("after_llm", self._on_after_llm)
        registry.on("before_tool", self._on_before_tool)
        registry.on("before_tools", self._on_before_tools)
        registry.on("after_tool", self._on_after_tool)
        registry.on("after_job", self._on_after_job)
        registry.on("command:compress", self._on_compress)

        self._max_load_messages = config.get('max_load_messages', 100)
        self._max_tokens = config.get('max_tokens', 65536)
        self._compress_threshold = config.get('compress_threshold', 0.9)
        self._compress_keep_recent = config.get('compress_keep_recent', 10)
        self._base_path.mkdir(parents=True, exist_ok=True)

        prompt_path = Path(config.get('system_prompt_path', 'agent/AGENTS.md'))
        if prompt_path.exists():
            self._system_prompt = Message(role="system", content=prompt_path.read_text(encoding="utf-8"))
        else:
            self._system_prompt = Message(role="system", content="You are an AI agent.")

        logger.info("SessionPlugin initialized, persistence_path=%s", self._base_path)

    def unload(self) -> None:
        self._sessions.clear()
        logger.info("SessionPlugin shut down")

    async def _on_before_job(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None or job.input is None:
            return
        content = job.input.content
        if not content:
            return
        # Each job writes to its own storage (job.id)
        # Sub-jobs get empty context, only their own input message
        state = self._get_or_load(job.id)
        state.messages.append(Message(role="user", content=content))
        self._append(job.id, {"role": "user", "content": content})

    async def _on_before_llm(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None or job.output is None:
            return

        # Only send status from root job
        if job.id == job.session_id:
            job.output.events.append(MessageEvent(type="status", content="thinking"))
            await ctx.emit("on_output", job)

        state = self._get_or_load(job.id)

        if self._needs_compression(state):
            if state.cold_loaded:
                logger.warning(
                    "Session %s needs compaction right after cold start — "
                    "consider increasing max_load_messages (currently %d)",
                    job.id, self._max_load_messages,
                )
                state.cold_loaded = False
            await self._compress(ctx, state, job)

        # Only root job consumes queued messages
        if job.id == job.session_id and job.loop and job.loop.queue_messages:
            for content in job.loop.queue_messages:
                state.messages.append(Message(role="user", content=content))
                self._append(job.id, {"role": "user", "content": content})

        msgs = self._get_messages(state)
        now = datetime.now().strftime("%Y-%m-%d %A %H:%M:%S")
        msgs[0] = Message(role="system", content=msgs[0].content + f"\n\nCurrent time: {now}")

        if job.loop and job.loop.skills_prompt:
            msgs[0] = Message(role="system", content=msgs[0].content + "\n\n" + job.loop.skills_prompt)

        job.data["messages"] = msgs

    async def _on_after_llm(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        response = job.data.get("response")
        if response is None:
            return

        # Only emit thinking content from root job
        if response.thinking and job.output is not None and job.id == job.session_id:
            job.output.events.append(MessageEvent(type="status", content="thinking", data={"content": response.thinking}))
            await ctx.emit("on_output", job)

        state = self._get_or_load(job.id)
        state.messages.append(Message(
            role="assistant",
            content=response.text,
            thinking=response.thinking,
            tool_calls=response.tool_calls,
        ))
        if response.usage:
            state.last_prompt_tokens = response.usage.prompt_tokens
        self._append(job.id, self._response_to_dict(response))


    async def _on_before_tool(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None or job.output is None:
            return
        # Only send tool_call event from root job
        if job.id != job.session_id:
            return
        tool_call = job.data.get("tool_call")
        if tool_call:
            job.output.events.append(MessageEvent(type="data", data={
                "name": "tool_call",
                "id": tool_call.id,
                "tool": tool_call.name,
                "arguments": tool_call.arguments,
            }))
            await ctx.emit("on_output", job)

    async def _on_after_tool(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        tool_call = job.data.get("tool_call")
        result = job.data.get("result", "")

        # Only send tool_result event from root job
        if tool_call and job.output is not None and job.id == job.session_id:
            job.output.events.append(MessageEvent(type="data", data={
                "name": "tool_result",
                "id": tool_call.id,
                "tool": tool_call.name,
                "result": result,
                "error": job.data.get("error", ""),
            }))
            await ctx.emit("on_output", job)

        # Persist to session (for all jobs)
        if tool_call:
            state = self._get_or_load(job.id)
            state.messages.append(Message(role="tool", content=result, tool_call_id=tool_call.id))
            self._append(job.id, {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call.id,
            })

    async def _on_compress(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        state = self._get_or_load(job.id)
        await self._compress(ctx, state, job)

    def _get_or_load(self, job_id: str) -> _SessionState:
        if job_id not in self._sessions:
            self._sessions[job_id] = self._load_session(job_id)
        return self._sessions[job_id]

    def _load_session(self, job_id: str) -> _SessionState:
        path = self._session_file(job_id)
        if not path.exists():
            return _SessionState(system_prompt=self._system_prompt)
        try:
            messages = []
            for line in self._tail_read(path):
                line = line.strip()
                if line:
                    messages.append(self._deserialize_message(json.loads(line)))
            state = _SessionState(system_prompt=self._system_prompt)
            for msg in messages:
                if msg.role == "system":
                    state.system_prompt = msg
                else:
                    state.messages.append(msg)
            self._clean_orphan_tool_calls(state)
            state.cold_loaded = True
            logger.info("Session %s loaded from disk (%d messages)", job_id, len(messages))
            return state
        except Exception:
            logger.warning("Failed to load session %s, starting fresh", job_id, exc_info=True)
        return _SessionState(system_prompt=self._system_prompt)

    def _append(self, job_id: str, msg_dict: dict) -> None:
        path = self._session_file(job_id)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg_dict, ensure_ascii=False) + "\n")
        except Exception:
            logger.warning("Failed to append to session file %s", job_id, exc_info=True)

    def _session_file(self, job_id: str) -> Path:
        return self._base_path / f"{job_id.replace('/', '--')}.jsonl"

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
    def _clean_orphan_tool_calls(state: _SessionState) -> None:
        msgs = state.messages
        for i in range(len(msgs) - 1, -1, -1):
            if not msgs[i].tool_calls:
                continue
            expected_ids = {tc.id for tc in msgs[i].tool_calls}
            found_ids: set[str] = set()
            cut_idx = i + 1
            for j in range(i + 1, len(msgs)):
                if msgs[j].role == "tool" and msgs[j].tool_call_id:
                    found_ids.add(msgs[j].tool_call_id)
                    cut_idx = j + 1
                else:
                    break
            if found_ids != expected_ids:
                logger.warning("Removing %d orphaned messages at end of session (incomplete tool_calls)",
                               cut_idx - i)
                del msgs[i:cut_idx]

    @staticmethod
    def _get_messages(state: _SessionState) -> list[Message]:
        msgs = [state.system_prompt]
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

    async def _compress(self, ctx: AgentContext, state: _SessionState, job: Job) -> None:
        llm = ctx.models.get("flash")
        if llm is None:
            logger.warning("No llm in context, skipping compression")
            return

        all_messages = self._get_messages(state)
        keep_recent = self._compress_keep_recent
        if len(all_messages) <= keep_recent + 1:
            return

        safe_keep = self._keep_iterations(state.messages, keep_recent)

        old = all_messages[1:-safe_keep]
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
            logger.warning("Compression model call failed", exc_info=True)
            return

        state.messages = [
            Message(role="user", content=f"[Previous conversation summary]\n{summary}"),
        ] + state.messages[-safe_keep:]
        state.last_prompt_tokens = 0
        logger.info("Session compressed: %d messages -> summary + %d recent",
                     len(old), safe_keep)

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
    def _keep_iterations(messages: list[Message], n: int) -> int:
        count = 0
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "assistant":
                count += 1
                if count >= n:
                    return len(messages) - i
        return len(messages)

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

    async def _on_before_tools(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None or job.output is None:
            return
        # Only send status from root job
        if job.id != job.session_id:
            return
        job.output.events.append(MessageEvent(type="status", content="acting"))
        await ctx.emit("on_output", job)

    async def _on_after_job(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None or job.output is None:
            return

        # Only send final message/status from root job
        if job.id != job.session_id:
            return

        if job.output.content:
            job.output.events.append(MessageEvent(type="message", content=job.output.content))

        if job.status == "error":
            job.output.events.append(MessageEvent(type="status", content="error", data={"reason": job.data.get("reason", "Unknown error")}))
        elif job.status == "cancelled":
            job.output.events.append(MessageEvent(type="status", content="cancelled"))
        else:
            job.output.events.append(MessageEvent(type="status", content="done"))

        await ctx.emit("on_output", job)