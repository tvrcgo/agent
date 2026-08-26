"use client"

import * as React from "react"
import { Settings, Trash2, Plus } from "lucide-react"

import { cn } from "@/lib/utils"
import { agentDesktop, type AgentState } from "@/lib/bridge"
import { useAgentState } from "@/lib/hooks"
import { messages, type Messages, systemLanguageIsChinese } from "@/lib/messages"
import { useTheme } from "@/components/theme-provider"
import { toast } from "@/components/ui/sonner"

import { ConversationRoot } from "@/components/chat/ConversationRoot"
import { LogsView } from "@/components/views/logs"
import { SettingsView } from "@/components/views/settings"
import type { SessionMeta } from "@/components/chat/session-model"

type View = "chat" | "logs" | "settings"
type AppearanceMode = "system" | "light" | "dark"
type LocaleMode = "system" | "en" | "zh"

function useMessages(locale: LocaleMode | undefined): Messages {
  const [lang, setLang] = React.useState<"en" | "zh">(systemLanguageIsChinese() ? "zh" : "en")
  React.useEffect(() => {
    if (locale === "en" || locale === "zh") {
      setLang(locale)
      return
    }
    setLang(systemLanguageIsChinese() ? "zh" : "en")
  }, [locale])
  return messages[lang] as Messages
}

// 状态小圆点（放在设置导航项右侧，点击打开日志/状态页）
function StatusDot({ tone, label, onClick }: { tone: "default" | "success" | "error" | "pending"; label: string; onClick?: () => void }) {
  return (
    <span
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      className={cn(
        "size-1.5 shrink-0 rounded-full",
        tone === "success" && "bg-emerald-500",
        tone === "error" && "bg-destructive",
        tone === "pending" && "animate-pulse bg-amber-500",
        tone === "default" && "bg-muted-foreground",
        onClick && "cursor-pointer hover:scale-125 transition-transform",
      )}
      title={label}
      aria-label={label}
      onClick={(e) => {
        if (!onClick) return
        e.stopPropagation()
        onClick()
      }}
      onKeyDown={onClick ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onClick() } } : undefined}
    />
  )
}

function runSafely(action: () => void | Promise<void>) {
  try {
    void action()
  } catch (e) {
    toast.error("Bridge error", e instanceof Error ? e.message : String(e))
  }
}

// 侧边栏会话列表区（导航下方常驻）
function SessionListArea({
  sessions, currentId, t, activeSessions, onPick, onNew, onDelete,
}: {
  sessions: SessionMeta[]
  currentId: string | null
  t: Messages
  activeSessions: ReadonlySet<string>
  onPick: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col border-t border-sidebar-border">
      <div className="flex items-center justify-between px-3 pt-3 pb-1">
        <span className="px-1 text-xs font-medium text-sidebar-foreground/60">{t.sessionList}</span>
        <button
          type="button"
          className="rounded-md p-1 text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          onClick={onNew}
          title={t.newSession}
        >
          <Plus className="size-3.5" />
        </button>
      </div>
      <div className="scroll-area flex-1 overflow-y-auto px-2 pb-2">
        {sessions.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-sidebar-foreground/50">{t.emptyChat}</div>
        ) : (
          <div className="flex flex-col gap-0.5">
            {sessions.map((s) => (
              <div
                key={s.id}
                className={cn(
                  "group flex cursor-pointer items-center gap-1 rounded-md px-2 py-1.5 hover:bg-sidebar-accent",
                  s.id === currentId && "bg-sidebar-accent text-sidebar-accent-foreground",
                )}
                onClick={() => onPick(s.id)}
              >
                {activeSessions.has(s.id) ? (
                  <span className="session-status-dot active" title={t.sessionActive} />
                ) : null}
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm" title={s.title}>{s.title}</span>
                  <span className="shrink-0 text-[11px] opacity-60">{formatSessionTime(s.updated_at)}</span>
                </div>
                {s.id === currentId ? (
                  <button
                    type="button"
                    className="hidden shrink-0 text-sidebar-foreground/60 hover:text-destructive group-hover:block"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(s.id)
                    }}
                    title={t.deleteSession}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function formatSessionTime(iso: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  const diff = Date.now() - d.getTime()
  if (diff < 60000) return "刚刚"
  const min = Math.floor(diff / 60000)
  if (min < 60) return `${min} 分钟前`
  const hrs = Math.floor(min / 60)
  if (hrs < 24) return `${hrs} 小时前`
  return d.toLocaleDateString()
}

export function DesktopShell() {
  const [view, setView] = React.useState<View>("logs")
  const [busy, setBusy] = React.useState<string | null>(null)
  const state = useAgentState()
  const [locale, setLocale] = React.useState<LocaleMode>("system")
  const t = useMessages(locale)
  const { setTheme } = useTheme()
  const [sessions, setSessions] = React.useState<SessionMeta[]>([])
  const [chatSessionId, setChatSessionId] = React.useState<string | null>(null)

  // 加载会话列表
  async function refreshSessions() {
    try {
      const list = await agentDesktop().listSessions()
      setSessions(list)
    } catch {
      setSessions([])
    }
  }
  React.useEffect(() => {
    void refreshSessions()
  }, [])

  function pickSession(id: string) {
    setChatSessionId(id)
    if (view !== "chat") setView("chat")
  }

  function newSidebarSession() {
    const id = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16)
    })
    void agentDesktop().saveSession(id, { title: "New Session", created_at: new Date().toISOString(), updated_at: new Date().toISOString(), nodes: [] })
      .then(() => {
        setChatSessionId(id)
        if (view !== "chat") setView("chat")
        void refreshSessions()
      })
  }

  function deleteSidebarSession(id: string) {
    if (id === chatSessionId) setChatSessionId(null)
    void agentDesktop().deleteSession(id).then(() => void refreshSessions())
  }

  // 同步后端持久化的语言/外观设置（含设置页修改后经 onState 推送）
  React.useEffect(() => {
    if (state?.locale && (state.locale === "en" || state.locale === "zh" || state.locale === "system")) {
      setLocale(state.locale as LocaleMode)
    }
    if (state?.appearance && (state.appearance === "light" || state.appearance === "dark" || state.appearance === "system")) {
      setTheme(state.appearance as AppearanceMode)
    }
  }, [state?.locale, state?.appearance])

  const isRunning = state?.status === "running" || state?.status === "starting"
  const [runningSessions, setRunningSessions] = React.useState<ReadonlySet<string>>(new Set())
  const handleRunningChange = React.useCallback((sessionId: string, running: boolean) => {
    setRunningSessions((prev) => {
      const next = new Set(prev)
      if (running) next.add(sessionId)
      else next.delete(sessionId)
      return next
    })
  }, [])

  async function run(action: string, fn: () => Promise<unknown>) {
    setBusy(action)
    try {
      return await fn()
    } catch (e) {
      toast.error("Error", e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy(null)
    }
  }

  async function handleStart() {
    await run("start", async () => {
      await agentDesktop().start()
      toast.success(t.agentStarted)
    })
  }

  async function handleStop() {
    await run("stop", async () => {
      await agentDesktop().stop()
      toast.success(t.agentStopped)
    })
  }

  // 状态 tone
  const statusTone: "success" | "error" | "pending" | "default" =
    state?.status === "running" ? "success" : state?.status === "error" ? "error" : state?.status === "starting" || state?.status === "stopping" ? "pending" : "default"
  const statusLabel =
    state?.status === "running" ? t.runtimeConnected : state?.status === "error" ? t.runtimeError : state?.status === "starting" ? t.runtimeStarting : state?.status === "stopping" ? t.runtimeStopping : t.runtimeIdle

  return (
    <div className="flex h-dvh w-full overflow-hidden">
      {/* 侧边栏 */}
      <aside className="flex w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
        <div className="flex h-12 items-center border-b border-sidebar-border px-4">
          <span className="wordmark">Agent</span>
        </div>
        <SessionListArea
          sessions={sessions}
          currentId={chatSessionId}
          t={t}
          activeSessions={runningSessions}
          onPick={pickSession}
          onNew={newSidebarSession}
          onDelete={deleteSidebarSession}
        />
        <div className="flex items-center gap-1 border-t border-sidebar-border p-2">
          <button
            type="button"
            className={cn(
              "flex h-9 min-w-0 flex-1 items-center gap-2 rounded-md px-3 text-left text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
            )}
            onClick={() => setView("settings")}
          >
            <Settings className="size-4 shrink-0" />
            <span className="min-w-0 flex-1 truncate">{t.settings}</span>
          </button>
          <button
            type="button"
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-md text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
            )}
            onClick={() => setView("logs")}
            title={t.runtimePage}
            aria-label={t.runtimePage}
          >
            <StatusDot tone={statusTone} label={statusLabel} onClick={() => setView("logs")} />
          </button>
        </div>
      </aside>

      {/* 主区 */}
      <main className="flex min-w-0 flex-1 flex-col bg-background">
        {view !== "chat" ? (
          <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-5">
            <h1 className="min-w-0 flex-1 truncate text-sm font-medium">
              {view === "logs" ? t.runtimePage : t.settings}
            </h1>
          </header>
        ) : null}

        <div className="min-h-0 flex-1 overflow-hidden">
          {view === "chat" ? (
            <ConversationRoot t={t} state={state} sessions={sessions} currentId={chatSessionId} onCurrentChange={setChatSessionId} onSessionsChanged={() => void refreshSessions()} onRunningChange={handleRunningChange} />
          ) : view === "logs" ? (
            <LogsView t={t} state={state} busy={busy} isRunning={isRunning} onStart={handleStart} onStop={handleStop} />
          ) : (
            <SettingsView t={t} state={state} busy={busy} isRunning={isRunning} />
          )}
        </div>
      </main>
    </div>
  )
}
