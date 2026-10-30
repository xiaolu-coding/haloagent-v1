"""消息系统。

设计原则是"对内丰富，对外兼容"：
- 内部保留 role / content / name / tool_call_id / tool_calls / metadata / timestamp；
- `to_openai()` 只输出当前协议需要的字段，避免把内部字段泄露给模型 API。

文本版 ReAct 只需要 user / assistant / system 三种角色；native tool calling
才需要 assistant.tool_calls 与 tool.tool_call_id，这里一并留好扩展位。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ToolCall:
    """一次原生 function call。文本版 ReAct 不使用它，但保留统一结构。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    # ------------------------------------------------------------------
    @classmethod
    def system(cls, content: str, **metadata: Any) -> "Message":
        return cls(role="system", content=content, metadata=metadata)

    @classmethod
    def user(cls, content: str, *, name: str | None = None, **metadata: Any) -> "Message":
        return cls(role="user", content=content, name=name, metadata=metadata)

    @classmethod
    def assistant(
        cls,
        content: str,
        *,
        tool_calls: list[ToolCall] | None = None,
        **metadata: Any,
    ) -> "Message":
        return cls(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
            metadata=metadata,
        )

    @classmethod
    def tool(cls, content: str, *, tool_call_id: str, name: str | None = None) -> "Message":
        return cls(role="tool", content=content, tool_call_id=tool_call_id, name=name)

    # ------------------------------------------------------------------
    def to_openai(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        if self.role == "assistant" and self.tool_calls:
            payload["tool_calls"] = [call.to_openai() for call in self.tool_calls]
            # OpenAI 协议里带 tool_calls 的 assistant 消息 content 可以为空
            if not self.content:
                payload["content"] = None
        if self.role == "tool" and self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload

    def to_dict(self) -> dict[str, Any]:
        data = self.to_openai()
        data["timestamp"] = self.timestamp
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data

    def preview(self, limit: int = 240) -> str:
        text = self.content.replace("\n", " ")
        if len(text) > limit:
            text = text[:limit] + "…"
        return f"[{self.role}] {text}"

    def __str__(self) -> str:
        return f"[{self.role}] {self.content}"


def messages_to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    return [m.to_openai() for m in messages]
