"""基座场景化改造的单元测试。

覆盖：registry 完整模块路径加载（场景包 tools/plugins）、依赖定位。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import tempfile

from agent.core.loop import AgentLoop
from agent.core.tool import ToolRegistry
from agent.core.plugin import PluginRegistry
from agent.plugins.session import SessionPlugin


def test_tool_registry_loads_scene_pkg() -> None:
    """带点名称按完整模块路径加载场景包工具。"""
    loop = AgentLoop()
    registry = ToolRegistry(loop.ctx)
    registry.load_modules(["tests.fixtures.scene_pkg.tools.scene_tool"])

    tool = registry.get("scene_hello")
    assert tool is not None, "scene tool not registered"
    assert tool.name == "scene_hello"


def test_tool_registry_builtin_fallback() -> None:
    """不带点名称回退基座内置前缀。"""
    loop = AgentLoop()
    registry = ToolRegistry(loop.ctx)
    registry.load_modules(["read_file"])

    assert registry.get("read_file") is not None


def test_tool_registry_missing_scene_module() -> None:
    """场景包模块不存在时记录错误并跳过，不中断加载。"""
    loop = AgentLoop()
    registry = ToolRegistry(loop.ctx)
    registry.load_modules(["tests.fixtures.scene_pkg.tools.not_exist"])

    assert registry.get("not_exist") is None


def test_check_deps_scene_pkg_requirements() -> None:
    """_check_deps 能从场景包模块目录定位 requirements.txt 并报依赖缺失。"""
    loop = AgentLoop()
    registry = ToolRegistry(loop.ctx)

    try:
        registry._check_deps(
            "scene_dep",
            "tests.fixtures.scene_pkg.tools.scene_dep.dep_tool",
        )
    except ToolRegistry.DependencyError:
        return
    raise AssertionError("expected DependencyError for missing scene dep")


def test_check_deps_builtin_missing_requirements() -> None:
    """内置工具无 requirements.txt 时静默跳过。"""
    loop = AgentLoop()
    registry = ToolRegistry(loop.ctx)

    registry._check_deps("read_file", "agent.tools.read_file")


def test_plugin_registry_loads_scene_pkg() -> None:
    """带点名称按完整模块路径加载场景包插件。"""
    loop = AgentLoop()
    registry = PluginRegistry(loop.ctx)
    registry.load_modules(["tests.fixtures.scene_pkg.plugins.scene_plugin"])

    plugin = registry._plugins.get("scene_plugin")
    assert plugin is not None, "scene plugin not registered"
    assert plugin.loaded is True

    registry.unload_all()


def test_plugin_registry_builtin_fallback() -> None:
    """不带点名称回退基座内置前缀。"""
    loop = AgentLoop()
    registry = PluginRegistry(loop.ctx)
    registry.load_modules(["skill"])

    assert any(p.name == "skill" for p in registry._plugins.values())

    registry.unload_all()


def test_session_plugin_custom_root() -> None:
    """session_root / workspace_root 从插件配置读取。"""
    tmp = Path(tempfile.mkdtemp(prefix="scene-session-"))
    session_root = tmp / "sessions"
    workspace_root = tmp / "workspace"

    loop = AgentLoop()
    plugin = SessionPlugin()
    plugin.load(loop.ctx, {
        "system_prompt_path": "nonexistent.md",
        "session_root": str(session_root),
        "workspace_root": str(workspace_root),
    })

    assert plugin._base_path == session_root
    assert session_root.is_dir(), "session root not created"
    assert plugin._workspace_root == workspace_root

    plugin.unload()


def test_session_plugin_default_root() -> None:
    """不配置时默认值与历史行为一致。"""
    loop = AgentLoop()
    plugin = SessionPlugin()
    plugin.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})

    assert plugin._base_path == Path("./data/sessions")
    assert plugin._workspace_root == Path("./workspace")

    plugin.unload()


async def main() -> None:
    results = {}
    scenarios = [
        ("scene_tool_load", test_tool_registry_loads_scene_pkg),
        ("builtin_tool_fallback", test_tool_registry_builtin_fallback),
        ("missing_scene_module", test_tool_registry_missing_scene_module),
        ("scene_dep_check", test_check_deps_scene_pkg_requirements),
        ("builtin_dep_skip", test_check_deps_builtin_missing_requirements),
        ("scene_plugin_load", test_plugin_registry_loads_scene_pkg),
        ("builtin_plugin_fallback", test_plugin_registry_builtin_fallback),
        ("session_custom_root", test_session_plugin_custom_root),
        ("session_default_root", test_session_plugin_default_root),
    ]
    for name, fn in scenarios:
        try:
            fn()
            results[name] = True
            print(f"  {name}: PASS")
        except Exception as e:
            print(f"  {name}: FAIL - {e}")
            results[name] = False
            import traceback
            traceback.print_exc()

    passed = sum(1 for v in results.values() if v)
    print(f"\nPassed: {passed}/{len(results)}")
    return passed == len(results)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
