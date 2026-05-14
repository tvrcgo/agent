from __future__ import annotations

import importlib
import logging
from abc import ABC
from collections import defaultdict
from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

PluginHandler = Callable[["AgentContext", "Job | None"], Awaitable[None]]


class Plugin(ABC):

    name: str = ""

    def load(self, registry: PluginRegistry, config: dict = {}) -> None:
        pass

    def unload(self) -> None:
        pass


class PluginRegistry:

    def __init__(self) -> None:
        self._handlers: dict[str, list[PluginHandler]] = defaultdict(list)
        self._plugins: dict[str, Plugin] = {}

    def on(self, hook_name: str, handler: PluginHandler) -> None:
        self._handlers[hook_name].append(handler)

    async def emit(self, hook_name: str, ctx: AgentContext, job: Job | None) -> None:
        for handler in self._handlers[hook_name]:
            await handler(ctx, job)

    def load_modules(self, items: list[str | dict]) -> None:
        for item in items:
            if isinstance(item, str):
                name, config = item, {}
            else:
                name = next(iter(item))
                config = item[name] or {}

            module_path = f"agent.plugins.{name}"
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                logger.error("Failed to import plugin module: %s", module_path, exc_info=True)
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Plugin)
                    and attr is not Plugin
                ):
                    try:
                        plugin = attr()
                        self._plugins[plugin.name] = plugin
                        plugin.load(self, config)
                        logger.info("Plugin registered: %s", plugin.name)
                    except Exception:
                        logger.error("Failed to instantiate plugin %s.%s", module_path, attr_name, exc_info=True)

    def unload_all(self) -> None:
        for plugin in reversed(list(self._plugins.values())):
            plugin.unload()
