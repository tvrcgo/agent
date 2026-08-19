from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.plugin import Plugin
from agent.core.loop import AgentContext, Job
from agent.core.events import Event
from agent.core.model import AssistantMessage, SystemMessage, ToolResult, UserMessage, ToolCall


logger = logging.getLogger(__name__)


@dataclass
class _SessionState:
    system_prompt: SystemMessage
    messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult] = field(default_factory=list)
    last_prompt_tokens: int = 0
    cold_loaded: bool = False


class SessionPlugin(Plugin):
    name = "session"

    def __init__(self) -> None:
        self._base_path = Path("./data/sessions")
        self._sessions: dict[str, _SessionState] = {}
        self._system_prompt: SystemMessage | None = None
        self._max_load_messages: int = 100
        self._max_tokens: int = 65536
        self._compress_threshold: float = 0.9
        self._compress_keep_recent: int = 10

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        ctx.on("job_start", self._on_job_start)
        ctx.on("turn_start", self._on_turn_start)
        ctx.on("llm_start", self._on_llm_start)
        ctx.on("llm_end", self._on_llm_end)
        ctx.on("tool_end", self._on_tool_end)
        ctx.on("tool_error", self._on_tool_error)
        ctx.on("cmd_compress", self._on_compress)

        self._max_load_messages = config.get('max_load_messages', 100)
        self._max_tokens = config.get('max_tokens', 65536)
        self._compress_threshold = config.get('compress_threshold', 0.9)
        self._compress_keep_recent = config.get('compress_keep_recent', 10)
        self._base_path.mkdir(parents=True, exist_ok=True)

        prompt_path = Path(config.get('system_prompt_path', 'agent/AGENTS.md'))
        if prompt_path.exists():
            self._system_prompt = SystemMessage(content=prompt_path.read_text(encoding="utf-8"))
        else:
            self._system_prompt = SystemMessage(content="You are an AI agent.")

        logger.info("SessionPlugin initialized, persistence_path=%s", self._base_path)

    def unload(self) -> None:
        self._sessions.clear()
        logger.info("SessionPlugin shut down")

    async def _on_job_start(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None or job.input is None:
            return
        content = job.input.content
        if not content:
            return

        workspace = Path("./workspace") / self._safe_name(job.id)
        workspace.mkdir(parents=True, exist_ok=True)
        job.data["work_dir"] = str(workspace.absolute())

        state = self._get_or_load(job.id)
        state.messages.append(UserMessage(content=content))
        self._append(job.id, {"role": "user", "content": content})

    async def _on_turn_start(self, ctx: AgentContext, evt: Event) -> None:
        # 当前时间作为提示段追加到 job.turn.prompts
        job = evt.job
        if job is None or job.turn is None:
            return
        now = datetime.now().strftime("%Y-%m-%d %A %H:%M:%S")
        job.turn.prompts.append(f"Current time: {now}")

    async def _on_llm_start(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return

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

        # Consume queued steering messages (session 即 job 身份，job.id 即会话)
        if job.turn and job.turn.steering_messages:
            for content in job.turn.steering_messages:
                state.messages.append(UserMessage(content=content))
                self._append(job.id, {"role": "user", "content": content})

        msgs = self._get_messages(state)

        if job.turn and job.turn.prompts:
            extras = "\n\n".join(job.turn.prompts)
            msgs[0] = SystemMessage(content=msgs[0].content + "\n\n" + extras)

        job.data["messages"] = msgs

    async def _on_llm_end(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        response = evt.response
        if response is None:
            return

        state = self._get_or_load(job.id)
        state.messages.append(AssistantMessage(
            content=response.text,
            thinking=response.thinking,
            tool_calls=response.tool_calls,
        ))
        if response.usage:
            state.last_prompt_tokens = response.usage.prompt_tokens
        self._append(job.id, self._response_to_dict(response))


    async def _on_tool_end(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        tool_call = evt.tool_call
        result = evt.data.get("result", "")
        error = evt.data.get("error", "")

        # Persist to session (for all jobs)
        if tool_call:
            state = self._get_or_load(job.id)
            state.messages.append(ToolResult(content=result, tool_call_id=tool_call.id))
            self._append(job.id, {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call.id,
            })

    async def _on_tool_error(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        tool_call = evt.tool_call
        error = evt.data.get("error", "")
        result = f"Error: {error}"
        if tool_call:
            state = self._get_or_load(job.id)
            state.messages.append(ToolResult(content=result, tool_call_id=tool_call.id))
            self._append(job.id, {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call.id,
            })

    async def _on_compress(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
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
                if isinstance(msg, SystemMessage):
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
        return self._base_path / f"{self._safe_name(job_id)}.jsonl"

    @staticmethod
    def _safe_name(name: str) -> str:
        # 子会话 id 含 ":"（Windows 非法），统一替换为合法字符
        return name.replace("/", "--").replace(":", "-")

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
            msg = msgs[i]
            if not isinstance(msg, AssistantMessage) or not msg.tool_calls:
                continue
            expected_ids = {tc.id for tc in msg.tool_calls}
            found_ids: set[str] = set()
            cut_idx = i + 1
            for j in range(i + 1, len(msgs)):
                if isinstance(msgs[j], ToolResult) and msgs[j].tool_call_id:
                    found_ids.add(msgs[j].tool_call_id)
                    cut_idx = j + 1
                else:
                    break
            if found_ids != expected_ids:
                logger.warning("Removing %d orphaned messages at end of session (incomplete tool_calls)",
                               cut_idx - i)
                del msgs[i:cut_idx]

    @staticmethod
    def _get_messages(state: _SessionState) -> list[SystemMessage | UserMessage | AssistantMessage | ToolResult]:
        return [state.system_prompt] + state.messages

    def _estimate_tokens(self, state: _SessionState) -> int:
        if state.last_prompt_tokens > 0:
            return state.last_prompt_tokens
        total_chars = 0
        for msg in self._get_messages(state):
            if msg.content:
                total_chars += len(msg.content)
            if isinstance(msg, AssistantMessage) and msg.thinking:
                total_chars += len(msg.thinking)
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
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
        summary_prompt: list[SystemMessage | UserMessage | AssistantMessage | ToolResult] = [
            SystemMessage(content=(
                "You are a conversation summarizer. Summarize the following conversation "
                "history concisely, preserving all key facts, decisions, actions taken, "
                "and their results. Output only the summary, no preamble."
            )),
            UserMessage(content=formatted),
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
            UserMessage(content=f"[Previous conversation summary]\n{summary}"),
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
    def _keep_iterations(messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult], n: int) -> int:
        count = 0
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], AssistantMessage):
                count += 1
                if count >= n:
                    return len(messages) - i
        return len(messages)

    @staticmethod
    def _format_for_summary(messages: list[SystemMessage | UserMessage | AssistantMessage | ToolResult]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = msg.role
            content = msg.content or ""
            if isinstance(msg, ToolResult) and len(content) > 500:
                content = content[:500] + "..."
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                tc_names = [tc.name for tc in msg.tool_calls]
                content = f"[called: {', '.join(tc_names)}] " + content
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _deserialize_message(d: dict[str, Any]) -> SystemMessage | UserMessage | AssistantMessage | ToolResult:
        role = d["role"]
        if role == "tool":
            return ToolResult(
                content=d.get("content"),
                tool_call_id=d.get("tool_call_id"),
            )
        elif role == "assistant":
            tool_calls = None
            if "tool_calls" in d:
                tool_calls = [
                    ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", {}))
                    for tc in d["tool_calls"]
                ]
            return AssistantMessage(
                content=d.get("content"),
                tool_calls=tool_calls,
                thinking=d.get("thinking"),
            )
        elif role == "system":
            return SystemMessage(content=d.get("content"))
        else:
            return UserMessage(content=d.get("content"))

