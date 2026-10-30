"""三范式在同一框架、同一任务、同一工具集下的轨迹对比。

默认用脚本模型，零 Key、零网络、可重复：
    python demo/compare_paradigms.py

接真实模型（需要 .env / 环境变量）：
    python demo/compare_paradigms.py --real --config configs/default.yaml

输出：
    reports/paradigm_comparison.md
    reports/paradigm_comparison.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haloagent.agents import create_agent  # noqa: E402
from haloagent.core.config import AgentConfig, TraceConfig, load_config  # noqa: E402
from haloagent.models import create_model  # noqa: E402
from scenarios import TASK, build_registry, model_for  # noqa: E402

PARADIGMS = ["react", "plan_solve", "reflection"]
LABELS = {"react": "ReAct", "plan_solve": "Plan-and-Solve", "reflection": "Reflection"}
NOTES = {
    "react": "边想边做；每步用 Observation 修正判断，步数最少。",
    "plan_solve": "先出完整计划再逐步执行；计划可见，但计划质量决定上限。",
    "reflection": "先出草稿，再评审打分并修订；质量更高，token 成本也更高。",
}


def run_one(paradigm: str, *, real: bool, config_path: str) -> dict:
    config = load_config(config_path) if real else None
    trace_dir = ROOT / "runs"
    if real:
        assert config is not None
        llm = create_model(config.llm)
        agent_config = AgentConfig(
            name=f"Halo-{LABELS[paradigm]}",
            paradigm=paradigm,
            max_steps=6,
            reflect_rounds=2,
            quality_threshold=8,
        )
        memory_config = config.memory
        trace_config = TraceConfig(enabled=True, dir=str(trace_dir), redact=True, console=False)
    else:
        llm = model_for(paradigm)
        agent_config = AgentConfig(
            name=f"Halo-{LABELS[paradigm]}",
            paradigm=paradigm,
            max_steps=6,
            reflect_rounds=2,
            quality_threshold=8,
        )
        memory_config = None
        trace_config = TraceConfig(enabled=True, dir=str(trace_dir), redact=True, console=False)

    agent = create_agent(
        agent_config.name,
        llm,
        build_registry(),
        paradigm=paradigm,
        config=agent_config,
        memory_config=memory_config,
        trace_config=trace_config,
        trace_dir=trace_dir,
    )
    result = agent.run(TASK)
    excerpt = []
    if result.trace_path:
        path = Path(result.trace_path)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event["type"] in {"plan", "thought", "action", "tool_result", "reflection", "revision", "final"}:
                    data = event["data"]
                    if event["type"] == "action":
                        text = f"{data.get('tool')}({json.dumps(data.get('arguments', {}), ensure_ascii=False)})"
                    elif event["type"] == "tool_result":
                        text = ("OK " if data.get("ok") else "ERR ") + str(data.get("output", ""))
                    elif event["type"] == "reflection":
                        text = f"score={data.get('score')} issues={data.get('issues')} suggestion={data.get('suggestion')}"
                    elif event["type"] == "plan":
                        text = " / ".join(data.get("steps", []))
                    else:
                        text = data.get("thought") or data.get("content") or data.get("answer") or data.get("result")
                    excerpt.append({"type": event["type"], "text": str(text or "")[:260]})
                if len(excerpt) >= 10:
                    break
    return {
        "paradigm": paradigm,
        "label": LABELS[paradigm],
        "note": NOTES[paradigm],
        "status": result.status,
        "answer": result.answer,
        "llm_calls": result.metrics.llm_calls,
        "tool_calls": result.metrics.tool_calls,
        "steps": result.metrics.steps,
        "prompt_tokens": result.metrics.prompt_tokens,
        "completion_tokens": result.metrics.completion_tokens,
        "total_tokens": result.metrics.total_tokens,
        "estimated": result.metrics.estimated,
        "latency_s": round(result.latency_s, 3),
        "trace_path": result.trace_path,
        "excerpt": excerpt,
    }


def render_report(rows: list[dict], *, real: bool) -> str:
    mode = "真实模型" if real else "脚本模型（Offline Mock，验证链路，不代表回答质量）"
    lines = [
        "# 三种范式在同一框架下的轨迹对比",
        "",
        f"- 运行模式：**{mode}**",
        f"- 任务：{TASK}",
        "- 共同条件：同一个 `HaloAgent v1.0`、同一套工具（weather/calculator/current_time）、同一条 Trace schema。",
        "",
        "## 指标对比",
        "",
        "| 范式 | 状态 | LLM 调用 | 工具调用 | 步数 | Prompt Tokens | Completion Tokens | 总 Tokens | 耗时(s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} | {row['status']} | {row['llm_calls']} | {row['tool_calls']} | "
            f"{row['steps']} | {row['prompt_tokens']} | {row['completion_tokens']} | "
            f"{row['total_tokens']} | {row['latency_s']} |"
        )
    lines += ["", "> Token 为框架估算值（Mock 模式无供应商 usage）；真实模式以 API 返回为准。", ""]
    lines += ["## 轨迹摘录", ""]
    for row in rows:
        lines += [f"### {row['label']}", "", row["note"], ""]
        for item in row["excerpt"]:
            text = item["text"].replace("\n", " ")
            lines.append(f"- `{item['type']}` {text}")
        lines += ["", f"完整 trace：`{row['trace_path']}`", ""]
    lines += [
        "## 结论",
        "",
        "- **ReAct** 的步数与调用最少，适合“需要外部事实、但路径不必提前确定”的任务；缺点是容易在工具错误上绕路。",
        "- **Plan-and-Solve** 的计划是显式产物，适合步骤清晰、需要审计的任务；代价是规划与逐步执行带来更多 LLM 调用。",
        "- **Reflection** 多了评审与修订，答案通常更完整；代价是 token 成倍增加，且需要质量阈值防止无限改稿。",
        "- 三者共享 `run() -> RunResult`、Memory/Context、工具注册表与 Trace，所以对比的是**范式差异**，不是工程实现差异。",
        "",
        "## 复现",
        "",
        "```powershell",
        "python demo/compare_paradigms.py",
        "python demo/compare_paradigms.py --real --config configs/default.yaml",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="使用 configs 中的真实模型")
    parser.add_argument("--config", default="configs/default.yaml")
    args = parser.parse_args()

    rows = [run_one(paradigm, real=args.real, config_path=args.config) for paradigm in PARADIGMS]
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    suffix = "real" if args.real else "mock"
    report_path = reports / f"paradigm_comparison_{suffix}.md"
    report_path.write_text(render_report(rows, real=args.real), encoding="utf-8")
    (reports / f"paradigm_comparison_{suffix}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"{'范式':<16} {'状态':<10} {'LLM':>4} {'工具':>4} {'步数':>4} {'Tokens':>8} {'耗时':>6}")
    for row in rows:
        print(
            f"{row['label']:<16} {row['status']:<10} {row['llm_calls']:>4} {row['tool_calls']:>4} "
            f"{row['steps']:>4} {row['total_tokens']:>8} {row['latency_s']:>5.2f}s"
        )
    print(f"\n报告：{report_path}")
    return 0 if all(row["status"] == "success" for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
