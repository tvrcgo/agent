// 消息流（复刻 harness ChatView）：居中列渲染节点树 + 底部坞 + 输入栏/审批接管
// 回到底部按钮 sticky；回合活动行（品牌蓝 shimmer）替代旧 loader。

import { useEffect, useRef, useState } from "react"
import { ArrowDown } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"
import type { SessionNode, SessionMeta, TodoItem } from "./session-model"
import { UserMessage, AssistantMessage } from "./MessageItem"
import { TodoPanel } from "./TodoPanel"
import { ApprovalPanel, type ApprovalRequest } from "./ApprovalPanel"
import { InputBar } from "./InputBar"
import { HeroShell } from "./HeroShell"

export function ChatView({
  t, nodes, todos, running, pendingTurn, selectedTool, onSelectTool,
  approval, onApproval, inputValue, onInput, onSend, onStop, inputDisabled,
  hero, sessions,
}: {
  t: Messages
  nodes: SessionNode[]
  todos: TodoItem[]
  running: boolean
  pendingTurn: { reasoning: string; text: string } | null
  selectedTool: string | null
  onSelectTool: (id: string) => void
  approval: ApprovalRequest | null
  onApproval: (decision: "approve" | "deny") => void
  inputValue: string
  onInput: (v: string) => void
  onSend: () => void
  onStop: () => void
  inputDisabled: boolean
  hero: boolean
  sessions: SessionMeta[]
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [showToBottom, setShowToBottom] = useState(false)
  const pinnedRef = useRef(true)

  const scrollToBottom = (smooth = true) => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" })
    pinnedRef.current = true
    setShowToBottom(false)
  }

  // 新节点/流式内容 → 若在底部则跟随滚动
  useEffect(() => {
    if (pinnedRef.current) {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
    }
  }, [nodes, pendingTurn])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    pinnedRef.current = nearBottom
    setShowToBottom(!nearBottom)
  }

  return (
    <div className="chatview-root">
      <div className="chatview-scroll" ref={scrollRef} onScroll={onScroll} data-conversation-scroll="">
        <div className="chatview-column">
          {nodes.map((node) => (
            <div key={node.id} className="chatview-flowitem">
              {node.type === "user"
                ? <UserMessage node={node} t={t} />
                : <AssistantMessage node={node} t={t} running={false} selectedTool={selectedTool} onSelectTool={onSelectTool} />}
            </div>
          ))}
          {pendingTurn && (
            <div className="chatview-turnstatus">
              <span className="chatview-turnstatus-text">{t.turnRunning}</span>
              {pendingTurn.reasoning ? (
                <span className="chatview-turnstatus-reasoning">{pendingTurn.reasoning.slice(0, 60)}</span>
              ) : null}
            </div>
          )}
          {(hero || (!running && nodes.length === 0 && !pendingTurn)) ? (
            <div className="chatview-hero">
              <HeroShell t={t} sessions={sessions} />
            </div>
          ) : null}
        </div>
        <div className={cn("chatview-tobottom-slot", showToBottom && "chatview-tobottom-visible")}>
          <button type="button" className="chatview-tobottom" onClick={() => scrollToBottom()} aria-label="scroll to bottom">
            <ArrowDown size={16} />
          </button>
        </div>
      </div>

      <div className="chatview-dock">
        <TodoPanel todos={todos} t={t} />
        {approval ? (
          <ApprovalPanel request={approval} onDecision={onApproval} t={t} />
        ) : (
          <InputBar
            value={inputValue}
            onChange={onInput}
            onSend={onSend}
            onStop={onStop}
            disabled={inputDisabled}
            running={running}
            placeholder={t.inputPlaceholder}
            t={t}
          />
        )}
      </div>
    </div>
  )
}
