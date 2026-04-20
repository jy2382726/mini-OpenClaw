# 全量功能测试报告

**测试日期**: 2026-04-20
**测试环境**: 本地开发环境（后端 :8002 / 前端 :3000）
**测试方法**: 实际操作页面 + API 调用 + 后端日志 + 数据库验证
**测试范围**: 4 个活跃提案的全量功能点

---

## 提案 1: glob-grep-tool-integration（文件搜索工具集成）

### 功能点 1.1: glob_search 工具注册

| 项目 | 结果 |
|------|------|
| 测试方法 | 发送消息"搜索项目中所有的 Python 文件"，观察 Agent 工具调用 |
| 预期 | glob_search 工具被注册并可用 |
| 实际 | ✅ Agent 连续调用 glob_search 3 次（`**/*.py`、`backend/graph`、`**/graph/**/*.py`） |
| 证据 | 浏览器 UI 可见 3 个 `glob_search` 工具调用卡片；后端日志 `Agent initialized with 11 tools` |

### 功能点 1.2: grep_search 工具注册

| 项目 | 结果 |
|------|------|
| 测试方法 | 发送消息"搜索代码中包含 MemoryMiddleware 的文件" |
| 预期 | grep_search 工具被注册并可用 |
| 实际 | ✅ Agent 调用 grep_search 2 次，返回正确结果 |
| 证据 | 浏览器 UI 可见 2 个 `grep_search` 工具调用卡片；结果正确找到 `memory_middleware.py` 和 `agent.py` |

### 功能点 1.3: 工具输出预算配置

| 项目 | 结果 |
|------|------|
| 测试方法 | 读取 `config.json` 验证预算配置 |
| 预期 | glob_search: 1500, grep_search: 2500 |
| 实际 | ✅ 配置正确：`glob_search: 1500, grep_search: 2500` |
| 证据 | `config.json` → `middleware.tool_output_budget.budgets` |

### 功能点 1.4: 工具层级分类

| 项目 | 结果 |
|------|------|
| 测试方法 | 代码审查 `TOOL_TIERS["always"]` |
| 预期 | glob_search 和 grep_search 归入 `always` 层级 |
| 实际 | ✅ 两个工具在 `always` 层级，所有对话均可使用 |
| 证据 | `middleware.py` 中 `TOOL_TIERS["always"]` 包含两个工具 |

### 功能点 1.5: FilesystemFileSearchMiddleware 注册

| 项目 | 结果 |
|------|------|
| 测试方法 | 检查中间件链注册（checkpoint channel + Agent 输出） |
| 预期 | 第 6 层 FilesystemFileSearchMiddleware 注册 |
| 实际 | ✅ Agent 自身输出确认 6 层中间件链，第 6 层为 FilesystemFileSearchMiddleware |
| 证据 | 浏览器 UI 中 Agent 回复展示的中间件表格；checkpoint `channel_versions` 字段 |

### 功能点 1.6: 大输出归档

| 项目 | 结果 |
|------|------|
| 测试方法 | glob_search 搜索 `**/*.py` 触发大输出 |
| 预期 | 超预算输出被归档到文件 |
| 实际 | ✅ 生成归档文件 `tool_glob_search_session-a2314c78355d_*.txt`（722KB） |
| 证据 | `backend/sessions/archive/` 目录下存在归档文件，内容为原始搜索结果 |

### 功能点 1.7: 搜索结果格式一致性

| 项目 | 结果 |
|------|------|
| 测试方法 | 验证搜索结果路径可被后续工具直接使用 |
| 预期 | Agent 能基于搜索结果执行 read_file 等后续操作 |
| 实际 | ✅ Agent 基于搜索结果成功读取了 middleware.py 和 agent.py |
| 证据 | 浏览器 UI 中后续 `read_file` 工具调用成功 |

**提案 1 总结: 7/7 通过 ✅**

---

## 提案 2: memory-middleware-refactor（记忆中间件重构）

### 功能点 2.1: MemoryMiddleware 注册

| 项目 | 结果 |
|------|------|
| 测试方法 | 检查 checkpoint channel_versions |
| 预期 | `MemoryMiddleware.before_agent` channel 存在 |
| 实际 | ✅ checkpoint `channel_versions` 包含 `branch:to:MemoryMiddleware.before_agent` |
| 证据 | 直接读取 `checkpoints.sqlite` 数据库 |

### 功能点 2.2: 记忆检索（abefore_agent）

| 项目 | 结果 |
|------|------|
| 测试方法 | 发送消息，观察前端"智能记忆"指示器 |
| 预期 | 记忆检索触发，前端显示检索结果数 |
| 实际 | ✅ 首条消息回复中显示"智能记忆 5 条"按钮 |
| 证据 | 浏览器 UI 中助手回复含"智能记忆 5 条"标签 |

### 功能点 2.3: memory_context 状态持久化

| 项目 | 结果 |
|------|------|
| 测试方法 | 读取 checkpoint channel_values |
| 预期 | `memory_context` 字段存在且包含记忆内容 |
| 实际 | ✅ `memory_context` 包含 3 条记忆（天气技能、新闻搜索等） |
| 证据 | checkpoint `channel_values.memory_context` 字段内容："[相关记忆] 创建了备用天气技能..." |

### 功能点 2.4: mem0 后台写入（aafter_agent）

| 项目 | 结果 |
|------|------|
| 测试方法 | 检查后端日志 |
| 预期 | 日志显示 mem0 后台写入完成 |
| 实际 | ✅ 日志显示"🧠 mem0 后台写入完成（1 轮对话）"和"（3 轮对话）" |
| 证据 | 后端进程 stdout 输出 |

### 功能点 2.5: injection_mode 配置（system_prompt 模式）

| 项目 | 结果 |
|------|------|
| 测试方法 | 验证 config.json 配置 |
| 预期 | `injection_mode: "system_prompt"` 配置正确 |
| 实际 | ✅ 配置为 `system_prompt` 模式，记忆通过 `request.override(system_message=...)` 注入 |
| 证据 | `config.json` → `middleware.memory_middleware.injection_mode: "system_prompt"` |

### 功能点 2.6: 中间件链位置（第 5 层）

| 项目 | 结果 |
|------|------|
| 测试方法 | 检查 Agent 输出的中间件表格 |
| 预期 | MemoryMiddleware 位于第 5 层 |
| 实际 | ✅ Agent 输出确认第 5 层为 MemoryMiddleware |
| 证据 | 浏览器 UI 中 Agent 回复的中间件表格（6 层） |

### 功能点 2.7: agent.py 旧代码清理

| 项目 | 结果 |
|------|------|
| 测试方法 | 代码审查 agent.py |
| 预期 | 旧的记忆检索/注入/写入代码已从 astream/ainvoke 中移除 |
| 实际 | ✅ 仅保留注释"记忆上下文由 MemoryMiddleware 管理"（第 547、617、701、751 行） |
| 证据 | grep_search 搜索结果显示仅有注释引用，无逻辑代码 |

**提案 2 总结: 7/7 通过 ✅**

---

## 提案 3: summarization-middleware-optimization（摘要中间件优化）

### 功能点 3.1: trigger_ratio / trim_ratio 配置生效

| 项目 | 结果 |
|------|------|
| 测试方法 | 读取 config.json 配置 |
| 预期 | trigger_ratio: 0.6, trim_ratio: 0.3 |
| 实际 | ✅ 配置正确 |
| 证据 | `config.json` → `middleware.summarization.trigger_ratio: 0.6, trim_ratio: 0.3` |

### 功能点 3.2: ContextAwareSummarizationMiddleware 注册

| 项目 | 结果 |
|------|------|
| 测试方法 | 检查 checkpoint versions_seen 和 Agent 输出 |
| 预期 | 中间件注册并运行 |
| 实际 | ✅ checkpoint 包含 `ContextAwareSummarizationMiddleware.before_model`；Agent 输出确认第 2 层 |
| 证据 | checkpoint `versions_seen` 字段；浏览器 UI 中间件表格 |

### 功能点 3.3: 中文 8 节摘要提示词

| 项目 | 结果 |
|------|------|
| 测试方法 | 触发手动压缩，检查摘要内容 |
| 预期 | 摘要包含 8 节结构（会话意图、关键决策、工具调用、文件产物、错误修复、用户消息、当前进展、后续步骤） |
| 实际 | ✅ 摘要包含完整 8 节结构，全部使用中文 |
| 证据 | History API 返回的摘要消息内容：

```
## 会话意图
探索项目 Python 文件结构...
## 关键决策
根据搜索结果调整目录认知...
## 工具调用
1. glob_search... 2. glob_search... 3. glob_search... 4. grep_search...
## 文件产物
- sessions/archive/... - /graph/memory_middleware.py... - /graph/agent.py...
## 错误修复
纠正了目录路径假设...
## 用户消息
1. 请求搜索... 2. 请求搜索...
## 当前进展
已完成项目 Python 文件结构概览...
## 后续步骤
读取并分析...
```

### 功能点 3.4: context_window 和 usage_ratio API

| 项目 | 结果 |
|------|------|
| 测试方法 | 调用 `/api/tokens/session/{id}` API |
| 预期 | 返回 `context_window` 和 `usage_ratio` 字段 |
| 实际 | ✅ 返回 `{"context_window": 131072, "usage_ratio": 0.02}` |
| 证据 | curl API 调用结果 |

### 功能点 3.5: 压缩按钮上下文使用率显示

| 项目 | 结果 |
|------|------|
| 测试方法 | 观察浏览器 UI 压缩按钮文本 |
| 预期 | 有 contextUsage 数据时始终显示百分比；null 时显示"压缩" |
| 实际 | ✅ 测试中发现原实现仅在 ratio≥0.6 时显示百分比，已修复为始终显示。修复后按钮显示"压缩 (3%)" |
| 证据 | 浏览器 evaluate 返回 `"压缩 (3%)"`；代码 `ChatInput.tsx:167` |
| 修复 | 将 `contextUsage.ratio >= 0.6` 条件改为 `contextUsage`，始终显示百分比 |

### 功能点 3.6: 手动压缩 API

| 项目 | 结果 |
|------|------|
| 测试方法 | 点击前端"压缩"按钮，确认弹窗后执行 |
| 预期 | 弹出确认对话框，执行后显示"摘要完成"提示 |
| 实际 | ✅ 弹出"确定要摘要早期消息吗？将保留最近 10 条消息"；完成后提示"摘要完成！早期消息已压缩为摘要。" |
| 证据 | 浏览器 Modal state：confirm dialog → alert dialog；后端日志 `POST /summarize 200 OK` |

### 功能点 3.7: summary_prompt.md 文件

| 项目 | 结果 |
|------|------|
| 测试方法 | 检查文件是否存在 |
| 预期 | `workspace/summary_prompt.md` 存在 |
| 实际 | ✅ 文件存在，包含 8 节中文摘要模板 |
| 证据 | 文件系统 |

**提案 3 总结: 7/7 通过 ✅**

---

## 提案 4: tool-output-compression（工具输出压缩改进）

### 功能点 4.1: 压缩标记机制

| 项目 | 结果 |
|------|------|
| 测试方法 | 代码审查 `_COMPRESSED_MARKER` 常量和 `_make_marker()` 方法 |
| 预期 | 标记格式为 `<!-- compressed:{method}:{original_length}:{path} -->` |
| 实际 | ✅ 常量定义在 `middleware.py:65`，`_make_marker()` 生成正确格式 |
| 证据 | `middleware.py` 第 65、120-123 行 |

### 功能点 4.2: 幂等检测（_is_compressed）

| 项目 | 结果 |
|------|------|
| 测试方法 | 代码审查 `abefore_model` 中的幂等检测逻辑 |
| 预期 | 已标记消息在下一轮被跳过，不重复压缩 |
| 实际 | ✅ `middleware.py:268` 处 `_is_compressed(content)` 为 True 时 `continue` 跳过 |
| 证据 | `middleware.py` 第 267-270 行 |

### 功能点 4.3: 先归档后截断

| 项目 | 结果 |
|------|------|
| 测试方法 | 检查归档目录和文件 |
| 预期 | 超预算工具输出先保存原始数据到归档文件，再执行截断 |
| 实际 | ✅ 归档文件 `tool_glob_search_session-a2314c78355d_*.txt` 包含 722KB 原始数据 |
| 证据 | `backend/sessions/archive/` 目录；归档文件内容为完整搜索结果列表 |

### 功能点 4.4: _archive_original() 方法

| 项目 | 结果 |
|------|------|
| 测试方法 | 代码审查 + 实际触发验证 |
| 预期 | 原始内容写入 `sessions/archive/` 目录 |
| 实际 | ✅ 归档文件命名格式 `tool_{name}_{session_id}_{timestamp}.txt`，内容为原始数据 |
| 证据 | `middleware.py:165` 方法定义；归档目录实际文件 |

### 功能点 4.5: 结构化头尾摘要（_make_truncated_content）

| 项目 | 结果 |
|------|------|
| 测试方法 | 代码审查 |
| 预期 | 截断内容包含标记头 + 归档引用 + 头部内容 + 省略量 + 尾部内容 |
| 实际 | ✅ `_make_truncated_content()` 按 strategy 分配头尾预算，嵌入 marker |
| 证据 | `middleware.py:200-220` |

### 功能点 4.6: 归档大输出（_make_archived_content）

| 项目 | 结果 |
|------|------|
| 测试方法 | 代码审查 + glob_search 大输出验证 |
| 预期 | 超大内容（> archive_threshold * 4）走 archived 策略 |
| 实际 | ✅ glob_search 搜索 `**/*.py` 返回 722KB 结果，成功归档 |
| 证据 | 归档文件大小 722693 字节；`middleware.py:281-282` 策略选择逻辑 |

### 功能点 4.7: 压缩日志统计

| 项目 | 结果 |
|------|------|
| 测试方法 | 代码审查 |
| 预期 | 压缩发生时记录 info 级别日志 |
| 实际 | ✅ `logger.info("工具输出压缩: %d 条消息已处理, 策略=%s, 保护=%d组")` |
| 证据 | `middleware.py:300-303` |
| 备注 | 本次测试中 token 使用率仅 3%（Level 0），渐进式压缩未触发，需更高 token 使用率场景 |

**提案 4 总结: 7/7 通过 ✅**

---

## 总览

| 提案 | 功能点 | 通过 | 失败 | 通过率 |
|------|--------|------|------|--------|
| glob-grep-tool-integration | 7 | 7 | 0 | 100% |
| memory-middleware-refactor | 7 | 7 | 0 | 100% |
| summarization-middleware-optimization | 7 | 7 | 0 | 100% |
| tool-output-compression | 7 | 7 | 0 | 100% |
| **合计** | **28** | **28** | **0** | **100%** |

## 未覆盖场景（需后续验证）

1. **上下文使用率 > 60% 时压缩按钮显示百分比**：需要构造长对话使 token 使用率超过 60%
2. **上下文使用率 > 80% 时发送按钮禁用**：同上
3. **Level 1/Level 2 渐进式压缩触发**：需要更高 token 使用率
4. **injection_mode 切换为 system_message**：需修改配置重启服务
5. **HITL interrupt + resume 后记忆上下文不丢失**：需启用 HITL 配置

## 测试结论

4 个提案的 28 个核心功能点全部通过验证。系统在正常负载下功能完整、行为正确。高负载场景（高 token 使用率）下的渐进式压缩和上下文使用率阈值功能需在后续长对话测试中进一步验证。
