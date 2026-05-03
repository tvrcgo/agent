from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.loop import JobContext
    from agent.core.config import Config

logger = logging.getLogger(__name__)


PluginHandler = Callable[["JobContext"], Awaitable[None]]


class Plugin(ABC):
    """Lifecycle plugin base."""

    name: str = ""

    @abstractmethod
    def register(self, registry: PluginRegistry) -> None:
        """Register hooks into the registry."""
        ...

    def init(self, config: Config) -> None:
        pass

    def shutdown(self) -> None:
        pass


class PluginRegistry:
    """Plugin hook registry."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[PluginHandler]] = defaultdict(list)
        self._plugins: dict[str, Plugin] = {}

    def on(self, hook_name: str, handler: PluginHandler) -> None:
        self._hooks[hook_name].append(handler)

    def register(self, plugin: Plugin) -> None:
        self._plugins[plugin.name] = plugin
        plugin.register(self)
        logger.info("Plugin registered: %s", plugin.name)

    def load_modules(self, names: list[str], config: Config) -> None:
        for name in names:
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
                        plugin.init(config)
                        self.register(plugin)
                    except Exception:
                        logger.error("Failed to instantiate plugin %s.%s", module_path, attr_name, exc_info=True)

    async def emit(self, hook_name: str, ctx: JobContext) -> None:
        for handler in self._hooks[hook_name]:
            await handler(ctx)

    def shutdown_all(self) -> None:
        for plugin in reversed(list(self._plugins.values())):
            plugin.shutdown()
