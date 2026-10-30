"""工具注册表：注册、发现、执行、插件热插拔。

热插拔的边界很清楚：
- 加载插件 = 把插件里的 ToolSpec 注册进当前 registry；
- 卸载插件 = 按来源记录精确移除，不误伤同名但不同来源的工具。
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import ToolError, ToolNotFound
from .base import ToolResult, ToolSpec

_ENTRYPOINT_GROUP = "haloagent.tools"


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolSpec | Any] | None = None, *, duplicate: str = "error") -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._plugins: dict[str, list[str]] = {}
        self.duplicate = duplicate  # error | replace | ignore
        if tools:
            self.register_many(tools)

    # ------------------------------------------------------------------
    def register(self, item: ToolSpec | Any, *, replace: bool | None = None, source: str | None = None) -> ToolSpec:
        spec = item if isinstance(item, ToolSpec) else getattr(item, "__halo_tool__", None)
        if spec is None and callable(item):
            spec = ToolSpec.from_function(item)
        if not isinstance(spec, ToolSpec):
            raise ToolError(f"无法注册工具：{item!r} 不是 ToolSpec 或带 @tool 的函数")
        if source:
            spec.source = source
        if spec.name in self._tools:
            should_replace = self.duplicate == "replace" if replace is None else replace
            if not should_replace:
                if self.duplicate == "ignore":
                    return self._tools[spec.name]
                raise ToolError(f"工具已存在：{spec.name}（如需覆盖请传 replace=True）")
        self._tools[spec.name] = spec
        return spec

    def register_many(self, tools: Iterable[ToolSpec | Any], *, replace: bool | None = None) -> list[ToolSpec]:
        return [self.register(item, replace=replace) for item in tools]

    def unregister(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise ToolNotFound(f"工具不存在：{name}")
        return self._tools.pop(name)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFound(f"工具不存在：{name}；可用：{', '.join(self.names()) or '无'}") from exc

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [self._tools[name] for name in self.names()]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # ------------------------------------------------------------------
    def subset(self, names: Iterable[str] | None) -> "ToolRegistry":
        if not names:
            return self
        registry = ToolRegistry(duplicate=self.duplicate)
        for name in names:
            registry.register(self.get(name))
        return registry

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        return self.get(name).invoke(arguments or {})

    def try_execute(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """永不抛异常的执行入口，专门给 Agent 生成 Observation。"""
        try:
            return self.execute(name, arguments or {})
        except ToolNotFound as exc:
            return ToolResult(name, False, str(exc), arguments or {}, str(exc))

    # ------------------------------------------------------------------
    def describe(self, *, with_schema: bool = False) -> str:
        if not self._tools:
            return "（无可用工具）"
        return "\n".join(spec.prompt_line(with_schema=with_schema) for spec in self.specs())

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [spec.to_openai_schema() for spec in self.specs()]

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
                "tags": spec.tags,
                "source": spec.source,
            }
            for spec in self.specs()
        ]

    # ------------------------------------------------------------------
    def load_plugin(self, target: str | Path, *, replace: bool = False) -> list[str]:
        """从 .py 文件 / 模块名 / 包目录加载工具。

        插件可以暴露：
        - ``TOOLS = [ToolSpec, ...]``
        - 任意带 ``__halo_tool__`` 的 ``@tool`` 函数
        - ``register(registry)`` 函数
        """
        plugin_id, module = self._import_target(target)
        if plugin_id in self._plugins:
            raise ToolError(f"插件已加载：{plugin_id}")

        before = set(self._tools)
        registered: list[ToolSpec] = []
        seen: set[str] = set()

        def add(item: Any) -> None:
            spec = item if isinstance(item, ToolSpec) else getattr(item, "__halo_tool__", None)
            if spec is None and callable(item):
                spec = ToolSpec.from_function(item, source=plugin_id)
            if isinstance(spec, ToolSpec) and spec.name not in seen:
                seen.add(spec.name)
                spec.source = plugin_id
                registered.append(self.register(spec, replace=replace))

        for name in ("TOOLS", "TOOL"):
            exported = getattr(module, name, None)
            if exported is None:
                continue
            if isinstance(exported, (list, tuple, set)):
                for item in exported:
                    add(item)
            else:
                add(exported)

        for _, value in vars(module).items():
            if inspect.isfunction(value) and hasattr(value, "__halo_tool__"):
                add(value)

        hook = getattr(module, "register", None)
        if callable(hook) and not hasattr(hook, "__halo_tool__"):
            hook(self)

        names = sorted(set(self._tools) - before)
        if not names:
            # 回滚 hook 可能带来的注册，避免"加载成功但没工具"的假象
            for name in list(set(self._tools) - before):
                self._tools.pop(name, None)
            raise ToolError(f"插件没有注册任何工具：{plugin_id}")
        self._plugins[plugin_id] = names
        return names

    def unload_plugin(self, plugin_id: str) -> list[str]:
        names = self._plugins.pop(plugin_id, None)
        if names is None:
            raise ToolError(f"插件未加载：{plugin_id}")
        for name in names:
            self._tools.pop(name, None)
        return names

    def loaded_plugins(self) -> dict[str, list[str]]:
        return {key: list(value) for key, value in self._plugins.items()}

    def _import_target(self, target: str | Path) -> tuple[str, Any]:
        raw = str(target)
        path = Path(raw)
        if path.exists():
            resolved = path.resolve()
            if resolved.is_dir():
                init = resolved / "__init__.py"
                if not init.exists():
                    raise ToolError(f"插件目录缺少 __init__.py：{resolved}")
                path = init
            if path.suffix != ".py":
                raise ToolError(f"插件文件必须是 .py：{path}")
            plugin_id = str(path.resolve())
            module_name = f"_halo_plugin_{abs(hash(plugin_id))}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ToolError(f"无法加载插件：{path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001
                sys.modules.pop(module_name, None)
                raise ToolError(f"插件执行失败：{path} -> {type(exc).__name__}: {exc}") from exc
            return plugin_id, module

        try:
            module = importlib.import_module(raw)
        except ImportError as exc:
            raise ToolError(f"找不到插件文件或模块：{raw}") from exc
        return raw, module

    def load_entrypoints(self, group: str = _ENTRYPOINT_GROUP) -> list[str]:
        """加载第三方包通过 ``[project.entry-points."haloagent.tools"]`` 暴露的工具。"""
        try:
            from importlib.metadata import entry_points
        except ImportError:  # pragma: no cover
            return []
        eps = entry_points()
        selected = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])
        loaded: list[str] = []
        for entry in selected:
            try:
                obj = entry.load()
            except Exception as exc:  # noqa: BLE001
                raise ToolError(f"入口点加载失败：{entry.name} -> {exc}") from exc
            if isinstance(obj, ToolSpec):
                self.register(obj, replace=True, source=f"entrypoint:{entry.name}")
                loaded.append(obj.name)
            elif callable(obj) and hasattr(obj, "__halo_tool__"):
                self.register(obj, replace=True, source=f"entrypoint:{entry.name}")
                loaded.append(obj.__halo_tool__.name)
            elif callable(obj):
                obj(self)
                loaded.append(f"entrypoint:{entry.name}")
        return loaded
