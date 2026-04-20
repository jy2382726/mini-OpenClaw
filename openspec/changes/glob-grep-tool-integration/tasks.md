## 1. 中间件注册

- [x] 1.1 在 `backend/graph/agent.py` 的 `_build_middleware()` 中追加第 6 层 `FilesystemFileSearchMiddleware` 注册，配置 `root_path=str(self._base_dir)`、`use_ripgrep=True`、`max_file_size_mb=10`

## 2. 配置更新

- [x] 2.1 在 `backend/graph/middleware.py` 的 `TOOL_OUTPUT_BUDGETS` 中添加 `glob_search: 1500` 和 `grep_search: 2500` 预算配置
- [x] 2.2 在 `backend/graph/middleware.py` 的 `TOOL_TIERS["always"]` 中添加 `glob_search` 和 `grep_search`
- [x] 2.3 在 `backend/graph/middleware.py` 的 `_has_coding_context` 中补充搜索相关**复合**关键词（"搜索文件"、"查找文件"、"搜索代码"、"查找代码"、"glob"、"grep"、"find file"、"search code"），避免单字误匹配
- [x] 2.4 在 `backend/config.json` 的 `middleware.tool_output_budgets` 中添加 glob_search 和 grep_search 的预算配置

## 3. 文档更新

- [x] 3.1 在 `backend/workspace/AGENTS.md` 工具列表中新增 glob_search 和 grep_search 工具说明（位于 read_file 之前）
- [x] 3.2 在 `backend/workspace/AGENTS.md` 中新增"文件搜索策略"指引段落：已知路径→read_file；不确定路径→glob_search→read_file；搜索内容→grep_search；路径格式提醒

## 4. 验证

- [x] 4.1 启动后端，确认 glob_search 和 grep_search 工具已注册且可正常调用
- [x] 4.2 验证 read_file 能直接使用搜索结果路径（lstrip("./") 自动处理）
- [x] 4.3 验证搜索结果在预算范围内被正确截断
