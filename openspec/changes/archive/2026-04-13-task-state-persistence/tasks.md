## 1. thread_id 传递与 checkpoint 激活

- [x] 1.1 在 `backend/graph/agent.py` 的 `agent.astream()` 调用中添加 `config={"configurable": {"thread_id": session_id}}` 参数，激活 InMemorySaver checkpointer
- [x] 1.2 在 `backend/graph/agent.py` 的 `agent.ainvoke()` 调用中添加相同的 config 参数
- [x] 1.3 测试：验证 config 参数正确传递，agent 可正常构建和调用

## 2. TaskState 写入 AgentCustomState

- [x] 2.1 在 `backend/graph/agent.py` 的 astream 方法中，将创建/恢复的 TaskState 写入消息前缀之前，通过 state 注入方式传递给 agent（而非仅格式化为 Markdown）
- [x] 2.2 实现 TaskState 恢复逻辑：从 agent 的 checkpoint state 中读取 `task_state` 字段，若存在则使用恢复的 TaskState 而非创建新的
- [x] 2.3 实现新任务追加逻辑：当已有活跃 TaskState 且新消息包含任务性动词时，追加步骤而非覆盖
- [x] 2.4 测试：验证 TaskState 写入 state、从 state 恢复、新任务追加步骤

## 3. update_task 工具

- [x] 3.1 在 `backend/graph/task_state.py` 中实现 `update_task` 工具函数，支持 add_step、update_step、add_artifact、add_blocker、add_decision 五种操作
- [x] 3.2 在 `backend/tools/` 中注册 `update_task` 工具，使其可被 Agent 调用
- [x] 3.3 工具执行后将更新写入 TaskState 并触发持久化
- [x] 3.4 测试：验证每种操作的正确性、边界条件（无效 step_index、空 description 等）

## 4. 任务状态更新指引注入

- [x] 4.1 在 `backend/graph/prompt_builder.py` 的 `build_dynamic_prefix()` 中，当存在活跃 TaskState 且包含 in_progress 步骤时，追加 update_task 工具使用指引
- [x] 4.2 指引内容包含：可用的 action 列表和简要用法说明
- [x] 4.3 测试：验证有活跃任务时指引注入、无活跃任务时不注入

## 5. 集成验证

- [x] 5.1 端到端测试：模拟多轮对话，验证 TaskState 跨请求持久化和恢复
- [x] 5.2 验证 update_task 工具调用后 TaskState 正确更新
- [x] 5.3 验证 SSE 事件流中 update_task 工具调用正常返回 tool_start/tool_end 事件
