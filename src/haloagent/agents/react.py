"""ReAct：Thought -> Action -> Observation 循环。"""

from __future__ import annotations

from typing import Any

from ..core.errors import AgentParseError
from ..core.message import Message
from ..core.types import STATUS_MAX_STEPS, STATUS_PARSE_ERROR, STATUS_SUCCESS
from .base import AgentOutcome, BaseAgent
from .parsing import guess_action_name, parse_action, parse_final_answer

REACT_PROTOCOL = """你是一个 ReAct 智能体：用「思考 -> 行动 -> 观察」的方式解决问题。

## 可用工具
{tools}

## 输出协议（每一步只能二选一）
A. 需要调用工具时，严格输出三行：
Thought: 你为什么需要这个工具（一句话）
Action: 工具名
Action Input: 一个 JSON 对象，例如 {{"city": "北京"}}

B. 信息足够时，输出：
Thought: 你已经掌握了什么
Final Answer: 给用户的最终答案

## 约束
1. 一次只调用一个工具；不要自己编造 Observation，Observation 由系统返回。
2. Action Input 必须是合法 JSON 对象；参数名必须与 Schema 一致。
3. 如果工具返回错误，先根据错误修正参数或换一种做法，不要重复同样的失败调用。
4. 最多 {max_steps} 步；能在当前步回答就直接给 Final Answer，不要为了用工具而用工具。"""


class ReActAgent(BaseAgent):
    paradigm = "react"
    default_system_prompt = "你是一个善于使用工具、从观察中修正判断的智能体。"

    def _react_system(self) -> str:
        return REACT_PROTOCOL.format(
            tools=self.tools.describe(with_schema=True),
            max_steps=self.config.max_steps,
        )

    def _execute(self, task: str) -> AgentOutcome:
        parse_failures = 0
        last_text = ""
        max_steps = max(1, self.config.max_steps)

        for step in range(1, max_steps + 1):
            self.metrics.steps = step
            messages = self._context(system_suffix=self._react_system(), step=step, reserve_tokens=500)
            text = self._chat(messages, note=f"react.step.{step}", step=step)
            last_text = text

            final = parse_final_answer(text)
            if final:
                assert self.tracer is not None
                self.tracer.emit("final", step=step, answer=final)
                return AgentOutcome(
                    answer=final,
                    status=STATUS_SUCCESS,
                    metadata={"steps": step, "parse_failures": parse_failures},
                )

            guessed = guess_action_name(text)
            spec = self.tools.get(guessed) if guessed and guessed in self.tools else None
            single_param = None
            if spec and len(spec.required) == 1 and len(spec.parameters.get("properties", {})) == 1:
                single_param = spec.required[0]
            try:
                action, arguments, thought = parse_action(text, single_param=single_param)
            except AgentParseError as exc:
                parse_failures += 1
                assert self.tracer is not None
                self.tracer.emit("parse_error", step=step, error=str(exc), raw=text)
                if parse_failures >= 2:
                    return AgentOutcome(
                        answer=None,
                        status=STATUS_PARSE_ERROR,
                        metadata={"steps": step, "parse_failures": parse_failures, "last_output": text},
                    )
                # 把错误输出与修复指令都放回上下文，给模型一次自我纠正的机会
                self.memory.add(Message.assistant(text))
                self.memory.add(
                    Message.user(
                        "格式错误：" + str(exc) + "\n请只输出规定格式，不要解释。"
                    )
                )
                continue

            assert self.tracer is not None
            self.tracer.emit("thought", step=step, thought=thought)
            self.memory.add(Message.assistant(text))

            result = self._tool(action, arguments, step=step)
            observation = result.output if result.ok else f"ERROR: {result.error or result.output}"
            self.memory.add(
                Message.user(
                    f"Observation from {action}: {observation}\n"
                    "请基于这个观察继续；如果信息已经足够，直接给 Final Answer。",
                    name=action,
                )
            )

        return AgentOutcome(
            answer=None,
            status=STATUS_MAX_STEPS,
            metadata={"steps": max_steps, "parse_failures": parse_failures, "last_output": last_text},
        )

    def run_stream(self, task: str, *, on_delta: Any = None) -> str:
        """演示流式输出：用同一个模型层直接生成一次回答（不进入多步循环）。"""
        return self.think(task, stream=True, on_delta=on_delta)
