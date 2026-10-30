"""Reflection：执行 -> 反思（评分） -> 优化，质量分达标提前停止。

初始稿复用 ReAct 循环，所以 Reflection 不是"只能聊天"的范式：
它同样可以查天气、算数，再把工具得到的事实带进反思。
"""

from __future__ import annotations

from ..core.message import Message
from ..core.types import STATUS_SUCCESS
from .base import AgentOutcome
from .parsing import parse_critique, parse_final_answer
from .react import ReActAgent

CRITIC_PROTOCOL = """你是严格的成果评审员。请对照原始任务检查当前答案。

只输出一个 JSON 对象，不要输出 Markdown 围栏或额外解释：
{"score": 1-10 的整数, "issues": ["具体问题1", "具体问题2"], "suggestion": "下一次修订最该做什么"}

评分标准：
- 事实是否准确、是否使用了工具返回的数据；
- 是否完整回答了任务的所有约束；
- 数字/单位/条件是否清楚；
- 是否存在编造、含糊或冗余。
8 分及以上表示可以交付。"""

REVISE_PROTOCOL = """你是修订者。根据评审意见改写答案，保留正确内容，修掉具体问题。

要求：
1. 不要解释你做了什么修改；
2. 不要输出评审意见；
3. 只输出：Final Answer: <修订后的完整答案>"""


class ReflectionAgent(ReActAgent):
    paradigm = "reflection"
    default_system_prompt = "你是一个愿意自我评审、用反馈迭代质量的智能体。"

    def _critique(self, task: str, current: str, round_index: int) -> tuple[int, list[str], str]:
        prompt = (
            f"## 原始任务\n{task}\n\n"
            f"## 当前答案\n{current}\n\n"
            "请评分并列出仍然存在的问题。只输出 JSON。"
        )
        messages = self._context(
            system_suffix=CRITIC_PROTOCOL,
            extra=[Message.user(prompt)],
            step=round_index,
            reserve_tokens=500,
        )
        text = self._chat(messages, note=f"reflection.critique.{round_index}", step=round_index)
        score, issues, suggestion = parse_critique(text)
        assert self.tracer is not None
        self.tracer.emit(
            "reflection",
            round=round_index,
            score=score,
            issues=issues,
            suggestion=suggestion,
            raw=text,
        )
        return score, issues, suggestion

    def _revise(self, task: str, current: str, score: int, issues: list[str], suggestion: str, round_index: int) -> str:
        prompt = (
            f"## 原始任务\n{task}\n\n"
            f"## 当前答案\n{current}\n\n"
            f"## 评审结果\n评分：{score}/10\n"
            f"问题：\n" + ("\n".join(f"- {item}" for item in issues) or "- 无") + "\n"
            f"建议：{suggestion or '保持事实准确，补全约束'}\n\n"
            "请输出修订后的最终答案。"
        )
        messages = self._context(
            system_suffix=REVISE_PROTOCOL,
            extra=[Message.user(prompt)],
            step=round_index,
            reserve_tokens=500,
        )
        text = self._chat(messages, note=f"reflection.revise.{round_index}", step=round_index)
        answer = parse_final_answer(text) or text.strip()
        assert self.tracer is not None
        self.tracer.emit("revision", round=round_index, answer=answer)
        return answer

    def _execute(self, task: str) -> AgentOutcome:
        draft = super()._execute(task)
        if draft.status != STATUS_SUCCESS or not draft.answer:
            return draft

        current = draft.answer
        critiques: list[dict[str, object]] = []
        rounds = max(0, self.config.reflect_rounds)
        final_score = 0
        draft_steps = int(draft.metadata.get("steps", 0))

        for round_index in range(1, rounds + 1):
            score, issues, suggestion = self._critique(task, current, round_index)
            final_score = score
            critiques.append({"round": round_index, "score": score, "issues": issues, "suggestion": suggestion})
            if score >= self.config.quality_threshold:
                break
            current = self._revise(task, current, score, issues, suggestion, round_index)

        self.metrics.steps = draft_steps + len(critiques)
        assert self.tracer is not None
        self.tracer.emit("final", step=self.metrics.steps, answer=current)
        return AgentOutcome(
            answer=current,
            status=STATUS_SUCCESS,
            metadata={
                "draft": draft.answer,
                "critiques": critiques,
                "rounds": len(critiques),
                "final_score": final_score,
                "steps": self.metrics.steps,
                "draft_metadata": draft.metadata,
            },
        )
