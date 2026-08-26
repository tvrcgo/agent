import * as React from "react"
import { createPortal } from "react-dom"

import { CheckCircle2, CircleAlert, Loader2, X } from "lucide-react"
import { cn } from "@/lib/utils"

type ToastKind = "success" | "error" | "info" | "loading"
type ToastItem = {
  id: number
  kind: ToastKind
  title?: string
  description?: string
}

let toastId = 0
let listeners: Array<(items: ToastItem[]) => void> = []

function emit() {
  listeners.forEach((fn) => fn(ToastState.items))
}

export const ToastState = {
  items: [] as ToastItem[],
  subscribe(fn: (items: ToastItem[]) => void) {
    listeners.push(fn)
    return () => {
      listeners = listeners.filter((f) => f !== fn)
    }
  },
}

function push(item: Omit<ToastItem, "id">, duration: number) {
  const id = ++toastId
  ToastState.items = [...ToastState.items, { ...item, id }]
  emit()
  if (duration > 0) {
    setTimeout(() => {
      ToastState.items = ToastState.items.filter((t) => t.id !== id)
      emit()
    }, duration)
  }
}

function dismiss(id: number) {
  ToastState.items = ToastState.items.filter((t) => t.id !== id)
  emit()
}

export const toast = {
  success: (title: string, description?: string) => push({ kind: "success", title, description }, 2500),
  error: (title: string, description?: string) => push({ kind: "error", title, description }, 4000),
  info: (title: string, description?: string) => push({ kind: "info", title, description }, 2500),
  loading: (title: string, description?: string) => push({ kind: "loading", title, description }, 0),
  dismiss,
}

export function ToasterProvider() {
  const [items, setItems] = React.useState<ToastItem[]>(ToastState.items)

  React.useEffect(() => ToastState.subscribe(setItems), [])

  return createPortal(
    <div className="pointer-events-none fixed right-4 top-4 z-[100] flex w-80 flex-col gap-2">
      {items.map((item) => (
        <div
          key={item.id}
          className={cn(
            "pointer-events-auto flex items-start gap-3 rounded-xl border bg-popover p-3 text-popover-foreground shadow-lg animate-in slide-in-from-top-2 fade-in-0",
            item.kind === "error" && "border-destructive/30",
            item.kind === "success" && "border-emerald-500/30",
          )}
        >
          <div className="mt-0.5 shrink-0">
            {item.kind === "success" && <CheckCircle2 className="size-4 text-emerald-500" />}
            {item.kind === "error" && <CircleAlert className="size-4 text-destructive" />}
            {item.kind === "loading" && <Loader2 className="size-4 animate-spin" />}
            {item.kind === "info" && <CircleAlert className="size-4 text-muted-foreground" />}
          </div>
          <div className="min-w-0 flex-1">
            {item.title ? <div className="text-sm font-medium">{item.title}</div> : null}
            {item.description ? <div className="mt-0.5 text-sm text-muted-foreground">{item.description}</div> : null}
          </div>
          <button type="button" className="shrink-0 text-muted-foreground hover:text-foreground" onClick={() => dismiss(item.id)}>
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>,
    document.body,
  )
}
