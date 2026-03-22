"""Memory module — User memory, deduplication, and semantic search.

This module provides:
- UserMemoryStore: Legacy SQLite-based memory (deprecated, kept for migration)
- SupabaseMemory: New PostgreSQL-based memory with asyncpg
- EmbeddingProvider: Text-to-vector encoding with caching
- MemoryFacade: Unified high-level API combining storage + embeddings

Migration path:
    Old: from src.memory.user_memory import UserMemoryStore
    New: from src.memory import MemoryFacade, SupabaseMemory
"""

# Legacy (kept for backward compatibility during migration)
from .user_memory import UserMemoryStore

# New Supabase-based memory
from .supabase_memory import SupabaseMemory, SupabaseMemoryConfig, get_memory_for_persona

# Embeddings
from .embeddings import EmbeddingProvider, get_embedding_provider, reset_embedding_provider

# Unified facade
from .memory_facade import MemoryFacade, get_facade_for_persona

__all__ = [
    # Legacy
    "UserMemoryStore",
    # Supabase
    "SupabaseMemory",
    "SupabaseMemoryConfig",
    "get_memory_for_persona",
    # Embeddings
    "EmbeddingProvider",
    "get_embedding_provider",
    "reset_embedding_provider",
    # Facade
    "MemoryFacade",
    "get_facade_for_persona",
]
