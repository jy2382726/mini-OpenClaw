## Why

`UnifiedMemoryRetriever._read_memory_md()` 对 MEMORY.md 段落使用关键词子串匹配后固定赋 `score="0.5"`，导致所有匹配段落都超过 `MIN_RELEVANCE_SCORE=0.3` 阈值，相关性过滤形同虚设。与查询仅弱相关的 MEMORY.md 段落也会被注入上下文，浪费 token。

## What Changes

- 将 MEMORY.md 段落的匹配评分从固定 `0.5` 改为动态评分：根据查询关键词在段落中的匹配比例计算分数（`匹配关键词数 / 查询关键词总数`），映射到 0.3-0.7 区间
- 未匹配任何关键词的段落不参与评分（保持当前行为）
- 匹配比例低于一定阈值（如 30%）的段落将被 0.3 门槛自然过滤

## Capabilities

### New Capabilities

（无新增能力）

### Modified Capabilities

- `unified-memory`: 修改记忆条目相关性过滤 Requirement，MEMORY.md 段落评分从固定值改为基于匹配比例的动态分数

## Impact

**后端文件**:
- `backend/graph/unified_memory.py` — `_read_memory_md()` 方法中的 score 计算（第 166-170 行）

**测试文件**:
- `backend/tests/test_unified_memory.py`（如存在）— 验证动态评分行为

**影响分析**:
- 仅改变 MEMORY.md 检索结果的分数，不影响 mem0 和 RAG 检索源
- 评分降低后部分弱相关段落不再被注入，可能减少上下文中的 MEMORY.md 内容量
- 向后兼容：高相关度段落的分数仍高于阈值，行为不变

**回滚方案**: 低风险。恢复 `score="0.5"` 即可还原。
