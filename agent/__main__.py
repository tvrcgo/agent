from __future__ import annotations

import asyncio
import logging

from agent.core.config import load_config
from agent.core.loop import AgentLoop
from agent.core.plugin import PluginRegistry
from agent.core.skill import SkillRegistry
from agent.core.tool import ToolRegistry
from agent.core.model import ModelRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    logger.info("Config loaded: model[main]=%s", config.model.alias.main)

    models = ModelRegistry(config=config.model)

    tools = ToolRegistry()
    if config.tools:
        tools.load_modules(config.tools)

    skills = SkillRegistry()
    skills.load_skills("agent/skills", "skills")

    plugins = PluginRegistry()
    if config.plugins:
        plugins.load_modules(config.plugins)

    loop = AgentLoop(
        models=models,
        tools=tools,
        skills=skills,
        plugins=plugins,
        config=config.agent,
    )

    await loop.start()

    logger.info("Agent is running. Waiting for connections...")

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await loop.stop()
        plugins.unload_all()
        await models.close()
        logger.info("Agent shut down.")


if __name__ == "__main__":
    asyncio.run(main())
