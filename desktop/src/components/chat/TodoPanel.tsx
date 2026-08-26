// 底部坞任务面板（复刻 harness TodoPanel）：仅当模型调用 todo 工具生成任务清单时展示
// 状态圆环：done 绿对勾 / 未完成 灰虚线环。

import { useState } from "react"
import { Check, ChevronDown, ChevronUp, ListChecks } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Messages } from "@/lib/messages"
import type { TodoItem } from "./session-model"

function StatusGlyph({ done }: { done: boolean }) {
  if (done) {
    return (
      <svg viewBox="0 0 14 14" width="14" height="14" className="todo-glyph todo-glyph-completed" aria-hidden>
        <circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M4.5 7.2l1.8 1.8 3.2-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 14 14" width="14" height="14" className="todo-glyph todo-glyph-pending" aria-hidden>
      <circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="2.4 2.4" />
    </svg>
  )
}

export function TodoPanel({ todos, t }: { todos: TodoItem[]; t: Messages }) {
  const [open, setOpen] = useState(true)
  // 没有 todo 工具生成的任务清单就不展示
  if (todos.length === 0) return null

  const completed = todos.filter((x) => x.done).length
  const pending = todos.length - completed

  return (
    <section className="todo-panel">
      <div className="todo-body">
        <button type="button" className="todo-header" onClick={() => setOpen(!open)}>
          <ListChecks size={14} className="todo-lead" />
          <span className="todo-title">{t.todoTitle}</span>
          <span className="todo-progress">
            {t.todoCompleted} {completed} · {t.todoPending} {pending}
          </span>
          <span className="todo-chevron">{open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</span>
        </button>
        {open && (
          <ul className="todo-list">
            {todos.map((todo) => (
              <li key={todo.id} className="todo-item">
                <StatusGlyph done={todo.done} />
                <span className={cn("todo-content", todo.done && "todo-content-done")}>{todo.content}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
