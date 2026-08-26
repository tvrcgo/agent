// 聊天区骨架（复刻 harness ConversationRoot）：标题栏 + hero/消息流 + 右侧详情抽屉
// 管理：会话切换（loadSession/saveSession/deleteSession）、WS 直连、事件流聚合为节点树、
// confirm 接管输入坞、jobs 任务面板、流式 assistant 回合。

import { useCallback, useEffect, useRef, useState } from "react"
import { PanelRightClose, Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { agentDesktop, type AgentState } from "@/lib/bridge"
import type { Messages } from "@/lib/messages"
import { HeroShell } from "./HeroShell"
import { ChatView } from "./ChatView"
import { DetailsPanel } from "./DetailsPanel"
import {
  uuid, finalizeTurn, sessionTitle, collectTodos,
  type SessionData, type SessionMeta, type SessionNode, type PendingTurn,
} from "./session-model"
import type { ApprovalRequest } from "./ApprovalPanel"
import type { SessionBlock } from "./session-model"

export function ConversationRoot({ t, state, sessions, currentId, onCurrentChange, onSessionsChanged, onRunningChange }: {
  t: Messages
  state: AgentState | null
  sessions: SessionMeta[]
  currentId: string | null
  onCurrentChange: (id: string | null) => void
  onSessionsChanged: () => void
  onRunningChange: (sessionId: string, running: boolean) => void
}) {
  const [nodes, setNodes] = useState<SessionNode[]>([])
  const [approval, setApproval] = useState<ApprovalRequest | null>(null)
  const [selectedTool, setSelectedTool] = useState<string | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [input, setInput] = useState("")
  const [wsReady, setWsReady] = useState(false)
  const [running, setRunning] = useState(false)
  const [pendingTurn, setPendingTurn] = useState<{ reasoning: string; text: string } | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const wsSessionRef = useRef<string | null>(null) // WS 绑定的 session_id（防止串会话）
  const nodesRef = useRef<SessionNode[]>([])
  const turnRef = useRef<PendingTurn | null>(null)
  const statusRef = useRef("")
  const currentIdRef = useRef<string | null>(null)
  currentIdRef.current = currentId
  const prevSessionRef = useRef<string | null>(null) // 上一次打开的会话（切走时保存其进行中内容）

  const syncNodes = (next: SessionNode[]) => {
    nodesRef.current = next
    setNodes(next)
  }

  const currentTitle = sessions.find((s) => s.id === currentId)?.title ?? ""

  // todo 工具生成的任务清单（仅模型调用过 todo 工具时非空）
  const todos = collectTodos(nodes)

  // ---- 会话加载（currentId 变化时）----
  useEffect(() => {
    const prevId = prevSessionRef.current
    prevSessionRef.current = currentId
    // 切走旧会话：把其进行中（未完成）的回复固化并保存，避免切换后内容不显示
    if (prevId && prevId !== currentId && turnRef.current) {
      const final = finalizeTurn(turnRef.current)
      const hasContent = final.blocks.length > 0
      if (hasContent) {
        const data = [...nodesRef.current, final]
        void agentDesktop().saveSession(prevId, { title: sessionTitle(data), nodes: data, updated_at: new Date().toISOString() }).then(() => onSessionsChanged())
      }
    }
    if (currentId === null) {
      // 无会话：断开 WS，清空
      const ws = wsRef.current
      if (ws) {
        ws.onclose = null
        ws.close()
        wsRef.current = null
        wsSessionRef.current = null
      }
      syncNodes([])
      setApproval(null)
      setSelectedTool(null)
      setInput("")
      turnRef.current = null
      setPendingTurn(null)
      setRunning(false)
      return
    }
    // 切换会话：若当前 WS 绑定到其他会话，先断开（防止消息串到旧会话）
    const curWs = wsRef.current
    if (curWs && wsSessionRef.current !== currentId) {
      curWs.onclose = null
      try {
        curWs.close()
      } catch {
        /* 忽略 */
      }
      wsRef.current = null
      wsSessionRef.current = null
      setWsReady(false)
      setRunning(false)
    }
    const session = agentDesktop()
    void session.loadSession(currentId).then((loaded) => {
      // 竞态保护：加载结果只应用于仍为当前会话的情况
      if (currentIdRef.current !== currentId) return
      const loadedNodes: SessionNode[] = (Array.isArray((loaded as { nodes?: unknown }).nodes) ? (loaded as { nodes: unknown[] }).nodes : []) as SessionNode[]
      syncNodes(loadedNodes)
      setApproval(null)
      setSelectedTool(null)
      setInput("")
      turnRef.current = null
      setPendingTurn(null)
      setRunning(false)
      ensureConnection(currentId)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId])

  // ---- 会话操作 ----
  const createSession = useCallback(async () => {
    const id = uuid()
    await agentDesktop().saveSession(id, { title: "New Session", created_at: new Date().toISOString(), updated_at: new Date().toISOString(), nodes: [] })
    onCurrentChange(id)
    onSessionsChanged()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onCurrentChange, onSessionsChanged])

  const deleteSession = useCallback(async (id: string) => {
    const ws = wsRef.current
    if (ws) {
      ws.onclose = null
      ws.close()
      wsRef.current = null
    }
    if (id === currentId) {
      onCurrentChange(null)
    }
    await agentDesktop().deleteSession(id)
    onSessionsChanged()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId, onCurrentChange, onSessionsChanged])

  // ---- WS 连接 ----
  const ensureConnection = useCallback((sessionId: string): void => {
    const existing = wsRef.current
    const bound = wsSessionRef.current
    // 已有连接且绑定同一会话 → 复用
    if (existing && bound === sessionId && (existing.readyState === WebSocket.CONNECTING || existing.readyState === WebSocket.OPEN)) return
    // 绑定其他会话或连接不存在 → 关闭旧的再建新（防止串会话）
    if (existing) {
      existing.onclose = null
      try {
        existing.close()
      } catch {
        /* 忽略 */
      }
      wsRef.current = null
      wsSessionRef.current = null
      setWsReady(false)
      setRunning(false)
    }
    const port = state?.port ?? 8765
    const url = `ws://127.0.0.1:${port}?session_id=${encodeURIComponent(sessionId)}`
    let socket: WebSocket
    try {
      socket = new WebSocket(url)
    } catch {
      return
    }
    wsRef.current = socket
    wsSessionRef.current = sessionId

    socket.onopen = () => setWsReady(true)
    socket.onclose = () => {
      if (wsRef.current === socket) {
        wsRef.current = null
        wsSessionRef.current = null
      }
      setWsReady(false)
      setRunning(false)
      if (currentIdRef.current) onRunningChange(currentIdRef.current, false)
    }
    socket.onerror = () => {}

    socket.onmessage = (e: MessageEvent) => {
      let msg: { type: string; payload?: Record<string, unknown> }
      try {
        msg = JSON.parse(e.data as string)
      } catch {
        return
      }
      const type = msg.type
      const d = msg.payload || {}

      switch (type) {
        case "tool_call": {
          const meta = (d.data as Record<string, unknown>) || {}
          const toolName = (meta.tool as string) || ""
          const toolId = (meta.id as string) || uuid()
          const args = (meta.arguments as Record<string, unknown>) || {}
          if (!turnRef.current) turnRef.current = { nodeId: uuid(), reasoning: [], tools: new Map(), texts: [], ts: Date.now() }
          const turn = turnRef.current
          turn.tools.set(toolId, { name: toolName, args, result: "", error: false })
          break
        }
        case "tool_result": {
          const meta = (d.data as Record<string, unknown>) || {}
          const toolId = (meta.id as string) || ""
          const error = (meta.error as string) || ""
          const failed = Boolean(meta.failed)
          const resultContent = error ? `ERROR: ${error}` : String(d.content || "")
          if (turnRef.current) {
            const existing = turnRef.current.tools.get(toolId)
            if (existing) {
              existing.result = resultContent
              existing.error = failed
              if (!existing.name) existing.name = String(meta.tool || "")
            } else {
              turnRef.current.tools.set(toolId, { name: String(meta.tool || ""), args: {}, result: resultContent, error: failed })
            }
          }
          break
        }
        case "confirm": {
          const meta = (d.data as Record<string, unknown>) || {}
          setApproval({ confirmId: String(meta.id || ""), description: String(meta.description || "") })
          break
        }
        case "message": {
          if (!turnRef.current) turnRef.current = { nodeId: uuid(), reasoning: [], tools: new Map(), texts: [], ts: Date.now() }
          const turn = turnRef.current
          const chunk = String(d.content || "")
          if (d.stream) {
            if (turn.texts.length === 0) turn.texts.push("")
            turn.texts[turn.texts.length - 1] += chunk
          } else {
            turn.texts.push(chunk)
          }
          setPendingTurn({ reasoning: turn.reasoning.join("\n"), text: turn.texts.join("\n") })
          break
        }
        case "thinking": {
          if (!turnRef.current) turnRef.current = { nodeId: uuid(), reasoning: [], tools: new Map(), texts: [], ts: Date.now() }
          const turn = turnRef.current
          const chunk = String(d.content || "")
          if (d.stream) {
            if (turn.reasoning.length === 0) turn.reasoning.push("")
            turn.reasoning[turn.reasoning.length - 1] += chunk
          } else {
            turn.reasoning.push(chunk)
          }
          setPendingTurn({ reasoning: turn.reasoning.join("\n"), text: turn.texts.join("\n") })
          break
        }
        case "status": {
          const content = String(d.content || "")
          statusRef.current = content
          if (content === "running" || content === "thinking" || content === "acting") {
            setRunning(true)
            if (currentIdRef.current) onRunningChange(currentIdRef.current, true)
          }
          if (content === "done" || content === "cancelled" || content === "error" || content === "idle") {
            setRunning(false)
            if (currentIdRef.current) onRunningChange(currentIdRef.current, false)
            if (turnRef.current) {
              const final = finalizeTurn(turnRef.current)
              const hasContent = final.blocks.length > 0
              if (hasContent) syncNodes([...nodesRef.current, final])
              turnRef.current = null
              setPendingTurn(null)
            }
            setApproval(null)
            void saveCurrent()
          }
          break
        }
        case "error": {
          const reason = (d.data as Record<string, unknown> | undefined)?.reason
          const err = reason ? String(reason) : String(d.message || d.content || "")
          if (!turnRef.current) turnRef.current = { nodeId: uuid(), reasoning: [], tools: new Map(), texts: [], ts: Date.now() }
          turnRef.current.texts.push(`**Error:** ${err}`)
          setPendingTurn({ reasoning: turnRef.current.reasoning.join("\n"), text: turnRef.current.texts.join("\n") })
          break
        }
        default:
          break
      }
    }
  }, [state?.port])

  async function saveCurrent() {
    // 用实时会话 id（ref），避免 onmessage 闭包捕获陈旧 currentId 导致保存到错误会话
    const id = currentIdRef.current
    if (!id) return
    const data = nodesRef.current
    const title = sessionTitle(data)
    await agentDesktop().saveSession(id, { title, nodes: data, updated_at: new Date().toISOString() })
    onSessionsChanged()
  }

  async function send() {
    const text = input.trim()
    if (!text) return
    let targetId = currentId
    // 无会话时先自动创建
    if (!targetId) {
      const id = uuid()
      await agentDesktop().saveSession(id, { title: "New Session", created_at: new Date().toISOString(), updated_at: new Date().toISOString(), nodes: [] })
      targetId = id
      onCurrentChange(id)
      onSessionsChanged()
    }
    // 等待 WS 绑定当前会话就绪（新建/切换后连接是异步的，避免静默丢消息）
    const deadline = Date.now() + 3000
    while (Date.now() < deadline) {
      const s = wsRef.current
      if (s && wsSessionRef.current === targetId && s.readyState === WebSocket.OPEN) break
      await new Promise((r) => setTimeout(r, 80))
    }
    const socket = wsRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    if (targetId !== currentIdRef.current) return
    // 双保险：socket 必须绑定当前会话，防止串会话
    if (wsSessionRef.current !== currentIdRef.current) return

    const userNode: SessionNode = { id: uuid(), type: "user", content: text, ts: Date.now() }
    syncNodes([...nodesRef.current, userNode])
    turnRef.current = { nodeId: uuid(), reasoning: [], tools: new Map(), texts: [], ts: Date.now() }
    setPendingTurn({ reasoning: "", text: "" })

    if (text.startsWith("/")) {
      const action = text.slice(1).split(" ")[0]
      socket.send(JSON.stringify({ type: "command", payload: { action } }))
    } else {
      socket.send(JSON.stringify({ type: "chat", payload: { content: text } }))
    }
    setInput("")
    void saveCurrent()
  }

  function stop() {
    const socket = wsRef.current
    if (socket && socket.readyState === WebSocket.OPEN && wsSessionRef.current === currentIdRef.current) {
      socket.send(JSON.stringify({ type: "command", payload: { action: "cancel" } }))
    }
  }

  function onApproval(decision: "approve" | "deny") {
    if (!approval) return
    const socket = wsRef.current
    if (socket && socket.readyState === WebSocket.OPEN && wsSessionRef.current === currentIdRef.current) {
      socket.send(JSON.stringify({ type: "command", payload: { action: "confirm", confirm_id: approval.confirmId, decision } }))
    }
    setApproval(null)
  }

  const selectedToolBlock = (() => {
    for (const node of nodes) {
      if (node.type !== "assistant") continue
      for (const block of node.blocks as SessionBlock[]) {
        if (block.type === "tool" && block.id === selectedTool) return block as Extract<SessionBlock, { type: "tool" }>
      }
    }
    return null
  })()

  // hero 模式（无会话）输入可用；有会话时需 WS 就绪
  const inputDisabled = currentId !== null && !wsReady

  // 选择工具：更新选中并打开详情抽屉
  function handleSelectTool(id: string) {
    setSelectedTool(id)
    if (!detailsOpen) setDetailsOpen(true)
  }

  return (
    <div className="conv-root">
      {currentId !== null ? (
        <div className="conv-header">
          <div className="conv-titleRow">
            <span className="conv-title">{currentTitle}</span>
            <div className="conv-headerActions">
              <button type="button" className="conv-delete" title={t.deleteSession} onClick={() => void deleteSession(currentId)}>
                <Trash2 size={14} />
              </button>
              <button
                type="button"
                className={cn("conv-drawer-btn", detailsOpen && "conv-drawer-btn-active")}
                title={t.detailsPanel}
                aria-pressed={detailsOpen}
                onClick={() => setDetailsOpen(!detailsOpen)}
              >
                <PanelRightClose size={16} />
              </button>
            </div>
          </div>
        </div>
      ) : null}
      <div className="conv-main">
        <div className="conv-body">
          <ChatView
            t={t}
            nodes={nodes}
            todos={todos}
            running={running}
            pendingTurn={pendingTurn}
            selectedTool={selectedTool}
            onSelectTool={handleSelectTool}
            approval={approval}
            onApproval={onApproval}
            inputValue={input}
            onInput={setInput}
            onSend={send}
            onStop={stop}
            inputDisabled={inputDisabled}
            hero={currentId === null}
            sessions={sessions}
          />
        </div>
        <DetailsPanel tool={selectedToolBlock} open={detailsOpen} onClose={() => setDetailsOpen(false)} t={t} />
      </div>
    </div>
  )
}
