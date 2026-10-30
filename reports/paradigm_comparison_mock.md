# 三种范式在同一框架下的轨迹对比

- 运行模式：**脚本模型（Offline Mock，验证链路，不代表回答质量）**
- 任务：帮我规划北京一日游：先查北京今天的天气，再算 3 张地铁票（每张 5 元）和 2 份午餐（每份 38 元）的总价，最后判断是否适合户外活动并给出两条建议。
- 共同条件：同一个 `HaloAgent v1.0`、同一套工具（weather/calculator/current_time）、同一条 Trace schema。

## 指标对比

| 范式 | 状态 | LLM 调用 | 工具调用 | 步数 | Prompt Tokens | Completion Tokens | 总 Tokens | 耗时(s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ReAct | success | 3 | 2 | 3 | 1561 | 105 | 1666 | 0.002 |
| Plan-and-Solve | success | 7 | 2 | 3 | 2604 | 149 | 2753 | 0.004 |
| Reflection | success | 6 | 2 | 5 | 2575 | 145 | 2720 | 0.003 |

> Token 为框架估算值（Mock 模式无供应商 usage）；真实模式以 API 返回为准。

## 轨迹摘录

### ReAct

边想边做；每步用 Observation 修正判断，步数最少。

- `thought` 先查北京天气，才能判断是否适合户外。
- `action` weather({"city": "北京"})
- `tool_result` OK 北京：晴，气温 18°C，湿度 40%，北风 2 级。
- `thought` 天气已拿到，接下来算交通和午餐总价。
- `action` calculator({"expression": "(3*5)+(2*38)"})
- `tool_result` OK (3*5)+(2*38) = 91
- `final` 北京今天晴、18°C，适合户外。地铁票 15 元 + 午餐 76 元 = 91 元。建议：① 上午去公园，午后转室内展馆避开日晒；② 预留 10% 机动预算。

完整 trace：`E:\helloAgents学习\haloagent-v1\runs\react-20261030-155042-d0140a.jsonl`

### Plan-and-Solve

先出完整计划再逐步执行；计划可见，但计划质量决定上限。

- `plan` 查询北京今天的天气 / 计算 3 张地铁票与 2 份午餐的总价 / 综合天气和预算判断是否适合户外
- `thought` 第一步查天气。
- `action` weather({"city": "北京"})
- `tool_result` OK 北京：晴，气温 18°C，湿度 40%，北风 2 级。
- `thought` 第二步算总价。
- `action` calculator({"expression": "(3*5)+(2*38)"})
- `tool_result` OK (3*5)+(2*38) = 91
- `final` 北京今天晴、18°C，适合户外。3 张地铁票 15 元，2 份午餐 76 元，合计 91 元。建议上午安排户外、下午转室内，并预留少量机动预算。

完整 trace：`E:\helloAgents学习\haloagent-v1\runs\plan_solve-20261030-155042-710a13.jsonl`

### Reflection

先出草稿，再评审打分并修订；质量更高，token 成本也更高。

- `thought` 先查天气。
- `action` weather({"city": "北京"})
- `tool_result` OK 北京：晴，气温 18°C，湿度 40%，北风 2 级。
- `thought` 再算总价。
- `action` calculator({"expression": "(3*5)+(2*38)"})
- `tool_result` OK (3*5)+(2*38) = 91
- `final` 北京晴，18°C，总价 91 元，适合户外。
- `reflection` score=6 issues=['没有列出地铁和午餐的分项', '建议不够具体'] suggestion=补分项，并给两条可执行建议
- `revision` 北京今天晴、18°C，适合户外。费用分项：3 张地铁票 15 元，2 份午餐 76 元，合计 91 元。建议：① 上午安排公园或徒步，午后转室内展馆；② 带薄外套并预留 10% 机动预算。
- `reflection` score=9 issues=[] suggestion=可以交付

完整 trace：`E:\helloAgents学习\haloagent-v1\runs\reflection-20261030-155042-9221c2.jsonl`

## 结论

- **ReAct** 的步数与调用最少，适合“需要外部事实、但路径不必提前确定”的任务；缺点是容易在工具错误上绕路。
- **Plan-and-Solve** 的计划是显式产物，适合步骤清晰、需要审计的任务；代价是规划与逐步执行带来更多 LLM 调用。
- **Reflection** 多了评审与修订，答案通常更完整；代价是 token 成倍增加，且需要质量阈值防止无限改稿。
- 三者共享 `run() -> RunResult`、Memory/Context、工具注册表与 Trace，所以对比的是**范式差异**，不是工程实现差异。

## 复现

```powershell
python demo/compare_paradigms.py
python demo/compare_paradigms.py --real --config configs/default.yaml
```
