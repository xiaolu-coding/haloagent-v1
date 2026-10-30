# HaloAgent v1.0 API 设计文档

> 目标读者：想用 HaloAgent 写 Agent、写工具、接模型或接 Web 的开发者。
> 所有公开 API 都从 `haloagent` 顶层导出；内部模块路径仅用于扩展。

---

## 1. 安装与导入

```powershell
cd E:\helloAgents学习\haloagent-v1
pip install -e .
# 或者不安装，直接设置：
$env:PYTHONPATH="$PWD\src"
```

```python
import haloagent
print(haloagent.__version__)   # 1.0.0

from haloagent import (
    ReActAgent, PlanAndSolveAgent, ReflectionAgent, SimpleAgent,
    MockChatModel, create_model, create_agent, load_config,
    ToolRegistry, ToolSpec, tool, default_registry,
    WindowMemory, SummaryMemory, ContextBuilder,
    Tracer, RunResult, AgentConfig, TraceConfig,
)
```

---

## 2. 统一入口：`agent.run(task)`

### 2.1 构造

```python
ReActAgent(
    name: str,
    llm: ChatModel,
    tools: ToolRegistry | None = None,
    *,
    config: AgentConfig | None = None,
    memory: Memory | None = None,
    memory_config: MemoryConfig | None = None,
    trace_config: TraceConfig | None = None,
    trace_dir: str | Path | None = None,
)
```

四种范式构造参数完全一致：

| 类 | `paradigm` | 说明 |
|---|---|---|
| `SimpleAgent` | `simple` | 单轮对话基线 |
| `ReActAgent` | `react` | Thought → Action → Observation |
| `PlanAndSolveAgent` | `plan_solve` | 规划 → 逐步执行 → 综合 |
| `ReflectionAgent` | `reflection` | ReAct 草稿 → 评审 → 修订 |

### 2.2 运行

```python
result: RunResult = agent.run(task: str, *, on_event: Callable[[TraceEvent], None] | None = None)
```

- `on_event`：实时监听 Trace 事件（CLI / Web SSE 用）；
- 同步阻塞，直到终答 / 达到步数 / 异常；
- 永不因为单次工具错误抛异常（工具错误是 Observation）；
- LLM 配置错误、解析失败会变成 `RunResult.status`，不会悄悄返回空字符串。

### 2.3 `RunResult`

```python
@dataclass
class RunResult:
    run_id: str
    agent: str
    paradigm: str
    task: str
    status: str                 # success | max_steps | parse_error | llm_error | tool_error | error
    answer: str | None
    metrics: RunMetrics
    started_at: str
    ended_at: str
    latency_s: float
    trace_path: str | None      # runs/<run_id>.jsonl
    result_path: str | None     # runs/<run_id>.json
    error: str | None
    metadata: dict              # plan / critiques / memory / context / trace_events ...
```

`RunMetrics`：

```python
llm_calls, tool_calls, steps, prompt_tokens, completion_tokens,
total_tokens, retries, errors, estimated
```

便捷方法：

```python
result.ok              # status == "success"
result.status_label    # 中文状态
result.to_dict()       # 可直接 json.dumps
```

### 2.4 多轮与历史

```python
agent.run("第一轮")
agent.run("第二轮")          # 同一个 Memory 会保留上下文
agent.get_history()          # list[Message]
agent.clear_history()
```

> 需要跨进程持久化时设置 `MemoryConfig(persist_path="runs/memory.json")`。

### 2.5 流式

```python
for chunk in agent.llm.stream([Message.user("写一段话")]):
    if chunk.delta:
        print(chunk.delta, end="", flush=True)

# 或便捷入口（不进入多步 Agent 循环）：
agent.think("写一段话", stream=True, on_delta=lambda d: print(d, end=""))
```

---

## 3. 模型层

### 3.1 `ChatModel` 接口

```python
class ChatModel(ABC):
    provider: str
    model: str

    def complete(
        self, messages: list[Message], *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> ModelResponse: ...

    def stream(...) -> Iterator[StreamChunk]: ...
```

`ModelResponse`：

```python
text, usage, model, provider, latency_s, finish_reason, tool_calls, strategy
```

`StreamChunk`：

```python
delta: str
done: bool
usage: Usage | None
```

### 3.2 工厂

```python
config = load_config("configs/default.yaml")
llm = create_model(config.llm)          # 自动解析 provider / key / base_url，并套重试
```

`provider` 可选：

```text
auto | deepseek | dashscope | openai | zhipu | moonshot |
modelscope | siliconflow | ollama | custom | mock
```

自动检测优先级：

1. `llm.mock: true`；
2. 通用 `LLM_API_KEY` + `LLM_BASE_URL`；
3. 厂商变量：`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `OPENAI_API_KEY` / ...
4. `OLLAMA_HOST`；
5. 都没有 -> `ConfigError`，错误信息列出应设的变量。

### 3.3 自定义供应商

不继承也可以，直接给 `OpenAICompatModel`：

```python
from haloagent.models import OpenAICompatModel

llm = OpenAICompatModel(
    model="my-model",
    api_key="sk-...",
    base_url="https://my-endpoint/v1",
    provider="my-provider",
    timeout=60,
    temperature=0.2,
)
```

要加进自动检测，则在 `models/router.py` 的 `PRESETS` 加一条 `ProviderPreset`。

### 3.4 重试语义

```python
RetryingChatModel(inner, max_attempts=3, base_delay=1.0, jitter=0.15)
```

- 只重试 `LLMError.retryable=True`：429 / 408 / 5xx / 超时 / 连接错误；
- 401/403/400/404 不重试；
- 流式响应一旦已经吐出 delta，不再重放，避免重复文本；
- `model.retries`、`model.last_attempts` 可观测。

---

## 4. 工具系统

### 4.1 定义工具

```python
from haloagent import tool

@tool(
    name="weather",                       # 可选，默认函数名
    description="查询城市天气",           # 可选，默认 docstring 首行
    tags=["weather"],
    timeout_s=15.0,
    max_output_chars=8000,
)
def weather(city: str, days: int = 1) -> str:
    """查询天气。

    Args:
        city: 城市名，例如 北京
        days: 预报天数
    """
    return f"{city} 未来 {days} 天：晴"
```

自动生成：

```python
weather.__halo_tool__.parameters
# {"type":"object","properties":{"city":{"type":"string",...},
#  "days":{"type":"integer","default":1,...}},"required":["city"],...}

weather.__halo_tool__.to_openai_schema()
```

支持的类型：`str/int/float/bool/list/dict/Optional/Literal/Enum`，
以及 `from __future__ import annotations` 的字符串注解（内部用 `get_type_hints` 解析）。

### 4.2 注册表

```python
from haloagent import ToolRegistry, default_registry

registry = default_registry(["calculator", "current_time"])
registry.register(weather)                 # 重复默认报错
registry.register(other, replace=True)     # 显式覆盖
registry.names()                           # 排序后的名字
registry.describe(with_schema=True)        # 给模型看的工具说明
registry.openai_schemas()                  # 原生 function calling schema
registry.unregister("weather")

result = registry.execute("weather", {"city": "北京"})
result.ok, result.output, result.error, result.latency_s

result = registry.try_execute("missing", {})   # 不抛异常，返回 ok=False
```

`ToolResult`：

```python
tool, ok, output, arguments, error, latency_s
```

### 4.3 插件

```python
names = registry.load_plugin("examples/plugins/weather_fixture.py")
registry.loaded_plugins()                  # {plugin_id: [tool names]}
registry.unload_plugin(plugin_id)
```

插件暴露方式（任选其一）：

```python
TOOLS = [weather.__halo_tool__]            # 推荐
TOOL = weather.__halo_tool__
# 或任意 @tool 函数
# 或 def register(registry): ...
```

也支持 entry points：

```toml
[project.entry-points."haloagent.tools"]
my_tool = "my_package.tools:my_tool"
```

```python
registry.load_entrypoints()
```

### 4.4 内置工具

| 名称 | 说明 | 网络 |
|---|---|---|
| `calculator` | AST 白名单安全求值，拒绝导入/属性访问/超大幂 | 否 |
| `current_time` | 当前时间，支持 IANA 时区 | 否 |
| `word_count` | 字符/汉字/行数 | 否 |
| `weather` | wttr.in 实时天气 | 是（失败返回错误说明） |

---

## 5. Memory 与 Context

```python
from haloagent import WindowMemory, SummaryMemory, ContextBuilder, Message

memory = WindowMemory(max_messages=24, max_tokens=3000)
memory.add(Message.user("你好"))
memory.messages()
memory.stats()          # messages / estimated_tokens / dropped

summary = SummaryMemory(max_messages=24, max_tokens=3000, keep_recent=6)
summary.context_messages()   # 摘要 system + 最近消息
```

`ContextBuilder`：

```python
builder = ContextBuilder(max_tokens=3000)
messages, stats = builder.build(
    system_prompt="你是助手",
    messages=memory.messages(),
    reserve_tokens=400,       # 给模型输出留的空间
)
stats.original, stats.kept, stats.dropped, stats.estimated_tokens, stats.budget
```

规则：系统提示固定在最前；从旧到新丢弃；**最后一条消息无条件保留**。

---

## 6. Trace

```python
from haloagent import Tracer

tracer = Tracer("run-1", "Halo", redact=True, console=False)

def on_event(event):
    print(event.type, event.data)

tracer.add_listener(on_event)
tracer.emit("custom", foo="bar")
tracer.write_jsonl("runs/run-1.jsonl")
tracer.counts()
```

`TraceEvent`：

```python
seq, ts, elapsed_ms, run_id, agent, type, data
```

内置事件类型：

| 事件 | 关键字段 |
|---|---|
| `run_start` | task, model, provider, tools |
| `context` | step, original, kept, dropped, estimated_tokens, budget |
| `llm_request` | note, messages, estimated_prompt_tokens |
| `llm_response` | text, usage, latency_s, model, strategy |
| `thought` | step, thought |
| `action` | step, tool, arguments |
| `tool_result` | step, tool, ok, output, error, latency_s |
| `observation` | step, tool, content |
| `plan` | steps, raw |
| `step_result` | step, title, result |
| `reflection` | round, score, issues, suggestion |
| `revision` | round, answer |
| `final` | step, answer |
| `error` | scope, error |
| `run_end` | status, answer, latency_s, metrics, memory |

所有事件写 JSONL，默认脱敏 `api_key / authorization / password / secret / access_token / token`。

---

## 7. 配置 Schema

```yaml
llm:
  provider: auto
  model: ""
  api_key: ""
  base_url: ""
  temperature: 0.2
  max_tokens: null
  timeout: 60
  max_attempts: 3
  backoff: 1.0
  mock: false

agent:
  name: Halo
  paradigm: react
  system_prompt: ""
  max_steps: 8
  max_iterations: 2
  reflect_rounds: 2
  quality_threshold: 8
  max_tool_rounds_per_step: 2
  temperature: null
  max_tokens: null
  verbose: false

memory:
  strategy: window        # window | summary | none
  max_messages: 24
  max_tokens: 3000
  persist_path: ""

trace:
  enabled: true
  dir: runs
  console: false
  redact: true
  save_result: true

tools: [calculator, current_time, weather]
plugins: [examples/plugins/weather_fixture.py]
```

加载：

```python
config = load_config("configs/default.yaml")
config.llm.mock = True          # 运行时覆盖也可以
```

`${ENV}` 展开发生在解析前；未定义且无默认值 -> 空字符串。

---

## 8. 低代码工作流 DSL

### 8.1 顶层

```yaml
name: 必填
description: 可选
inputs: { key: default }
on_error: fail | continue
output: 节点 id
nodes: [...]
```

### 8.2 节点通用字段

```yaml
- id: 唯一 id
  type: tool | agent | template | condition | output
  depends_on: [其他 id]
  when: ${nodes.condition.output}   # 为假则 skipped
```

### 8.3 各节点字段

```yaml
# tool
- id: w
  type: tool
  tool: weather
  args: { city: ${inputs.city} }

# agent
- id: brief
  type: agent
  paradigm: react          # simple | react | plan_solve | reflection
  max_steps: 4
  tools: [current_time]    # 子集；不写则用全局工具表
  task: |
    根据 ${nodes.w.output} 写简报

# template
- id: note
  type: template
  template: "提醒：${inputs.city}有雨"

# condition
- id: rainy
  type: condition
  if: contains(${nodes.w.output}, '雨')
  # 也支持 == / != / not / ${...}

# output
- id: result
  type: output
  value: ${nodes.brief.output}
```

### 8.4 变量

| 语法 | 含义 |
|---|---|
| `${inputs.city}` | 工作流输入 |
| `${nodes.weather.output}` | 节点输出 |
| `${nodes.rainy.status}` | `success / skipped / error` |
| `${env.HOME}` | 环境变量 |

整串匹配（如 `value: ${inputs.count}`）保留原类型；嵌入字符串则转字符串。

### 8.5 Python API

```python
from haloagent.workflow import WorkflowSpec, WorkflowEngine

spec = WorkflowSpec.from_yaml("examples/workflows/travel_brief.yaml")
engine = WorkflowEngine(
    spec, llm, tools,
    agent_config=config.agent,
    memory_config=config.memory,
    trace_config=config.trace,
)
result = engine.run({"city": "上海"})
print(result.status, result.output)
print(result.nodes["rainy"].output)
```

---

## 9. CLI

```text
haloagent [--config PATH] [--mock] [--trace] [--no-trace] [--trace-dir DIR] <command>

run <task> [--paradigm simple|react|plan_solve|reflection] [--json]
stream <task>
tools list
tools call <name> [--args JSON]
workflow <yaml> [--input key=value ...] [--json]
doctor
```

示例：

```powershell
python -m haloagent doctor --config configs/mock.yaml
python -m haloagent run "1+1" --mock
python -m haloagent tools call calculator --args '{"expression":"1+1"}' --mock
python -m haloagent workflow examples/workflows/travel_brief.yaml --config configs/mock.yaml
```

---

## 10. 异常与状态

```text
HaloAgentError
├── ConfigError
├── LLMError(retryable, status_code, provider, attempts)
├── ToolError
│   ├── ToolNotFound
│   └── ToolValidationError
├── AgentParseError
├── MaxStepsExceeded
└── WorkflowError
```

| 状态 | 含义 |
|---|---|
| `success` | 正常得到答案 |
| `max_steps` | 达到最大步数 |
| `parse_error` | 模型输出不符合协议，且自我纠正失败 |
| `llm_error` | 模型调用失败（重试后） |
| `tool_error` | 工具层未捕获错误（通常工具错误会转为 Observation） |
| `error` | 其他运行异常 |
| `cancelled` | 取消（预留给 Web 层） |

---

## 11. 最小可复用片段

```python
from haloagent import (
    create_agent, create_model, load_config, build_tools, TraceConfig,
)

config = load_config("configs/default.yaml")
agent = create_agent(
    "MyAgent",
    create_model(config.llm),
    build_tools(config.tools, config.plugins),
    paradigm="react",
    config=config.agent,
    memory_config=config.memory,
    trace_config=TraceConfig(enabled=True, dir="runs"),
)
result = agent.run("你的任务")
print(result.answer)
```
