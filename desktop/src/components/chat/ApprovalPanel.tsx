// 审批接管输入坞（复刻 harness ApprovalPanel）
// 有 confirm 待决时，底部输入坞整体变成审批卡：琥珀警示带 + 描述 + 右对齐拒绝/允许。

import { AlertTriangle } from "lucide-react"
import type { Messages } from "@/lib/messages"

export type ApprovalRequest = {
  confirmId: string
  description: string
}

export function ApprovalPanel({ request, onDecision, t }: {
  request: ApprovalRequest
  onDecision: (decision: "approve" | "deny") => void
  t: Messages
}) {
  return (
    <div className="approval-root">
      <div className="approval-card">
        <div className="approval-strip">
          <span className="approval-dot" />
          <span>{t.approvalTitle}</span>
        </div>
        <div className="approval-body" tabIndex={0}>
          <div className="approval-headline">{request.description || t.confirmDescription}</div>
        </div>
        <div className="approval-actionrow">
          <button
            type="button"
            className="approval-btn approval-btn-outline"
            onClick={() => onDecision("deny")}
          >
            {t.denyAction}
          </button>
          <button
            type="button"
            className="approval-btn approval-btn-primary"
            onClick={() => onDecision("approve")}
          >
            {t.approveAction}
          </button>
        </div>
      </div>
    </div>
  )
}
