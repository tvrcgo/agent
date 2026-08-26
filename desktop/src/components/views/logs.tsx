"use client"

import * as React from "react"
import { Play, RefreshCw, Square, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import { agentDesktop, logMessage, type AgentLog, type AgentState } from "@/lib/bridge"
import type { Messages } from "@/lib/messages"
import { toast } from "@/components/ui/sonner"

const PAGE_SIZE_OPTIONS = [100, 300, 1000, 3000]

type Tone = "default" | "success" | "error" | "pending"

function StatusPill({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          tone === "success" && "bg-emerald-500",
          tone === "error" && "bg-destructive",
          tone === "pending" && "animate-pulse bg-amber-500",
          tone === "default" && "bg-muted-foreground",
        )}
      />
      <span
        className={cn(
          "font-medium",
          tone === "error" && "text-destructive",
          tone === "success" && "text-emerald-500",
          tone === "pending" && "text-amber-500",
        )}
      >
        {label}
      </span>
    </span>
  )
}

export function LogsView({
  t,
  state,
  busy,
  isRunning,
  onStart,
  onStop,
}: {
  t: Messages
  state: AgentState | null
  busy: string | null
  isRunning: boolean
  onStart: () => Promise<void>
  onStop: () => Promise<void>
}) {
  const [logs, setLogs] = React.useState<AgentLog[]>([])
  const [total, setTotal] = React.useState(0)
  const [hasMore, setHasMore] = React.useState(false)
  const [pageSize, setPageSize] = React.useState(100)
  const [stickToBottom, setStickToBottom] = React.useState(true)

  const viewportRef = React.useRef<HTMLDivElement | null>(null)
  const loadingMoreRef = React.useRef(false)

  const refresh = React.useCallback(async () => {
    try {
      const page = await agentDesktop().getLogs({ pageSize })
      setLogs(page.items)
      setTotal(page.total)
      setHasMore(page.hasMoreBefore)
    } catch (e) {
      /* bridge 不可用时静默 */
    }
  }, [pageSize])

  React.useEffect(() => {
    void refresh()
    const unsubscribe = agentDesktop().onLog(() => {
      void refresh()
    })
    const unsubCleared = agentDesktop().onLogsCleared(() => {
      setLogs([])
      setTotal(0)
      setHasMore(false)
    })
    return () => {
      unsubscribe()
      unsubCleared()
    }
  }, [refresh])

  React.useEffect(() => {
    if (!stickToBottom) return
    const viewport = viewportRef.current
    if (!viewport) return
    requestAnimationFrame(() => {
      viewport.scrollTop = viewport.scrollHeight
    })
  }, [logs, stickToBottom])

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 8
    setStickToBottom(atBottom)
    if (!hasMore || loadingMoreRef.current) return
    if (el.scrollTop > 24) return
    loadingMoreRef.current = true
    void (async () => {
      try {
        const firstSeq = logs.length > 0 ? logs[0].seq ?? null : null
        const page = await agentDesktop().getLogs({ beforeSeq: firstSeq, pageSize })
        if (page.items.length > 0) {
          setLogs((prev) => [...page.items, ...prev])
          setHasMore(page.hasMoreBefore)
        } else {
          setHasMore(false)
        }
      } finally {
        window.setTimeout(() => {
          loadingMoreRef.current = false
        }, 500)
      }
    })()
  }

  async function handleClear() {
    try {
      await agentDesktop().clearLogs()
      toast.success(t.clearLogs)
    } catch (e) {
      toast.error("Error", e instanceof Error ? e.message : String(e))
    }
  }

  const statusTone: Tone = state?.status === "running" ? "success" : state?.status === "error" ? "error" : state?.status === "starting" || state?.status === "stopping" ? "pending" : "default"
  const statusLabel =
    state?.status === "running" ? t.runtimeConnected : state?.status === "error" ? t.runtimeError : state?.status === "starting" ? t.runtimeStarting : state?.status === "stopping" ? t.runtimeStopping : t.runtimeIdle
  const statusDetail = state?.pid ? `PID ${state.pid} · port ${state.port}` : state?.lastError || t.runtimeIdle

  const connTone: Tone = state?.status === "running" ? "success" : "default"
  const connLabel = state?.status === "running" ? t.runtimeConnected : t.runtimeIdle

  return (
    <div className="flex h-[calc(100vh-3rem)] min-h-0 flex-col">
      {/* 上方：紧凑状态栏（agent/ws 状态 + 启停） */}
      <div className="flex items-center justify-between gap-4 px-5 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm">
          <span className="shrink-0 text-muted-foreground">{t.runtimeStatus}</span>
          <StatusPill tone={statusTone} label={statusLabel} />
          <span className="mx-1 text-muted-foreground/40">·</span>
          <span className="shrink-0 text-muted-foreground">{t.connectionStatus}</span>
          <StatusPill tone={connTone} label={connLabel} />
          {statusDetail ? <span className="ml-2 min-w-0 truncate text-xs text-muted-foreground">{statusDetail}</span> : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isRunning ? (
            <Button size="sm" variant="outline" onClick={() => void onStop()} disabled={Boolean(busy)}>
              {busy === "stop" ? <RefreshCw className="size-3 animate-spin" /> : <Square className="size-3" />}
              {t.stopAgent}
            </Button>
          ) : (
            <Button size="sm" onClick={() => void onStart()} disabled={Boolean(busy)}>
              {busy === "start" ? <RefreshCw className="size-3 animate-spin" /> : <Play className="size-3" />}
              {t.startAgent}
            </Button>
          )}
        </div>
      </div>

      {/* 下方：滚动日志工具栏 */}
      <div className="flex items-center justify-between border-t px-5 py-2">
        <div className="text-xs text-muted-foreground">
          {logs.length} / {total} {t.logTotal}
        </div>
        <div className="flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger>
              <Button variant="outline" size="xs">
                {t.pageSize}: {pageSize}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuRadioGroup value={String(pageSize)} onValueChange={(v) => setPageSize(Number(v))}>
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <DropdownMenuRadioItem key={size} value={String(size)}>
                    {size}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="outline" size="xs" onClick={() => void handleClear()}>
            <Trash2 className="size-3" />
            {t.clearLogs}
          </Button>
        </div>
      </div>
      <div ref={viewportRef} onScroll={handleScroll} className="scroll-area min-h-0 flex-1 overflow-y-auto">
        {logs.length === 0 && total === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">{t.noLogs}</div>
        ) : (
          <div className="px-5 py-3 font-mono text-xs">
            {hasMore ? (
              <div className="flex justify-center pb-3">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    const firstSeq = logs.length > 0 ? logs[0].seq ?? null : null
                    void (async () => {
                      const page = await agentDesktop().getLogs({ beforeSeq: firstSeq, pageSize })
                      if (page.items.length > 0) {
                        setLogs((prev) => [...page.items, ...prev])
                        setHasMore(page.hasMoreBefore)
                      } else {
                        setHasMore(false)
                      }
                    })()
                  }}
                >
                  {t.loadMore}
                </Button>
              </div>
            ) : null}
            {logs.map((log, index) => (
              <div key={`${log.time || index}-${index}`} className="grid grid-cols-[88px_72px_1fr] gap-2 py-0.5">
                <span className="text-muted-foreground">{log.time ? new Date(log.time).toLocaleTimeString() : "--:--:--"}</span>
                <span className={cn("text-muted-foreground", log.level === "ERROR" && "text-destructive", log.level === "WARNING" && "text-yellow-600")}>{log.level || "INFO"}</span>
                <span className="min-w-0 break-words">{logMessage(log)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
