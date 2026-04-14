## 1. Phase 0：验证期（spike）

- [x] 1.1 验证 `AsyncSqliteSaver` 的 thread 级消息恢复：仅传当前 user message，不传历史，通过 `thread_id` 恢复后检查 messages 是否完整
- [x] 1.2 验证恢复后的消息列表是否包含 SummarizationMiddleware 修改后的结果（摘要消息、RemoveMessage 效果）
- [x] 1.3 验证恢复后的消息是否与应用层传入的历史产生重复注入
- [x] 1.4 检查 `AsyncSqliteSaver` 源码，确认是否存在 thread 级历史读取接口（如 `alist`、`aget_tuple`）和 thread 级删除接口
- [x] 1.5 编写最小 `checkpoint → history DTO` 原型，验证 assistant 分段、tool_calls 挂接、仅工具调用无文本等场景
- [x] 1.6 明确 `"default"` 会话与懒创建语义：记录当前行为，决定保留或移除策略
- [x] 1.7 验证流式中断（GeneratorExit/Exception）时的 checkpoint 状态：是否产生有效快照
- [x] 1.8 汇总验证结论，形成可执行决策文档。**终止门控**：以下任一条件不满足则提案整体中止，不进入 Phase 1-5：(a) checkpoint 通过 thread_id 能完整恢复消息；(b) 恢复后消息无重复注入；(c) 存在 thread 级历史读取接口

> **⚠️ Phase 1-5 均以 Phase 0 验证通过为前提。如果 Phase 0 结论为"checkpoint 消息恢复不可靠"，则后续 Phase 不执行，需要重新评估迁移方案或继续依赖 session_manager。**

## 2. Phase 1：元数据抽离

> **前提**：Phase 0 验证通过。此外，建议在 `unify-auxiliary-model` 提案完成后再开始本 Phase，以减少 `backend/api/chat.py`、`backend/api/sessions.py` 的合并冲突。

- [x] 2.1 在 `checkpoints.sqlite` 中新增 `sessions` 表（session_id, title, created_at, updated_at, deleted_at）和 `idx_sessions_updated_at` 索引
- [x] 2.2 实现 `SessionRepository` 类：create/list/get/rename/touch/soft_delete/bootstrap_if_missing 方法
- [x] 2.3 在 `backend/graph/agent.py` 的 `_ensure_checkpointer()` 中初始化 `sessions` 表（CREATE TABLE IF NOT EXISTS）
- [x] 2.4 修改 `backend/api/sessions.py` 的 `list_sessions` 和 `create_session` 切到 `SessionRepository`
- [x] 2.5 修改 `backend/api/sessions.py` 的 `rename_session` 和 `delete_session` 切到 `SessionRepository`
- [x] 2.6 在 `backend/api/chat.py` 的 `event_generator()` 开头调用 `SessionRepository.touch(session_id)`
- [x] 2.7 实现 `bootstrap_if_missing`：在 `/api/chat` 请求时自动创建不存在的会话元数据
- [x] 2.8 编写 `SessionRepository` 单元测试，覆盖 CRUD 和 bootstrap 场景
- [x] 2.9 验证会话列表不再依赖扫描 JSON 文件

## 3. Phase 2：历史读取实验迁移

- [x] 3.1 实现 `CheckpointHistoryService`：从 checkpoint 读取最新 state，转换为 UI DTO（role/content/tool_calls）
- [x] 3.2 处理 AIMessage 的 tool_calls 挂接：将 ToolMessage 的内容作为对应 tool_call 的 output
- [x] 3.3 处理连续 assistant 消息：作为独立 DTO 对象输出，与前端 new_response 分段语义一致
- [x] 3.4 处理仅工具调用无文本的 assistant 消息：保留空 content + tool_calls
- [x] 3.5 实现 `CheckpointDebugViewService`：组装 system_prompt + 投影消息列表，标注 `is_approximation`
- [x] 3.6 在 `backend/config.json` 的 `features` 段新增 `checkpoint_history_read` 配置项（默认 false）
- [x] 3.7 修改 `backend/api/sessions.py` 的 `get_session_history` 和 `get_raw_messages`：根据 flag 切换数据源
- [x] 3.8 编写投影层单元测试：覆盖基本对话、工具调用挂接、连续 assistant、仅工具调用等场景
- [x] 3.9 灰度对比测试：开启 flag 后对比 JSON 与 checkpoint 投影结果，记录差异

## 4. Phase 3：Agent 输入迁移

- [x] 4.1 在 `backend/config.json` 的 `features` 段新增 `checkpoint_agent_input` 配置项（默认 false）
- [x] 4.2 修改 `backend/graph/agent.py` 的 `astream()`：当 flag 开启时不传历史消息，仅传当前 user message
- [x] 4.3 修改 `backend/graph/agent.py` 的 `ainvoke()`：同样支持 flag 控制
- [x] 4.4 验证 TaskState 恢复：`_read_task_state()` 在不传历史时仍能正确读取
- [x] 4.5 验证 SummarizationMiddleware 在 checkpoint 恢复的消息上正常工作
- [x] 4.6 验证多轮对话上下文正常延续，无消息重复注入
- [x] 4.7 保留 JSON 写入（`save_message()`），便于回滚和结果对比

## 5. Phase 4：停止 JSON 消息双写

- [x] 5.1 在 `backend/config.json` 的 `features` 段新增 `session_json_write_enabled` 配置项（默认 true）
- [x] 5.2 修改 `backend/api/chat.py` 的 `event_generator()`：根据 flag 决定是否调用 `save_message()`
- [x] 5.3 处理流式中断场景：根据 Phase 0 对中断时 checkpoint 状态的验证结果，从 spec 定义的三个策略（依赖 checkpoint 快照 / 独立暂存机制 / 放弃部分内容保存）中选择一种实现
- [x] 5.4 修改 `backend/api/tokens.py`：切到 checkpoint projection 进行 token 统计
- [x] 5.5 端到端验证：新会话不再生成 JSON 文件，前端行为无明显回归

## 6. Phase 5：清理兼容逻辑

- [x] 6.1 删除 `session_manager.load_session_for_agent()` 的运行时调用
- [x] 6.2 删除 `session_manager.save_message()` 的运行时调用
- [x] 6.3 评估 `compress.py` 与 `compressed_context`：确认 SummarizationMiddleware 完全接管后清理相关代码
- [x] 6.4 实现 clear 的 checkpoint 侧清理：根据 Phase 0 验证结果选择线程清理或 thread_revision 策略
- [x] 6.5 实现 delete 的 checkpoint 侧清理：软删除 + 延后物理删除（如需要）
- [x] 6.6 清理 `session_manager.py` 中的消息读写方法，保留仅元数据管理能力
- [x] 6.7 明确 Raw Messages 的长期定义：保留为"近似调试视图"或升级为"真实执行载荷视图"
- [x] 6.8 最终回归测试：所有 API 端点正常、前端展示无回归、TaskState 正常、中间件正常
