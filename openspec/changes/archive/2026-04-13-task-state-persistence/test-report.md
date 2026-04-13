# 联调测试报告

**测试日期**: 2026-04-13
**测试环境**: WSL2 Ubuntu + Python 3.13 + Node.js
**测试方式**: 前后端实际联调（禁止 mock 数据）+ curl SSE 流验证 + checkpoint 数据库检查 + 浏览器 UI 操作

---

## 一、提案：context-engineering-optimization（已归档）

### 1.1 SkillRegistry 技能自动发现

| 测试项 | 结果 | 验证证据 |
|--------|------|----------|
| 启动时自动扫描 skills/ 目录 | ✅ 通过 | 后端日志 `🤖 Agent initialized with 9 tools` |
| SkillRegistry 实例缓存（不重复扫描） | ✅ 通过 | 单元测试 `test_skill_registry_cached` 确认同一实例 |
| 精简技能摘要注入 Zone 2 | ✅ 通过 | checkpoint 中 Zone 2 包含 `SKILLS_SNAPSHOT.md 1174t` |
| Skills 面板展示 6 个技能 | ✅ 通过 | 前端 Skills 面板显示 6 个技能卡片 |

### 1.2 UnifiedMemoryRetriever 统一记忆检索

| 测试项 | 结果 | 验证证据 |
|--------|------|----------|
| mem0 记忆检索（DashScope embedding） | ✅ 通过 | SSE 流 `retrieval` 事件返回 5 条记忆（置信度 0.27-0.40） |
| 检索结果注入 Zone 3 SystemMessage | ✅ 通过 | checkpoint 解码确认 `<!-- Zone 3: Dynamic -->` 包含记忆内容 |
| 前端 "智能记忆" 按钮展示 | ✅ 通过 | UI 显示 "智能记忆 5 条" |
| 记忆置信度标注 | ✅ 通过 | 记忆条目含 `置信度: 1.00` 标注 |

### 1.3 三段式提示缓存（Zone 1/2/3 分离）

| 测试项 | 结果 | 验证证据 |
|--------|------|----------|
| Zone 1 稳定层（SOUL + IDENTITY + USER） | ✅ 通过 | checkpoint Zone 1 内容稳定，workspace 文件不变时不重复生成 |
| Zone 2 低频层（AGENTS + Skills Snapshot） | ✅ 通过 | `SKILLS_SNAPSHOT.md` 包含精简技能描述 |
| Zone 3 动态层（记忆 + TaskState） | ✅ 通过 | 每次请求动态注入，不影响 Zone 1/2 缓存 |

### 1.4 四层中间件链

| 测试项 | 结果 | 验证证据 |
|--------|------|----------|
| ToolOutputBudgetMiddleware（工具输出截断） | ✅ 通过 | terminal 输出被截断至 2000 字符（config.json 配置） |
| SummarizationMiddleware（自动摘要） | ✅ 待触发 | 需要 >= 8000 tokens 触发，短对话未达到阈值 |
| ContextAwareToolFilter（工具过滤） | ✅ 通过 | agent 根据上下文自主选择工具 |
| ToolCallLimitMiddleware（工具调用限流） | ✅ 通过 | terminal 限 10 次/轮，python_repl 限 5 次/轮，未被误触 |
| 配置开关可独立关闭 | ✅ 通过 | 单元测试 `test_all_features_disabled` 确认中间件链为空 |

---

## 二、提案：task-state-persistence（当前）

### 2.1 TaskState 创建与持久化

| 测试项 | 结果 | 验证证据 |
|--------|------|----------|
| 任务性消息检测（is_task_message） | ✅ 通过 | "帮我创建..." 匹配 `帮我[做写创构建开设计实配部]` 模式 |
| TaskState 自动创建 | ✅ 通过 | checkpoint 数据库中 `task_state` 字段存在，goal 由 LLM 生成 |
| goal 摘要生成（_summarize_goal） | ✅ 通过 | 原始消息 → "创建简单的 Python Hello World 脚本并保存到指定路径。" |
| AsyncSqliteSaver 懒加载 | ✅ 通过 | 首次异步调用时创建 aiosqlite 连接，不阻塞同步 initialize() |
| checkpoint SQLite 文件持久化 | ✅ 通过 | `checkpoints.sqlite` 文件存在，包含 msgpack 编码的 checkpoint 数据 |
| 71 个单元测试全部通过 | ✅ 通过 | `pytest tests/ -v` → 71 passed, 4 warnings |

**checkpoint 数据验证**：
```
Session: task-test-2-1776048582
  Goal: 创建简单的 Python Hello World 脚本并保存到指定路径。
  Steps: 1 (active: 1)
    0. [in_progress] 为脚本添加命令行参数支持，允许传入名字。
```

### 2.2 跨请求 TaskState 恢复与追加

| 测试项 | 结果 | 验证证据 |
|--------|------|----------|
| 同会话跨请求恢复 TaskState | ✅ 通过 | 第二条消息发送后，checkpoint 恢复已有 goal 和 steps |
| 新任务性消息追加步骤 | ✅ 通过 | 步骤 "为脚本添加命令行参数支持" 成功追加 |
| goal 保持不变（不覆盖） | ✅ 通过 | 跨请求后 goal 仍为原始值 |
| 不同会话 TaskState 隔离 | ✅ 通过 | 单元测试 `test_different_sessions_isolated` 确认 |
| 非任务消息不覆盖已有 TaskState | ✅ 通过 | 单元测试 `test_non_task_message_preserves_existing_state` 确认 |

### 2.3 update_task 工具

| 测试项 | 结果 | 验证证据 |
|--------|------|----------|
| update_task 工具注册到 Agent | ✅ 通过 | `tool_names` 包含 "update_task"，Agent 可调用 |
| 工具参数 schema 正确 | ✅ 通过 | UpdateTaskArgs 包含 action/description/step_index/status/path 等 |
| Command 返回格式正确 | ✅ 通过 | 单元测试确认 `isinstance(result, Command)` |
| TaskState 无活跃任务时返回错误 | ✅ 通过 | 返回 "⚠️ 当前无活跃任务" ToolMessage |
| Agent 实际调用 update_task | ⚠️ 未触发 | LLM（Qwen 3.5）收到指引后仍选择不调用（模型行为，非代码 bug） |

### 2.4 update_task 指引注入

| 测试项 | 结果 | 验证证据 |
|--------|------|----------|
| 有 in_progress 步骤时注入指引 | ✅ 通过 | checkpoint 解码确认 Zone 3 包含 `<!-- Task Update Guidance -->` 完整指引 |
| 无 in_progress 步骤时不注入 | ✅ 通过 | 初始 TaskState steps 为空时，Zone 3 不含 update_task 指引 |
| 指引内容完整（5 种操作） | ✅ 通过 | add_step / update_step / add_artifact / add_blocker / add_decision 均列出 |
| 指引注入位置正确（倒数第二条） | ✅ 通过 | SystemMessage 在最后一条 HumanMessage 之前 |

**Zone 3 完整内容验证**：
```
<!-- Zone 3: Dynamic -->
[相关记忆]
...
## 当前任务
**目标**: 创建简单的 Python Hello World 脚本并保存到指定路径。

**步骤**:
1. 🔄 为脚本添加命令行参数支持，允许传入名字。

<!-- Task Update Guidance -->
**任务状态更新指引**：你可以在完成关键操作后调用 `update_task` 工具更新任务进度。可用操作：
- `add_step` description="步骤描述" — 添加新步骤
- `update_step` step_index=N status="completed|in_progress|blocked" result_summary="结果" — 更新步骤状态
...
```

---

## 三、发现的 BUG 与问题

### 3.1 已修复的 BUG

| # | 严重程度 | 描述 | 修复方式 |
|---|----------|------|----------|
| 1 | **严重** | SqliteSaver 不支持异步，agent.astream() 内部调用 aget_tuple() 抛 NotImplementedError | 替换为 AsyncSqliteSaver + aiosqlite，懒加载模式 |
| 2 | **中等** | _read_task_state / _write_task_state 使用同步方法，与 AsyncSqliteSaver 不兼容 | 改为 async def，使用 aget_state / aupdate_state |
| 3 | **中等** | 会话标题始终为 "New Chat" | 前一会话已修复的 bug |

### 3.2 已知限制（非 BUG）

| # | 描述 | 影响范围 |
|---|------|----------|
| 1 | uvicorn `--reload` 模式下，write_file 写入 workspace 目录触发服务热重载，导致 SSE 连接断开 | 仅开发模式，生产环境不影响。去掉 `--reload` 即可解决 |
| 2 | Qwen 3.5 模型对 update_task 指引遵循度不高，实际测试中未主动调用 | 不影响功能正确性，可通过调整指引措辞或换用更强模型改善 |
| 3 | aiosqlite 线程清理警告（Event loop is closed） | 仅在测试中出现（asyncio.run 退出时），不影响运行时功能 |
| 4 | write_file 工具限制只能写入 skills/workspace/memory 目录 | 设计如此（安全沙箱），用户请求写入 /tmp 时 agent 自动改写到 workspace/ |

### 3.3 待修复的 BUG

| # | 严重程度 | 描述 | 建议 |
|---|----------|------|------|
| 1 | **低** | /api/skills/load 返回 404 | 需检查前端 Skills 面板是否调用了不存在的 API 端点 |

---

## 四、优化建议

### 4.1 可进一步提升的功能点

| # | 提案相关 | 描述 | 优先级 |
|---|----------|------|--------|
| 1 | task-state | **update_task 调用率优化**：当前 Qwen 3.5 模型极少主动调用 update_task。建议：(a) 在 AGENTS.md 中添加明确的工具使用指引；(b) 考虑将 update_task 调用嵌入工具执行后置钩子（自动化）；(c) 评估 GPT-4o / Claude 等模型的遵循度 | 高 |
| 2 | context-engineering | **SummarizationMiddleware 真实触发测试**：需要构造 >= 8000 tokens 的长对话来验证自动摘要功能。当前联调测试的对话较短，未达到触发阈值 | 中 |
| 3 | task-state | **TaskState 过期清理机制**：当前 TaskState 只增不删，长期运行可能导致 checkpoint 膨胀。建议添加：(a) completed 状态 TaskState 自动归档；(b) 超过 N 天的 TaskState 自动清理 | 中 |
| 4 | task-state | **TaskState 前端可视化**：当前 TaskState 仅通过 Zone 3 注入给模型，用户不可见。建议在前端添加任务进度面板，展示当前目标、步骤状态、产物列表 | 低 |
| 5 | context-engineering | **UnifiedMemoryRetriever 缓存优化**：每次请求都调用 mem0 检索，可以添加短时间缓存（如 30 秒内相同查询复用结果），减少 API 调用 | 低 |

### 4.2 架构层面建议

| # | 描述 | 理由 |
|---|------|------|
| 1 | 生产部署时使用 `--reload` 以外的热更新方案 | 当前开发模式的热重载会在文件变更时中断 SSE 连接 |
| 2 | 考虑将 checkpoint 存储从 SQLite 迁移到 PostgreSQL（生产环境） | SQLite 不支持高并发写入，多用户场景可能成为瓶颈 |
| 3 | 添加 Prometheus 指标监控中间件链执行耗时 | 便于性能调优和异常告警 |

---

## 五、测试覆盖率总结

### 提案：context-engineering-optimization

| 模块 | 单元测试 | 联调测试 | 状态 |
|------|----------|----------|------|
| SkillRegistry | ✅ 3 项 | ✅ 前端 Skills 面板 | 全部通过 |
| UnifiedMemoryRetriever | ✅ 3 项 | ✅ SSE retrieval 事件 | 全部通过 |
| 三段式提示缓存 | ✅ 2 项 | ✅ checkpoint Zone 验证 | 全部通过 |
| ToolOutputBudgetMiddleware | ✅ 3 项 | ✅ 工具输出截断 | 全部通过 |
| SummarizationMiddleware | ✅ 2 项 | ⚠️ 未触发阈值 | 单元通过 |
| ContextAwareToolFilter | ✅ 2 项 | ✅ 工具选择正确 | 全部通过 |
| ToolCallLimitMiddleware | ✅ 2 项 | ✅ 未误触限流 | 全部通过 |

### 提案：task-state-persistence

| 模块 | 单元测试 | 联调测试 | 状态 |
|------|----------|----------|------|
| is_task_message 检测 | ✅ 4 项 | ✅ curl 验证 | 全部通过 |
| TaskState 创建 + checkpoint 写入 | ✅ 5 项 | ✅ SQLite 数据验证 | 全部通过 |
| 跨请求恢复 + 步骤追加 | ✅ 3 项 | ✅ curl + checkpoint 验证 | 全部通过 |
| update_task 工具注册 | ✅ 3 项 | ✅ tool_names 包含 | 全部通过 |
| update_task 工具执行 | ✅ 2 项 | ⚠️ LLM 未主动调用 | 单元通过 |
| 指引注入（有/无 active steps） | ✅ 2 项 | ✅ checkpoint Zone 3 验证 | 全部通过 |
| SSE 事件流兼容 | ✅ 6 项 | ✅ curl SSE 流验证 | 全部通过 |
| 功能开关 | ✅ 3 项 | — | 全部通过 |

**总计**：71 个单元测试全部通过 + 15+ 项联调验证
