"""MemoryIndexer — Vector index for MEMORY.md with auto-rebuild on change."""

import hashlib
import os
from pathlib import Path
from typing import Any


class MemoryIndexer:
    """Indexes memory/MEMORY.md for RAG retrieval.

    Uses MD5 hash to detect changes and auto-rebuild the vector index.
    Storage is kept separate from the knowledge base index.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._memory_path = base_dir / "memory" / "MEMORY.md"
        self._storage_dir = base_dir / "storage" / "memory_index"
        self._hash_path = self._storage_dir / ".memory_hash"
        self._index: Any = None

    def _get_file_hash(self) -> str:
        """Get MD5 hash of MEMORY.md."""
        if not self._memory_path.exists():
            return ""
        content = self._memory_path.read_bytes()
        return hashlib.md5(content).hexdigest()

    def _get_stored_hash(self) -> str:
        """Get the stored hash from the last build."""
        if not self._hash_path.exists():
            return ""
        return self._hash_path.read_text(encoding="utf-8").strip()

    def _save_hash(self, hash_value: str) -> None:
        """Save the current hash."""
        self._hash_path.parent.mkdir(parents=True, exist_ok=True)
        self._hash_path.write_text(hash_value, encoding="utf-8")

    def _maybe_rebuild(self) -> None:
        """Rebuild index if MEMORY.md has changed."""
        current_hash = self._get_file_hash()
        stored_hash = self._get_stored_hash()
        if current_hash and current_hash != stored_hash:
            self.rebuild_index()

    def rebuild_index(self) -> None:
        """Read MEMORY.md, split into chunks, build vector index, persist."""
        if not self._memory_path.exists():
            print("⚠️ memory/MEMORY.md not found, skipping index build")
            self._index = None
            return

        try:
            from llama_index.core import (
                Document,
                StorageContext,
                VectorStoreIndex,
            )
            from llama_index.core.node_parser import SentenceSplitter
            from llama_index.core.settings import Settings

            # Use DashScope for embeddings
            try:
                from llama_index.embeddings.dashscope import DashScopeEmbedding
                Settings.embed_model = DashScopeEmbedding(
                    model_name=os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                )
            except ImportError:
                # Fallback to OpenAI compatible mode
                from llama_index.embeddings.openai import OpenAIEmbedding
                Settings.embed_model = OpenAIEmbedding(
                    model="text-embedding-3-small",
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                    api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
                )

            content = self._memory_path.read_text(encoding="utf-8")
            if not content.strip():
                self._index = None
                return

            doc = Document(text=content, metadata={"source": "MEMORY.md"})

            splitter = SentenceSplitter(chunk_size=256, chunk_overlap=32)
            nodes = splitter.get_nodes_from_documents([doc])

            self._storage_dir.mkdir(parents=True, exist_ok=True)
            index = VectorStoreIndex(nodes)
            index.storage_context.persist(persist_dir=str(self._storage_dir))
            self._index = index

            # Save hash
            self._save_hash(self._get_file_hash())
            print(f"🔄 Memory index rebuilt ({len(nodes)} chunks)")

        except ImportError as e:
            print(f"⚠️ LlamaIndex not fully installed: {e}")
            self._index = None
        except Exception as e:
            print(f"⚠️ Memory index build error: {e}")
            self._index = None

    def _load_index(self) -> Any:
        """Load persisted index from storage."""
        if self._index is not None:
            return self._index

        if not self._storage_dir.exists() or not any(self._storage_dir.iterdir()):
            return None

        try:
            from llama_index.core import StorageContext, load_index_from_storage
            from llama_index.core.settings import Settings

            # Use DashScope for embeddings
            try:
                from llama_index.embeddings.dashscope import DashScopeEmbedding
                Settings.embed_model = DashScopeEmbedding(
                    model_name=os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                )
            except ImportError:
                # Fallback to OpenAI compatible mode
                from llama_index.embeddings.openai import OpenAIEmbedding
                Settings.embed_model = OpenAIEmbedding(
                    model="text-embedding-3-small",
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                    api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
                )

            storage_context = StorageContext.from_defaults(
                persist_dir=str(self._storage_dir)
            )
            self._index = load_index_from_storage(storage_context)
            return self._index
        except Exception as e:
            print(f"⚠️ Failed to load memory index: {e}")
            return None

    def retrieve(
        self, query: str, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """Retrieve relevant memory chunks for a query."""
        self._maybe_rebuild()

        index = self._load_index()
        if index is None:
            return []

        try:
            retriever = index.as_retriever(similarity_top_k=top_k)
            nodes = retriever.retrieve(query)

            results: list[dict[str, Any]] = []
            for node in nodes:
                results.append({
                    "text": node.get_text(),
                    "score": f"{node.get_score():.4f}" if node.get_score() else "N/A",
                    "source": node.metadata.get("source", "MEMORY.md"),
                })
            return results
        except Exception as e:
            print(f"⚠️ Memory retrieval error: {e}")
            return []


# Singleton
_instance: MemoryIndexer | None = None


def get_memory_indexer(base_dir: Path) -> MemoryIndexer:
    """Get or create the singleton MemoryIndexer."""
    global _instance
    if _instance is None:
        _instance = MemoryIndexer(base_dir)
    return _instance
