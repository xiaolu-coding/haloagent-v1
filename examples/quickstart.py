"""最小示例：不联网，跑通 ReAct + 工具 + Trace。

    python examples/quickstart.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haloagent import AgentConfig, MockChatModel, ReActAgent, default_registry  # noqa: E402


def main() -> int:
    model = MockChatModel(
        [
            'Thought: 需要算一下。\nAction: calculator\nAction Input: {"expression": "(3*5)+(2*38)"}',
            "Thought: 已经得到 91。\nFinal Answer: 3 张地铁票 15 元，2 份午餐 76 元，合计 91 元。",
        ]
    )
    agent = ReActAgent(
        "Quickstart",
        model,
        default_registry(["calculator"]),
        config=AgentConfig(max_steps=4),
    )
    result = agent.run("3 张地铁票每张 5 元，2 份午餐每份 38 元，一共多少钱？")
    print("status:", result.status)
    print("answer:", result.answer)
    print("metrics:", result.metrics.to_dict())
    print("trace:", result.trace_path)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
