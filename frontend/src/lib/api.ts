/**
 * API client for mini OpenClaw backend.
 * Custom SSE parser for POST requests (native EventSource only supports GET).
 */

// 从环境变量读取后端端口，默认为 8002
const BACKEND_PORT = process.env.NEXT_PUBLIC_BACKEND_PORT || "8002";

export const API_BASE =
  typeof window !== "undefined"
    ? `http://${window.location.hostname}:${BACKEND_PORT}/api`
    : `http://localhost:${BACKEND_PORT}/api`;

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

/**
 * Stream chat messages via POST SSE.
 * Yields parsed SSE events as they arrive.
 */
export async function* streamChat(
  message: string,
  sessionId: string,
  signal?: AbortSignal
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId, stream: true }),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Chat API error: ${response.status}`);
  }

  yield* parseSSEStream(response);
}

/**
 * Parse SSE events from a fetch Response.
 * Shared by streamChat, approveTool, rejectTool.
 */
async function* parseSSEStream(
  response: Response
): AsyncGenerator<SSEEvent> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      if (buffer.trim()) {
        const remaining = buffer.split("\n");
        let currentEvent = "message";
        for (const line of remaining) {
          if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            const dataStr = line.slice(5).trim();
            if (dataStr) {
              try {
                const data = JSON.parse(dataStr);
                yield { event: currentEvent, data };
              } catch {
                // Skip malformed JSON
              }
            }
          }
          if (line === "") {
            currentEvent = "message";
          }
        }
      }
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    let currentEvent = "message";

    for (const line of lines) {
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const dataStr = line.slice(5).trim();
        if (dataStr) {
          try {
            const data = JSON.parse(dataStr);
            yield { event: currentEvent, data };
          } catch {
            // Skip malformed JSON
          }
        }
      }
      if (line === "") {
        currentEvent = "message";
      }
    }
  }
}

/**
 * Approve a tool call — resumes agent from checkpoint, returns SSE stream.
 */
export async function* approveTool(
  sessionId: string,
  toolCallId: string
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_BASE}/chat/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, tool_call_id: toolCallId }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Approve failed (${response.status}): ${body}`);
  }

  yield* parseSSEStream(response);
}

/**
 * Reject a tool call — injects rejection message and resumes agent, returns SSE stream.
 */
export async function* rejectTool(
  sessionId: string,
  toolCallId: string
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_BASE}/chat/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, tool_call_id: toolCallId }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Reject failed (${response.status}): ${body}`);
  }

  yield* parseSSEStream(response);
}

/**
 * Read a file from the backend.
 */
export async function readFile(path: string): Promise<string> {
  const resp = await fetch(`${API_BASE}/files?path=${encodeURIComponent(path)}`);
  if (!resp.ok) throw new Error(`Failed to read file: ${resp.status}`);
  const data = await resp.json();
  return data.content;
}

/**
 * Save a file to the backend.
 */
export async function saveFile(path: string, content: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/files`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  if (!resp.ok) throw new Error(`Failed to save file: ${resp.status}`);
}

/**
 * List all sessions.
 */
export async function listSessions(): Promise<
  Array<{ id: string; title: string; updated_at: number }>
> {
  const resp = await fetch(`${API_BASE}/sessions`);
  if (!resp.ok) throw new Error(`Failed to list sessions: ${resp.status}`);
  const data = await resp.json();
  return data.sessions;
}

/**
 * Create a new session.
 */
export async function createSession(): Promise<{ id: string; title: string }> {
  const resp = await fetch(`${API_BASE}/sessions`, { method: "POST" });
  if (!resp.ok) throw new Error(`Failed to create session: ${resp.status}`);
  return resp.json();
}

/**
 * Rename a session.
 */
export async function renameSession(id: string, title: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!resp.ok) throw new Error(`Failed to rename session: ${resp.status}`);
}

/**
 * Delete a session.
 */
export async function deleteSession(id: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!resp.ok) throw new Error(`Failed to delete session: ${resp.status}`);
}

/**
 * Get raw messages for a session (including system prompt).
 */
export async function getRawMessages(
  sessionId: string
): Promise<{ session_id: string; title: string; messages: Array<{ role: string; content: string }> }> {
  const resp = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/messages`
  );
  if (!resp.ok) throw new Error(`Failed to get raw messages: ${resp.status}`);
  return resp.json();
}

/**
 * Get session conversation history (no system prompt, includes tool_calls).
 */
export async function getSessionHistory(
  sessionId: string
): Promise<{
  session_id: string;
  messages: Array<{ role: string; content: string; tool_calls?: Array<{ tool: string; input?: string; output?: string }> }>;
}> {
  const resp = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/history`
  );
  if (!resp.ok) throw new Error(`Failed to get session history: ${resp.status}`);
  return resp.json();
}

/**
 * List available skills.
 */
export async function listSkills(): Promise<
  Array<{ name: string; path: string; description: string }>
> {
  const resp = await fetch(`${API_BASE}/skills`);
  if (!resp.ok) throw new Error(`Failed to list skills: ${resp.status}`);
  const data = await resp.json();
  return data.skills;
}

/**
 * Load a skill into the current session.
 */
export async function loadSkill(skillName: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/skills/load`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skill_name: skillName }),
  });
  if (!resp.ok) throw new Error(`Failed to load skill: ${resp.status}`);
}

/**
 * Generate a title for a session using AI.
 */
export async function generateTitle(
  sessionId: string
): Promise<{ title: string }> {
  const resp = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/generate-title`,
    { method: "POST" }
  );
  if (!resp.ok) throw new Error(`Failed to generate title: ${resp.status}`);
  return resp.json();
}

/**
 * Get token count for a session (system + messages).
 */
export async function getSessionTokenCount(
  sessionId: string
): Promise<{ system_tokens: number; message_tokens: number; total_tokens: number }> {
  const resp = await fetch(
    `${API_BASE}/tokens/session/${encodeURIComponent(sessionId)}`
  );
  if (!resp.ok) throw new Error(`Failed to get token count: ${resp.status}`);
  return resp.json();
}

/**
 * Get token counts for a list of files.
 */
export async function getFileTokenCounts(
  paths: string[]
): Promise<{ files: Array<{ path: string; tokens: number }> }> {
  const resp = await fetch(`${API_BASE}/tokens/files`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  if (!resp.ok) throw new Error(`Failed to get file token counts: ${resp.status}`);
  return resp.json();
}

/**
 * Compress a session's conversation history.
 * @deprecated 使用 summarizeSession 替代（基于 checkpoint 摘要）
 */
export async function compressSession(
  sessionId: string
): Promise<{ archived_count: number; remaining_count: number }> {
  const resp = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/compress`,
    { method: "POST" }
  );
  if (!resp.ok) throw new Error(`Failed to compress session: ${resp.status}`);
  return resp.json();
}

/**
 * Summarize early messages in a session via checkpoint (keeps latest 10).
 */
export async function summarizeSession(
  sessionId: string
): Promise<{ summarized: boolean; summarized_count: number; preserved_count: number }> {
  const resp = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/summarize`,
    { method: "POST" }
  );
  if (!resp.ok) throw new Error(`Failed to summarize session: ${resp.status}`);
  return resp.json();
}

/**
 * Clear all messages in a session (like Claude Code /clear).
 */
export async function clearSession(
  sessionId: string
): Promise<{ status: string; session_id: string }> {
  const resp = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/clear`,
    { method: "POST" }
  );
  if (!resp.ok) throw new Error(`Failed to clear session: ${resp.status}`);
  return resp.json();
}

/**
 * Get current RAG mode status.
 */
export async function getRagMode(): Promise<{ rag_mode: boolean }> {
  const resp = await fetch(`${API_BASE}/config/rag-mode`);
  if (!resp.ok) throw new Error(`Failed to get RAG mode: ${resp.status}`);
  return resp.json();
}

/**
 * Set RAG mode enabled/disabled.
 */
export async function setRagMode(
  enabled: boolean
): Promise<{ rag_mode: boolean }> {
  const resp = await fetch(`${API_BASE}/config/rag-mode`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!resp.ok) throw new Error(`Failed to set RAG mode: ${resp.status}`);
  return resp.json();
}

/**
 * 从 checkpoint 恢复 TaskState（用于页面刷新/会话切换时恢复）。
 */

export interface TaskStateResponse {
  task_state: {
    session_id: string;
    goal: string;
    steps: Array<{
      description: string;
      status: string;
      result_summary?: string;
    }>;
    artifacts: string[];
    decisions: string[];
    blockers: string[];
  } | null;
}

export async function fetchTaskState(
  sessionId: string
): Promise<TaskStateResponse> {
  const resp = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/task-state`
  );
  if (!resp.ok) throw new Error(`Failed to fetch task state: ${resp.status}`);
  return resp.json();
}
