from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job


class TodoTool(Tool):
    name = "todo"
    description = (
        "Maintain a task list (todos) for the current job. "
        "Actions: 'add' a task with content; 'complete'/'remove' a task by its id; "
        "'clear' all tasks; 'list' the current tasks. "
        "Each call returns the full current task list as a JSON array of "
        '{"id","content","done"} objects, so the client can render it as a checklist.'
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "complete", "remove", "clear", "list"],
                "description": "Operation to perform on the task list",
            },
            "content": {
                "type": "string",
                "description": "Task content; required for 'add'",
            },
            "id": {
                "type": "string",
                "description": "Task id; required for 'complete'/'remove'",
            },
        },
        "required": ["action"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        action = arguments.get("action", "list")

        # 任务清单存在 job.data（每 job 独立），跨回合保持
        todos = job.data.get("todos")
        if todos is None:
            todos = []
            job.data["todos"] = todos
        seq = job.data.get("_todo_seq", 0)

        if action == "add":
            content = str(arguments.get("content") or "").strip()
            if not content:
                return "Error: 'content' is required for 'add'"
            seq += 1
            job.data["_todo_seq"] = seq
            todos.append({"id": f"t{seq}", "content": content, "done": False})
        elif action == "complete":
            tid = str(arguments.get("id") or "")
            target = next((t for t in todos if t["id"] == tid), None)
            if target is None:
                return f"Error: task '{tid}' not found"
            target["done"] = True
        elif action == "remove":
            tid = str(arguments.get("id") or "")
            kept = [t for t in todos if t["id"] != tid]
            if len(kept) == len(todos):
                return f"Error: task '{tid}' not found"
            todos[:] = kept
        elif action == "clear":
            todos.clear()

        # list 及所有操作：返回完整清单，便于前端渲染
        return json.dumps(todos, ensure_ascii=False)
