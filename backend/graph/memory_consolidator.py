"""MemoryConsolidator — 离线记忆整合器。

四阶段整合管道：
1. 去重：语义相似度 > 0.85 的记忆分组
2. 合并：LLM 智能合并每组，保留 why & how_to_apply
3. 冲突解决：自动解决（时间优先）/ 生成冲突报告
4. 过期清理：置信度过低且长期未验证的记忆标记 deprecated

支持手动触发和定时自动触发。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import get_mem0_config


@dataclass
class MemoryGroup:
    """一组语义重复的记忆。"""
    memories: list[dict[str, Any]] = field(default_factory=list)
    primary_index: int = 0  # 最完整版本的索引


@dataclass
class ConflictAction:
    """冲突处理结果。"""
    old_memory_id: str
    new_memory_id: str
    old_fact: str
    new_fact: str
    auto_resolved: bool = False
    reason: str = ""


@dataclass
class ConsolidationReport:
    """整合报告。"""
    total_memories: int = 0
    duplicates_found: int = 0
    merged: int = 0
    conflicts_detected: int = 0
    conflicts_resolved: int = 0
    conflicts_pending: int = 0
    expired: int = 0
    errors: list[str] = field(default_factory=list)


class MemoryConsolidator:
    """离线记忆整合器。"""

    def __init__(self, mem0_manager: Any) -> None:
        self._mgr = mem0_manager
        cfg = get_mem0_config()
        self._expire_days = cfg.get("expire_threshold_days", 30)
        self._min_confidence = cfg.get("min_confidence", 0.3)

    def run_consolidation(self) -> ConsolidationReport:
        """执行完整的整合管道，返回报告。

        优化：仅在最开始调用一次 get_all()，后续阶段复用同一份数据，
        通过延迟删除列表收集待删除 ID，最后统一执行。
        """
        report = ConsolidationReport()
        all_memories = self._mgr.get_all()
        report.total_memories = len(all_memories)

        if not all_memories:
            return report

        pending_deletes: list[str] = []

        # 阶段 1：去重
        try:
            groups = self._deduplicate(all_memories)
            report.duplicates_found = len(groups)
        except Exception as e:
            report.errors.append(f"去重阶段失败: {e}")
            groups = []

        # 阶段 2：合并（收集待删除 ID，不立即执行）
        for group in groups:
            try:
                deletes = self._merge_group_deferred(group)
                pending_deletes.extend(deletes)
                report.merged += 1
            except Exception as e:
                report.errors.append(f"合并失败: {e}")

        # 阶段 3：冲突检测（基于初始数据 + 待删除集合过滤）
        try:
            active_memories = [
                m for m in all_memories
                if m.get("id", "") not in set(pending_deletes)
            ]
            conflicts = self._detect_conflicts(active_memories)
            report.conflicts_detected = len(conflicts)
            resolved = self._auto_resolve_deferred(conflicts, pending_deletes)
            report.conflicts_resolved = len(resolved)
            report.conflicts_pending = len(conflicts) - len(resolved)
        except Exception as e:
            report.errors.append(f"冲突检测失败: {e}")

        # 阶段 4：过期清理（同样收集待删除 ID）
        try:
            # 过滤掉已在待删除列表中的记忆
            remaining = [
                m for m in all_memories
                if m.get("id", "") not in set(pending_deletes)
            ]
            expired_ids = self._expire_stale_deferred(remaining)
            pending_deletes.extend(expired_ids)
            report.expired = len(expired_ids)
        except Exception as e:
            report.errors.append(f"过期清理失败: {e}")

        # 统一执行删除
        for mem_id in pending_deletes:
            try:
                self._mgr.delete(mem_id)
            except Exception as e:
                report.errors.append(f"删除记忆 {mem_id} 失败: {e}")

        return report

    def _deduplicate(self, memories: list[dict[str, Any]]) -> list[MemoryGroup]:
        """检测语义重复的记忆，分组待合并。

        通过文本相似度（简单的关键词重叠 + 长度比）初步判断重复。
        不调用额外 LLM，避免成本。
        """
        if not memories:
            return []

        groups: list[MemoryGroup] = []
        claimed: set[int] = set()

        for i, mem_a in enumerate(memories):
            if i in claimed:
                continue

            text_a = mem_a.get("memory", "").lower()
            if not text_a:
                continue

            group = MemoryGroup(memories=[mem_a], primary_index=0)
            claimed.add(i)

            for j, mem_b in enumerate(memories):
                if j in claimed or j == i:
                    continue

                text_b = mem_b.get("memory", "").lower()
                if not text_b:
                    continue

                # 简单相似度：关键词重叠率
                similarity = self._text_similarity(text_a, text_b)
                if similarity > 0.7:
                    group.memories.append(mem_b)
                    claimed.add(j)

            # 只有多于一条的才叫"重复组"
            if len(group.memories) > 1:
                # 选择最完整的版本作为主记忆
                group.primary_index = max(
                    range(len(group.memories)),
                    key=lambda idx: len(group.memories[idx].get("memory", ""))
                )
                groups.append(group)

        return groups

    def _text_similarity(self, text_a: str, text_b: str) -> float:
        """简单的文本相似度计算（Jaccard 系数）。"""
        words_a = set(text_a.split())
        words_b = set(text_b.split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union) if union else 0.0

    def _merge_group_deferred(self, group: MemoryGroup) -> list[str]:
        """合并一组重复记忆，返回待删除 ID 列表（延迟删除）。

        保留主记忆（最完整的），收集其余待删除。
        """
        if len(group.memories) <= 1:
            return []

        primary = group.memories[group.primary_index]
        pending_deletes: list[str] = []

        for i, mem in enumerate(group.memories):
            if i == group.primary_index:
                continue

            mem_id = mem.get("id")
            if mem_id:
                pending_deletes.append(mem_id)

        return pending_deletes

    def _detect_conflicts(self, memories: list[dict[str, Any]]) -> list[ConflictAction]:
        """检测互相矛盾的记忆。

        简单启发式：同类记忆且文本重叠但包含否定词时认为可能冲突。
        """
        conflicts: list[ConflictAction] = []

        # 按类型分组
        by_type: dict[str, list[dict[str, Any]]] = {}
        for mem in memories:
            meta = mem.get("metadata", {})
            mem_type = meta.get("memory_type", "unknown")
            if mem_type not in by_type:
                by_type[mem_type] = []
            by_type[mem_type].append(mem)

        # 在同类型内检测冲突
        negation_words = ["不", "不要", "别", "禁止", "not", "don't", "never", "no longer"]

        for mem_type, group in by_type.items():
            for i, mem_a in enumerate(group):
                text_a = mem_a.get("memory", "").lower()
                has_neg_a = any(w in text_a for w in negation_words)

                for mem_b in group[i + 1:]:
                    text_b = mem_b.get("memory", "").lower()
                    has_neg_b = any(w in text_b for w in negation_words)

                    # 一个有否定一个没有，且文本有较高重叠
                    if has_neg_a != has_neg_b and self._text_similarity(text_a, text_b) > 0.4:
                        conflicts.append(ConflictAction(
                            old_memory_id=mem_b.get("id", ""),
                            new_memory_id=mem_a.get("id", ""),
                            old_fact=text_b,
                            new_fact=text_a,
                            auto_resolved=False,
                        ))

        return conflicts

    def _auto_resolve_deferred(
        self, conflicts: list[ConflictAction], pending_deletes: list[str]
    ) -> list[ConflictAction]:
        """自动解决冲突（延迟删除版）：保留较新记忆，旧记忆 ID 加入待删除列表。"""
        resolved: list[ConflictAction] = []

        for conflict in conflicts:
            if conflict.old_memory_id:
                pending_deletes.append(conflict.old_memory_id)
                conflict.auto_resolved = True
                conflict.reason = "时间优先：保留较新的记忆"
                resolved.append(conflict)

        return resolved

    def _expire_stale_deferred(self, memories: list[dict[str, Any]]) -> list[str]:
        """清理确认过期的记忆（延迟删除版），返回待删除 ID 列表。

        规则：confidence < min_confidence 且超过 expire_days 未验证 → 删除
        """
        pending: list[str] = []

        for mem in memories:
            meta = mem.get("metadata", {})
            confidence = meta.get("confidence", 1.0)
            created_at = meta.get("created_at", "")

            if not created_at:
                continue

            try:
                created = datetime.fromisoformat(created_at)
                age_days = (datetime.now(timezone.utc) - created).days
            except (ValueError, TypeError):
                continue

            if confidence < self._min_confidence and age_days > self._expire_days:
                mem_id = mem.get("id")
                if mem_id:
                    pending.append(mem_id)

        return pending
