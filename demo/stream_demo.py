"""流式输出演示：模型层逐块返回，CLI 层逐块打印。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haloagent.core.message import Message  # noqa: E402
from haloagent.models import MockChatModel  # noqa: E402


def main() -> int:
    model = MockChatModel(
        "流式输出不是把整段答案切碎，而是模型边生成、前端边渲染；"
        "框架层用 StreamChunk 保留 delta 与最终 usage，上层不需要关心供应商差异。",
        chunk_size=8,
    )
    for chunk in model.stream([Message.user("解释流式输出")]):
        if chunk.delta:
            print(chunk.delta, end="", flush=True)
    print(f"\n\nusage={chunk.usage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
