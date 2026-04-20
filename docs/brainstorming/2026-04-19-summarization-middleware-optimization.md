# SummarizationMiddleware 优化方案

> 针对 `SummarizationMiddleware` 的 4 个已确认缺陷，结合 Claude Code 压缩提示词设计、项目中文工具调用密集场景，提出具体优化方案。

---

## 一、问题清单

### 缺陷 1：`trigger_tokens` 配置项失效（死配置）

**现状**：`config.json` 中配置了 `trigger_tokens: 8000`，但 `agent.py:174-175` 完全忽略此值：

```python
# agent.py:174-175
sum_cfg = mw_cfg.get("summarization", {})
trigger_tokens = int(context_window * 0.6)  # 硬编码 60%，未读 sum_cfg.trigger_tokens
```

**影响**：
- 用户修改 `trigger_tokens` 不会生效，实际阈值为 `131072 × 0.6 = 78643` token
- 无法通过配置灵活调整触发时机

### 缺陷 2：`trim_tokens_to_summarize` 写死 4000 token

**现状**：`SummarizationMiddleware` 构造时有个参数 `trim_tokens_to_summarize`（LangChain 默认值 4000），但 `agent.py:176-182` 未传此参数：

```python
# agent.py:176-182 — 缺少 trim_tokens_to_summarize 参数
middleware.append(
    SummarizationMiddleware(
        model=summary_llm,
        trigger=("tokens", trigger_tokens),
        keep=("messages", sum_cfg.get("keep_messages", 10)),
    )
)
```

**影响**：

假设 128K 上下文窗口触发摘要时：
- 消息总量 ≈ 78K token
- 保留最近 10 条 ≈ 5K-10K token
- 待摘要内容 ≈ 68K-73K token
- 实际发给摘要 LLM 的 = **4000 token**（仅占待摘要内容的 ~6%）

94% 的待摘要内容被直接丢弃，摘要 LLM 只能看到最后一小段，无法生成有意义的全量摘要。

### 缺陷 3：Zone 3 SystemMessage 无保护

**现状**：系统提示分三层注入，但只有 Zone 1+2 被保护：

| 层 | 注入方式 | 存入 state["messages"] | 压缩安全性 |
|---|---------|----------------------|-----------|
| Zone 1+2 (stable) | `create_agent(system_prompt=...)` | 否，框架每次调用时注入 | 安全 |
| Zone 3 (dynamic) | `SystemMessage(content=...)` 插入 messages | 是，随 checkpoint 持久化 | **会被压缩** |

`agent.py:576-577` 将 Zone 3 动态内容作为 SystemMessage 注入：

```python
if dynamic_prefix:
    messages.insert(len(messages) - 1, SystemMessage(content=dynamic_prefix))
```

这些 SystemMessage（包含记忆检索结果、任务状态、update_task 指引）存入 checkpoint 后，会被 `SummarizationMiddleware` 当作普通消息处理：
- `_trim_messages_for_summary` 中 `include_system=True`（`summarization.py:654`），SystemMessage 可被裁剪丢弃
- `_partition_messages` 中 SystemMessage 无特殊保护，会被纳入摘要范围

**影响**：Agent 在长对话中丢失早期的记忆上下文和任务状态指引。虽然 Zone 3 每轮会重新注入最新的，但历史轮次的记忆上下文丢失后，摘要无法准确还原 Agent 曾掌握的信息。

### 缺陷 4：`summary_prompt` 不可配置

**现状**：`SummarizationMiddleware.__init__` 支持 `summary_prompt` 参数（`summarization.py:168`），但 `agent.py` 注册时未传，config.json 也无配置项。

当前使用 LangChain 内置的 `DEFAULT_SUMMARY_PROMPT`，存在两个问题：
1. **全英文**，与项目中文人设不一致
2. **通用设计**，未考虑工具调用密集、TaskState 追踪等业务特征
3. **无直接引用要求**，摘要可能改写关键信息导致上下文漂移

---

## 二、设计原则

| 原则 | 说明 |
|------|------|
| **最小侵入** | 优先通过子类扩展而非修改 LangChain 源码，保持框架兼容性 |
| **配置驱动** | 所有阈值通过 config.json 控制，支持运行时调整 |
| **SystemMessage 保护** | 摘要过程中 SystemMessage 不参与压缩，保持原文注入 |
| **上下文保全** | trim 阈值应确保摘要 LLM 能覆盖足够的待摘要内容 |

---

## 三、逐项修复方案

### 修复 1：让 `trigger_tokens` 和 `trigger_ratio` 均可配置

**改动文件**：`backend/graph/agent.py`（`_build_middleware` 方法）

**设计**：config.json 支持 `trigger_ratio`（比例模式）和 `trigger_tokens`（绝对值模式），比例模式优先。

```json
// config.json — 优化后的配置结构
"summarization": {
    "enabled": true,
    "trigger_ratio": 0.6,
    "trigger_tokens": null,
    "keep_messages": 10,
    "trim_ratio": 0.30,
    "trim_tokens": null,
    "summary_prompt_file": null
}
```

**代码改动**（`agent.py` `_build_middleware` 方法）：

```python
# 第 2 层：自动摘要（使用轻量模型）
if mw_cfg.get("summarization", {}).get("enabled", True):
    summary_llm = self._create_summary_llm()
    if summary_llm:
        sum_cfg = mw_cfg.get("summarization", {})

        # 触发阈值：trigger_ratio 比例优先，trigger_tokens 作为绝对值覆盖
        trigger_ratio = sum_cfg.get("trigger_ratio", 0.6)
        trigger_tokens = int(context_window * trigger_ratio)
        trigger_tokens_cfg = sum_cfg.get("trigger_tokens")
        if trigger_tokens_cfg and trigger_tokens_cfg > 0:
            trigger_tokens = trigger_tokens_cfg

        # trim 阈值：同上逻辑，比例优先
        trim_ratio = sum_cfg.get("trim_ratio", 0.30)
        trim_tokens = int(context_window * trim_ratio)
        trim_tokens_cfg = sum_cfg.get("trim_tokens")
        if trim_tokens_cfg and trim_tokens_cfg > 0:
            trim_tokens = trim_tokens_cfg

        # 自定义摘要提示词
        summary_prompt = self._load_summary_prompt(sum_cfg)

        middleware.append(
            ContextAwareSummarizationMiddleware(
                model=summary_llm,
                trigger=("tokens", trigger_tokens),
                keep=("messages", sum_cfg.get("keep_messages", 10)),
                trim_tokens_to_summarize=trim_tokens,
                summary_prompt=summary_prompt,
            )
        )
```

**向下兼容**：旧的 `trigger_tokens: 8000` 配置会生效（作为绝对值），无此配置时回退到 `trigger_ratio: 0.6`。

### 修复 2：`trim_tokens_to_summarize` 联动上下文窗口

**改动文件**：`backend/graph/agent.py`（同修复 1 的代码段）

**设计**：

当前固定 4000 token 的问题：
- 128K 窗口：待摘要内容可能 68K token，只发 4000 给摘要 LLM → 信息丢失 94%
- 32K 窗口：待摘要内容可能 15K token，发 4000 → 信息丢失 73%

优化方案：`trim_ratio = 0.30`，即上下文窗口的 30%。效果对比：

| 上下文窗口 | 旧值(固定) | 新值(30%) | 占待摘要内容比例（估算） |
|-----------|-----------|----------|----------------------|
| 32K | 4,000 token | 9,600 token | ~64% |
| 128K | 4,000 token | 38,400 token | ~56% |
| 1M | 4,000 token | 300,000 token | ~50% |

辅助模型 `qwen3.5-flash` 上下文窗口也是 128K，38400 token 在其承受范围内（摘要 prompt 本身约 1K token + 输出约 2K token，合计 ~41K，远低于 128K 上限）。同时支持 `trim_tokens` 绝对值覆盖，适配小窗口模型。

### 修复 3：Zone 3 SystemMessage 保护

**改动文件**：`backend/graph/middleware.py`（新增 `ContextAwareSummarizationMiddleware` 类）

**设计**：继承 `SummarizationMiddleware`，重写 `abefore_model`，在摘要前后保护 SystemMessage。

**核心思路**：
1. 从 messages 中提取所有 SystemMessage（记录原始位置）
2. 构造不含 SystemMessage 的临时消息列表，交给父类处理
3. 父类返回 `RemoveMessage(REMOVE_ALL_MESSAGES) + summary + preserved`
4. 将 SystemMessage 重新插入到 summary 之后、preserved 之前

```python
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import SystemMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES


class ContextAwareSummarizationMiddleware(SummarizationMiddleware):
    """扩展 SummarizationMiddleware，保护 SystemMessage 不被摘要。

    SystemMessage 包含 Zone 3 动态内容（记忆上下文、任务状态、技能指引），
    这些内容在摘要过程中应保持原文，不参与 LLM 摘要。
    """

    async def abefore_model(self, state, runtime):
        messages = state["messages"]

        # Step 1: 提取所有 SystemMessage
        system_messages = [msg for msg in messages if isinstance(msg, SystemMessage)]

        if not system_messages:
            # 无 SystemMessage，直接调用父类
            return await super().abefore_model(state, runtime)

        # Step 2: 构造不含 SystemMessage 的临时 state
        non_system = [msg for msg in messages if not isinstance(msg, SystemMessage)]
        temp_state = {**state, "messages": non_system}

        # Step 3: 调用父类摘要逻辑（处理非 System 消息）
        result = await super().abefore_model(temp_state, runtime)

        if result is None:
            return None

        # Step 4: 在结果中重新注入 SystemMessage
        result_messages = result["messages"]
        # result_messages 结构: [RemoveMessage, HumanMessage(summary), ...preserved]
        # 在 RemoveMessage + summary 之后、preserved 之前插入 SystemMessage
        insert_pos = min(2, len(result_messages))  # RemoveMessage[0] + summary[1]
        for sys_msg in system_messages:
            result_messages.insert(insert_pos, sys_msg)
            insert_pos += 1

        return {"messages": result_messages}
```

**为什么选择子类方案**：
- 不修改 LangChain 源码，保持框架升级兼容性
- 保护逻辑集中在一个类中，职责清晰
- 无 SystemMessage 时回退到父类行为，零开销

**SystemMessage 在摘要结果中的位置**：

```
[0] RemoveMessage(REMOVE_ALL_MESSAGES)   ← 清空全部
[1] HumanMessage(summary)                 ← 摘要
[2] SystemMessage(Zone 3 - turn 1)        ← 保护的 SystemMessage
[3] SystemMessage(Zone 3 - turn 2)
[4] HumanMessage(最近的用户消息)           ← preserved 开始
[5] AIMessage(...)
...
```

### 修复 4：自定义摘要提示词

**改动文件**：
- `backend/graph/agent.py`（新增 `_load_summary_prompt` 方法）
- `backend/config.json`（新增 `summary_prompt_file` 配置项）
- `backend/workspace/summary_prompt.md`（默认提示词文件）

**设计**：

提示词加载优先级：
1. `config.json` 中 `summary_prompt_file` 指定的文件路径
2. `workspace/summary_prompt.md` 默认文件（如存在）
3. 内置硬编码的中文提示词（最后兜底）

```python
# agent.py 新增方法
def _load_summary_prompt(self, sum_cfg: dict) -> str:
    """加载自定义摘要提示词。

    优先级：config 指定文件 > workspace/summary_prompt.md > 内置默认
    """
    # 1. 配置指定路径
    prompt_file = sum_cfg.get("summary_prompt_file")
    if prompt_file and Path(prompt_file).exists():
        return Path(prompt_file).read_text(encoding="utf-8")

    # 2. 默认文件
    default_path = self._base_dir / "workspace" / "summary_prompt.md"
    if default_path.exists():
        return default_path.read_text(encoding="utf-8")

    # 3. 内置默认
    return DEFAULT_SUMMARY_PROMPT_ZH
```

**提示词存放为 Markdown 文件**的好处：
- 遵循项目 Markdown-as-config 设计模式
- 用户可直接编辑，无需改代码
- 通过 `summary_prompt_file` 配置可切换不同提示词版本

---

## 四、自定义摘要提示词设计

结合 Claude Code 的 9 节结构化摘要与项目实际情况设计。

### 设计参考

| 来源 | 设计要点 |
|------|---------|
| Claude Code Layer 3 | 9 节结构化摘要，要求**直接引用原文关键短语**防止上下文漂移 |
| 项目特征 | 中文对话、工具调用密集（terminal/read_file/write_file）、有 TaskState 追踪、有记忆检索 |
| LangChain DEFAULT_SUMMARY_PROMPT | 4 节结构（SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS），占位符 `{messages}` |

### 提示词正文

保存在 `backend/workspace/summary_prompt.md`，包含 `{messages}` 占位符供 `str.format()` 替换：

```markdown
<role>
会话上下文提取助手
</role>

<primary_objective>
你唯一的任务是从下方对话历史中提取最高质量、最相关的上下文信息。
</primary_objective>

<objective_information>
对话即将接近上下文窗口上限，你需要从对话历史中提取最重要的信息。
这些上下文将覆盖下方的对话历史，因此请确保只保留推进整体目标最关键的信息。
</objective_information>

<instructions>
下方对话历史将被你提取的上下文替代。
你需要确保不会重复已完成的工作，因此提取的上下文应聚焦于推进整体目标最关键的信息。

请使用以下结构组织摘要。每个章节作为检查清单——有相关信息则填充，无则标注"无"：

## 会话意图
用户的核心目标或请求是什么？正在执行的整体任务是什么？简洁但完整地描述整个会话的目的。

## 关键决策与推理
记录对话中所有重要的选择、结论或策略。包含关键决策背后的推理。记录被拒绝的选项及其原因。
**重要**：直接引用原文中的关键短语（用引号标注），不要改写，防止上下文漂移。

## 工具调用摘要
按时间顺序列出所有重要的工具调用及其结果摘要：
- 执行的终端命令及其输出要点
- 读取/写入的文件及关键内容发现
- API 调用及其响应
- 搜索查询及其发现

## 文件与产物
本次会话中创建、修改或访问了哪些文件？列出具体文件路径并简要描述对每个文件的更改。

## 错误与修复
遇到了哪些错误？如何诊断和修复的？记录错误消息的关键部分（直接引用原文）。

## 用户消息摘要
按时间顺序列出每条用户消息的核心意图（每条一句话）。

## 当前进展
目前完成到了哪里？哪些步骤已完成，哪些正在进行中？

## 后续步骤
为了达成会话意图，还剩哪些具体任务？下一步应该做什么？
</instructions>

请仔细阅读整个对话历史，提取最重要和最相关的上下文来替代它，以释放对话历史空间。
只输出提取的上下文，不要包含任何额外信息或前后说明。

<messages>
待摘要的消息：
{messages}
</messages>
```

### 与 LangChain 原始提示词的对比

| 维度 | LangChain DEFAULT_SUMMARY_PROMPT | 自定义提示词 |
|------|--------------------------------|-------------|
| 语言 | 英文 | 中文 |
| 结构节数 | 4 节 | 8 节 |
| 工具调用 | 未覆盖 | 独立章节 |
| 错误修复 | 未覆盖 | 独立章节 |
| 直接引用要求 | 无 | 明确要求引用原文关键短语 |
| 用户消息 | 未单独覆盖 | 独立章节（每条一句） |
| 占位符 | `{messages}` | `{messages}`（兼容） |

---

## 五、改动范围总览

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `backend/graph/agent.py` | 修改 | `_build_middleware`：读取 trigger/trim 配置，传入新参数；新增 `_load_summary_prompt` |
| `backend/graph/middleware.py` | 新增类 | `ContextAwareSummarizationMiddleware`：子类化 SummarizationMiddleware，保护 SystemMessage |
| `backend/graph/middleware.py` | 新增常量 | `DEFAULT_SUMMARY_PROMPT_ZH`：内置中文摘要提示词兜底 |
| `backend/workspace/summary_prompt.md` | 新增文件 | 默认自定义提示词文件 |
| `backend/config.json` | 修改 | `summarization` 配置段新增 `trigger_ratio`、`trim_ratio`、`summary_prompt_file` |

### 不改动的部分

- LangChain 框架源码（通过子类扩展）
- `state["messages"]` 数据结构
- 手动摘要 API（`compress.py` 的 `/summarize` 端点）
- Checkpoint 持久化逻辑
- 前端代码

### 向下兼容性

| 场景 | 处理方式 |
|------|---------|
| 旧 config.json 无新字段 | 所有新字段有默认值，行为与当前一致 |
| 无 workspace/summary_prompt.md | 回退到内置 DEFAULT_SUMMARY_PROMPT_ZH |
| 无 SystemMessage 的对话 | 子类直接调用父类，零开销 |
| 手动摘要 API | 使用 _generate_checkpoint_summary，直接调用辅助 LLM，不走中间件，不受影响 |

---

## 六、config.json 优化后完整结构

```json
"summarization": {
    "enabled": true,
    "trigger_ratio": 0.6,
    "trigger_tokens": null,
    "keep_messages": 10,
    "trim_ratio": 0.30,
    "trim_tokens": null,
    "summary_prompt_file": null
}
```

字段说明：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `enabled` | bool | true | 是否启用自动摘要 |
| `trigger_ratio` | float | 0.6 | 触发比例（上下文窗口的百分比），优先使用 |
| `trigger_tokens` | int\|null | null | 绝对值触发阈值，覆盖 trigger_ratio |
| `keep_messages` | int | 10 | 保留最近 N 条消息 |
| `trim_ratio` | float | 0.30 | 发给摘要 LLM 的 token 占窗口比例，优先使用 |
| `trim_tokens` | int\|null | null | 绝对值 trim 阈值，覆盖 trim_ratio |
| `summary_prompt_file` | string\|null | null | 自定义提示词文件路径，null 时查找 workspace/summary_prompt.md |
