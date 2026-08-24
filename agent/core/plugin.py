from __future__ import annotations

import importlib
import logging
from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.loop import AgentContext

logger = logging.getLogger(__name__)


class Plugin(ABC):

    name: str = ""

    def load(self, ctx: "AgentContext", config: dict = {}) -> None:
        pass

    def unload(self) -> None:
        pass


class PluginRegistry:

    def __init__(self, ctx: "AgentContext") -> None:
        self._ctx = ctx
        self._plugins: dict[str, Plugin] = {}

    def load_modules(self, items: list[str | dict]) -> None:
        for item in items:
            if isinstance(item, str):
                name, config = item, {}
            else:
                name = next(iter(item))
                config = item[name] or {}

            # 名称含 "." 视为完整模块路径（场景目录），否则回退基座内置前缀
            module_path = name if "." in name else f"agent.plugins.{name}"
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
                        plugin.load(self._ctx, config)
                        logger.info("Plugin registered: %s", plugin.name)
                    except Exception:
                        logger.error("Failed to instantiate plugin %s.%s", module_path, attr_name, exc_info=True)

    def unload_all(self) -> None:
        for plugin in reversed(list(self._plugins.values())):
            plugin.unload()
