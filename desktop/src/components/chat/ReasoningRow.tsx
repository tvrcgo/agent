// Think 折叠行（复刻 harness ReasoningRow）：运行中扫光 + 折叠摘要 + 展开正文

import { useState } from "react"
import { ChevronDown, ChevronRight, Brain } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"

export function ReasoningRow({ text, running, t, streaming }: {
  text: string
  running: boolean
  t: Messages
  streaming?: boolean
}) {
  const [open, setOpen] = useState(false)
  const summary = text.split("\n").find((line) => line.trim()) ?? text
  const clipped = summary.length > 120 ? summary.slice(0, 120) + "…" : summary

  return (
    <div className={cn("reasoning-row", running && "reasoning-row-running")} data-state={running ? "running" : "ok"}>
      <button type="button" className="reasoning-header" onClick={() => setOpen(!open)}>
        <Brain size={14} className="reasoning-icon" />
        <span className="reasoning-title">{t.think}</span>
        {!open && (
          <span className={cn("reasoning-summary", running && streaming && "reasoning-summary-follow")}>
            <span className="reasoning-sep">·</span>
            {clipped}
          </span>
        )}
        <span className="reasoning-chevron">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </button>
      {open && <div className="reasoning-body">{text}</div>}
    </div>
  )
}
