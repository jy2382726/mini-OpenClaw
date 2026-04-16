/**
 * mem0 记忆管理前端 API 客户端
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8002";

export interface Mem0Memory {
  id: string;
  memory: string;
  score?: number;
  metadata?: {
    memory_type?: string;
    why?: string;
    how_to_apply?: string;
    created_at?: string;
    confidence?: number;
    freshness?: string;
    source_session_id?: string;
  };
}

export interface Mem0Status {
  enabled: boolean;
  mode: string;
  auto_extract: boolean;
  mem0_ready: boolean;
  memory_count: number;
  buffer_pending: number;
  config: {
    buffer_size: number;
    flush_interval_seconds: number;
    stale_threshold_days: number;
    expire_threshold_days: number;
    min_confidence: number;
  };
}

export interface ConsolidationReport {
  total_memories: number;
  duplicates_found: number;
  merged: number;
  conflicts_detected: number;
  conflicts_resolved: number;
  conflicts_pending: number;
  expired: number;
  errors: string[];
}

export interface MemoryListResponse {
  total: number;
  items: Mem0Memory[];
  limit: number;
  offset: number;
}

/**
 * 获取 mem0 系统状态
 */
export async function getMem0Status(): Promise<Mem0Status> {
  const res = await fetch(`${API_BASE}/api/mem0/status`);
  if (!res.ok) throw new Error("获取 mem0 状态失败");
  return res.json();
}

/**
 * 获取记忆列表
 */
export async function getMemories(
  memoryType?: string,
  limit = 50,
  offset = 0
): Promise<MemoryListResponse> {
  const params = new URLSearchParams();
  if (memoryType) params.set("memory_type", memoryType);
  params.set("limit", String(limit));
  params.set("offset", String(offset));

  const res = await fetch(`${API_BASE}/api/mem0/memories?${params}`);
  if (!res.ok) throw new Error("获取记忆列表失败");
  return res.json();
}

/**
 * 删除单条记忆
 */
export async function deleteMemory(memoryId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/mem0/memories/${memoryId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("删除记忆失败");
}

/**
 * 批量导入记忆
 */
export async function importMemories(
  items: Array<{
    fact: string;
    memory_type: string;
    why?: string;
    how_to_apply?: string;
  }>
): Promise<{ imported: number; total: number; errors: string[] }> {
  const res = await fetch(`${API_BASE}/api/mem0/memories/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new Error("导入记忆失败");
  return res.json();
}

/**
 * 触发记忆整合
 */
export async function consolidateMemories(): Promise<{
  ok: boolean;
  report: ConsolidationReport;
}> {
  const res = await fetch(`${API_BASE}/api/mem0/consolidate`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("记忆整合失败");
  return res.json();
}

/**
 * 手动刷新缓冲区
 */
export async function flushBuffer(): Promise<{
  ok: boolean;
  flushed: number;
  message: string;
}> {
  const res = await fetch(`${API_BASE}/api/mem0/flush`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("缓冲区刷新失败");
  return res.json();
}

/**
 * 更新 mem0 设置
 */
export async function updateMem0Settings(updates: Record<string, unknown>): Promise<void> {
  const res = await fetch(`${API_BASE}/api/mem0/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error("更新设置失败");
}
