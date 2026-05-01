# Regression Test Checklist

每次大改动后逐项验证，确保核心行为不变。

## 单元测试 — SessionMemory

### 1. max_context_messages 读限制
- [ ] `get_messages()` 返回 system prompt + 最多 N 条最近消息
- [ ] system prompt 始终在第一位
- [ ] 消息数少于限制时全量返回

### 2. token 估算
- [ ] 无 API 数据时走字符估算 (`total_chars // 4`)
- [ ] 有 `_last_prompt_tokens` 时返回 API 实际值
- [ ] `thinking` 和 `tool_calls` 内容计入估算

### 3. needs_compression 阈值
- [ ] 低于 `max_tokens * compress_threshold` 返回 `False`
- [ ] 达到 / 超过阈值时返回 `True`

### 4. compact() 行为
- [ ] 消息数 > `keep_recent` 时：旧消息替换为 1 条摘要 + 保留最近 N 条
- [ ] 摘要消息以 `[Previous conversation summary]` 开头
- [ ] 消息数 ≤ `keep_recent` 时 no-op
- [ ] `_last_prompt_tokens` 重置为 0

### 5. 持久化 round-trip
- [ ] 摘要消息正常保存和恢复
- [ ] `tool_calls` 正常保存和恢复
- [ ] `thinking` 正常保存和恢复
- [ ] 文件命名格式 `<session_id>.jsonl`

### 6. _format_for_summary
- [ ] tool 角色结果超过 500 字符时截断
- [ ] 包含 tool_calls 的消息标注 `[called: name]`

### 7. 边界情况
- [ ] 空内存不报错
- [ ] 只有 system prompt 不触发压缩
- [ ] `max_context_messages` 大于存储量时正常返回
- [ ] `keep_recent=0` 时 compact 只保留摘要

## 集成测试 — WebSocket

### 8. 基本 Echo 任务
- [ ] 发送 task，收到 status → thinking → acting → tool_call → tool_result → message → done
- [ ] 最终收到 message 事件包含 echo 结果

### 9. 多轮工具调用
- [ ] 连续多次工具调用正常执行
- [ ] 每次工具调用的结果正确传回 LLM

### 10. 会话持久化
- [ ] WebSocket 断开后 session 文件写入 `data/memory/`
- [ ] 重连后恢复之前的上下文

### 11. 多会话并发
- [ ] 两个不同 session_id 的 WebSocket 连接可并发执行任务
- [ ] 两个会话的上下文不交叉污染

### 12. 长程任务
- [ ] 5 次以上迭代不中断
- [ ] 最终正确完成任务

### 13. reasoning_content 回传（DeepSeek）
- [ ] 非首轮 API 调用 200 OK，不出现 400 Bad Request
- [ ] assistant 消息中 `reasoning_content` 字段正确回传

## 快速验证

```bash
# 单元测试
uv run python -c "
from agent.plugins.session import SessionMemory
from agent.core.llm import Message
import json

# 1. read limit
m = SessionMemory(max_context_messages=5)
m.set_system_prompt('You are helpful.')
for i in range(20):
    m.add_user_message(f'msg {i}')
    m.add_assistant_message(content=f'resp {i}')
msgs = m.get_messages()
assert msgs[0].role == 'system'
assert len(msgs) <= 6
assert msgs[-1].content == 'resp 19'
print('[PASS] 1. read limit')

# 2. token estimation
m = SessionMemory(max_tokens=1000)
m.set_system_prompt('sys')
for i in range(5):
    m.add_user_message('hello ' * 50)
    m.add_assistant_message(content='world ' * 50)
assert m.estimate_tokens() > 0
m.set_last_prompt_tokens(1234)
assert m.estimate_tokens() == 1234
print('[PASS] 2. token estimation')

# 3. threshold
m = SessionMemory(max_tokens=1000, compress_threshold=0.9)
m.set_last_prompt_tokens(500)
assert not m.needs_compression()
m.set_last_prompt_tokens(900)
assert m.needs_compression()
print('[PASS] 3. threshold')

# 4. compact
m = SessionMemory(keep_recent=5)
for i in range(20):
    m.add_user_message(f'msg {i}')
    m.add_assistant_message(content=f'resp {i}')
m.compact('summary')
assert len(m._messages) == 6
assert m._messages[0].content.startswith('[Previous conversation summary]')
assert m._last_prompt_tokens == 0
print('[PASS] 4. compact')

# 5. compact no-op (<= keep_recent)
m = SessionMemory(keep_recent=10)
m.add_user_message('only one')
m.compact('summary')
assert len(m._messages) == 1
print('[PASS] 5. compact no-op')

# 6. edge cases
m = SessionMemory()
assert not m.needs_compression()
assert len(m.get_messages()) == 0
m.set_system_prompt('sys')
assert len(m.get_messages()) == 1
m = SessionMemory(keep_recent=0)
for i in range(5): m.add_user_message(f'msg {i}')
m.compact('summary')
assert len(m._messages) == 1
print('[PASS] 6. edge cases')

print('All unit tests passed')
"

# 集成测试（需要 agent 在运行）
uv run python tests/test_ws.py
```
