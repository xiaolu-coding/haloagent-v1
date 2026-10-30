"""无网络的脚本模型：单测、演示、CI 都靠它。

两种用法：
    MockChatModel("Final Answer: 42")
    MockChatModel(["第一轮输出", "第二轮输出"])

队列耗尽后返回一个显式的 ``Final Answer: [mock script exhausted]``，
避免 Agent 在 max_steps 里空转，也让测试失败原因一眼可见。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Iterator

from ..core.message import Message
from ..core.tokens import estimate_tokens
from ..core.types import ModelResponse, StreamChunk, Usage
from .base import ChatModel

Responder = Callable[[list[Message]], str]


class MockChatModel(ChatModel):
    provider = "mock"

    def __init__(
        self,
        script: str | Sequence[str] | Responder,
        *,
        model: str = "mock-scripted",
        chunk_size: int = 12,
        latency_s: float = 0.0,
    ) -> None:
        self._script = script
        self.model = model
        self.chunk_size = max(1, chunk_size)
        self.latency_s = latency_s
        self.calls: list[dict[str, Any]] = []
        self._cursor = 0

    # ------------------------------------------------------------------
    def _next_text(self, messages: list[Message]) -> str:
        if callable(self._script):
            return str(self._script(messages))
        if isinstance(self._script, str):
            return self._script
        if not self._script:
            return "Final Answer: [mock script is empty]"
        if self._cursor < len(self._script):
            text = self._script[self._cursor]
            self._cursor += 1
            return text
        return "Final Answer: [mock script exhausted]"

    @staticmethod
    def _total_chars(messages: list[Message]) -> int:
        return sum(len(m.content) for m in messages)

    # ------------------------------------------------------------------
    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        started = time.perf_counter()
        text = self._next_text(messages)
        self.calls.append(
            {
                "messages": [m.to_dict() for m in messages],
                "text": text,
                "tools": tools,
                "temperature": temperature,
            }
        )
        if self.latency_s:
            time.sleep(self.latency_s)
        prompt_tokens = estimate_tokens("\n".join(m.content for m in messages))
        completion_tokens = estimate_tokens(text)
        return ModelResponse(
            text=text,
            usage=Usage(prompt_tokens, completion_tokens, estimated=True),
            model=self.model,
            provider=self.provider,
            latency_s=time.perf_counter() - started,
            finish_reason="stop",
            strategy="mock",
        )

    def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[StreamChunk]:
        text = self._next_text(messages)
        self.calls.append(
            {
                "messages": [m.to_dict() for m in messages],
                "text": text,
                "tools": tools,
                "temperature": temperature,
                "stream": True,
            }
        )
        for index in range(0, len(text), self.chunk_size):
            yield StreamChunk(delta=text[index : index + self.chunk_size])
        yield StreamChunk(
            delta="",
            done=True,
            usage=Usage(
                estimate_tokens("\n".join(m.content for m in messages)),
                estimate_tokens(text),
                estimated=True,
            ),
        )
