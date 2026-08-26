// 会话节点树模型（复刻 harness 的 UserMessageNode + AssistantTurn 结构）
// 存储格式：user 节点独立；assistant 回合内含 reasoning/tool/text blocks。
// 旧扁平格式会话不再兼容（清空）。

export type SessionBlock =
  | { type: "reasoning"; content: string }
  | { type: "tool"; id: string; name: string; args: Record<string, unknown>; result: string; error: boolean }
  | { type: "text"; content: string }

export type SessionNode =
  | { id: string; type: "user"; content: string; ts: number }
  | { id: string; type: "assistant"; ts: number; blocks: SessionBlock[] }

export type SessionMeta = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export type SessionData = SessionMeta & {
  nodes: SessionNode[]
}

/** 待写入的流式 assistant 回合（未完成前驻留在内存） */
export type PendingTurn = {
  nodeId: string
  reasoning: string[]
  tools: Map<string, { name: string; args: Record<string, unknown>; result: string; error: boolean }>
  texts: string[]
  ts: number
}

export function uuid(): string {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16)
  })
}

/** 将进行中的 assistant 回合固化为节点（done 时调用） */
export function finalizeTurn(turn: PendingTurn): Extract<SessionNode, { type: "assistant" }> {
  const blocks: SessionBlock[] = []
  for (const r of turn.reasoning) {
    if (r.trim()) blocks.push({ type: "reasoning", content: r })
  }
  for (const [toolId, t] of turn.tools.entries()) {
    blocks.push({ type: "tool", id: toolId, name: t.name, args: t.args, result: t.result, error: t.error })
  }
  for (const text of turn.texts) {
    if (text.trim()) blocks.push({ type: "text", content: text })
  }
  return { id: turn.nodeId, type: "assistant", ts: turn.ts, blocks }
}

/** 从持久化 session 数据构造 title（首条用户消息前 30 字） */
export function sessionTitle(nodes: SessionNode[]): string {
  const first = nodes.find((n) => n.type === "user")
  if (first && first.type === "user") return first.content.slice(0, 30)
  return "New Session"
}

/** todo 工具生成的任务清单条目 */
export type TodoItem = { id: string; content: string; done: boolean }

/**
 * 从会话节点树提取 todo 工具生成的任务清单。
 * 只有模型调用过 todo 工具才返回清单；取最近一次调用返回的完整快照。
 */
export function collectTodos(nodes: SessionNode[]): TodoItem[] {
  let todos: TodoItem[] = []
  for (const node of nodes) {
    if (node.type !== "assistant") continue
    for (const block of node.blocks) {
      if (block.type === "tool" && block.name === "todo" && !block.error) {
        const parsed = parseTodoResult(block.result)
        if (parsed) todos = parsed
      }
    }
  }
  return todos
}

function parseTodoResult(result: string): TodoItem[] | null {
  try {
    const data = JSON.parse(result)
    if (Array.isArray(data)) {
      return data.filter(
        (x): x is TodoItem =>
          x && typeof x === "object" && typeof x.id === "string" && typeof x.content === "string" && typeof x.done === "boolean",
      )
    }
  } catch {
    /* 忽略非 JSON 结果 */
  }
  return null
}
