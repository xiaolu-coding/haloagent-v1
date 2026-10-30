# 第 4 周学习总结 · 提交填写稿

> 直接复制下面各节到学习营的提交表单即可。
> **提交前必须做一件事**：把工程推到你的 GitHub/Gitee，把第 3 节的链接替换成真实仓库地址。

---

## 1. 学习内容（表单必填，≥20 字）

本周对应《Hello-Agents》第 7 章，并把第 1–6 章的知识收束成一个可运行的自研框架 **HaloAgent v1.0**。

我先把前七章的主线串起来：第 1 章定义智能体是"LLM + 工具 + 记忆 + 规划"的循环；第 2 章说明智能体从符号主义、强化学习走到 LLM Agent 的历史必然；第 3 章拆开 LLM 的 token、上下文窗口与幻觉，解释了为什么 Agent 需要工具和上下文管理；第 4 章给出 ReAct、Plan-and-Solve、Reflection 三种经典范式；第 5 章用低代码平台展示节点、连线、变量和条件分支；第 6 章通过主流框架的实践暴露了"过度抽象、黑盒、依赖重"的问题；第 7 章则要求从零构建框架，完成从"使用者"到"创造者"的转变。

基于这个脉络，我没有再写一个单文件 Demo，而是实现了 7 个模块的最小内核：`Message` / `Config` / `ChatModel` / `ToolRegistry` / `Memory+Context` / `BaseAgent` / `Tracer`。模型层用 `ChatModel` 抽象 + OpenAI 兼容实现 + Mock + 重试；工具层用 `@tool` 自动生成 JSON Schema，支持插件热加载/卸载；运行时用 `BaseAgent.run()` 统一生命周期，把 ReAct、Plan-and-Solve、Reflection 都迁到同一个 `run(task)` 接口；记忆层用 Window/Summary + 预算裁剪解决上下文膨胀；Trace 层把 LLM 请求、Thought、Action、Observation、工具结果、耗时全部写成 JSONL，让"为什么这么答"可以逐帧回放。此外用 YAML 实现了一个低代码工作流引擎（tool / agent / template / condition / output + DAG），把第 5 章的画布语义落到代码。

最终交付：框架源码、架构图、22 个单元测试、三范式同任务对比报告、低代码工作流 Demo、工具热插拔与流式输出 Demo。三范式在 Mock 模式下均跑通：ReAct 3 次 LLM / 2 次工具 / 1666 tokens；Plan-and-Solve 7 次 / 2 次 / 2753 tokens；Reflection 6 次 / 2 次 / 2720 tokens。最大的收获是：框架的价值不是"功能多"，而是用统一约束让不同范式的差异变得可对比、可解释、可扩展。

---

## 2. 学习内容附件（可选，建议上传）

| 附件 | 文件 | 说明 |
|---|---|---|
| 学习总结（长文） | `docs/学习总结-第4周.md` | 前七章串联 + 实现细节 + 问题记录 |
| 架构图 | `docs/architecture.png` | 五层架构：接入 / 范式 / 运行时 / 模型 / 外部 |
| API 设计文档 | `docs/API.md` | 公开 API、配置 Schema、工作流 DSL、Trace 事件 |
| 三范式对比报告 | `reports/paradigm_comparison_mock.md` | 同任务、同工具、同框架的实测指标与轨迹 |
| 单元测试记录 | `reports/pytest.txt` | 22 passed |
| 工程压缩包 | `dist/haloagent-v1.zip` | 不含 `.env`、不含运行产物 |

---

## 3. 代码/工程链接（表单必填）

### 现在可用的本地仓库

```text
E:\helloAgents学习\haloagent-v1
```

本地已初始化 git，提交号见 `git log -1 --oneline`。

### 提交到公网（二选一）

**GitHub：**

```powershell
cd E:\helloAgents学习\haloagent-v1
git remote add origin https://github.com/<你的GitHub用户名>/haloagent-v1.git
git branch -M main
git push -u origin main
```

**Gitee：**

```powershell
cd E:\helloAgents学习\haloagent-v1
git remote add origin https://gitee.com/<你的Gitee用户名>/haloagent-v1.git
git branch -M main
git push -u origin main
```

推送后，把表单里的链接填成：

```text
https://github.com/<你的GitHub用户名>/haloagent-v1
```

> 如果仓库设为私有，请把助教/评审加入协作者，或在提交时附上压缩包。
> **不要提交 `.env`**；`.gitignore` 已排除。

---

## 4. 遇到的问题（可选）

1. **模型不按 ReAct 格式输出**：真实模型经常只写自然语言，或把 `Action Input` 包在 Markdown 围栏里，导致解析失败。
2. **工具参数类型不转换**：`from __future__ import annotations` 把 `int` 注解变成字符串，工具拿到 `"41"` 而不是 `41`，运行时报 `TypeError`。
3. **上下文越跑越长**：ReAct 每一步都把完整历史塞进 prompt，几轮后 token 暴涨，模型开始"忘事"。
4. **流式重试导致重复文本**：流式响应已经输出一半时网络错误，重试后用户看到重复内容。
5. **Reflection 停不下来**：评审总能挑出问题，没有阈值时会无限改稿。
6. **工作流变量类型丢失**：`${inputs.count}` 被替换成字符串，计算工具拿到 `"3"`。

---

## 5. 解决方法（可选）

1. **解析器 + 一次自我纠正**：支持中英文冒号、代码围栏、JSON/单参数裸值；第一次失败把错误和修复提示写回上下文，第二次仍失败则标记 `parse_error`，保留原始输出，不伪造答案。
2. **`typing.get_type_hints()` 解析注解**：在 `ToolSpec.from_function()` 里先解析字符串注解，再做受控类型转换；未知参数直接报错并列出可用参数，让模型能改。
3. **Window/Summary Memory + ContextBuilder**：按消息数和 token 预算从旧到新裁剪，永远保留系统提示与最后一条用户消息；每次构建发 `context` 事件，记录 `original/kept/dropped/budget`。
4. **重试层区分"是否已输出"**：`RetryingChatModel.stream()` 记录 `yielded`，一旦已吐出 delta 就不再重放；只重试 429/5xx/超时，401/400 快速失败。
5. **双刹车**：`quality_threshold`（≥8 分停止）+ `reflect_rounds`（最多几轮）；评审强制输出 1–10 分和具体 issue，避免空泛反馈。
6. **模板整串匹配保留类型**：`${inputs.count}` 单独出现时返回原类型，只有嵌入字符串才 `str()`；条件表达式单独求布尔值。

---

## 6. 本周产出与验收对照（可附在总结末尾）

| 第 4 周要求 | 产出 | 证据 |
|---|---|---|
| 自研框架 v1.0 | `src/haloagent/` | 7 个模块、约 2.5k 行 |
| 代码仓库 | 本地 git + 远端待推送 | `git log -1` |
| 架构图 | `docs/architecture.png` | 五层架构 |
| 单元测试 | `tests/` | 22 passed |
| 端到端演示 | `demo/` + CLI | 离线可跑 |
| 三范式迁移 | `agents/` | 统一 `run(task)` |
| 低代码工作流 | `workflow/` + YAML | DAG + 条件 + 变量 |
| 可替换 Model Layer | `models/` | OpenAI 兼容 / Mock / 重试 |
| 工具热插拔 | `tools/` | load/unload + entry points |
| Context/Memory | `memory/` | Window/Summary + 预算 |
| 全链路 Trace | `trace/` | JSONL + 实时 listener |
| API 设计文档 | `docs/API.md` | 完整 |
| 轨迹对比报告 | `reports/paradigm_comparison_mock.md` | 实测 |

---

## 7. 评审快速验证（3 条命令）

```powershell
cd haloagent-v1
$env:PYTHONPATH="$PWD\src"
python -m pytest -q
python demo/compare_paradigms.py
python -m haloagent workflow examples/workflows/travel_brief.yaml --config configs/mock.yaml
```

预期：`22 passed`；三范式均 `success`；工作流输出一份出行简报。
不需要 API Key，不需要联网。
