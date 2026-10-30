"""HaloAgent 命令行。

常用：
    haloagent doctor
    haloagent run "帮我算 (3*5)+(2*38)" --mock
    haloagent run "..." --config configs/default.yaml --trace
    haloagent tools list
    haloagent tools call calculator --args '{"expression": "1+1"}'
    haloagent workflow examples/workflows/travel_brief.yaml --input city=北京 --mock
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .agents import build_tools, create_agent
from .core.config import AppConfig, load_config
from .core.errors import HaloAgentError
from .models.router import create_model, resolve_provider
from .workflow import WorkflowEngine, WorkflowSpec


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _parse_inputs(items: list[str] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--input 需要 key=value 形式：{item}")
        key, raw = item.split("=", 1)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        result[key.strip()] = value
    return result


def _config_from_args(args: argparse.Namespace, *, mock: bool | None = None) -> AppConfig:
    config = load_config(args.config)
    if mock is not None:
        config.llm.mock = mock
    if args.mock:
        config.llm.mock = True
    if args.trace_dir:
        config.trace.dir = args.trace_dir
    if args.no_trace:
        config.trace.enabled = False
    if args.trace:
        config.trace.console = True
    return config


def _build_runtime(config: AppConfig):
    tools = build_tools(config.tools, config.plugins)
    llm = create_model(config.llm)
    return llm, tools


def cmd_run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    llm, tools = _build_runtime(config)
    agent = create_agent(
        config.agent.name,
        llm,
        tools,
        paradigm=args.paradigm or config.agent.paradigm,
        config=config.agent,
        memory_config=config.memory,
        trace_config=config.trace,
        trace_dir=config.trace.dir,
    )
    result = agent.run(args.task)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=_json_default))
        return 0 if result.ok else 2
    print(result.answer or f"[{result.status_label}] {result.error or '没有答案'}")
    print(
        f"\nstatus={result.status}  llm_calls={result.metrics.llm_calls}  "
        f"tool_calls={result.metrics.tool_calls}  steps={result.metrics.steps}  "
        f"tokens={result.metrics.total_tokens}  latency={result.latency_s:.2f}s",
        file=sys.stderr,
    )
    if result.trace_path:
        print(f"trace: {result.trace_path}", file=sys.stderr)
    return 0 if result.ok else 2


def cmd_stream(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    llm, tools = _build_runtime(config)
    agent = create_agent(
        config.agent.name,
        llm,
        tools,
        paradigm="simple",
        config=config.agent,
        memory_config=config.memory,
        trace_config=config.trace,
        trace_dir=config.trace.dir,
    )

    def on_delta(delta: str) -> None:
        print(delta, end="", flush=True)

    answer = agent.think(args.task, stream=True, on_delta=on_delta)
    print()
    if not answer:
        return 1
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    config = _config_from_args(args, mock=True)
    _, tools = _build_runtime(config)
    if args.tools_command == "list":
        if not tools.names():
            print("（没有注册工具）")
            return 0
        for spec in tools.specs():
            print(f"{spec.name:<16} {spec.description}")
            print(f"  parameters: {json.dumps(spec.parameters, ensure_ascii=False)}")
            print(f"  source: {spec.source}")
        return 0
    if args.tools_command == "call":
        arguments = json.loads(args.args) if args.args else {}
        result = tools.try_execute(args.name, arguments)
        print(result.output)
        return 0 if result.ok else 1
    return 1


def cmd_workflow(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    llm, tools = _build_runtime(config)
    spec = WorkflowSpec.from_yaml(args.file)
    engine = WorkflowEngine(
        spec,
        llm,
        tools,
        agent_config=config.agent,
        memory_config=config.memory,
        trace_config=config.trace,
        trace_dir=config.trace.dir,
    )
    result = engine.run(_parse_inputs(args.input))
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=_json_default))
        return 0 if result.status == "success" else 2
    print(result.output if result.output is not None else f"[{result.status}] {result.error or '没有输出'}")
    print(
        f"\nstatus={result.status}  nodes={len(result.nodes)}  "
        f"llm_calls={result.metrics.llm_calls}  tokens={result.metrics.total_tokens}  "
        f"latency={result.latency_s:.2f}s",
        file=sys.stderr,
    )
    if result.trace_path:
        print(f"trace: {result.trace_path}", file=sys.stderr)
    return 0 if result.status == "success" else 2


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    print(f"HaloAgent v1.0  配置：{args.config or '内置默认值'}")
    print(f"mock={config.llm.mock}  provider={config.llm.provider}")
    print(f"agent={config.agent.paradigm}  max_steps={config.agent.max_steps}")
    print(f"memory={config.memory.strategy}/{config.memory.max_tokens}  trace_dir={config.trace.dir}")
    try:
        resolved = resolve_provider(config.llm)
        print(f"resolved: provider={resolved.provider} model={resolved.model} base_url={resolved.base_url or '(默认)'}")
        print(f"api_key: {'已设置' if resolved.api_key else '(不需要/未设置)'}")
    except HaloAgentError as exc:
        print(f"配置检查：{exc}")
        return 2
    tools = build_tools(config.tools, config.plugins)
    print(f"tools({len(tools)}): {', '.join(tools.names())}")
    return 0


def _add_common(parser: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--config", default=default or "configs/default.yaml", help="YAML/JSON 配置文件")
    parser.add_argument("--mock", action="store_true", default=argparse.SUPPRESS if suppress else False, help="使用脚本模型，不调用真实 API")
    parser.add_argument("--trace", action="store_true", default=argparse.SUPPRESS if suppress else False, help="把 trace 实时打印到 stderr")
    parser.add_argument("--no-trace", action="store_true", default=argparse.SUPPRESS if suppress else False, help="不写 trace 文件")
    parser.add_argument("--trace-dir", default=default or "", help="trace 输出目录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haloagent", description="HaloAgent v1.0：小内核、全链路可见的智能体框架")
    _add_common(parser)

    # 允许 --config 放在子命令后面：子解析器用 SUPPRESS，不覆盖全局值
    common = argparse.ArgumentParser(add_help=False)
    _add_common(common, suppress=True)

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", parents=[common], help="运行一个 Agent 任务")
    run.add_argument("task")
    run.add_argument("--paradigm", choices=["simple", "react", "plan_solve", "reflection"])
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    stream = sub.add_parser("stream", parents=[common], help="流式输出（SimpleAgent）")
    stream.add_argument("task")
    stream.set_defaults(func=cmd_stream)

    tools_parser = sub.add_parser("tools", parents=[common], help="查看/调用工具")
    tools_sub = tools_parser.add_subparsers(dest="tools_command", required=True)
    tools_list = tools_sub.add_parser("list", parents=[common], help="列出工具")
    tools_list.set_defaults(func=cmd_tools)
    tools_call = tools_sub.add_parser("call", parents=[common], help="调用工具")
    tools_call.add_argument("name")
    tools_call.add_argument("--args", default="{}")
    tools_call.set_defaults(func=cmd_tools)

    workflow = sub.add_parser("workflow", parents=[common], help="运行 YAML 低代码工作流")
    workflow.add_argument("file")
    workflow.add_argument("--input", action="append", default=[])
    workflow.add_argument("--json", action="store_true")
    workflow.set_defaults(func=cmd_workflow)

    doctor = sub.add_parser("doctor", parents=[common], help="检查配置与工具")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except HaloAgentError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
