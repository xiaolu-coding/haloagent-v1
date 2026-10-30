"""Agent 工厂：范式名 -> 类，配置 -> 可运行实例。"""

from __future__ import annotations

from pathlib import Path

from ..core.config import AgentConfig, MemoryConfig, TraceConfig
from ..core.errors import ConfigError
from ..models.base import ChatModel
from ..tools.builtin import BUILTIN_NAMES, default_registry
from ..tools.registry import ToolRegistry
from .base import BaseAgent
from .plan_solve import PlanAndSolveAgent
from .react import ReActAgent
from .reflection import ReflectionAgent
from .simple import SimpleAgent

PARADIGMS: dict[str, type[BaseAgent]] = {
    "simple": SimpleAgent,
    "react": ReActAgent,
    "plan_solve": PlanAndSolveAgent,
    "plan-solve": PlanAndSolveAgent,
    "plan-and-solve": PlanAndSolveAgent,
    "reflection": ReflectionAgent,
}


def create_agent(
    name: str,
    llm: ChatModel,
    tools: ToolRegistry | None = None,
    *,
    paradigm: str | None = None,
    config: AgentConfig | None = None,
    memory_config: MemoryConfig | None = None,
    trace_config: TraceConfig | None = None,
    trace_dir: str | Path | None = None,
) -> BaseAgent:
    config = config or AgentConfig(name=name)
    key = (paradigm or config.paradigm or "react").lower().replace("_", "-")
    cls = PARADIGMS.get(key)
    if cls is None:
        raise ConfigError(f"未知范式：{paradigm or config.paradigm}；可选：{', '.join(sorted(PARADIGMS))}")
    return cls(
        name=name,
        llm=llm,
        tools=tools,
        config=config,
        memory_config=memory_config,
        trace_config=trace_config,
        trace_dir=trace_dir,
    )


def build_tools(names: list[str] | None = None, plugins: list[str] | None = None) -> ToolRegistry:
    """按名称组装工具：内置工具直接注册，插件路径/模块名走 load_plugin。"""
    requested = list(names or [])
    builtin_names = [name for name in requested if name in BUILTIN_NAMES]
    registry = default_registry(builtin_names or (None if not requested else []))
    for plugin in plugins or []:
        registry.load_plugin(plugin)
    for name in requested:
        if name in BUILTIN_NAMES or name in registry:
            continue
        # 允许把插件路径直接写进 tools 列表
        if Path(name).exists() or name.startswith("halo_plugin_"):
            registry.load_plugin(name)
        else:
            raise ConfigError(f"未知工具：{name}。内置工具：{', '.join(BUILTIN_NAMES)}；插件请放到 plugins 列表")
    return registry
