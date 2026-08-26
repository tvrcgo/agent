// 右侧工具详情抽屉（复刻 harness DetailsPanel 的视觉，抽屉式交互）
// 默认隐藏；由标题栏按钮切换；选中工具时显示 Input/Output JSON。

import { X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"
import type { SessionBlock } from "./session-model"

type ToolBlock = Extract<SessionBlock, { type: "tool" }>

export function DetailsPanel({ tool, open, onClose, t }: {
  tool: ToolBlock | null
  open: boolean
  onClose: () => void
  t: Messages
}) {
  return (
    <aside className={cn("details-panel", open && "details-panel-open")} aria-hidden={!open}>
      <div className="details-header">
        <span className="details-title">{tool ? tool.name : t.detailsPanel}</span>
        <button type="button" className="details-close" onClick={onClose} aria-label="close">
          <X size={14} />
        </button>
      </div>
      <div className="details-body">
        {tool === null ? (
          <div className="details-empty">{t.noSelection}</div>
        ) : (
          <>
            <div className="details-section">
              <div className="details-section-label">Input</div>
              <pre className="details-code">{JSON.stringify(tool.args, null, 2)}</pre>
            </div>
            {(tool.result !== "" || tool.error) && (
              <div className="details-section">
                <div className="details-section-label">{tool.error ? "Error" : "Output"}</div>
                <pre className={tool.error ? "details-code details-code-error" : "details-code"}>{tool.result}</pre>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  )
}
