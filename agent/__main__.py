from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from agent.core.config import load_config
from agent.core.loop import AgentLoop
from agent.core.plugin import PluginRegistry
from agent.core.skill import SkillRegistry
from agent.core.ws import WebSocketServer
from agent.core.llm import OpenAIProvider

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

    # LLM
    llm = OpenAIProvider(
        base_url=config.model.base_url,
        api_key=config.model.api_key,
        model=config.model.name,
    )

    # Skills (tools for LLM)
    skills = SkillRegistry()
    if config.skills.modules:
        skills.load_modules(config.skills.modules)

    # Plugins (lifecycle hooks)
    plugins = PluginRegistry()
    if config.plugins.modules:
        plugins.load_modules(config.plugins.modules, config)

    # Agent loop
    loop = AgentLoop(
        llm=llm,
        skills=skills,
        plugins=plugins,
        max_iterations=config.agent.max_iterations,
    )
    loop._max_concurrent = config.agent.max_concurrent_sessions

    # WebSocket server
    ws = WebSocketServer(host=config.ws.host, port=config.ws.port)
    ws.on_connect(loop.on_connect)
    ws.on_message(loop.handle_message)
    ws.on_disconnect(loop.on_disconnect)
    await ws.start()

    logger.info("Agent is running. Waiting for connections...")

    try:
        await asyncio.Future()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await ws.stop()
        plugins.shutdown_all()
        await llm.close()
        logger.info("Agent shut down.")


if __name__ == "__main__":
    asyncio.run(main())