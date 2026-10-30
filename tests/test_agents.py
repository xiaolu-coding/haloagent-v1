from __future__ import annotations

import json
from pathlib import Path

from haloagent.agents import PlanAndSolveAgent, ReActAgent, ReflectionAgent, SimpleAgent
from haloagent.core.config import AgentConfig, TraceConfig
from haloagent.core.message import Message
from haloagent.models import MockChatModel
from haloagent.tools import ToolRegistry, tool
from haloagent.trace import Tracer


@tool(name="weather", description="查询天气")
def weather(city: str) -> str:
    """查询天气。

    Args:
        city: 城市
    """
    return f"{city}：晴，18°C"


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(weather)
    from haloagent.tools.builtin import calculator

    registry.register(calculator)
    return registry


def make_agent(cls, model, tmp_path, **config):
    config.setdefault("max_steps", 4)
    cfg = AgentConfig(**config)
    return cls(
        "Tester",
        model,
        make_registry(),
        config=cfg,
        trace_config=TraceConfig(enabled=True, dir=str(tmp_path), console=False),
        trace_dir=tmp_path,
    )


def test_react_success_and_trace(tmp_path):
    model = MockChatModel(
        [
            'Thought: 需要查天气\nAction: weather\nAction Input: {"city": "北京"}',
            "Thought: 已经拿到观察\nFinal Answer: 北京晴，18°C。",
        ]
    )
    agent = make_agent(ReActAgent, model, tmp_path)
    result = agent.run("北京天气如何？")

    assert result.ok
    assert result.answer == "北京晴，18°C。"
    assert result.metrics.llm_calls == 2
    assert result.metrics.tool_calls == 1
    assert result.trace_path and Path(result.trace_path).exists()

    events = [json.loads(line) for line in Path(result.trace_path).read_text(encoding="utf-8").splitlines()]
    types = [event["type"] for event in events]
    assert "llm_request" in types and "tool_result" in types and "run_end" in types
    assert sum(1 for item in types if item == "llm_response") == 2


def test_react_parse_error_then_self_correct(tmp_path):
    model = MockChatModel(
        [
            "我觉得应该先查一下。",
            'Thought: 修正格式\nAction: calculator\nAction Input: {"expression": "2+2"}',
            "Thought: 得到 4\nFinal Answer: 4",
        ]
    )
    agent = make_agent(ReActAgent, model, tmp_path)
    result = agent.run("2+2 等于几？")
    assert result.ok
    assert result.answer == "4"
    assert result.metadata["parse_failures"] == 1


def test_react_max_steps(tmp_path):
    model = MockChatModel('Thought: 还在想\nAction: calculator\nAction Input: {"expression": "1+1"}')
    agent = make_agent(ReActAgent, model, tmp_path, max_steps=1)
    result = agent.run("算一下")
    assert result.status == "max_steps"
    assert result.answer is None


def test_plan_and_solve(tmp_path):
    model = MockChatModel(
        [
            '["查询北京天气", "计算 45 * 2"]',
            'Thought: 查天气\nAction: weather\nAction Input: {"city": "北京"}',
            "Step Result: 北京晴，18°C",
            'Thought: 算总价\nAction: calculator\nAction Input: {"expression": "45*2"}',
            "Step Result: 90 元",
            "Final Answer: 北京晴，总价 90 元。",
        ]
    )
    agent = make_agent(PlanAndSolveAgent, model, tmp_path)
    result = agent.run("北京天气 + 45*2")
    assert result.ok
    assert "90" in (result.answer or "")
    assert result.metadata["plan"] == ["查询北京天气", "计算 45 * 2"]
    assert result.metrics.tool_calls == 2


def test_reflection_revises_and_scores(tmp_path):
    model = MockChatModel(
        [
            'Thought: 算一下\nAction: calculator\nAction Input: {"expression": "21*2"}',
            "Final Answer: 42",
            '{"score": 5, "issues": ["缺少单位"], "suggestion": "补上元"}',
            "Final Answer: 42 元",
        ]
    )
    agent = make_agent(ReflectionAgent, model, tmp_path, reflect_rounds=1, quality_threshold=8)
    result = agent.run("21*2 是多少？")
    assert result.ok
    assert result.answer == "42 元"
    assert result.metadata["final_score"] == 5
    assert result.metadata["rounds"] == 1


def test_simple_agent(tmp_path):
    model = MockChatModel("Final Answer: 你好，我是 Halo。")
    agent = make_agent(SimpleAgent, model, tmp_path)
    result = agent.run("你好")
    assert result.ok and "Halo" in (result.answer or "")
    assert result.metrics.llm_calls == 1


def test_trace_redacts_secrets():
    tracer = Tracer("r1", "a", redact=True)
    event = tracer.emit("llm_request", api_key="sk-secret", messages=[Message.user("hi").to_dict()])
    assert event.data["api_key"] == "***"
