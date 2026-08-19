"""I/O 消息模型。

`msg_input` / `msg_output` 是 agent 与外部交流的 I/O 通道，
消息体（`InputMessage` / `OutputMessage`）定义在此，供 loop、core/tool、
以及各插件引用。独立成模块避免 core 内部（tool.py ← loop.py）循环依赖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


OutputType = Literal["message", "thinking", "status", "data", "error", "confirm", "tool_call", "tool_result"]


@dataclass
class OutputMessage:
    type: OutputType
    content: str = ""
    data: dict = field(default_factory=dict)
    session_id: str = ""
    stream: bool = False


@dataclass
class InputMessage:
    content: str
    type: str = "chat"
    action: str = ""
    data: dict = field(default_factory=dict)
    session_id: str = ""
