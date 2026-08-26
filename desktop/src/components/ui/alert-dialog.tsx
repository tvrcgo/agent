import * as React from "react"
import { createPortal } from "react-dom"

import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"

type AlertDialogContextValue = {
  open: boolean
  setOpen: (open: boolean) => void
}

const AlertDialogContext = React.createContext<AlertDialogContextValue | undefined>(undefined)

function useAlertDialog() {
  const ctx = React.useContext(AlertDialogContext)
  if (!ctx) throw new Error("AlertDialog components must be used within AlertDialog")
  return ctx
}

function AlertDialog({
  open,
  onOpenChange,
  children,
}: {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  children: React.ReactNode
}) {
  // 支持受控（open/onOpenChange）与非受控（内部管理）
  const [internalOpen, setInternalOpen] = React.useState(false)
  const isControlled = open !== undefined
  const isOpen = isControlled ? open : internalOpen

  const setOpen = React.useCallback(
    (next: boolean) => {
      if (isControlled) {
        onOpenChange?.(next)
      } else {
        setInternalOpen(next)
      }
    },
    [isControlled, onOpenChange],
  )

  React.useEffect(() => {
    if (!isOpen) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [isOpen, setOpen])

  return <AlertDialogContext.Provider value={{ open: isOpen, setOpen }}>{children}</AlertDialogContext.Provider>
}

function AlertDialogTrigger({ children }: { children: React.ReactElement<{ onClick?: React.MouseEventHandler }> }) {
  const { open, setOpen } = useAlertDialog()
  return React.cloneElement(children, {
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation()
      setOpen(!open)
      children.props.onClick?.(e)
    },
  })
}

function AlertDialogContent({ className, children, ...props }: React.ComponentProps<"div">) {
  const { open, setOpen } = useAlertDialog()
  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center" role="alertdialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/50 animate-in fade-in-0" onClick={() => setOpen(false)} />
      <div
        data-slot="alert-dialog-content"
        className={cn(
          "relative z-10 w-full max-w-md rounded-3xl border border-border bg-card p-6 text-card-foreground shadow-xl outline-none animate-in fade-in-0 zoom-in-95",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}

function AlertDialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-dialog-header" className={cn("flex flex-col gap-1.5", className)} {...props} />
}

function AlertDialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-dialog-footer" className={cn("mt-4 flex flex-row justify-end gap-2", className)} {...props} />
}

function AlertDialogTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-dialog-title" className={cn("font-heading text-lg font-semibold", className)} {...props} />
}

function AlertDialogDescription({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-dialog-description" className={cn("text-sm text-muted-foreground", className)} {...props} />
}

function AlertDialogCancel({ className, ...props }: React.ComponentProps<"button">) {
  const { setOpen } = useAlertDialog()
  return <button type="button" data-slot="alert-dialog-cancel" className={cn(buttonVariants({ variant: "ghost" }), className)} onClick={() => setOpen(false)} {...props} />
}

function AlertDialogAction({
  className,
  variant,
  onClick,
  ...props
}: React.ComponentProps<"button"> & { variant?: "default" | "destructive" | "outline" | "secondary" | "ghost" }) {
  const { setOpen } = useAlertDialog()
  return (
    <button
      type="button"
      data-slot="alert-dialog-action"
      className={cn(buttonVariants({ variant: variant === "destructive" ? "destructive" : "default" }), className)}
      onClick={(e) => {
        setOpen(false)
        onClick?.(e)
      }}
      {...props}
    />
  )
}

export { AlertDialog, AlertDialogTrigger, AlertDialogContent, AlertDialogHeader, AlertDialogFooter, AlertDialogTitle, AlertDialogDescription, AlertDialogCancel, AlertDialogAction }
