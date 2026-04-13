"""Mini-OpenClaw Backend — FastAPI Entry Point"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: scan skills, initialize agent, build memory index."""
    from graph.skill_registry import SkillRegistry
    from graph.agent import agent_manager
    from graph.memory_indexer import get_memory_indexer

    # 使用 SkillRegistry 替代旧的 scan_skills
    registry = SkillRegistry.discover(BASE_DIR / "skills")

    agent_manager.initialize(BASE_DIR)

    # Initialize memory indexer for RAG mode
    indexer = get_memory_indexer(BASE_DIR)
    indexer.rebuild_index()

    # 初始化统一记忆检索器
    from graph.unified_memory import get_unified_retriever
    unified_retriever = get_unified_retriever(BASE_DIR)

    # 条件初始化 mem0 记忆系统（失败不阻塞启动）
    try:
        from config import get_mem0_config
        mem0_cfg = get_mem0_config()
        if mem0_cfg.get("enabled"):
            from graph.mem0_manager import get_mem0_manager
            from graph.memory_buffer import get_memory_buffer

            mem0_mgr = get_mem0_manager(BASE_DIR)
            # 将 mem0 客户端注入统一检索器
            unified_retriever.set_mem0_client(mem0_mgr)
            # 恢复缓冲区（从持久化文件）
            buffer = get_memory_buffer(BASE_DIR)
            if buffer.pending_count > 0:
                print(f"📦 发现 {buffer.pending_count} 条缓冲对话待处理")
    except ImportError:
        print("⚠️ mem0 未安装，跳过记忆系统初始化")
    except Exception as e:
        print(f"⚠️ mem0 初始化失败（不影响核心功能）: {e}")

    print("✅ mini OpenClaw backend ready")
    yield


app = FastAPI(title="mini OpenClaw", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.chat import router as chat_router
from api.files import router as files_router
from api.sessions import router as sessions_router
from api.tokens import router as tokens_router
from api.compress import router as compress_router
from api.config_api import router as config_router
from api.eval_api import router as eval_router
from api.skills_api import router as skills_router

app.include_router(chat_router, prefix="/api")
app.include_router(files_router, prefix="/api")  # Must come before skills_router for /versions and /diff routes
app.include_router(eval_router, prefix="/api")  # Must come before skills_router for /eval-result route
app.include_router(skills_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
app.include_router(tokens_router, prefix="/api")
app.include_router(compress_router, prefix="/api")
app.include_router(config_router, prefix="/api")

# mem0 记忆管理 API（条件注册）
try:
    from api.mem0_api import router as mem0_router
    app.include_router(mem0_router, prefix="/api")
except ImportError:
    pass


@app.get("/")
async def root():
    return {"name": "mini OpenClaw", "version": "0.1.0", "status": "running"}
