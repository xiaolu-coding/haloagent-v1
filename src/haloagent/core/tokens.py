"""轻量 token 估算。

不引入 tiktoken：框架的目标是教学与可移植，估算值只用于"上下文预算"和
明显异常的监控；真实计费以供应商返回的 usage 为准。
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = max(0, len(text) - cjk)
    return max(1, int(cjk / 1.5 + other / 4))
