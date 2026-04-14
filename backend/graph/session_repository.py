"""SessionRepository — 会话元数据的 SQLite 持久化层。

将 session 元数据（标题、时间戳、软删除标记）从 JSON 文件迁移到
checkpoints.sqlite 中的 sessions 业务表。

职责：
- create: 创建新会话元数据
- list: 列出所有未删除的会话（按 updated_at 降序）
- get: 获取单个会话元数据
- rename: 更新标题
- touch: 更新 updated_at（每次 /api/chat 请求时调用）
- soft_delete: 标记为已删除
- bootstrap_if_missing: 不存在时自动创建（保留懒创建语义）
"""

import time
from typing import Any


# sessions 表 DDL
SESSIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT 'New Chat',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    deleted_at  REAL DEFAULT NULL
)
"""

SESSIONS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
ON sessions (updated_at DESC)
"""


class SessionRepository:
    """会话元数据仓库，共享 aiosqlite 连接。"""

    def __init__(self, conn) -> None:
        self._conn = conn

    async def initialize(self) -> None:
        """创建 sessions 表和索引（幂等）。"""
        await self._conn.execute(SESSIONS_TABLE_DDL)
        await self._conn.execute(SESSIONS_INDEX_DDL)
        await self._conn.commit()

    # ── CRUD ──

    async def create(self, session_id: str, title: str = "New Chat") -> dict[str, Any]:
        """创建新会话元数据。返回元数据字典。"""
        now = time.time()
        await self._conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        await self._conn.commit()
        return {"id": session_id, "title": title, "created_at": now, "updated_at": now}

    async def list(self) -> list[dict[str, Any]]:
        """列出所有未删除的会话，按 updated_at 降序。"""
        cursor = await self._conn.execute(
            "SELECT session_id, title, created_at, updated_at FROM sessions WHERE deleted_at IS NULL ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3]}
            for row in rows
        ]

    async def get(self, session_id: str) -> dict[str, Any] | None:
        """获取单个会话元数据。不存在或已删除返回 None。"""
        cursor = await self._conn.execute(
            "SELECT session_id, title, created_at, updated_at FROM sessions WHERE session_id = ? AND deleted_at IS NULL",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3]}

    async def rename(self, session_id: str, title: str) -> None:
        """更新会话标题。不存在则抛出 FileNotFoundError。"""
        cursor = await self._conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ? AND deleted_at IS NULL",
            (title, time.time(), session_id),
        )
        await self._conn.commit()
        if cursor.rowcount == 0:
            raise FileNotFoundError(f"Session {session_id} not found")

    async def touch(self, session_id: str) -> None:
        """更新会话的 updated_at 时间戳。"""
        now = time.time()
        await self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ? AND deleted_at IS NULL",
            (now, session_id),
        )
        await self._conn.commit()

    async def soft_delete(self, session_id: str) -> None:
        """软删除会话：标记 deleted_at。"""
        await self._conn.execute(
            "UPDATE sessions SET deleted_at = ?, updated_at = ? WHERE session_id = ?",
            (time.time(), time.time(), session_id),
        )
        await self._conn.commit()

    async def bootstrap_if_missing(self, session_id: str) -> dict[str, Any]:
        """如果会话不存在则自动创建（保留懒创建语义）。

        返回会话元数据（已存在则返回现有数据，不存在则创建后返回）。
        """
        existing = await self.get(session_id)
        if existing is not None:
            return existing
        return await self.create(session_id)
