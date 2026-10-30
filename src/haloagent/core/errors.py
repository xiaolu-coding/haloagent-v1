"""统一异常体系。

框架的每个失败点都有明确类型，调用方既能按类型兜底，也能保留原始错误链。
`retryable` 只出现在 LLMError 上：重试策略只应该重试"可能下一次就好"的错误，
401/参数错误重试一万次也没有意义。
"""

from __future__ import annotations


class HaloAgentError(Exception):
    """框架内所有可预期错误的基类。"""


class ConfigError(HaloAgentError):
    """配置缺失或格式错误。"""


class LLMError(HaloAgentError):
    """模型调用失败。"""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable
        self.attempts = attempts

    def __str__(self) -> str:  # pragma: no cover - 只影响展示
        parts = [super().__str__()]
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.status_code:
            parts.append(f"status={self.status_code}")
        if self.attempts:
            parts.append(f"attempts={self.attempts}")
        return " | ".join(parts)


class ToolError(HaloAgentError):
    """工具执行失败。"""


class ToolNotFound(ToolError):
    """注册表里没有这个工具（通常是模型拼错了名字）。"""


class ToolValidationError(ToolError):
    """模型给的参数不符合工具 Schema。"""


class AgentParseError(HaloAgentError):
    """模型输出无法按当前范式的协议解析。"""


class MaxStepsExceeded(HaloAgentError):
    """达到最大步数仍未得到终答。"""


class WorkflowError(HaloAgentError):
    """低代码工作流定义或执行失败。"""
