"""内置工具：计算器、当前时间、字数统计、天气（可选联网）。

计算器坚持用 AST 白名单求值，绝不用 eval；这是第 4 章习题 3 的安全底线。
"""

from __future__ import annotations

import ast
import json
import operator
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .base import ToolSpec, tool
from .registry import ToolRegistry

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("只支持数字常量")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的运算符：{type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(left) > 1e6 or abs(right) > 1000):
            raise ValueError("幂运算数值过大，已拒绝执行")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ValueError("除数不能为 0")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的一元运算符：{type(node.op).__name__}")
        return op(_eval_node(node.operand))
    raise ValueError(f"不支持的表达式节点：{type(node).__name__}")


@tool(name="calculator", tags=["math", "safe"], description="计算一个纯算术表达式，支持 + - * / // % ** 与括号。")
def calculator(expression: str) -> str:
    """计算纯算术表达式。

    Args:
        expression: 例如 ``(3 * 5) + (2 * 38)``，只允许数字、括号和算术运算符。
    """
    expr = str(expression).strip().strip("'\"")
    if not expr:
        return "错误：表达式为空"
    if len(expr) > 500:
        return "错误：表达式过长"
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return f"错误：表达式语法有误 - {exc}"
    try:
        value = _eval_node(tree)
    except ValueError as exc:
        return f"错误：{exc}"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{expr} = {value}"


@tool(name="current_time", tags=["time"], description="获取当前日期与时间；可传 IANA 时区，如 Asia/Shanghai。")
def current_time(tz: str = "local") -> str:
    """获取当前时间。

    Args:
        tz: IANA 时区名；默认 local 表示本机时区。
    """
    try:
        zone = None if tz in {"", "local", "system"} else ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        return f"错误：未知时区 {tz}，请用 Asia/Shanghai 这类 IANA 名称"
    now = datetime.now(zone) if zone else datetime.now().astimezone()
    weekday = "一二三四五六日"[now.weekday()]
    return now.strftime(f"%Y-%m-%d %H:%M:%S（星期{weekday}，{now.tzname() or 'local'}）")


@tool(name="word_count", tags=["text"], description="统计一段文本的字符数、中文汉字数和行数。")
def word_count(text: str) -> str:
    """统计文本规模。

    Args:
        text: 要统计的文本。
    """
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return json.dumps(
        {"chars": len(text), "cjk": cjk, "lines": text.count("\n") + 1},
        ensure_ascii=False,
    )


@tool(name="weather", tags=["network", "demo"], timeout_s=15.0, description="查询城市实时天气（wttr.in，免费；无网络时返回错误说明）。")
def weather(city: str) -> str:
    """查询实时天气。

    Args:
        city: 城市名，例如 北京 或 Beijing。
    """
    url = "https://wttr.in/" + urllib.parse.quote(str(city)) + "?format=j1"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "haloagent/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        current = data["current_condition"][0]
        desc = current["weatherDesc"][0]["value"]
        return (
            f"{city}当前天气：{desc}，气温{current['temp_C']}摄氏度"
            f"（体感{current.get('FeelsLikeC', current['temp_C'])}度，湿度{current.get('humidity', '?')}%）"
        )
    except Exception as exc:  # noqa: BLE001 - 网络失败是正常观察值
        return f"错误：查询天气失败（{type(exc).__name__}: {exc}）。可改用本地插件或检查网络。"


BUILTIN_TOOLS: list[ToolSpec] = [
    calculator.__halo_tool__,  # type: ignore[attr-defined]
    current_time.__halo_tool__,  # type: ignore[attr-defined]
    word_count.__halo_tool__,  # type: ignore[attr-defined]
    weather.__halo_tool__,  # type: ignore[attr-defined]
]

BUILTIN_NAMES = [spec.name for spec in BUILTIN_TOOLS]


def register_builtin(registry: ToolRegistry, names: list[str] | None = None) -> ToolRegistry:
    selected = set(names) if names else set(BUILTIN_NAMES)
    for spec in BUILTIN_TOOLS:
        if spec.name in selected:
            registry.register(spec, replace=True)
    return registry


def default_registry(names: list[str] | None = None) -> ToolRegistry:
    return register_builtin(ToolRegistry(), names)
