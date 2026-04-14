"""辅助模型统一配置测试 — 覆盖 get_auxiliary_model_config() 和 create_auxiliary_llm()。

验证：
- 优先级链：auxiliary_model > summary_model > mem0.extraction_model > 默认值
- 向后兼容：旧 summary_model 配置仍能正常工作
- 工厂函数降级策略：无 API key 返回 None、构造异常返回 None
"""

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试用临时配置文件路径
_TEST_CONFIG = Path(__file__).resolve().parent.parent / "config.json"
_BACKUP_CONFIG = Path(__file__).resolve().parent.parent / "config.json.bak"


class _ConfigFixture(unittest.TestCase):
    """配置测试基类：每次测试前备份/恢复 config.json，清空缓存。"""

    def setUp(self):
        import config
        config._cache = None
        config._cache_ts = 0.0
        if _TEST_CONFIG.exists():
            shutil.copy(_TEST_CONFIG, _BACKUP_CONFIG)

    def tearDown(self):
        import config
        config._cache = None
        config._cache_ts = 0.0
        if _BACKUP_CONFIG.exists():
            shutil.copy(_BACKUP_CONFIG, _TEST_CONFIG)
            _BACKUP_CONFIG.unlink()


# ─── Task 4.1: get_auxiliary_model_config() 向后兼容测试 ────────────────────


class TestGetAuxiliaryModelConfigPriority(_ConfigFixture):
    """优先级链测试：auxiliary_model > summary_model > mem0.extraction_model > 默认值。"""

    @patch("config.CONFIG_FILE")
    def test_priority_1_auxiliary_model(self, mock_path):
        """最高优先级：auxiliary_model 配置。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "auxiliary_model": {"model": "qwen3.5-plus", "temperature": 0.5},
            "summary_model": {"model": "qwen-turbo", "temperature": 0},
            "mem0": {"extraction_model": {"model": "qwen-plus"}},
        })

        from config import get_auxiliary_model_config
        result = get_auxiliary_model_config()

        self.assertEqual(result["model"], "qwen3.5-plus")
        self.assertEqual(result["temperature"], 0.5)

    @patch("config.CONFIG_FILE")
    def test_priority_2_summary_model_fallback(self, mock_path):
        """次优先级：auxiliary_model 未配置时回退到 summary_model。"""
        mock_path.exists.return_value = True
        # auxiliary_model 不存在或 model 为空
        mock_path.read_text.return_value = json.dumps({
            "summary_model": {"model": "qwen-turbo", "temperature": 0.3},
            "mem0": {"extraction_model": {"model": "qwen-plus"}},
        })

        from config import get_auxiliary_model_config
        result = get_auxiliary_model_config()

        self.assertEqual(result["model"], "qwen-turbo")
        self.assertEqual(result["temperature"], 0.3)

    @patch("config.CONFIG_FILE")
    def test_priority_2_summary_model_empty_auxiliary(self, mock_path):
        """auxiliary_model 存在但 model 为空时回退到 summary_model。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "auxiliary_model": {"model": "", "temperature": 0},
            "summary_model": {"model": "qwen-turbo", "temperature": 0},
        })

        from config import get_auxiliary_model_config
        result = get_auxiliary_model_config()

        self.assertEqual(result["model"], "qwen-turbo")

    @patch("config.CONFIG_FILE")
    def test_priority_3_mem0_extraction_model(self, mock_path):
        """第三优先级：auxiliary_model 和 summary_model.model 都为空时回退到 mem0.extraction_model。

        注意：_DEFAULT_CONFIG 包含 summary_model 默认值，_deep_merge 会合并它，
        所以需要显式设置 summary_model.model 为空才能穿透到第三优先级。
        """
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "summary_model": {"model": "", "temperature": 0},
            "mem0": {"extraction_model": {"model": "qwen-plus", "max_tokens": 256}},
        })

        from config import get_auxiliary_model_config
        result = get_auxiliary_model_config()

        self.assertEqual(result["model"], "qwen-plus")
        self.assertEqual(result["temperature"], 0)

    @patch("config.CONFIG_FILE")
    def test_priority_4_default(self, mock_path):
        """最低优先级：所有 model 配置都为空时使用默认值。

        注意：_DEFAULT_CONFIG 包含 summary_model 默认值，需显式清空。
        实际生产环境中 summary_model 始终存在，此测试验证极端边界场景。
        """
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "summary_model": {"model": "", "temperature": 0},
            "mem0": {"extraction_model": {}},
        })

        from config import get_auxiliary_model_config
        result = get_auxiliary_model_config()

        self.assertEqual(result["model"], "qwen3.5-flash")
        self.assertEqual(result["temperature"], 0)

    @patch("config.CONFIG_FILE")
    def test_priority_4_default_no_file(self, mock_path):
        """配置文件不存在时使用 _DEFAULT_CONFIG 合并后的值。

        由于 _DEFAULT_CONFIG 包含 summary_model.model=qwen-turbo，
        无文件时优先级链在第二级命中，不会到达默认值 qwen3.5-flash。
        这是 _deep_merge 合并机制决定的预期行为。
        """
        mock_path.exists.return_value = False

        from config import get_auxiliary_model_config
        result = get_auxiliary_model_config()

        # _DEFAULT_CONFIG 中 summary_model.model = "qwen-turbo"
        self.assertEqual(result["model"], "qwen-turbo")

    @patch("config.CONFIG_FILE")
    def test_backward_compat_summary_model_migration(self, mock_path):
        """向后兼容测试：旧 config.json 只有 summary_model 配置，迁移后仍正常工作。"""
        mock_path.exists.return_value = True
        # 模拟旧版配置：没有 auxiliary_model，只有 summary_model
        mock_path.read_text.return_value = json.dumps({
            "llm": {
                "model": "qwen3.5-plus",
                "api_key": "sk-test",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            "summary_model": {
                "model": "qwen-turbo",
                "temperature": 0,
            },
        })

        from config import get_auxiliary_model_config
        result = get_auxiliary_model_config()

        self.assertEqual(result["model"], "qwen-turbo")
        self.assertEqual(result["temperature"], 0)

    @patch("config.CONFIG_FILE")
    def test_temperature_defaults_to_zero(self, mock_path):
        """未指定 temperature 时默认为 0。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "auxiliary_model": {"model": "qwen3.5-flash"},
        })

        from config import get_auxiliary_model_config
        result = get_auxiliary_model_config()

        self.assertEqual(result["temperature"], 0)


# ─── Task 4.2: create_auxiliary_llm() 工厂函数测试 ─────────────────────────


class TestCreateAuxiliaryLlm(_ConfigFixture):
    """工厂函数测试：正常创建、降级策略。"""

    @patch("config.CONFIG_FILE")
    @patch.dict(os.environ, {}, clear=False)
    def test_normal_creation(self, mock_path):
        """有 API key 时应成功创建 LLM 实例。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "llm": {
                "api_key": "sk-test-key",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            "auxiliary_model": {"model": "qwen3.5-flash", "temperature": 0},
        })

        from config import create_auxiliary_llm
        llm = create_auxiliary_llm()

        self.assertIsNotNone(llm)

    @patch("config.CONFIG_FILE")
    def test_no_api_key_returns_none(self, mock_path):
        """无 API key 时应返回 None。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "llm": {"api_key": "", "base_url": "https://example.com"},
            "auxiliary_model": {"model": "qwen3.5-flash", "temperature": 0},
        })

        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            from config import create_auxiliary_llm
            llm = create_auxiliary_llm()

        self.assertIsNone(llm)

    @patch("config.CONFIG_FILE")
    @patch("langchain_openai.ChatOpenAI", side_effect=Exception("构造失败"))
    def test_constructor_failure_returns_none(self, mock_chat, mock_path):
        """ChatOpenAI 构造异常时应返回 None。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "llm": {
                "api_key": "sk-test",
                "base_url": "https://example.com",
            },
            "auxiliary_model": {"model": "qwen3.5-flash", "temperature": 0},
        })

        from config import create_auxiliary_llm
        llm = create_auxiliary_llm()

        self.assertIsNone(llm)

    @patch("config.CONFIG_FILE")
    @patch.dict(os.environ, {}, clear=False)
    def test_uses_auxiliary_model_config(self, mock_path):
        """应使用 auxiliary_model 配置而非主模型。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "llm": {
                "model": "qwen3.5-plus",
                "api_key": "sk-test-key",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            "auxiliary_model": {"model": "qwen-turbo", "temperature": 0.5},
        })

        with patch("langchain_openai.ChatOpenAI") as MockChat:
            mock_instance = MagicMock()
            MockChat.return_value = mock_instance

            from config import create_auxiliary_llm
            result = create_auxiliary_llm()

            MockChat.assert_called_once_with(
                model="qwen-turbo",
                api_key="sk-test-key",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                temperature=0.5,
            )
            self.assertEqual(result, mock_instance)

    @patch("config.CONFIG_FILE")
    @patch.dict(os.environ, {"DASHSCOPE_API_KEY": "sk-env-key"}, clear=False)
    def test_env_var_api_key_fallback(self, mock_path):
        """配置文件无 API key 时应回退到 DASHSCOPE_API_KEY 环境变量。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "llm": {"api_key": "", "base_url": ""},
            "auxiliary_model": {"model": "qwen3.5-flash", "temperature": 0},
        })

        with patch("langchain_openai.ChatOpenAI") as MockChat:
            MockChat.return_value = MagicMock()

            from config import create_auxiliary_llm
            create_auxiliary_llm()

            call_kwargs = MockChat.call_args[1]
            self.assertEqual(call_kwargs["api_key"], "sk-env-key")

    @patch("config.CONFIG_FILE")
    @patch.dict(os.environ, {"DASHSCOPE_BASE_URL": "https://custom.api.com/v1"}, clear=False)
    def test_env_var_base_url_fallback(self, mock_path):
        """配置文件无 base_url 时应回退到 DASHSCOPE_BASE_URL 环境变量。"""
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps({
            "llm": {
                "api_key": "sk-test",
                "base_url": "",
            },
            "auxiliary_model": {"model": "qwen3.5-flash", "temperature": 0},
        })

        with patch("langchain_openai.ChatOpenAI") as MockChat:
            MockChat.return_value = MagicMock()

            from config import create_auxiliary_llm
            create_auxiliary_llm()

            call_kwargs = MockChat.call_args[1]
            self.assertEqual(call_kwargs["base_url"], "https://custom.api.com/v1")


if __name__ == "__main__":
    unittest.main()
