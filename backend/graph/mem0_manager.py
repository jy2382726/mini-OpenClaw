"""Mem0Manager — mem0 智能记忆管理器单例。

封装 mem0 Memory 实例的创建、配置、生命周期管理。
复用项目现有的 DashScope API（OpenAI 兼容模式）作为 LLM 和 Embedding 提供者。
使用 Qdrant 本地磁盘模式持久化向量数据。
"""

import os
from datetime import datetime, timezone
from pathlib import Path

# 确保 .env 被加载（app.py 中也调用了，这里做兜底）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from typing import Any

from config import get_mem0_config, load_config


class Mem0Manager:
    """mem0 智能记忆管理器。

    支持结构化记忆元数据：
    - memory_type: user / feedback / project / reference
    - why: 为什么记住（来源事件/上下文）
    - how_to_apply: 何时应用此记忆
    - confidence: 置信度（0-1）
    - created_at: 创建时间
    - last_verified_at: 上次验证时间
    """

    def __init__(self) -> None:
        self._base_dir: Path | None = None
        self._memory: Any = None

    def initialize(self, base_dir: Path) -> None:
        """初始化 mem0 Memory 实例，复用 DashScope API 配置。"""
        self._base_dir = base_dir
        mem0_cfg = get_mem0_config()
        config = load_config()

        llm_config = config.get("llm", {})
        emb_config = config.get("embedding", {})

        # 优先使用 mem0 独立抽取模型，未配置则复用主对话模型
        extraction_cfg = mem0_cfg.get("extraction_model") or {}
        if extraction_cfg.get("model"):
            llm_model = extraction_cfg.get("model")
            llm_api_key = extraction_cfg.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
            llm_base_url = extraction_cfg.get("base_url") or os.getenv(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            max_tokens = extraction_cfg.get("max_tokens", 512)
        else:
            llm_model = llm_config.get("model") or os.getenv("DASHSCOPE_MODEL", "qwen3.5-plus")
            llm_api_key = llm_config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
            llm_base_url = llm_config.get("base_url") or os.getenv(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            max_tokens = llm_config.get("max_tokens", 1500)

        emb_model = emb_config.get("model") or "text-embedding-v4"
        emb_api_key = emb_config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
        emb_base_url = emb_config.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"

        # mem0 数据存储目录
        mem0_data_dir = base_dir / "storage" / "mem0_data"
        mem0_data_dir.mkdir(parents=True, exist_ok=True)

        mem0_config = {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": llm_model,
                    "api_key": llm_api_key,
                    "openai_base_url": llm_base_url,
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": emb_model,
                    "api_key": emb_api_key,
                    "openai_base_url": emb_base_url,
                    "embedding_dims": 1024,  # text-embedding-v4 的维度
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "mini_openclaw_memories",
                    "path": str(mem0_data_dir),
                    "on_disk": True,
                    "embedding_model_dims": 1024,  # 必须与 embedder 的 embedding_dims 一致
                },
            },
            "fact_extraction_prompt": self._build_extraction_prompt(),
        }

        # 关闭思考模式（qwen3.5-flash 默认启用 thinking，对结构化抽取不必要且增加延迟）
        if extraction_cfg.get("enable_thinking") is False:
            mem0_config["llm"]["config"]["extra_body"] = {"enable_thinking": False}

        try:
            from mem0 import Memory
            self._memory = Memory.from_config(mem0_config)
            print(f"🧠 mem0 记忆管理器已初始化 (数据目录: {mem0_data_dir})")
        except ImportError:
            print("⚠️ mem0 未安装，跳过初始化。安装方式: pip install mem0ai")
            self._memory = None
        except Exception as e:
            print(f"⚠️ mem0 初始化失败: {e}")
            self._memory = None

    @property
    def is_ready(self) -> bool:
        """mem0 是否已成功初始化。"""
        return self._memory is not None

    def _build_extraction_prompt(self) -> str:
        """构建结构化事实提取 Prompt。"""
        return """从对话中提取值得长期记住的事实。每条记忆必须包含以下结构化信息：

1. fact: 值得记住的事实或规则（一句话概括）
2. type: 分类标签，必须从以下四选一：
   - user: 用户偏好、习惯、个人信息
   - feedback: 用户对AI回复的正向/负向反馈（喜欢什么/不喜欢什么）
   - project: 项目相关的技术上下文（架构、技术栈、文件位置、设计决策）
   - reference: 外部引用（文档链接、API地址、参考资料）
3. why: 为什么这条记忆值得保留（具体的事件、对话上下文或用户原话）
4. how_to_apply: 在什么场景下应该应用这条记忆（什么情况下适用/不适用）

排除以下内容：寒暄、临时性指令（如"帮我查一下天气"）、纯情绪表达、重复信息。
冲突处理：如果新提取的事实与已有知识矛盾，在 why 中说明变化原因。

以 JSON 格式返回，结构如下：
{"facts": [{"fact": "...", "type": "...", "why": "...", "how_to_apply": "..."}]}"""

    def add(
        self,
        messages: list[dict[str, str]],
        user_id: str = "default",
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """添加对话到 mem0，自动提取结构化事实。

        Args:
            messages: 对话消息列表 [{"role": "user/assistant", "content": "..."}]
            user_id: 用户标识
            metadata: 额外元数据
            session_id: 来源会话 ID

        Returns:
            提取的记忆列表
        """
        if not self.is_ready:
            return []

        meta = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "confidence": 1.0,
            "last_verified_at": None,
        }
        if session_id:
            meta["source_session_id"] = session_id
        if metadata:
            meta.update(metadata)

        try:
            result = self._memory.add(messages, user_id=user_id, metadata=meta)
            # mem0 返回 {"results": [...]} 格式
            if isinstance(result, dict) and "results" in result:
                return result["results"]
            return result if isinstance(result, list) else []
        except Exception as e:
            print(f"⚠️ mem0 添加记忆失败: {e}")
            return []

    def add_structured(
        self,
        fact: str,
        memory_type: str,
        why: str = "",
        how_to_apply: str = "",
        user_id: str = "default",
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """显式添加一条结构化记忆（由 Agent 工具调用）。

        Args:
            fact: 事实内容
            memory_type: 记忆类型 (user/feedback/project/reference)
            why: 为什么记住
            how_to_apply: 何时应用
            user_id: 用户标识
            session_id: 来源会话

        Returns:
            添加的记忆对象，失败返回 None
        """
        if not self.is_ready:
            return None

        metadata = {
            "memory_type": memory_type,
            "why": why,
            "how_to_apply": how_to_apply,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "confidence": 1.0,
            "last_verified_at": None,
        }
        if session_id:
            metadata["source_session_id"] = session_id

        try:
            result = self._memory.add(fact, user_id=user_id, metadata=metadata)
            # mem0 返回 {"results": [...]} 格式
            if isinstance(result, dict) and "results" in result:
                return result["results"][0] if result["results"] else None
            return result
        except Exception as e:
            print(f"⚠️ mem0 结构化写入失败: {e}")
            return None

    def batch_add(
        self,
        turns: list[Any],
        user_id: str = "default",
    ) -> list[dict[str, Any]]:
        """批量处理对话轮次，一次性提交给 mem0 提取。

        Args:
            turns: 对话轮次列表（ConversationTurn 对象或字典）
            user_id: 用户标识

        Returns:
            所有提取的记忆列表
        """
        if not self.is_ready or not turns:
            return []

        # 将多轮对话合并为一条消息列表
        all_messages: list[dict[str, str]] = []
        last_session_id: str | None = None
        for turn in turns:
            # 兼容 ConversationTurn 对象和字典两种类型
            if hasattr(turn, "user_message"):
                user_msg = turn.user_message
                asst_msg = turn.assistant_message
                last_session_id = getattr(turn, "session_id", None)
            else:
                user_msg = turn["user_message"]
                asst_msg = turn["assistant_message"]
                last_session_id = turn.get("session_id")

            all_messages.append({"role": "user", "content": user_msg})
            all_messages.append({"role": "assistant", "content": asst_msg})

        return self.add(all_messages, user_id=user_id, session_id=last_session_id)

    def search(
        self,
        query: str,
        user_id: str = "default",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """语义搜索记忆，返回带完整元数据的结果。

        Args:
            query: 搜索查询
            user_id: 用户标识
            limit: 最大返回数量

        Returns:
            记忆列表，每条包含 memory, score, id, metadata
        """
        if not self.is_ready:
            return []

        try:
            results = self._memory.search(query, user_id=user_id, limit=limit)
            # mem0 返回 {"results": [...]} 格式
            if isinstance(results, dict) and "results" in results:
                return results["results"]
            return results if isinstance(results, list) else []
        except Exception as e:
            print(f"⚠️ mem0 搜索失败: {e}")
            return []

    def get_all(
        self,
        user_id: str = "default",
    ) -> list[dict[str, Any]]:
        """获取用户的所有记忆。

        Returns:
            记忆列表
        """
        if not self.is_ready:
            return []

        try:
            results = self._memory.get_all(user_id=user_id)
            # mem0 返回 {"results": [...]} 格式
            if isinstance(results, dict) and "results" in results:
                return results["results"]
            return results if isinstance(results, list) else []
        except Exception as e:
            print(f"⚠️ mem0 获取记忆列表失败: {e}")
            return []

    def delete(self, memory_id: str) -> bool:
        """删除指定记忆。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否成功
        """
        if not self.is_ready:
            return False

        try:
            self._memory.delete(memory_id)
            return True
        except Exception as e:
            print(f"⚠️ mem0 删除记忆失败: {e}")
            return False

    def verify_memory(self, memory_id: str) -> bool:
        """验证记忆仍然有效，更新 last_verified_at 和 confidence。

        通过 delete + re-add 实现（mem0 暂无原生 update metadata 接口）。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否成功
        """
        if not self.is_ready:
            return False

        try:
            # 获取原始记忆内容
            raw = self._memory.get_all(user_id=self._get_user_id())
            all_memories = raw.get("results", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
            target = None
            for mem in all_memories:
                if mem.get("id") == memory_id:
                    target = mem
                    break

            if not target:
                print(f"⚠️ 记忆 {memory_id} 不存在，无法验证")
                return False

            # 保存原始内容和元数据
            original_text = target.get("memory", "")
            original_meta = dict(target.get("metadata", {}))

            # 更新验证时间和置信度
            original_meta["last_verified_at"] = datetime.now(timezone.utc).isoformat()
            original_meta["confidence"] = min(1.0, original_meta.get("confidence", 0.7) + 0.3)

            # 先添加新版本，成功后再删除旧版本（避免中途失败丢失数据）
            new_result = self._memory.add(original_text, user_id=self._get_user_id(), metadata=original_meta)
            if new_result:
                self._memory.delete(memory_id)
                print(f"✅ 记忆 {memory_id} 已验证（置信度: {original_meta['confidence']}）")
                return True
            else:
                print(f"⚠️ 记忆 {memory_id} 新版本添加失败，保留原始记忆")
                return False
        except Exception as e:
            print(f"⚠️ mem0 验证记忆失败: {e}")
            return False

    def _get_user_id(self) -> str:
        """获取配置中的 user_id。"""
        try:
            from config import get_mem0_config
            return get_mem0_config().get("user_id", "default")
        except Exception:
            return "default"


# 单例
_instance: Mem0Manager | None = None


def get_mem0_manager(base_dir: Path | None = None) -> Mem0Manager:
    """获取或创建 Mem0Manager 单例。"""
    global _instance
    if _instance is None:
        _instance = Mem0Manager()
        if base_dir:
            _instance.initialize(base_dir)
    return _instance
