from __future__ import annotations

import pytest

from haloagent.core.errors import LLMError
from haloagent.core.message import Message
from haloagent.core.types import ModelResponse, StreamChunk, Usage
from haloagent.models import MockChatModel, RetryingChatModel


def test_mock_stream_joins_to_complete_text():
    model = MockChatModel("你好，世界！")
    chunks = list(model.stream([Message.user("hi")]))
    assert "".join(chunk.delta for chunk in chunks) == "你好，世界！"
    assert chunks[-1].done and chunks[-1].usage is not None


def test_retry_retries_only_retryable():
    class Flaky:
        provider = "fake"
        model = "fake-1"

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise LLMError("rate limit", retryable=True, status_code=429)
            return ModelResponse(text="ok", usage=Usage(1, 1), provider="fake", model="fake-1")

        def stream(self, messages, **kwargs):
            yield StreamChunk(delta="ok", done=True)

    inner = Flaky()
    model = RetryingChatModel(inner, max_attempts=3, base_delay=0, jitter=0, sleep=lambda _: None)
    response = model.complete([Message.user("hi")])
    assert response.text == "ok"
    assert model.retries == 2
    assert inner.calls == 3


def test_retry_does_not_retry_fatal():
    class Fatal:
        provider = "fake"
        model = "fake-1"

        def complete(self, messages, **kwargs):
            raise LLMError("bad key", retryable=False, status_code=401)

        def stream(self, messages, **kwargs):
            yield StreamChunk(done=True)

    model = RetryingChatModel(Fatal(), max_attempts=3, base_delay=0, sleep=lambda _: None)
    with pytest.raises(LLMError):
        model.complete([Message.user("hi")])
    assert model.retries == 0
