"""框架公共数据类型：Usage / ModelResponse / RunMetrics / RunResult。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.estimated = self.estimated or other.estimated


@dataclass(slots=True)
class ModelResponse:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    latency_s: float = 0.0
    finish_reason: str | None = None
    tool_calls: list[Any] = field(default_factory=list)
    strategy: str = "blocking"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "usage": asdict(self.usage),
            "model": self.model,
            "provider": self.provider,
            "latency_s": round(self.latency_s, 3),
            "finish_reason": self.finish_reason,
            "strategy": self.strategy,
        }


@dataclass(slots=True)
class StreamChunk:
    delta: str = ""
    done: bool = False
    usage: Usage | None = None


@dataclass
class RunMetrics:
    llm_calls: int = 0
    tool_calls: int = 0
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retries: int = 0
    errors: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add_usage(self, usage: Usage) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.estimated = self.estimated or usage.estimated

    def merge(self, other: "RunMetrics") -> None:
        self.llm_calls += other.llm_calls
        self.tool_calls += other.tool_calls
        self.steps += other.steps
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.retries += other.retries
        self.errors += other.errors
        self.estimated = self.estimated or other.estimated

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total_tokens"] = self.total_tokens
        return data


# 统一状态码：报告、CLI、Web 适配层都只认这些值
STATUS_SUCCESS = "success"
STATUS_MAX_STEPS = "max_steps"
STATUS_PARSE_ERROR = "parse_error"
STATUS_LLM_ERROR = "llm_error"
STATUS_TOOL_ERROR = "tool_error"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"

STATUS_LABELS = {
    STATUS_SUCCESS: "已完成",
    STATUS_MAX_STEPS: "达到最大步数",
    STATUS_PARSE_ERROR: "输出解析失败",
    STATUS_LLM_ERROR: "模型调用失败",
    STATUS_TOOL_ERROR: "工具执行失败",
    STATUS_ERROR: "运行异常",
    STATUS_CANCELLED: "已取消",
}


@dataclass
class RunResult:
    run_id: str
    agent: str
    paradigm: str
    task: str
    status: str
    answer: str | None
    metrics: RunMetrics
    started_at: str
    ended_at: str
    latency_s: float
    trace_path: str | None = None
    result_path: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent": self.agent,
            "paradigm": self.paradigm,
            "task": self.task,
            "status": self.status,
            "status_label": self.status_label,
            "answer": self.answer,
            "metrics": self.metrics.to_dict(),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "latency_s": round(self.latency_s, 3),
            "trace_path": self.trace_path,
            "result_path": self.result_path,
            "error": self.error,
            "metadata": self.metadata,
        }


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
