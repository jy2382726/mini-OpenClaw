"""mem0 优化联调测试 — 真实环境端到端验证。

启动方式：
  cd backend && source .venv/bin/activate
  python tests/test_mem0_integration.py
"""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 禁用 mem0 遥测，避免与运行中的后端服务产生 ~/.mem0/migrations_qdrant 锁冲突
os.environ["MEM0_TELEMETRY"] = "False"

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


class TestMem0Integration(unittest.TestCase):
    """mem0 优化联调测试 — 所有测试在同一个类中顺序执行，共享 mem0 实例。

    使用独立的临时目录，避免与运行中的后端服务产生 Qdrant 锁冲突。

    测试顺序：
    1. 初始化 + 配置验证
    2. 写入 + 延迟测量
    3. 同步检索
    4. 异步检索
    5. 并行异步检索
    6. verify_memory 先加后删
    7. 整合管道单次扫描
    8. 配置缓存
    9. 线程池
    """

    mgr = None
    base_dir = Path(__file__).resolve().parent.parent
    test_user = "default"  # 与 mem0_manager._get_user_id() 保持一致
    _tmpdir = None

    @classmethod
    def setUpClass(cls):
        """初始化 mem0 管理器（只初始化一次，所有测试共享）。
        使用独立临时目录避免 Qdrant 本地模式的并发锁冲突。
        """
        import graph.mem0_manager as mm
        mm._instance = None
        from graph.mem0_manager import get_mem0_manager
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="mem0_test_")
        tmp_path = Path(cls._tmpdir.name)
        # 创建 workspace 文件让 prompt_builder 正常工作
        (tmp_path / "workspace").mkdir(exist_ok=True)
        (tmp_path / "workspace" / "SOUL.md").write_text("测试", encoding="utf-8")
        (tmp_path / "workspace" / "IDENTITY.md").write_text("测试", encoding="utf-8")
        (tmp_path / "workspace" / "USER.md").write_text("测试", encoding="utf-8")
        (tmp_path / "workspace" / "AGENTS.md").write_text("测试", encoding="utf-8")
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(exist_ok=True)
        cls.mgr = get_mem0_manager(tmp_path)

    @classmethod
    def tearDownClass(cls):
        """清理临时目录。"""
        if cls._tmpdir:
            cls._tmpdir.cleanup()

    # ==================== 方案一：独立轻量模型 ====================

    def test_01_init_success(self):
        """mem0 应成功初始化。"""
        self.assertTrue(self.mgr.is_ready, "mem0 未成功初始化，检查 API Key 和依赖")

    def test_02_extraction_model_config(self):
        """config.json 中 extraction_model 应为 qwen3.5-flash + enable_thinking=false。"""
        from config import load_config
        config = load_config()
        ext = config.get("mem0", {}).get("extraction_model", {})
        self.assertEqual(ext.get("model"), "qwen3.5-flash")
        self.assertFalse(ext.get("enable_thinking", True))

    def test_03_batch_add_performance(self):
        """batch_add 写入延迟应 < 30s（关闭 thinking 后目标 < 15s）。"""
        if not self.mgr.is_ready:
            self.skipTest("mem0 未初始化")

        turns = [
            {"user_message": "我叫张三，我喜欢用 Python 编程。",
             "assistant_message": "你好张三！Python 是很好的选择。", "session_id": "int_test"},
            {"user_message": "我习惯用中文写注释，变量名保持英文。",
             "assistant_message": "明白，好习惯。", "session_id": "int_test"},
            {"user_message": "我的项目使用 FastAPI + Next.js 14 技术栈。",
             "assistant_message": "已了解。", "session_id": "int_test"},
        ]

        start = time.time()
        results = self.mgr.batch_add(turns, user_id=self.test_user)
        elapsed = time.time() - start

        print(f"\n  ⏱️ batch_add: {elapsed:.1f}s")
        self.assertLess(elapsed, 30, f"写入延迟 {elapsed:.1f}s 超过 30s")

    # ==================== 方案三：检索异步化 ====================

    def test_04_sync_search(self):
        """同步检索应找到之前写入的记忆。"""
        if not self.mgr.is_ready:
            self.skipTest("mem0 未初始化")

        results = self.mgr.search("张三 编程", user_id=self.test_user, limit=3)
        self.assertTrue(len(results) > 0, "应检索到记忆")
        print(f"\n  📝 同步检索 {len(results)} 条")
        for r in results:
            print(f"     [{r.get('score', 0):.4f}] {r.get('memory', '')[:50]}")

    def test_05_async_retrieve(self):
        """异步检索应返回结果。"""
        from graph.memory_retriever import Mem0Retriever

        retriever = Mem0Retriever(self.base_dir)
        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(retriever.retrieve_async("技术栈", top_k=3))
        finally:
            loop.close()

        self.assertTrue(len(results) > 0, "异步检索应返回结果")
        print(f"\n  📝 异步检索 {len(results)} 条")

    def test_06_async_parallel(self):
        """并行异步检索应正常工作。"""
        from graph.memory_retriever import Mem0Retriever

        retriever = Mem0Retriever(self.base_dir)

        async def parallel():
            t1 = asyncio.create_task(retriever.retrieve_async("Python", top_k=2))
            t2 = asyncio.create_task(retriever.retrieve_async("FastAPI", top_k=2))
            return await asyncio.gather(t1, t2)

        loop = asyncio.new_event_loop()
        try:
            r1, r2 = loop.run_until_complete(parallel())
        finally:
            loop.close()

        self.assertTrue(len(r1) > 0 or len(r2) > 0, "至少一组有结果")
        print(f"\n  📝 并行: Python={len(r1)}, FastAPI={len(r2)}")

    # ==================== 方案五：verify 安全加固 ====================

    def test_07_verify_add_before_delete(self):
        """verify_memory 应成功执行先加后删。"""
        if not self.mgr.is_ready:
            self.skipTest("mem0 未初始化")

        all_mems = self.mgr.get_all(user_id=self.test_user)
        if not all_mems:
            self.skipTest("无可用记忆")

        target = all_mems[0]
        mem_id = target.get("id")

        # verify 应成功
        result = self.mgr.verify_memory(mem_id)
        self.assertTrue(result, "verify_memory 应成功")

        # 验证后旧 ID 不应再存在（被替换为新版本）
        updated = self.mgr.get_all(user_id=self.test_user)
        updated_ids = [m.get("id") for m in updated]
        self.assertNotIn(mem_id, updated_ids, "旧记忆 ID 应已被替换")
        print(f"\n  ✅ verify 先加后删成功（旧 ID 已替换）")

    # ==================== 方案四：整合单次扫描 ====================

    def test_08_consolidation_single_scan(self):
        """整合管道应只调用 1 次 get_all。"""
        if not self.mgr.is_ready:
            self.skipTest("mem0 未初始化")

        from graph.memory_consolidator import MemoryConsolidator
        consolidator = MemoryConsolidator(self.mgr)

        original = self.mgr.get_all
        call_count = 0

        def counting(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original(*args, **kwargs)

        self.mgr.get_all = counting
        try:
            report = consolidator.run_consolidation()
        finally:
            self.mgr.get_all = original

        self.assertEqual(call_count, 1, f"get_all 应调用 1 次，实际 {call_count} 次")
        print(f"\n  📊 整合: total={report.total_memories}, dup={report.duplicates_found}, "
              f"merged={report.merged}, expired={report.expired}")

    # ==================== 方案六：配置缓存 ====================

    def test_09_config_cache(self):
        """load_config 应缓存，save_config 应失效缓存。"""
        import config
        config._cache = None
        config._cache_ts = 0.0

        # 缓存命中
        c1 = config.load_config()
        c2 = config.load_config()
        self.assertIs(c1, c2, "连续 load 应返回同一缓存对象")

        # save 失效
        config.save_config(c1)
        c3 = config.load_config()
        self.assertIsNot(c1, c3, "save 后应重新加载")
        print(f"\n  ✅ 配置缓存正常")

    # ==================== 方案二：线程池 ====================

    def test_10_thread_pool(self):
        """AgentManager 应使用 ThreadPoolExecutor。"""
        from concurrent.futures import ThreadPoolExecutor
        from graph.agent import AgentManager

        agent = AgentManager()
        self.assertIsInstance(agent._write_executor, ThreadPoolExecutor)
        self.assertEqual(agent._write_executor._max_workers, 4)

        results = []
        executor = ThreadPoolExecutor(max_workers=4)

        def task(v):
            time.sleep(0.05)
            results.append(v)

        futures = [executor.submit(task, i) for i in range(4)]
        for f in futures:
            f.result(timeout=5)

        self.assertEqual(len(results), 4)
        executor.shutdown(wait=False)
        print(f"\n  ✅ 线程池正常")


if __name__ == "__main__":
    unittest.main(verbosity=2)
