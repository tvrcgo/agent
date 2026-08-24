# 场景扩展说明

基于 agent 基座构造垂直场景 agent 的机制说明。

## 架构形态

- **基座**：本仓库（包名 `agent`），提供 core 执行机制 + 内置插件/工具。基座不感知场景，场景资源不侵入 `agent/` 包。
- **场景**：独立目录（自己的 plugins/tools/AGENTS.md/skills），与基座分层存放，COPY 进镜像即可运行，无需打包。
- **部署**：每场景一个容器，`FROM agent-base` 构建；场景间不共享工具。

## 场景目录结构

场景目录直接 COPY 到容器 `/app` 下（与基座 `agent/` 同级），包名即目录名：

```
/app/
├── agent/                      # 基座包（不受场景影响）
└── 场景目录名/                  # 场景根目录（包名即目录名）
    ├── AGENTS.md               # 场景系统提示词
    ├── tools/                  # 场景工具（Tool 子类，需 __init__.py）
    ├── plugins/                # 场景插件（Plugin 子类，可选）
    └── skills/                 # SKILL.md 技能（可选）
```

**保留名**：场景目录名禁止使用 `agent`——基座包安装在 `/app/agent`，同名会覆盖基座。

## 加载机制

无特殊开关，一切按配置项显式声明：

1. **代码**：场景 tool/plugin 写进 config.yml 的 `tools`/`plugins` 列表，用完整模块路径（场景目录名即包名）。容器 CWD 即 `/app`，场景目录可直接 import。
2. **资产**：按普通文件路径引用——`session` 插件的 `system_prompt_path`、`skill` 插件的 `dirs` 直接写场景目录内路径。
3. **依赖**：工具第三方依赖放工具模块目录的 `requirements.txt`，镜像构建时安装；运行时只检查不安装，缺失即启动报错。

## 注册规则

`tools`/`plugins` 列表项名称：

- 不含 `.` → 回退基座内置前缀（`agent.tools.{name}` / `agent.plugins.{name}`）
- 含 `.` → 视为完整模块路径直接 import（场景目录）

## 可配置路径（session 插件）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `session_root` | `./data/sessions` | 会话 JSONL 存储目录（session 插件） |
| `workspace_root` | `./workspace` | 每会话工作目录根（session 插件，按 session id 建子目录） |
| `system_prompt_path` | `agent/AGENTS.md` | 系统提示词文件 |

## 镜像构建

- 基座镜像：根 `Dockerfile` 构建 `agent-base:<版本>`
- 场景镜像：`FROM agent-base:<版本>`，COPY 场景目录与 config.yml 进 `/app` 即可
- 场景工具依赖（`tools/*/requirements.txt`）在场景镜像构建时安装

## 版本约定

- 基座镜像标签：`agent-base:<基座包版本>`
- 场景独立发版，`FROM` 固定基座镜像版本，升级基座需显式更新并回归
