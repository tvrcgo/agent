# E2E 测试 — playground 全链路

## 背景

在 playground 页面（`playground/index.html`）通过真实 WebSocket 连接 agent 服务，用真实 LLM（deepseek:v4-pro + flash）验证完整链路：消息输入 → LLM 推理 → 工具调用/确认 → 输出渲染 → 持久化。fake LLM 的链路级验证在 `scripts/test_e2e.py`（无网络依赖，先跑）。

## 前置条件

- agent 服务运行于 `ws://127.0.0.1:8765`（`config.yml` 的 `alias.main` 指向可用 LLM）
- 浏览器用 playwright 驱动，`file:///D:/Code/tvrcgo-agent/playground/index.html`
- 会话 ID 随机生成（页面 `+` 新建），避免历史数据干扰

## 场景列表

| # | 场景 | 步骤 | 预期 |
|---|---|---|---|
| 1 | 会话创建与连接 | 点 `+` 新建会话 | 状态点 `connected`、输入框可用 |
| 2 | 流式问答 | 发「1+1等于几？只要答案」 | thinking/message 流式渲染，最终 message 非空 |
| 3 | 安全工具调用 | 发「用 shell 执行 echo hello-e2e」 | `tool_call` → `tool_result` 均渲染，无 confirm 弹窗 |
| 4 | 危险工具 confirm | 发「用 shell 执行 del /f /s /q C:\Windows」 | `confirm` 弹窗出现，Approve/Deny 按钮可点 |
| 5 | Deny 阻断链路 | 点 Deny | 失败 tool_result 渲染（failed 样式），LLM 汇报已拒绝，最终 status done |
| 6 | Approve 放行链路 | 再发危险命令，点 Approve | 工具真实执行，tool_result 正常，status done |
| 7 | 会话持久化 | 刷新页面 | 历史消息完整渲染、WS 自动重连 |
| 8 | 多轮上下文延续 | 新消息引用上一轮内容 | LLM 正确引用历史上下文 |
| 9 | /cancel 命令 | 发长任务后输入 `/cancel` | job 终止，status cancelled |
| 10 | /compress 命令 | 输入 `/compress` | 不报错，服务正常（压缩逻辑执行） |
| 11 | subjob 任务树 | 发「用 subjob 查 1+1 和 2+2」 | `data` jobs 事件渲染，任务树显示父子关系 |

## 验证点

- [ ] 所有场景无异常（页面无 error 事件、console 无报错）
- [ ] status 终态为 `done` / `cancelled`（无 `error`）
- [ ] confirm 弹窗 approve/deny 决策正确回传（`cmd_confirm` 链路）
- [ ] tool_call 与 tool_result 渲染配对（嵌套在 collapsible 内）
- [ ] 刷新后历史消息完整（sql.js 持久化 + 会话 JSONL）

## 备注

- fake LLM E2E（`scripts/test_e2e.py`）覆盖：echo 工具链路、subjob 递归聚合、流式、tool_guard、confirm 决策、取消、截断、max_iterations、异常路径、排队——先跑 fake，再跑本清单
- 本清单依赖真实 LLM 输出引导工具调用，若 LLM 未按预期调用工具，重试一次再判失败
