"""MemoryRetriever — 统一记忆检索接口（策略模式 + 防御性读取）。

提供三种检索策略：
- LegacyRetriever: 封装现有 LlamaIndex MemoryIndexer
- Mem0Retriever: 封装 mem0 Memory.search()，附带防御性读取
- HybridRetriever: 合并两个检索源

通过 get_retriever() 工厂方法根据配置返回实例。
agent.py 只需调用 get_retriever()，无需关心底层实现。
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_mem0_config, get_rag_mode


class MemoryRetriever(ABC):
    """统一记忆检索抽象基类。"""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """检索相关记忆。

        Returns:
            统一格式列表:
            [{
                "text": str,
                "score": str,
                "source": str,
                "memory_type"?: str,
                "id"?: str,
                "why"?: str,
                "how_to_apply"?: str,
                "created_at"?: str,
                "confidence"?: float,
                "freshness"?: str,
            }]
        """
        ...

    @abstractmethod
    def format_context(self, results: list[dict[str, Any]]) -> str:
        """将检索结果格式化为注入上下文的字符串。"""
        ...


class LegacyRetriever(MemoryRetriever):
    """封装现有 LlamaIndex MemoryIndexer，行为完全不变。"""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        from graph.memory_indexer import get_memory_indexer
        indexer = get_memory_indexer(self._base_dir)
        return indexer.retrieve(query, top_k=top_k)

    def format_context(self, results: list[dict[str, Any]]) -> str:
        """与现有格式完全一致。"""
        snippets = "\n\n".join(
            f"[片段 {i+1}] (score: {r['score']})\n{r['text']}"
            for i, r in enumerate(results)
        )
        return f"[记忆检索结果]\n{snippets}"


class Mem0Retriever(MemoryRetriever):
    """封装 mem0 Memory.search()，附带防御性读取。

    三层防御机制：
    1. 时间轴新鲜度分级（fresh/recent/aging/stale）
    2. 引用验证提示（文件路径、URL）
    3. 置信度过滤与衰减
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        cfg = get_mem0_config()
        self._user_id = cfg.get("user_id", "default")
        self._min_confidence = cfg.get("min_confidence", 0.3)
        self._stale_days = cfg.get("stale_threshold_days", 7)
        self._expire_days = cfg.get("expire_threshold_days", 30)

    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        from graph.mem0_manager import get_mem0_manager
        mgr = get_mem0_manager(self._base_dir)
        if not mgr.is_ready:
            return []

        raw_results = mgr.search(query, user_id=self._user_id, limit=top_k * 2)
        if not raw_results:
            return []

        results: list[dict[str, Any]] = []
        for item in raw_results:
            # mem0 返回格式：{"memory": "...", "score": ..., "id": "...", "metadata": {...}}
            metadata = item.get("metadata", {})
            # mem0 的 created_at 在顶层，不在 metadata 中
            created_at = item.get("created_at", "") or metadata.get("created_at", "")
            confidence = self._calculate_confidence(
                created_at, metadata.get("last_verified_at")
            )

            # 置信度过滤
            if confidence < self._min_confidence:
                continue

            result = {
                "text": item.get("memory", ""),
                "score": f"{item.get('score', 0):.4f}",
                "source": "mem0",
                "memory_type": metadata.get("memory_type", "unknown"),
                "id": item.get("id", ""),
                "why": metadata.get("why", ""),
                "how_to_apply": metadata.get("how_to_apply", ""),
                "created_at": created_at,
                "confidence": round(confidence, 2),
                "freshness": self._calculate_freshness(created_at),
            }
            results.append(result)

            if len(results) >= top_k:
                break

        # 按置信度降序排序
        results.sort(key=lambda r: r.get("confidence", 0), reverse=True)
        return results

    def format_context(self, results: list[dict[str, Any]]) -> str:
        """生成带防御性提示的结构化上下文。"""
        if not results:
            return ""

        parts = ["[智能记忆检索结果]"]
        for i, r in enumerate(results):
            freshness = r.get("freshness", "fresh")
            mem_type = r.get("memory_type", "unknown")
            confidence = r.get("confidence", 1.0)
            created = r.get("created_at", "")

            part = f"--- 片段 {i+1} [{mem_type}, 置信度: {confidence}"
            if created:
                part += f", 记录于: {created[:10]}"
            part += "] ---\n"

            part += f"事实: {r['text']}\n"

            why = r.get("why", "")
            if why:
                part += f"原因: {why}\n"

            how = r.get("how_to_apply", "")
            if how:
                part += f"适用场景: {how}\n"

            # 防御性提示：新鲜度警告
            if freshness != "fresh":
                age_days = self._calculate_age_days(created)
                if age_days is not None:
                    if freshness == "recent":
                        part += f"⚠️ 此记忆已存在 {age_days} 天，使用前请验证是否仍然有效\n"
                    elif freshness == "aging":
                        part += f"⚠️ 此记忆已存在 {age_days} 天，很可能已过时，使用前必须验证\n"
                    elif freshness == "stale":
                        part += f"🔴 此记忆已超过 {age_days} 天，仅作参考。使用前必须验证当前状态\n"

            # 防御性提示：引用验证
            references = self._extract_references(r["text"])
            if references:
                part += f"📋 记忆中引用了: {', '.join(references)}，请验证是否仍然存在\n"

            part += "---"
            parts.append(part)

        return "\n".join(parts)

    def _calculate_confidence(
        self, created_at: str, last_verified_at: str | None
    ) -> float:
        """计算记忆的当前置信度（时间衰减 + 验证回血）。"""
        base_confidence = 1.0

        if not created_at:
            return base_confidence

        try:
            created = self._parse_datetime(created_at)
            age_days = (datetime.now(timezone.utc) - created).days

            # 时间衰减：每天降低 0.02，最低 0.3
            confidence = max(0.3, base_confidence - age_days * 0.02)

            # 验证加分
            if last_verified_at:
                verified = self._parse_datetime(last_verified_at)
                verified_age = (datetime.now(timezone.utc) - verified).days
                verify_bonus = max(0, 0.3 - verified_age * 0.02)
                confidence = min(1.0, confidence + verify_bonus)

            return round(confidence, 2)
        except (ValueError, TypeError):
            return base_confidence

    def _calculate_freshness(self, created_at: str) -> str:
        """计算记忆新鲜度等级。"""
        age_days = self._calculate_age_days(created_at)
        if age_days is None:
            return "fresh"

        if age_days < 1:
            return "fresh"
        elif age_days < self._stale_days:
            return "recent"
        elif age_days < self._expire_days:
            return "aging"
        else:
            return "stale"

    def _calculate_age_days(self, created_at: str) -> int | None:
        """计算记忆存活天数。"""
        if not created_at:
            return None
        try:
            created = self._parse_datetime(created_at)
            return (datetime.now(timezone.utc) - created).days
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_datetime(dt_str: str) -> datetime:
        """解析时间字符串，确保返回 UTC 时区感知的 datetime。"""
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _extract_references(self, text: str) -> list[str]:
        """从记忆文本中提取可验证的引用（文件路径、URL）。"""
        refs: list[str] = []

        # 文件路径模式（如 backend/app.py, src/lib/store.tsx）
        file_pattern = r'(?:[\w\-./]+\.[\w]+)'
        for match in re.findall(file_pattern, text):
            if "/" in match and not match.startswith("http"):
                refs.append(match)

        # URL 模式
        url_pattern = r'https?://[^\s,，。]+'
        for match in re.findall(url_pattern, text):
            refs.append(match)

        # 去重并限制数量
        return list(dict.fromkeys(refs))[:3]


class HybridRetriever(MemoryRetriever):
    """合并 Legacy 和 Mem0 两个检索源的结果。"""

    def __init__(self, base_dir: Path) -> None:
        self._legacy = LegacyRetriever(base_dir)
        self._mem0 = Mem0Retriever(base_dir)

    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        import asyncio

        # 并行检索两个源
        legacy_results = self._legacy.retrieve(query, top_k=top_k)
        mem0_results = self._mem0.retrieve(query, top_k=top_k)

        # 合并结果
        all_results = []

        for r in legacy_results:
            r["source"] = "legacy"
            all_results.append(r)

        for r in mem0_results:
            r["source"] = "mem0"
            all_results.append(r)

        # 按置信度/score 排序
        def sort_key(r: dict) -> float:
            if "confidence" in r:
                return r["confidence"]
            try:
                return float(r.get("score", "0"))
            except (ValueError, TypeError):
                return 0.0

        all_results.sort(key=sort_key, reverse=True)
        return all_results[:top_k]

    def format_context(self, results: list[dict[str, Any]]) -> str:
        """合并格式化：legacy 用旧格式，mem0 用防御性格式。"""
        legacy_results = [r for r in results if r.get("source") == "legacy"]
        mem0_results = [r for r in results if r.get("source") == "mem0"]

        parts: list[str] = []

        if legacy_results:
            parts.append(self._legacy.format_context(legacy_results))
        if mem0_results:
            parts.append(self._mem0.format_context(mem0_results))

        return "\n\n".join(parts)


def get_retriever(base_dir: Path) -> MemoryRetriever | None:
    """工厂方法：根据配置返回对应的检索器实例。

    Returns:
        MemoryRetriever 实例，或 None（RAG 未启用时）
    """
    if not get_rag_mode():
        return None

    mem0_cfg = get_mem0_config()
    enabled = mem0_cfg.get("enabled", False)
    mode = mem0_cfg.get("mode", "legacy")

    if not enabled or mode == "legacy":
        return LegacyRetriever(base_dir)
    elif mode == "mem0":
        return Mem0Retriever(base_dir)
    elif mode == "hybrid":
        return HybridRetriever(base_dir)
    else:
        return LegacyRetriever(base_dir)
