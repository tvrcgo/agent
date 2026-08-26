// 浮动输入胶囊栏（复刻 harness InputBar）：22px 圆角卡 + textarea + 工具栏
// 发送：Enter（无 Shift）；运行中主键变停止方块。

import { useRef } from "react"
import { ArrowUp, Plus, Square } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"

export function InputBar({
  value, onChange, onSend, onStop, disabled, running, placeholder, t,
}: {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  onStop: () => void
  disabled: boolean
  running: boolean
  placeholder: string
  t: Messages
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null)
  const empty = value.trim() === ""

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (!empty && !disabled) onSend()
    }
  }

  return (
    <div className="inputbar-root">
      <div className="inputbar-card">
        <div className="inputbar-scroll">
          <div className="inputbar-grow">
            <textarea
              ref={ref}
              className="inputbar-input"
              value={value}
              placeholder={placeholder}
              disabled={disabled}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={onKeyDown}
              rows={1}
            />
          </div>
        </div>
        <div className="inputbar-row">
          <div className="inputbar-tools">
            <button type="button" className="inputbar-add" title="attach" disabled={disabled}>
              <Plus size={16} />
            </button>
          </div>
          <div className="inputbar-trailing">
            <button
              type="button"
              className={cn("inputbar-primary", running && "inputbar-primary-stop")}
              disabled={disabled || (!running && empty)}
              onClick={running ? onStop : onSend}
              title={running ? t.stop : t.sendMessage}
            >
              {running ? <Square size={14} fill="currentColor" /> : <ArrowUp size={16} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
