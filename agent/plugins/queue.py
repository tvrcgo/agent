"""Queue Plugin - Redis-based input/output message queue."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

import redis.asyncio as redis

from agent.core.plugin import Plugin
from agent.core.loop import AgentContext, InputMessage, Job, MessageEvent
from agent.core.events import Event


logger = logging.getLogger(__name__)

RETRY_INTERVAL = 5


class QueuePlugin(Plugin):
    """Plugin that bridges Redis queues with agent input/output."""

    name = "queue"

    def __init__(self) -> None:
        self._redis_url: str = "redis://localhost:6379"
        self._input_queue: str = "agent:input"
        self._output_queue: str = "agent:output"
        self._redis: redis.Redis | None = None
        self._consume_task: asyncio.Task[None] | None = None
        self._ctx: AgentContext | None = None

    def load(self, ctx: AgentContext, config: dict[str, Any] = {}) -> None:
        self._redis_url = config.get("redis_url", "redis://localhost:6379")
        self._input_queue = config.get("input_queue", "agent:input")
        self._output_queue = config.get("output_queue", "agent:output")

        ctx.on("agent_start", self._on_start)
        ctx.on("agent_stop", self._on_stop)
        ctx.on("msg_output", self._on_output)

        logger.info(
            "QueuePlugin loaded, redis_url=%s, input=%s, output=%s",
            self._redis_url, self._input_queue, self._output_queue
        )

    def unload(self) -> None:
        self._redis = None
        logger.info("QueuePlugin shut down")

    async def _on_start(self, ctx: AgentContext, evt: Event) -> None:
        self._ctx = ctx
        try:
            self._redis = redis.from_url(self._redis_url)
            await self._redis.ping()
            self._consume_task = asyncio.create_task(self._consume_loop())
            logger.info("QueuePlugin started")
        except Exception as e:
            logger.warning("Failed to connect to Redis: %s, will retry", e)
            self._consume_task = asyncio.create_task(self._retry_connect_loop())

    async def _retry_connect_loop(self) -> None:
        while True:
            try:
                self._redis = redis.from_url(self._redis_url)
                await self._redis.ping()
                logger.info("Redis reconnected")
                self._consume_task = asyncio.create_task(self._consume_loop())
                return
            except Exception as e:
                logger.warning("Redis connection retry failed: %s", e)
                await asyncio.sleep(RETRY_INTERVAL)

    async def _on_stop(self, ctx: AgentContext, evt: Event) -> None:
        if self._consume_task:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass
            self._consume_task = None

        if self._redis:
            await self._redis.aclose()
            self._redis = None
        logger.info("QueuePlugin stopped")

    async def _consume_loop(self) -> None:
        while True:
            try:
                result = await self._redis.blpop(self._input_queue, timeout=0)
                if result is None:
                    continue
                _, data = result
                await self._handle_message(data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Error consuming from queue: %s", e)
                await asyncio.sleep(1)

    async def _handle_message(self, data: bytes) -> None:
        if self._ctx is None:
            return
        try:
            msg = json.loads(data)
            content = msg.get("content", "")
            session_id = msg.get("session_id", "")
            if not session_id:
                logger.warning("Message missing session_id: %s", data)
                return

            input_msg = InputMessage(content=content, session_id=session_id)
            job = Job(
                id=session_id,
                session_id=session_id,
                status="pending",
                input=input_msg,
            )
            await self._ctx.emit("msg_input", job=job)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse input message: %s", e)
        except Exception as e:
            logger.warning("Failed to handle input message: %s", e)

    async def _on_output(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None or job.output is None or self._redis is None:
            return

        events = job.output.events
        job.output.events = []
        if not events:
            return

        payload = {
            "session_id": job.session_id,
            "content": job.output.content,
            "events": [asdict(e) if isinstance(e, MessageEvent) else e for e in events],
            "turns": [asdict(t) for t in job.output.turns],
        }
        try:
            await self._redis.rpush(self._output_queue, json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.warning("Failed to push output to queue: %s", e)
