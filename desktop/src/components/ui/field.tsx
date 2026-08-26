import * as React from "react"

import { cn } from "@/lib/utils"

// 复刻目标项目的 Field 组件（横向字段：标题/描述 + 控件）
function Field({
  orientation = "vertical",
  className,
  ...props
}: React.ComponentProps<"div"> & { orientation?: "horizontal" | "vertical" }) {
  return (
    <div
      data-slot="field"
      className={cn(
        orientation === "horizontal"
          ? "flex items-center justify-between gap-6 py-3"
          : "flex flex-col gap-1.5",
        className,
      )}
      {...props}
    />
  )
}

function FieldContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="field-content" className={cn("flex min-w-0 flex-col gap-0.5", className)} {...props} />
}

function FieldTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="field-title" className={cn("text-sm font-medium", className)} {...props} />
}

function FieldDescription({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="field-description" className={cn("text-sm text-muted-foreground", className)} {...props} />
}

function FieldGroup({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="field-group" className={cn("flex flex-col gap-4", className)} {...props} />
}

export { Field, FieldContent, FieldTitle, FieldDescription, FieldGroup }
