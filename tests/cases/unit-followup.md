# 单元测试 — Follow-up 多轮输出

## 背景

用户 follow-up 消息（追加/纠正确认）在 agent 运行中排队，下轮迭代开始时被消费。此时会产生**多轮文本轮**（如「先回答 A，用户再说 B，再回答 B」）。回归验证这些场景下的输出消息组织。

文本统一放在 `job.turn.content`（每轮重建、天然清零、工具轮为空）。输出经 `msg_output` 事件增量外发——载荷 `output` 直接携带单个 `OutputMessage`（`type`/`content`/`data`/`session_id`），与 `InputMessage` 对应构成 I/O 规范，消息自包含 session 归属。

## 测试用例

### 1. 多轮文本轮语义

- [ ] 多轮文本轮后 `job.turn.content` 保持**最后一个 turn 输出**（不拼接、不覆盖历史字段语义）
- [ ] job 状态保持 `done`（follow-up 不代表 job 已完成，不改变状态机）

### 2. 非流式逐轮独立推送

- [ ] SessionPlugin 在 `turn_end` 时，每轮有文本（`job.turn.content`）则推送一条独立 `message` 事件
- [ ] 多条消息**顺序保留**，体现先后关系
- [ ] `job_end` 只发终态 status，不重复兜底推送 message

### 3. 流式不重复

- [ ] 流式模式下 `turn_end` 不推送 `message`（文本已由 `stream` 事件实时渲染）
- [ ] 流式下也没有多余 message 兜底，避免前端重复展示

### 4. 单轮

- [ ] 单轮文本轮：`job.turn.content` 即该轮文本，`turn_end` 推一条 `message`

### 5. 工具轮后接文本轮

- [ ] 工具轮（无文本）不产生 `message`，`turn_end` 读到空 `content` 跳过
- [ ] 之后文本轮正常推一条 `message`，无上一轮残留误推

---

```bash
uv run python tests/scripts/test_loop_followup.py
```
