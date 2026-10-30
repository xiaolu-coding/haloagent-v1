"""Plan-and-Solve：先规划，再逐步执行，最后综合。

与第 4 章的区别：计划不是一次性提示词，而是可观测的 plan 事件；每一步都可以
调用框架工具，工具调用/观察同样进入同一条 Trace。
"""

from __future__ import annotations

from ..core.errors import AgentParseError
from ..core.message import Message
from ..core.types import STATUS_SUCCESS
from .base import AgentOutcome, BaseAgent
from .parsing import guess_action_name, parse_action, parse_final_answer, parse_plan

PLAN_PROTOCOL = """你是 Plan-and-Solve 的规划器。

把用户任务拆成 3-6 个有先后依赖、可直接执行的步骤。要求：
1. 每一步都是一个可独立完成的动作，不要写空泛的"分析一下"；
2. 需要外部信息或计算的步骤，明确写出要查什么/算什么；
3. 只输出 JSON 字符串数组，不要输出解释、Markdown 围栏或多余文字。

示例：["查询北京今天的天气", "计算 3 张地铁票与 2 份午餐的总价", "根据天气和预算给出出行建议"]"""

STEP_PROTOCOL = """你是 Plan-and-Solve 的执行器。你只负责执行「当前步骤」，不要提前做后面的步骤。

## 可用工具
{tools}

## 输出协议（二选一）
A. 需要工具时：
Thought: 为什么需要它
Action: 工具名
Action Input: JSON 对象

B. 当前步骤完成时：
Step Result: 这一步的结论（简洁、可被后续步骤直接使用）

## 约束
- 一次只调用一个工具；Observation 由系统给出，不要编造。
- 如果已有信息足够完成当前步骤，直接输出 Step Result。
- 不要输出 Final Answer，最终答案由综合阶段生成。"""

SYNTH_PROTOCOL = """你是 Plan-and-Solve 的综合者。

根据原始任务、完整计划和每一步的执行结果，写一个直接回答用户问题的最终答案。
要求：结论明确、必要时给出关键数字、不要复述整个计划过程。
输出格式：Final Answer: <答案>"""


class PlanAndSolveAgent(BaseAgent):
    paradigm = "plan_solve"
    default_system_prompt = "你是一个先规划后执行、重视步骤可验证性的智能体。"

    # ------------------------------------------------------------------
    def _make_plan(self, task: str) -> list[str]:
        messages = self._context(system_suffix=PLAN_PROTOCOL, step=0, reserve_tokens=700)
        text = self._chat(messages, note="plan.generate", step=0)
        plan = parse_plan(text, max_steps=min(8, max(1, self.config.max_steps)))
        assert self.tracer is not None
        if not plan:
            plan = [task]
            self.tracer.emit("plan_fallback", raw=text, plan=plan)
        self.tracer.emit("plan", steps=plan, raw=text)
        return plan

    def _execute_step(self, task: str, plan: list[str], done: list[dict[str, str]], step: str, index: int) -> str:
        tool_text = self.tools.describe(with_schema=False)
        history = "\n".join(
            f"{item['index']}. {item['step']} -> {item['result']}" for item in done
        ) or "（还没有完成任何步骤）"
        prompt = (
            f"## 原始任务\n{task}\n\n"
            f"## 完整计划\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(plan, 1)) + "\n\n"
            f"## 已完成步骤与结果\n{history}\n\n"
            f"## 当前步骤（第 {index + 1} 步）\n{step}\n\n"
            "请执行当前步骤。需要工具就按协议调用；否则直接给 Step Result。"
        )
        local: list[Message] = [Message.user(prompt)]
        max_rounds = max(0, self.config.max_tool_rounds_per_step)
        last_text = ""

        for round_index in range(max_rounds + 1):
            messages = self._context(
                system_suffix=STEP_PROTOCOL.format(tools=tool_text),
                extra=local,
                step=index + 1,
                reserve_tokens=500,
            )
            text = self._chat(messages, note=f"plan.step.{index + 1}.round.{round_index + 1}", step=index + 1)
            last_text = text

            step_result = self._extract_step_result(text)
            if step_result is not None:
                assert self.tracer is not None
                self.tracer.emit("step_result", step=index + 1, title=step, result=step_result)
                return step_result

            guessed = guess_action_name(text)
            spec = self.tools.get(guessed) if guessed and guessed in self.tools else None
            single_param = None
            if spec and len(spec.required) == 1 and len(spec.parameters.get("properties", {})) == 1:
                single_param = spec.required[0]
            try:
                action, arguments, thought = parse_action(text, single_param=single_param)
            except AgentParseError:
                # 既不是 Step Result 也没有 Action：把整段当作这一步的结论
                assert self.tracer is not None
                self.tracer.emit("step_result", step=index + 1, title=step, result=text.strip(), fallback=True)
                return text.strip()

            assert self.tracer is not None
            self.tracer.emit("thought", step=index + 1, thought=thought)
            result = self._tool(action, arguments, step=index + 1)
            observation = result.output if result.ok else f"ERROR: {result.error or result.output}"
            local.append(Message.assistant(text))
            local.append(Message.user(f"Observation from {action}: {observation}"))

        assert self.tracer is not None
        self.tracer.emit("step_result", step=index + 1, title=step, result=last_text.strip(), exhausted=True)
        return last_text.strip()

    @staticmethod
    def _extract_step_result(text: str) -> str | None:
        import re

        match = re.search(r"(?is)(?:step\s*result|步骤结果)\s*[:：]\s*(.*)$", text or "")
        if not match:
            return None
        value = match.group(1).strip()
        return value or None

    def _synthesize(self, task: str, plan: list[str], results: list[dict[str, str]]) -> str:
        body = "\n".join(f"{item['index']}. {item['step']} -> {item['result']}" for item in results)
        prompt = f"## 原始任务\n{task}\n\n## 完整计划\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(plan, 1)
        ) + f"\n\n## 各步骤结果\n{body}\n\n请给出最终答案。"
        messages = self._context(
            system_suffix=SYNTH_PROTOCOL,
            extra=[Message.user(prompt)],
            step=len(plan) + 1,
            reserve_tokens=500,
        )
        text = self._chat(messages, note="plan.synthesize", step=len(plan) + 1)
        return parse_final_answer(text) or text.strip()

    # ------------------------------------------------------------------
    def _execute(self, task: str) -> AgentOutcome:
        plan = self._make_plan(task)
        results: list[dict[str, str]] = []
        for index, step in enumerate(plan):
            result = self._execute_step(task, plan, results, step, index)
            results.append({"index": index + 1, "step": step, "result": result})
            self.memory.add(
                Message.assistant(f"[计划步骤 {index + 1} 完成] {step}\n结果：{result}")
            )
        self.metrics.steps = len(plan)
        answer = self._synthesize(task, plan, results)
        assert self.tracer is not None
        self.tracer.emit("final", step=len(plan) + 1, answer=answer)
        return AgentOutcome(
            answer=answer,
            status=STATUS_SUCCESS,
            metadata={"plan": plan, "step_results": results},
        )
