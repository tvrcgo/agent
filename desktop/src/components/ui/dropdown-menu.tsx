import * as React from "react"
import { createPortal } from "react-dom"

import { cn } from "@/lib/utils"

type DropdownContextValue = {
  open: boolean
  setOpen: (open: boolean) => void
  triggerRef: React.RefObject<HTMLElement | null>
}

const DropdownContext = React.createContext<DropdownContextValue | undefined>(undefined)

function useDropdown() {
  const ctx = React.useContext(DropdownContext)
  if (!ctx) throw new Error("Dropdown components must be used within DropdownMenu")
  return ctx
}

function DropdownMenu({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false)
  const triggerRef = React.useRef<HTMLElement | null>(null)

  // 点击外部关闭
  React.useEffect(() => {
    if (!open) return
    function onPointerDown(e: PointerEvent) {
      const target = e.target as Node
      if (triggerRef.current?.contains(target)) return
      if (!document.querySelector("[data-slot=dropdown-content]")?.contains(target)) {
        setOpen(false)
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown)
      document.removeEventListener("keydown", onKeyDown)
    }
  }, [open])

  return <DropdownContext.Provider value={{ open, setOpen, triggerRef }}>{children}</DropdownContext.Provider>
}

function DropdownMenuTrigger({
  children,
}: {
  children: React.ReactElement<{ onClick?: React.MouseEventHandler; ref?: React.Ref<HTMLElement>; "aria-expanded"?: boolean }>
}) {
  const { open, setOpen, triggerRef } = useDropdown()
  return React.cloneElement(children, {
    ref: (node: HTMLElement | null) => {
      triggerRef.current = node
    },
    "aria-expanded": open,
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation()
      setOpen(!open)
      children.props.onClick?.(e)
    },
  })
}

function DropdownMenuContent({
  className,
  align = "end",
  children,
}: {
  className?: string
  align?: "start" | "end"
  children: React.ReactNode
}) {
  const { open, setOpen, triggerRef } = useDropdown()
  const [pos, setPos] = React.useState<{ top: number; left: number } | null>(null)

  React.useEffect(() => {
    if (!open || !triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    const width = 192
    const estimateHeight = 40 * React.Children.count(children) + 8
    const left = align === "end" ? rect.right - width : rect.left
    let top = rect.bottom + 6
    // 底部空间不足时向上弹
    if (top + estimateHeight > window.innerHeight - 8) {
      top = Math.max(8, rect.top - estimateHeight - 6)
    }
    setPos({ top, left: Math.max(8, Math.min(left, window.innerWidth - width - 8)) })
  }, [open, align, triggerRef, children])

  if (!open || !pos) return null

  return createPortal(
    <div
      data-slot="dropdown-content"
      className={cn(
        "z-50 min-w-48 rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-md",
        "animate-in fade-in-0 zoom-in-95",
        className,
      )}
      style={{ position: "fixed", top: pos.top, left: pos.left }}
    >
      {children}
    </div>,
    document.body,
  )
}

function DropdownMenuRadioGroup({
  value,
  onValueChange,
  children,
}: {
  value?: string
  onValueChange?: (value: string) => void
  children: React.ReactNode
}) {
  return (
    <div role="radiogroup">
      {React.Children.map(children, (child) => {
        if (React.isValidElement(child) && typeof child.type !== "string") {
          const itemProps = (child.props ?? {}) as { value?: string }
          return React.cloneElement(child as React.ReactElement<{ selected?: boolean; onSelect?: (v: string) => void }>, {
            selected: itemProps.value === value,
            onSelect: (v: string) => onValueChange?.(v),
          })
        }
        return child
      })}
    </div>
  )
}

function DropdownMenuRadioItem({
  value,
  selected,
  onSelect,
  children,
}: {
  value: string
  selected?: boolean
  onSelect?: (value: string) => void
  children: React.ReactNode
}) {
  const { setOpen } = useDropdown()
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      data-slot="dropdown-item"
      onClick={() => {
        onSelect?.(value)
        setOpen(false)
      }}
      className={cn(
        "relative flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm outline-none select-none hover:bg-accent hover:text-accent-foreground",
        selected && "bg-accent text-accent-foreground",
      )}
    >
      {children}
    </button>
  )
}

export { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem }
