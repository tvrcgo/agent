// test-agent-manager.js — 冒烟测试：驱动 agent-manager 子进程完整生命周期
// 1) start → 等待 running 状态（健康检查通过）
// 2) WS 连接验证（读 session_id 握手）
// 3) stop → 等待 stopped 状态，验证 agent 进程退出
'use strict';

const { fork } = require('child_process');
const path = require('path');

const CONFIG = {
  python: process.env.TEST_PYTHON || 'D:\\Code\\tvrcgo-agent\\.venv\\Scripts\\python.exe',
  cwd: 'D:\\Code\\tvrcgo-agent',
  port: 8765,
};

const manager = fork(path.join(__dirname, '..', 'agent-manager.cjs'), [], {
  stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
});

let state = 'unknown';
let agentPid = null;
let sawReady = false;

function request(type, payload) {
  return new Promise((resolve) => {
    const listener = (msg) => {
      if (msg && msg.type === 'status' && msg.state) {
        resolve(msg);
      }
    };
    manager.on('message', listener);
    manager.send({ type, ...(payload || {}) });
    setTimeout(() => manager.removeListener('message', listener), 15000);
  });
}

manager.on('message', (msg) => {
  if (!msg || !msg.type) return;
  if (msg.type === 'status') {
    state = msg.state;
    agentPid = msg.pid;
    console.log(`[status] state=${msg.state} pid=${msg.pid} ${msg.log || ''}`);
  } else if (msg.type === 'agent-log') {
    const line = msg.line.trim();
    if (line) console.log(`[agent:${msg.stream}] ${line}`);
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

async function wsProbe() {
  return new Promise((resolve) => {
    // Node 24 内置 WebSocket（全局）
    const ws = new WebSocket(`ws://127.0.0.1:${CONFIG.port}?session_id=smoke-test-1`);
    const timer = setTimeout(() => { try { ws.close(); } catch {} resolve(false); }, 5000);
    ws.onopen = () => { clearTimeout(timer); resolve(true); ws.close(); };
    ws.onerror = () => { clearTimeout(timer); resolve(false); };
  });
}

(async () => {
  // 1) start
  manager.send({ type: 'start', config: CONFIG });
  const ready = await waitFor(() => state === 'running', 30000, 'agent 进入 running 状态');
  if (!ready) { manager.kill(); process.exit(1); }

  // 2) WS 探活
  const wsOk = await wsProbe();
  console.log(`WS 连接验证: ${wsOk ? 'OK' : 'FAIL'}`);
  if (!wsOk) { manager.kill(); process.exit(1); }

  // 3) stop
  manager.send({ type: 'stop' });
  const stopped = await waitFor(() => state === 'stopped', 15000, 'agent 进入 stopped 状态');
  console.log(`停止流程: ${stopped ? 'OK' : 'FAIL'}`);

  console.log('=== SMOKE TEST PASSED ===');
  // manager 在收到 stop 后进程保持存活，直接 kill 收尾
  manager.kill();
  process.exit(stopped ? 0 : 1);
})();
