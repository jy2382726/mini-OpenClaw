## 1. ToolOutputBudgetMiddleware（前置截断）

- [x] 1.1 在 `backend/graph/` 下新建 `middleware.py`，实现 `ToolOutputBudgetMiddleware` 类，继承 `AgentMiddleware`，定义 `TOOL_OUTPUT_BUDGETS` 字典（terminal: 2000, python_repl: 1500, fetch_url: 3000, read_file: 2000, search_knowledge: 1000）
- [x] 1.2 实现 `abefore_model` 方法：遍历消息列表，对超过预算的 ToolMessage 调用 `_truncate_with_summary` 截断（保留头 2/3 + 尾 1/3，插入省略标注）
- [x] 1.3 编写单元测试：验证截断逻辑（超预算截断、未超不处理、非 ToolMessage 不受影响），在 `backend/tests/test_middleware.py`

## 2. SummarizationMiddleware 集成（自动摘要）

- [x] 2.1 验证 LangChain `SummarizationMiddleware` 的 API 兼容性：确认 `langchain==1.2.12` 中 `from langchain.agents.middleware import SummarizationMiddleware` 可正常导入，检查构造函数参数
- [x] 2.2 在 `backend/graph/agent.py` 的 `_build_agent()` 方法中，为 `create_agent` 添加 `middleware` 参数，将 `SummarizationMiddleware(model=lightweight_llm, trigger=("tokens", 8000), keep=("messages", 10))` 加入中间件列表
- [x] 2.3 配置轻量摘要模型：在 `backend/config.py` 中添加 `summary_model` 配置项（默认 `qwen-turbo`），在 `agent.py` 中根据配置创建轻量 LLM 实例
- [x] 2.4 标记 `backend/api/compress.py` 为废弃（添加 deprecation 注释），前端压缩按钮保留但标注为"可选"
- [x] 2.5 集成测试：发送超过 8000 tokens 的多轮对话，验证摘要自动触发、四段结构生成、最近 10 条消息保留

## 3. ToolCallLimitMiddleware（防死循环）

- [x] 3.1 在 `backend/graph/agent.py` 的中间件列表中添加 `ToolCallLimitMiddleware`：terminal 限 10 次、python_repl 限 5 次
- [x] 3.2 验证 LangChain `ToolCallLimitMiddleware` 的导入路径和构造参数
- [x] 3.3 测试：模拟 terminal 连续调用 11 次，验证第 11 次被拒绝并返回提示

## 4. ContextAwareToolFilter（运行时工具过滤）

- [x] 4.1 在 `backend/graph/middleware.py` 中实现 `ContextAwareToolFilter` 类，定义 `TOOL_TIERS` 字典（always/coding/web/memory/admin 分组）
- [x] 4.2 实现 `wrap_model_call` / `awrap_model_call` 方法：分析最近 6 条消息的关键词，判断需要的工具 tier，过滤 `request.tools`
- [x] 4.3 实现 `_has_coding_context`、`_has_web_context`、`_has_memory_context` 关键词匹配辅助方法
- [x] 4.4 在 `backend/graph/agent.py` 的中间件列表中注册 `ContextAwareToolFilter`
- [x] 4.5 测试：验证纯闲聊只暴露 always 工具、编码上下文暴露 coding 工具、工具定义不变

## 5. SkillRegistry（技能注册表）

- [x] 5.1 在 `backend/graph/` 下新建 `skill_registry.py`，实现 `SkillMeta` 数据类：`name`、`description`、`metadata: dict[str, str]`，提供 `is_auto_invocable`、`trigger_patterns`、`categories` 属性方法
- [x] 5.2 实现 `SkillRegistry` 类：`register()`、`get_auto_invocable_skills()`、`find_by_trigger()`、`build_compact_snapshot()` 方法，维护 `_index_by_trigger` 和 `_index_by_category` 索引
- [x] 5.3 实现 `discover(skills_dir)` 类方法：扫描 skills 目录，解析每个 SKILL.md 的 frontmatter，提取 name、description、metadata，注册到 SkillRegistry
- [x] 5.4 实现启发式推断逻辑：metadata 缺失时从 description 内容自动推断 invocation_auto（中文触发句式 → auto，"Use when asked to" → manual，默认 auto）和 trigger_patterns（从引号内容提取关键词）。不修改现有 SKILL.md 文件
- [x] 5.5 修改 `backend/app.py` 启动逻辑：用 `SkillRegistry.discover()` 替代当前 `skills_scanner.py` 的扫描逻辑
- [x] 5.6 测试：验证元数据解析、触发词匹配、分类索引、四级注入的精简快照生成

## 6. UnifiedMemoryRetriever（统一记忆检索）

- [x] 6.1 在 `backend/graph/` 下新建 `unified_memory.py`，实现 `UnifiedMemoryRetriever` 类，`__init__` 接受 mem0_client、rag_index、memory_md_path 三个记忆源
- [x] 6.2 实现 `retrieve(query, top_k=5)` 方法：依次从 mem0、RAG、MEMORY.md 检索，合并结果按来源优先级和相关性排序
- [x] 6.3 实现记忆源降级逻辑：任一记忆源异常时不阻塞请求，记录 warning 日志
- [x] 6.4 修改 `backend/graph/agent.py` 的 astream 方法：将 RAG 检索结果从 assistant 消息改为 SystemMessage 注入（位于当前 HumanMessage 之前），格式为 `[相关记忆] 内容（来源: xxx，置信度: 0.x）`
- [x] 6.5 测试：验证多记忆源合并检索、降级容错、注入格式正确性

## 7. 系统提示三段式缓存前缀

- [x] 7.1 重构 `backend/graph/prompt_builder.py`：将 `build_system_prompt()` 拆分为 `build_stable_prefix()`（Zone 1+2）和 `build_dynamic_prefix()`（Zone 3 由 agent.py 动态注入）
- [x] 7.2 `build_stable_prefix()` 包含：SOUL.md + IDENTITY.md + USER.md（Zone 1）+ AGENTS.md + SkillRegistry.build_compact_snapshot()（Zone 2）
- [x] 7.3 移除系统提示中的 MEMORY.md 全文注入（第 6 层）和 RAG_GUIDANCE，改为由 `UnifiedMemoryRetriever` 按需注入
- [x] 7.4 确保系统提示模板化拼接的确定性：使用固定 `_TEMPLATE` 模板 + `{变量}` 占位符
- [x] 7.5 测试：验证相同输入产生逐字符一致的输出、Zone 1+2 在 workspace 不变时稳定（11 项测试全通过）

## 8. TaskState（任务状态管理）

- [x] 8.1 在 `backend/graph/` 下新建 `task_state.py`，定义 `TaskStep` 和 `TaskState` 数据结构（TypedDict），包含 goal、steps、artifacts、decisions、blockers 字段
- [x] 8.2 定义 `AgentCustomState(TypedDict)`：包含 `task_state: NotRequired[dict | None]` 和 `context_type: NotRequired[str]`
- [x] 8.3 在 `backend/graph/agent.py` 的 `create_agent` 调用中添加 `state_schema=AgentCustomState` 参数，配合 `InMemorySaver()` checkpointer
- [x] 8.4 实现任务状态自动检测：在 `agent.py` 的 astream/ainvoke 中检测用户消息是否包含任务性动词，自动初始化 TaskState
- [x] 8.5 实现任务状态格式化注入：将活跃 TaskState 格式化为 Markdown（目标 + 步骤列表 + 状态图标），通过 build_dynamic_prefix(task_state=...) 作为 SystemMessage 注入在用户消息之前
- [x] 8.6 测试：验证 TaskState 初始化、格式化、任务动词检测（14 项测试全通过）

## 9. 集成验证与清理

- [x] 9.1 端到端集成测试：发送多轮对话（含工具调用、编码任务、闲聊），验证完整中间件链执行无报错
- [x] 9.2 验证 SSE 事件流：确认 token/tool_start/tool_end/rag_hit 等事件正常返回，与中间件链不冲突
- [x] 9.3 配置化开关：在 `backend/config.json` 中为每个中间件和功能添加开关，支持逐个启用/禁用
- [x] 9.4 清理旧代码：移除 `agent.py` 中的 `MAX_HISTORY_MESSAGES` 硬截断逻辑，移除 `prompt_builder.py` 中旧的技能快照生成逻辑
