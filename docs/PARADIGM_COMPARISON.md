# 三种范式在同一框架下的轨迹对比

> 对应第 4 周产出要求：「附一份 ... 三种范式在同一框架下的轨迹对比报告」。
> 可复现命令：`python demo/compare_paradigms.py`；完整原始报告见
> [`reports/paradigm_comparison_mock.md`](../reports/paradigm_comparison_mock.md)。

## 1. 实验设计

- **同一框架**：HaloAgent v1.0，三者都继承 `BaseAgent`，共享 `run()`、Memory、ToolRegistry、Tracer。
- **同一任务**：

  > 帮我规划北京一日游：先查北京今天的天气，再算 3 张地铁票（每张 5 元）和 2 份午餐（每份 38 元）的总价，最后判断是否适合户外活动并给出两条建议。

- **同一工具集**：`weather`（本地 fixture 插件）、`calculator`、`current_time`。
- **同一模型**：`MockChatModel` 的脚本响应（三套脚本，分别模拟各范式的合法输出）。
- **同一 Trace schema**：JSONL，一行一个事件。

> Mock 模式的指标只验证"链路与结构"，不代表真实回答质量；真实模型运行：
> `python demo/compare_paradigms.py --real --config configs/default.yaml`。

## 2. 指标结果

| 范式 | 状态 | LLM 调用 | 工具调用 | 步数 | Prompt Tokens | Completion Tokens | 总 Tokens |
|---|---|---:|---:|---:|---:|---:|---:|
| ReAct | success | 3 | 2 | 3 | 1561 | 105 | 1666 |
| Plan-and-Solve | success | 7 | 2 | 3 | 2604 | 149 | 2753 |
| Reflection | success | 6 | 2 | 5 | 2575 | 145 | 2720 |

## 3. 轨迹差异

### ReAct（3 次 LLM）

```text
thought   先查北京天气，才能判断是否适合户外
action    weather({"city":"北京"})
result    北京：晴，18°C，湿度 40%
thought   天气已拿到，接下来算总价
action    calculator({"expression":"(3*5)+(2*38)"})
result    (3*5)+(2*38) = 91
final     北京晴、18°C，适合户外；地铁 15 + 午餐 76 = 91 元
```

特点：**路径最短**。模型在每一步根据 Observation 决定下一步，不需要提前承诺计划。

### Plan-and-Solve（7 次 LLM）

```text
plan      查询天气 / 计算总价 / 综合判断
step 1    Action weather -> Step Result 北京晴 18°C
step 2    Action calculator -> Step Result 91 元
step 3    Step Result 适合户外，建议上午活动
final     综合三段结果输出最终答案
```

特点：**计划是显式产物**，可以人工审核、逐步回放；代价是规划、每步执行、综合各一次调用。
本实现还允许每一步内部调用工具（最多 `max_tool_rounds_per_step` 轮）。

### Reflection（6 次 LLM）

```text
draft     ReAct：查天气 -> 算总价 -> "北京晴，18°C，总价 91 元，适合户外"
critique  {"score": 6, "issues": ["没有分项", "建议不具体"]}
revision  补分项、给两条建议
critique  {"score": 9, "issues": []}  -> 提前停止
```

特点：**用评审分控制迭代**。第 1 轮 6 分 -> 修订；第 2 轮 9 分 >= 阈值 8 -> 停止。
若没有阈值，Reflection 很容易陷入"为改而改"的无限循环。

## 4. 结论

1. **成本**：ReAct < Reflection < Plan-and-Solve（本任务下 3 < 6 < 7 次调用）。
2. **可审计性**：Plan-and-Solve 最好（计划显式），ReAct 次之（每步思考），Reflection 多了评审记录。
3. **答案完整度**：Reflection 通常最好，因为它显式针对 issue 修订；但需要评分阈值。
4. **选择建议**：
   - 工具路径不确定、想快速拿结果 -> ReAct；
   - 步骤多、需要人工审核计划 -> Plan-and-Solve；
   - 对答案质量敏感、能接受额外成本 -> Reflection。
5. **框架价值**：三者的差异只在"如何组织循环"，模型、工具、记忆、Trace 完全复用；
   如果没有统一框架，换范式往往意味着重写一套工具调用与日志。

## 5. 如何看 Trace

```powershell
# 最近一次 ReAct 轨迹
Get-ChildItem runs\react-*.jsonl | Sort-Object LastWriteTime | Select-Object -Last 1 |
  Get-Content -Encoding UTF8 -TotalCount 8
```

每个事件都带 `seq / ts / elapsed_ms / run_id / agent / type / data`，
`data.messages` 保留当时发给模型的完整消息，`data.usage` 保留 token 与耗时。

## 6. 复现真实模型对比

```powershell
Copy-Item .env.example .env
# 填入 DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY 等
python demo/compare_paradigms.py --real --config configs/default.yaml
```

真实模型的输出格式可能偏离协议，框架会：
- 第一次解析失败 -> 注入修复提示并重试；
- 第二次仍失败 -> `parse_error`，Trace 保留原始输出；
- 工具错误 -> Observation，让模型自己修正。

这比"脚本模型一次成功"更接近生产，也更能体现 Trace 的价值。
