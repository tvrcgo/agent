from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


InputType = Literal["chat", "command"]

OutputType = Literal["message", "thinking", "status", "data", "error", "confirm", "tool_call", "tool_result"]


@dataclass
class OutputMessage:
    type: OutputType
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    stream: bool = False


@dataclass
class InputMessage:
    type: InputType = "chat"
    content: str = ""
    action: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
