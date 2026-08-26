// 桥接 API：与 electron/preload.cjs 暴露的 window.agentDesktop 对应
// 类型定义（部分简化，仅含本项目用到的能力）

export type AgentStatus = "stopped" | "starting" | "running" | "stopping" | "error"

export type AgentState = {
  status: AgentStatus
  pid: number | null
  port: number
  lastError: string | null
  // 设置项
  configPath: string
  sessionsDir: string
  logPath: string
  openAtLogin?: boolean
  silentLaunch?: boolean
  appearance?: "system" | "light" | "dark"
  locale?: "system" | "en" | "zh"
}

export type AgentConfig = {
  python: string
  projectDir: string
  port: number
}

export type AgentLog = {
  seq?: number
  time?: string
  level?: string
  stream?: "stdout" | "stderr"
  message?: string
}

export type LogPage = {
  items: AgentLog[]
  firstSeq: number | null
  lastSeq: number | null
  hasMoreBefore: boolean
  total: number
}

export type DesktopSettings = {
  openAtLogin?: boolean
  silentLaunch?: boolean
  appearance?: "system" | "light" | "dark"
  locale?: "system" | "en" | "zh"
}

export type AgentDesktopApi = {
  getState: () => Promise<AgentState>
  getConfig: () => Promise<AgentConfig>
  saveConfig: (config: AgentConfig) => Promise<AgentConfig>
  start: (config?: AgentConfig) => Promise<AgentState>
  stop: () => Promise<AgentState>
  restart: () => Promise<AgentState>
  saveSettings: (settings: DesktopSettings) => Promise<AgentState>
  clearSessions: () => Promise<void>
  factoryReset: () => Promise<void>
  openConfigFolder: () => Promise<string>
  openSessionsFolder: () => Promise<string>
  openProjectDir: () => Promise<string>
  openLogFile: () => Promise<string>
  getLogs: (options?: { beforeSeq?: number | null; afterSeq?: number | null; pageSize?: number }) => Promise<LogPage>
  clearLogs: () => Promise<LogPage>
  onState: (callback: (state: AgentState) => void) => () => void
  onLog: (callback: (log: AgentLog) => void) => () => void
  onLogsCleared: (callback: () => void) => () => void
  // 会话存储（来自原有 preload）
  listSessions: () => Promise<SessionMeta[]>
  loadSession: (id: string) => Promise<{ id: string; title: string; nodes?: unknown[] }>
  saveSession: (id: string, payload: Record<string, unknown>) => Promise<boolean>
  deleteSession: (id: string) => Promise<{ ok: boolean }>
  selectProject: () => Promise<string | null>
  selectPython: () => Promise<string | null>
}

export type SessionMeta = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

declare global {
  interface Window {
    agentDesktop?: AgentDesktopApi
  }
}

export function agentDesktop(): AgentDesktopApi {
  if (!window.agentDesktop) {
    throw new Error("Agent desktop bridge is not available.")
  }
  return window.agentDesktop
}

export function logMessage(log: AgentLog): string {
  return log.message || ""
}
