from __future__ import annotations

from haloagent.core.message import Message
from haloagent.memory import ContextBuilder, SummaryMemory, WindowMemory


def test_window_memory_trims_oldest():
    memory = WindowMemory(max_messages=3, max_tokens=9999)
    for index in range(5):
        memory.add(Message.user(f"m{index}"))
    assert [m.content for m in memory.messages()] == ["m2", "m3", "m4"]
    assert memory.stats().dropped == 2


def test_context_builder_keeps_system_and_last():
    builder = ContextBuilder(max_tokens=300)
    messages = [Message.user("旧消息" * 120), Message.user("中间" * 120), Message.user("最后的问题")]
    built, stats = builder.build(system_prompt="系统提示", messages=messages, reserve_tokens=10)
    assert built[0].role == "system"
    assert built[-1].content == "最后的问题"
    assert stats.dropped >= 1
    assert stats.kept <= 3


def test_summary_memory_compacts():
    memory = SummaryMemory(max_messages=3, max_tokens=9999, keep_recent=2)
    for index in range(5):
        memory.add(Message.user(f"消息{index}"))
    assert memory.summary
    assert memory.summaries >= 1
    assert len(memory.messages()) <= 3
    assert memory.context_messages()[0].role == "system"
