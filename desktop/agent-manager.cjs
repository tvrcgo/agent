// agent-manager.js — node 子进程：管理 Python agent 生命周期
// 职责：spawn python agent → TCP 健康检查端口就绪 → 状态上报主进程；
//       监听 agent 退出/崩溃 → 清理并上报；收到 stop 指令时按序终止 agent。
// 通过 child_process.fork 与主进程 IPC（process.send / process.on('message')）。

'use strict';

const { spawn } = require('child_process');
const net = require('net');

const AGENT_READY_TIMEOUT_MS = 60000;   // 等待 agent 就绪的上限
const HEALTH_CHECK_INTERVAL_MS = 500;   // 端口就绪探测间隔
const TERMINATE_GRACE_MS = 3000;        // SIGTERM 后强制结束的宽限

let agent = null;          // 当前 agent 子进程
let state = 'stopped';     // stopped | starting | running | stopping | error
let healthTimer = null;
let readyDeadline = null;
let configured = null;     // { python, cwd, port } — 最近一次 start 的配置
let stopping = false;

function send(payload) {
  if (process.connected) {
    process.send({ type: 'status', ...payload });
  }
}

function setState(next, extra = {}) {
  state = next;
  send({ state, pid: agent ? agent.pid : null, port: configured ? configured.port : null, ...extra });
}

function healthCheck() {
  if (state !== 'starting' || !configured) return;
  const { port } = configured;
  const socket = net.connect({ host: '127.0.0.1', port }, () => {
    socket.destroy();
    if (state !== 'starting') return;
    clearInterval(healthTimer);
    healthTimer = null;
    setState('running', { log: `Agent ready on ws://127.0.0.1:${port}` });
  });
  socket.on('error', () => {
    socket.destroy();
    // 端口未就绪：继续轮询；但超过截止时间判定启动失败
    if (Date.now() > readyDeadline) {
      clearInterval(healthTimer);
      healthTimer = null;
      failStart('Agent 启动超时：端口未在限定时间内就绪');
    }
  });
}

function failStart(message) {
  if (state === 'starting' || state === 'running') {
    setState('error', { error: message, log: message });
    cleanup();
  }
}

function cleanup() {
  if (healthTimer) {
    clearInterval(healthTimer);
    healthTimer = null;
  }
  readyDeadline = null;
}

function startAgent(cfg) {
  if (state === 'starting' || state === 'running') {
    send({ type: 'status', state, log: 'Agent 已在运行，忽略重复启动' });
    return;
  }
  configured = cfg;
  setState('starting', { log: '正在启动 agent...' });

  const { python, cwd, port } = cfg;
  const args = ['-m', 'agent'];
  agent = spawn(python, args, {
    cwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    // Windows 下管道默认 GBK 编码，Node 侧按 UTF-8 解码会乱码；强制 agent 输出 UTF-8
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });

  agent.stdout.on('data', (d) => {
    send({ type: 'agent-log', stream: 'stdout', line: d.toString() });
  });
  agent.stderr.on('data', (d) => {
    send({ type: 'agent-log', stream: 'stderr', line: d.toString() });
  });

  agent.on('error', (err) => {
    failStart(`无法启动 Python agent: ${err.message}`);
  });

  agent.on('exit', (code, signal) => {
    const wasStopping = stopping;
    stopping = false;
    agent = null;
    cleanup();
    if (wasStopping) {
      setState('stopped', { log: `Agent 已退出（code=${code}, signal=${signal}）` });
    } else if (state === 'running') {
      setState('error', { error: `Agent 意外退出（code=${code}, signal=${signal}）`, log: `Agent 意外退出（code=${code}）` });
    } else if (state === 'starting') {
      setState('error', { error: `Agent 在就绪前退出（code=${code}, signal=${signal}）`, log: 'Agent 在就绪前退出' });
    }
  });

  // 健康检查：轮询端口
  readyDeadline = Date.now() + AGENT_READY_TIMEOUT_MS;
  healthTimer = setInterval(healthCheck, HEALTH_CHECK_INTERVAL_MS);
}

function stopAgent() {
  if (!agent) {
    setState('stopped', { log: 'Agent 未在运行' });
    return;
  }
  stopping = true;
  setState('stopping', { log: '正在停止 agent...' });
  const proc = agent;
  // 优先优雅终止：先发 SIGTERM，宽限后强制结束
  try {
    proc.kill('SIGTERM');
  } catch (e) {
    // Windows 下无 SIGTERM 语义，直接强制结束
  }
  setTimeout(() => {
    if (proc && !proc.killed) {
      try {
        proc.kill();
      } catch (e) {
        /* 已退出则忽略 */
      }
    }
  }, TERMINATE_GRACE_MS);
}

process.on('message', (msg) => {
  if (!msg || !msg.type) return;
  switch (msg.type) {
    case 'start':
      startAgent(msg.config);
      break;
    case 'stop':
      stopAgent();
      break;
    case 'status':
      send({ state, pid: agent ? agent.pid : null, port: configured ? configured.port : null });
      break;
    default:
      break;
  }
});

// 父进程退出时兜底清理：终止 agent 后退出自身
process.on('disconnect', () => {
  if (agent) {
    try {
      agent.kill();
    } catch (e) {
      /* ignore */
    }
  }
  process.exit(0);
});
