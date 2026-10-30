"""HaloAgent v1.0：小内核、全链路可见的智能体框架。

最小可用示例::

    from haloagent import ReActAgent, MockChatModel, default_registry

    agent = ReActAgent(
        "Halo",
        MockChatModel("Final Answer: 你好"),
        default_registry(["calculator"]),
    )
    result = agent.run("1+1 等于几？")
    print(result.answer, result.metrics.to_dict())
"""

from __future__ import annotations

from .agents import (
    PARADIGMS,
    AgentOutcome,
    BaseAgent,
    PlanAndSolveAgent,
    ReActAgent,
    ReflectionAgent,
    SimpleAgent,
    build_tools,
    create_agent,
)
from .core import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    MemoryConfig,
    Message,
    RunMetrics,
    RunResult,
    TraceConfig,
    load_config,
)
from .memory import ContextBuilder, Memory, SummaryMemory, WindowMemory
from .models import ChatModel, MockChatModel, OpenAICompatModel, RetryingChatModel, create_model
from .tools import ToolRegistry, ToolSpec, default_registry, tool
from .trace import TraceEvent, Tracer

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "PARADIGMS",
    "AgentOutcome",
    "BaseAgent",
    "PlanAndSolveAgent",
    "ReActAgent",
    "ReflectionAgent",
    "SimpleAgent",
    "build_tools",
    "create_agent",
    "AgentConfig",
    "AppConfig",
    "LLMConfig",
    "MemoryConfig",
    "Message",
    "RunMetrics",
    "RunResult",
    "TraceConfig",
    "load_config",
    "ContextBuilder",
    "Memory",
    "SummaryMemory",
    "WindowMemory",
    "ChatModel",
    "MockChatModel",
    "OpenAICompatModel",
    "RetryingChatModel",
    "create_model",
    "ToolRegistry",
    "ToolSpec",
    "default_registry",
    "tool",
    "TraceEvent",
    "Tracer",
]
