from __future__ import annotations

from pathlib import Path

from haloagent.core.config import AgentConfig, TraceConfig
from haloagent.models import MockChatModel
from haloagent.tools import ToolRegistry, tool
from haloagent.workflow import WorkflowEngine, WorkflowSpec


@tool(name="weather", description="查询天气")
def weather(city: str) -> str:
    """查询天气。"""
    return f"{city}：晴，18°C"


def test_workflow_condition_branch_and_output(tmp_path):
    yaml_text = """
name: test-flow
inputs:
  city: 北京
  price: 10
  count: 3
nodes:
  - id: w
    type: tool
    tool: weather
    args:
      city: ${inputs.city}
  - id: total
    type: tool
    tool: calculator
    args:
      expression: ${inputs.price} * ${inputs.count}
  - id: rainy
    type: condition
    if: contains(${nodes.w.output}, '雨')
  - id: umbrella
    type: template
    when: ${nodes.rainy.output}
    template: 带伞
  - id: answer
    type: agent
    paradigm: simple
    task: |
      天气：${nodes.w.output}
      总价：${nodes.total.output}
      提醒：${nodes.umbrella.output}
  - id: out
    type: output
    value: ${nodes.answer.output}
"""
    path = tmp_path / "flow.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    spec = WorkflowSpec.from_yaml(path)

    registry = ToolRegistry()
    registry.register(weather)
    from haloagent.tools.builtin import calculator

    registry.register(calculator)

    engine = WorkflowEngine(
        spec,
        MockChatModel("Final Answer: 出行简报完成"),
        registry,
        agent_config=AgentConfig(paradigm="simple", max_steps=2),
        trace_config=TraceConfig(enabled=True, dir=str(tmp_path)),
        trace_dir=tmp_path,
    )
    result = engine.run()
    assert result.status == "success"
    assert result.output == "出行简报完成"
    assert result.nodes["rainy"].output is False
    assert result.nodes["umbrella"].status == "skipped"
    assert result.nodes["total"].output == "10 * 3 = 30"
    assert result.metrics.tool_calls == 2
    assert result.trace_path and Path(result.trace_path).exists()


def test_workflow_cycle_detection(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
name: bad
nodes:
  - id: a
    type: template
    template: a
    depends_on: [b]
  - id: b
    type: template
    template: b
    depends_on: [a]
""",
        encoding="utf-8",
    )
    spec = WorkflowSpec.from_yaml(path)
    try:
        WorkflowEngine(spec, MockChatModel("x"), ToolRegistry())
    except Exception as exc:  # noqa: BLE001
        assert "循环依赖" in str(exc)
    else:
        raise AssertionError("应当检测到循环依赖")
