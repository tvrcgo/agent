// preload.cjs — 暴露安全 IPC 桥给 renderer（contextIsolation）
// 与 src/lib/bridge.ts 的 AgentDesktopApi 对应
"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("agentDesktop", {
  // 状态
  getState: () => ipcRenderer.invoke("agent:state"),
  onState: (callback) => {
    const listener = (_e, state) => callback(state);
    ipcRenderer.on("agent:state", listener);
    return () => ipcRenderer.removeListener("agent:state", listener);
  },

  // 配置
  getConfig: () => ipcRenderer.invoke("config:get"),
  saveConfig: (config) => ipcRenderer.invoke("config:save", config),

  // agent 生命周期
  start: (config) => ipcRenderer.invoke("agent:start", config),
  stop: () => ipcRenderer.invoke("agent:stop"),
  restart: () => ipcRenderer.invoke("agent:restart"),

  // 设置
  saveSettings: (settings) => ipcRenderer.invoke("settings:save", settings),

  // 会话存储
  listSessions: () => ipcRenderer.invoke("sessions:list"),
  loadSession: (id) => ipcRenderer.invoke("sessions:load", id),
  saveSession: (id, payload) => ipcRenderer.invoke("sessions:save", id, payload),
  deleteSession: (id) => ipcRenderer.invoke("sessions:delete", id),

  // 日志
  getLogs: (options) => ipcRenderer.invoke("logs:get", options),
  clearLogs: () => ipcRenderer.invoke("logs:clear"),
  onLog: (callback) => {
    const listener = (_e, log) => callback(log);
    ipcRenderer.on("agent-log", listener);
    return () => ipcRenderer.removeListener("agent-log", listener);
  },
  onLogsCleared: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("logs:cleared", listener);
    return () => ipcRenderer.removeListener("logs:cleared", listener);
  },

  // 本地文件
  openConfigFolder: () => ipcRenderer.invoke("shell:openConfigFolder"),
  openSessionsFolder: () => ipcRenderer.invoke("shell:openSessionsFolder"),
  openProjectDir: () => ipcRenderer.invoke("shell:openProjectDir"),
  openLogFile: () => ipcRenderer.invoke("shell:openLogFile"),
  selectProject: () => ipcRenderer.invoke("dialog:selectProject"),
  selectPython: () => ipcRenderer.invoke("dialog:selectPython"),

  // 危险操作
  clearSessions: () => ipcRenderer.invoke("danger:clearSessions"),
  factoryReset: () => ipcRenderer.invoke("danger:factoryReset"),
});
