from __future__ import annotations

from agent.core.plugin import Plugin


class ScenePlugin(Plugin):
    name = "scene_plugin"

    def load(self, ctx, config: dict = {}) -> None:
        self.loaded = True
