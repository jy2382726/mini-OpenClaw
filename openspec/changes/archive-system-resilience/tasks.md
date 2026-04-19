## 1. 归档文件名格式变更与 session_id 获取

- [ ] 1.1 验证 `abefore_model(state, runtime)` 中 runtime 参数是否包含 `config.configurable.thread_id`，确认 session_id 获取路径可行（`backend/graph/middleware.py`）
- [ ] 1.2 修改 `_archive_output(content, tool_name)` → `_archive_output(content, tool_name, session_id)`，文件名改为 `tool_{tool_name}_{session_id}_{timestamp}.txt`（`backend/graph/middleware.py:116-134`）
- [ ] 1.3 修改 `abefore_model` 中调用 `_archive_output` 的位置，从 runtime config 提取 session_id 并传入（`backend/graph/middleware.py:172-174`）

## 2. 归档写入降级保护

- [ ] 2.1 在 `_archive_output()` 中包裹 try/except（OSError/PermissionError/IOError），捕获时 log.warning 并返回纯截断结果（不包含归档路径）（`backend/graph/middleware.py:116-134`）

## 3. 归档文件级联清理

- [ ] 3.1 在 `sessions.py` 或独立工具模块中新增 `_cleanup_session_archives(session_id)` 函数，用 `glob("tool_*_{session_id}_*.txt")` 批量删除匹配文件，清理失败时 log.warning（`backend/api/sessions.py`）
- [ ] 3.2 在 `delete_session()` 中调用 `_cleanup_session_archives(session_id)`，位于 `adelete_thread` 之后（`backend/api/sessions.py:57-71`）
- [ ] 3.3 在 `clear_session_messages()` 中调用 `_cleanup_session_archives(session_id)`，位于 `adelete_thread` 之后（`backend/api/sessions.py:159-169`）

## 4. 归档文件过期清理（GC）

- [ ] 4.1 新增 `_gc_expired_archives(max_age_days=7)` 函数，遍历 `sessions/archive/` 下所有文件，删除 `st_mtime` 超过 7 天的文件，同时处理新旧两种格式（`backend/graph/middleware.py` 或 `backend/app.py`）
- [ ] 4.2 在 `app.py` 的 `lifespan()` 启动阶段调用 `_gc_expired_archives()`（`backend/app.py`）

## 5. 测试更新

- [ ] 5.1 更新 `test_middleware.py` 中 `test_archive_large_output`、`test_archive_priority_over_truncation`、`test_archive_file_content_recoverable` 等测试的文件名断言，适配新格式（`backend/tests/test_middleware.py`）
- [ ] 5.2 新增测试：`_archive_output()` 写入失败时降级为纯截断（mock write_text 抛出 OSError）
- [ ] 5.3 新增测试：`_cleanup_session_archives()` 正确删除指定 session_id 的归档文件，不删除其他 session 的文件
- [ ] 5.4 新增测试：`_gc_expired_archives()` 删除超期文件、保留未超期文件

## 验证

- [ ] 5.5 运行 `pytest backend/tests/test_middleware.py -v` 确认所有测试通过
- [ ] 5.6 启动应用，验证归档文件名格式正确、delete/clear 后归档文件被清理
