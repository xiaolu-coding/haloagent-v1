"""工具基类与 ``@tool`` 装饰器。

工具三要素：名称、描述、执行逻辑。描述不是注释，而是模型选择工具的依据；
所以这里把函数签名 + docstring 自动转成 JSON Schema，并同时生成给模型看的文本。
"""

from __future__ import annotations

import asyncio
import enum
import inspect
import json
import re
import time
import types
import typing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Union, get_args, get_origin

from ..core.errors import ToolValidationError

ToolFunc = Callable[..., Any]


def _json_type(annotation: Any) -> dict[str, Any]:
    if annotation in (inspect.Parameter.empty, Any, typing.Any):
        return {"type": "string"}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is list or annotation is tuple:
        return {"type": "array", "items": {"type": "string"}}
    if annotation is dict:
        return {"type": "object"}
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return {"type": "string", "enum": [item.value for item in annotation]}

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        values = list(args)
        kind = "string"
        if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            kind = "integer"
        return {"type": kind, "enum": values}
    if origin in (list, tuple, set):
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object"}
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return _json_type(non_none[0])
        return {"type": "string"}
    if origin is typing.Annotated and args:
        return _json_type(args[0])
    return {"type": "string"}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """解析首行摘要与 Google 风格 ``Args:``。"""
    if not doc:
        return "", {}
    lines = [line.rstrip() for line in inspect.cleandoc(doc).splitlines()]
    summary = lines[0].strip() if lines else ""
    descriptions: dict[str, str] = {}
    in_args = False
    current: str | None = None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.lower() in {"args:", "arguments:", "参数:"}:
            in_args = True
            continue
        if not in_args:
            continue
        if not stripped:
            continue
        if re.match(r"^(returns?|raises?|yields?|示例|example|返回)\b", stripped, re.I):
            break
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.+)$", stripped)
        if match:
            current = match.group(1)
            descriptions[current] = match.group(2).strip()
        elif current and line.startswith((" ", "\t")):
            descriptions[current] = (descriptions[current] + " " + stripped).strip()
    return summary, descriptions


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: str
    arguments: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "output": self.output,
            "arguments": self.arguments,
            "error": self.error,
            "latency_s": round(self.latency_s, 3),
        }


@dataclass
class ToolSpec:
    name: str
    description: str
    func: ToolFunc
    parameters: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    timeout_s: float | None = 30.0
    max_output_chars: int = 8000
    source: str = "manual"

    # ------------------------------------------------------------------
    @classmethod
    def from_function(
        cls,
        func: ToolFunc,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        timeout_s: float | None = 30.0,
        max_output_chars: int = 8000,
        source: str = "manual",
    ) -> "ToolSpec":
        tool_name = (name or func.__name__).strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tool_name):
            raise ValueError(f"工具名不合法：{tool_name}（只允许字母、数字、下划线）")

        signature = inspect.signature(func)
        summary, arg_docs = _parse_docstring(inspect.getdoc(func) or "")
        try:
            # from __future__ import annotations 会把注解变成字符串，必须解析
            hints = typing.get_type_hints(func)
        except Exception:  # noqa: BLE001 - 局部类型/前向引用解析失败时退回原始注解
            hints = {}
        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter in signature.parameters.values():
            if parameter.name in {"self", "cls"}:
                continue
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            schema = _json_type(hints.get(parameter.name, parameter.annotation))
            schema["description"] = arg_docs.get(parameter.name, parameter.name)
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)
            else:
                schema["default"] = parameter.default
            properties[parameter.name] = schema

        final_description = (description or summary or f"调用 {tool_name}").strip()
        return cls(
            name=tool_name,
            description=final_description,
            func=func,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            tags=tags or [],
            timeout_s=timeout_s,
            max_output_chars=max_output_chars,
            source=source,
        )

    # ------------------------------------------------------------------
    @property
    def required(self) -> list[str]:
        return list(self.parameters.get("required", []))

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def prompt_line(self, *, with_schema: bool = True) -> str:
        props = self.parameters.get("properties", {})
        args = ", ".join(
            f"{key}: {value.get('type', 'string')}" + ("" if key in self.required else "（可选）")
            for key, value in props.items()
        )
        line = f"- {self.name}({args})：{self.description}"
        if with_schema:
            line += f"\n  参数 Schema：{json.dumps(self.parameters, ensure_ascii=False)}"
        return line

    # ------------------------------------------------------------------
    def _coerce(self, key: str, value: Any, schema: dict[str, Any]) -> Any:
        kind = schema.get("type", "string")
        if value is None:
            return None
        if kind == "string":
            return value if isinstance(value, str) else str(value)
        if kind == "integer":
            if isinstance(value, bool):
                raise ToolValidationError(f"参数 {key} 需要整数，收到布尔值")
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ToolValidationError(f"参数 {key} 需要整数，收到 {value!r}") from exc
        if kind == "number":
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise ToolValidationError(f"参数 {key} 需要数字，收到 {value!r}") from exc
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if str(value).lower() in {"true", "1", "yes"}:
                return True
            if str(value).lower() in {"false", "0", "no"}:
                return False
            raise ToolValidationError(f"参数 {key} 需要布尔值，收到 {value!r}")
        if kind == "array":
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ToolValidationError(f"参数 {key} 需要数组，收到 {value!r}") from exc
            if not isinstance(value, (list, tuple)):
                raise ToolValidationError(f"参数 {key} 需要数组，收到 {type(value).__name__}")
            return list(value)
        if kind == "object":
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ToolValidationError(f"参数 {key} 需要对象，收到 {value!r}") from exc
            if not isinstance(value, dict):
                raise ToolValidationError(f"参数 {key} 需要对象，收到 {type(value).__name__}")
            return value
        return value

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ToolValidationError(f"工具 {self.name} 的参数必须是对象")
        properties = self.parameters.get("properties", {})
        missing = [name for name in self.required if name not in arguments]
        if missing:
            raise ToolValidationError(f"工具 {self.name} 缺少参数：{', '.join(missing)}")
        unknown = [name for name in arguments if name not in properties]
        if unknown:
            raise ToolValidationError(
                f"工具 {self.name} 不认识参数：{', '.join(unknown)}；可用参数：{', '.join(properties) or '无'}"
            )
        return {key: self._coerce(key, value, properties[key]) for key, value in arguments.items()}

    def invoke(self, arguments: dict[str, Any] | None = None) -> ToolResult:
        started = time.perf_counter()
        arguments = arguments or {}
        try:
            checked = self.validate(arguments)
        except ToolValidationError as exc:
            return ToolResult(self.name, False, f"参数校验失败：{exc}", arguments, str(exc), time.perf_counter() - started)

        try:
            if inspect.iscoroutinefunction(self.func):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    output = asyncio.run(self.func(**checked))
                else:
                    # 同步 Agent 在事件循环里调用 async 工具：丢到独立线程执行
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        output = pool.submit(lambda: asyncio.run(self.func(**checked))).result(
                            timeout=self.timeout_s
                        )
            else:
                output = self.func(**checked)
            text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 - 工具错误要变成观察值，而不是炸掉 Agent
            return ToolResult(
                self.name,
                False,
                f"工具执行失败：{type(exc).__name__}: {exc}",
                arguments,
                f"{type(exc).__name__}: {exc}",
                time.perf_counter() - started,
            )

        if len(text) > self.max_output_chars:
            text = text[: self.max_output_chars] + f"\n…（已截断，原长 {len(text)} 字符）"
        return ToolResult(self.name, True, text, arguments, None, time.perf_counter() - started)


def tool(
    func: ToolFunc | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    timeout_s: float | None = 30.0,
    max_output_chars: int = 8000,
):
    """``@tool`` 或 ``@tool(name="...")``。函数仍可直接调用，同时带上 ToolSpec。"""

    def decorate(target: ToolFunc) -> ToolFunc:
        spec = ToolSpec.from_function(
            target,
            name=name,
            description=description,
            tags=tags,
            timeout_s=timeout_s,
            max_output_chars=max_output_chars,
            source="decorator",
        )
        setattr(target, "__halo_tool__", spec)
        return target

    if func is None:
        return decorate
    return decorate(func)
