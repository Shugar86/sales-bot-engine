"""Tests for vector memory and semantic search.

Tests SupabaseMemory.add_embedding() and search_semantic() methods.
Uses mocking for database operations or requires real Supabase test project.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.memory.supabase_memory import SupabaseMemory, SupabaseMemoryConfig
from src.memory.embeddings import EmbeddingProvider


@pytest.fixture
def mock_embedding_provider():
    """Create a mock embedding provider that returns predictable vectors."""
    provider = MagicMock(spec=EmbeddingProvider)
    # Return 1024-dim vector of 0.1s for any text
    provider.encode = MagicMock(return_value=[0.1] * 1024)
    provider.encode_batch = MagicMock(return_value=[[0.1] * 1024, [0.2] * 1024])
    provider.get_cache_stats = MagicMock(return_value={"cache_size": 0, "max_cache_size": 512})
    return provider


@pytest.fixture
def mock_supabase_memory(mock_embedding_provider):
    """Create a SupabaseMemory with mocked database."""
    config = SupabaseMemoryConfig(
        database_url="postgresql://test:test@localhost/test",
        persona_name="test_persona",
        embedding_provider=mock_embedding_provider,
    )
    memory = SupabaseMemory(config)

    # Mock the database pool
    memory._pool = MagicMock()

    return memory


class TestEmbeddingsBasics:
    """Test basic embedding operations."""

    @pytest.mark.asyncio
    async def test_add_embedding_calls_provider(self, mock_supabase_memory):
        """add_embedding should call the embedding provider."""
        await mock_supabase_memory.add_embedding(
            user_id="user123",
            chat_id="chat456",
            text="Test message",
            role="user",
        )

        mock_supabase_memory._embedding_provider.encode.assert_called_once_with("Test message")

    @pytest.mark.asyncio
    async def test_add_embedding_skips_without_provider(self):
        """add_embedding should skip if no provider configured."""
        config = SupabaseMemoryConfig(
            database_url="postgresql://test:test@localhost/test",
            persona_name="test_persona",
            embedding_provider=None,
        )
        memory = SupabaseMemory(config)

        # Should not raise
        await memory.add_embedding(
            user_id="user123",
            chat_id="chat456",
            text="Test message",
            role="user",
        )


class TestSemanticSearch:
    """Test semantic search functionality."""

    @pytest.mark.asyncio
    async def test_search_semantic_requires_provider(self):
        """search_semantic should return empty list without provider."""
        config = SupabaseMemoryConfig(
            database_url="postgresql://test:test@localhost/test",
            persona_name="test_persona",
            embedding_provider=None,
        )
        memory = SupabaseMemory(config)

        results = await memory.search_semantic(
            query="test query",
            user_id="user123",
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_search_semantic_encodes_query(self, mock_supabase_memory):
        """search_semantic should encode the query text."""
        # Mock the database fetch to return empty results
        mock_supabase_memory._fetch = AsyncMock(return_value=[])

        await mock_supabase_memory.search_semantic(
            query="find similar messages",
            user_id="user123",
        )

        mock_supabase_memory._embedding_provider.encode.assert_called_once_with("find similar messages")


class TestEmbeddingIntegration:
    """Integration tests requiring real Supabase (optional)."""

    @pytest.mark.skipif(
        not os.getenv("TEST_SUPABASE_URL"),
        reason="TEST_SUPABASE_URL not set, skipping integration tests"
    )
    @pytest.mark.asyncio
    async def test_real_embedding_flow(self):
        """Test with real Supabase (requires TEST_SUPABASE_URL env var)."""
        from src.memory.embeddings import EmbeddingProvider

        # Create real embedding provider
        provider = EmbeddingProvider(cache_size=10)

        config = SupabaseMemoryConfig(
            database_url=os.getenv("TEST_SUPABASE_URL"),
            persona_name="test_integration",
            embedding_provider=provider,
        )

        memory = SupabaseMemory(config)
        await memory.initialize()

        try:
            # Add some embeddings
            await memory.add_embedding(
                user_id="test_user",
                chat_id="test_chat",
                text="Корм для собак с аллергией",
                role="user",
            )

            await memory.add_embedding(
                user_id="test_user",
                chat_id="test_chat",
                text="Гипоаллергенный корм на ягнёнке",
                role="bot",
            )

            # Search
            results = await memory.search_semantic(
                query="корм для аллергиков",
                user_id="test_user",
                top_k=5,
                min_similarity=0.5,
            )

            # Should find at least one result
            assert len(results) >= 1
            assert all("text" in r for r in results)
            assert all("similarity" in r for r in results)

        finally:
            await memory.close()


class TestEmbeddingCache:
    """Test embedding provider cache."""

    def test_cache_returns_same_embedding(self):
        """Same text should return cached embedding."""
        provider = EmbeddingProvider(cache_size=10)

        # First call
        emb1 = provider.encode("Test message for caching")

        # Second call should use cache
        emb2 = provider.encode("Test message for caching")

        assert emb1 == emb2
        stats = provider.get_cache_stats()
        assert stats["cache_size"] == 1

    def test_cache_eviction(self):
        """Cache should evict oldest when full."""
        provider = EmbeddingProvider(cache_size=3)

        # Fill cache
        provider.encode("Message 1")
        provider.encode("Message 2")
        provider.encode("Message 3")

        stats = provider.get_cache_stats()
        assert stats["cache_size"] == 3

        # Add one more - should evict oldest
        provider.encode("Message 4")

        stats = provider.get_cache_stats()
        assert stats["cache_size"] == 3

    def test_clear_cache(self):
        """clear_cache should empty the cache."""
        provider = EmbeddingProvider(cache_size=10)
        provider.encode("Test message")

        assert provider.get_cache_stats()["cache_size"] == 1

        provider.clear_cache()
        assert provider.get_cache_stats()["cache_size"] == 0

    def test_batch_encoding(self):
        """encode_batch should handle multiple texts."""
        provider = EmbeddingProvider(cache_size=10)

        texts = ["Message 1", "Message 2", "Message 3"]
        embeddings = provider.encode_batch(texts)

        assert len(embeddings) == 3
        assert all(len(e) == 1024 for e in embeddings)

    def test_empty_text(self):
        """Empty text should return zero vector."""
        provider = EmbeddingProvider(cache_size=10)

        result = provider.encode("")
        assert result == [0.0] * 1024

        result = provider.encode("   ")
        assert result == [0.0] * 1024
