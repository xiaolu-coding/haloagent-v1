"""OpenAI 兼容模型客户端。

几乎所有主流厂商都兼容 `/v1/chat/completions`，所以框架在这一层做一次适配，
而不是为每家写一个 SDK。SDK 自带重试被关闭（max_retries=0），统一交给
RetryingChatModel，这样 trace 里的重试次数才是真实的。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterator

from openai import OpenAI

from ..core.errors import LLMError
from ..core.message import Message, ToolCall
from ..core.types import ModelResponse, StreamChunk, Usage
from .base import ChatModel

# 这些错误重试没有意义
_FATAL_STATUS = {400, 401, 403, 404, 405, 422}
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _error_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def classify_error(exc: Exception, *, provider: str, model: str) -> LLMError:
    status = _error_status(exc)
    message = str(exc)
    retryable = status in _RETRYABLE_STATUS
    if status is None:
        lowered = message.lower()
        retryable = any(
            marker in lowered
            for marker in ("timeout", "timed out", "connection", "rate limit", "temporarily", "overloaded")
        )
    if status in _FATAL_STATUS:
        retryable = False
    return LLMError(
        f"模型调用失败（{provider}/{model}）：{message}",
        provider=provider,
        status_code=status,
        retryable=retryable,
    )


class OpenAICompatModel(ChatModel):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        provider: str = "custom",
        timeout: float = 60.0,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> None:
        if not model:
            raise LLMError("模型 ID 为空，请设置 llm.model 或 LLM_MODEL_ID")
        if not api_key:
            raise LLMError(f"缺少 {provider} 的 API Key")
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            timeout=timeout,
            max_retries=0,
        )

    # ------------------------------------------------------------------
    def _payload(
        self,
        messages: list[Message],
        temperature: float | None,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
        }
        token_limit = max_tokens or self.max_tokens
        if token_limit:
            payload["max_tokens"] = token_limit
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)
        return payload

    @staticmethod
    def _usage(raw: Any, text: str) -> Usage:
        if raw is None:
            return Usage(0, 0, estimated=True)
        prompt = int(getattr(raw, "prompt_tokens", 0) or 0)
        completion = int(getattr(raw, "completion_tokens", 0) or 0)
        return Usage(prompt, completion, estimated=False)

    @staticmethod
    def _tool_calls(message: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for raw in getattr(message, "tool_calls", None) or []:
            function = getattr(raw, "function", None)
            if function is None:
                continue
            try:
                arguments = json.loads(function.arguments or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = {"_raw": getattr(function, "arguments", "")}
            call_id = getattr(raw, "id", None) or f"call_{uuid.uuid4().hex[:12]}"
            calls.append(ToolCall(name=function.name, arguments=arguments, id=call_id))
        return calls

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
        try:
            response = self._client.chat.completions.create(
                **self._payload(messages, temperature, max_tokens, tools, **kwargs)
            )
        except Exception as exc:  # noqa: BLE001 - 统一转成框架异常
            raise classify_error(exc, provider=self.provider, model=self.model) from exc

        choice = response.choices[0] if getattr(response, "choices", None) else None
        message = getattr(choice, "message", None)
        text = (getattr(message, "content", None) or "") if message else ""
        return ModelResponse(
            text=text,
            usage=self._usage(getattr(response, "usage", None), text),
            model=self.model,
            provider=self.provider,
            latency_s=time.perf_counter() - started,
            finish_reason=getattr(choice, "finish_reason", None) if choice else None,
            tool_calls=self._tool_calls(message) if message else [],
            strategy="blocking",
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
        payload = self._payload(messages, temperature, max_tokens, tools, **kwargs)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        try:
            stream = self._client.chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001
            status = _error_status(exc)
            # 少数兼容端点不认识 stream_options，去掉再试一次
            if status in (400, 422):
                payload.pop("stream_options", None)
                try:
                    stream = self._client.chat.completions.create(**payload)
                except Exception as retry_exc:  # noqa: BLE001
                    raise classify_error(retry_exc, provider=self.provider, model=self.model) from retry_exc
            else:
                raise classify_error(exc, provider=self.provider, model=self.model) from exc

        usage: Usage | None = None
        try:
            for chunk in stream:
                raw_usage = getattr(chunk, "usage", None)
                if raw_usage is not None:
                    usage = self._usage(raw_usage, "")
                choices = getattr(chunk, "choices", None) or []
                if choices:
                    delta = getattr(choices[0], "delta", None)
                    text = getattr(delta, "content", None) if delta else None
                    if text:
                        yield StreamChunk(delta=text)
        except Exception as exc:  # noqa: BLE001
            raise classify_error(exc, provider=self.provider, model=self.model) from exc
        yield StreamChunk(done=True, usage=usage)
