"""接真实模型：复制 .env.example 为 .env，填 Key 后运行。

    python examples/real_llm.py "帮我算 (3*5)+(2*38)"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haloagent import build_tools, create_agent, create_model, load_config  # noqa: E402


def main() -> int:
    task = sys.argv[1] if len(sys.argv) > 1 else "用一句话介绍你自己，并计算 12*12。"
    config = load_config(ROOT / "configs" / "default.yaml")
    llm = create_model(config.llm)
    agent = create_agent(
        config.agent.name,
        llm,
        build_tools(config.tools, config.plugins),
        paradigm="react",
        config=config.agent,
        memory_config=config.memory,
        trace_config=config.trace,
        trace_dir=ROOT / "runs",
    )
    result = agent.run(task)
    print(result.answer or result.error)
    print(result.metrics.to_dict())
    print("trace:", result.trace_path)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
