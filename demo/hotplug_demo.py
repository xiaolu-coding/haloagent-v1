"""工具热插拔演示：加载 -> 看见 -> 调用 -> 卸载 -> 不可见。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haloagent.tools import default_registry  # noqa: E402


def main() -> int:
    registry = default_registry(["calculator"])
    plugin = ROOT / "examples" / "plugins" / "weather_fixture.py"

    print("1) 初始工具：", registry.names())
    print("2) 加载插件：", plugin)
    names = registry.load_plugin(plugin)
    print("   新增：", names)
    print("   当前：", registry.names())
    print("3) 直接调用：", registry.execute("weather", {"city": "北京"}).output)
    print("4) 卸载插件：", registry.unload_plugin(str(plugin.resolve())))
    print("   当前：", registry.names())
    result = registry.try_execute("weather", {"city": "北京"})
    print("5) 再调用：", result.ok, result.error)
    return 0 if not result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
