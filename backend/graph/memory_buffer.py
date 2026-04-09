"""MemoryBuffer — 对话缓冲区 + 智能截流器。

累积对话轮次，智能判断记忆写入时机。
不逐轮触发 mem0 写入，而是批次化管理，减少 API 成本和记忆碎片。

多级触发机制：
- 立即触发：用户明确指令（"记住这个"）或强烈纠正（"我说了不要"）
- 轮次触发：缓冲区累积 >= N 轮（默认 5）
- 时间触发：距上次写入 > T 秒（默认 300）
- 会话结束触发：会话关闭时
- 启动恢复：上次会话有未处理的缓冲
"""

import json
import re
import time
from pathlib import Path
from typing import Any

from config import get_mem0_config


# 显式保存关键词模式
EXPLICIT_SAVE_PATTERNS = [
    r"记住[：:]",
    r"记住这个",
    r"记住，",
    r"以后都",
    r"不要忘记",
    r"别忘了",
    r"帮我记",
    r"记下来",
    r"重要[：:]",
    r"注意事项[：:]",
    r"remember\s+(this|that|:)",
    r"save\s+(this|that)",
    r"don'?t\s+forget",
    r"note\s+(this|that|down)",
    r"keep\s+in\s+mind",
]

# 强烈纠正关键词模式
STRONG_CORRECTION_PATTERNS = [
    r"我说了?\s*(不要|别|不)",
    r"又(犯|来|是)",
    r"不是这个意思",
    r"怎么还",
    r"(停下来|停|停一下).*(听|看|想)",
    r"I\s+said\s+no",
    r"stop\s+(doing|it)",
    r"not\s+what\s+I\s+meant",
]


class ConversationTurn:
    """一轮对话记录。"""

    __slots__ = ("user_message", "assistant_message", "session_id", "timestamp")

    def __init__(
        self,
        user_message: str,
        assistant_message: str,
        session_id: str,
        timestamp: float | None = None,
    ) -> None:
        self.user_message = user_message
        self.assistant_message = assistant_message
        self.session_id = session_id
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_message": self.user_message,
            "assistant_message": self.assistant_message,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationTurn":
        return cls(
            user_message=data["user_message"],
            assistant_message=data["assistant_message"],
            session_id=data["session_id"],
            timestamp=data.get("timestamp", time.time()),
        )


class MemoryBuffer:
    """对话缓冲区：累积对话轮次，智能判断写入时机。"""

    def __init__(self, storage_path: Path) -> None:
        self._buffer: list[ConversationTurn] = []
        self._last_flush_time: float = time.time()
        self._storage_path = storage_path

        cfg = get_mem0_config()
        self._buffer_size = cfg.get("buffer_size", 5)
        self._flush_interval = cfg.get("flush_interval_seconds", 300)

        # 编译正则
        self._explicit_patterns = [re.compile(p, re.IGNORECASE) for p in EXPLICIT_SAVE_PATTERNS]
        self._correction_patterns = [re.compile(p, re.IGNORECASE) for p in STRONG_CORRECTION_PATTERNS]

        # 从持久化文件恢复
        self._load()

    def add_turn(
        self,
        user_message: str,
        assistant_message: str,
        session_id: str,
    ) -> None:
        """添加一轮对话到缓冲区。"""
        turn = ConversationTurn(user_message, assistant_message, session_id)
        self._buffer.append(turn)
        self._persist()

    def check_immediate_trigger(self, user_message: str) -> bool:
        """检查用户消息是否触发了立即写入。

        匹配条件：显式保存指令 或 强烈纠正。
        """
        return self._match_patterns(user_message, self._explicit_patterns) or \
               self._match_patterns(user_message, self._correction_patterns)

    def should_flush(self) -> bool:
        """判断是否应该触发批次提取（非立即触发）。"""
        # 轮次触发
        if len(self._buffer) >= self._buffer_size:
            return True

        # 时间触发（仅在有缓冲内容时）
        if self._buffer and (time.time() - self._last_flush_time) > self._flush_interval:
            return True

        return False

    def flush(self) -> list[ConversationTurn]:
        """提取并清空缓冲区，返回待处理的对话批次。"""
        turns = list(self._buffer)
        self._buffer = []
        self._last_flush_time = time.time()
        self._persist()
        return turns

    def flush_all(self) -> list[ConversationTurn]:
        """强制提取所有缓冲内容（会话结束时调用）。"""
        return self.flush()

    @property
    def pending_count(self) -> int:
        """当前缓冲区中待处理的轮次数。"""
        return len(self._buffer)

    def _match_patterns(self, text: str, patterns: list[re.Pattern]) -> bool:
        """检查文本是否匹配任一正则模式。"""
        return any(p.search(text) for p in patterns)

    def _persist(self) -> None:
        """将缓冲区状态持久化到文件。"""
        try:
            data = {
                "buffer": [t.to_dict() for t in self._buffer],
                "last_flush_time": self._last_flush_time,
            }
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._storage_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"⚠️ 缓冲区持久化失败: {e}")

    def _load(self) -> None:
        """从持久化文件恢复缓冲区状态。"""
        if not self._storage_path.exists():
            return

        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            self._buffer = [ConversationTurn.from_dict(t) for t in data.get("buffer", [])]
            self._last_flush_time = data.get("last_flush_time", time.time())
            if self._buffer:
                print(f"📦 恢复了 {len(self._buffer)} 条缓冲对话")
        except Exception as e:
            print(f"⚠️ 缓冲区恢复失败: {e}")
            self._buffer = []


# 单例
_instance: MemoryBuffer | None = None


def get_memory_buffer(base_dir: Path | None = None) -> MemoryBuffer:
    """获取或创建 MemoryBuffer 单例。"""
    global _instance
    if _instance is None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent
        storage_path = base_dir / "storage" / "memory_buffer.json"
        _instance = MemoryBuffer(storage_path)
    return _instance
