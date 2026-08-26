// test-ws-tool.js — 工具调用 + confirm 链路验证
// 1) 发一个触发 write_file 的请求（write_file 在 tool_guard 审查列表）
// 2) 期待 confirm 事件 → 发送 approve
// 3) 期待 tool_call / tool_result / jobs 数据
// 4) done 终态
'use strict';

const { fork } = require('child_process');
const path = require('path');
const fs = require('fs');

const CONFIG = {
  python: 'D:\\Code\\tvrcgo-agent\\.venv\\Scripts\\python.exe',
  cwd: 'D:\\Code\\tvrcgo-agent',
  port: 8765,
};

const manager = fork(path.join(__dirname, '..', 'agent-manager.cjs'), [], {
  stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
});

let state = 'unknown';
const sessionId = 'tool-test-' + Date.now().toString(36);
let ws = null;
const events = [];
let approved = false;
const results = { confirm: false, tool_call: false, tool_result: false, jobs: false };

manager.on('message', (msg) => {
  if (msg && msg.type === 'status') state = msg.state;
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
    ws.onerror = () => { clearTimeout(timer); reject(new Error('WS error')); };
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      events.push(msg);
      const d = msg.payload || {};
      if (msg.type === 'confirm' && !approved) {
        const conf = d.data || {};
        results.confirm = true;
        approved = true;
        console.log(`[confirm] 收到确认: ${conf.description}`);
        ws.send(JSON.stringify({ type: 'command', payload: { action: 'confirm', confirm_id: conf.id, decision: 'approve' } }));
        console.log('[confirm] 已发送 approve');
      }
      if (msg.type === 'tool_call') results.tool_call = true;
      if (msg.type === 'tool_result') results.tool_result = true;
      if (msg.type === 'data') {
        const inner = d.data || {};
        if (inner.name === 'jobs') results.jobs = true;
      }
    };
  });
}

function send(obj) { ws.send(JSON.stringify(obj)); }

(async () => {
  manager.send({ type: 'start', config: CONFIG });
  if (!await waitFor(() => state === 'running', 30000, 'agent running')) { manager.kill(); process.exit(1); }
  await connectWS();

  // 工作区临时文件：请求写一个文件（触发 write_file 审查 → confirm）
  send({ type: 'chat', payload: { content: '请在工作区当前目录写入一个文件 hello.txt，内容为 "hi from desktop test"，然后结束。不要做其它事。' } });

  const done = await waitFor(() => events.some((e) => e.type === 'status' && (e.payload || {}).content === 'done'), 90000, 'done 终态');
  console.log(`confirm: ${results.confirm ? 'OK' : 'MISS'}`);
  console.log(`tool_call: ${results.tool_call ? 'OK' : 'MISS'}`);
  console.log(`tool_result: ${results.tool_result ? 'OK' : 'MISS'}`);
  console.log(`jobs: ${results.jobs ? 'OK' : 'MISS'}`);

  manager.send({ type: 'stop' });
  await waitFor(() => state === 'stopped', 10000, 'agent stopped');
  const pass = done && results.confirm && results.tool_call && results.tool_result;
  console.log(pass ? '=== WS TOOL TEST PASSED ===' : '=== WS TOOL TEST FAILED ===');
  manager.kill();
  process.exit(pass ? 0 : 1);
})();
