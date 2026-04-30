# Regression Test Checklist

每次大改动后逐项验证，确保核心行为不变。

## SessionMemory

### 1. max_context_messages 读限制
- [ ] `get_messages()` 返回 system prompt + 最多 N 条最近消息
- [ ] system prompt 始终在第一位

### 2. window_size 存储裁剪 vs 读限制分离
- [ ] `window_size` 控制 `_messages` 存储上限，超出时删除最旧
- [ ] `max_context_messages` 控制 `get_messages()` 返回上限，不影响存储
- [ ] 两者独立工作

### 3. token 估算
- [ ] 无 API 数据时走字符估算 (`total_chars // 4`)
- [ ] 有 `_last_prompt_tokens` 时返回 API 实际值
- [ ] `thinking` 和 `tool_calls` 内容计入估算

### 4. needs_compression 阈值
- [ ] 低于 `max_tokens * compress_threshold` 返回 `False`
- [ ] 达到阈值时返回 `True`
- [ ] 超过阈值时返回 `True`

### 5. compact() 行为
- [ ] 消息数 > `keep_recent` 时：替换旧消息为 1 条摘要 + 保留最近 N 条
- [ ] 摘要消息以 `[Previous conversation summary]` 开头
- [ ] 消息数 ≤ `keep_recent` 时 no-op
- [ ] `_last_prompt_tokens` 重置为 0

### 6. 持久化 round-trip（serialize/deserialize）
- [ ] 摘要消息正常保存和恢复
- [ ] `tool_calls` 正常保存和恢复
- [ ] `thinking` 正常保存和恢复

### 7. _format_for_summary
- [ ] tool 角色结果超过 500 字符时截断
- [ ] 包含 tool_calls 的消息标注 `[called: name]`

## SessionPlugin

### 8. _compress 端到端
- [ ] 提供 LLM 时：调用 LLM 生成摘要 → `compact()` → 消息数减少
- [ ] 摘要写入 `_messages[0]`

### 9. 边界情况
- [ ] 空内存不报错
- [ ] 只有 system prompt 不触发压缩
- [ ] `max_context_messages` 大于存储量时正常返回
- [ ] `keep_recent=0` 时 compact 只保留摘要

### 10. ctx.llm 为 None 时的安全兜底
- [ ] 跳过压缩，日志告警
- [ ] 消息不变，回退到 sliding window

## 快速验证命令

```bash
# 所有场景一次性运行
uv run python -c "
from agent.plugins.session import SessionMemory, SessionPlugin
from agent.core.llm import Message, ToolCall
import json, asyncio

# 1. max_context_messages
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

# 2. window_size vs read limit
m2 = SessionMemory(window_size=10, max_context_messages=100)
for i in range(30):
    m2.add_user_message(f'msg {i}')
assert len(m2._messages) == 10
print('[PASS] 2. storage trim')

# 3. token estimation
m3 = SessionMemory(max_tokens=1000)
m3.set_system_prompt('sys')
for i in range(5):
    m3.add_user_message('hello '*50)
    m3.add_assistant_message(content='world '*50)
assert m3.estimate_tokens() > 0
m3.set_last_prompt_tokens(1234)
assert m3.estimate_tokens() == 1234
print('[PASS] 3. token estimation')

# 4. threshold
m4 = SessionMemory(max_tokens=1000, compress_threshold=0.9)
m4.set_last_prompt_tokens(500)
assert not m4.needs_compression()
m4.set_last_prompt_tokens(900)
assert m4.needs_compression()
print('[PASS] 4. threshold')

# 5. compact
m5 = SessionMemory(keep_recent=5)
for i in range(20):
    m5.add_user_message(f'msg {i}')
    m5.add_assistant_message(content=f'resp {i}')
m5.compact('summary')
assert len(m5._messages) == 6
assert m5._messages[0].content.startswith('[Previous conversation summary]')
m5b = SessionMemory(keep_recent=10)
m5b.add_user_message('only one')
m5b.compact('summary')
assert len(m5b._messages) == 1
print('[PASS] 5. compact')

# 6. persistence
m6 = SessionMemory(keep_recent=3)
m6.set_system_prompt('sys')
for i in range(10):
    m6.add_user_message(f'msg {i}')
    m6.add_assistant_message(content=f'resp {i}')
m6.compact('summary')
lines = [json.dumps({
    'role': msg.role,
    'content': msg.content,
    'thinking': msg.thinking,
    'tool_calls': [{'id': t.id, 'name': t.name, 'arguments': t.arguments} for t in msg.tool_calls] if msg.tool_calls else None,
    'tool_call_id': msg.tool_call_id,
}, ensure_ascii=False) for msg in m6.get_messages()]
restored = [json.loads(l) for l in lines]
assert any('[Previous conversation summary]' in (r.get('content') or '') for r in restored)
print('[PASS] 6. persistence')

# 7. format helper
msgs = [
    Message(role='user', content='Hello'),
    Message(role='tool', content='x'*600, tool_call_id='1'),
]
formatted = SessionPlugin._format_for_summary(msgs)
assert '...' in formatted
print('[PASS] 7. format helper')

# 9. edge cases
m9 = SessionMemory()
assert not m9.needs_compression()
assert len(m9.get_messages()) == 0
m9.set_system_prompt('sys')
assert len(m9.get_messages()) == 1
m9b = SessionMemory(max_context_messages=1000)
for i in range(5): m9b.add_user_message('msg')
assert len(m9b.get_messages()) == 5
m9c = SessionMemory(keep_recent=0)
for i in range(5): m9c.add_user_message(f'msg {i}')
m9c.compact('summary')
assert len(m9c._messages) == 1
print('[PASS] 9. edge cases')

print('All regression tests passed')
"
```
