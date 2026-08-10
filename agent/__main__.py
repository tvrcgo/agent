from __future__ import annotations

import asyncio
import logging

from agent.core.config import load_config
from agent.core.loop import AgentLoop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    logger.info("Config loaded: model[main]=%s", config.model.alias.main)

    loop = AgentLoop(config=config)

    await loop.start()

    logger.info("Agent is running. Waiting for connections...")

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await loop.stop()
        logger.info("Agent shut down.")


if __name__ == "__main__":
    asyncio.run(main())
