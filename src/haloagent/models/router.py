"""供应商解析与模型工厂。

解析优先级（provider=auto 时）:
1. 显式 mock；
2. 通用 LLM_API_KEY + LLM_BASE_URL（接任何兼容端点）；
3. 厂商专有环境变量（DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / ...）；
4. OLLAMA_HOST（本地模型，不要求 Key）；
5. 都没有 -> ConfigError，错误信息直接告诉你该设哪个变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..core.config import LLMConfig
from ..core.errors import ConfigError
from .base import ChatModel
from .mock import MockChatModel
from .openai_compat import OpenAICompatModel
from .retry import RetryingChatModel


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str
    model: str
    env_keys: tuple[str, ...] = ()
    api_key: str = ""


PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset("openai", "https://api.openai.com/v1", "gpt-4o-mini", ("OPENAI_API_KEY",)),
    "deepseek": ProviderPreset("deepseek", "https://api.deepseek.com/v1", "deepseek-chat", ("DEEPSEEK_API_KEY",)),
    "dashscope": ProviderPreset(
        "dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", ("DASHSCOPE_API_KEY", "QWEN_API_KEY")
    ),
    "zhipu": ProviderPreset("zhipu", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", ("ZHIPU_API_KEY", "GLM_API_KEY")),
    "moonshot": ProviderPreset("moonshot", "https://api.moonshot.cn/v1", "moonshot-v1-8k", ("MOONSHOT_API_KEY", "KIMI_API_KEY")),
    "modelscope": ProviderPreset(
        "modelscope", "https://api-inference.modelscope.cn/v1", "Qwen/Qwen2.5-72B-Instruct", ("MODELSCOPE_API_KEY",)
    ),
    "siliconflow": ProviderPreset(
        "siliconflow", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct", ("SILICONFLOW_API_KEY",)
    ),
    "ollama": ProviderPreset("ollama", "http://localhost:11434/v1", "qwen2.5:7b", ("OLLAMA_HOST",), api_key="ollama"),
}


@dataclass
class ResolvedProvider:
    provider: str
    base_url: str
    api_key: str
    model: str


def _first_env(keys: tuple[str, ...]) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def resolve_provider(config: LLMConfig) -> ResolvedProvider:
    provider = (config.provider or "auto").strip().lower()
    generic_key = os.getenv("LLM_API_KEY", "").strip() or config.api_key.strip()
    generic_base = os.getenv("LLM_BASE_URL", "").strip() or config.base_url.strip()
    generic_model = config.model.strip() or os.getenv("LLM_MODEL_ID", "").strip()

    if provider in {"mock", "scripted"} or config.mock:
        return ResolvedProvider("mock", "", "", generic_model or "mock-scripted")

    if provider not in {"auto", "custom", "openai-compatible", "openai_compatible"}:
        if provider not in PRESETS:
            raise ConfigError(
                f"未知 provider：{provider}。可选：auto/custom/mock/" + "/".join(sorted(PRESETS))
            )
        preset = PRESETS[provider]
        api_key = generic_key or _first_env(preset.env_keys) or preset.api_key
        base_url = generic_base or (os.getenv("OLLAMA_HOST", "").strip() if provider == "ollama" else "") or preset.base_url
        return ResolvedProvider(provider, base_url, api_key, generic_model or preset.model)

    # auto / custom：先看通用变量，再看厂商变量，最后看 Ollama
    if generic_base and generic_key:
        return ResolvedProvider("custom", generic_base, generic_key, generic_model or "gpt-4o-mini")

    for name, preset in PRESETS.items():
        if name == "ollama":
            continue
        key = _first_env(preset.env_keys)
        if key:
            return ResolvedProvider(
                name,
                generic_base or preset.base_url,
                generic_key or key,
                generic_model or preset.model,
            )

    ollama_host = os.getenv("OLLAMA_HOST", "").strip()
    if ollama_host:
        return ResolvedProvider("ollama", generic_base or ollama_host, generic_key or "ollama", generic_model or "qwen2.5:7b")

    raise ConfigError(
        "没有找到可用的模型配置。请任选一种：\n"
        "  1) 复制 .env.example 为 .env，设置 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY 之一；\n"
        "  2) 设置通用 LLM_API_KEY + LLM_BASE_URL + LLM_MODEL_ID；\n"
        "  3) 本地 Ollama：设置 OLLAMA_HOST=http://localhost:11434；\n"
        "  4) 不想联网：使用 --mock 或设置 HALO_MOCK_LLM=1。"
    )


def _offline_reply(messages) -> str:
    task = ""
    for message in reversed(messages):
        if getattr(message, "role", "") == "user":
            task = getattr(message, "content", "")
            break
    task = task.replace("\n", " ")[:100]
    return (
        "Final Answer: [Mock 模式] 已收到任务："
        + (task or "（空）")
        + "。这是离线占位回答；配置真实模型（.env）后即可获得真实结果。"
    )


def create_model(config: LLMConfig, *, force_mock: bool | None = None) -> ChatModel:
    """按配置创建模型；真实模型自动套一层重试。"""
    if force_mock or config.mock or config.provider.lower() in {"mock", "scripted"}:
        return MockChatModel(_offline_reply)

    resolved = resolve_provider(config)
    if resolved.provider == "mock":
        return MockChatModel(_offline_reply)

    inner = OpenAICompatModel(
        model=resolved.model,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        provider=resolved.provider,
        timeout=config.timeout,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    if config.max_attempts <= 1:
        return inner
    return RetryingChatModel(
        inner,
        max_attempts=config.max_attempts,
        base_delay=config.backoff,
    )
