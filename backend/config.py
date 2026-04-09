"""Global configuration management — JSON-based persistence."""

import json
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "rag_mode": False,
    "llm": {
        "provider": "dashscope",
        "model": "qwen3.5-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "temperature": 0.7,
        "max_tokens": 4096,
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
    """Load configuration from disk, returning defaults if missing."""
    if not CONFIG_FILE.exists():
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return _deep_merge(_DEFAULT_CONFIG, data)
    except Exception:
        return json.loads(json.dumps(_DEFAULT_CONFIG))


def save_config(config: dict[str, Any]) -> None:
    """Persist configuration to disk."""
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_rag_mode() -> bool:
    """Get current RAG mode setting."""
    return bool(load_config().get("rag_mode", False))


def set_rag_mode(enabled: bool) -> None:
    """Set RAG mode on/off."""
    config = load_config()
    config["rag_mode"] = enabled
    save_config(config)


def get_mem0_config() -> dict[str, Any]:
    """获取 mem0 模块配置，始终返回完整默认值。"""
    config = load_config()
    defaults = _DEFAULT_CONFIG["mem0"]
    mem0_cfg = config.get("mem0", {})
    return _deep_merge(defaults, mem0_cfg)


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
        for key in ("provider", "model", "base_url", "temperature", "max_tokens"):
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

    save_config(config)
