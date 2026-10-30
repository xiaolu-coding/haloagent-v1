"""模型层抽象。

框架只依赖这个接口，不依赖任何具体厂商。替换模型 = 换一个 ChatModel 实现，
Agent / Workflow / Trace 全部不用改。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterator

from ..core.message import Message
from ..core.types import ModelResponse, StreamChunk


class ChatModel(ABC):
    provider: str = "base"
    model: str = ""

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """阻塞式调用，返回完整文本与 usage。"""

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[StreamChunk]:
        """流式调用，逐段产出文本；最后一个 chunk 带 usage（若供应商支持）。"""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} provider={self.provider} model={self.model}>"

    def close(self) -> None:  # pragma: no cover - 可选资源释放
        return None
