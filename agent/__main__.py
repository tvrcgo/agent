from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from agent.config import load_config
from agent.core.loop import AgentLoop
from agent.core.memory import ShortTermMemory
from agent.core.plugin import PluginRegistry
from agent.core.ws import WebSocketServer
from agent.providers.openai import OpenAIProvider
from agent.skills.echo import EchoSkill

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_system_prompt(path: str = "AGENTS.md") -> str:
    p = Path(path)
    if p.exists():
        return p.read_text()
    return ""


async def main() -> None:
    config = load_config()
    logger.info("Config loaded: model=%s, ws=%s:%d", config.model.name, config.ws.host, config.ws.port)

    # Provider
    provider = OpenAIProvider(
        base_url=config.model.base_url,
        api_key=config.model.api_key,
        model=config.model.name,
    )

    # Plugin registry
    registry = PluginRegistry()

    # Memory (registered as plugin)
    memory = ShortTermMemory(window_size=config.agent.window_size)
    system_prompt = load_system_prompt()
    if system_prompt:
        memory.set_system_prompt(system_prompt)
    registry.register(memory)

    # Skills
    registry.register(EchoSkill())

    await registry.init_all(None)

    # Agent loop
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        memory=memory,
        max_iterations=config.agent.max_iterations,
    )

    # WebSocket server
    ws = WebSocketServer(host=config.ws.host, port=config.ws.port)
    ws.on_message(loop.handle_message)
    await ws.start()

    logger.info("Agent is running. Waiting for connections...")

    try:
        await asyncio.Future()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await ws.stop()
        await registry.shutdown_all()
        await provider.close()
        logger.info("Agent shut down.")


if __name__ == "__main__":
    asyncio.run(main())
