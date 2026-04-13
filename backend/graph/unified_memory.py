"""UnifiedMemoryRetriever — 统一记忆检索接口。

合并三大记忆源：
1. mem0 结构化记忆（置信度 + 新鲜度）
2. RAG 向量索引（MemoryIndexer / LlamaIndex）
3. MEMORY.md 直接读取（降级回退）

任一记忆源异常时不阻塞请求，记录 warning 日志。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class UnifiedMemoryRetriever:
    """统一记忆检索：合并 mem0、RAG、MEMORY.md 三大记忆源。

    优先级：mem0（有置信度和新鲜度指标）> RAG 向量检索 > MEMORY.md 直接读取。
    每个源独立 try/except，任一失败不阻塞其余源的检索。
    """

    def __init__(
        self,
        mem0_client: Any = None,
        rag_index: Any = None,
        memory_md_path: Path | None = None,
    ) -> None:
        self._mem0_client = mem0_client
        self._rag_index = rag_index
        self._memory_md_path = memory_md_path

    def set_mem0_client(self, client: Any) -> None:
        """延迟设置 mem0 客户端（mem0 可能晚于 retriever 初始化）。"""
        self._mem0_client = client

    # 最低相关性阈值：低于此分数的结果不注入上下文
    MIN_RELEVANCE_SCORE = 0.3

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """从所有可用记忆源检索，合并后按相关性排序返回最多 top_k 条。

        依次从 mem0、RAG、MEMORY.md 检索，合并去重后按分数降序排序。
        分数低于 MIN_RELEVANCE_SCORE 的结果会被过滤，不注入上下文。
        """
        all_results: list[dict[str, Any]] = []

        # Source 1: mem0 结构化记忆（优先级最高）
        all_results.extend(self._retrieve_mem0(query, top_k))

        # Source 2: RAG 向量索引
        all_results.extend(self._retrieve_rag(query, top_k))

        # Source 3: MEMORY.md 直接读取（结果不足时的补充/降级）
        if len(all_results) < top_k:
            all_results.extend(self._read_memory_md(query))

        # 去重 + 相关性过滤 + 排序
        seen_texts: set[str] = set()
        unique: list[dict[str, Any]] = []
        for r in all_results:
            text = r.get("text", "")
            if text and text not in seen_texts:
                seen_texts.add(text)
                # 过滤低相关度结果
                if _result_score(r) >= self.MIN_RELEVANCE_SCORE:
                    unique.append(r)

        unique.sort(key=_result_score, reverse=True)
        return unique[:top_k]

    async def retrieve_async(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """异步检索，通过 run_in_executor 避免阻塞事件循环。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.retrieve, query, top_k)

    def format_for_injection(self, results: list[dict[str, Any]]) -> str:
        """格式化为系统消息注入内容。

        格式：[相关记忆]\\n内容（来源: xxx，置信度: 0.x）
        """
        if not results:
            return ""

        parts = ["[相关记忆]"]
        for r in results:
            source = r.get("source", "unknown")
            conf = r.get("confidence")
            if conf is not None:
                conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else str(conf)
            else:
                conf_str = str(r.get("score", "N/A"))
            text = r.get("text", "")
            parts.append(f"{text}（来源: {source}，置信度: {conf_str}）")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 私有方法：各记忆源检索（每个独立 try/except 保证降级）
    # ------------------------------------------------------------------

    def _retrieve_mem0(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """从 mem0 检索结构化记忆。失败时返回空列表，不阻塞。"""
        if not self._mem0_client:
            return []
        try:
            # mem0_client 是 Mem0Manager 实例
            if hasattr(self._mem0_client, "is_ready") and not self._mem0_client.is_ready:
                return []

            raw = self._mem0_client.search(query, limit=top_k)
            if not raw:
                return []

            results: list[dict[str, Any]] = []
            for item in raw:
                metadata = item.get("metadata", {})
                score = item.get("score", 0)
                results.append({
                    "text": item.get("memory", ""),
                    "score": f"{score:.4f}" if isinstance(score, (int, float)) else str(score),
                    "source": "mem0",
                    "memory_type": metadata.get("memory_type", "unknown"),
                    "confidence": metadata.get("confidence", 0.5),
                })
            return results
        except Exception as e:
            print(f"⚠️ mem0 检索失败（已降级）: {e}")
            return []

    def _retrieve_rag(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """从 RAG 向量索引检索。失败时返回空列表，不阻塞。"""
        if not self._rag_index:
            return []
        try:
            results = self._rag_index.retrieve(query, top_k=top_k)
            for r in results:
                r.setdefault("source", "RAG")
            return results
        except Exception as e:
            print(f"⚠️ RAG 检索失败（已降级）: {e}")
            return []

    def _read_memory_md(self, query: str) -> list[dict[str, Any]]:
        """直接读取 MEMORY.md 文件，按关键词匹配段落。

        仅在其他源结果不足时作为补充，使用简单的关键词匹配。
        """
        if not self._memory_md_path or not self._memory_md_path.exists():
            return []

        try:
            content = self._memory_md_path.read_text(encoding="utf-8")
            if not content.strip():
                return []

            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            query_words = [w for w in query.lower().split() if len(w) > 1]

            results: list[dict[str, Any]] = []
            for para in paragraphs:
                para_lower = para.lower()
                if any(w in para_lower for w in query_words):
                    results.append({
                        "text": para,
                        "score": "0.5",
                        "source": "MEMORY.md",
                    })
            return results
        except Exception as e:
            print(f"⚠️ MEMORY.md 读取失败（已降级）: {e}")
            return []


def _result_score(r: dict[str, Any]) -> float:
    """提取结果分数用于排序。"""
    if "confidence" in r:
        try:
            return float(r["confidence"])
        except (ValueError, TypeError):
            pass
    try:
        return float(r.get("score", "0"))
    except (ValueError, TypeError):
        return 0.0


# 单例
_instance: UnifiedMemoryRetriever | None = None


def get_unified_retriever(base_dir: Path | None = None) -> UnifiedMemoryRetriever:
    """获取或创建 UnifiedMemoryRetriever 单例。"""
    global _instance
    if _instance is None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent
        from graph.memory_indexer import get_memory_indexer

        _instance = UnifiedMemoryRetriever(
            rag_index=get_memory_indexer(base_dir),
            memory_md_path=base_dir / "memory" / "MEMORY.md",
        )
    return _instance
