"""Agent 抽象基类：统一 run() 生命周期、上下文、模型调用、工具调用与 Trace。

子类只实现 ``_execute(task) -> AgentOutcome``，这样三种范式共享同一套：
- 入口 / 异常兜底 / 指标汇总；
- Memory + ContextBuilder；
- LLM 调用与重试后的 usage 统计；
- 工具执行与 Observation；
- Trace 落盘。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import AgentConfig, MemoryConfig, TraceConfig
from ..core.errors import AgentParseError, LLMError, ToolError
from ..core.message import Message
from ..core.tokens import estimate_tokens
from ..core.types import (
    STATUS_ERROR,
    STATUS_LLM_ERROR,
    STATUS_PARSE_ERROR,
    STATUS_SUCCESS,
    STATUS_TOOL_ERROR,
    RunMetrics,
    RunResult,
    Usage,
    now_iso,
)
from ..memory import ContextBuilder, JsonMemoryStore, Memory, NullMemory, SummaryMemory, WindowMemory
from ..models.base import ChatModel
from ..tools.base import ToolResult
from ..tools.builtin import default_registry
from ..tools.registry import ToolRegistry
from ..trace import TraceEvent, Tracer, new_run_id


@dataclass
class AgentOutcome:
    answer: str | None
    status: str = STATUS_SUCCESS
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    paradigm: str = "base"
    default_system_prompt = "你是一个严谨、可靠的智能体。先理解任务，再给出可执行的答案。"

    def __init__(
        self,
        name: str,
        llm: ChatModel,
        tools: ToolRegistry | None = None,
        *,
        config: AgentConfig | None = None,
        memory: Memory | None = None,
        memory_config: MemoryConfig | None = None,
        trace_config: TraceConfig | None = None,
        trace_dir: str | Path | None = None,
    ) -> None:
        self.name = name
        self.llm = llm
        self.tools = tools if tools is not None else default_registry()
        self.config = config or AgentConfig(name=name)
        self.memory_config = memory_config or MemoryConfig()
        self.trace_config = trace_config or TraceConfig()
        self.memory = memory or self._build_memory()
        self.context_builder = ContextBuilder(max_tokens=self.memory_config.max_tokens)
        self.metrics = RunMetrics()
        self.tracer: Tracer | None = None
        self.last_result: RunResult | None = None
        self.last_context_stats: dict[str, int] = {}
        self._trace_dir = Path(trace_dir or self.trace_config.dir)

    def _build_memory(self) -> Memory:
        strategy = self.memory_config.strategy.lower()
        if strategy == "none":
            return NullMemory()
        if strategy == "summary":
            return SummaryMemory(
                max_messages=self.memory_config.max_messages,
                max_tokens=self.memory_config.max_tokens,
            )
        return WindowMemory(
            max_messages=self.memory_config.max_messages,
            max_tokens=self.memory_config.max_tokens,
        )

    # ------------------------------------------------------------------
    @abstractmethod
    def _execute(self, task: str) -> AgentOutcome:
        """子类实现：返回终答、状态与范式特有的元数据。"""

    # ------------------------------------------------------------------
    def run(self, task: str, *, on_event: Any | None = None) -> RunResult:
        if not task or not task.strip():
            raise ValueError("task 不能为空")

        self.metrics = RunMetrics()
        run_id = new_run_id(self.paradigm)
        self.tracer = Tracer(
            run_id,
            self.name,
            redact=self.trace_config.redact,
            console=self.trace_config.console,
            listeners=[on_event] if on_event else [],
        )
        started_perf = time.perf_counter()
        started_at = now_iso()
        status = STATUS_SUCCESS
        answer: str | None = None
        error: str | None = None
        metadata: dict[str, Any] = {}

        self.tracer.emit(
            "run_start",
            task=task,
            agent=self.name,
            paradigm=self.paradigm,
            model=self.llm.model,
            provider=self.llm.provider,
            tools=self.tools.names(),
        )
        self.memory.add(Message.user(task))

        try:
            outcome = self._execute(task)
            status = outcome.status
            answer = outcome.answer
            metadata.update(outcome.metadata)
        except LLMError as exc:
            status, error = STATUS_LLM_ERROR, str(exc)
            self.metrics.errors += 1
            self.tracer.emit("error", scope="llm", error=error, retryable=exc.retryable)
        except AgentParseError as exc:
            status, error = STATUS_PARSE_ERROR, str(exc)
            self.metrics.errors += 1
            self.tracer.emit("error", scope="parse", error=error)
        except ToolError as exc:
            status, error = STATUS_TOOL_ERROR, str(exc)
            self.metrics.errors += 1
            self.tracer.emit("error", scope="tool", error=error)
        except Exception as exc:  # noqa: BLE001 - 框架入口必须兜底
            status, error = STATUS_ERROR, f"{type(exc).__name__}: {exc}"
            self.metrics.errors += 1
            self.tracer.emit("error", scope="agent", error=error)

        if answer:
            self.memory.add(Message.assistant(answer))

        latency = time.perf_counter() - started_perf
        metadata.update(
            {
                "memory": self.memory.stats().to_dict(),
                "context": self.last_context_stats,
                "trace_events": self.tracer.counts(),
                "tools_used": sorted({e.data.get("tool") for e in self.tracer.find("tool_result") if e.data.get("tool")}),
            }
        )
        self.tracer.emit(
            "run_end",
            status=status,
            answer=answer,
            error=error,
            latency_s=round(latency, 3),
            metrics=self.metrics.to_dict(),
            memory=metadata["memory"],
        )

        trace_path = self._write_trace(run_id)
        result = RunResult(
            run_id=run_id,
            agent=self.name,
            paradigm=self.paradigm,
            task=task,
            status=status,
            answer=answer,
            metrics=self.metrics,
            started_at=started_at,
            ended_at=now_iso(),
            latency_s=latency,
            trace_path=trace_path,
            error=error,
            metadata=metadata,
        )
        if self.trace_config.save_result:
            result.result_path = self._write_result(run_id, result)
        self._persist_memory()
        self.last_result = result
        return result

    # ------------------------------------------------------------------
    def _write_trace(self, run_id: str) -> str | None:
        if not self.trace_config.enabled or self.tracer is None:
            return None
        path = self._trace_dir / f"{run_id}.jsonl"
        return self.tracer.write_jsonl(path)

    def _write_result(self, run_id: str, result: RunResult) -> str | None:
        if not self.trace_config.enabled:
            return None
        path = self._trace_dir / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _persist_memory(self) -> None:
        if not self.memory_config.persist_path:
            return
        try:
            JsonMemoryStore(self.memory_config.persist_path).save(self.memory)
        except OSError:
            pass

    # ------------------------------------------------------------------
    def _base_system(self) -> str:
        return self.config.system_prompt.strip() or self.default_system_prompt

    def _context(
        self,
        *,
        system_suffix: str = "",
        extra: list[Message] | None = None,
        reserve_tokens: int = 400,
        step: int | None = None,
    ) -> list[Message]:
        system_prompt = self._base_system()
        if system_suffix.strip():
            system_prompt = system_prompt + "\n\n" + system_suffix.strip()
        memory_messages = (
            self.memory.context_messages() if isinstance(self.memory, SummaryMemory) else self.memory.messages()
        )
        if extra:
            memory_messages = memory_messages + extra
        messages, stats = self.context_builder.build(
            system_prompt=system_prompt,
            messages=memory_messages,
            reserve_tokens=reserve_tokens,
        )
        self.last_context_stats = stats.to_dict()
        assert self.tracer is not None
        self.tracer.emit(
            "context",
            step=step,
            original=stats.original,
            kept=stats.kept,
            dropped=stats.dropped,
            estimated_tokens=stats.estimated_tokens,
            budget=stats.budget,
        )
        return messages

    def _chat(
        self,
        messages: list[Message],
        *,
        note: str = "",
        step: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        assert self.tracer is not None
        self.tracer.emit(
            "llm_request",
            note=note,
            step=step,
            model=self.llm.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            message_count=len(messages),
            estimated_prompt_tokens=sum(estimate_tokens(m.content) for m in messages),
            tools=[tool.get("function", {}).get("name") for tool in (tools or [])],
        )
        try:
            response = self.llm.complete(
                messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                tools=tools,
            )
        except LLMError as exc:
            self.tracer.emit("llm_error", note=note, error=str(exc), retryable=exc.retryable)
            raise
        self.metrics.llm_calls += 1
        self.metrics.add_usage(response.usage)
        if response.usage.estimated:
            self.metrics.estimated = True
        self.tracer.emit(
            "llm_response",
            note=note,
            step=step,
            text=response.text,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "estimated": response.usage.estimated,
            },
            latency_s=round(response.latency_s, 3),
            model=response.model,
            strategy=response.strategy,
        )
        return response.text

    def _tool(self, name: str, arguments: dict[str, Any], *, step: int | None = None) -> ToolResult:
        assert self.tracer is not None
        self.tracer.emit("action", step=step, tool=name, arguments=arguments)
        result = self.tools.try_execute(name, arguments)
        self.metrics.tool_calls += 1
        if not result.ok:
            self.metrics.errors += 1
        self.tracer.emit(
            "tool_result",
            step=step,
            tool=name,
            ok=result.ok,
            output=result.output,
            error=result.error,
            latency_s=round(result.latency_s, 3),
        )
        self.tracer.emit(
            "observation",
            step=step,
            tool=name,
            content=result.output,
        )
        return result

    # ------------------------------------------------------------------
    def think(
        self,
        prompt: str,
        *,
        system: str | None = None,
        stream: bool = False,
        on_delta: Any | None = None,
    ) -> str:
        """框架级便捷入口：一次性提问，不进入 Agent 循环。"""
        messages = [
            Message.system(system or self._base_system()),
            Message.user(prompt),
        ]
        if not stream:
            return self._chat(messages, note="think")
        parts: list[str] = []
        for chunk in self.llm.stream(messages):  # type: ignore[attr-defined]
            if chunk.delta:
                parts.append(chunk.delta)
                if on_delta:
                    on_delta(chunk.delta)
        return "".join(parts)

    def get_history(self) -> list[Message]:
        return self.memory.messages()

    def clear_history(self) -> None:
        self.memory.clear()

    def close(self) -> None:
        self.llm.close()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name} paradigm={self.paradigm}>"
