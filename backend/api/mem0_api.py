"""mem0 管理 API — 记忆的 CRUD、整合、导入、状态检查。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(tags=["mem0"])


class MemoryImportItem(BaseModel):
    """从 MEMORY.md 导入的单条记忆。"""
    fact: str
    memory_type: str = Field(default="project", description="user/feedback/project/reference")
    why: str = Field(default="从 MEMORY.md 迁移导入")
    how_to_apply: str = Field(default="")


class MemoryImportRequest(BaseModel):
    """批量导入请求。"""
    items: list[MemoryImportItem]


class Mem0SettingsUpdate(BaseModel):
    """mem0 设置更新。"""
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    auto_extract: Optional[bool] = None
    buffer_size: Optional[int] = None
    flush_interval_seconds: Optional[int] = None
    stale_threshold_days: Optional[int] = None
    expire_threshold_days: Optional[int] = None
    min_confidence: Optional[float] = None


@router.get("/mem0/status")
async def get_mem0_status():
    """获取 mem0 系统状态。"""
    from config import get_mem0_config

    cfg = get_mem0_config()

    mem0_ready = False
    memory_count = 0
    try:
        from graph.mem0_manager import get_mem0_manager
        mgr = get_mem0_manager()
        mem0_ready = mgr.is_ready
        if mem0_ready:
            all_memories = mgr.get_all(user_id=cfg.get("user_id", "default"))
            memory_count = len(all_memories)
    except Exception:
        pass

    buffer_count = 0
    try:
        from graph.memory_buffer import get_memory_buffer
        buffer = get_memory_buffer()
        buffer_count = buffer.pending_count
    except Exception:
        pass

    return {
        "enabled": cfg.get("enabled", False),
        "mode": cfg.get("mode", "legacy"),
        "auto_extract": cfg.get("auto_extract", True),
        "mem0_ready": mem0_ready,
        "memory_count": memory_count,
        "buffer_pending": buffer_count,
        "config": {
            "buffer_size": cfg.get("buffer_size", 5),
            "flush_interval_seconds": cfg.get("flush_interval_seconds", 300),
            "stale_threshold_days": cfg.get("stale_threshold_days", 7),
            "expire_threshold_days": cfg.get("expire_threshold_days", 30),
            "min_confidence": cfg.get("min_confidence", 0.3),
        },
    }


@router.get("/mem0/memories")
async def list_memories(
    memory_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """列出所有记忆，支持按类型筛选和分页。"""
    from config import get_mem0_config

    cfg = get_mem0_config()

    try:
        from graph.mem0_manager import get_mem0_manager
        mgr = get_mem0_manager()
        if not mgr.is_ready:
            raise HTTPException(status_code=503, detail="mem0 未初始化")

        all_memories = mgr.get_all(user_id=cfg.get("user_id", "default"))

        # 按类型筛选
        if memory_type:
            all_memories = [
                m for m in all_memories
                if m.get("metadata", {}).get("memory_type") == memory_type
            ]

        # 按创建时间降序排序
        all_memories.sort(
            key=lambda m: m.get("metadata", {}).get("created_at", ""),
            reverse=True,
        )

        # 分页
        total = len(all_memories)
        page = all_memories[offset:offset + limit]

        return {
            "total": total,
            "items": page,
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取记忆列表失败: {e}")


@router.delete("/mem0/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """删除指定记忆。"""
    try:
        from graph.mem0_manager import get_mem0_manager
        mgr = get_mem0_manager()
        if not mgr.is_ready:
            raise HTTPException(status_code=503, detail="mem0 未初始化")

        if mgr.delete(memory_id):
            return {"ok": True, "message": f"记忆 {memory_id} 已删除"}
        raise HTTPException(status_code=404, detail=f"记忆 {memory_id} 不存在或删除失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除记忆失败: {e}")


@router.post("/mem0/memories/import")
async def import_memories(request: MemoryImportRequest):
    """从外部批量导入记忆（如从 MEMORY.md 迁移）。"""
    from config import get_mem0_config

    cfg = get_mem0_config()

    try:
        from graph.mem0_manager import get_mem0_manager
        mgr = get_mem0_manager()
        if not mgr.is_ready:
            raise HTTPException(status_code=503, detail="mem0 未初始化")

        imported = 0
        errors: list[str] = []

        for item in request.items:
            result = mgr.add_structured(
                fact=item.fact,
                memory_type=item.memory_type,
                why=item.why,
                how_to_apply=item.how_to_apply,
                user_id=cfg.get("user_id", "default"),
            )
            if result:
                imported += 1
            else:
                errors.append(f"导入失败: {item.fact[:50]}")

        return {
            "ok": True,
            "imported": imported,
            "total": len(request.items),
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量导入失败: {e}")


@router.post("/mem0/consolidate")
async def consolidate_memories():
    """手动触发记忆整合（去重、合并、冲突解决、过期清理）。"""
    try:
        from graph.mem0_manager import get_mem0_manager
        from graph.memory_consolidator import MemoryConsolidator

        mgr = get_mem0_manager()
        if not mgr.is_ready:
            raise HTTPException(status_code=503, detail="mem0 未初始化")

        consolidator = MemoryConsolidator(mgr)
        report = consolidator.run_consolidation()

        return {
            "ok": True,
            "report": {
                "total_memories": report.total_memories,
                "duplicates_found": report.duplicates_found,
                "merged": report.merged,
                "conflicts_detected": report.conflicts_detected,
                "conflicts_resolved": report.conflicts_resolved,
                "conflicts_pending": report.conflicts_pending,
                "expired": report.expired,
                "errors": report.errors,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"记忆整合失败: {e}")


@router.post("/mem0/flush")
async def flush_buffer():
    """手动触发缓冲区刷新（将待处理对话立即写入 mem0）。"""
    from config import get_mem0_config

    cfg = get_mem0_config()

    try:
        from graph.memory_buffer import get_memory_buffer
        from graph.mem0_manager import get_mem0_manager

        buffer = get_memory_buffer()
        turns = buffer.flush_all()

        if not turns:
            return {"ok": True, "flushed": 0, "message": "缓冲区为空，无需刷新"}

        mgr = get_mem0_manager()
        if not mgr.is_ready:
            raise HTTPException(status_code=503, detail="mem0 未初始化")

        mgr.batch_add(turns, user_id=cfg.get("user_id", "default"))
        return {
            "ok": True,
            "flushed": len(turns),
            "message": f"已将 {len(turns)} 轮对话写入 mem0",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"缓冲区刷新失败: {e}")


@router.put("/mem0/settings")
async def update_mem0_settings(update: Mem0SettingsUpdate):
    """更新 mem0 设置。"""
    try:
        from config import set_mem0_config

        updates = {k: v for k, v in update.model_dump().items() if v is not None}
        if not updates:
            return {"ok": True, "message": "无更新"}

        set_mem0_config(updates)
        return {"ok": True, "updated": list(updates.keys())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新设置失败: {e}")
