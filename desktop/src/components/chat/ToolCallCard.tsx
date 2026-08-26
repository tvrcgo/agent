// 工具调用卡（映射 harness tool-call 渲染 + DetailsPanel 联动）
// 显示工具名 + 参数摘要；点击选中 → 右侧详情面板显示 Input/Output。

import { memo, useState } from "react"
import { ChevronDown, ChevronRight, Hammer, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"
import type { SessionBlock } from "./session-model"

type ToolBlock = Extract<SessionBlock, { type: "tool" }>

export const ToolCallCard = memo(function ToolCallCard({
  tool, selected, onSelect, t, running,
}: {
  tool: ToolBlock
  selected: boolean
  onSelect: () => void
  t: Messages
  running: boolean
}) {
  const [open, setOpen] = useState(false)
  const hasResult = tool.result !== "" || tool.error
  const argsPreview = Object.keys(tool.args).length > 0 ? JSON.stringify(tool.args).slice(0, 80) : ""
  return (
    <div
      className={cn("tool-card", selected && "tool-card-selected")}
      data-selected={selected || undefined}
      onClick={onSelect}
    >
      <div className="tool-card-header" onClick={(e) => { e.stopPropagation(); setOpen(!open) }}>
        <span className="tool-card-icon">
          {running ? <Loader2 size={14} className="animate-spin" /> : <Hammer size={14} />}
        </span>
        <span className="tool-card-name">{tool.name}</span>
        {argsPreview ? <span className="tool-card-args">{argsPreview}</span> : null}
        <span className="tool-card-chevron">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </div>
      {open && (
        <div className="tool-card-body">
          <div className="tool-card-section">
            <div className="tool-card-section-label">Input</div>
            <pre className="tool-card-code">{JSON.stringify(tool.args, null, 2)}</pre>
          </div>
          {hasResult && (
            <div className="tool-card-section">
              <div className="tool-card-section-label">{tool.error ? "Error" : "Output"}</div>
              <pre className={cn("tool-card-code", tool.error && "tool-card-code-error")}>{tool.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
})
