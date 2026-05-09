# SessionPlugin 单元测试

## get_messages

- [ ] 返回 system prompt + 全部消息（无 window_size 裁剪）
- [ ] system prompt 始终在第一位

## token 估算

- [ ] 无 API 数据时走字符估算（`total_chars // 4`）
- [ ] 有 `_last_prompt_tokens` 时返回 API 实际值
- [ ] `thinking` 和 `tool_calls` 计入估算

## 压缩阈值

- [ ] 低于 `max_tokens * compress_threshold` → `False`
- [ ] 达到或超过 → `True`

## compact 行为

- [ ] 保留最近 `compress_keep_recent` 条 + 摘要
- [ ] 摘要以 `[Previous conversation summary]` 开头
- [ ] `_last_prompt_tokens` 重置为 0

## 持久化

- [ ] JSONL 追加写入，不覆写
- [ ] 冷启只 tail-read 尾部 N 行
- [ ] `tool_calls` 正确保存和恢复
- [ ] `thinking` 正确保存和恢复
- [ ] 文件命名 `<session_id>.jsonl`

## _format_for_summary

- [ ] tool 结果超过 500 字符时截断
- [ ] 含 tool_calls 的消息标注 `[called: name]`

## 边界情况

- [ ] 空内存不报错
- [ ] 仅 system prompt 时不触发压缩
- [ ] `max_load_messages` 大于存储量时正常返回全部
- [ ] 冷启后 token 超限 → compact + warning 日志

---

```bash
uv run python -c "
from agent.plugins.session import SessionPlugin, _SessionState
from agent.core.model import Message, ToolCall

def _make_plugin(max_tokens=65536, compress_threshold=0.9, max_load_messages=100):
    p = SessionPlugin()
    p._max_tokens = max_tokens
    p._compress_threshold = compress_threshold
    p._max_load_messages = max_load_messages
    return p

# 1. get_messages
p = _make_plugin()
state = _SessionState(system_prompt=Message(role='system', content='sys'))
for i in range(20):
    state.messages.append(Message(role='user', content=f'msg {i}'))
    state.messages.append(Message(role='assistant', content=f'resp {i}'))
msgs = p._get_messages(state)
assert msgs[0].role == 'system' and len(msgs) == 41
print('[PASS] get_messages')

# 2. token estimation / threshold
p = _make_plugin(max_tokens=1000, compress_threshold=0.9)
state = _SessionState()
state.last_prompt_tokens = 500
assert not p._needs_compression(state)
state.last_prompt_tokens = 900
assert p._needs_compression(state)
print('[PASS] compression threshold')

# 3. edge cases
state = _SessionState()
assert not p._needs_compression(state)
print('[PASS] edge cases')

# 4. _format_for_summary
msgs = [
    Message(role='tool', content='x' * 600, tool_call_id='t1'),
    Message(role='assistant', content='done', tool_calls=[ToolCall(id='1', name='echo', arguments={})]),
]
formatted = p._format_for_summary(msgs)
assert len(formatted.split(chr(10))[0]) < 600
print('[PASS] _format_for_summary')

# 5. deserialize round-trip
d = {'role': 'assistant', 'content': 'hi', 'thinking': 'hmm', 'tool_calls': [{'id': '1', 'name': 'echo', 'arguments': {'text': 'x'}}]}
msg = p._deserialize_message(d)
assert msg.thinking == 'hmm' and msg.tool_calls[0].name == 'echo'
print('[PASS] deserialize')

print('All passed')
"
```
