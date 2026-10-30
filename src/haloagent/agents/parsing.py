"""范式协议解析。

LLM 不会永远守格式，所以解析器要满足两点：
1. 尽量宽容（中英文冒号、代码围栏、JSON/单参数裸值）；
2. 解析失败时给出明确的修复提示，让 Agent 能自我纠正，而不是直接崩。
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from ..core.errors import AgentParseError

_FINAL_RE = re.compile(r"(?ims)^\s*(?:final\s*answer|最终答案|最终回答)\s*[:：]\s*(.*)\Z")
_ACTION_RE = re.compile(r"(?im)^\s*(?:action|行动)\s*[:：]\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*$")
_INPUT_RE = re.compile(r"(?is)(?:action\s*input|行动输入)\s*[:：]\s*(.*)$")
_THOUGHT_RE = re.compile(r"(?is)^\s*(?:thought|思考)\s*[:：]\s*(.*)$")


def strip_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def parse_final_answer(text: str) -> str | None:
    match = _FINAL_RE.search(text or "")
    if not match:
        return None
    answer = strip_fence(match.group(1))
    return answer or None


def extract_thought(text: str) -> str:
    body = (text or "").strip()
    match = _THOUGHT_RE.match(body)
    if match:
        body = match.group(1).strip()
    # 截断到 Action / Final Answer 之前
    body = re.split(r"(?im)^\s*(?:action|行动|final\s*answer|最终答案)\s*[:：]", body, maxsplit=1)[0]
    return body.strip()


def _coerce_arguments(raw: str, *, single_param: str | None) -> dict[str, Any]:
    value = strip_fence(raw).strip()
    # 去掉模型可能附带的 Observation 续写
    value = re.split(r"(?im)^\s*observation\s*[:：]", value, maxsplit=1)[0].strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            parsed = value
    if isinstance(parsed, dict):
        return parsed
    if single_param:
        if isinstance(parsed, (list, tuple)) and len(parsed) == 1:
            return {single_param: parsed[0]}
        return {single_param: parsed}
    raise AgentParseError(
        "Action Input 需要是 JSON 对象，例如 {\"city\": \"北京\"}；"
        f"当前收到：{value[:200]}"
    )


def guess_action_name(text: str) -> str | None:
    match = _ACTION_RE.search(text or "")
    return match.group(1) if match else None


def parse_action(text: str, *, single_param: str | None = None) -> tuple[str, dict[str, Any], str]:
    """返回 (tool_name, arguments, thought)。找不到 Action 时抛 AgentParseError。"""
    match = _ACTION_RE.search(text or "")
    if not match:
        raise AgentParseError(
            "没有找到 Action。请严格输出：\n"
            "Thought: ...\nAction: 工具名\nAction Input: {\"参数\": \"值\"}\n"
            "或者：Final Answer: 最终答案"
        )
    tool = match.group(1)
    thought = extract_thought(text[: match.start()])
    input_match = _INPUT_RE.search(text[match.end() :])
    if not input_match:
        raise AgentParseError("找到 Action 但没有找到 Action Input，请补上 JSON 参数")
    arguments = _coerce_arguments(input_match.group(1), single_param=single_param)
    return tool, arguments, thought


def parse_plan(text: str, *, max_steps: int = 8) -> list[str]:
    """从规划器输出中提取步骤列表。"""
    body = strip_fence(text or "")
    array_match = re.search(r"\[.*\]", body, re.S)
    if array_match:
        try:
            data = json.loads(array_match.group(0))
            if isinstance(data, list):
                steps: list[str] = []
                for item in data:
                    if isinstance(item, str) and item.strip():
                        steps.append(item.strip())
                    elif isinstance(item, dict):
                        value = item.get("step") or item.get("task") or item.get("description") or item.get("action")
                        if value:
                            steps.append(str(value).strip())
                if steps:
                    return steps[:max_steps]
        except json.JSONDecodeError:
            pass

    steps = []
    for line in body.splitlines():
        stripped = line.strip()
        match = re.match(r"^(?:\d+[.)、]|[-*])\s+(.+)$", stripped)
        if match:
            steps.append(match.group(1).strip())
    return steps[:max_steps]


def parse_critique(text: str) -> tuple[int, list[str], str]:
    """解析反思 JSON；容忍外面包一层解释文字。"""
    body = strip_fence(text or "")
    match = re.search(r"\{.*\}", body, re.S)
    data: dict[str, Any] = {}
    if match:
        try:
            loaded = json.loads(match.group(0))
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            data = {}
    score = data.get("score", data.get("评分"))
    try:
        score_int = max(1, min(10, int(score)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        score_match = re.search(r"(?:score|评分)\s*[:：]\s*(\d+)", body, re.I)
        score_int = max(1, min(10, int(score_match.group(1)))) if score_match else 5

    issues_raw = data.get("issues", data.get("问题", []))
    if isinstance(issues_raw, str):
        issues = [issues_raw]
    elif isinstance(issues_raw, list):
        issues = [str(item) for item in issues_raw if str(item).strip()]
    else:
        issues = []
    suggestion = str(data.get("suggestion", data.get("建议", "")) or "").strip()
    if not issues and not suggestion:
        # 没有结构化输出时，把原文当作反馈
        suggestion = body[:1200]
    return score_int, issues, suggestion
