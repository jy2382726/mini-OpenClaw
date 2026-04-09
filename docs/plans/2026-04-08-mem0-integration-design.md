# mem0 集成方案：增强跨会话长期记忆管理

## 背景

当前 mini-OpenClaw 的长期记忆系统存在以下局限：
1. 所有记忆堆在单一 `MEMORY.md` 文件中，无分类
2. 依赖 Agent 手动通过 `terminal` 工具写入，无自动提取
3. 无去重/过期机制，记忆只增不减
4. 每次变化全量重建 LlamaIndex 向量索引
5. 记忆只是扁平文本，缺乏 Why（为什么记住）和 How to apply（何时应用）的上下文推理能力
6. 无时间轴管理，无法感知记忆是否过时
7. 无碎片整理机制，重复和冲突的记忆无法自动处理
8. 每轮对话都触发记忆写入，缺乏批次化截流管理

本方案参考 Claude Code 的记忆管理架构，引入 mem0 作为智能记忆层，与现有系统并行运行、可切换，破坏性最小。

---

## 核心设计原则

- **适配器/策略模式**：通过 `MemoryRetriever` 抽象层封装三种检索策略（legacy / mem0 / hybrid）
- **不修改现有文件行为**：`memory_indexer.py` 和 `prompt_builder.py` 零改动
- **配置驱动切换**：`config.json` 新增 `mem0` 配置块，默认关闭，启用时零开销
- **复用现有 API**：mem0 的 LLM 和 embedding 复用项目已有的 DashScope 配置
- **Qdrant 本地模式**：无需 Docker 或外部服务，数据持久化到 `backend/mem0_data/`
- **结构化记忆**：每条记忆包含主体 + Why + How to apply，而非扁平文本标签
- **防御性读取**：记忆是时间快照而非当前事实，使用前必须验证
- **离线碎片整理**：定期对记忆做去重、合并、冲突解决
- **智能截流提取**：批次化管理对话内容，不逐轮触发记忆写入

---

## 新增文件（7 个后端 + 3 个前端）

| 文件 | 职责 |
|------|------|
| `backend/graph/mem0_manager.py` | mem0 核心管理器单例：封装 mem0 Memory 实例的创建、配置、生命周期。提供 `add()`, `search()`, `get_all()`, `delete()` 方法，内部处理 4 种记忆类型分类 |
| `backend/graph/memory_retriever.py` | 统一检索接口：`MemoryRetriever` 抽象基类 + `LegacyRetriever` / `Mem0Retriever` / `HybridRetriever` 三个实现，工厂方法 `get_retriever()`。包含防御性读取逻辑 |
| `backend/graph/memory_consolidator.py` | **离线记忆整合器**：对记忆做去重、合并、冲突解决、过期清理。支持手动触发和定时自动触发 |
| `backend/graph/memory_buffer.py` | **对话缓冲区 + 智能截流器**：累积对话轮次，判断写入时机，批次化提交给 mem0_manager |
| `backend/api/mem0_api.py` | 记忆管理 REST API：列表/删除/导入/整合/状态检查 |
| `backend/tools/mem0_tool.py` | Agent 可调用工具：`save_memory`(显式保存，带 Why & How to apply) 和 `search_memories`(主动搜索) |
| `frontend/src/lib/mem0Api.ts` | 前端 API 客户端 |
| `frontend/src/components/chat/Mem0Card.tsx` | mem0 检索结果卡片组件（带类型标签、时间戳、置信度，区别于现有紫色 RetrievalCard） |
| `frontend/src/app/settings/memory/page.tsx` | 记忆管理设置页面 |

---

## 设计模式一：结构化记忆（Why & How to Apply）

### 问题

当前系统的记忆只是扁平文本片段（如"用户喜欢更加严谨的回答"），AI 缺乏判断这条记忆是否适用于当前场景的推理依据。当遇到边缘情况时，AI 要么盲目遵循，要么忽略。

### 方案

每条记忆采用**结构化三元组**存储，不只是分类标签，而是包含完整的上下文推理信息：

```
┌─────────────────────────────────────────────────┐
│ 记忆主体（fact/rule）                             │
│   "用户偏好简洁直接的代码风格，不希望过多注释"       │
├─────────────────────────────────────────────────┤
│ Why: 为什么记住这条                               │
│   "用户在代码审查中多次要求删除冗余注释，明确表示   │
│    '我能读懂代码，不需要每行都解释'"               │
├─────────────────────────────────────────────────┤
│ How to apply: 何时应用                            │
│   "当为新函数添加注释时适用；当修改他人代码时不     │
│    适用——保持原有注释风格"                         │
└─────────────────────────────────────────────────┘
```

### 存储结构

mem0 的每条记忆存储为以下元数据结构：

```python
memory_metadata = {
    "memory_type": "feedback",          # user / feedback / project / reference
    "why": "用户在代码审查中多次要求删除冗余注释...",  # 为什么记住
    "how_to_apply": "当为新函数添加注释时适用...",     # 何时应用
    "source_session_id": "abc123",      # 来源会话
    "created_at": "2026-04-08T10:30:00Z",
    "last_verified_at": None,           # 上次验证时间（防御性读取用）
    "confidence": 1.0,                  # 置信度 0-1
}
```

### 提取 Prompt 设计

事实提取时，要求 LLM 输出结构化结果：

```
从对话中提取值得长期记住的事实。每条记忆必须包含：

1. fact: 值得记住的事实或规则（一句话概括）
2. type: 分类标签
   - user: 用户偏好、习惯、个人信息
   - feedback: 用户对AI回复的正向/负向反馈
   - project: 项目相关的技术上下文（架构、技术栈、文件位置）
   - reference: 外部引用（文档链接、API地址、参考资料）
3. why: 为什么这条记忆值得保留（具体的事件或对话上下文）
4. how_to_apply: 在什么场景下应该应用这条记忆

排除：寒暄、临时性对话、纯情绪表达。
冲突处理：如果新提取的事实与已知记忆矛盾，在 why 中说明。

返回 JSON 数组。
```

### Agent 工具的显式保存

`save_memory` 工具接收结构化输入：

```python
# Agent 调用示例
save_memory(
    fact="项目使用 DashScope Qwen 作为主 LLM",
    memory_type="project",
    why="从 config.json 确认的配置，用户在第一个会话中配置",
    how_to_apply="当讨论 LLM 相关功能或排查模型调用问题时适用"
)
```

### 检索时的上下文注入

当记忆被检索到时，注入到上下文的格式包含完整结构，让 Agent 能推理是否应用：

```
[智能记忆检索结果]
--- 片段 1 [feedback, 置信度: 0.95, 记录于: 2026-04-05] ---
事实: 用户偏好简洁直接的代码风格，不希望过多注释
原因: 用户在代码审查中多次要求删除冗余注释
适用场景: 当为新函数添加注释时适用；修改他人代码时不适用
⚠️ 此记忆已存在 3 天，使用前请验证是否仍然有效
---
```

---

## 设计模式二：防御性读取（Defensive Reading）

### 问题

记忆是某个时间点的快照，不一定是当前的真实状态。例如：
- 记忆记录"项目使用 DeepSeek 模型"，但用户已切换到 DashScope
- 记忆引用某个文件路径，但文件已被删除或移动
- 记忆记录的 API 端点已变更

如果 AI 盲目信任记忆，会产生错误行为。

### 方案：三层防御机制

#### 第一层：时间轴标注

每次检索返回的记忆都附带时间信息，并由 `memory_retriever.py` 计算新鲜度等级：

| 存活时间 | 新鲜度等级 | 注入到上下文的提示 |
|----------|-----------|-------------------|
| < 24 小时 | `fresh` | 无提示 |
| 1-7 天 | `recent` | "此记忆已存在 N 天，使用前请验证是否仍然有效" |
| 7-30 天 | `aging` | "⚠️ 此记忆已存在 N 天，很可能已过时，使用前必须验证" |
| > 30 天 | `stale` | "🔴 此记忆已超过 30 天，仅作参考。使用前必须验证当前状态" |

实现位置：`Mem0Retriever.format_context()` 方法中，根据 `created_at` 和当前时间计算等级。

#### 第二层：引用验证提示

当记忆中包含可验证的引用（文件路径、函数名、API 端点）时，在上下文中附加验证建议：

```
[智能记忆检索结果]
--- 片段 1 [project, 记录于: 2026-03-20] ---
事实: 后端入口文件位于 backend/app.py
原因: 项目初始化时确认的架构
适用场景: 当需要修改后端启动逻辑或添加新路由时
⚠️ 此记忆已超过 19 天。记忆中引用了文件路径 "backend/app.py"，
请使用 read_file 工具验证该路径是否仍然存在后再依赖此信息。
---
```

实现方式：在 `format_context()` 中用正则提取记忆文本中的文件路径（`[\w/]+\.\w+`）和 URL 模式（`https?://...`），如存在则附加验证提示。

#### 第三层：置信度衰减

每条记忆有 `confidence` 字段（0-1 初始值 1.0），随时间自然衰减：

```python
def calculate_confidence(created_at: str, last_verified_at: str | None) -> float:
    """计算记忆的当前置信度"""
    base_confidence = 1.0
    age_days = (now - created_at).days

    # 时间衰减：每天降低 0.02，最低 0.3
    time_decay = max(0.3, base_confidence - age_days * 0.02)

    # 验证加分：如果曾被验证过，提升置信度
    if last_verified_at:
        verified_age = (now - last_verified_at).days
        verify_bonus = max(0, 0.3 - verified_age * 0.02)
        time_decay = min(1.0, time_decay + verify_bonus)

    return round(time_decay, 2)
```

低置信度的记忆（< 0.5）在检索排序中降权，低于阈值（< 0.3）的默认不返回（除非用户显式查询历史记忆）。

### 验证回写机制

当 Agent 在对话中通过工具读取文件或调用 API 后，如果发现某条记忆仍然有效，通过 `verify_memory` 工具更新 `last_verified_at`：

```python
# Agent 可调用
verify_memory(memory_id="mem_abc123")
# 内部：更新 metadata.last_verified_at = now(), confidence = min(1.0, confidence + 0.3)
```

---

## 设计模式三：离线记忆整合（Memory Consolidation）

### 问题

长期运行后，记忆库会出现：
- **重复记忆**：同一件事在不同对话中被多次记录（"用户喜欢简洁的回复"、"用户偏好简洁风格"、"不要写冗长解释"）
- **冲突记忆**：旧记忆说"使用 DeepSeek"，新记忆说"使用 DashScope"
- **碎片记忆**：多条小记忆本应合并为一条完整的上下文
- **过期记忆**：项目已重构，旧的文件结构记忆已无用

### 方案：四阶段整合管道

`memory_consolidator.py` 实现离线整合，不阻塞对话流程。

#### 阶段 1：去重（Deduplication）

```python
def deduplicate(memories: list[Memory]) -> list[MemoryGroup]:
    """检测语义重复的记忆，分组待合并"""
    # 1. 对所有记忆做向量嵌入
    # 2. 计算两两余弦相似度
    # 3. 相似度 > 0.85 的分入同一组
    # 4. 每组保留最完整的版本（内容最长的作为主记忆）
    # 5. 其余标记为 "待合并"
```

#### 阶段 2：合并（Merge）

```python
def merge_group(group: MemoryGroup) -> Memory:
    """将一组重复/相关记忆合并为一条"""
    # 使用 LLM 做智能合并：
    # 输入：组内所有记忆的 fact + why + how_to_apply
    # 输出：一条合并后的记忆，保留最完整的 why 和最全面的 how_to_apply
    #
    # LLM Prompt：
    # "以下多条记忆描述了同一件事的不同方面，请合并为一条完整的记忆。
    #  保留最详细的原因说明，合并所有适用场景。
    #  如有矛盾，以时间最新的为准并在 why 中说明变化。"
```

#### 阶段 3：冲突解决（Conflict Resolution）

```python
def resolve_conflicts(memories: list[Memory]) -> list[ConflictAction]:
    """检测互相矛盾的记忆"""
    # 检测方式：用 LLM 判断两条记忆是否在说矛盾的事实
    # 筛选条件：同类（memory_type 相同）且语义相关但不一致
    #
    # 解决策略：
    # 1. 时间优先：保留更新的，标记旧的为 deprecated
    # 2. 自动合并到新记忆的 why 中："之前曾认为 X，后改为 Y"
    # 3. 无法自动判断时，生成冲突报告，等待用户在管理界面手动解决
```

#### 阶段 4：过期清理（Expiration）

```python
def expire_stale(memories: list[Memory]) -> list[Memory]:
    """清理确认过期的记忆"""
    # 规则：
    # - confidence < 0.2 且超过 60 天未被验证 → 标记 deprecated
    # - memory_type == "project" 且引用的文件路径不存在 → 标记 deprecated
    # - 用户显式删除 → 直接删除
    #
    # deprecated 记忆不会出现在检索结果中，但保留在数据库中
    # 可在管理界面查看和恢复
```

### 整合触发时机

| 触发方式 | 条件 | 说明 |
|----------|------|------|
| 自动定时 | 每天首次启动时检查 | 如果记忆总数 > 50 且上次整合 > 24 小时 |
| 阈值触发 | 未整合的新记忆 > 20 条 | 防止碎片累积过多 |
| 手动触发 | 用户在管理页面点击"整理记忆" | 即时执行 |
| API 触发 | `POST /api/mem0/consolidate` | 外部脚本触发 |

### 新增文件

`backend/graph/memory_consolidator.py` — 整合器，核心接口：

```python
class MemoryConsolidator:
    def __init__(self, mem0_manager: Mem0Manager):
        self._mgr = mem0_manager

    def run_consolidation(self) -> ConsolidationReport:
        """执行完整的整合管道，返回报告"""
        all_memories = self._mgr.get_all()
        report = ConsolidationReport()

        # 阶段 1：去重
        groups = self.deduplicate(all_memories)
        report.duplicates_found = len(groups)

        # 阶段 2：合并
        for group in groups:
            merged = self.merge_group(group)
            self._mgr.replace_group(group, merged)
        report.merged = len(groups)

        # 阶段 3：冲突检测
        conflicts = self.detect_conflicts(self._mgr.get_all())
        resolved = self.auto_resolve(conflicts)
        report.conflicts_resolved = len(resolved)
        report.conflicts_pending = len(conflicts) - len(resolved)

        # 阶段 4：过期清理
        expired = self.expire_stale(self._mgr.get_all())
        report.expired = len(expired)

        return report

    def get_consolidation_preview(self) -> ConsolidationPreview:
        """预览整合结果，不实际执行（供管理页面使用）"""
```

---

## 设计模式四：智能截流提取（Smart Throttled Extraction）

### 问题

如果每轮对话都调用 mem0 的 `add()` 做 LLM 事实提取，会导致：
- API 成本倍增（每轮额外一次 LLM 调用）
- 响应延迟增加（提取需要时间）
- 记忆碎片化（单轮对话的信息量不足以判断重要性）
- 噪音记忆增多（寒暄、临时指令被当作事实记录）

### 方案：对话缓冲区 + 多级触发机制

`memory_buffer.py` 实现对话缓冲和截流控制。

#### 缓冲区设计

```python
class MemoryBuffer:
    """对话缓冲区：累积对话轮次，智能判断写入时机"""

    def __init__(self):
        self._buffer: list[ConversationTurn] = []  # 待处理的对话轮次
        self._last_flush_time: float = time.time()
        self._session_turn_count: int = 0

    def add_turn(self, user_msg: str, assistant_msg: str, session_id: str):
        """添加一轮对话到缓冲区"""
        self._buffer.append(ConversationTurn(
            user_message=user_msg,
            assistant_message=assistant_msg,
            session_id=session_id,
            timestamp=time.time(),
        ))
        self._session_turn_count += 1

    def should_flush(self) -> bool:
        """判断是否应该触发批次提取"""
        # 检查各级触发条件
        ...

    def flush(self) -> list[ConversationTurn]:
        """提取并清空缓冲区，返回待处理的对话批次"""
        turns = self._buffer
        self._buffer = []
        self._last_flush_time = time.time()
        return turns
```

#### 多级触发条件

| 触发级别 | 条件 | 说明 |
|----------|------|------|
| **立即触发** | 用户明确指令 | "记住这个"、"以后都这样"、"不要这样做"、"记住：..." |
| **立即触发** | 用户强烈纠正 | "我说了不要..."、"又来了"、"我不是这个意思" |
| **立即触发** | Agent 工具调用 | Agent 调用 `save_memory` 工具时 |
| **轮次触发** | 缓冲区累积 ≥ N 轮对话 | 默认 N=5，可配置。一次性对多轮对话做批量提取 |
| **时间触发** | 距上次写入 > T 秒 | 默认 T=300（5分钟），会话中长时间沉默后触发 |
| **会话结束触发** | 会话被关闭或切换 | 将当前缓冲区全部提取 |
| **启动触发** | 上次会话有未处理的缓冲 | 服务启动时检查持久化缓冲区 |

#### 判定优先级

```
用户明确指令 > 强烈纠正 > 工具调用 > 轮次触发 > 时间触发 > 会话结束
```

#### 显式触发检测

在 `memory_buffer.py` 中实现关键词检测，判断是否需要立即提取：

```python
EXPLICIT_SAVE_PATTERNS = [
    # 中文模式
    r"记住[：:]", r"记住这个", r"记住，", r"以后都",
    r"不要忘记", r"别忘了", r"帮我记", r"记下来",
    r"重要[：:]", r"注意事项[：:]",
    # 英文模式
    r"remember\s+(this|that|:)", r"save\s+(this|that)",
    r"don't\s+forget", r"note\s+(this|that|down)",
    r"keep\s+in\s+mind",
]

STRONG_CORRECTION_PATTERNS = [
    # 中文模式
    r"我说了?\s*(不要|别|不)", r"又(犯|来|是)",
    r"不是这个意思", r"怎么还",
    r"(停下来|停|停一下).*(听|看|想)",
    # 英文模式
    r"I\s+said\s+no", r"stop\s+(doing|it)",
    r"not\s+what\s+I\s+meant", r"again\?",
]
```

#### 批次提取流程

```
缓冲区累积 5 轮对话
    ↓
触发 flush() → 取出 5 轮对话
    ↓
构造批次提取 Prompt：
  "以下是一段连续的对话，请从中提取值得长期记住的事实。
   注意：跳过寒暄、临时指令、已经存在的重复信息。
   对于每条记忆，提供 fact/type/why/how_to_apply。"
    ↓
调用 LLM 做批量提取
    ↓
去重：与已有记忆对比，跳过语义重复的
    ↓
写入 mem0（附带完整元数据）
```

#### 缓冲区持久化

为防止服务重启导致缓冲区丢失，`memory_buffer.py` 将缓冲区状态持久化到文件：

```python
BUFFER_FILE = "storage/memory_buffer.json"

# 格式
{
    "buffer": [
        {"user": "...", "assistant": "...", "session_id": "...", "timestamp": 1712553600},
        ...
    ],
    "last_flush_time": 1712553500
}
```

### 对 agent.py 的影响

原来"每轮对话末尾调用 mem0.add()"的设计，改为：

```python
# agent.py astream() 末尾
# 不再直接调用 mem0_manager.add()
# 改为追加到缓冲区
buffer = get_memory_buffer()
buffer.add_turn(message, full_response, session_id)

# 检查是否需要立即触发（显式指令/强烈纠正）
if buffer.check_immediate_trigger(message):
    turns = buffer.flush()
    mem0_manager.batch_add(turns)
# 否则等待轮次/时间触发
```

---

## 需修改的文件

### 1. `backend/config.py` — 向后兼容扩展

在 `_DEFAULT_CONFIG` 中新增 `mem0` 配置块：

```python
"mem0": {
    "enabled": False,
    "mode": "legacy",               # "legacy" | "mem0" | "hybrid"
    "auto_extract": True,            # 是否自动从对话中提取事实
    "user_id": "default",
    # 智能截流
    "buffer_size": 5,                # 缓冲区轮次阈值
    "flush_interval_seconds": 300,   # 时间触发间隔（秒）
    # 离线整合
    "consolidation_interval_hours": 24,  # 整合间隔（小时）
    "consolidation_threshold": 50,       # 触发整合的最小记忆数量
    # 防御性读取
    "stale_threshold_days": 7,       # 标记为 aging 的天数
    "expire_threshold_days": 30,     # 标记为 stale 的天数
    "min_confidence": 0.3,           # 最低返回置信度
},
```

### 2. `backend/graph/agent.py` — 条件分支扩展（2 处改动）

**改动 A**：`astream()` 第 158-176 行，将硬编码 `memory_indexer` 调用替换为：

```python
from graph.memory_retriever import get_retriever
retriever = get_retriever(self._base_dir)
if retriever:
    results = retriever.retrieve(message)
    if results:
        yield {"type": "retrieval", "query": message, "results": results}
        rag_context = retriever.format_context(results)
```

**改动 B**：`astream()` 末尾 yield done 之前，将直接调用 mem0.add() 改为缓冲区追加：

```python
if mem0_enabled:
    from graph.memory_buffer import get_memory_buffer
    buffer = get_memory_buffer(self._base_dir)
    buffer.add_turn(message, full_response, session_id)
    if buffer.check_immediate_trigger(message):
        turns = buffer.flush()
        mem0_manager.batch_add(turns)
```

### 3. `backend/app.py` — 启动初始化 + 路由注册

- `lifespan()` 中条件初始化 mem0（try/except 包裹，失败不阻塞启动）
- 启动时恢复持久化的缓冲区
- 注册 `mem0_api` 路由

### 4. `backend/tools/__init__.py` — 条件注册 mem0 工具

### 5. `backend/requirements.txt` — 新增 `mem0ai` 和 `qdrant-client`

### 6. 前端文件
- `store.tsx`：新增 `mem0Retrievals` 字段和 `mem0_retrieval` 事件处理
- `ChatMessage.tsx`：条件渲染 `Mem0Card`（展示置信度、时间标注、验证提示）
- `settings/page.tsx`：新增「记忆管理」分类
- `settingsApi.ts`：扩展 mem0 相关类型

---

## 完整数据流

### 写入流（智能截流）

```
用户对话
  │
  ├─ [显式指令/强烈纠正] ──→ 立即触发 ──→ 批次提取 ──→ mem0 写入
  │
  └─ [普通对话] ──→ memory_buffer.add_turn()
                      │
                      ├─ 缓冲区 ≥ 5 轮 ──→ flush() → 批次提取 → mem0 写入
                      ├─ 距上次 > 300 秒 ──→ flush() → 批次提取 → mem0 写入
                      └─ 会话结束 ──→ flush() → 批次提取 → mem0 写入

Agent 主动写入:
  save_memory(fact, type, why, how_to_apply) → mem0_manager.add() [直接写入，不经缓冲区]
```

### 检索流（防御性读取）

```
用户消息
  │
  v
get_retriever(base_dir)
  │
  ├─ [legacy] MemoryIndexer.retrieve()           (现有逻辑不变)
  ├─ [mem0]   Mem0Manager.search() → 计算置信度 → 过滤低置信度
  └─ [hybrid] 两者并行 → 合并去重
  │
  v
format_context(results):
  ├─ 附加新鲜度等级提示（fresh/recent/aging/stale）
  ├─ 附加引用验证建议（文件路径、URL）
  └─ 附加置信度和时间信息
  │
  v
yield retrieval 事件 → 注入上下文 → Agent 回复（含验证行为）
```

### 整合流（离线碎片整理）

```
触发条件满足（定时/阈值/手动）
  │
  v
MemoryConsolidator.run_consolidation()
  │
  ├─ 阶段 1：去重 ──→ 语义相似度 > 0.85 的分组
  ├─ 阶段 2：合并 ──→ LLM 智能合并每组，保留 why & how_to_apply
  ├─ 阶段 3：冲突解决 ──→ 自动解决（时间优先）/ 生成冲突报告
  └─ 阶段 4：过期清理 ──→ 置信度 < 0.2 且 > 60天 → deprecated
  │
  v
生成 ConsolidationReport → 日志 / 管理页面展示
```

---

## mem0 配置方案

复用项目现有的 DashScope API（OpenAI 兼容模式）：

- **LLM**：用于事实提取和记忆整合，与主 Agent 共用模型配置
- **Embedding**：使用 `text-embedding-v4`，与现有 RAG 共用
- **向量存储**：Qdrant 本地磁盘模式（`backend/mem0_data/`），`on_disk=True`

### 四种记忆类型的元数据映射

| 类型 | `memory_type` | 提取时机 | 示例 fact | 示例 why | 示例 how_to_apply |
|------|--------------|----------|-----------|----------|-------------------|
| user | `"user"` | 自动或显式 | "用户偏好中文回复" | "用户在首次对话中明确要求全程中文" | "所有文本输出使用中文" |
| feedback | `"feedback"` | 自动提取 | "不要在代码中添加过多注释" | "用户审查代码时连续 3 次要求删除注释" | "写新代码时适用；修改他人代码时保留原风格" |
| project | `"project"` | 自动或显式 | "后端使用 DashScope Qwen 作为 LLM" | "config.json 中配置的 LLM provider" | "排查模型调用问题或修改 LLM 配置时参考" |
| reference | `"reference"` | 显式保存 | "DashScope API 文档地址" | "开发过程中需要频繁查阅" | "当需要查询 API 参数或错误码时" |

---

## memory_retriever.py 接口设计

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

class MemoryRetriever(ABC):
    """统一的记忆检索接口"""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """
        返回格式:
        [{
            "text": str,
            "score": str,
            "source": str,
            "memory_type"?: str,
            "id"?: str,
            "why"?: str,
            "how_to_apply"?: str,
            "created_at"?: str,
            "confidence"?: float,
            "freshness"?: str,        # fresh / recent / aging / stale
        }]
        """
        ...

    @abstractmethod
    def format_context(self, results: list[dict[str, Any]]) -> str:
        """将检索结果格式化为注入上下文的字符串，包含防御性读取提示"""
        ...


class LegacyRetriever(MemoryRetriever):
    """封装现有 LlamaIndex MemoryIndexer（行为不变）"""
    ...


class Mem0Retriever(MemoryRetriever):
    """封装 mem0 Memory.search()，附加防御性读取"""

    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        results = get_mem0_manager(self._base_dir).search(query, limit=top_k)
        # 过滤低置信度
        filtered = [r for r in results if r.get("confidence", 1.0) >= min_confidence]
        # 附加新鲜度等级
        for r in filtered:
            r["freshness"] = self._calculate_freshness(r.get("created_at"))
        return filtered

    def format_context(self, results: list[dict[str, Any]]) -> str:
        """生成带防御性提示的上下文"""
        parts = ["[智能记忆检索结果]"]
        for i, r in enumerate(results):
            part = f"--- 片段 {i+1} [{r['memory_type']}, 置信度: {r['confidence']}, 记录于: {r['created_at']}] ---\n"
            part += f"事实: {r['text']}\n"
            part += f"原因: {r.get('why', '未记录')}\n"
            part += f"适用场景: {r.get('how_to_apply', '未记录')}\n"
            # 防御性提示
            freshness = r.get("freshness", "fresh")
            if freshness != "fresh":
                age_days = self._calculate_age_days(r["created_at"])
                if freshness == "recent":
                    part += f"⚠️ 此记忆已存在 {age_days} 天，使用前请验证是否仍然有效\n"
                elif freshness == "aging":
                    part += f"⚠️ 此记忆已存在 {age_days} 天，很可能已过时，使用前必须验证\n"
                elif freshness == "stale":
                    part += f"🔴 此记忆已超过 {age_days} 天，仅作参考。使用前必须验证当前状态\n"
            # 引用验证提示
            references = self._extract_references(r["text"])
            if references:
                part += f"📋 记忆中引用了: {', '.join(references)}，请验证是否仍然存在\n"
            part += "---"
            parts.append(part)
        return "\n".join(parts)


class HybridRetriever(MemoryRetriever):
    """合并两个检索源的结果"""
    ...


def get_retriever(base_dir: Path) -> MemoryRetriever | None:
    """工厂方法：根据配置返回对应的检索器实例"""
    ...
```

---

## 分步实施

| 阶段 | 内容 | 优先级 | 涉及文件 |
|------|------|--------|----------|
| 1. 基础设施 | 添加依赖、扩展 `config.py`、创建 `mem0_manager.py`、修改 `app.py` 启动 | P0 | `requirements.txt`, `config.py`, `mem0_manager.py`(新), `app.py` |
| 2. 检索集成 | 创建 `memory_retriever.py`（含防御性读取）、修改 `agent.py` 检索入口 | P0 | `memory_retriever.py`(新), `agent.py` |
| 3. 智能截流 | 创建 `memory_buffer.py`（缓冲区 + 触发检测）、修改 `agent.py` 写入入口 | P1 | `memory_buffer.py`(新), `agent.py` |
| 4. 自动提取和工具 | 创建 `mem0_tool.py`（含 verify_memory）、修改 `tools/__init__.py` | P1 | `mem0_tool.py`(新), `tools/__init__.py` |
| 5. 离线整合 | 创建 `memory_consolidator.py`、在 `app.py` 中注册定时任务 | P1 | `memory_consolidator.py`(新), `app.py` |
| 6. 后端 API | 创建 `mem0_api.py`（含整合/验证端点） | P1 | `mem0_api.py`(新), `app.py` |
| 7. 前端集成 | `mem0Api.ts`, `Mem0Card.tsx`（含置信度/新鲜度展示），store/settings 扩展 | P2 | 前端多文件 |
| 8. 数据迁移 | MEMORY.md → mem0 导入（按标题段落分类，推导 why/how）、更新文档 | P2 | `mem0_api.py`, `AGENTS.md` |

---

## 降级策略

- **一级（配置切换）**：`config.mem0.enabled = false` → 完全回到现有行为，零开销
- **二级（依赖隔离）**：`mem0ai` 导入失败 → `app.py` 的 try/except 捕获，`get_retriever()` 自动降级为 LegacyRetriever
- **三级（数据保留）**：mem0 数据损坏 → MEMORY.md 始终不变（mem0 从不修改它），可重新导入
- **四级（缓冲区安全）**：服务崩溃 → 缓冲区已持久化到 `storage/memory_buffer.json`，重启后自动恢复
- **五级（完全卸载）**：删依赖、删 `mem0_data/`、删 `storage/memory_buffer.json`、关配置，恢复原始状态

---

## 风险点与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| mem0 与 LlamaIndex 版本冲突 | 安装失败或运行时错误 | 中 | mem0 作为可选依赖，导入时 try/except；禁用时完全不导入 |
| DashScope API 不兼容 mem0 的 OpenAI provider | 事实提取 LLM 调用失败 | 低 | DashScope compatible-mode 已验证兼容；可配置独立 LLM 参数 |
| Qdrant 本地存储重启丢数据 | 记忆丢失 | 中 | 强制 `on_disk=True`；启动检查完整性；提供从 MEMORY.md 重新导入的降级路径 |
| 批次提取的 LLM 成本 | 每 N 轮对话额外一次 LLM 调用 | 确定 | 缓冲区默认 5 轮才触发；显式触发仅检测关键词，不调 LLM；整合用轻量模型 |
| 整合管道误合并/误删除 | 丢失重要记忆 | 低 | deprecated 不物理删除；整合前生成预览；用户可在管理页面恢复 |
| 前端新增 SSE 事件类型不兼容旧版 | 旧版前端无法解析 | 低 | 新事件是可选的，前端优雅忽略未知事件 |
| 防御性提示过长占用上下文窗口 | 降低可用上下文 | 中 | 提示模板精简；fresh 级别不加提示；可配置是否启用防御性提示 |

---

## 验证方式

1. `mem0.enabled=False` 时，所有现有功能正常（回归测试）
2. `mem0.enabled=True, mode="mem0"` 时，对话后检查 `mem0_data/` 是否有数据写入
3. 连续对话 5 轮，验证缓冲区批次提取是否触发
4. 对话中输入"记住：我喜欢简洁的回复"，验证立即触发提取
5. 验证提取的记忆包含 why 和 how_to_apply 字段
6. 新对话中引用之前提到的偏好，验证检索是否命中，上下文中是否包含防御性提示
7. 创建冲突记忆（先说 A 后说 B），执行整合，验证冲突解决
8. 前端 Mem0Card 正确展示置信度、新鲜度、类型标签
9. 设置页面可触发手动整合、查看整合报告

---

## 性能优化（2026-04-09 实施）

mem0 集成后发现写入延迟过高（20-90 秒），进行了 6 项性能优化。详见 [性能优化方案](2026-04-09-mem0-performance-optimization.md)。

### 优化前后架构对比

```
优化前写入流程：
  Agent 回复 → Thread(target=background_write) → mgr.batch_add()
    ├─ LLM 事实提取（qwen3.5-plus, thinking 开启）  15-60s
    ├─ Embedding 计算                                2-5s
    └─ Qdrant 写入                                   <100ms
  总计: 20-90s

优化后写入流程：
  Agent 回复 → ThreadPoolExecutor(4).submit(background_write) → mgr.batch_add()
    ├─ LLM 事实提取（qwen3.5-flash, thinking 关闭）  2-5s
    ├─ Embedding 计算                                2-5s
    └─ Qdrant 写入                                   <100ms
  总计: ~5s
```

### 优化项目清单

| 优化项 | 改动文件 | 效果 |
|--------|---------|------|
| 独立轻量 LLM（qwen3.5-flash + thinking off） | config.json, config.py, mem0_manager.py | 写入延迟从 20-90s → 5s |
| ThreadPoolExecutor 替代裸 Thread | agent.py | 并发线程可控，超出排队不丢弃 |
| 检索异步化（run_in_executor） | memory_retriever.py, agent.py | 检索不阻塞 SSE 事件循环 |
| 整合管道单次扫描 | memory_consolidator.py | get_all() 从 3 次 → 1 次 |
| verify_memory 先加后删 | mem0_manager.py | 消除数据丢失窗口 |
| 配置缓存（30s TTL） | config.py | 避免每次请求读磁盘 |

### 实施中发现的额外 Bug

优化过程中额外发现并修复了 3 个原始集成代码的 bug：
1. `verify_memory()` 遍历 `_memory.get_all()` 返回的 dict 时未提取 `results` 列表
2. `verify_memory()` add 失败后仍返回 `True`
3. mem0 `OpenAIConfig` 不支持 `extra_body`，需通过 patch `generate_response` 注入

### 真实环境验证结果

10 项端到端联调测试全部通过（真实 DashScope API + Qdrant 本地存储），关键数据：
- batch_add(3 turns): **5.2s**
- 同步/异步/并行检索均正常
- 整合管道 get_all 调用 1 次
- verify 先加后删，旧 ID 正确替换
- 配置缓存命中 + save 失效
10. 服务重启后，验证缓冲区是否从持久化文件恢复
