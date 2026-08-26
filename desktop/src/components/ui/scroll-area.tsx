import * as React from "react"

import { cn } from "@/lib/utils"

function ScrollArea({
  className,
  children,
  horizontal = false,
  ...props
}: React.ComponentProps<"div"> & { horizontal?: boolean }) {
  return (
    <div
      data-slot="scroll-area"
      className={cn("scroll-area min-h-0 min-w-0 flex-1 overflow-auto", horizontal && "overflow-x-auto", className)}
      {...props}
    >
      {children}
    </div>
  )
}

export { ScrollArea }
