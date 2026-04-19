"""UnifiedMemoryRetriever 单元测试。"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from graph.unified_memory import UnifiedMemoryRetriever, _result_score


class FakeMem0Manager:
    """模拟 Mem0Manager，用于测试。"""

    def __init__(self, results: list[dict] | None = None, *, ready: bool = True):
        self._results = results or []
        self._ready = ready

    @property
    def is_ready(self) -> bool:
        return self._ready

    def search(self, query: str, limit: int = 5) -> list[dict]:
        return self._results


class FakeRagIndex:
    """模拟 MemoryIndexer，用于测试。"""

    def __init__(self, results: list[dict] | None = None):
        self._results = results or []

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        return self._results


class TestRetrieveBasic:
    """测试基本检索和合并逻辑。"""

    def test_retrieve_from_mem0(self):
        mem0 = FakeMem0Manager([
            {"memory": "用户住在北京", "score": 0.95, "id": "1",
             "metadata": {"memory_type": "user", "confidence": 0.9}},
        ])
        retriever = UnifiedMemoryRetriever(mem0_client=mem0)
        results = retriever.retrieve("用户住在哪里")
        assert len(results) >= 1
        assert any("北京" in r["text"] for r in results)
        assert results[0]["source"] == "mem0"

    def test_retrieve_from_rag(self):
        rag = FakeRagIndex([
            {"text": "用户喜欢 Python", "score": "0.8800", "source": "MEMORY.md"},
        ])
        retriever = UnifiedMemoryRetriever(rag_index=rag)
        results = retriever.retrieve("用户喜欢什么语言")
        assert len(results) >= 1
        assert any("Python" in r["text"] for r in results)

    def test_retrieve_merge_both_sources(self):
        mem0 = FakeMem0Manager([
            {"memory": "用户住在北京", "score": 0.9, "id": "1",
             "metadata": {"memory_type": "user", "confidence": 0.9}},
        ])
        rag = FakeRagIndex([
            {"text": "用户喜欢 Python", "score": "0.8500", "source": "MEMORY.md"},
        ])
        retriever = UnifiedMemoryRetriever(mem0_client=mem0, rag_index=rag)
        results = retriever.retrieve("用户信息", top_k=5)
        assert len(results) == 2
        sources = {r["source"] for r in results}
        assert "mem0" in sources
        # RAG 结果的 source 由 MemoryIndexer 返回（默认 "MEMORY.md"），setdefault 不覆盖
        assert "MEMORY.md" in sources

    def test_retrieve_deduplication(self):
        """重复内容只保留一条。"""
        same_text = "用户住在北京"
        mem0 = FakeMem0Manager([
            {"memory": same_text, "score": 0.9, "id": "1", "metadata": {"confidence": 0.9}},
        ])
        rag = FakeRagIndex([
            {"text": same_text, "score": "0.8500", "source": "MEMORY.md"},
        ])
        retriever = UnifiedMemoryRetriever(mem0_client=mem0, rag_index=rag)
        results = retriever.retrieve("用户住址")
        assert len(results) == 1

    def test_retrieve_top_k_limit(self):
        mem0 = FakeMem0Manager([
            {"memory": f"记忆{i}", "score": 0.9 - i * 0.1, "id": str(i),
             "metadata": {"confidence": 0.9 - i * 0.1}}
            for i in range(10)
        ])
        retriever = UnifiedMemoryRetriever(mem0_client=mem0)
        results = retriever.retrieve("测试", top_k=3)
        assert len(results) == 3

    def test_retrieve_no_sources_returns_empty(self):
        retriever = UnifiedMemoryRetriever()
        results = retriever.retrieve("随便什么")
        assert results == []

    def test_retrieve_filters_low_relevance(self):
        """低于 MIN_RELEVANCE_SCORE 的结果被过滤。"""
        mem0 = FakeMem0Manager([
            {"memory": "高相关记忆", "score": 0.9, "id": "1",
             "metadata": {"confidence": 0.9}},
            {"memory": "低相关记忆", "score": 0.1, "id": "2",
             "metadata": {"confidence": 0.1}},
        ])
        retriever = UnifiedMemoryRetriever(mem0_client=mem0)
        results = retriever.retrieve("测试")
        assert len(results) == 1
        assert "高相关" in results[0]["text"]


class TestMemoryMdFallback:
    """测试 MEMORY.md 直接读取作为补充/降级。"""

    def test_read_memory_md_when_no_other_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "MEMORY.md"
            md_path.write_text(
                "## 用户偏好\n\n用户喜欢深色主题\n\n## 工作信息\n\n用户在 Google 工作\n",
                encoding="utf-8",
            )
            retriever = UnifiedMemoryRetriever(memory_md_path=md_path)
            results = retriever.retrieve("深色主题")
            assert len(results) >= 1
            assert any("深色主题" in r["text"] for r in results)
            assert results[0]["source"] == "MEMORY.md"

    def test_memory_md_not_used_when_other_sources_sufficient(self):
        """其他源结果充足时不再读取 MEMORY.md。"""
        mem0 = FakeMem0Manager([
            {"memory": f"记忆{i}", "score": 0.9, "id": str(i),
             "metadata": {"confidence": 0.9}}
            for i in range(5)
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "MEMORY.md"
            md_path.write_text("补充信息", encoding="utf-8")
            retriever = UnifiedMemoryRetriever(
                mem0_client=mem0,
                memory_md_path=md_path,
            )
            results = retriever.retrieve("测试", top_k=5)
            # 全部来自 mem0，不需要 MEMORY.md
            assert all(r["source"] == "mem0" for r in results)

    def test_memory_md_nonexistent_file(self):
        retriever = UnifiedMemoryRetriever(
            memory_md_path=Path("/tmp/nonexistent_memory_xyz.md"),
        )
        results = retriever.retrieve("测试")
        assert results == []


class TestGracefulDegradation:
    """测试记忆源降级逻辑。"""

    def test_mem0_failure_does_not_block(self):
        """mem0 抛异常时仍可从 RAG 获取结果。"""
        broken_mem0 = MagicMock()
        broken_mem0.is_ready = True
        broken_mem0.search.side_effect = RuntimeError("mem0 连接失败")

        rag = FakeRagIndex([
            {"text": "RAG 结果", "score": "0.8000", "source": "MEMORY.md"},
        ])
        retriever = UnifiedMemoryRetriever(mem0_client=broken_mem0, rag_index=rag)
        results = retriever.retrieve("测试")
        assert len(results) == 1
        assert results[0]["text"] == "RAG 结果"

    def test_rag_failure_does_not_block(self):
        """RAG 抛异常时仍可从 mem0 获取结果。"""
        mem0 = FakeMem0Manager([
            {"memory": "mem0 结果", "score": 0.9, "id": "1",
             "metadata": {"confidence": 0.9}},
        ])
        broken_rag = MagicMock()
        broken_rag.retrieve.side_effect = RuntimeError("RAG 索引损坏")

        retriever = UnifiedMemoryRetriever(mem0_client=mem0, rag_index=broken_rag)
        results = retriever.retrieve("测试")
        assert len(results) == 1
        assert results[0]["text"] == "mem0 结果"

    def test_all_sources_fail_returns_empty(self):
        """所有源都失败时返回空列表而不抛异常。"""
        broken_mem0 = MagicMock()
        broken_mem0.is_ready = True
        broken_mem0.search.side_effect = RuntimeError("mem0 down")

        broken_rag = MagicMock()
        broken_rag.retrieve.side_effect = RuntimeError("RAG down")

        retriever = UnifiedMemoryRetriever(
            mem0_client=broken_mem0,
            rag_index=broken_rag,
            memory_md_path=Path("/tmp/nonexistent_xyz.md"),
        )
        results = retriever.retrieve("测试")
        assert results == []

    def test_mem0_not_ready_skips_gracefully(self):
        """mem0 未就绪时跳过而不报错。"""
        not_ready = FakeMem0Manager(ready=False)
        retriever = UnifiedMemoryRetriever(mem0_client=not_ready)
        results = retriever.retrieve("测试")
        assert results == []

    def test_mem0_none_skips_gracefully(self):
        """mem0_client 为 None 时跳过。"""
        retriever = UnifiedMemoryRetriever(mem0_client=None)
        results = retriever.retrieve("测试")
        assert results == []

    def test_set_mem0_client_later(self):
        """延迟设置 mem0 客户端后可正常检索。"""
        retriever = UnifiedMemoryRetriever()
        assert retriever.retrieve("测试") == []

        mem0 = FakeMem0Manager([
            {"memory": "延迟注入的记忆", "score": 0.9, "id": "1",
             "metadata": {"confidence": 0.9}},
        ])
        retriever.set_mem0_client(mem0)
        results = retriever.retrieve("记忆")
        assert len(results) == 1
        assert "延迟注入" in results[0]["text"]


class TestFormatForInjection:
    """测试系统消息注入格式。"""

    def test_format_basic(self):
        results = [
            {"text": "用户住在北京", "source": "mem0", "confidence": 0.9},
        ]
        retriever = UnifiedMemoryRetriever()
        formatted = retriever.format_for_injection(results)
        assert formatted.startswith("[相关记忆]")
        assert "用户住在北京" in formatted
        assert "来源: mem0" in formatted
        assert "置信度: 0.90" in formatted

    def test_format_with_score_instead_of_confidence(self):
        results = [
            {"text": "用户喜欢 Python", "source": "RAG", "score": "0.8500"},
        ]
        retriever = UnifiedMemoryRetriever()
        formatted = retriever.format_for_injection(results)
        assert "来源: RAG" in formatted
        assert "置信度: 0.8500" in formatted

    def test_format_empty_returns_empty_string(self):
        retriever = UnifiedMemoryRetriever()
        assert retriever.format_for_injection([]) == ""

    def test_format_multiple_results(self):
        results = [
            {"text": "记忆A", "source": "mem0", "confidence": 0.9},
            {"text": "记忆B", "source": "RAG", "score": "0.7"},
        ]
        retriever = UnifiedMemoryRetriever()
        formatted = retriever.format_for_injection(results)
        assert "记忆A" in formatted
        assert "记忆B" in formatted


class TestResultScore:
    """测试排序分数提取。"""

    def test_confidence_float(self):
        assert _result_score({"confidence": 0.9}) == 0.9

    def test_score_string(self):
        assert _result_score({"score": "0.8500"}) == 0.85

    def test_no_score_returns_zero(self):
        assert _result_score({}) == 0.0

    def test_invalid_score_returns_zero(self):
        assert _result_score({"score": "N/A"}) == 0.0

    def test_confidence_takes_priority(self):
        assert _result_score({"confidence": 0.9, "score": "0.1"}) == 0.9


class TestMemoryMdDynamicScoring:
    """MEMORY.md 段落动态评分测试。"""

    def test_full_match_gets_highest_score(self):
        """全匹配段落 score 为 0.7。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "MEMORY.md"
            md_path.write_text("用户住在北京\n", encoding="utf-8")
            retriever = UnifiedMemoryRetriever(memory_md_path=md_path)
            results = retriever.retrieve("用户 住在 北京")
            assert len(results) == 1
            assert results[0]["score"] == pytest.approx(0.7)

    def test_partial_match_gets_lower_score(self):
        """部分匹配段落 score 低于全匹配段落。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "MEMORY.md"
            md_path.write_text(
                "用户住在北京\n\n用户喜欢 Python\n",
                encoding="utf-8",
            )
            retriever = UnifiedMemoryRetriever(memory_md_path=md_path)
            results = retriever.retrieve("用户 住在 喜欢 Python")
            assert len(results) == 2
            # "用户喜欢 Python" 匹配 3/4 个关键词 → 0.3 + 0.4*0.75 = 0.6
            # "用户住在北京" 匹配 2/4 个关键词 → 0.3 + 0.4*0.5 = 0.5
            scores = {r["text"]: r["score"] for r in results}
            assert scores["用户喜欢 Python"] == pytest.approx(0.6)
            assert scores["用户住在北京"] == pytest.approx(0.5)

    def test_single_keyword_match_gets_0_7(self):
        """单关键词查询匹配段落 score 为 0.7。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "MEMORY.md"
            md_path.write_text("深色主题设置\n", encoding="utf-8")
            retriever = UnifiedMemoryRetriever(memory_md_path=md_path)
            results = retriever.retrieve("深色")
            assert len(results) == 1
            assert results[0]["score"] == pytest.approx(0.7)

    def test_unmatched_paragraph_not_returned(self):
        """未匹配任何关键词的段落不出现在结果中。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "MEMORY.md"
            md_path.write_text(
                "用户住在北京\n\n系统配置信息\n",
                encoding="utf-8",
            )
            retriever = UnifiedMemoryRetriever(memory_md_path=md_path)
            results = retriever.retrieve("用户 住在")
            assert len(results) == 1
            assert "用户住在北京" in results[0]["text"]

    def test_score_is_float_type(self):
        """score 类型为 float 而非字符串。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "MEMORY.md"
            md_path.write_text("测试段落\n", encoding="utf-8")
            retriever = UnifiedMemoryRetriever(memory_md_path=md_path)
            results = retriever.retrieve("测试")
            assert len(results) == 1
            assert isinstance(results[0]["score"], float)
