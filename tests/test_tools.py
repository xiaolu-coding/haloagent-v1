from __future__ import annotations

import textwrap

import pytest

from haloagent.core.errors import ToolError, ToolNotFound
from haloagent.tools import ToolRegistry, ToolSpec, default_registry, tool
from haloagent.tools.builtin import calculator


def test_calculator_basic_and_safe():
    assert "14" in calculator("2 + 3 * 4")
    assert "错误" in calculator("__import__('os').system('whoami')")
    assert "错误" in calculator("1 / 0")
    assert "错误" in calculator("9 ** 9 ** 9")


def test_schema_from_type_hints():
    @tool(name="greet", description="打招呼")
    def greet(city: str, times: int = 1) -> str:
        """打招呼。

        Args:
            city: 城市名
            times: 重复次数
        """
        return f"{city}" * times

    spec = greet.__halo_tool__
    assert spec.parameters["required"] == ["city"]
    assert spec.parameters["properties"]["times"]["default"] == 1
    assert spec.parameters["properties"]["city"]["description"] == "城市名"
    assert spec.to_openai_schema()["function"]["name"] == "greet"


def test_validation_and_error_result():
    @tool(name="only_int")
    def only_int(value: int) -> str:
        return str(value + 1)

    ok = only_int.__halo_tool__.invoke({"value": "41"})
    assert ok.ok and ok.output == "42"

    bad = only_int.__halo_tool__.invoke({"value": "abc"})
    assert not bad.ok and "整数" in bad.output

    unknown = only_int.__halo_tool__.invoke({"value": 1, "extra": 2})
    assert not unknown.ok and "不认识参数" in unknown.output


def test_registry_duplicate_replace_and_unregister():
    reg = default_registry(["calculator"])
    assert "calculator" in reg
    with pytest.raises(ToolError):
        reg.register(calculator)
    spec = ToolSpec.from_function(lambda x: x, name="echo", description="回声")
    reg.register(spec)
    assert "echo" in reg.names()
    reg.unregister("echo")
    with pytest.raises(ToolNotFound):
        reg.get("echo")


def test_plugin_hot_plug(tmp_path):
    plugin = tmp_path / "my_plugin.py"
    plugin.write_text(
        textwrap.dedent(
            '''
            from haloagent.tools import ToolSpec, tool

            @tool(name="ping", description="返回 pong")
            def ping() -> str:
                return "pong"

            TOOLS = [ping.__halo_tool__]
            '''
        ),
        encoding="utf-8",
    )
    reg = ToolRegistry()
    names = reg.load_plugin(plugin)
    assert names == ["ping"]
    assert reg.execute("ping").output == "pong"
    reg.unload_plugin(str(plugin.resolve()))
    assert "ping" not in reg
    assert reg.loaded_plugins() == {}
