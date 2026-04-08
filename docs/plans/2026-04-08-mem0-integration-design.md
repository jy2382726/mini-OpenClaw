# mem0 集成方案：增强跨会话长期记忆管理

## 背景

当前 mini-OpenClaw 的长期记忆系统存在以下局限：
1. 所有记忆堆在单一 `MEMORY.md` 文件中，无分类
2. 依赖 Agent 手动通过 `terminal` 工具写入，无自动提取
3. 无去重/过期机制，记忆只增不减
4. 每次变化全量重建 LlamaIndex 向量索引

本方案参考 Claude Code 的记忆管理架构（4 种类型分类：user/feedback/project/reference，每条记忆独立管理），引入 mem0 作为智能记忆层，与现有系统并行运行、可切换，破坏性最小。

## 核心设计原则

- **适配器/策略模式**：通过 `MemoryRetriever` 抽象层封装三种检索策略（legacy / mem0 / hybrid）
- **不修改现有文件行为**：`memory_indexer.py` 和 `prompt_builder.py` 零改动
- **配置驱动切换**：`config.json` 新增 `mem0` 配置块，默认关闭，启用时零开销
- **复用现有 API**：mem0 的 LLM 和 embedding 复用项目已有的 DashScope 配置
- **Qdrant 本地模式**：无需 Docker 或外部服务，数据持久化到 `backend/mem0_data/`

---

## 新增文件（5 个后端 + 3 个前端）

| 文件 | 职责 |
|------|------|
| `backend/graph/mem0_manager.py` | mem0 核心管理器单例：封装 mem0 Memory 实例的创建、配置、生命周期。提供 `add()`, `search()`, `get_all()`, `delete()` 方法，内部处理 4 种记忆类型分类 |
| `backend/graph/memory_retriever.py` | 统一检索接口：`MemoryRetriever` 抽象基类 + `LegacyRetriever`(现有 LlamaIndex) / `Mem0Retriever`(mem0) / `HybridRetriever`(两者合并) 三个实现，工厂方法 `get_retriever()` |
| `backend/api/mem0_api.py` | 记忆管理 REST API：列表/删除/导入/状态检查 |
| `backend/tools/mem0_tool.py` | Agent 可调用工具：`save_memory`(带类型标签保存) 和 `search_memories`(主动搜索) |
| `frontend/src/lib/mem0Api.ts` | 前端 API 客户端 |
| `frontend/src/components/chat/Mem0Card.tsx` | mem0 检索结果卡片组件（带类型标签，区别于现有紫色 RetrievalCard） |
| `frontend/src/app/settings/memory/page.tsx` | 记忆管理设置页面 |

---

## 需修改的文件（6 个）

### 1. `backend/config.py` — 向后兼容扩展

在 `_DEFAULT_CONFIG` 中新增 `mem0` 配置块（`_deep_merge` 自动兼容旧配置）：

```python
"mem0": {
    "enabled": False,
    "mode": "legacy",       # "legacy" | "mem0" | "hybrid"
    "auto_extract": True,   # 自动从对话提取事实
    "user_id": "default",
}
```

新增 `get_mem0_config()` 和 `set_mem0_config()` 函数。在 `get_settings_for_display()` / `update_settings()` 中增加 mem0 块处理。

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

**改动 B**：`astream()` 末尾 yield done 之前，添加 mem0 自动提取：

```python
if mem0_enabled and auto_extract:
    mem0_mgr.add(messages=[user_msg, assistant_msg], user_id=...)
```

### 3. `backend/app.py` — 启动初始化 + 路由注册

- `lifespan()` 中条件初始化 mem0（try/except 包裹，失败不阻塞启动）
- 注册 `mem0_api` 路由

### 4. `backend/tools/__init__.py` — 条件注册 mem0 工具

### 5. `backend/requirements.txt` — 新增 `mem0ai` 和 `qdrant-client`

### 6. 前端文件

- `store.tsx`：新增 `mem0Retrievals` 字段和 `mem0_retrieval` 事件处理
- `ChatMessage.tsx`：条件渲染 `Mem0Card`
- `settings/page.tsx`：新增「记忆管理」分类
- `settingsApi.ts`：扩展 mem0 相关类型

---

## 数据流

### 写入流

```
用户对话 → agent.py astream() 完成
  → [mem0 启用] mem0_manager.add(user_msg + assistant_msg)
  → mem0 内部 LLM 提取事实（复用 DashScope）
  → 自动去重/合并 → Qdrant 本地存储

Agent 工具 → save_memory(content, type="user") → mem0_manager.add()
```

### 检索流

```
用户消息 → agent.py → get_retriever(base_dir)
  → [legacy] MemoryIndexer.retrieve()    (现有逻辑不变)
  → [mem0]   Mem0Manager.search()
  → [hybrid] 两者并行，合并去重
  → yield retrieval 事件 → 注入上下文 → Agent 回复
```

### 管理流

```
设置页面 → 记忆管理
  → mem0Api.ts → GET /api/mem0/memories?type=user&limit=20
  → mem0_api.py → mem0_manager.get_all(user_id, filters)
  → 返回 [{id, memory, memory_type, created_at, updated_at}]
  → 前端展示记忆列表：按类型筛选、搜索、单条删除、从 MEMORY.md 批量导入
```

---

## mem0 配置方案

复用项目现有的 DashScope API（OpenAI 兼容模式）：

- **LLM**：用于事实提取，与主 Agent 共用模型配置
- **Embedding**：使用 `text-embedding-v4`，与现有 RAG 共用
- **向量存储**：Qdrant 本地磁盘模式（`backend/mem0_data/`），`on_disk=True`

### 核心配置代码（mem0_manager.py 内部）

```python
MEM0_CONFIG = {
    "llm": {
        "provider": "openai",  # DashScope 兼容 OpenAI API
        "config": {
            "model": llm_model,           # 从 config.json 读取
            "api_key": api_key,            # 从 config.json 读取
            "api_base": api_base_url,      # DashScope compatible-mode URL
            "temperature": 0.1,
            "max_tokens": 1500,
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": emb_model,            # text-embedding-v4
            "api_key": emb_api_key,
            "api_base": emb_base_url,
        },
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": "mini_openclaw_memories",
            "path": str(base_dir / "mem0_data"),
            "on_disk": True,
            "embedding_model_dims": 1024,   # text-embedding-v4 维度
        },
    },
}
```

### 自定义事实提取 Prompt

```
从对话中提取值得长期记住的事实。按以下分类标记：
- user: 用户偏好、习惯、个人信息
- feedback: 用户对AI回复的反馈（满意/不满意/改进建议）
- project: 项目相关的上下文（技术栈、架构决策、文件位置）
- reference: 外部引用（文档链接、代码片段、参考资料）

排除：寒暄、重复信息、临时性对话。
返回 JSON，key 为 "facts"，value 为字符串列表。
```

### 四种记忆类型的元数据映射

| 类型 | `metadata.memory_type` | 提取时机 | 示例 |
|------|------------------------|----------|------|
| user | `"user"` | 自动提取 或 Agent 工具 | "用户喜欢更严谨的回答" |
| feedback | `"feedback"` | 自动提取 | "用户要求回复更简洁" |
| project | `"project"` | Agent 工具 或自动提取 | "项目使用 DashScope Qwen 作为 LLM" |
| reference | `"reference"` | Agent 工具 | "API 文档地址：https://..." |

---

## memory_retriever.py 接口设计

这是集成的核心抽象层。`agent.py` 只需修改一处——将 `get_memory_indexer()` 替换为 `get_retriever()`——所有策略变化封装在此模块中。

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

class MemoryRetriever(ABC):
    """统一的记忆检索接口"""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """返回格式: [{"text": str, "score": str, "source": str, "memory_type"?: str, "id"?: str}]"""
        ...

    @abstractmethod
    def format_context(self, results: list[dict[str, Any]]) -> str:
        """将检索结果格式化为注入上下文的字符串"""
        ...

class LegacyRetriever(MemoryRetriever):
    """封装现有 LlamaIndex MemoryIndexer"""
    # 内部调用 get_memory_indexer(base_dir).retrieve(query, top_k)
    # format_context 生成 [记忆检索结果] 格式（与现有格式一致）
    ...

class Mem0Retriever(MemoryRetriever):
    """封装 mem0 Memory.search()"""
    # 内部调用 get_mem0_manager(base_dir).search(query, user_id, limit=top_k)
    # 将 mem0 的 {"memory": ..., "score": ...} 映射为统一格式
    # format_context 生成 [智能记忆检索结果] 格式，带类型标签
    ...

class HybridRetriever(MemoryRetriever):
    """合并两个检索源的结果"""
    # 并行调用 LegacyRetriever 和 Mem0Retriever
    # 按 score 排序，截取 top_k
    # 标注来源 (legacy / mem0)
    ...

def get_retriever(base_dir: Path) -> MemoryRetriever | None:
    """工厂方法：根据配置返回对应的检索器实例"""
    from config import get_mem0_config, get_rag_mode

    if not get_rag_mode():
        return None

    mem0_cfg = get_mem0_config()
    mode = mem0_cfg.get("mode", "legacy")

    if mode == "legacy" or not mem0_cfg.get("enabled"):
        return LegacyRetriever(base_dir)
    elif mode == "mem0":
        return Mem0Retriever(base_dir)
    elif mode == "hybrid":
        return HybridRetriever(base_dir)
    else:
        return LegacyRetriever(base_dir)
```

---

## 分步实施

| 阶段 | 内容 | 优先级 | 涉及文件 |
|------|------|--------|----------|
| 1. 基础设施 | 添加依赖、创建 `mem0_manager.py`、扩展 `config.py`、修改 `app.py` 启动 | P0 | `requirements.txt`, `config.py`, `mem0_manager.py`(新), `app.py` |
| 2. 检索集成 | 创建 `memory_retriever.py`、修改 `agent.py` 检索入口 | P0 | `memory_retriever.py`(新), `agent.py` |
| 3. 自动提取和工具 | 修改 `agent.py` 添加自动提取、创建 `mem0_tool.py` | P1 | `agent.py`, `mem0_tool.py`(新), `tools/__init__.py` |
| 4. 后端 API | 创建 `mem0_api.py` | P1 | `mem0_api.py`(新), `app.py` |
| 5. 前端集成 | Mem0Card、store 扩展、设置页面 | P2 | `mem0Api.ts`(新), `Mem0Card.tsx`(新), `store.tsx`, `ChatMessage.tsx`, `settings/page.tsx`, `settingsApi.ts` |
| 6. 数据迁移 | MEMORY.md → mem0 导入功能、更新文档 | P2 | `mem0_api.py`, `AGENTS.md` |

---

## 降级策略

- **一级（配置切换）**：`config.mem0.enabled = false` → 完全回到现有行为，零开销
- **二级（依赖隔离）**：`mem0ai` 导入失败 → `app.py` 的 try/except 捕获，`get_retriever()` 自动降级为 LegacyRetriever
- **三级（数据保留）**：mem0 数据损坏 → MEMORY.md 始终不变（mem0 从不修改它），可重新导入
- **四级（完全卸载）**：删依赖、删 `mem0_data/`、关配置，恢复原始状态

---

## 风险点与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| mem0 与 LlamaIndex 版本冲突 | 安装失败或运行时错误 | 中 | mem0 作为可选依赖，导入时 try/except；禁用时完全不导入 |
| DashScope API 不兼容 mem0 的 OpenAI provider | 事实提取 LLM 调用失败 | 低 | DashScope compatible-mode 已验证兼容；可配置独立 LLM 参数 |
| Qdrant 本地存储重启丢数据 | 记忆丢失 | 中 | 强制 `on_disk=True`；启动检查完整性；提供从 MEMORY.md 重新导入的降级路径 |
| mem0 自动提取增加 API 调用开销 | 每次对话额外 LLM 成本 | 确定 | 默认关闭 auto_extract；支持 Agent 工具手动触发 |
| 前端新增 SSE 事件类型不兼容旧版 | 旧版前端无法解析 | 低 | 新事件是可选的，前端优雅忽略未知事件 |

---

## 验证方式

1. `mem0.enabled=False` 时，所有现有功能正常（回归测试）
2. `mem0.enabled=True, mode="mem0"` 时，对话后检查 `mem0_data/` 是否有数据写入
3. 对话中提及用户偏好，验证 mem0 是否自动提取并分类
4. 新对话中引用之前提到的偏好，验证检索是否命中
5. 前端 Mem0Card 正确展示检索结果和类型标签
6. 设置页面可切换模式、查看/删除记忆
7. `hybrid` 模式下同时检索 MEMORY.md 和 mem0 记忆
