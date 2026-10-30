"""SimpleAgent：单轮对话基线，用来回答“不用范式时框架长什么样”。"""

from __future__ import annotations

from ..core.types import STATUS_SUCCESS
from .base import AgentOutcome, BaseAgent
from .parsing import parse_final_answer


class SimpleAgent(BaseAgent):
    paradigm = "simple"
    default_system_prompt = "你是一个简洁、准确、乐于助人的 AI 助手。"

    def _execute(self, task: str) -> AgentOutcome:
        self.metrics.steps = 1
        messages = self._context(step=1, reserve_tokens=500)
        text = self._chat(messages, note="simple.chat", step=1)
        answer = parse_final_answer(text) or text.strip()
        assert self.tracer is not None
        self.tracer.emit("final", step=1, answer=answer)
        return AgentOutcome(answer=answer, status=STATUS_SUCCESS, metadata={"steps": 1})
