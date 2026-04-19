# 代码审查修复提案计划

> 基于 11 个 OpenSpec spec 与代码逐行对比发现的全部问题，按领域分组为 4 个修复提案。
> 本文档仅为计划，不直接操作代码。

---

## 提案总览

| 提案 | 域 | 问题数 | 优先级 | 涉及文件 |
|------|----|--------|--------|---------|
| A. 归档系统健壮性修复 | progressive-tool-compression | 4 | P0/P1 | `middleware.py`, `sessions.py`, `chat.py` |
| B. 技能注册表功能补全 | skill-registry | 2 | P2 | `skill_registry.py` |
| C. 系统韧性加固 | 多域 | 3 | P1/P2 | `chat.py`, `config.py`, `agent.py` |
| D. 记忆过滤质量优化 | unified-memory | 1 | P3 | `unified_memory.py` |

---

## 提案 A: 归档系统健壮性修复

**Domain**: progressive-tool-compression
**优先级**: P0（含可导致中间件链中断的 Bug）
**影响范围**: `backend/graph/middleware.py`, `backend/api/sessions.py`

### 问题清单

#### A1. `_archive_output()` 写入失败导致中间件链中断 [Bug]

- **位置**: `middleware.py:116-134`
- **现状**: `_archive_output()` 中 `mkdir`（第 119 行）和 `write_text`（第 125 行）无 try/except 保护
- **风险**: 磁盘满、权限不足时异常直接冒泡到 `abefore_model`，导致 Agent 请求失败
- **修复**: 包裹 try/except，失败时 log.warning 并降级为纯截断

#### A2. 归档文件名不含 session_id，无法按会话关联清理 [设计缺陷]

- **位置**: `middleware.py:122`
- **现状**: 文件名格式 `tool_{tool_name}_{timestamp}.txt`，无 session_id 信息
- **风险**: 无法判断归档文件属于哪个会话，无法按会话清理
- **修复**: 文件名改为 `tool_{tool_name}_{session_id}_{timestamp}.txt`
- **依赖**: 需要将 session_id 传入 `ToolOutputBudgetMiddleware`

#### A3. delete/clear 不清理归档文件 [资源泄漏]

- **位置**: `sessions.py:57-71`（delete）、`sessions.py:159-169`（clear）
- **现状**: 只清理 checkpoint 线程，不清理 `sessions/archive/` 下的归档文件
- **风险**: 归档文件只增不减，长期运行后磁盘空间持续增长
- **修复**: 在 delete/clear 中根据 session_id 前缀批量删除关联的归档文件
- **前置依赖**: A2（文件名包含 session_id）

#### A4. 无归档文件 GC 机制 [资源泄漏]

- **位置**: 无现有代码
- **现状**: 没有任何过期清理机制
- **修复（可选）**: 在应用启动时或定期任务中清理超过 N 天（如 7 天）的归档文件
- **前置依赖**: A2（文件名包含 session_id 后更容易实现按时间清理）

### 实施顺序

```
A2 (文件名含 session_id) → A1 (写入降级保护) → A3 (delete/clear 级联清理) → A4 (GC，可选)
```

### 关键设计决策

1. **session_id 如何传入中间件**: 当前 `ToolOutputBudgetMiddleware.__init__` 不接收 session_id。需新增参数或在 `abefore_model` 的 state 中读取。推荐从 state 中的 `configurable.thread_id` 获取，避免修改构造函数签名。
2. **A4 GC 是否纳入本次**: 建议作为可选 Phase，A1-A3 是必须修复。

### 测试要点

- `_archive_output()` 磁盘不可写时降级为截断（不抛异常）
- 文件名包含 session_id 且格式一致
- delete 端点删除后归档文件被清理
- clear 端点清空后归档文件被清理
- 现有 `test_middleware.py` 中的归档测试需要更新文件名断言

---

## 提案 B: 技能注册表功能补全

**Domain**: skill-registry
**优先级**: P2
**影响范围**: `backend/graph/skill_registry.py`

### 问题清单

#### B1. Level 1 快照缺少触发词格式 [功能缺失]

- **位置**: `skill_registry.py:142`
- **现状**: `build_compact_snapshot()` 只输出 `- {skill.name}: {skill.description}`
- **期望**: 有触发词时追加 `[触发: 关键词1/关键词2]`
- **兼容性要求**: 无触发词的技能保持当前格式不变，不追加空括号
- **修复**: 在 `build_compact_snapshot()` 中检查 `trigger_patterns`，非空时追加

修复后格式：
```
## 可用技能（按需读取 SKILL.md 获取详情）
- get_weather: 获取天气信息 [触发: 天气/气温]
- dialogue-summarizer: 对话摘要工具
- get_date: 获取当前日期和时间
```

#### B2. Level 3 预加载注入逻辑缺失 [功能缺失]

- **位置**: `skill_registry.py:87-89`（属性定义）、`prompt_builder.py`（注入逻辑）
- **现状**: `inject_system_prompt` 属性推断已实现（可以读取到某个技能需要预加载），但没有任何代码读取该属性并将完整 SKILL.md 注入系统提示
- **修复**: 在 `build_compact_snapshot()` 或 `build_stable_prefix()` 中检查 `inject_system_prompt` 为 true 的技能，将完整 SKILL.md 内容注入 Zone 2
- **风险**: Zone 2 膨胀可能影响缓存命中率，需要控制注入的技能数量和内容长度

### 实施顺序

```
B1 (触发词格式，简单) → B2 (Level 3 注入，需评估影响)
```

### 关键设计决策

1. **B1 触发词数量**: 是否限制显示的触发词数量（如最多 3 个），避免单行过长
2. **B2 注入位置**: Zone 2（影响缓存）还是 Zone 3（不影响缓存但每次请求重新构建）
3. **B2 内容长度限制**: 是否对预加载的 SKILL.md 内容做长度限制

### 测试要点

- 有触发词的技能快照包含 `[触发: ...]`
- 无触发词的技能快照格式不变
- 多个触发词用 `/` 分隔
- Level 3 技能的完整内容出现在系统提示中
- 现有技能（dialogue-summarizer, get_weather 等）的注册不受影响

---

## 提案 C: 系统韧性加固

**Domain**: 多域
**优先级**: P1/P2
**影响范围**: `backend/api/chat.py`, `backend/config.py`, `backend/graph/agent.py`

### 问题清单

#### C1. bootstrap/touch 异常静默吞掉 [风险]

- **位置**: `chat.py:93-95`
- **现状**: `try...except Exception: pass` 静默吞掉所有异常
- **风险**: SQLite 连接失败、磁盘满等严重错误被隐藏，导致会话列表不一致而难以排查
- **修复**: 改为 `except Exception: logger.warning(...)` 至少记录异常日志，不阻塞对话但保留可追踪性

#### C2. 辅助模型默认值重复定义 [技术债]

- **位置**: `config.py:192`（`get_auxiliary_model_config()` 末尾）和 `config.py:286`（`get_settings_for_display()`）
- **现状**: `_DEFAULT_CONFIG` 中没有 `auxiliary_model` 字段，默认值在两处函数中重复硬编码
- **风险**: 修改默认值时容易遗漏一处，导致行为不一致
- **修复**: 在 `_DEFAULT_CONFIG` 中添加 `auxiliary_model` 默认配置，两处函数改为引用该默认值

#### C3. 流结束自动完成 in_progress 步骤 [行为确认]

- **位置**: `agent.py:535-547`
- **现状**: 流式响应结束后自动将所有 in_progress 步骤标记为 completed
- **风险**: 如果 Agent 正在多步任务中间（如步骤 2/5 完成，步骤 3 需要等待用户确认），流结束后步骤 3 被强制完成
- **修复选项**:
  - **选项 A（保持现状）**: 维持自动完成，更新 spec 匹配代码（已完成）
  - **选项 B（条件完成）**: 仅在流以正常 AI 文本响应结束时自动完成，以工具调用结束时不自动完成
  - **选项 C（可配置）**: 通过配置项控制是否自动完成
- **需要用户决策**: 这个行为是否符合产品意图？

### 实施顺序

```
C1 (日志记录，简单) → C2 (默认值统一，简单) → C3 (需先确认产品意图)
```

### 测试要点

- bootstrap 失败时日志中有 warning 级别记录
- 辅助模型默认值修改后两处引用一致
- C3 的测试取决于最终决策

---

## 提案 D: 记忆过滤质量优化

**Domain**: unified-memory
**优先级**: P3
**影响范围**: `backend/graph/unified_memory.py`

### 问题清单

#### D1. MEMORY.md 相关性过滤形同虚设 [质量优化]

- **位置**: `unified_memory.py:160-170`
- **现状**: 关键词子串匹配 + 固定 `score="0.5"`，所有匹配段落都超过 0.3 阈值
- **影响**: 与查询弱相关的 MEMORY.md 段落也会被注入，浪费 token
- **修复选项**:
  - **选项 A（匹配度评分）**: 根据匹配关键词数量/比例计算动态分数（如 0.3-0.7 区间）
  - **选项 B（段落预切分 + LlamaIndex 向量检索）**: 启动时将 MEMORY.md 切分为段落并用 LlamaIndex 索引，检索时走向量相似度
  - **选项 C（保持现状，调整阈值）**: 将 MEMORY.md 的匹配阈值从全局 0.3 提高到 0.5+，过滤掉低匹配度段落
- **评估**: 选项 A 改动最小、选项 B 效果最好但复杂度高、选项 C 是最简改进

### 实施顺序

待选定方案后确定。

---

## 实施优先级总排序

```
A1 (归档写入降级)    — P0，可导致请求失败的 Bug
A2 (归档文件名)      — P1，A3 的前置
A3 (归档级联清理)    — P1，防止资源泄漏
C1 (异常日志记录)    — P1，排查问题的基础
C2 (默认值统一)      — P2，技术债清理
B1 (触发词格式)      — P2，功能补全
B2 (Level 3 注入)    — P2，需评估影响
C3 (自动完成策略)    — P2，需先确认产品意图
A4 (归档 GC)         — P3，可选
D1 (过滤质量)        — P3，质量优化
```

## 需要用户决策的问题

1. **C3**: 流结束自动完成 in_progress 步骤，是保持现状（选项 A）还是改为条件完成（选项 B）？
2. **B2**: Level 3 预加载注入到 Zone 2 还是 Zone 3？是否有内容长度限制？
3. **D1**: MEMORY.md 过滤优化选择哪个方案（A/B/C）？
4. **A4**: 归档 GC 是否纳入本次修复范围？
