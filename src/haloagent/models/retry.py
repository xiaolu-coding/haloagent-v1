"""可组合的重试层：只重试 LLMError.retryable 为 True 的错误。"""

from __future__ import annotations

import random
import time
from typing import Any, Iterator

from ..core.errors import LLMError
from ..core.message import Message
from ..core.types import ModelResponse, StreamChunk
from .base import ChatModel


class RetryingChatModel(ChatModel):
    def __init__(
        self,
        inner: ChatModel,
        *,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 20.0,
        jitter: float = 0.15,
        sleep: Any = time.sleep,
    ) -> None:
        self.inner = inner
        self.provider = inner.provider
        self.model = inner.model
        self.max_attempts = max(1, max_attempts)
        self.base_delay = max(0.0, base_delay)
        self.max_delay = max(0.0, max_delay)
        self.jitter = max(0.0, jitter)
        self._sleep = sleep
        self.retries = 0
        self.last_attempts = 0

    def _delay(self, attempt: int) -> float:
        base = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        return base + (random.random() * self.jitter if self.jitter else 0.0)

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        last: LLMError | None = None
        for attempt in range(1, self.max_attempts + 1):
            self.last_attempts = attempt
            try:
                return self.inner.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    **kwargs,
                )
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempt >= self.max_attempts:
                    exc.attempts = attempt
                    raise
                self.retries += 1
                self._sleep(self._delay(attempt))
        raise last or LLMError("retry exhausted")  # pragma: no cover

    def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[StreamChunk]:
        yielded = False
        for attempt in range(1, self.max_attempts + 1):
            self.last_attempts = attempt
            try:
                for chunk in self.inner.stream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    **kwargs,
                ):
                    yielded = True
                    yield chunk
                return
            except LLMError as exc:
                # 已经向用户吐出部分内容时不再重放，避免重复文本
                if yielded or not exc.retryable or attempt >= self.max_attempts:
                    exc.attempts = attempt
                    raise
                self.retries += 1
                self._sleep(self._delay(attempt))
