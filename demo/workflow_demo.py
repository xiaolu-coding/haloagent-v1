"""低代码工作流端到端演示：YAML -> 工具 -> 条件 -> Agent -> 输出。

    python demo/workflow_demo.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haloagent.core.config import AgentConfig, TraceConfig  # noqa: E402
from haloagent.models import MockChatModel  # noqa: E402
from haloagent.tools import default_registry  # noqa: E402
from haloagent.workflow import WorkflowEngine, WorkflowSpec  # noqa: E402


def responder(messages) -> str:
    prompt = messages[-1].content if messages else ""
    weather = re.search(r"天气：(.+)", prompt)
    total = re.search(r"外套总价：(.+)", prompt)
    weather_text = weather.group(1).strip() if weather else "天气信息缺失"
    total_text = total.group(1).strip() if total else "预算信息缺失"
    if "=" in total_text:
        total_text = total_text.split("=")[-1].strip() + " 元"
    return (
        "Final Answer: 北京周末天气晴、18°C，适合出行；"
        f"3 件外套合计 {total_text}。"
        "建议：① 上午安排户外散步，午后转室内展馆；② 早晚温差大，带一件薄外套。"
    )


def main() -> int:
    registry = default_registry(["calculator", "current_time"])
    registry.load_plugin(ROOT / "examples" / "plugins" / "weather_fixture.py")
    spec = WorkflowSpec.from_yaml(ROOT / "examples" / "workflows" / "travel_brief.yaml")
    engine = WorkflowEngine(
        spec,
        MockChatModel(responder),
        registry,
        agent_config=AgentConfig(name="WorkflowBrief", paradigm="react", max_steps=4),
        trace_config=TraceConfig(enabled=True, dir=str(ROOT / "runs")),
        trace_dir=ROOT / "runs",
    )
    result = engine.run()
    print(result.output)
    print("\n节点状态：")
    for node in result.nodes.values():
        print(f"  {node.id:<12} {node.type:<10} {node.status:<8} {str(node.output)[:80]}")
    print(f"\nstatus={result.status}  llm_calls={result.metrics.llm_calls}  "
          f"tool_calls={result.metrics.tool_calls}  tokens={result.metrics.total_tokens}")
    print(f"trace: {result.trace_path}")
    return 0 if result.status == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
