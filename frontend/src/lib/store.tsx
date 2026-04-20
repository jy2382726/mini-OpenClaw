"use client";

import React, {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
} from "react";
import {
  streamChat,
  listSessions as apiListSessions,
  createSession as apiCreateSession,
  renameSession as apiRenameSession,
  deleteSession as apiDeleteSession,
  getRawMessages as apiGetRawMessages,
  getSessionHistory as apiGetSessionHistory,
  summarizeSession as apiSummarizeSession,
  clearSession as apiClearSession,
  getRagMode as apiGetRagMode,
  setRagMode as apiSetRagMode,
  loadSkill as apiLoadSkill,
  fetchTaskState as apiFetchTaskState,
  getSessionTokenCount,
} from "./api";

// ── Types ──────────────────────────────────────────────────

export interface ToolCall {
  tool: string;
  input?: string;
  output?: string;
  status: "running" | "done" | "pending_approval";
  toolCallId?: string;
  timeoutSeconds?: number;
}

export interface RetrievalResult {
  text: string;
  score: string;
  source: string;
}

export interface Mem0RetrievalResult {
  text: string;
  score: string;
  source: string;
  memory_type?: string;
  id?: string;
  why?: string;
  how_to_apply?: string;
  created_at?: string;
  confidence?: number;
  freshness?: string;
}

export interface TaskStep {
  description: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
  result_summary?: string;
}

export interface TaskState {
  session_id: string;
  goal: string;
  steps: TaskStep[];
  artifacts: string[];
  decisions: string[];
  blockers: string[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCall[];
  retrievals?: RetrievalResult[];
  mem0Retrievals?: Mem0RetrievalResult[];
  timestamp: number;
}

export interface SessionMeta {
  id: string;
  title: string;
  updated_at: number;
}

export interface RawMessage {
  role: string;
  content: string;
}

interface AppState {
  // Chat
  messages: ChatMessage[];
  isStreaming: boolean;
  sendMessage: (text: string) => Promise<void>;
  stopStreaming: () => void;

  // Sessions
  sessionId: string;
  setSessionId: (id: string) => void;
  sessions: SessionMeta[];
  loadSessions: () => void;
  createSession: () => Promise<void>;
  renameSession: (id: string, title: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;

  // Sidebar
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  toggleSidebar: () => void;

  // Inspector (Monaco editor)
  inspectorFile: string | null;
  setInspectorFile: (path: string | null) => void;
  inspectorOpen: boolean;
  setInspectorOpen: (open: boolean) => void;
  toggleInspector: () => void;

  // Right panel tab
  rightTab: "memory" | "skills";
  setRightTab: (tab: "memory" | "skills") => void;

  // Raw messages
  rawMessages: RawMessage[] | null;
  loadRawMessages: () => void;

  // Expanded file (editor full-panel mode)
  expandedFile: boolean;
  setExpandedFile: (v: boolean) => void;

  // Context usage
  contextUsage: { ratio: number; totalTokens: number; contextWindow: number } | null;

  // Panel widths
  sidebarWidth: number;
  setSidebarWidth: (w: number | ((prev: number) => number)) => void;
  inspectorWidth: number;
  setInspectorWidth: (w: number | ((prev: number) => number)) => void;

  // Compression
  isCompressing: boolean;
  compressCurrentSession: () => Promise<void>;

  // Clear
  clearCurrentSession: () => Promise<void>;

  // RAG mode
  ragMode: boolean;
  toggleRagMode: () => void;

  // TaskState
  currentTaskState: TaskState | null;
  taskTriggerMsgId: string | null;

  // HITL
  approveToolCall: (messageId: string, toolCallId: string) => Promise<void>;
  rejectToolCall: (messageId: string, toolCallId: string) => Promise<void>;
}

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionIdRaw] = useState("default");
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [inspectorFile, setInspectorFileRaw] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [rightTab, setRightTab] = useState<"memory" | "skills">("memory");
  const [rawMessages, setRawMessages] = useState<RawMessage[] | null>(null);
  const [expandedFile, setExpandedFile] = useState(false);
  const [contextUsage, setContextUsage] = useState<{ ratio: number; totalTokens: number; contextWindow: number } | null>(null);
  const [sidebarWidth, setSidebarWidth] = useState(260);
  const [inspectorWidth, setInspectorWidth] = useState(360);
  const [isCompressing, setIsCompressing] = useState(false);
  const [ragMode, setRagMode] = useState(false);
  const [currentTaskState, setCurrentTaskState] = useState<TaskState | null>(null);
  const [taskTriggerMsgId, setTaskTriggerMsgId] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentUserMsgIdRef = useRef<string | null>(null);
  const taskTriggerSetRef = useRef(false);

  // Load RAG mode on mount
  useEffect(() => {
    apiGetRagMode()
      .then((data) => setRagMode(data.rag_mode))
      .catch(() => {});
  }, []);

  const toggleSidebar = useCallback(() => setSidebarOpen((v) => !v), []);
  const toggleInspector = useCallback(() => setInspectorOpen((v) => !v), []);

  // When a file is selected, auto-open the inspector
  const setInspectorFile = useCallback((path: string | null) => {
    setInspectorFileRaw(path);
    if (path) setInspectorOpen(true);
  }, []);

  // ── Session management ─────────────────────────────

  const loadSessions = useCallback(() => {
    apiListSessions()
      .then((list) => setSessions(list))
      .catch(() => {});
  }, []);

  // Load sessions on mount
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Context usage: fetch after streaming ends or session/message changes
  useEffect(() => {
    if (isStreaming || messages.length === 0) {
      if (messages.length === 0) setContextUsage(null);
      return;
    }
    getSessionTokenCount(sessionId)
      .then((data) =>
        setContextUsage({
          ratio: data.usage_ratio,
          totalTokens: data.total_tokens,
          contextWindow: data.context_window,
        })
      )
      .catch(() => setContextUsage(null));
  }, [sessionId, messages.length, isStreaming]);

  const setSessionId = useCallback(
    (id: string) => {
      setSessionIdRaw(id);
      setMessages([]);
      setRawMessages(null);
      setCurrentTaskState(null);
      setTaskTriggerMsgId(null);
      taskTriggerSetRef.current = false;

      // Load existing conversation history from backend
      apiGetSessionHistory(id)
        .then((data) => {
          if (data.messages && data.messages.length > 0) {
            const loaded: ChatMessage[] = [];
            let msgIndex = 0;
            for (const msg of data.messages) {
              if (msg.role === "user") {
                loaded.push({
                  id: `hist-user-${msgIndex++}`,
                  role: "user",
                  content: msg.content,
                  timestamp: Date.now() - (data.messages.length - msgIndex) * 1000,
                });
              } else if (msg.role === "assistant") {
                const toolCalls: ToolCall[] = (msg.tool_calls || []).map(
                  (tc: { tool: string; input?: string; output?: string }) => ({
                    tool: tc.tool,
                    input: tc.input || "",
                    output: tc.output || "",
                    status: "done" as const,
                  })
                );
                loaded.push({
                  id: `hist-asst-${msgIndex++}`,
                  role: "assistant",
                  content: msg.content,
                  toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
                  timestamp: Date.now() - (data.messages.length - msgIndex) * 1000,
                });
              }
            }
            setMessages(loaded);

            // 有对话历史时，尝试恢复 TaskState
            apiFetchTaskState(id)
              .then((res) => {
                if (res.task_state) {
                  setCurrentTaskState(res.task_state as TaskState);
                }
              })
              .catch(() => {});
          }
        })
        .catch(() => {
          // Session might not exist yet, that's OK
        });
    },
    []
  );

  const createSession = useCallback(async () => {
    try {
      const meta = await apiCreateSession();
      setSessions((prev) => [{ id: meta.id, title: meta.title, updated_at: Date.now() / 1000 }, ...prev]);
      setSessionId(meta.id);
    } catch {
      // ignore
    }
  }, [setSessionId]);

  const renameSessionFn = useCallback(async (id: string, title: string) => {
    try {
      await apiRenameSession(id, title);
      setSessions((prev) =>
        prev.map((s) => (s.id === id ? { ...s, title } : s))
      );
    } catch {
      // ignore
    }
  }, []);

  const deleteSessionFn = useCallback(
    async (id: string) => {
      try {
        await apiDeleteSession(id);
        setSessions((prev) => prev.filter((s) => s.id !== id));
        if (sessionId === id) {
          setSessionId("default");
        }
      } catch {
        // ignore
      }
    },
    [sessionId, setSessionId]
  );

  const loadRawMessages = useCallback(() => {
    if (!sessionId) return;
    apiGetRawMessages(sessionId)
      .then((data) => setRawMessages(data.messages))
      .catch(() => setRawMessages(null));
  }, [sessionId]);

  // ── Compression ──────────────────────────────────────

  const compressCurrentSession = useCallback(async () => {
    if (isCompressing) return;
    setIsCompressing(true);
    try {
      await apiSummarizeSession(sessionId);
      // Refresh raw messages and chat history after summarize
      loadRawMessages();
      const data = await apiGetSessionHistory(sessionId);
      if (data.messages && data.messages.length > 0) {
        const loaded: ChatMessage[] = [];
        let msgIndex = 0;
        for (const msg of data.messages) {
          if (msg.role === "user") {
            loaded.push({
              id: `hist-user-${msgIndex++}`,
              role: "user",
              content: msg.content,
              timestamp: Date.now() - (data.messages.length - msgIndex) * 1000,
            });
          } else if (msg.role === "assistant") {
            const toolCalls: ToolCall[] = (msg.tool_calls || []).map(
              (tc: { tool: string; input?: string; output?: string }) => ({
                tool: tc.tool,
                input: tc.input || "",
                output: tc.output || "",
                status: "done" as const,
              })
            );
            loaded.push({
              id: `hist-asst-${msgIndex++}`,
              role: "assistant",
              content: msg.content,
              toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
              timestamp: Date.now() - (data.messages.length - msgIndex) * 1000,
            });
          }
        }
        setMessages(loaded);
      } else {
        setMessages([]);
      }
    } finally {
      setIsCompressing(false);
    }
  }, [isCompressing, sessionId, loadRawMessages]);

  // ── RAG mode ────────────────────────────────────────

  const toggleRagMode = useCallback(() => {
    const newMode = !ragMode;
    setRagMode(newMode);
    apiSetRagMode(newMode).catch(() => setRagMode(ragMode));
  }, [ragMode]);

  // ── Clear session ───────────────────────────────────

  const clearCurrentSession = useCallback(async () => {
    if (isCompressing) return;
    setIsCompressing(true);
    try {
      await apiClearSession(sessionId);
      // Clear frontend messages immediately
      setMessages([]);
      setRawMessages(null);
      setCurrentTaskState(null);
      setTaskTriggerMsgId(null);
      taskTriggerSetRef.current = false;
    } catch {
      // ignore
    } finally {
      setIsCompressing(false);
    }
  }, [isCompressing, sessionId]);

  // ── Stop streaming ─────────────────────────────────

  const stopStreaming = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  // ── Send message ───────────────────────────────────

  // Ref to track the current assistant message ID during streaming
  const currentAssistantIdRef = useRef("");

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming || isCompressing) return;

      // 斜杠命令处理：检测消息中任意位置的 /skill-name 并加载
      // 用 split + filter 替代 lookbehind 正则，避免 Safari < 16.4 兼容问题
      let processedText = text;
      const tokens = text.split(/(\s+)/); // 保留分隔符用于重组
      const skillNames: string[] = [];
      for (const token of tokens) {
        if (token.startsWith("/") && token.length > 1 && !/\s/.test(token)) {
          skillNames.push(token.slice(1));
        }
      }
      if (skillNames.length > 0) {
        await Promise.allSettled(skillNames.map((name) => apiLoadSkill(name)));
        // 将 /skill-name token 替换为 [使用技能: skill-name]
        processedText = tokens
          .map((t) => {
            if (t.startsWith("/") && t.length > 1 && !/\s/.test(t)) {
              return `[使用技能: ${t.slice(1)}]`;
            }
            return t;
          })
          .join("");
        if (!processedText.replace(/\[使用技能:\s*[^\]]+\]/g, "").trim()) {
          processedText += " 请执行该技能的默认操作";
        }
      }

      const userMsg: ChatMessage = {
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
        timestamp: Date.now(),
      };
      currentUserMsgIdRef.current = userMsg.id;
      // 新请求始终允许重新设置触发消息（后端会判断旧任务是否已完成并创建新任务）
      taskTriggerSetRef.current = false;
      setTaskTriggerMsgId(null);

      const firstAssistantId = `assistant-${Date.now()}`;
      const assistantMsg: ChatMessage = {
        id: firstAssistantId,
        role: "assistant",
        content: "",
        toolCalls: [],
        timestamp: Date.now(),
      };

      currentAssistantIdRef.current = firstAssistantId;
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsStreaming(true);

      const controller = new AbortController();
      abortControllerRef.current = controller;

      try {
        for await (const event of streamChat(processedText, sessionId, controller.signal)) {
          if (controller.signal.aborted) break;

          // Handle retrieval event (RAG mode) — 分流 mem0 和 legacy 结果
          if (event.event === "retrieval") {
            const targetId = currentAssistantIdRef.current;
            const retrievalData = event.data as {
              query: string;
              results: Array<Record<string, unknown>>;
            };
            // 按 source/memory_type 分流到不同的展示组件
            const mem0Results = retrievalData.results.filter(
              (r: Record<string, unknown>) => r.source === "mem0" || r.memory_type
            ) as unknown as Mem0RetrievalResult[];
            const legacyResults = retrievalData.results.filter(
              (r: Record<string, unknown>) => r.source !== "mem0" && !r.memory_type
            ) as unknown as RetrievalResult[];

            setMessages((prev) => {
              const updated = [...prev];
              const idx = updated.findIndex((m) => m.id === targetId);
              if (idx === -1) return prev;
              updated[idx] = {
                ...updated[idx],
                retrievals: legacyResults.length > 0 ? legacyResults : updated[idx].retrievals,
                mem0Retrievals: mem0Results.length > 0 ? mem0Results : updated[idx].mem0Retrievals,
              };
              return updated;
            });
            continue;
          }

          // Handle title event (auto-generated after first message)
          if (event.event === "title") {
            const titleData = event.data as { session_id: string; title: string };
            setSessions((prev) =>
              prev.map((s) =>
                s.id === titleData.session_id
                  ? { ...s, title: titleData.title }
                  : s
              )
            );
            continue;
          }

          // Handle task_update event — 实时更新 TaskState
          if (event.event === "task_update") {
            const taskData = event.data as { task_state: TaskState };
            if (taskData.task_state) {
              setCurrentTaskState(taskData.task_state);
              // 首次收到 task_update 时，记录触发任务的用户消息 ID
              if (!taskTriggerSetRef.current && currentUserMsgIdRef.current) {
                setTaskTriggerMsgId(currentUserMsgIdRef.current);
                taskTriggerSetRef.current = true;
              }
            }
            continue;
          }

          // Handle new_response — create a new assistant bubble
          if (event.event === "new_response") {
            const newId = `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
            currentAssistantIdRef.current = newId;
            setMessages((prev) => [
              ...prev,
              {
                id: newId,
                role: "assistant",
                content: "",
                toolCalls: [],
                timestamp: Date.now(),
              },
            ]);
            continue;
          }

          // Handle tool_approval — mark tools as pending_approval
          if (event.event === "tool_approval") {
            const targetId = currentAssistantIdRef.current;
            const approvalData = event.data as {
              pending_tools: Array<{ tool_call_id: string; tool: string; input: unknown }>;
              session_id: string;
              timeout_seconds: number;
            };
            const timeout = approvalData.timeout_seconds || 30;
            setMessages((prev) => {
              const updated = [...prev];
              const idx = updated.findIndex((m) => m.id === targetId);
              if (idx === -1) return prev;
              const msg = { ...updated[idx] };
              const newCalls: ToolCall[] = approvalData.pending_tools.map((pt) => ({
                tool: pt.tool,
                input: typeof pt.input === "string" ? pt.input : JSON.stringify(pt.input),
                status: "pending_approval" as const,
                toolCallId: pt.tool_call_id,
                timeoutSeconds: timeout,
              }));
              msg.toolCalls = [...(msg.toolCalls || []), ...newCalls];
              updated[idx] = msg;
              return updated;
            });
            continue;
          }

          const targetId = currentAssistantIdRef.current;

          setMessages((prev) => {
            const updated = [...prev];
            const idx = updated.findIndex((m) => m.id === targetId);
            if (idx === -1) return prev;
            const msg = { ...updated[idx] };

            switch (event.event) {
              case "token":
                msg.content += (event.data.content as string) || "";
                break;

              case "tool_start":
                msg.toolCalls = [
                  ...(msg.toolCalls || []),
                  {
                    tool: event.data.tool as string,
                    input: event.data.input as string,
                    status: "running",
                  },
                ];
                break;

              case "tool_end": {
                const calls = [...(msg.toolCalls || [])];
                for (let i = calls.length - 1; i >= 0; i--) {
                  if (
                    calls[i].tool === event.data.tool &&
                    calls[i].status === "running"
                  ) {
                    calls[i] = {
                      ...calls[i],
                      output: event.data.output as string,
                      status: "done",
                    };
                    break;
                  }
                }
                msg.toolCalls = calls;
                break;
              }

              case "done":
                // 清理因 new_response 产生但未收到任何内容的空 assistant 消息
                if (!msg.content && (!msg.toolCalls || msg.toolCalls.length === 0)) {
                  updated.splice(idx, 1);
                }
                break;

              case "error":
                msg.content +=
                  `\n\n**Error:** ${event.data.error || "Unknown error"}`;
                break;
            }

            updated[idx] = msg;
            return updated;
          });
        }
      } catch (err) {
        // Don't show error for manual abort (user clicked stop)
        if (err instanceof DOMException && err.name === "AbortError") {
          // User stopped — append a subtle indicator
          const targetId = currentAssistantIdRef.current;
          setMessages((prev) => {
            const updated = [...prev];
            const idx = updated.findIndex((m) => m.id === targetId);
            if (idx !== -1 && updated[idx].content) {
              updated[idx] = {
                ...updated[idx],
                content: updated[idx].content + "\n\n*— 已停止生成 —*",
              };
            }
            return updated;
          });
        } else {
          const targetId = currentAssistantIdRef.current;
          setMessages((prev) => {
            const updated = [...prev];
            const idx = updated.findIndex((m) => m.id === targetId);
            if (idx !== -1) {
              const errorMsg = err instanceof Error ? err.message : "Unknown";
              const isNetworkError = errorMsg.toLowerCase().includes("network") ||
                                     errorMsg.toLowerCase().includes("fetch") ||
                                     errorMsg.toLowerCase().includes("failed");
              const userFriendlyMsg = isNetworkError
                ? "连接已断开，可能是由于网络不稳定或处理时间过长。请重试或刷新页面。"
                : `连接错误: ${errorMsg}`;
              updated[idx] = {
                ...updated[idx],
                content:
                  updated[idx].content +
                  `\n\n**⚠️ ${userFriendlyMsg}**`,
              };
            }
            return updated;
          });
        }
      } finally {
        abortControllerRef.current = null;
        setIsStreaming(false);
        loadSessions();
      }
    },
    [isStreaming, isCompressing, sessionId, loadSessions]
  );

  // ── HITL approve/reject ───────────────────────────────

  const approveToolCall = useCallback(
    async (messageId: string, toolCallId: string) => {
      // 视觉反馈：将同消息内所有 pending_approval 工具统一标记为 running
      // （interrupt_before 暂停粒度为整个 tools 节点，审批是 all-or-nothing）
      setMessages((prev) => {
        const updated = [...prev];
        const idx = updated.findIndex((m) => m.id === messageId);
        if (idx === -1) return prev;
        const msg = { ...updated[idx] };
        msg.toolCalls = (msg.toolCalls || []).map((c) =>
          c.status === "pending_approval" ? { ...c, status: "running" as const } : c
        );
        updated[idx] = msg;
        return updated;
      });

      try {
        for await (const event of (await import("./api")).approveTool(sessionId, toolCallId)) {
          setMessages((prev) => {
            const updated = [...prev];
            const idx = updated.findIndex((m) => m.id === messageId);
            if (idx === -1) return prev;
            const msg = { ...updated[idx] };

            if (event.event === "tool_end") {
              const calls = [...(msg.toolCalls || [])];
              const tc = calls.find((c) => c.toolCallId === toolCallId || c.tool === (event.data.tool as string));
              if (tc) {
                calls[calls.indexOf(tc)] = { ...tc, output: event.data.output as string, status: "done" as const };
              }
              msg.toolCalls = calls;
            } else if (event.event === "token") {
              msg.content += (event.data.content as string) || "";
            } else if (event.event === "done") {
              // 流结束
            }

            updated[idx] = msg;
            return updated;
          });
        }
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : "审批失败";
        setMessages((prev) => {
          const updated = [...prev];
          const idx = updated.findIndex((m) => m.id === messageId);
          if (idx !== -1) updated[idx] = { ...updated[idx], content: updated[idx].content + `\n\n**⚠️ ${errMsg}**` };
          return updated;
        });
      }
    },
    [sessionId]
  );

  const rejectToolCall = useCallback(
    async (messageId: string, toolCallId: string) => {
      try {
        // 视觉反馈：将同消息内所有 pending_approval 工具统一标记为 running
        setMessages((prev) => {
          const updated = [...prev];
          const idx = updated.findIndex((m) => m.id === messageId);
          if (idx === -1) return prev;
          const msg = { ...updated[idx] };
          msg.toolCalls = (msg.toolCalls || []).map((c) =>
            c.status === "pending_approval" ? { ...c, status: "running" as const } : c
          );
          updated[idx] = msg;
          return updated;
        });

        for await (const event of (await import("./api")).rejectTool(sessionId, toolCallId)) {
          setMessages((prev) => {
            const updated = [...prev];
            const idx = updated.findIndex((m) => m.id === messageId);
            if (idx === -1) return prev;
            const msg = { ...updated[idx] };

            if (event.event === "token") {
              msg.content += (event.data.content as string) || "";
            } else if (event.event === "done") {
              const calls = [...(msg.toolCalls || [])];
              const tc = calls.find((c) => c.toolCallId === toolCallId);
              if (tc) {
                calls[calls.indexOf(tc)] = { ...tc, output: "用户拒绝了此工具调用", status: "done" as const };
              }
              msg.toolCalls = calls;
            }

            updated[idx] = msg;
            return updated;
          });
        }
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : "拒绝操作失败";
        setMessages((prev) => {
          const updated = [...prev];
          const idx = updated.findIndex((m) => m.id === messageId);
          if (idx !== -1) updated[idx] = { ...updated[idx], content: updated[idx].content + `\n\n**⚠️ ${errMsg}**` };
          return updated;
        });
      }
    },
    [sessionId]
  );

  return (
    <AppContext.Provider
      value={{
        messages,
        isStreaming,
        sendMessage,
        stopStreaming,
        sessionId,
        setSessionId,
        sessions,
        loadSessions,
        createSession,
        renameSession: renameSessionFn,
        deleteSession: deleteSessionFn,
        sidebarOpen,
        setSidebarOpen,
        toggleSidebar,
        inspectorFile,
        setInspectorFile,
        inspectorOpen,
        setInspectorOpen,
        toggleInspector,
        rightTab,
        setRightTab,
        rawMessages,
        loadRawMessages,
        expandedFile,
        setExpandedFile,
        contextUsage,
        sidebarWidth,
        setSidebarWidth,
        inspectorWidth,
        setInspectorWidth,
        isCompressing,
        compressCurrentSession,
        clearCurrentSession,
        ragMode,
        toggleRagMode,
        currentTaskState,
        taskTriggerMsgId,
        approveToolCall,
        rejectToolCall,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}
