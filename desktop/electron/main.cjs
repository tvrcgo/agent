// main.cjs — Electron 主进程
// 职责：创建窗口；fork node 子进程（agent-manager）管理 Python agent；
//       提供 IPC：agent 状态/启停/重启、配置读写、设置、日志环形缓冲、
//       会话文件存储、本地文件打开、危险操作；app 退出时按序清理。
// 与 src/lib/bridge.ts 的 AgentDesktopApi 对应。

"use strict";

const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require("electron");
const { fork } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_PORT = 8765;
const DEFAULT_CONFIG = {
  python: "",
  projectDir: "",
  port: DEFAULT_PORT,
};

// 日志环形缓冲配置
const LOG_BUFFER_MAX = 5000; // 内存中最多保留的日志条目
const LOG_FLUSH_CHUNK = 500;

let mainWindow = null;
let manager = null; // node 子进程（agent-manager）
let managerStatus = { state: "stopped", pid: null, port: DEFAULT_PORT, lastError: null };
let logBuffer = []; // { seq, time, level, stream, message }
let nextLogSeq = 1;
let pendingLogFlush = [];
let logFlushTimer = null;

// ---- 配置读写（userData）----
function userDataPath(name) {
  return path.join(app.getPath("userData"), name);
}

function configPath() {
  return userDataPath("config.json");
}

function loadConfig() {
  try {
    const raw = fs.readFileSync(configPath(), "utf-8");
    const cfg = JSON.parse(raw);
    return { ...DEFAULT_CONFIG, ...cfg };
  } catch (e) {
    return { ...DEFAULT_CONFIG };
  }
}

function saveConfig(cfg) {
  const full = { ...DEFAULT_CONFIG, ...cfg };
  fs.mkdirSync(path.dirname(configPath()), { recursive: true });
  fs.writeFileSync(configPath(), JSON.stringify(full, null, 2), "utf-8");
  return full;
}

// ---- 设置读写（userData/settings.json）----
const DEFAULT_SETTINGS = {
  openAtLogin: false,
  silentLaunch: false,
  appearance: "system",
  locale: "system",
};

function settingsPath() {
  return userDataPath("settings.json");
}

function loadSettings() {
  try {
    const raw = fs.readFileSync(settingsPath(), "utf-8");
    const cfg = JSON.parse(raw);
    return { ...DEFAULT_SETTINGS, ...cfg };
  } catch (e) {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings(partial) {
  const full = { ...loadSettings(), ...partial };
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(full, null, 2), "utf-8");
  return full;
}

// ---- 会话文件存储（userData/sessions/）----
function sessionsDir() {
  const dir = userDataPath("sessions");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function sessionFilePath(id) {
  // 会话 id 仅由 app 生成（uuid），防目录穿越
  if (!/^[A-Za-z0-9._:-]+$/.test(id)) return null;
  return path.join(sessionsDir(), `${id}.json`);
}

function listSessions() {
  const dir = sessionsDir();
  const out = [];
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    const id = name.slice(0, -5);
    try {
      const data = JSON.parse(fs.readFileSync(path.join(dir, name), "utf-8"));
      out.push({
        id,
        title: data.title || "New Session",
        created_at: data.created_at || "",
        updated_at: data.updated_at || "",
      });
    } catch (e) {
      /* 忽略损坏文件 */
    }
  }
  out.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
  return out;
}

function loadSession(id) {
  const fp = sessionFilePath(id);
  if (!fp || !fs.existsSync(fp)) return { id, title: "New Session", nodes: [] };
  try {
    const data = JSON.parse(fs.readFileSync(fp, "utf-8"));
    // 节点树格式（复刻后）：nodes 字段
    // 旧扁平格式（messages 字段）→ 视为空（用户已确认清空旧会话）
    return { id, title: data.title || "New Session", nodes: Array.isArray(data.nodes) ? data.nodes : [] };
  } catch (e) {
    return { id, title: "New Session", nodes: [] };
  }
}

/** 启动迁移：清除旧扁平格式（含 messages 字段、无 nodes）会话 */
function migrateSessions() {
  const dir = sessionsDir();
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    try {
      const data = JSON.parse(fs.readFileSync(path.join(dir, name), "utf-8"));
      const isLegacyFlat = Array.isArray(data.messages) && !Array.isArray(data.nodes);
      if (isLegacyFlat) fs.unlinkSync(path.join(dir, name));
    } catch (e) {
      /* 忽略损坏文件 */
    }
  }
}

function saveSession(id, payload) {
  const fp = sessionFilePath(id);
  if (!fp) return false;
  fs.mkdirSync(path.dirname(fp), { recursive: true });
  fs.writeFileSync(fp, JSON.stringify(payload, null, 2), "utf-8");
  return true;
}

function deleteSession(id) {
  const fp = sessionFilePath(id);
  if (fp && fs.existsSync(fp)) fs.unlinkSync(fp);
}

function clearAllSessions() {
  const dir = sessionsDir();
  for (const name of fs.readdirSync(dir)) {
    if (name.endsWith(".json")) {
      try {
        fs.unlinkSync(path.join(dir, name));
      } catch (e) {
        /* 忽略 */
      }
    }
  }
}

// ---- 日志环形缓冲 ----
function appendLog(entry) {
  const log = {
    seq: nextLogSeq++,
    time: new Date().toISOString(),
    level: entry.level || "INFO",
    stream: entry.stream || "stdout",
    message: entry.message || entry.line || "",
  };
  logBuffer.push(log);
  if (logBuffer.length > LOG_BUFFER_MAX) {
    logBuffer = logBuffer.slice(logBuffer.length - LOG_BUFFER_MAX);
  }
  // 立即推给 renderer
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("agent-log", log);
  }
}

function getLogs(options = {}) {
  const { beforeSeq = null, afterSeq = null, pageSize = 100 } = options;
  let items = logBuffer;
  if (beforeSeq != null) items = items.filter((l) => l.seq < beforeSeq);
  if (afterSeq != null) items = items.filter((l) => l.seq > afterSeq);
  const slice = items.slice(-pageSize);
  return {
    items: slice,
    firstSeq: slice.length > 0 ? slice[0].seq : null,
    lastSeq: slice.length > 0 ? slice[slice.length - 1].seq : null,
    hasMoreBefore: items.length > slice.length,
    total: logBuffer.length,
  };
}

function clearLogs() {
  logBuffer = [];
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("logs:cleared");
  }
  return getLogs({ pageSize: 100 });
}

// ---- agent 状态组装 ----
function buildState() {
  const cfg = loadConfig();
  const settings = loadSettings();
  return {
    status: managerStatus.state,
    pid: managerStatus.pid ?? null,
    port: managerStatus.port ?? DEFAULT_PORT,
    lastError: managerStatus.lastError ?? null,
    configPath: configPath(),
    sessionsDir: sessionsDir(),
    logPath: userDataPath("logs"),
    openAtLogin: settings.openAtLogin,
    silentLaunch: settings.silentLaunch,
    appearance: settings.appearance,
    locale: settings.locale,
  };
}

// ---- agent-manager 子进程 ----
function agentManagerPath() {
  // 打包后 extraResources 在 process.resourcesPath/agent-manager.cjs
  const packaged = path.join(process.resourcesPath, "agent-manager.cjs");
  if (fs.existsSync(packaged)) return packaged;
  return path.join(__dirname, "..", "agent-manager.cjs");
}

function startManager() {
  if (manager) return;
  manager = fork(agentManagerPath(), [], {
    stdio: ["ignore", "ignore", "ignore", "ipc"],
    windowsHide: true,
  });
  manager.on("message", (msg) => {
    if (!msg) return;
    if (msg.type === "status") {
      managerStatus = msg;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("agent:state", buildState());
      }
    } else if (msg.type === "agent-log") {
      // 解析 Python logging 格式：[LEVEL] message → 提取级别
      const line = msg.line || "";
      const match = line.match(/\[(INFO|ERROR|WARNING|DEBUG|CRITICAL)\]/);
      let level = "INFO";
      if (match) {
        level = match[1];
      } else if (msg.stream === "stderr" && !/^\d{4}-\d{2}-\d{2}/.test(line)) {
        level = "ERROR";
      }
      appendLog({ level, stream: msg.stream, message: line });
    }
  });
  manager.on("exit", (code, signal) => {
    manager = null;
    managerStatus = { state: "stopped", pid: null, port: loadConfig().port, lastError: null };
    appendLog({ level: "ERROR", message: `后端管理器已退出（${code}/${signal}）` });
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("agent:state", buildState());
    }
  });
}

function stopManager() {
  if (!manager) return;
  const m = manager;
  manager = null;
  m.disconnect();
}

function createWindow() {
  // 彻底移除系统菜单栏（autoHideMenuBar + 空应用菜单）
  Menu.setApplicationMenu(null);
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 600,
    title: "Agent Desktop",
    backgroundColor: "#ffffff",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));

  // dev 模式热重载：--dev（npm run dev / scripts/dev.mjs）时监听 dist/index.html 变化自动 reload
  if (process.argv.includes("--dev")) {
    const distDir = path.join(__dirname, "..", "dist");
    let lastReload = 0;
    try {
      fs.watch(distDir, (evt, fname) => {
        // 只等 index.html（vite 最后写入它，此时 assets 已就绪），避免重建清空 dist 时 404
        if (fname !== "index.html") return;
        const now = Date.now();
        if (now - lastReload < 300) return;
        lastReload = now;
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.reload();
        }
      });
    } catch (e) {
      /* 文件监听失败不影响运行 */
    }
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// ---- IPC 注册 ----
function registerIpc() {
  // 状态
  ipcMain.handle("agent:state", () => buildState());

  // 配置
  ipcMain.handle("config:get", () => loadConfig());
  ipcMain.handle("config:save", (e, cfg) => saveConfig(cfg));

  // agent 生命周期
  ipcMain.handle("agent:start", (e, cfg) => {
    if (cfg) saveConfig(cfg);
    const saved = loadConfig();
    startManager();
    if (manager) {
      manager.send({
        type: "start",
        config: { python: saved.python, cwd: saved.projectDir, port: saved.port },
      });
    }
    return buildState();
  });
  ipcMain.handle("agent:stop", () => {
    if (manager) manager.send({ type: "stop" });
    return buildState();
  });
  ipcMain.handle("agent:restart", () => {
    const saved = loadConfig();
    if (manager) {
      manager.send({ type: "stop" });
      // 停止后立即重新启动
      setTimeout(() => {
        if (manager) manager.send({ type: "start", config: { python: saved.python, cwd: saved.projectDir, port: saved.port } });
      }, 400);
    }
    return buildState();
  });

  // 设置
  ipcMain.handle("settings:save", (e, settings) => {
    saveSettings(settings);
    if (settings.openAtLogin !== undefined) {
      app.setLoginItemSettings({
        openAtLogin: settings.openAtLogin,
        openAsHidden: settings.silentLaunch || false,
        path: process.execPath,
      });
    }
    // 推送更新后的状态给 renderer（语言/外观即时生效）
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("agent:state", buildState());
    }
    return buildState();
  });

  // 会话存储
  ipcMain.handle("sessions:list", () => listSessions());
  ipcMain.handle("sessions:load", (e, id) => loadSession(id));
  ipcMain.handle("sessions:save", (e, id, payload) => saveSession(id, payload));
  ipcMain.handle("sessions:delete", (e, id) => {
    deleteSession(id);
    return { ok: true };
  });

  // 日志
  ipcMain.handle("logs:get", (e, options) => getLogs(options));
  ipcMain.handle("logs:clear", () => clearLogs());

  // 本地文件
  ipcMain.handle("shell:openConfigFolder", () => {
    shell.openPath(path.dirname(configPath()));
    return path.dirname(configPath());
  });
  ipcMain.handle("shell:openSessionsFolder", () => {
    shell.openPath(sessionsDir());
    return sessionsDir();
  });
  ipcMain.handle("shell:openProjectDir", () => {
    const cfg = loadConfig();
    if (cfg.projectDir) shell.openPath(cfg.projectDir);
    return cfg.projectDir;
  });
  ipcMain.handle("shell:openLogFile", () => {
    const logPath = userDataPath("logs");
    fs.mkdirSync(logPath, { recursive: true });
    const fp = path.join(logPath, "agent.log");
    shell.openPath(fp);
    return fp;
  });

  ipcMain.handle("dialog:selectProject", async () => {
    const res = await dialog.showOpenDialog(mainWindow, {
      title: "选择 tvrcgo-agent 项目目录",
      properties: ["openDirectory"],
    });
    if (res.canceled || res.filePaths.length === 0) return null;
    return res.filePaths[0];
  });
  ipcMain.handle("dialog:selectPython", async () => {
    const res = await dialog.showOpenDialog(mainWindow, {
      title: "选择 Python 解释器（.venv\\Scripts\\python.exe）",
      properties: ["openFile"],
      filters: [{ name: "Python", extensions: ["exe"] }],
    });
    if (res.canceled || res.filePaths.length === 0) return null;
    return res.filePaths[0];
  });

  // 危险操作
  ipcMain.handle("danger:clearSessions", () => {
    clearAllSessions();
    return { ok: true };
  });
  ipcMain.handle("danger:factoryReset", () => {
    // 停止 agent
    if (manager) manager.send({ type: "stop" });
    // 删除 userData 下全部本地数据（配置/设置/会话/日志）
    const ud = app.getPath("userData");
    try {
      for (const name of fs.readdirSync(ud)) {
        const fp = path.join(ud, name);
        fs.rmSync(fp, { recursive: true, force: true });
      }
    } catch (e) {
      /* 忽略 */
    }
    app.relaunch();
    app.exit(0);
    return { ok: true };
  });
}

// ---- 生命周期 ----
app.whenReady().then(() => {
  migrateSessions();
  registerIpc();
  createWindow();
  startManager();

  // 打开应用即自动启动 agent
  const saved = loadConfig();
  if (manager) {
    manager.send({
      type: "start",
      config: { python: saved.python, cwd: saved.projectDir, port: saved.port },
    });
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    stopManager();
    app.quit();
  }
});

app.on("before-quit", () => {
  stopManager();
});
