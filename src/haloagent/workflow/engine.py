"""低代码工作流：把可视化节点编排落成一份 YAML DSL。

节点类型只有五种，够用且容易讲清楚：
- tool：调用一个已注册工具；
- agent：运行一个 Agent 范式（可指定工具子集）；
- template：纯文本模板（适合做提醒、拼接）；
- condition：把一个表达式算成布尔值；
- output：声明工作流最终输出。

依赖用 ``depends_on`` 声明，执行前做拓扑排序与环检测；节点可用 ``when`` 条件跳过。
变量统一用 ``${inputs.x}`` / ``${nodes.id.output}`` / ``${env.X}``。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from ..agents.base import BaseAgent
from ..agents.factory import create_agent
from ..core.config import AgentConfig, MemoryConfig, TraceConfig
from ..core.errors import WorkflowError
from ..core.types import RunMetrics, now_iso
from ..models.base import ChatModel
from ..tools.registry import ToolRegistry
from ..trace import Tracer, new_run_id

_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_NODE_TYPES = {"tool", "agent", "template", "condition", "output"}


@dataclass
class NodeSpec:
    id: str
    type: str
    depends_on: list[str] = field(default_factory=list)
    when: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "depends_on": self.depends_on,
            "when": self.when,
            "config": self.config,
        }


@dataclass
class WorkflowSpec:
    name: str
    description: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    nodes: list[NodeSpec] = field(default_factory=list)
    output: str | None = None
    on_error: str = "fail"
    source: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str = "") -> "WorkflowSpec":
        if not isinstance(data, dict):
            raise WorkflowError("工作流根节点必须是对象")
        name = str(data.get("name") or "").strip()
        if not name:
            raise WorkflowError("工作流缺少 name")
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise WorkflowError("工作流缺少 nodes 列表")
        nodes: list[NodeSpec] = []
        for index, item in enumerate(raw_nodes):
            if not isinstance(item, dict):
                raise WorkflowError(f"第 {index + 1} 个节点不是对象")
            node_id = str(item.get("id") or "").strip()
            node_type = str(item.get("type") or "").strip()
            if not node_id:
                raise WorkflowError(f"第 {index + 1} 个节点缺少 id")
            if node_type not in _NODE_TYPES:
                raise WorkflowError(f"节点 {node_id} 的 type 非法：{node_type}；可选：{', '.join(sorted(_NODE_TYPES))}")
            depends = item.get("depends_on", item.get("needs", []))
            if isinstance(depends, str):
                depends = [depends]
            config = {key: value for key, value in item.items() if key not in {"id", "type", "depends_on", "needs", "when"}}
            nodes.append(
                NodeSpec(
                    id=node_id,
                    type=node_type,
                    depends_on=[str(dep) for dep in (depends or [])],
                    when=str(item["when"]) if item.get("when") is not None else None,
                    config=config,
                )
            )
        return cls(
            name=name,
            description=str(data.get("description", "")),
            inputs=dict(data.get("inputs", {}) or {}),
            nodes=nodes,
            output=str(data["output"]) if data.get("output") is not None else None,
            on_error=str(data.get("on_error", "fail")),
            source=source,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "WorkflowSpec":
        target = Path(path)
        if not target.exists():
            raise WorkflowError(f"工作流文件不存在：{target}")
        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise WorkflowError(f"工作流 YAML 解析失败：{exc}") from exc
        return cls.from_dict(data, source=str(target))

    # ------------------------------------------------------------------
    def validate(self) -> None:
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            duplicates = sorted({item for item in ids if ids.count(item) > 1})
            raise WorkflowError(f"节点 id 重复：{', '.join(duplicates)}")
        known = set(ids)
        for node in self.nodes:
            missing = [dep for dep in node.depends_on if dep not in known]
            if missing:
                raise WorkflowError(f"节点 {node.id} 依赖不存在的节点：{', '.join(missing)}")
            if node.id in node.depends_on:
                raise WorkflowError(f"节点 {node.id} 不能依赖自己")
            if node.type == "tool" and not node.get("tool"):
                raise WorkflowError(f"tool 节点 {node.id} 缺少 tool 字段")
            if node.type == "agent" and not node.get("task"):
                raise WorkflowError(f"agent 节点 {node.id} 缺少 task 字段")
            if node.type == "template" and node.get("template") is None:
                raise WorkflowError(f"template 节点 {node.id} 缺少 template 字段")
            if node.type == "condition" and not (node.get("if") or node.get("condition")):
                raise WorkflowError(f"condition 节点 {node.id} 缺少 if 字段")
            if node.type == "output" and node.get("value") is None:
                raise WorkflowError(f"output 节点 {node.id} 缺少 value 字段")
        self._topological_order()

    def _topological_order(self) -> list[str]:
        indegree = {node.id: len(node.depends_on) for node in self.nodes}
        children: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                children[dep].append(node.id)
        queue = [node.id for node in self.nodes if indegree[node.id] == 0]
        order: list[str] = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(self.nodes):
            raise WorkflowError("工作流存在循环依赖")
        return order


@dataclass
class NodeResult:
    id: str
    type: str
    status: str
    output: Any = None
    error: str | None = None
    run_id: str | None = None
    trace_path: str | None = None
    metrics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "run_id": self.run_id,
            "trace_path": self.trace_path,
            "metrics": self.metrics,
        }


@dataclass
class WorkflowResult:
    workflow: str
    run_id: str
    status: str
    output: Any
    nodes: dict[str, NodeResult]
    metrics: RunMetrics
    started_at: str
    ended_at: str
    latency_s: float
    trace_path: str | None = None
    error: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "run_id": self.run_id,
            "status": self.status,
            "output": self.output,
            "inputs": self.inputs,
            "nodes": {key: value.to_dict() for key, value in self.nodes.items()},
            "metrics": self.metrics.to_dict(),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "latency_s": round(self.latency_s, 3),
            "trace_path": self.trace_path,
            "error": self.error,
        }


class WorkflowEngine:
    def __init__(
        self,
        spec: WorkflowSpec,
        llm: ChatModel,
        tools: ToolRegistry,
        *,
        agent_config: AgentConfig | None = None,
        memory_config: MemoryConfig | None = None,
        trace_config: TraceConfig | None = None,
        trace_dir: str | Path = "runs",
    ) -> None:
        spec.validate()
        self.spec = spec
        self.llm = llm
        self.tools = tools
        self.agent_config = agent_config or AgentConfig()
        self.memory_config = memory_config or MemoryConfig()
        self.trace_config = trace_config or TraceConfig()
        self.trace_dir = Path(trace_dir)

    # ------------------------------------------------------------------
    def run(self, inputs: dict[str, Any] | None = None) -> WorkflowResult:
        run_id = new_run_id("wf")
        tracer = Tracer(run_id, f"workflow:{self.spec.name}", redact=self.trace_config.redact, console=self.trace_config.console)
        values = dict(self.spec.inputs)
        values.update(inputs or {})
        started = time.perf_counter()
        started_at = now_iso()
        results: dict[str, NodeResult] = {}
        metrics = RunMetrics()
        status = "success"
        error: str | None = None

        tracer.emit("workflow_start", workflow=self.spec.name, inputs=values, nodes=[n.id for n in self.spec.nodes])
        try:
            for node_id in self.spec._topological_order():
                node = next(item for item in self.spec.nodes if item.id == node_id)
                tracer.emit("workflow_node_start", node=node.id, type=node.type)
                if node.when is not None and not self._truthy(self._eval(node.when, values, results)):
                    results[node.id] = NodeResult(node.id, node.type, "skipped")
                    tracer.emit("workflow_node_end", node=node.id, status="skipped")
                    continue
                try:
                    result = self._run_node(node, values, results)
                except Exception as exc:  # noqa: BLE001 - 节点错误要带上节点 id
                    message = f"{type(exc).__name__}: {exc}"
                    results[node.id] = NodeResult(node.id, node.type, "error", error=message)
                    tracer.emit("workflow_node_end", node=node.id, status="error", error=message)
                    if self.spec.on_error == "continue":
                        status = "partial"
                        error = error or message
                        continue
                    raise WorkflowError(f"节点 {node.id} 执行失败：{message}") from exc
                results[node.id] = result
                metrics.merge(self._result_metrics(result))
                tracer.emit(
                    "workflow_node_end",
                    node=node.id,
                    status=result.status,
                    output=result.output,
                    run_id=result.run_id,
                )
        except Exception as exc:  # noqa: BLE001
            status = "error"
            error = str(exc)
            tracer.emit("workflow_error", error=error)

        output = self._final_output(results)
        latency = time.perf_counter() - started
        trace_path = None
        if self.trace_config.enabled:
            trace_path = str(self.trace_dir / f"{run_id}.jsonl")
            tracer.write_jsonl(trace_path)
        tracer.emit("workflow_end", status=status, output=output, error=error, latency_s=round(latency, 3))
        # run_end 之后再落一次盘，保证 workflow_end 也在文件里
        if trace_path:
            tracer.write_jsonl(trace_path)
        result = WorkflowResult(
            workflow=self.spec.name,
            run_id=run_id,
            status=status,
            output=output,
            nodes=results,
            metrics=metrics,
            started_at=started_at,
            ended_at=now_iso(),
            latency_s=latency,
            trace_path=trace_path,
            error=error,
            inputs=values,
        )
        if self.trace_config.save_result and self.trace_config.enabled:
            path = self.trace_dir / f"{run_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    # ------------------------------------------------------------------
    def _run_node(self, node: NodeSpec, values: dict[str, Any], results: dict[str, NodeResult]) -> NodeResult:
        if node.type == "tool":
            tool_name = str(self._render(node.get("tool"), values, results))
            arguments = self._render(node.get("args", {}), values, results)
            if not isinstance(arguments, dict):
                raise WorkflowError(f"节点 {node.id} 的 args 必须是对象")
            tool_result = self.tools.try_execute(tool_name, arguments)
            if not tool_result.ok:
                raise WorkflowError(tool_result.error or tool_result.output)
            return NodeResult(node.id, node.type, "success", output=tool_result.output, metrics={"tool_calls": 1})

        if node.type == "template":
            return NodeResult(node.id, node.type, "success", output=self._render(node.get("template", ""), values, results))

        if node.type == "condition":
            expression = node.get("if", node.get("condition"))
            return NodeResult(node.id, node.type, "success", output=bool(self._truthy(self._eval(str(expression), values, results))))

        if node.type == "output":
            return NodeResult(node.id, node.type, "success", output=self._render(node.get("value", ""), values, results))

        if node.type == "agent":
            return self._run_agent_node(node, values, results)

        raise WorkflowError(f"未实现的节点类型：{node.type}")

    def _run_agent_node(self, node: NodeSpec, values: dict[str, Any], results: dict[str, NodeResult]) -> NodeResult:
        task = self._render(node.get("task", ""), values, results)
        if not isinstance(task, str) or not task.strip():
            raise WorkflowError(f"节点 {node.id} 渲染后的 task 为空")
        paradigm = str(node.get("paradigm", self.agent_config.paradigm))
        tool_names = node.get("tools")
        tools = self.tools.subset(tool_names) if tool_names else self.tools
        config = replace(
            self.agent_config,
            paradigm=paradigm,
            name=str(node.get("name", f"{self.spec.name}:{node.id}")),
            max_steps=int(node.get("max_steps", self.agent_config.max_steps)),
        )
        agent = create_agent(
            config.name,
            self.llm,
            tools,
            paradigm=paradigm,
            config=config,
            memory_config=self.memory_config,
            trace_config=self.trace_config,
            trace_dir=self.trace_dir,
        )
        run = agent.run(task)
        if not run.ok:
            raise WorkflowError(f"Agent 节点失败：{run.status} {run.error or ''}".strip())
        return NodeResult(
            node.id,
            node.type,
            "success",
            output=run.answer,
            run_id=run.run_id,
            trace_path=run.trace_path,
            metrics=run.metrics.to_dict(),
        )

    # ------------------------------------------------------------------
    def _final_output(self, results: dict[str, NodeResult]) -> Any:
        if self.spec.output:
            node = next((item for item in self.spec.nodes if item.id == self.spec.output), None)
            if node is None:
                raise WorkflowError(f"output 指向不存在的节点：{self.spec.output}")
            return results.get(node.id, NodeResult(node.id, node.type, "skipped")).output
        for node in reversed(self.spec.nodes):
            result = results.get(node.id)
            if result and result.status == "success" and result.output not in (None, ""):
                return result.output
        return None

    @staticmethod
    def _result_metrics(result: NodeResult) -> RunMetrics:
        metrics = RunMetrics()
        if result.metrics:
            metrics.llm_calls = int(result.metrics.get("llm_calls", 0))
            metrics.tool_calls = int(result.metrics.get("tool_calls", 0))
            metrics.steps = int(result.metrics.get("steps", 0))
            metrics.prompt_tokens = int(result.metrics.get("prompt_tokens", 0))
            metrics.completion_tokens = int(result.metrics.get("completion_tokens", 0))
            metrics.errors = int(result.metrics.get("errors", 0))
            metrics.estimated = bool(result.metrics.get("estimated", False))
        return metrics

    # ------------------------------------------------------------------
    def _lookup(self, expression: str, values: dict[str, Any], results: dict[str, NodeResult]) -> Any:
        expr = expression.strip()
        if expr.startswith("inputs."):
            return values.get(expr[len("inputs.") :], "")
        if expr.startswith("nodes."):
            body = expr[len("nodes.") :]
            node_id, _, attr = body.partition(".")
            result = results.get(node_id)
            if result is None:
                return ""
            if attr in {"", "output"}:
                return result.output if result.output is not None else ""
            if attr == "status":
                return result.status
            if attr == "error":
                return result.error or ""
            return ""
        if expr.startswith("env."):
            return os.getenv(expr[len("env.") :], "")
        if expr in {"true", "True"}:
            return True
        if expr in {"false", "False"}:
            return False
        return expr

    def _render(self, value: Any, values: dict[str, Any], results: dict[str, NodeResult]) -> Any:
        if isinstance(value, str):
            full = _VAR_RE.fullmatch(value.strip())
            if full:
                return self._lookup(full.group(1), values, results)

            def repl(match: re.Match[str]) -> str:
                found = self._lookup(match.group(1), values, results)
                return "" if found is None else str(found)

            return _VAR_RE.sub(repl, value)
        if isinstance(value, list):
            return [self._render(item, values, results) for item in value]
        if isinstance(value, dict):
            return {key: self._render(item, values, results) for key, item in value.items()}
        return value

    def _eval(self, expression: str, values: dict[str, Any], results: dict[str, NodeResult]) -> Any:
        expr = expression.strip()
        # contains(a, b)
        match = re.fullmatch(r"contains\((.*)\)", expr, re.S | re.I)
        if match:
            parts = _split_args(match.group(1))
            if len(parts) != 2:
                raise WorkflowError(f"contains 需要两个参数：{expression}")
            left = self._render(parts[0].strip(), values, results)
            right = self._render(parts[1].strip().strip("'\""), values, results)
            return str(right) in str(left)
        for operator in ("==", "!="):
            if operator in expr:
                left, right = expr.split(operator, 1)
                left_value = self._render(left.strip(), values, results)
                right_value = self._render(right.strip().strip("'\""), values, results)
                equal = str(left_value) == str(right_value)
                return equal if operator == "==" else not equal
        if expr.lower().startswith("not "):
            return not self._truthy(self._eval(expr[4:], values, results))
        return self._render(expr, values, results)

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() not in {"", "false", "0", "no", "none", "null", "skipped"}


def _split_args(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current = ""
    for char in text:
        if quote:
            current += char
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current += char
        elif char in "([{":
            depth += 1
            current += char
        elif char in ")]}":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    return parts
