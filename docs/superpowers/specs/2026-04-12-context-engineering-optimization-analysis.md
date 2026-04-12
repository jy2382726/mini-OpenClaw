# mini-OpenClaw 上下文工程优化分析报告

> 基于 Anthropic Claude Code、Manus、LangChain Deep Agents 三大框架的上下文工程实践，结合项目现状进行全面分析。

---

## 一、项目上下文工程现状盘点

### 1.1 当前架构概览

```
┌─────────────────────────────────────────────────┐
│                  System Prompt                    │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐      │
│  │SKILLS_    │ │SOUL.md    │ │IDENTITY.md│      │
│  │SNAPSHOT.md│ │           │ │           │      │
│  └───────────┘ └───────────┘ └───────────┘      │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐      │
│  │USER.md    │ │AGENTS.md  │ │MEMORY.md  │      │
│  │           │ │           │ │(或RAG指引) │      │
│  └───────────┘ └───────────┘ └───────────┘      │
├─────────────────────────────────────────────────┤
│              Tool Definitions (7+个)              │
├─────────────────────────────────────────────────┤
│              Message History (≤20条)              │
│  ┌──────────────────────────────────────────┐    │
│  │ [压缩上下文摘要]                           │    │
│  │ user: ... → assistant: ... → tool: ...    │    │
│  └──────────────────────────────────────────┘    │
├─────────────────────────────────────────────────┤
│              Current User Message                │
│  + RAG 检索结果（按需注入为 assistant 消息）        │
└─────────────────────────────────────────────────┘
```

### 1.2 关键指标现状

| 维度 | 当前状态 | 问题 |
|------|---------|------|
| 系统提示 | 约 2000-4000 tokens（估算），每次重建 | 无缓存前缀设计，每次请求全量计算 |
| 工具定义 | 7-10 个工具，全量加载 | 工具 JSON Schema 占用可观 token，无法按需裁剪 |
| 历史消息 | 硬上限 20 条，前 50% 压缩 | 压缩质量低（500 字摘要），无分级策略 |
| 记忆注入 | MEMORY.md 全文或 RAG top-k | RAG 结果以 assistant 消息注入，语义角色混淆；无相关性过滤 |
| 任务状态 | 无 | Agent 无法追踪多步任务进度 |
| 工具输出 | 原样保留在历史中 | 大量 token 浪费在已消耗的 tool result 上 |

---

## 二、系统提示优化（System Prompt）

### 2.1 问题诊断

**当前实现**（`prompt_builder.py`）：
- 6 层 Markdown 文件拼接，使用 `<!-- label -->` 分隔
- 每次请求调用 `build_system_prompt()` 重新构建
- 技能快照 `SKILLS_SNAPSHOT.md` 包含当前 6 个技能的 XML 描述

**核心问题：**

1. **每次请求全量重建**：`agent.py:_build_agent()` 每次调用都重建 Agent 和系统提示，完全没有利用 KV-cache
2. **技能快照膨胀**：6 个技能的 XML 描述虽不多，但随着技能增长将显著膨胀；且每次请求只需 0-2 个技能，全量加载浪费 token
3. **动态内容混入静态区域**：`MEMORY.md`（或 RAG 指引）嵌在系统提示末尾，导致整个前缀缓存失效
4. **无 cache breakpoint 设计**：没有利用 OpenAI/DashScope 的 prompt caching 能力

### 2.2 优化方案：三段式缓存前缀

参考 Manus 的"KV-cache hit rate 是最重要的指标"和 Anthropic 的"keep prompt prefix stable"：

```
┌──────────────────────────────────────────────────────┐
│ [Cache Zone 1] — 极稳定层（几乎不变）                    │
│  SOUL.md + IDENTITY.md + USER.md                     │
│  ← cache breakpoint ─────────────────────────────     │
├──────────────────────────────────────────────────────┤
│ [Cache Zone 2] — 低频变化层（会话间变化）                 │
│  AGENTS.md + 工具定义 + 精简技能摘要                     │
│  ← cache breakpoint ─────────────────────────────     │
├──────────────────────────────────────────────────────┤
│ [Cache Zone 3] — 高频变化层（每次请求可能变化）            │
│  动态记忆注入 + RAG 检索结果 + 任务状态                    │
└──────────────────────────────────────────────────────┘
```

**具体措施：**

| 措施 | 预估收益 | 实现复杂度 |
|------|---------|-----------|
| 将 MEMORY.md/RAG 结果从系统提示移到用户消息前缀 | 系统提示前缀稳定，缓存命中率有望显著提升 | 低 |
| 技能快照从 XML 全量描述改为「名称 + 一句话摘要」 | 系统提示减少数百 tokens（随技能数量增长收益更大） | 低 |
| 为 DashScope API 添加 `extra_body` cache control 参数 | 缓存命中后 token 单价降至 1/10，成本下降取决于实际命中率 | 中 |
| 系统提示模板化：使用变量占位符替代字符串拼接 | 确保序列化确定性 | 中 |

### 2.3 技能元数据精细化管理

#### 2.3.1 当前问题

**当前**（`skills_scanner.py`）：扫描 SKILL.md 时仅解析 `name`、`description`、`location` 三个字段，其余 frontmatter 全部丢弃。这导致：

- **无法区分技能调用权限**：有些技能应只允许用户通过斜杠命令手动触发（如 `skill-benchmark`），有些允许 Agent 自主调用（如 `get_weather`）
- **无法表达触发条件**：Agent 不知道何时该主动使用某个技能 vs 仅在用户明确要求时使用
- **无法表达技能复杂度**：简单工具型技能（如 `get_date`）和复杂流程型技能（如 `skill-creator-pro`）被同等对待
- **无法表达依赖和冲突**：技能间可能存在依赖关系或互斥关系

当前的快照格式（XML 全量描述）对于简单技能尚可，但复杂技能的元数据需求远超"名称+描述"。

#### 2.3.2 技能元数据 Schema：遵循 Agent Skills 标准

**严格遵循 [Anthropic Agent Skills 标准](https://agentskills.io)**，不在 frontmatter 顶层添加自定义字段。所有扩展属性统一放在标准规定的 `metadata` 字段中，确保与外部技能生态兼容。

##### Agent Skills 标准 frontmatter 字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 技能唯一标识，最多 64 字符，仅字母/数字/连字符/下划线/斜杠 |
| `description` | 是 | 一句话功能描述，最多 1024 字符 |
| `license` | 否 | 许可证标识 |
| `compatibility` | 否 | 兼容性声明，最多 500 字符 |
| `metadata` | 否 | **自由格式的 string→string 映射**，用于存放未定义的扩展属性 |
| `allowed-tools` | 否 | 空格分隔的允许工具列表（实验性） |

##### 简单技能示例（仅标准字段）

```yaml
---
name: get_weather
description: "获取指定城市的天气信息"
---
```

##### 复杂技能示例（通过 metadata 扩展）

```yaml
---
name: get_weather
description: "获取指定城市的天气信息"
metadata:
  # ── 调用控制 ──
  invocation_manual: "true"            # 允许用户手动调用（斜杠命令）
  invocation_auto: "true"              # 允许 Agent 自主判断调用
  invocation_priority: "normal"        # 调度优先级: critical | high | normal | low

  # ── 触发条件 ──
  trigger_patterns: "天气,气温,温度,weather"  # 关键词，逗号分隔
  trigger_confidence: "0.8"            # 自动触发置信度阈值

  # ── 分类标签 ──
  categories: "utility,weather"
  tags: "天气,API,外部服务"

  # ── 资源约束 ──
  needs_network: "true"
  timeout_seconds: "10"
  estimated_tokens: "500"

  # ── 依赖关系 ──
  depends_tools: "fetch_url"
  depends_skills: ""

  # ── 上下文需求 ──
  needs_memory: "false"
  needs_rag: "false"
  inject_system_prompt: "false"        # 是否需要完整注入到系统提示

  # ── 输出规格 ──
  output_format: "text"                # text | json | markdown
  output_max_tokens: "500"

  # ── 安全约束 ──
  sandbox_required: "true"
  permissions: "network"
  rate_limit: "10"

  # ── 生命周期 ──
  deprecated: "false"
  replaced_by: ""
---
```

> **关键设计决策**：`metadata` 的值必须是 string 类型（Agent Skills 标准规定 "string keys to string values"）。复杂结构（如列表、嵌套对象）需要序列化为逗号分隔字符串或 JSON 字符串。这保证了与所有遵循标准的外部技能互操作。

##### 简单技能 vs 复杂技能的对比

| 维度 | 简单技能 | 复杂技能 |
|------|---------|---------|
| frontmatter | 仅 `name` + `description` | `name` + `description` + `metadata` |
| 扩展属性 | 无 | 通过 `metadata` 字段存放 |
| 标准兼容 | 完全兼容 | 完全兼容（`metadata` 是标准字段） |
| 外部技能可接入 | 直接接入 | 直接接入，忽略未知 metadata 键即可 |
| Token 开销 | ~30 tokens | ~150-200 tokens |

#### 2.3.3 分层注入策略

根据技能 `metadata` 中的 `invocation_auto` 和 `inject_system_prompt` 键，采用不同的上下文注入策略：

```
┌─────────────────────────────────────────────────────────────────┐
│                   技能上下文注入决策树                              │
│                                                                  │
│  ┌─ metadata.invocation_auto == "true" ?                        │
│  │   ├─ YES → 紧凑摘要注入系统提示（Zone 2）                      │
│  │   │        格式: "skill_name: 一句话描述 [trigger: 关键词]"    │
│  │   │        例: "get_weather: 获取天气 [触发: 天气/气温]"        │
│  │   │                                                          │
│  │   └─ NO  → 不注入系统提示，仅注册到技能索引                     │
│  │           Agent 需通过 search_knowledge 工具发现                │
│  │                                                              │
│  └─ metadata.inject_system_prompt == "true" ?                   │
│      ├─ YES → 完整 SKILL.md 注入（仅限关键技能，如 AGENTS.md）    │
│      └─ NO  → 按需加载（Agent 用 read_file 读取）                │
└─────────────────────────────────────────────────────────────────┘
```

**四种注入级别：**

| 级别 | 条件 | 注入内容 | Token 开销 |
|------|------|---------|-----------|
| **Level 0: 隐藏** | `invocation_auto` 缺失或为 `"false"` | 不注入 | 0 |
| **Level 1: 索引** | `invocation_auto` = `"true"` | 名称 + 描述 + 触发词（~20 tokens/技能） | 低 |
| **Level 2: 按需加载** | Agent 决定使用时 | 通过 `read_file` 加载完整 SKILL.md | 按需 |
| **Level 3: 预加载** | `inject_system_prompt` = `"true"` | 完整 SKILL.md 注入系统提示 | 高 |

> **设计说明**：复杂技能若无 `metadata` 或 `metadata` 中无扩展键，退化为简单技能行为（Level 0 或 Level 1）。这确保了标准兼容性——外部技能只需提供 `name` + `description` 即可被系统识别。

#### 2.3.4 技能注册表（Skill Registry）

将当前的 `skills_scanner.py`（一次性扫描生成快照）升级为持久化的技能注册表，严格基于 Agent Skills 标准的 `metadata` 字段做扩展：

```python
class SkillMeta:
    """遵循 Agent Skills 标准的技能元数据"""
    name: str                              # 标准字段：唯一标识
    description: str                       # 标准字段：功能描述
    metadata: dict[str, str]               # 标准字段：扩展属性

    @property
    def is_auto_invocable(self) -> bool:
        """是否允许 Agent 自动调用"""
        return self.metadata.get("invocation_auto", "false") == "true"

    @property
    def trigger_patterns(self) -> list[str]:
        """获取触发词模式列表"""
        raw = self.metadata.get("trigger_patterns", "")
        return [p.strip() for p in raw.split(",") if p.strip()]

    @property
    def categories(self) -> list[str]:
        """获取分类标签"""
        raw = self.metadata.get("categories", "")
        return [c.strip() for c in raw.split(",") if c.strip()]

class SkillRegistry:
    """技能注册表：管理 Agent Skills 标准元数据"""

    def __init__(self, skills_dir: Path):
        self.skills: dict[str, SkillMeta] = {}
        self._index_by_trigger: dict[str, str] = {}   # 触发词 → 技能名
        self._index_by_category: dict[str, list] = {}  # 分类 → 技能列表

    def register(self, skill: SkillMeta):
        """注册技能，构建多维度索引"""
        self.skills[skill.name] = skill
        # 索引触发词
        for pattern in skill.trigger_patterns:
            self._index_by_trigger[pattern] = skill.name
        # 索引分类
        for cat in skill.categories:
            self._index_by_category.setdefault(cat, []).append(skill.name)

    def get_auto_invocable_skills(self) -> list[SkillMeta]:
        """获取 Agent 可自动调用的技能（用于系统提示注入）"""
        return [s for s in self.skills.values()
                if s.is_auto_invocable]

    def find_by_trigger(self, user_message: str) -> list[SkillMeta]:
        """根据用户消息匹配可能触发的技能"""
        matches = []
        for pattern, skill_name in self._index_by_trigger.items():
            if re.search(pattern, user_message):
                matches.append(self.skills[skill_name])
        return matches

    def build_compact_snapshot(self) -> str:
        """构建精简快照（用于系统提示 Zone 2）"""
        lines = ["## 可用技能（按需读取 SKILL.md 获取详情）"]
        for skill in self.get_auto_invocable_skills():
            triggers = skill.metadata.get("trigger_patterns", "")
            lines.append(f"- {skill.name}: {skill.description} [{triggers}]")
        return "\n".join(lines)
```

**对比当前方案的改进：**

| 维度 | 当前方案 | 优化后方案 |
|------|---------|-----------|
| 元数据字段 | name + description（其余丢弃） | 标准字段 + `metadata` 扩展 |
| 标准兼容 | 无 | 严格遵循 Agent Skills 标准 |
| 注入策略 | 6 个技能全量注入（随技能增长线性膨胀） | 按 `metadata.invocation_auto` 分级注入 |
| Token 开销 | ~2000 tokens | ~300-500 tokens（仅 auto 技能索引） |
| 技能发现 | 被动（全量列出） | 主动（触发词匹配） |
| 外部技能接入 | 不支持 | 直接接入（忽略未知 metadata 键） |
| 扩展性 | 添加字段需改代码 | `metadata` 自由扩展，无需改代码 |

---

## 三、对话历史压缩优化（Conversation History）

### 3.1 问题诊断

**当前实现**（`api/compress.py`，前端手动触发）：
- 压缩前 50% 消息为 500 字摘要
- 使用单独 LLM（qwen，temperature=0.3）生成
- 最低要求 4 条消息才能压缩
- `agent.py` 中硬上限 `MAX_HISTORY_MESSAGES = 20` 限制历史长度

**核心问题：**

1. **简单的比例切割**：不考虑消息重要性，统一砍前 50%
2. **摘要质量差**：500 字限制过于粗暴，丢失关键上下文（如决策理由、错误教训）
3. **工具输出全量保留**：一个 `terminal` 调用返回 5000 字，压缩时一并保留
4. **无分级压缩**：没有"先清理工具输出 → 再压缩早期对话"的渐进策略
5. **压缩后信息不可恢复**：摘要丢弃的细节无法找回

### 3.2 优化方案：中间件驱动的双层压缩架构

> **核心策略**：以 `SummarizationMiddleware` 为压缩主力（自动触发、结构化摘要、消息配对保护），在其前方挂载独立的 `ToolOutputBudgetMiddleware` 做前置截断。两层通过 middleware 列表串联，各司其职。

#### 压缩架构总览

```
请求到达
  ↓
[ToolOutputBudgetMiddleware.before_model]    ← 第 1 层：前置截断
  │  每次请求前运行，将超过预算的工具输出原地截断
  │  降低整体 token 数，延缓摘要触发
  ↓
[SummarizationMiddleware.before_model]       ← 第 2 层：自动摘要
  │  token 超阈值时触发，生成结构化摘要替换旧消息
  │  内置 AI/Tool 消息配对保护、安全截断点
  ↓
[模型调用]
```

#### 第 1 层：工具输出预算制（前置截断）

`SummarizationMiddleware` 处理不了单个工具输出过大的场景——在摘要触发前，数千 token 的 tool result 原样保留在消息列表中。因此需要一个前置中间件，在每次模型调用前截断过大的工具输出。

```python
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

TOOL_OUTPUT_BUDGETS = {
    "terminal":     2000,  # 终端输出最多 2000 tokens
    "python_repl":  1500,  # Python 输出最多 1500 tokens
    "fetch_url":    3000,  # 网页内容最多 3000 tokens
    "read_file":    2000,  # 文件内容最多 2000 tokens
    "search_knowledge": 1000,  # 检索结果最多 1000 tokens
}

class ToolOutputBudgetMiddleware(AgentMiddleware):
    """工具输出预算制：在摘要触发前先截断大输出"""

    async def abefore_model(self, state, runtime):
        messages = state["messages"]
        processed = []
        changed = False
        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.name in TOOL_OUTPUT_BUDGETS:
                budget = TOOL_OUTPUT_BUDGETS[msg.name]
                if self._exceeds_budget(msg.content, budget):
                    msg = msg.model_copy(update={
                        "content": self._truncate_with_summary(msg.content, budget)
                    })
                    changed = True
            processed.append(msg)
        if changed:
            return {"messages": processed}
        return None

    @staticmethod
    def _exceeds_budget(content: str, budget: int) -> bool:
        return len(content) > budget * 4  # 粗略：1 token ≈ 4 字符

    @staticmethod
    def _truncate_with_summary(content: str, budget: int) -> str:
        char_budget = budget * 4
        head = content[:char_budget * 2 // 3]
        tail = content[-char_budget // 3:]
        omitted = len(content) - char_budget
        return f"{head}\n...[省略约 {omitted} 字符]...\n{tail}"
```

**设计要点**：
- 截断发生在**原地**（替换 ToolMessage content），不改变消息数量和顺序
- 保留头尾内容，确保 Agent 能看到关键的开头和结尾信息
- 前置截断降低 token 总量，延缓 SummarizationMiddleware 的触发时机

#### 第 2 层：SummarizationMiddleware（自动摘要）

直接使用 LangChain 内置的 `SummarizationMiddleware`，无需自行实现摘要逻辑。

```python
from langchain.agents.middleware import SummarizationMiddleware

SummarizationMiddleware(
    model=lightweight_llm,          # 用轻量模型做摘要（如 qwen-turbo）
    trigger=("tokens", 8000),       # 超过 8000 tokens 时触发摘要
    keep=("messages", 10),          # 始终保留最近 10 条消息
)
```

**SummarizationMiddleware 内置能力清单**（源码 `langchain/agents/middleware/summarization.py`）：

| 能力 | 实现方式 | 对应的手动方案 |
|------|---------|--------------|
| 自动触发 | `trigger=("tokens", 8000)` | 当前：前端手动点击压缩按钮 |
| 保留最近消息 | `keep=("messages", 10)` | 当前：保留后 50% |
| 结构化摘要 | 内置 prompt 包含 SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS 四段 | 当前：500 字自由文本 |
| 保留决策理由 | 内置 prompt 要求记录 "rejected options and why" | 当前：不保留 |
| 保留文件变更 | 内置 ARTIFACTS 段追踪文件操作 | 当前：不追踪 |
| AI/Tool 配对保护 | `_find_safe_cutoff_point` 避免截断点落在 ToolMessage 中间 | 当前：无保护 |
| 替换而非追加 | `RemoveMessage(REMOVE_ALL_MESSAGES)` + 新摘要 | 当前：旧消息和新摘要并存 |

#### 极端场景补充：工具输出归档

对于极端大的工具输出（如编译日志超过 10000 字符），前置截断可能丢失重要信息。可增加可选的归档逻辑：

```python
# 可选：在 ToolOutputBudgetMiddleware 中增加归档能力
if self._exceeds_archive_threshold(msg.content, 40000):  # 约 10000 tokens
    archive_path = save_to_archive(msg.content, msg.name)
    msg = msg.model_copy(update={
        "content": f"[完整输出已归档到 {archive_path}]\n{self._truncate_with_summary(msg.content, budget)}"
    })
```

- Agent 可通过 `read_file` 重新加载归档文件
- 仅在极端场景触发，日常场景靠前置截断即可

### 3.3 Observation 遮蔽

### 3.3 Observation 遮蔽

参考 Claude Code 的 compaction 策略——"once a tool has been called deep in the message history, why would the agent need to see the raw result again?"

**实施方案：**

```python
def mask_observations(messages, keep_recent_n=3):
    """遮蔽早期工具调用中的详细输出"""
    for i, msg in enumerate(messages):
        if i < len(messages) - keep_recent_n:
            if msg.role == "tool":
                # 保留 50 字摘要 + 关键发现
                msg.content = compact_observation(msg.content)
            # 注意：当前使用 DashScope/Qwen，不支持 extended thinking
            # 如果未来切换到支持 thinking 的模型（如 Claude），
            # 可在此处添加: if hasattr(msg, 'thinking') and msg.thinking: msg.thinking = None
```

---

## 四、记忆注入优化（Memory Injection）

### 4.1 问题诊断

**当前实现**：
- 非 RAG 模式：`MEMORY.md` 全文拼入系统提示第 6 层
- RAG 模式：检索结果作为 assistant 消息追加到历史末尾（紧贴用户消息之前）
- mem0 模式：结构化记忆 + 时间衰减 + 置信度

**核心问题：**

1. **注入位置错误**：RAG 结果作为 assistant 消息追加到历史末尾（`agent.py:184-186`），LLM 可能将其视为自己说过的话而非外部知识
2. **全量注入无过滤**：`MEMORY.md` 包含大量过时/不相关信息全部注入
3. **记忆与上下文割裂**：系统提示中的记忆和 RAG 检索的记忆是两套独立系统
4. **无记忆相关性评估**：不根据当前用户消息筛选相关记忆

### 4.2 优化方案：统一记忆层 + 按需注入

#### 统一记忆检索接口

将 MEMORY.md 文件注入、mem0 检索、RAG 向量检索统一为单一接口：

```python
class UnifiedMemoryRetriever:
    """统一记忆检索层"""

    async def retrieve(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        """
        从所有记忆源检索，按相关性排序
        记忆源优先级：
        1. mem0 结构化记忆（有置信度 + 新鲜度）
        2. RAG 向量索引（LlamaIndex）
        3. 短期对话缓冲区（当前会话重要事实）
        """
```

#### 注入位置优化

**当前**（RAG 模式）：记忆作为 assistant 消息追加到历史末尾（紧贴当前用户消息之前）
```
[user] 帮我查一下天气
[assistant] [RAG: 用户住在北京，偏好简洁回复]  ← 伪装成自己的话
[assistant] 北京今天晴...
```

**优化后**：记忆作为 system 消息注入在当前用户消息之前
```
[system] [相关记忆] 用户住在北京（置信度: 0.9，来源: mem0）
         用户偏好简洁回复（置信度: 0.8，来源: mem0）
[user]   帮我查一下天气
```

这样做的好处：
- 明确标记为外部知识，不会与 Agent 自己的回复混淆
- 放在最新消息旁边，attention 权重最高（避免 lost-in-the-middle）
- 每次只注入与当前 query 相关的记忆，减少 token 浪费

#### 记忆蒸馏（Memory Distillation）

参考 Anthropic 的 structured note-taking 概念，将碎片化的记忆条目蒸馏为结构化摘要：

```python
async def distill_memories(self, user_id: str):
    """定期将碎片记忆蒸馏为结构化摘要"""
    all_memories = await self.mem0.get_all(user_id)

    # 按主题分组
    groups = group_by_topic(all_memories)

    for group in groups:
        # LLM 生成该主题的结构化摘要
        summary = await llm.summarize(
            f"将这些记忆条目合并为一个简洁的结构化摘要:\n{group}"
        )
        # 保存为蒸馏记忆，降低原始条目的置信度
        await self.mem0.save(user_id, summary, metadata={"type": "distilled"})
        for m in group:
            await self.mem0.update(m.id, confidence=0.1)  # 降权原始条目
```

---

## 五、工具上下文优化（Tool Context）

### 5.1 问题诊断

**当前实现**：
- 7 个核心工具 + 3 个 mem0 工具，全部每次加载
- 工具定义通过 LangChain `create_agent` 注入
- `SKILLS_SNAPSHOT.md` 包含当前 6 个技能的全量描述

**核心问题：**

1. **工具定义 token 开销大**：每个工具的 JSON Schema + 描述约 100-200 tokens，10 个工具占用可观 token
2. **无法按需裁剪**：即使用户只是闲聊，也加载 terminal、python_repl 等工具
3. **工具输出无截断**：一个 `terminal` 调用返回数千字，原样保留在消息历史中

### 5.2 优化方案：wrap_model_call 运行时工具过滤

参考 Manus 的"Mask, don't remove"策略和 Anthropic 的"minimal viable set of tools"。

通过 `wrap_model_call` 中间件，在**每次模型调用时**根据对话上下文动态过滤可用工具。工具定义始终完整（保护 KV-cache），但运行时只暴露相关子集。

#### 工具分类定义

```python
# 工具分类——供 wrap_model_call 中间件内部使用
TOOL_TIERS = {
    "always": ["read_file", "search_knowledge"],       # 基础工具，始终可用
    "coding": ["terminal", "python_repl", "write_file"], # 编码相关
    "web":    ["fetch_url"],                             # 网络访问
    "memory": ["save_memory", "search_memories"],        # 记忆管理
    "admin":  ["create_skill_version"],                  # 管理功能
}
```

#### wrap_model_call 中间件实现

```python
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest

class ContextAwareToolFilter(AgentMiddleware):
    """运行时工具过滤：根据对话上下文动态裁剪可用工具"""

    def wrap_model_call(self, request, handler):
        messages = request.messages
        tools = request.tools

        # 分析对话上下文，判断需要哪些工具
        needed_tiers = ["always"]  # 基础工具始终可用

        # 检查是否涉及编码任务
        if self._has_coding_context(messages):
            needed_tiers.append("coding")

        # 检查是否涉及网络请求
        if self._has_web_context(messages):
            needed_tiers.append("web")

        # 有记忆操作时包含记忆工具
        if self._has_memory_context(messages):
            needed_tiers.append("memory")

        # 收集允许的工具名
        allowed_names = set()
        for tier in needed_tiers:
            allowed_names.update(TOOL_TIERS.get(tier, []))

        # 过滤工具（不修改定义，只控制可见范围）
        filtered = [t for t in tools if t.name in allowed_names]
        request = request.override(tools=filtered)

        return handler(request)

    async def awrap_model_call(self, request, handler):
        # 异步版本，逻辑同上
        ...

    @staticmethod
    def _has_coding_context(messages) -> bool:
        """检查最近消息是否涉及编码任务"""
        recent = messages[-6:] if len(messages) > 6 else messages
        text = " ".join(m.content for m in recent if hasattr(m, "content") and m.content)
        coding_keywords = ["代码", "函数", "文件", "终端", "运行", "python", "terminal"]
        return any(kw in text.lower() for kw in coding_keywords)

    # _has_web_context, _has_memory_context 类似实现...
```

**关键设计决策**：

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具定义是否变动 | 不变，始终全量加载 | 保护 KV-cache（Manus 原则） |
| 过滤时机 | 运行时，每次模型调用前 | 能根据最新消息动态决策 |
| 上下文判断方式 | 关键词匹配（可扩展为意图识别） | 简单可靠，无需额外 LLM 调用 |
| 过滤失效时 | 允许 Agent 通过 search_knowledge 请求被过滤的工具 | 安全网 |

### 5.3 工具输出 Token 预算制

> 已整合到 Section 3.2 的 `ToolOutputBudgetMiddleware` 中实现。该中间件在 `SummarizationMiddleware` 之前运行，确保过大的工具输出在摘要触发前就被截断。

工具输出预算定义（被 `ToolOutputBudgetMiddleware` 引用）：

```python
TOOL_OUTPUT_BUDGETS = {
    "terminal":     2000,  # 终端输出最多 2000 tokens
    "python_repl":  1500,  # Python 输出最多 1500 tokens
    "fetch_url":    3000,  # 网页内容最多 3000 tokens
    "read_file":    2000,  # 文件内容最多 2000 tokens
    "search_knowledge": 1000,  # 检索结果最多 1000 tokens
}
```

---

## 六、任务状态管理（Task State）

### 6.1 问题诊断

**当前状态**：完全缺失。Agent 无法追踪多步任务进度，依赖对话历史中的隐式状态。

**导致的问题：**
- 长任务中 Agent 容易偏离目标（goal drift）
- 压缩后任务进度信息丢失
- 无法断点续做

### 6.2 优化方案：结构化任务状态 + Recitation

参考 Manus 的 recitation 技术和 Claude Code 的 TodoWrite。

> **实现方式**：`create_agent()` 的 `middleware` 和 `state_schema` **可以同时使用**（见第八章），因此任务状态有两种实现方式：
> - **方式 A（state_schema 嵌入，推荐）**：通过 `state_schema` 参数将 `TaskState` 嵌入 Agent 状态，类型安全、自动持久化（通过 checkpointer）
> - **方式 B（消息注入）**：将任务状态文本注入到用户消息之前，不依赖 state_schema，适用于不想修改 Agent 状态的场景
>
> 两种方式可以与 middleware（SummarizationMiddleware 等）同时使用，不冲突。

#### 核心数据结构

```python
class TaskState:
    """任务状态跟踪"""
    session_id: str
    goal: str                    # 用户原始目标
    steps: list[TaskStep]        # 步骤列表
    artifacts: list[str]         # 已创建/修改的文件
    decisions: list[Decision]    # 关键决策记录
    blockers: list[str]          # 当前阻塞项

class TaskStep:
    description: str
    status: "pending" | "in_progress" | "completed" | "blocked"
    result_summary: str | None   # 完成后的简要结果
```

#### 注入方式 A：state_schema 嵌入（推荐）

通过 `create_agent` 的 `state_schema` 参数，将 TaskState 直接嵌入 Agent 状态：

```python
from typing import TypedDict, NotRequired

class AgentCustomState(TypedDict):
    task_state: NotRequired[dict | None]   # 任务进度

# 使用时，TaskState 通过 state_schema 自动传入 Agent，
# 并通过 checkpointer 自动持久化，无需手动管理
```

**优势**：类型安全、自动持久化、与 middleware 不冲突。

#### 注入方式 B：Recitation 消息注入

借鉴 Manus 的"通过不断重写 todo.md 将目标推入模型注意力范围"：

```python
def build_task_context(task_state: TaskState) -> str:
    """生成任务状态上下文，注入到每次请求中"""
    if not task_state or not task_state.steps:
        return ""

    lines = ["## 当前任务状态"]
    lines.append(f"**目标**: {task_state.goal}")
    lines.append("")

    for step in task_state.steps:
        icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "blocked": "❌"}
        lines.append(f"{icon[step.status]} {step.description}")
        if step.result_summary:
            lines.append(f"   → {step.result_summary}")

    if task_state.blockers:
        lines.append(f"\n**阻塞项**: {'; '.join(task_state.blockers)}")

    return "\n".join(lines)
```

这个任务状态上下文**注入在当前用户消息之前**（不是系统提示中），这样：
1. 不影响系统提示的缓存
2. 位于上下文窗口的近端，attention 权重最高
3. 每次请求都更新（recitation），防止 goal drift

#### 触发机制

- **自动检测**：当用户消息包含"帮我做"、"创建"、"实现"等任务性动词时，自动初始化 TaskState
- **Agent 主动更新**：在工具调用完成后，Agent 更新步骤状态
- **压缩时保留**：对话压缩时，TaskState 作为独立结构保留，不参与摘要

---

## 七、外部知识优化（External Knowledge）

### 7.1 问题诊断

**当前实现**：
- RAG 索引仅覆盖 `memory/MEMORY.md` 一个文件
- 使用 LlamaIndex，256 字节分块，32 字节重叠
- 检索结果直接拼入上下文

**核心问题：**

1. **知识源单一**：只有 MEMORY.md，无法覆盖项目代码、文档、技能等
2. **无分层检索**：不区分"需要精确回答"和"需要模糊参考"
3. **检索结果无过滤**：top-k 结果可能包含不相关的片段

### 7.2 优化方案：Just-In-Time 知识检索

参考 Anthropic 的 hybrid 策略和 Claude Code 的"维护轻量级标识符，运行时动态加载数据"：

#### 扩展知识源

```
知识库结构:
├── memory/           # 用户记忆（已有）
├── workspace/        # Agent 人设和工作空间文档
├── docs/             # 项目文档
├── skills/           # 技能库
└── sessions/archive/ # 历史会话归档
```

#### Just-In-Time 检索模式

```python
async def jit_retrieve(query: str) -> list[KnowledgeItem]:
    """
    分层检索策略：
    1. 先从索引中获取轻量级引用（文件路径 + 标题 + 相关性分数）
    2. 只对 top-3 相关的文件加载完整内容
    3. 将内容按 token 预算裁剪后注入
    """
    # Step 1: 索引检索
    references = await index.search(query, top_k=10)

    # Step 2: 按相关性排序，只加载 top-3
    loaded = []
    for ref in references[:3]:
        content = await load_and_truncate(ref, budget=500)
        loaded.append(KnowledgeItem(
            source=ref.path,
            relevance=ref.score,
            content=content
        ))

    return loaded
```

---

## 八、LangChain 中间件与 LangGraph 架构复用

> 项目已安装 `langchain==1.2.12` 和 `langgraph==1.1.2`，但当前仅使用了 `create_agent` + `astream` 的最基础用法。以下列出可直接复用的高级能力。
>
> **关键发现**：`create_agent()` 的 `middleware` 和 `state_schema` 参数**可以同时使用**（源码 `langchain/agents/factory.py:335-340`，`_resolve_schema` 函数会合并所有 schema）。这意味着任务状态（TaskState）可以通过 `state_schema` 直接嵌入 Agent 状态，同时使用 middleware 实现压缩、限流等功能——两者不冲突。

### 8.0 架构选择

```
┌──────────────────────────────────────────────────────────────────┐
│                        架构选择决策树                              │
│                                                                   │
│  ┌─ 需要自定义图结构（条件分支、并行节点、人工介入）？               │
│  │   ├─ YES → 方案 B: 自定义 StateGraph                          │
│  │   │        完全自定义 Agent 图（放弃 create_agent 便捷性）      │
│  │   │        适用于复杂流程控制场景                               │
│  │   │                                                           │
│  │   └─ NO  → 方案 A: create_agent + middleware + state_schema   │
│  │            保留当前 create_agent 架构（推荐）                   │
│  │            通过中间件实现压缩/限流/工具过滤                      │
│  │            通过 state_schema 嵌入自定义状态（如 TaskState）     │
│  └──────────────────────────────────────────────────────────────────┘
```

| 维度 | 方案 A: create_agent + middleware + state_schema | 方案 B: 自定义 StateGraph |
|------|--------------------------------------------------|-------------------------|
| 改造成本 | **低**（在现有架构上添加参数） | **高**（需要重写 Agent 逻辑） |
| 内置压缩 | `SummarizationMiddleware`（配置即用） | 需自行实现压缩节点 |
| 工具限流 | `ToolCallLimitMiddleware`（配置即用） | 需自行实现计数逻辑 |
| 工具过滤 | `wrap_model_call`（运行时过滤） | 直接控制节点输入输出 |
| 自定义状态 | **支持**（通过 `state_schema` 参数） | 任意 `TypedDict` 状态 |
| 条件分支 | 不支持（线性流程） | 图边条件路由 |
| 人工介入 | 不支持 | `interrupt_before/after` |
| 状态持久化 | `checkpointer` 参数 | `checkpointer` 参数 |

> **推荐**：当前阶段选择**方案 A**。项目中短期需求（压缩、限流、工具过滤、任务状态）均可通过 middleware + state_schema 实现。仅当需要条件分支、并行节点、人工确认等复杂流程控制时，才考虑方案 B。

### 8.1 当前 LangChain 能力利用率分析

| LangChain 能力 | 当前状态 | 可优化场景 |
|---------------|---------|-----------|
| `create_agent` | ✅ 已使用（基础模式） | 可添加 middleware + state_schema 参数 |
| `SummarizationMiddleware` | ❌ 未使用 | 替代手动压缩逻辑 |
| `trim_messages` + `count_tokens_approximately` | ❌ 未使用 | 替代 `MAX_HISTORY_MESSAGES=20` 硬截断 |
| `ToolCallLimitMiddleware` | ❌ 未使用 | 防止工具调用死循环 |
| `wrap_model_call` 中间件 | ❌ 未使用 | 运行时动态过滤工具、切换模型 |
| `state_schema` 参数 | ❌ 未使用 | 嵌入自定义状态（TaskState 等） |
| `beforeAgent` 钩子 | ❌ 未使用 | 请求预处理、安全检查、上下文注入 |
| LangGraph `StateGraph` | ❌ 未使用（已安装） | 复杂多步任务状态机（方案 B） |
| LangGraph `checkpoint` | ❌ 未使用 | 会话持久化（替代手写 JSON 存储） |
| `init_chat_model` | ❌ 未使用 | 模型切换更简洁 |
| `BaseCallbackHandler` | ❌ 未使用 | 工具调用追踪、token 计数、性能监控 |

### 8.2 SummarizationMiddleware：替代手动压缩

**当前问题**：`api/compress.py` 手动实现了压缩逻辑（50% 切割 + 500 字摘要），质量差且不可配置。

**LangChain 内置方案**：

```python
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware

agent = create_agent(
    model=llm,
    tools=all_tools,
    system_prompt=system_prompt,
    middleware=[
        SummarizationMiddleware(
            model=lightweight_llm,         # 用轻量模型做摘要（如 qwen-turbo）
            trigger=("tokens", 4000),       # token 数超过 4000 时触发
            keep=("messages", 10),           # 始终保留最近 10 条消息
        )
    ],
    checkpointer=checkpointer,
)
```

**SummarizationMiddleware 内置的结构化摘要能力**（源码 `summarization.py` 的 `DEFAULT_SUMMARY_PROMPT`）：

中间件生成的摘要不是自由文本，而是包含四段结构的上下文提取结果：

| 摘要段 | 内容 | 解决的问题 |
|--------|------|-----------|
| **SESSION INTENT** | 用户的核心目标和请求 | 防止 goal drift（Manus recitation 思想） |
| **SUMMARY** | 关键上下文、决策、策略、**被排除的方案及其原因** | 保留决策理由，避免重复错误 |
| **ARTIFACTS** | 创建/修改/访问的文件路径及变更描述 | 防止文件变更信息丢失 |
| **NEXT STEPS** | 剩余待办任务 | 保持任务连续性 |

这意味着 Section 3.2 中自定义的"结构化对话摘要"（目标/步骤/决策/下一步）已经**被 SummarizationMiddleware 内置覆盖**，无需自行实现。

**收益**：
- **自动化**：不需要前端手动触发压缩按钮，中间件在每次调用前自动检查
- **精确触发**：基于实际 token 计数触发，而非消息条数
- **无缝衔接**：摘要替换旧消息（`RemoveMessage`），不膨胀消息列表
- **消息配对保护**：自动避免截断点落在 ToolMessage 中间（`_find_safe_cutoff_point`）
- **可配置**：`trigger` 和 `keep` 参数可灵活调整

**与当前方案的对比**：

```
当前: 前端点击"压缩" → API 调用 → LLM 生成 500 字自由文本摘要 → 替换前 50% 消息
优化: 每次请求前中间件自动检查 → token 超阈值 → 生成四段结构化摘要 → 替换旧消息
```

### 8.3 trim_messages：替代硬编码消息截断

**当前问题**：`agent.py` 中 `MAX_HISTORY_MESSAGES = 20` 硬编码截断，不考虑实际 token 消耗。

**LangChain 内置方案**：

```python
from langchain_core.messages.utils import trim_messages, count_tokens_approximately

def call_model(state: MessagesState):
    # 按 token 预算智能修剪，而非按消息条数硬截断
    messages = trim_messages(
        state["messages"],
        strategy="last",                          # 保留最新消息
        token_counter=count_tokens_approximately,  # 近似 token 计数
        max_tokens=6000,                           # 历史消息 token 上限
        start_on="human",                         # 从 human 消息开始（确保对话完整）
        end_on=("human", "tool"),                 # 在 human/tool 消息处结束（确保配对完整）
    )
    response = model.invoke(messages)
    return {"messages": [response]}
```

**收益**：
- **Token 精确控制**：按实际 token 数修剪，不浪费也不溢出
- **消息配对完整**：`start_on`/`end_on` 确保不会出现孤立的 tool_call 无对应的 tool_result
- **无需手动管理**：替代 `MAX_HISTORY_MESSAGES` 硬编码

### 8.4 wrap_model_call 中间件：运行时工具过滤

**当前问题**：所有工具全量加载，无法根据上下文动态调整。

**方案**：通过 `AgentMiddleware` 子类实现 `wrap_model_call` 方法，在每次模型调用时动态过滤工具。完整实现见 Section 5.2 的 `ContextAwareToolFilter`。

**对比手动工具分层的优势**：

| 维度 | 手动工具分层 | wrap_model_call 方案 |
|------|------------|---------------------|
| 工具定义 | 需要按场景硬编码加载不同工具集 | 工具定义不变，运行时动态过滤 |
| KV-cache | 不同工具集会破坏缓存 | 工具定义完整，过滤不改变序列化 |
| 灵活性 | 需要预定义所有场景 | 可根据对话上下文动态决策 |
| 上下文判断 | 需要额外的分类步骤 | 可直接访问当前消息列表 |
| 维护性 | 每增一个场景需改代码 | 只需调整中间件逻辑 |

### 8.5 ToolCallLimitMiddleware：防止工具死循环

**当前问题**：Agent 可能反复调用同一个工具（如反复执行 terminal 命令），浪费 token 和时间。

```python
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware

agent = create_agent(
    model=llm,
    tools=all_tools,
    middleware=[
        # 限制每个工具每次会话最多调用 10 次
        ToolCallLimitMiddleware(tool_name="terminal", run_limit=10),
        ToolCallLimitMiddleware(tool_name="python_repl", run_limit=5),
    ],
)
```

### 8.6 LangGraph StateGraph：方案 B 的完全替代方案

> **适用场景**：当项目需要条件分支（如根据任务类型走不同处理路径）、并行执行多个工具、人工确认断点等复杂流程控制时。需要**完全重写**当前 `agent.py` 的 Agent 构建逻辑。

选择方案 B 意味着不再使用 `create_agent`，需要自行构建 LangGraph StateGraph。压缩、限流、修剪需要在图节点中自行实现，但获得了完全的流程控制自由。

```python
from langgraph.graph import StateGraph, START, END, MessagesState

class AgentState(MessagesState):
    task_state: dict | None     # 任务进度
    context_type: str           # 当前上下文类型
    tool_usage_count: dict      # 工具使用计数

# 构建 Agent 图
builder = StateGraph(AgentState)
# ... 节点和边的定义（省略，详见 8.6 节）...
agent = builder.compile(checkpointer=checkpointer)
```

**方案 B 的额外代价**：
- 需要自行实现压缩逻辑（方案 A 的 `SummarizationMiddleware` 不可用）
- 需要自行实现工具限流（方案 A 的 `ToolCallLimitMiddleware` 不可用）
- 需要自行实现消息修剪（方案 A 的 `trim_messages` 仍可复用）
- 需要重写 `agent.py` 的 Agent 构建逻辑

**方案 B 的独有收益**：
- **可视化流程**：LangGraph 支持生成 Agent 流程图，便于调试
- **条件分支**：根据任务类型走不同路径
- **人工介入**：支持 `interrupt_before`/`interrupt_after` 断点

### 8.7 方案 A 实施方案：middleware + state_schema

> **推荐方案**：在当前 `create_agent()` 架构上添加 middleware 和 state_schema 参数，渐进式升级。所有中间件通过列表串联，按顺序执行。

#### 中间件执行链路

```
用户消息到达
  ↓
[ToolOutputBudgetMiddleware.before_model]    ← 第 1 层：前置截断（Section 3.2）
  │  截断超过预算的工具输出，降低 token 总量
  ↓
[SummarizationMiddleware.before_model]       ← 第 2 层：自动摘要（Section 3.2）
  │  token 超阈值时触发，生成结构化摘要替换旧消息
  ↓
[ContextAwareToolFilter.wrap_model_call]     ← 第 3 层：工具过滤（Section 5.2）
  │  根据对话上下文动态裁剪可用工具
  ↓
[ToolCallLimitMiddleware]                    ← 第 4 层：工具限流（Section 8.5）
  │  防止同一工具被反复调用
  ↓
[模型调用]
```

#### 综合代码

```python
from typing import TypedDict, NotRequired
from langchain.agents import create_agent
from langchain.agents.middleware import (
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from langgraph.checkpoint.memory import InMemorySaver

# 自定义状态——通过 state_schema 嵌入 Agent 状态
# _resolve_schema 会自动将此与 middleware 的 state_schema 合并
class AgentCustomState(TypedDict):
    task_state: NotRequired[dict | None]   # 任务进度（见第六章）
    context_type: NotRequired[str]         # 当前上下文类型

agent = create_agent(
    model=llm,
    tools=all_tools,
    system_prompt=build_stable_prefix(),     # 稳定前缀（Cache Zone 1+2）

    middleware=[
        # ── 第 1 层：工具输出截断（Section 3.2）──
        # 在摘要触发前先截断大输出，延缓摘要触发
        ToolOutputBudgetMiddleware(),

        # ── 第 2 层：自动摘要（Section 3.2）──
        # 替代 api/compress.py 的手动压缩
        SummarizationMiddleware(
            model=lightweight_llm,
            trigger=("tokens", 8000),    # 超过 8000 tokens 时触发
            keep=("messages", 10),        # 保留最近 10 条
        ),

        # ── 第 3 层：运行时工具过滤（Section 5.2）──
        # 工具定义不变（保护 KV-cache），运行时动态裁剪
        ContextAwareToolFilter(),

        # ── 第 4 层：工具调用限制（Section 8.5）──
        # 防止同一工具被反复调用
        ToolCallLimitMiddleware(tool_name="terminal", run_limit=10),
        ToolCallLimitMiddleware(tool_name="python_repl", run_limit=5),
    ],

    state_schema=AgentCustomState,        # 自定义状态（与 middleware 合并）
    checkpointer=InMemorySaver(),          # 会话状态持久化
)
```

**方案 A 实施步骤**（渐进式，每步独立可验证）：

```
Step 1: 添加 SummarizationMiddleware
  → 替代 api/compress.py 的手动压缩
  → 移除 agent.py 的 MAX_HISTORY_MESSAGES 硬截断
  → 收益：自动化压缩、结构化摘要、消息配对保护

Step 2: 添加 ToolOutputBudgetMiddleware
  → 在 SummarizationMiddleware 前方挂载
  → 收益：前置截断降低 token 总量，延缓摘要触发

Step 3: 添加 ContextAwareToolFilter（wrap_model_call）
  → 实现运行时工具过滤
  → 收益：闲聊时不需要暴露 terminal/python_repl 等编码工具

Step 4: 添加 ToolCallLimitMiddleware
  → 防止工具死循环
  → 收益：token 不再浪费在重复调用上

Step 5: 添加 state_schema 嵌入 TaskState
  → 实现任务状态管理（见第六章）
  → 收益：任务状态类型安全、自动持久化
```

### 8.8 方案 B 实施方案：自定义 StateGraph

此方案的完整架构见 8.6 节代码示例。

**方案 B 实施步骤**：

```
Step 1: 定义 AgentState + 构建基础 StateGraph（call_model + execute_tools 循环）
Step 2: 添加 analyze_context + inject_memory 节点
Step 3: 添加 update_task 节点 + 条件边
Step 4: 在 call_model 节点中集成 trim_messages + 压缩逻辑
Step 5: 添加 checkpointer 持久化
```

> **方案切换成本**：如果从方案 A 切换到方案 B，需要重写 `agent.py` 的 Agent 构建逻辑，但 `trim_messages`、工具定义、prompt_builder 等模块可以复用。

---

## 九、额外优化方向

以下方向不包含在用户初始列表中，但基于三大框架的实践和 LangChain 生态能力，对项目有显著价值。

### 9.1 子 Agent 上下文隔离

参考 Anthropic 的 sub-agent 架构和 Claude Code 的 Agent 工具：

**场景**：当主 Agent 需要执行大量探索性操作（如代码分析、文件搜索）时，这些操作会产生大量工具调用和输出，污染主 Agent 的上下文窗口。

**方案**：

```python
class SubAgent:
    """子 Agent：独立上下文窗口，完成后返回精简结果"""
    def __init__(self, task: str, tools: list):
        self.context = []  # 独立的上下文
        self.tools = tools

    async def run(self) -> SubAgentResult:
        """执行任务，返回精简结果"""
        # ... 执行 N 轮工具调用 ...
        return SubAgentResult(
            summary="...",       # 1000-2000 tokens 的精简结果
            artifacts=[...],     # 产生的文件
            confidence=0.9       # 结果置信度
        )
```

**适用场景**：
- 大范围代码搜索和分析
- 复杂的文件生成/修改
- 需要多轮工具调用的探索性任务

**收益**：主 Agent 上下文只增加 1000-2000 tokens 的摘要，而非数万 tokens 的完整工具调用链。

### 9.2 文件系统作为上下文（Filesystem-as-Context）

参考 Manus 的核心设计——"将文件系统视为终极上下文：无限大小、天然持久、Agent 可直接操作"。

**当前项目已有基础**：Agent 已经有 `read_file`、`write_file` 工具，`memory/` 目录已经作为外部存储使用。

**优化方向**：
- 工具输出自动归档到 `sessions/archive/` 目录
- 压缩时用文件引用替代原文（已有基础，需完善）
- Agent 的"工作笔记"持久化到文件系统而非仅存于上下文

### 9.3 上下文腐烂监控（Context Rot Detection）

参考 Anthropic 对 context rot 的研究："随着 token 数增加，模型准确检索信息的能力下降"。

**方案**：添加上下文健康度监控：

```python
class ContextHealthMonitor:
    def compute_health_score(self, messages: list) -> float:
        """
        返回 0-1 的健康度分数
        考虑因素：
        - 总 token 数 / 模型上下文窗口大小
        - 工具输出占比（越高越差）
        - 最近 N 条消息的信息密度
        - 重复/冗余内容比例
        """
        ...

    def should_compress(self, messages: list) -> bool:
        """当健康度 < 0.4 时建议压缩"""
        return self.compute_health_score(messages) < 0.4
```

### 9.4 保留错误信息（Keep the Wrong Stuff In）

参考 Manus 的核心洞察："擦除失败就移除了证据，没有证据模型就无法适应"。

**当前问题**：压缩时可能将失败的工具调用和错误信息一起压缩掉。

**优化**：在压缩摘要中专门保留"已尝试但失败的方法"：

```markdown
### 已排除的方案
- 方案 A：尝试直接修改 config.json 但格式不兼容
- 工具 X：terminal 命令 `xxx` 因权限不足失败
```

### 9.5 避免上下文模式化（Don't Get Few-Shotted）

参考 Manus 的观察："如果上下文中充满相似的 action-observation 对，模型会倾向于复制那个模式，即使已不再最优"。

**识别模式化**：
```python
def detect_pattern_rut(messages: list, window=5) -> bool:
    """检测最近 N 轮是否有重复模式"""
    recent_actions = extract_tool_calls(messages[-window:])
    return len(set(a.tool_name for a in recent_actions)) == 1
```

**缓解策略**：当检测到模式化时，注入提示鼓励 Agent 换一种方式：
```
[系统提示] 检测到你已经连续 4 次使用相同工具。考虑换一种方法或直接向用户确认。
```

---

## 十、优化优先级矩阵

按 **ROI（投入产出比）** 排序：

| 优先级 | 优化项 | 预估收益 | 实现难度 | 影响模块 |
|-------|--------|---------|---------|---------|
| P0 | SummarizationMiddleware 替代手动压缩 | 自动压缩 + 四段结构化摘要 + 消息配对保护 | **极低**（langchain 内置，配置即用） | 对话历史 |
| P0 | ToolOutputBudgetMiddleware 前置截断 | 大输出不浪费 token，延缓摘要触发 | 低（独立中间件，~50 行） | 对话历史 |
| P0 | 系统提示缓存前缀设计 | 系统提示前缀稳定，缓存命中率提升 | 中 | 系统提示 |
| P0 | 技能元数据 Schema + 分层注入 | 精细化技能管理 + Agent Skills 标准兼容 | 中 | 系统提示/工具 |
| P1 | ContextAwareToolFilter 运行时工具过滤 | 动态裁剪工具（不破坏 KV-cache） | 低（AgentMiddleware 子类） | 工具上下文 |
| P1 | ToolCallLimitMiddleware 防死循环 | 防止 token 浪费 | **极低**（langchain 内置） | 工具上下文 |
| P1 | 记忆注入位置优化 | 记忆利用率提升 | 低 | 记忆注入 |
| P1 | 任务状态管理（state_schema 嵌入） | 长任务不偏离目标 | 中 | 任务状态 |
| P2 | LangGraph StateGraph 重写（方案 B，替代 create_agent） | 条件分支 + 自定义流程 + 人工介入 | 高 | 架构级 |
| P2 | Just-In-Time 知识检索 | 知识相关性提升 | 高 | 外部知识 |
| P2 | Observation 遮蔽 | 早期工具输出不浪费 token | 中 | 对话历史 |
| P2 | trim_messages 替代硬编码截断 | Token 精确控制 | **极低**（langchain 内置） | 对话历史 |
| P3 | 子 Agent 隔离 | 复杂任务不污染上下文 | 高 | 架构级 |
| P3 | 上下文健康度监控 | 智能触发压缩 | 中 | 全局 |
| P3 | 保留错误信息 | 避免重复错误 | 低 | 压缩策略 |
| P3 | 防模式化检测 | 提升任务完成率 | 中 | 全局 |

---

## 十一、参考框架对比

| 设计原则 | Claude Code (Anthropic) | Manus | LangChain v1 | mini-OpenClaw 现状 |
|---------|------------------------|-------|-------------|-------------------|
| **缓存策略** | 前缀稳定 + cache breakpoint | KV-cache hit rate #1 指标 | — | 无缓存设计 |
| **压缩策略** | Compaction + 保留最近文件 | 文件系统作为上下文 | SummarizationMiddleware（内置） | 手动 50% 切割 |
| **消息修剪** | — | — | trim_messages + count_tokens（内置） | MAX_HISTORY_MESSAGES=20 硬编码 |
| **工具管理** | 最小可行集 + 按需探索 | Mask don't remove + logits masking | wrap_model_call 中间件（运行时过滤） | 全量加载 |
| **工具限流** | — | — | ToolCallLimitMiddleware（内置） | 无 |
| **记忆管理** | Structured note-taking | 文件系统记忆 | SummarizationMiddleware + Checkpointer | mem0 + RAG |
| **任务追踪** | TodoWrite 工具 | Recitation（重写 todo.md） | StateGraph 节点状态 | 无 |
| **Agent 架构** | Sub-agent（独立上下文） | — | 方案A: middleware + state_schema / 方案B: StateGraph | create_agent 线性流 |
| **错误处理** | 保留错误上下文 | Keep the wrong stuff in | — | 可能被压缩丢失 |
| **检索策略** | Hybrid（预加载 + JIT） | 文件引用 + 按需读取 | — | RAG top-k |
| **权限控制** | — | — | beforeAgent 钩子 + context_schema | 无 |
| **技能标准** | Agent Skills (agentskills.io) | — | — | 自定义 name+description |

---

## 十二、总结

mini-OpenClaw 的上下文工程有良好的基础（6 层系统提示、mem0 记忆、RAG 检索），但在以下方面有显著优化空间：

### 即刻可落地的低成本优化

1. **LangChain 中间件复用**（最被低估的优化）：项目已安装 `langchain==1.2.12` + `langgraph==1.1.2`，但 `SummarizationMiddleware`、`trim_messages`、`wrap_model_call`、`ToolCallLimitMiddleware` 等内置能力完全未使用。这些中间件可替代当前手动开发的压缩、截断、工具过滤功能，改造成本低。

2. **缓存效率**：当前每次请求全量重建系统提示，无缓存前缀设计。通过三段式缓存前缀设计可显著提升缓存命中率

3. **技能元数据精细化管理**：当前 `skills_scanner.py` 仅解析 `name`、`description`、`location` 三个字段，无法支持复杂技能的权限控制、触发条件等元数据。通过遵循 Agent Skills 标准的 `metadata` 扩展 + SkillRegistry 注册表，实现分级注入（隐藏/索引/按需/预加载），同时保持与外部技能生态的兼容性。

### 中期优化

4. **压缩质量**：当前简单的 50% 切割 + 500 字摘要。通过 `SummarizationMiddleware`（自动化）+ 工具输出卸载/摘要（补充中间件能力）可大幅提升长对话质量

5. **Token 效率**：工具输出、技能快照等占大量 token 但价值有限。通过卸载、遮蔽、瘦身可减少显著 token 消耗

6. **任务连续性**：缺少任务状态管理导致长任务偏离目标。通过 `state_schema` 嵌入 TaskState（类型安全、持久化）或 Recitation 消息注入均可实现

7. **记忆利用率**：注入位置和方式影响记忆的实际效果。统一记忆层 + 按需注入可显著提升

### 架构级决策点

8. **方案选择**：`create_agent` + middleware + `state_schema`（方案 A）与自定义 StateGraph（方案 B）是两种架构选择。**方案 A 是推荐路径**——middleware 和 state_schema 可以同时使用（源码 `_resolve_schema` 会合并），因此压缩/限流/工具过滤/任务状态都可以在方案 A 内解决。仅当需要条件分支、并行节点、人工介入等复杂流程控制时，才需要方案 B

9. **子 Agent 上下文隔离**：复杂探索性任务不污染主上下文

10. **上下文健康度监控 + 防模式化检测**：智能触发压缩，避免陷入重复行为模式

**最重要的发现**：

- 项目已安装但未使用的 LangChain 中间件能力（`SummarizationMiddleware`、`trim_messages`、`wrap_model_call`）是 ROI 最高的优化
- `middleware` 和 `state_schema` **可以同时使用**，这意味着任务状态管理（TaskState）可以通过 `state_schema` 直接嵌入，不需要退回到消息注入方案
- 前期报告错误地声称两者互斥，导致方案设计过于保守——实际上方案 A 可以覆盖几乎所有需求

---

## 参考资料

- [Anthropic - Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Manus - Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [LangChain - Context Management for Deep Agents](https://blog.langchain.com/context-management-for-deepagents/)
- [LangChain v1 - Agent Middleware (SummarizationMiddleware, wrap_model_call, ToolCallLimitMiddleware)](https://docs.langchain.com/oss/python/releases/langchain-v1)
- [LangGraph - StateGraph and Memory Management](https://docs.langchain.com/oss/python/langgraph/quickstart)
- [LangChain - Context Engineering (Runtime Tool Filtering)](https://docs.langchain.com/oss/python/langchain/context-engineering)
- [Agent Skills Standard (agentskills.io)](https://agentskills.io) — Anthropic 制定的 SKILL.md 格式标准，定义 `name`/`description`/`metadata` 等标准字段
- [LangChain `create_agent` 源码](https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain/agents/factory.py) — `middleware` 与 `state_schema` 通过 `_resolve_schema` 合并，可同时使用（第 335-340 行）
- [PromptBuilder - Context Engineering for AI Agents (2025): Practical Guide](https://promptbuilder.cc/blog/context-engineering-agents-guide-2025)
- [GitHub Blog - Agentic Primitives and Context Engineering](https://github.blog/ai-and-ml/github-copilot/how-to-build-reliable-ai-workflows-with-agentic-primitives-and-context-engineering/)
