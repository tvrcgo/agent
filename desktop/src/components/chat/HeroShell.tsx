// hero 空态（复刻 harness HeroShell）：品牌 logo + 标题 + 光晕。
// 无当前会话或空会话时显示；输入卡由 ConversationRoot 定位（hero 变体居中）。
// 不含会话 chip 下拉（原 chip 无点击行为、无实际用途，已移除）。

import { Bot } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"
import type { SessionMeta } from "./session-model"

export function HeroGlow({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 1051 468" fill="none" aria-hidden="true">
      <defs>
        <filter id="empty-glow" x="0" y="0" width="1051" height="468" filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
          <feFlood floodOpacity="0" result="BackgroundImageFix" />
          <feBlend mode="normal" in="SourceGraphic" in2="BackgroundImageFix" result="shape" />
          <feGaussianBlur stdDeviation="50" result="effect1_foregroundBlur" />
        </filter>
      </defs>
      <g filter="url(#empty-glow)">
        <ellipse cx="525.5" cy="234" rx="425.5" ry="134" fill="#6187D8" fillOpacity="0.08" />
      </g>
    </svg>
  )
}

export function HeroShell({ t, sessions }: {
  t: Messages
  sessions: SessionMeta[]
}) {
  return (
    <div className="hero-root">
      <div className="hero-stack">
        <div className="hero-headline">
          <span className="hero-fish">
            <Bot size={34} strokeWidth={1.5} />
          </span>
          <span className="hero-headline-text">{t.chatHeroTitle}</span>
        </div>
        <div className="hero-body">
          <HeroGlow className={cn("hero-glow")} />
          {sessions.length === 0 ? (
            <div className="hero-hint">{t.chatHeroHint}</div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
