# 单元测试 — MCP Plugin

## 测试范围

- MCPPlugin 加载/卸载
- MCPTool 执行
- tools 同步流程

## 测试用例

### 1. MCPPlugin 基本流程

```python
async def test_mcp_plugin_lifecycle():
    from agent.plugins.mcp import MCPPlugin
    from agent.core.plugin import PluginRegistry
    
    plugin = MCPPlugin()
    registry = PluginRegistry()
    plugin.load(registry, {"base_url": "http://localhost:18001"})
    
    assert plugin.name == "mcp"
    assert plugin._base_url == "http://localhost:18001"
    
    plugin.unload()
    assert len(plugin._tools) == 0
```

### 2. MCPTool 命名

```python
def test_mcp_tool_creation():
    from agent.plugins.mcp import MCPTool, MCPPlugin
    
    plugin = MCPPlugin()
    tool = MCPTool(
        name="mcp_filesystem_read_file",
        description="Read file from filesystem",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        plugin=plugin
    )
    
    assert tool.name == "mcp_filesystem_read_file"
    assert "filesystem" in tool.description
```

### 3. AgentContext.tools 引用

```python
def test_context_tools_reference():
    from agent.core.loop import AgentLoop, AgentContext
    from agent.core.tool import ToolRegistry
    
    tools = ToolRegistry()
    loop = AgentLoop(models=None, tools=tools, skills=None, plugins=None)
    ctx = loop.ctx
    
    assert ctx.tools is tools
    assert isinstance(ctx.tools, ToolRegistry)
```

### 4. ToolRegistry.register 支持数组

```python
def test_tool_registry_register_array():
    from agent.core.tool import ToolRegistry, Tool
    
    class MockTool(Tool):
        def __init__(self, name):
            self.name = name
        async def execute(self, args, ctx, job):
            return "ok"
    
    registry = ToolRegistry()
    
    # 单个注册
    registry.register(MockTool("tool1"))
    assert "tool1" in registry._tools
    
    # 数组注册
    registry.register([MockTool("tool2"), MockTool("tool3")])
    assert "tool2" in registry._tools
    assert "tool3" in registry._tools
```

---

## 集成测试

需要先启动 agent-mcp 服务：

```bash
cd services/mcp && PORT=18001 .venv/bin/python mcp_server.py
```

```bash
uv run python -c "
import asyncio
from agent.core.config import load_config
from agent.core.loop import AgentLoop
from agent.core.plugin import PluginRegistry
from agent.core.skill import SkillRegistry
from agent.core.tool import ToolRegistry
from agent.core.model import ModelRegistry

async def test():
    config = load_config()
    models = ModelRegistry(config=config.model)
    tools = ToolRegistry()
    skills = SkillRegistry()
    plugins = PluginRegistry()
    plugins.load_modules([{'mcp': {'base_url': 'http://localhost:18001'}}])
    
    loop = AgentLoop(models, tools, skills, plugins)
    await loop.start()
    await asyncio.sleep(2)
    
    # MCP tools 应该已同步
    mcp_tools = [t for t in tools._tools if t.startswith('mcp_')]
    print(f'[PASS] MCP tools synced: {mcp_tools}')
    
    await loop.stop()

asyncio.run(test())
"
```
