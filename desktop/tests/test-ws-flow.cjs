// test-ws-flow.js — WS 交互链路验证
// 直接驱动 agent-manager 启动 agent，然后：
// 1) 建立 WS 会话
// 2) 发送 chat（真实 DeepSeek 调用，链路验证）
// 3) 收集事件：thinking/message/status/tool_call/tool_result 等
// 4) 验证 done 终态
'use strict';

const { fork } = require('child_process');
const path = require('path');

const CONFIG = {
  python: 'D:\\Code\\tvrcgo-agent\\.venv\\Scripts\\python.exe',
  cwd: 'D:\\Code\\tvrcgo-agent',
  port: 8765,
};

const manager = fork(path.join(__dirname, '..', 'agent-manager.cjs'), [], {
  stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
});

let state = 'unknown';
let sessionId = 'flow-test-' + Date.now().toString(36);
let ws = null;
const events = [];

manager.on('message', (msg) => {
  if (!msg || !msg.type) return;
  if (msg.type === 'status') {
    state = msg.state;
    console.log(`[manager] state=${msg.state}`);
  } else if (msg.type === 'agent-log') {
    const line = msg.line.trim();
    if (line && line.includes('ERROR') && line.includes('WebSocket')) {
      // 忽略握手噪音
    } else if (line && (line.includes('Config loaded') || line.includes('listening'))) {
      console.log(`[agent] ${line}`);
    }
  }
});

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function waitFor(predicate, timeoutMs, desc) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (predicate()) return true;
    await sleep(300);
  }
  console.error(`FAIL: 等待超时 — ${desc}`);
  return false;
}

function connectWS() {
  return new Promise((resolve, reject) => {
    ws = new WebSocket(`ws://127.0.0.1:${CONFIG.port}?session_id=${sessionId}`);
    const timer = setTimeout(() => reject(new Error('WS connect timeout')), 8000);
    ws.onopen = () => { clearTimeout(timer); resolve(); };
    ws.onerror = (e) => { clearTimeout(timer); reject(new Error('WS error')); };
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      events.push(msg);
      const d = msg.payload || {};
      if (msg.type === 'message' && d.stream) {
        // 流式
      }
    };
  });
}

function send(obj) {
  ws.send(JSON.stringify(obj));
}

(async () => {
  manager.send({ type: 'start', config: CONFIG });
  if (!await waitFor(() => state === 'running', 30000, 'agent running')) { manager.kill(); process.exit(1); }
  await connectWS();

  // 发送一个简单的真实 chat（DeepSeek）
  send({ type: 'chat', payload: { content: '用一句话自我介绍，不要调用任何工具。' } });

  const done = await waitFor(() => events.some((e) => e.type === 'status' && (e.payload || {}).content === 'done'), 60000, 'done 终态');
  console.log(`收到事件数: ${events.length}`);
  const types = events.map((e) => e.type);
  console.log(`事件类型序列: ${[...new Set(types)].join(', ')}`);
  const msgs = events.filter((e) => e.type === 'message');
  if (msgs.length > 0) {
    const full = msgs.map((m) => (m.payload || {}).content || '').join('');
    console.log(`回复摘要: ${full.slice(0, 120)}`);
  }
  console.log(`done 终态: ${done ? 'OK' : 'FAIL'}`);

  manager.send({ type: 'stop' });
  await waitFor(() => state === 'stopped', 10000, 'agent stopped');
  console.log(done ? '=== WS FLOW TEST PASSED ===' : '=== WS FLOW TEST FAILED ===');
  manager.kill();
  process.exit(done ? 0 : 1);
})();
