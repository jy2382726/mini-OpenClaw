## 1. 动态评分实现

- [ ] 1.1 修改 `_read_memory_md()` 方法，在匹配段落时计算动态评分：`score = 0.3 + 0.4 * (matched_count / len(query_words))`，将 score 类型从字符串改为 float。`matched_count` 为段落中命中的关键词数量（去重），`query_words` 为查询关键词列表（`backend/graph/unified_memory.py:146-174`）

## 2. 测试更新

- [ ] 2.1 新增测试：多关键词查询时部分匹配段落的评分低于全匹配段落（`backend/tests/test_unified_memory.py`）
- [ ] 2.2 新增测试：单关键词查询匹配段落的评分为 0.7
- [ ] 2.3 新增测试：未匹配段落不返回

## 验证

- [ ] 2.4 运行 `pytest backend/tests/test_unified_memory.py -v` 确认所有测试通过
- [ ] 2.5 启动应用，发送消息验证 MEMORY.md 检索结果的相关性改善
