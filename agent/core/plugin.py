from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.ws import ClientSession
    from agent.config import Config

logger = logging.getLogger(__name__)


@dataclass
class PluginContext:
    """Context passed to every plugin handler.

    Attributes:
        session_id: The WebSocket session ID (from query string)
        client: The ClientSession for sending events
        data: Mutable dict for plugins to share arbitrary data
    """

    session_id: str | None
    client: ClientSession
    data: dict[str, Any] = field(default_factory=dict)


PluginHandler = Callable[[PluginContext], Awaitable[None]]


class Plugin(ABC):
    """Base class for lifecycle plugins."""

    name: str = ""

    @abstractmethod
    def register(self, registry: PluginRegistry) -> None:
        """Register hooks into the registry."""
        ...

    def init(self, config: Config) -> None:
        """Initialize plugin with config. Override if needed."""
        pass

    def shutdown(self) -> None:
        """Cleanup on shutdown. Override if needed."""
        pass


class PluginRegistry:
    """Registry for lifecycle plugins."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[PluginHandler]] = defaultdict(list)
        self._plugins: dict[str, Plugin] = {}

    def on(self, hook_name: str, handler: PluginHandler) -> None:
        """Register a handler for a hook point."""
        self._hooks[hook_name].append(handler)

    def register(self, plugin: Plugin) -> None:
        """Register a plugin and its hooks."""
        self._plugins[plugin.name] = plugin
        plugin.register(self)
        logger.info("Plugin registered: %s", plugin.name)

    def load_modules(self, module_paths: list[str], config: Config) -> None:
        """Dynamically load plugin modules and register all Plugin subclasses."""
        for module_path in module_paths:
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

    async def emit(self, hook_name: str, ctx: PluginContext) -> None:
        """Trigger all handlers for a hook point in registration order."""
        for handler in self._hooks[hook_name]:
            await handler(ctx)

    def shutdown_all(self) -> None:
        """Shutdown all registered plugins."""
        for plugin in reversed(list(self._plugins.values())):
            plugin.shutdown()
