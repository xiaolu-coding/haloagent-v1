"""记忆与上下文管理。

- WindowMemory：按消息条数 + token 预算保留最近对话；
- SummaryMemory：溢出时把旧消息压成摘要，保留长期线索；
- ContextBuilder：在"系统提示 + 记忆 + 当前任务"之间做预算裁剪，永远保留最后一条用户消息。

Agent 的完整轨迹在 Trace 里，记忆只保存"下一轮对话真正需要看到的内容"。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.message import Message
from ..core.tokens import estimate_tokens

Summarizer = Callable[[list[Message]], str]


@dataclass
class MemoryStats:
    messages: int = 0
    estimated_tokens: int = 0
    dropped: int = 0
    summaries: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "messages": self.messages,
            "estimated_tokens": self.estimated_tokens,
            "dropped": self.dropped,
            "summaries": self.summaries,
        }


class Memory(ABC):
    @abstractmethod
    def add(self, message: Message) -> None: ...

    @abstractmethod
    def messages(self) -> list[Message]: ...

    @abstractmethod
    def clear(self) -> None: ...

    def extend(self, messages: list[Message]) -> None:
        for message in messages:
            self.add(message)

    def __len__(self) -> int:
        return len(self.messages())

    def stats(self) -> MemoryStats:
        items = self.messages()
        return MemoryStats(messages=len(items), estimated_tokens=sum(estimate_tokens(m.content) for m in items))


class NullMemory(Memory):
    def add(self, message: Message) -> None:
        return None

    def messages(self) -> list[Message]:
        return []

    def clear(self) -> None:
        return None


class WindowMemory(Memory):
    def __init__(self, *, max_messages: int = 24, max_tokens: int = 3000) -> None:
        self.max_messages = max(1, max_messages)
        self.max_tokens = max(1, max_tokens)
        self._messages: list[Message] = []
        self.dropped = 0

    def add(self, message: Message) -> None:
        self._messages.append(message)
        self._trim()

    def _trim(self) -> None:
        while len(self._messages) > self.max_messages:
            self._messages.pop(0)
            self.dropped += 1
        while len(self._messages) > 1 and self._tokens() > self.max_tokens:
            self._messages.pop(0)
            self.dropped += 1

    def _tokens(self) -> int:
        return sum(estimate_tokens(m.content) for m in self._messages)

    def messages(self) -> list[Message]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()
        self.dropped = 0

    def stats(self) -> MemoryStats:
        return MemoryStats(
            messages=len(self._messages),
            estimated_tokens=self._tokens(),
            dropped=self.dropped,
        )


class SummaryMemory(WindowMemory):
    """溢出时用摘要替换最旧的一半消息。"""

    def __init__(
        self,
        *,
        max_messages: int = 24,
        max_tokens: int = 3000,
        summarizer: Summarizer | None = None,
        keep_recent: int = 6,
    ) -> None:
        super().__init__(max_messages=max_messages, max_tokens=max_tokens)
        self.summarizer = summarizer
        self.keep_recent = max(2, keep_recent)
        self.summary: str = ""
        self.summaries = 0

    def _trim(self) -> None:
        while len(self._messages) > self.max_messages or (
            len(self._messages) > self.keep_recent and self._tokens() > self.max_tokens
        ):
            cut = max(1, len(self._messages) - self.keep_recent)
            old = self._messages[:cut]
            self._messages = self._messages[cut:]
            self.dropped += len(old)
            self._append_summary(old)

    def _append_summary(self, old: list[Message]) -> None:
        if self.summarizer is not None:
            try:
                text = self.summarizer(old)
            except Exception:  # noqa: BLE001 - 摘要失败不能中断主流程
                text = self._fallback_summary(old)
        else:
            text = self._fallback_summary(old)
        self.summary = (self.summary + "\n" + text).strip() if self.summary else text
        self.summaries += 1

    @staticmethod
    def _fallback_summary(old: list[Message]) -> str:
        lines = []
        for message in old:
            content = message.content.replace("\n", " ")
            if len(content) > 160:
                content = content[:160] + "…"
            lines.append(f"{message.role}: {content}")
        return "历史摘要：\n" + "\n".join(lines)

    def context_messages(self) -> list[Message]:
        items: list[Message] = []
        if self.summary:
            items.append(Message.system("以下是更早对话的摘要，请把它当作背景事实：\n" + self.summary))
        items.extend(self._messages)
        return items

    def stats(self) -> MemoryStats:
        stats = super().stats()
        stats.summaries = self.summaries
        if self.summary:
            stats.estimated_tokens += estimate_tokens(self.summary)
        return stats


@dataclass
class JsonMemoryStore:
    """把记忆落盘，跨进程继续对话；默认关闭，Demo 不依赖它。"""

    path: str | Path

    def save(self, memory: Memory) -> None:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [message.to_dict() for message in memory.messages()]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> list[Message]:
        path = Path(self.path)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        result: list[Message] = []
        for item in data:
            result.append(
                Message(
                    role=item.get("role", "user"),
                    content=item.get("content", ""),
                    name=item.get("name"),
                    tool_call_id=item.get("tool_call_id"),
                    metadata=item.get("metadata", {}),
                )
            )
        return result


class ContextStats:
    def __init__(
        self,
        *,
        original: int,
        kept: int,
        dropped: int,
        estimated_tokens: int,
        budget: int,
    ) -> None:
        self.original = original
        self.kept = kept
        self.dropped = dropped
        self.estimated_tokens = estimated_tokens
        self.budget = budget

    def to_dict(self) -> dict[str, int]:
        return {
            "original": self.original,
            "kept": self.kept,
            "dropped": self.dropped,
            "estimated_tokens": self.estimated_tokens,
            "budget": self.budget,
        }


class ContextBuilder:
    def __init__(self, *, max_tokens: int = 3000) -> None:
        self.max_tokens = max(256, max_tokens)

    def build(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        reserve_tokens: int = 400,
    ) -> tuple[list[Message], ContextStats]:
        system = [Message.system(system_prompt)] if system_prompt.strip() else []
        budget = max(256, self.max_tokens - reserve_tokens)
        used = sum(estimate_tokens(m.content) for m in system)
        kept: list[Message] = []
        # 从最新往旧保留；最后一条消息无条件保留
        for index, message in enumerate(reversed(messages)):
            cost = estimate_tokens(message.content)
            if index == 0 or used + cost <= budget:
                kept.append(message)
                used += cost
            else:
                break
        kept.reverse()
        dropped = len(messages) - len(kept)
        return system + kept, ContextStats(
            original=len(messages),
            kept=len(kept),
            dropped=dropped,
            estimated_tokens=used,
            budget=budget,
        )
