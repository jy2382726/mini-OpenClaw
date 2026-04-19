"""Global configuration management — JSON-based persistence with TTL cache."""

import copy
import json
import os
import time
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

_cache: dict[str, Any] | None = None
_cache_ts: float = 0.0
_CACHE_TTL: float = 30.0

_DEFAULT_CONFIG: dict[str, Any] = {
    "rag_mode": False,
    "llm": {
        "provider": "dashscope",
        "model": "qwen3.5-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "temperature": 0.7,
        "max_tokens": 4096,
        "context_window": 131072,
    },
    "embedding": {
        "provider": "dashscope",
        "model": "text-embedding-v4",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
    },
    "rag": {
        "top_k": 3,
        "similarity_threshold": 0.7,
    },
    "compression": {
        "ratio": 0.5,
    },
    "summary_model": {
        "model": "qwen-turbo",
        "temperature": 0,
    },
    "middleware": {
        "tool_output_budget": {
            "enabled": True,
            "safe_ratio": 0.25,
            "pressure_ratio": 0.45,
            "budgets": {
                "terminal": 2000,
                "python_repl": 1500,
                "fetch_url": 3000,
                "read_file": 2000,
                "search_knowledge": 1000,
            },
        },
        "summarization": {
            "enabled": True,
            "trigger_tokens": 8000,
            "keep_messages": 10,
        },
        "tool_filter": {
            "enabled": True,
        },
        "tool_call_limit": {
            "enabled": True,
            "limits": {
                "terminal": 10,
                "python_repl": 5,
            },
        },
    },
    "features": {
        "task_state": True,
        "unified_memory": True,
    },
    "auxiliary_model": {
        "model": "qwen3.5-flash",
        "temperature": 0,
    },
    "mem0": {
        "enabled": False,
        "mode": "legacy",  # "legacy" | "mem0" | "hybrid"
        "auto_extract": True,
        "user_id": "default",
        # 智能截流参数
        "buffer_size": 5,
        "flush_interval_seconds": 300,
        # 离线整合参数
        "consolidation_interval_hours": 24,
        "consolidation_threshold": 50,
        # 防御性读取参数
        "stale_threshold_days": 7,
        "expire_threshold_days": 30,
        "min_confidence": 0.3,
        # 独立抽取模型（可选，不配置则复用主对话模型）
        "extraction_model": {},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base, preserving nested defaults."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    """Load configuration from disk, returning defaults if missing.

    使用 TTL 缓存（30秒）避免每次请求都读磁盘。
    """
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    if not CONFIG_FILE.exists():
        _cache = copy.deepcopy(_DEFAULT_CONFIG)
    else:
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            _cache = _deep_merge(_DEFAULT_CONFIG, data)
        except Exception:
            _cache = copy.deepcopy(_DEFAULT_CONFIG)
    _cache_ts = now
    return _cache


def save_config(config: dict[str, Any]) -> None:
    """Persist configuration to disk and invalidate cache."""
    global _cache, _cache_ts
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _cache = None
    _cache_ts = 0.0


def get_context_window() -> int:
    """获取模型上下文窗口大小（token 数），默认 131072（128K）。"""
    config = load_config()
    return int(config.get("llm", {}).get("context_window", 131072))


def get_rag_mode() -> bool:
    """Get current RAG mode setting."""
    return bool(load_config().get("rag_mode", False))


def set_rag_mode(enabled: bool) -> None:
    """Set RAG mode on/off."""
    config = load_config()
    config["rag_mode"] = enabled
    save_config(config)


def get_auxiliary_model_config() -> dict[str, Any]:
    """获取辅助模型配置（摘要、标题生成、记忆提取等统一使用）。

    配置来源：config.json 的 auxiliary_model 段，缺省使用 _DEFAULT_CONFIG。
    """
    config = load_config()
    aux_cfg = config.get("auxiliary_model", _DEFAULT_CONFIG["auxiliary_model"])
    return {
        "model": aux_cfg.get("model", _DEFAULT_CONFIG["auxiliary_model"]["model"]),
        "temperature": aux_cfg.get("temperature", _DEFAULT_CONFIG["auxiliary_model"]["temperature"]),
    }


def create_auxiliary_llm():
    """创建辅助模型 LLM 实例，所有辅助任务统一调用。

    复用主模型的 api_key 和 base_url。无 API key 时返回 None。
    构造失败时也返回 None，确保调用方安全降级。
    """
    from langchain_openai import ChatOpenAI

    aux_cfg = get_auxiliary_model_config()
    config = load_config()

    # 复用主模型的 API 配置
    llm_config = config.get("llm", {})
    api_key = llm_config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
    api_base = llm_config.get("base_url") or os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    if not api_key:
        return None

    try:
        return ChatOpenAI(
            model=aux_cfg["model"],
            api_key=api_key,
            base_url=api_base,
            temperature=aux_cfg["temperature"],
        )
    except Exception as e:
        print(f"⚠️ 辅助模型创建失败: {e}")
        return None


def get_mem0_config() -> dict[str, Any]:
    """获取 mem0 模块配置，始终返回完整默认值。"""
    config = load_config()
    defaults = _DEFAULT_CONFIG["mem0"]
    mem0_cfg = config.get("mem0", {})
    return _deep_merge(defaults, mem0_cfg)


def get_middleware_config() -> dict[str, Any]:
    """获取中间件配置，始终返回完整默认值。"""
    config = load_config()
    defaults = _DEFAULT_CONFIG["middleware"]
    mw_cfg = config.get("middleware", {})
    return _deep_merge(defaults, mw_cfg)


def get_features_config() -> dict[str, Any]:
    """获取功能开关配置。"""
    config = load_config()
    defaults = _DEFAULT_CONFIG["features"]
    feat_cfg = config.get("features", {})
    return _deep_merge(defaults, feat_cfg)


def set_mem0_config(updates: dict[str, Any]) -> None:
    """更新 mem0 配置（部分更新，自动合并）。"""
    config = load_config()
    if "mem0" not in config:
        config["mem0"] = {}
    config["mem0"].update(updates)
    save_config(config)


def mask_api_key(key: str) -> str:
    """Mask API key for display: sk-***...last4"""
    if not key or len(key) < 8:
        return "***"
    return f"{key[:3]}***...{key[-4:]}"


def get_settings_for_display() -> dict[str, Any]:
    """Get settings with masked API keys for frontend display."""
    config = load_config()
    result = {
        "llm": {
            **config.get("llm", {}),
            "api_key_masked": mask_api_key(config.get("llm", {}).get("api_key", "")),
        },
        "embedding": {
            **config.get("embedding", {}),
            "api_key_masked": mask_api_key(config.get("embedding", {}).get("api_key", "")),
        },
        "rag": {
            "enabled": config.get("rag_mode", False),
            **config.get("rag", {}),
        },
        "compression": config.get("compression", {}),
        "auxiliary_model": config.get("auxiliary_model", _DEFAULT_CONFIG["auxiliary_model"]),
        "summary_model": config.get("summary_model", _DEFAULT_CONFIG["summary_model"]),
        "middleware": get_middleware_config(),
        "features": get_features_config(),
        "mem0": config.get("mem0", _DEFAULT_CONFIG["mem0"]),
    }
    # Remove raw API keys from response
    result["llm"].pop("api_key", None)
    result["embedding"].pop("api_key", None)
    return result


def update_settings(updates: dict[str, Any]) -> None:
    """Update settings from frontend, handling partial updates and API key logic."""
    config = load_config()

    if "llm" in updates:
        llm_update = updates["llm"]
        if "llm" not in config:
            config["llm"] = {}
        for key in ("provider", "model", "base_url", "temperature", "max_tokens", "context_window"):
            if key in llm_update:
                config["llm"][key] = llm_update[key]
        # Only update API key if a non-empty value is provided
        if llm_update.get("api_key"):
            config["llm"]["api_key"] = llm_update["api_key"]

    if "embedding" in updates:
        emb_update = updates["embedding"]
        if "embedding" not in config:
            config["embedding"] = {}
        for key in ("provider", "model", "base_url"):
            if key in emb_update:
                config["embedding"][key] = emb_update[key]
        if emb_update.get("api_key"):
            config["embedding"]["api_key"] = emb_update["api_key"]

    if "rag" in updates:
        rag_update = updates["rag"]
        if "rag" not in config:
            config["rag"] = {}
        for key in ("top_k", "similarity_threshold"):
            if key in rag_update:
                config["rag"][key] = rag_update[key]
        if "enabled" in rag_update:
            config["rag_mode"] = rag_update["enabled"]

    if "compression" in updates:
        comp_update = updates["compression"]
        if "compression" not in config:
            config["compression"] = {}
        if "ratio" in comp_update:
            config["compression"]["ratio"] = comp_update["ratio"]

    if "auxiliary_model" in updates:
        aux_update = updates["auxiliary_model"]
        if "auxiliary_model" not in config:
            config["auxiliary_model"] = {}
        for key in ("model", "temperature"):
            if key in aux_update:
                config["auxiliary_model"][key] = aux_update[key]

    if "mem0" in updates:
        mem0_update = updates["mem0"]
        if "mem0" not in config:
            config["mem0"] = {}
        for key in (
            "enabled", "mode", "auto_extract", "user_id",
            "buffer_size", "flush_interval_seconds",
            "consolidation_interval_hours", "consolidation_threshold",
            "stale_threshold_days", "expire_threshold_days", "min_confidence",
        ):
            if key in mem0_update:
                config["mem0"][key] = mem0_update[key]

    if "middleware" in updates:
        mw_update = updates["middleware"]
        if "middleware" not in config:
            config["middleware"] = {}
        for mw_name, mw_vals in mw_update.items():
            if isinstance(mw_vals, dict):
                if mw_name not in config["middleware"]:
                    config["middleware"][mw_name] = {}
                config["middleware"][mw_name].update(mw_vals)

    if "features" in updates:
        feat_update = updates["features"]
        if "features" not in config:
            config["features"] = {}
        config["features"].update(feat_update)

    save_config(config)
