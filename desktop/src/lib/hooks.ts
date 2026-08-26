import * as React from "react"

import { agentDesktop, type AgentState } from "@/lib/bridge"

// 订阅 agent 状态（初始拉取 + onState 推送）
export function useAgentState(): AgentState | null {
  const [state, setState] = React.useState<AgentState | null>(null)

  React.useEffect(() => {
    let mounted = true
    agentDesktop()
      .getState()
      .then((s) => {
        if (mounted) setState(s)
      })
      .catch(() => {})
    const unsubscribe = agentDesktop().onState((s) => {
      if (mounted) setState(s)
    })
    return () => {
      mounted = false
      unsubscribe()
    }
  }, [])

  return state
}
