"""全链路 Trace。

每个关键动作都落一个事件：run_start / context / llm_request / llm_response /
thought / action / tool_result / observation / plan / reflection / run_end。
事件既保存在内存里，也可以实时推给 CLI / Web（listeners），最后写成 JSONL。

设计目标不是"日志更多"，而是"任何一个结论都能回溯到当时的输入、输出和耗时"。
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REDACT_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "access_token", "token"}


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in REDACT_KEYS:
                cleaned[key] = "***"
            else:
                cleaned[key] = _sanitize(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and len(value) > 12000:
        return value[:12000] + "…（trace 已截断）"
    return value


@dataclass
class TraceEvent:
    seq: int
    ts: str
    elapsed_ms: int
    run_id: str
    agent: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "elapsed_ms": self.elapsed_ms,
            "run_id": self.run_id,
            "agent": self.agent,
            "type": self.type,
            "data": self.data,
        }

    def __str__(self) -> str:
        return f"#{self.seq:03d} [{self.type}] {json.dumps(self.data, ensure_ascii=False)[:180]}"


Listener = Callable[[TraceEvent], None]


class Tracer:
    def __init__(
        self,
        run_id: str,
        agent: str,
        *,
        redact: bool = True,
        console: bool = False,
        listeners: list[Listener] | None = None,
    ) -> None:
        self.run_id = run_id
        self.agent = agent
        self.redact = redact
        self.console = console
        self.listeners: list[Listener] = list(listeners or [])
        self.events: list[TraceEvent] = []
        self._started = time.perf_counter()
        self._seq = 0

    # ------------------------------------------------------------------
    def emit(self, event_type: str, **data: Any) -> TraceEvent:
        self._seq += 1
        payload = _sanitize(data) if self.redact else data
        event = TraceEvent(
            seq=self._seq,
            ts=datetime.now().isoformat(timespec="milliseconds"),
            elapsed_ms=int((time.perf_counter() - self._started) * 1000),
            run_id=self.run_id,
            agent=self.agent,
            type=event_type,
            data=payload,
        )
        self.events.append(event)
        if self.console:
            print(f"  · {event.type:<14} {_short(payload)}", file=sys.stderr)
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception as exc:  # noqa: BLE001 - 监听器不能影响主流程
                print(f"[trace listener error] {exc}", file=sys.stderr)
        return event

    def add_listener(self, listener: Listener) -> None:
        self.listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    # ------------------------------------------------------------------
    def write_jsonl(self, path: str | Path) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return str(target)

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for event in self.events:
            result[event.type] = result.get(event.type, 0) + 1
        return result

    def find(self, event_type: str) -> list[TraceEvent]:
        return [event for event in self.events if event.type == event_type]


def _short(data: dict[str, Any], limit: int = 160) -> str:
    pieces = []
    for key in ("tool", "thought", "text", "message", "answer", "status", "plan", "round"):
        if key in data and data[key] not in (None, ""):
            value = data[key]
            text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            pieces.append(f"{key}={text[:limit]}")
    return " ".join(pieces) or json.dumps(data, ensure_ascii=False)[:limit]
