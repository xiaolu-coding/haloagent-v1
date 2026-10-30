"""配置管理：YAML / JSON -> 带默认值的数据类，并支持 ``${ENV}`` 展开。

配置优先级（从低到高）:
    代码默认值 < YAML / JSON < 环境变量（如 LLM_MODEL_ID / HALO_PROVIDER）

这一层只负责"把配置读对"，不创建任何网络客户端，方便单测。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    """递归展开 ``${VAR}`` 与 ``${VAR:-default}``。"""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            return default if default is not None else ""

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class LLMConfig:
    provider: str = "auto"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.2
    max_tokens: int | None = None
    timeout: float = 60.0
    max_attempts: int = 3
    backoff: float = 1.0
    mock: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LLMConfig":
        data = data or {}
        return cls(
            provider=str(data.get("provider", "auto")),
            model=str(data.get("model", "")),
            api_key=str(data.get("api_key", "")),
            base_url=str(data.get("base_url", "")),
            temperature=float(data.get("temperature", 0.2)),
            max_tokens=int(data["max_tokens"]) if data.get("max_tokens") else None,
            timeout=float(data.get("timeout", 60.0)),
            max_attempts=int(data.get("max_attempts", 3)),
            backoff=float(data.get("backoff", 1.0)),
            mock=_as_bool(data.get("mock", False)),
        )

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key"] = "***" if self.api_key else ""
        return data


@dataclass
class AgentConfig:
    name: str = "Halo"
    paradigm: str = "react"
    system_prompt: str = ""
    max_steps: int = 8
    max_iterations: int = 2
    reflect_rounds: int = 2
    quality_threshold: int = 8
    max_tool_rounds_per_step: int = 2
    temperature: float | None = None
    max_tokens: int | None = None
    tool_names: list[str] = field(default_factory=list)
    verbose: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AgentConfig":
        data = data or {}
        tools = data.get("tools", data.get("tool_names", []))
        if isinstance(tools, str):
            tools = [item.strip() for item in tools.split(",") if item.strip()]
        return cls(
            name=str(data.get("name", "Halo")),
            paradigm=str(data.get("paradigm", "react")),
            system_prompt=str(data.get("system_prompt", "")),
            max_steps=int(data.get("max_steps", 8)),
            max_iterations=int(data.get("max_iterations", 2)),
            reflect_rounds=int(data.get("reflect_rounds", 2)),
            quality_threshold=int(data.get("quality_threshold", 8)),
            max_tool_rounds_per_step=int(data.get("max_tool_rounds_per_step", 2)),
            temperature=float(data["temperature"]) if data.get("temperature") is not None else None,
            max_tokens=int(data["max_tokens"]) if data.get("max_tokens") else None,
            tool_names=list(tools or []),
            verbose=_as_bool(data.get("verbose", False)),
        )


@dataclass
class MemoryConfig:
    strategy: str = "window"          # window | summary | none
    max_messages: int = 24
    max_tokens: int = 3000
    persist_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MemoryConfig":
        data = data or {}
        return cls(
            strategy=str(data.get("strategy", "window")),
            max_messages=int(data.get("max_messages", 24)),
            max_tokens=int(data.get("max_tokens", 3000)),
            persist_path=str(data.get("persist_path", "")),
        )


@dataclass
class TraceConfig:
    enabled: bool = True
    dir: str = "runs"
    console: bool = False
    redact: bool = True
    save_result: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TraceConfig":
        data = data or {}
        return cls(
            enabled=_as_bool(data.get("enabled", True)),
            dir=str(data.get("dir", "runs")),
            console=_as_bool(data.get("console", False)),
            redact=_as_bool(data.get("redact", True)),
            save_result=_as_bool(data.get("save_result", True)),
        )


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    tools: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        data = data or {}
        return cls(
            llm=LLMConfig.from_dict(data.get("llm")),
            agent=AgentConfig.from_dict(data.get("agent")),
            memory=MemoryConfig.from_dict(data.get("memory")),
            trace=TraceConfig.from_dict(data.get("trace")),
            tools=list(data.get("tools", []) or []),
            plugins=list(data.get("plugins", []) or []),
            metadata=dict(data.get("metadata", {}) or {}),
        )

    def public(self) -> dict[str, Any]:
        return {
            "llm": self.llm.public(),
            "agent": asdict(self.agent),
            "memory": asdict(self.memory),
            "trace": asdict(self.trace),
            "tools": self.tools,
            "plugins": self.plugins,
            "metadata": self.metadata,
        }


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "auto",
        "temperature": 0.2,
        "timeout": 60,
        "max_attempts": 3,
        "backoff": 1.0,
        "mock": False,
    },
    "agent": {
        "name": "Halo",
        "paradigm": "react",
        "max_steps": 8,
        "max_iterations": 2,
        "reflect_rounds": 2,
        "quality_threshold": 8,
        "max_tool_rounds_per_step": 2,
        "tools": [],
    },
    "memory": {"strategy": "window", "max_messages": 24, "max_tokens": 3000, "persist_path": ""},
    "trace": {"enabled": True, "dir": "runs", "console": False, "redact": True, "save_result": True},
    "tools": ["calculator", "current_time"],
    "plugins": [],
}


def load_dotenv_if_present(path: str | Path = ".env") -> None:
    """优先使用 python-dotenv；没装也能跑（依赖声明里已包含）。"""
    env_path = Path(path)
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(env_path, override=False)


def load_config(path: str | Path | None = None, *, dotenv: bool = True) -> AppConfig:
    """读取 YAML/JSON 配置；文件不存在时使用内置默认值。"""
    if dotenv:
        load_dotenv_if_present()
    raw: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            raise ConfigError(f"配置文件不存在：{config_path}")
        text = config_path.read_text(encoding="utf-8")
        try:
            if config_path.suffix.lower() in {".yaml", ".yml"}:
                raw = yaml.safe_load(text) or {}
            elif config_path.suffix.lower() == ".json":
                raw = json.loads(text)
            else:
                raise ConfigError(f"不支持的配置格式：{config_path.suffix}（只支持 .yaml/.yml/.json）")
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ConfigError(f"配置文件解析失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件根节点必须是对象")

    raw = expand_env(raw)
    merged = deep_merge(DEFAULT_CONFIG, raw)

    # 环境变量覆盖：保持和教材第 7 章一致的“环境优先”直觉
    if os.getenv("HALO_PROVIDER"):
        merged.setdefault("llm", {})["provider"] = os.environ["HALO_PROVIDER"]
    if os.getenv("LLM_MODEL_ID"):
        merged.setdefault("llm", {})["model"] = os.environ["LLM_MODEL_ID"]
    if os.getenv("LLM_BASE_URL"):
        merged.setdefault("llm", {})["base_url"] = os.environ["LLM_BASE_URL"]
    if os.getenv("LLM_API_KEY"):
        merged.setdefault("llm", {})["api_key"] = os.environ["LLM_API_KEY"]
    if os.getenv("HALO_MOCK_LLM"):
        merged.setdefault("llm", {})["mock"] = _as_bool(os.environ["HALO_MOCK_LLM"])
    return AppConfig.from_dict(merged)
