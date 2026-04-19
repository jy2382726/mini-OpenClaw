## MODIFIED Requirements

### Requirement: 记忆条目相关性过滤

系统 MUST 对检索到的记忆条目进行相关性过滤，只保留分数超过 `MIN_RELEVANCE_SCORE`（0.3）的条目。

过滤通过 `_result_score()` 函数实现，优先取 `confidence` 字段，其次取 `score` 字段作为排序/过滤依据。

MEMORY.md 段落的评分 MUST 使用动态评分：`score = 0.3 + 0.4 * (matched_keywords / total_query_keywords)`，其中 `matched_keywords` 为段落中命中的查询关键词数量，`total_query_keywords` 为查询关键词总数。score MUST 为 float 类型。

#### Scenario: 高匹配度段落获得高分

- **WHEN** 查询包含 3 个关键词，MEMORY.md 某段落匹配其中 3 个（覆盖率 100%）
- **THEN** 该段落 score MUST 为 `0.3 + 0.4 * 1.0 = 0.7`，通过 0.3 阈值被注入

#### Scenario: 低匹配度段落获得低分

- **WHEN** 查询包含 5 个关键词，MEMORY.md 某段落仅匹配其中 1 个（覆盖率 20%）
- **THEN** 该段落 score MUST 为 `0.3 + 0.4 * 0.2 = 0.38`，分数低于固定 0.5，但仍通过 0.3 阈值

#### Scenario: 未匹配任何关键词的段落不返回

- **WHEN** MEMORY.md 某段落不包含任何查询关键词
- **THEN** 该段落 MUST NOT 出现在检索结果中（保持当前行为）

#### Scenario: 单关键词查询全覆盖

- **WHEN** 查询仅包含 1 个关键词，段落匹配该关键词
- **THEN** 该段落 score MUST 为 `0.3 + 0.4 * 1.0 = 0.7`，高相关度通过过滤
