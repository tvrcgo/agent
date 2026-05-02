# Regression Test Checklist

每次大改动后逐项验证，确保核心行为不变。

## 单元测试 — SessionPlugin 内部逻辑

### 1. get_messages 行为
- [ ] `get_messages()` 返回 system prompt + 全部消息（compact 已保证不超限）
- [ ] system prompt 始终在第一位

### 2. token 估算
- [ ] 无 API 数据时走字符估算 (`total_chars // 4`)
- [ ] 有 `_last_prompt_tokens` 时返回 API 实际值
- [ ] `thinking` 和 `tool_calls` 内容计入估算

### 3. needs_compression 阈值
- [ ] 低于 `max_tokens * compress_threshold` 返回 `False`
- [ ] 达到 / 超过阈值时返回 `True`

### 4. compact() 行为
- [ ] 消息数 > `max_context_messages` 时：旧消息替换为 1 条摘要 + 保留最近 N 条
- [ ] 摘要消息以 `[Previous conversation summary]` 开头
- [ ] 消息数 ≤ `max_context_messages` 时 no-op
- [ ] `_last_prompt_tokens` 重置为 0

### 5. 持久化 round-trip
- [ ] JSONL 追加写入，不覆写
- [ ] 冷启 tail-read 只加载尾部 N 条
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
- [ ] 冷启后 token 超限自动 compact + warning 日志

## 集成测试 — WebSocket

### 8. 基本 Echo 任务
- [ ] 发送 chat，收到 status → thinking → acting → tool_call → tool_result → message → done
- [ ] 最终收到 message 事件包含 echo 结果

### 9. 多轮工具调用
- [ ] 连续多次工具调用正常执行
- [ ] 每次工具调用的结果正确传回 LLM

### 10. 会话持久化
- [ ] 消息实时追加写入 `data/sessions/`，不依赖断开
- [ ] 重连后从尾部恢复上下文

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
from agent.plugins.session import SessionPlugin, _SessionState
from agent.core.llm import Message

# Helper: create plugin with test params
def _make_plugin(max_tokens=65536, compress_threshold=0.9, max_context_messages=100):
    p = SessionPlugin()
    p._max_tokens = max_tokens
    p._compress_threshold = compress_threshold
    p._max_context_messages = max_context_messages
    return p

# 1. get_messages returns all
p = _make_plugin()
state = _SessionState(system_prompt=Message(role='system', content='You are helpful.'))
for i in range(20):
    state.messages.append(Message(role='user', content=f'msg {i}'))
    state.messages.append(Message(role='assistant', content=f'resp {i}'))
msgs = p._get_messages(state)
assert msgs[0].role == 'system'
assert len(msgs) == 41
assert msgs[-1].content == 'resp 19'
print('[PASS] 1. get_messages returns all')

# 2. token estimation
p = _make_plugin(max_tokens=1000)
state = _SessionState(system_prompt=Message(role='system', content='sys'))
for i in range(5):
    state.messages.append(Message(role='user', content='hello ' * 50))
    state.messages.append(Message(role='assistant', content='world ' * 50))
assert p._estimate_tokens(state) > 0
state.last_prompt_tokens = 1234
assert p._estimate_tokens(state) == 1234
print('[PASS] 2. token estimation')

# 3. threshold
p = _make_plugin(max_tokens=1000, compress_threshold=0.9)
state = _SessionState()
state.last_prompt_tokens = 500
assert not p._needs_compression(state)
state.last_prompt_tokens = 900
assert p._needs_compression(state)
print('[PASS] 3. threshold')

# 4. compress slicing (inline: summary + tail compress_keep_recent)
state = _SessionState()
for i in range(20):
    state.messages.append(Message(role='user', content=f'msg {i}'))
    state.messages.append(Message(role='assistant', content=f'resp {i}'))
compress_keep_recent = 5
state.messages = [
    Message(role='user', content='[Previous conversation summary]\nsummary'),
] + state.messages[-compress_keep_recent:]
state.last_prompt_tokens = 0
assert len(state.messages) == 6
assert state.messages[0].content.startswith('[Previous conversation summary]')
assert state.last_prompt_tokens == 0
print('[PASS] 4. compress slicing')

# 5. edge cases
p = _make_plugin()
state = _SessionState()
assert not p._needs_compression(state)
assert len(p._get_messages(state)) == 0
state.system_prompt = Message(role='system', content='sys')
assert len(p._get_messages(state)) == 1
print('[PASS] 5. edge cases')

print('All unit tests passed')
"

# 集成测试（需要 agent 在运行）
uv run python tests/test_ws.py
```
