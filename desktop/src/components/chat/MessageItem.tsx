// 消息项（复刻 harness MessageItem + MessageIconActions）
// 用户消息右对齐 + footer 时钟/复制；助手消息聚合 blocks（reasoning/tool/text）。

import { memo, useState } from "react"
import { Check, Copy } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"
import type { SessionNode } from "./session-model"
import { ReasoningRow } from "./ReasoningRow"
import { MarkdownText } from "./AssistantMarkdown"
import { ToolCallCard } from "./ToolCallCard"

function formatClock(ts: number): string {
  if (!ts) return ""
  const d = new Date(ts)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
  }
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}

function CopyAction({ text, t }: { text: string; t: Messages }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="msg-action"
      title={t.copyMessage}
      onClick={() => {
        void navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 1000)
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      <span className="msg-action-label">{copied ? t.copiedMessage : t.copyMessage}</span>
    </button>
  )
}

/** 用户消息 */
export const UserMessage = memo(function UserMessage({ node, t }: {
  node: Extract<SessionNode, { type: "user" }>
  t: Messages
}) {
  return (
    <div className="msg-user">
      <div className="msg-user-body">
        <div className="msg-bubble msg-bubble-user">
          <MarkdownText text={node.content} />
        </div>
        <div className="msg-actions">
          <span className="msg-clock">{formatClock(node.ts)}</span>
          <CopyAction text={node.content} t={t} />
        </div>
      </div>
    </div>
  )
})

/** 助手回合（含 reasoning/tool/text blocks） */
export const AssistantMessage = memo(function AssistantMessage({ node, t, running, selectedTool, onSelectTool }: {
  node: Extract<SessionNode, { type: "assistant" }>
  t: Messages
  running: boolean
  selectedTool: string | null
  onSelectTool: (id: string) => void
}) {
  const text = node.blocks.filter((b) => b.type === "text").map((b) => b.content).join("\n\n")
  return (
    <div className="msg-assistant">
      <div className="msg-assistant-body">
        <div className="msg-bubble msg-bubble-assistant">
          {node.blocks.map((block, i) => {
            if (block.type === "reasoning") {
              return <ReasoningRow key={i} text={block.content} running={running} t={t} streaming={running && i === node.blocks.length - 1} />
            }
            if (block.type === "tool") {
              return (
                <ToolCallCard
                  key={block.id ?? i}
                  tool={block}
                  selected={selectedTool === block.id}
                  onSelect={() => onSelectTool(block.id)}
                  t={t}
                  running={running}
                />
              )
            }
            return null
          })}
          {text ? <MarkdownText text={text} /> : null}
        </div>
        <div className="msg-actions">
          <CopyAction text={text} t={t} />
          <span className="msg-clock">{formatClock(node.ts)}</span>
        </div>
      </div>
    </div>
  )
})
