"""三范式对比用的同一道题与脚本模型。

脚本模型只用于"无 Key 也能复现链路"；它返回的文本与真实模型协议完全一致，
Agent 并不会因为模型是 Mock 而走捷径。
"""

from __future__ import annotations

from pathlib import Path

from haloagent.models import MockChatModel
from haloagent.tools import ToolRegistry, default_registry

TASK = (
    "帮我规划北京一日游：先查北京今天的天气，再算 3 张地铁票（每张 5 元）"
    "和 2 份午餐（每份 38 元）的总价，最后判断是否适合户外活动并给出两条建议。"
)

ROOT = Path(__file__).resolve().parents[1]
WEATHER_PLUGIN = ROOT / "examples" / "plugins" / "weather_fixture.py"


def build_registry() -> ToolRegistry:
    registry = default_registry(["calculator", "current_time"])
    registry.load_plugin(WEATHER_PLUGIN)
    return registry


def react_script() -> list[str]:
    return [
        'Thought: 先查北京天气，才能判断是否适合户外。\nAction: weather\nAction Input: {"city": "北京"}',
        'Thought: 天气已拿到，接下来算交通和午餐总价。\nAction: calculator\nAction Input: {"expression": "(3*5)+(2*38)"}',
        (
            "Thought: 天气晴、总价 91 元，信息足够。\n"
            "Final Answer: 北京今天晴、18°C，适合户外。地铁票 15 元 + 午餐 76 元 = 91 元。"
            "建议：① 上午去公园，午后转室内展馆避开日晒；② 预留 10% 机动预算。"
        ),
    ]


def plan_script() -> list[str]:
    return [
        '["查询北京今天的天气", "计算 3 张地铁票与 2 份午餐的总价", "综合天气和预算判断是否适合户外"]',
        'Thought: 第一步查天气。\nAction: weather\nAction Input: {"city": "北京"}',
        "Step Result: 北京晴，18°C，湿度 40%。",
        'Thought: 第二步算总价。\nAction: calculator\nAction Input: {"expression": "(3*5)+(2*38)"}',
        "Step Result: 地铁 15 元 + 午餐 76 元 = 91 元。",
        "Step Result: 晴天、18°C、总价 91 元，适合户外；建议上午活动、带一件薄外套。",
        (
            "Final Answer: 北京今天晴、18°C，适合户外。3 张地铁票 15 元，2 份午餐 76 元，"
            "合计 91 元。建议上午安排户外、下午转室内，并预留少量机动预算。"
        ),
    ]


def reflection_script() -> list[str]:
    return [
        'Thought: 先查天气。\nAction: weather\nAction Input: {"city": "北京"}',
        'Thought: 再算总价。\nAction: calculator\nAction Input: {"expression": "(3*5)+(2*38)"}',
        "Final Answer: 北京晴，18°C，总价 91 元，适合户外。",
        '{"score": 6, "issues": ["没有列出地铁和午餐的分项", "建议不够具体"], "suggestion": "补分项，并给两条可执行建议"}',
        (
            "Final Answer: 北京今天晴、18°C，适合户外。费用分项：3 张地铁票 15 元，"
            "2 份午餐 76 元，合计 91 元。建议：① 上午安排公园或徒步，午后转室内展馆；"
            "② 带薄外套并预留 10% 机动预算。"
        ),
        '{"score": 9, "issues": [], "suggestion": "可以交付"}',
    ]


SCRIPTS = {
    "react": react_script,
    "plan_solve": plan_script,
    "reflection": reflection_script,
}


def model_for(paradigm: str) -> MockChatModel:
    return MockChatModel(SCRIPTS[paradigm](), model=f"mock-{paradigm}")
