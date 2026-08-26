// 助手 markdown 渲染（复刻 harness AssistantMarkdown）
// 用 react-markdown + remark-gfm；代码块/表格/列表/行内代码/粗体/链接/图片。

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Check, Copy } from "lucide-react"
import { useState } from "react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="md-copy-btn"
      aria-label="copy"
      onClick={() => {
        void navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 1000)
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  )
}

export function MarkdownText({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn("md-body", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className: c, children, ...props }) {
            const isBlock = c?.includes("language-") || String(children).includes("\n")
            const codeText = String(children).replace(/\n$/, "")
            if (isBlock) {
              return (
                <div className="md-codeblock">
                  <div className="md-codeblock-head">
                    <span className="md-codeblock-lang">{(c ?? "").replace("language-", "") || "code"}</span>
                    <CopyButton text={codeText} />
                  </div>
                  <pre>
                    <code className={c} {...props}>{codeText}</code>
                  </pre>
                </div>
              )
            }
            return <code className={c} {...props}>{children}</code>
          },
          table({ children }) {
            return <div className="md-table-wrap"><table>{children}</table></div>
          },
          a({ href, children }) {
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
