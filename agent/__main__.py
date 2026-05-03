from __future__ import annotations

import asyncio
import logging

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


async def main() -> None:
    config = load_config()
    logger.info("Config loaded: model=%s, ws=%s:%d", config.model.name, config.ws.host, config.ws.port)

    llm = OpenAIProvider(
        base_url=config.model.base_url,
        api_key=config.model.api_key,
        model=config.model.name,
    )

    skills = SkillRegistry()
    if config.skills:
        skills.load_modules(config.skills)
    skills.load_skills()

    plugins = PluginRegistry()
    if config.plugins:
        plugins.load_modules(config.plugins, config)

    loop = AgentLoop(
        llm=llm,
        skills=skills,
        plugins=plugins,
        max_iterations=config.agent.max_iterations,
        max_concurrent=config.agent.max_concurrent_sessions,
    )

    ws = WebSocketServer(host=config.ws.host, port=config.ws.port)
    ws.on_connect(loop.on_connect)
    ws.on_message(loop.on_message)
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
