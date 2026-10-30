"""插件示例：用本地 fixture 替代联网天气。

它演示两件事：
1. 第三方工具不用改框架代码，只要暴露 TOOLS / @tool / register(registry) 之一；
2. 插件可以热加载、热卸载，Agent 的可用工具会实时变化。

运行：
    haloagent tools list --config configs/mock.yaml
"""

from __future__ import annotations

from haloagent.tools import ToolSpec, tool


@tool(
    name="weather",
    description="查询城市天气（本地演示数据，不联网）。",
    tags=["demo", "weather"],
)
def weather(city: str) -> str:
    """查询城市天气。

    Args:
        city: 城市名，例如 北京、上海。
    """
    fixtures = {
        "北京": "北京：晴，气温 18°C，湿度 40%，北风 2 级。",
        "上海": "上海：小雨，气温 22°C，湿度 78%，东南风 3 级。",
        "深圳": "深圳：多云，气温 26°C，湿度 70%。",
    }
    return fixtures.get(str(city).strip(), f"{city}：多云，气温 20°C（演示数据，未命中 fixture）。")


# 推荐显式导出：加载器会优先读取 TOOLS，再扫描 @tool 函数
TOOLS: list[ToolSpec] = [weather.__halo_tool__]  # type: ignore[attr-defined]
