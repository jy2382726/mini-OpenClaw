"""mem0 性能优化测试 — 覆盖 6 项优化方案的单元测试。"""

import asyncio
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestExtractionModel(unittest.TestCase):
    """方案一：独立轻量 LLM 模型配置。"""

    @patch("graph.mem0_manager.get_mem0_config")
    @patch("graph.mem0_manager.load_config")
    def test_extraction_model_overrides_main(self, mock_load_config, mock_get_mem0_config):
        """配置了 extraction_model 时，mem0 使用独立模型而非主对话模型。"""
        mock_get_mem0_config.return_value = {
            "extraction_model": {
                "model": "qwen3.5-flash",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-test-flash",
                "max_tokens": 512,
                "enable_thinking": False,
            }
        }
        mock_load_config.return_value = {
            "llm": {
                "model": "qwen3.5-plus",
                "api_key": "sk-test-main",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            "embedding": {
                "model": "text-embedding-v4",
                "api_key": "sk-test-emb",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
        }

        from graph.mem0_manager import Mem0Manager
        mgr = Mem0Manager()

        captured_config = {}
        def mock_from_config(cfg):
            nonlocal captured_config
            captured_config = cfg
            return MagicMock()

        # Memory 在 initialize() 内部通过 try/except 延迟导入
        with patch.dict("sys.modules", {"mem0": MagicMock(from_config=mock_from_config)}):
            with patch("mem0.Memory") as MockMem:
                MockMem.from_config = mock_from_config
                mgr.initialize(Path("/tmp/test_mem0"))

        self.assertEqual(captured_config["llm"]["config"]["model"], "qwen3.5-flash")
        self.assertEqual(captured_config["llm"]["config"]["api_key"], "sk-test-flash")
        self.assertEqual(captured_config["llm"]["config"]["max_tokens"], 512)

    @patch("graph.mem0_manager.get_mem0_config")
    @patch("graph.mem0_manager.load_config")
    def test_no_extraction_model_uses_main(self, mock_load_config, mock_get_mem0_config):
        """未配置 extraction_model 时，回退使用主对话模型。"""
        mock_get_mem0_config.return_value = {"extraction_model": {}}
        mock_load_config.return_value = {
            "llm": {
                "model": "qwen3.5-plus",
                "api_key": "sk-test-main",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "max_tokens": 8096,
            },
            "embedding": {
                "model": "text-embedding-v4",
                "api_key": "sk-test-emb",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
        }

        from graph.mem0_manager import Mem0Manager
        mgr = Mem0Manager()

        captured_config = {}
        def mock_from_config(cfg):
            nonlocal captured_config
            captured_config = cfg
            return MagicMock()

        with patch.dict("sys.modules", {"mem0": MagicMock()}):
            with patch("mem0.Memory") as MockMem:
                MockMem.from_config = mock_from_config
                mgr.initialize(Path("/tmp/test_mem0"))

        self.assertEqual(captured_config["llm"]["config"]["model"], "qwen3.5-plus")
        self.assertEqual(captured_config["llm"]["config"]["max_tokens"], 8096)
        self.assertNotIn("extra_body", captured_config["llm"]["config"])

    @patch("graph.mem0_manager.get_mem0_config")
    @patch("graph.mem0_manager.load_config")
    def test_enable_thinking_false_patches_llm(self, mock_load_config, mock_get_mem0_config):
        """enable_thinking=false 时通过 post-init patch 注入，而非配置参数。

        mem0 的 OpenAIConfig 不支持 extra_body，所以实现改为初始化后
        _patch_disable_thinking() 包装 generate_response 方法。
        """
        mock_get_mem0_config.return_value = {
            "extraction_model": {
                "model": "qwen3.5-flash",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-test",
                "max_tokens": 512,
                "enable_thinking": False,
            }
        }
        mock_load_config.return_value = {
            "llm": {"model": "qwen3.5-plus", "api_key": "", "base_url": ""},
            "embedding": {"model": "text-embedding-v4", "api_key": "", "base_url": ""},
        }

        from graph.mem0_manager import Mem0Manager
        mgr = Mem0Manager()

        mock_memory = MagicMock()
        mock_llm = MagicMock()
        mock_llm.generate_response = MagicMock(return_value="test")
        mock_memory.llm = mock_llm

        captured_config = {}
        def mock_from_config(cfg):
            nonlocal captured_config
            captured_config = cfg
            return mock_memory

        with patch.dict("sys.modules", {"mem0": MagicMock()}):
            with patch("mem0.Memory") as MockMem:
                MockMem.from_config = mock_from_config
                mgr.initialize(Path("/tmp/test_mem0"))

        # 验证：配置中不应有 extra_body（mem0 不支持）
        self.assertNotIn("extra_body", captured_config["llm"]["config"])

        # 验证：generate_response 已被 patch（不再是原来的 MagicMock）
        # patch 后是闭包函数，调用时会自动注入 extra_body
        self.assertFalse(isinstance(mock_llm.generate_response, MagicMock))


class TestThreadPool(unittest.TestCase):
    """方案二：线程池替代裸 Thread。"""

    def test_agent_has_write_executor(self):
        """AgentManager 应包含 ThreadPoolExecutor 实例。"""
        from graph.agent import AgentManager
        agent = AgentManager()
        self.assertIsInstance(agent._write_executor, ThreadPoolExecutor)

    def test_executor_max_workers(self):
        """线程池最大工作线程数应为 4。"""
        from graph.agent import AgentManager
        agent = AgentManager()
        self.assertEqual(agent._write_executor._max_workers, 4)

    def test_submit_to_executor(self):
        """_schedule_mem0_write 应通过 executor.submit 提交。"""
        from graph.agent import AgentManager
        agent = AgentManager()
        agent._base_dir = Path("/tmp/test")

        submit_called = False
        original_submit = agent._write_executor.submit

        def mock_submit(fn, *args, **kwargs):
            nonlocal submit_called
            submit_called = True

        agent._write_executor.submit = mock_submit
        agent._schedule_mem0_write("hello", "hi", {"enabled": True, "auto_extract": True})

        self.assertTrue(submit_called, "应通过 executor.submit 提交任务")


class TestAsyncRetrieve(unittest.TestCase):
    """方案三：检索异步化。"""

    def test_retriever_has_retrieve_async(self):
        """MemoryRetriever 基类应定义 retrieve_async 方法。"""
        from graph.memory_retriever import MemoryRetriever
        self.assertTrue(hasattr(MemoryRetriever, "retrieve_async"))

    def test_mem0_retriever_has_retrieve_async(self):
        """Mem0Retriever 应继承 retrieve_async。"""
        from graph.memory_retriever import Mem0Retriever
        self.assertTrue(hasattr(Mem0Retriever, "retrieve_async"))

    def test_hybrid_has_retrieve_async_override(self):
        """HybridRetriever 应覆盖 retrieve_async 实现并行检索。"""
        from graph.memory_retriever import HybridRetriever
        self.assertIn("retrieve_async", HybridRetriever.__dict__)

    def test_retrieve_async_returns_results(self):
        """异步检索应正确返回结果。"""
        from graph.memory_retriever import Mem0Retriever

        with patch("graph.memory_retriever.get_mem0_config") as mock_cfg:
            mock_cfg.return_value = {
                "user_id": "default",
                "min_confidence": 0.3,
                "stale_threshold_days": 7,
                "expire_threshold_days": 30,
            }
            retriever = Mem0Retriever(Path("/tmp/test"))

        # get_mem0_manager 在 retrieve() 内部延迟导入
        mock_instance = MagicMock()
        mock_instance.is_ready = True
        mock_instance.search.return_value = [
            {
                "memory": "test fact",
                "score": 0.9,
                "id": "mem1",
                "metadata": {"memory_type": "user", "confidence": 1.0},
                "created_at": "2026-04-09T00:00:00+00:00",
            }
        ]

        with patch("graph.mem0_manager.get_mem0_manager", return_value=mock_instance):
            loop = asyncio.new_event_loop()
            try:
                results = loop.run_until_complete(retriever.retrieve_async("test query"))
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["text"], "test fact")
            finally:
                loop.close()


class TestConsolidationSingleScan(unittest.TestCase):
    """方案四：整合管道单次扫描。"""

    def test_run_consolidation_calls_get_all_once(self):
        """run_consolidation 应只调用一次 get_all()。"""
        from graph.memory_consolidator import MemoryConsolidator

        mock_mgr = MagicMock()
        mock_mgr.get_all.return_value = [
            {"id": "1", "memory": "fact A", "metadata": {"memory_type": "user", "confidence": 1.0, "created_at": "2026-04-09T00:00:00+00:00"}},
            {"id": "2", "memory": "fact B", "metadata": {"memory_type": "user", "confidence": 1.0, "created_at": "2026-04-09T00:00:00+00:00"}},
        ]

        with patch("graph.memory_consolidator.get_mem0_config") as mock_cfg:
            mock_cfg.return_value = {"expire_threshold_days": 30, "min_confidence": 0.3}
            consolidator = MemoryConsolidator(mock_mgr)

        consolidator.run_consolidation()
        self.assertEqual(mock_mgr.get_all.call_count, 1)

    def test_expired_memories_get_deleted(self):
        """过期记忆应被删除。"""
        from graph.memory_consolidator import MemoryConsolidator

        mock_mgr = MagicMock()
        mock_mgr.get_all.return_value = [
            {
                "id": "expired1",
                "memory": "old fact",
                "metadata": {
                    "memory_type": "user",
                    "confidence": 0.1,
                    "created_at": "2026-03-01T00:00:00+00:00",
                },
            },
        ]

        with patch("graph.memory_consolidator.get_mem0_config") as mock_cfg:
            mock_cfg.return_value = {"expire_threshold_days": 30, "min_confidence": 0.3}
            consolidator = MemoryConsolidator(mock_mgr)

        report = consolidator.run_consolidation()
        self.assertEqual(report.expired, 1)
        mock_mgr.delete.assert_called_with("expired1")


class TestVerifyMemorySafety(unittest.TestCase):
    """方案五：verify_memory 先加后删。"""

    def test_add_before_delete(self):
        """verify_memory 应先 add 再 delete（非先删后加）。"""
        from graph.mem0_manager import Mem0Manager

        mgr = Mem0Manager()
        mock_memory = MagicMock()
        mgr._memory = mock_memory

        # mem0 的 get_all 返回 {"results": [...]} 格式
        mock_memory.get_all.return_value = {
            "results": [
                {
                    "id": "mem123",
                    "memory": "test memory",
                    "metadata": {"confidence": 0.7, "memory_type": "user"},
                }
            ]
        }
        mock_memory.add.return_value = {"results": [{"id": "new_mem"}]}

        with patch.object(mgr, "_get_user_id", return_value="default"):
            mgr.verify_memory("mem123")

        call_names = [c[0] for c in mock_memory.method_calls]
        # get_all → add → delete
        self.assertEqual(call_names, ["get_all", "add", "delete"])

    def test_no_delete_if_add_fails(self):
        """add 返回 falsy 值时不应 delete 旧记忆。"""
        from graph.mem0_manager import Mem0Manager

        mgr = Mem0Manager()
        mock_memory = MagicMock()
        mgr._memory = mock_memory

        mock_memory.get_all.return_value = {
            "results": [
                {
                    "id": "mem456",
                    "memory": "important memory",
                    "metadata": {"confidence": 0.7},
                }
            ]
        }
        mock_memory.add.return_value = None

        with patch.object(mgr, "_get_user_id", return_value="default"):
            result = mgr.verify_memory("mem456")

        self.assertFalse(result)
        mock_memory.delete.assert_not_called()


class TestConfigCache(unittest.TestCase):
    """方案六：配置缓存 TTL。"""

    def setUp(self):
        import config
        config._cache = None
        config._cache_ts = 0.0

    def test_load_config_caches(self):
        """连续调用 load_config 应返回缓存值。"""
        import config

        with patch("config.CONFIG_FILE") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps({"rag_mode": True})

            cfg1 = config.load_config()
            cfg2 = config.load_config()

            self.assertEqual(mock_path.read_text.call_count, 1)
            self.assertIs(cfg1, cfg2)

    def test_save_config_invalidates_cache(self):
        """save_config 后缓存应失效。"""
        import config

        with patch("config.CONFIG_FILE") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps({"rag_mode": True})

            config.load_config()
            self.assertEqual(mock_path.read_text.call_count, 1)

            mock_path.write_text.return_value = None
            config.save_config({"rag_mode": False})

            config.load_config()
            self.assertEqual(mock_path.read_text.call_count, 2)

    def test_cache_expires_after_ttl(self):
        """超过 TTL 后缓存应失效。"""
        import config

        with patch("config.CONFIG_FILE") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps({"rag_mode": True})

            config.load_config()
            self.assertEqual(mock_path.read_text.call_count, 1)

            config.load_config()
            self.assertEqual(mock_path.read_text.call_count, 1)

            # 模拟 TTL 过期
            config._cache_ts = time.monotonic() - 31
            config.load_config()
            self.assertEqual(mock_path.read_text.call_count, 2)


if __name__ == "__main__":
    unittest.main()
