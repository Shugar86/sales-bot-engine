"""MemoryFacade — Unified interface for SupabaseMemory + EmbeddingProvider.

Provides a high-level API that:
- Automatically computes embeddings on record_dm/record_group_message
- Includes semantic context in get_user_context()
- Maintains backward compatibility with UserMemoryStore API

Example:
    facade = await MemoryFacade.create(persona_name="my_bot")

    # Record interaction (embedding computed automatically)
    await facade.record_dm(
        user_id="123",
        username="@user",
        display_name="User",
        message="Какой корм выбрать?",
        response="Рекомендую гипоаллергенный...",
    )

    # Get context (includes semantic search results)
    context = await facade.get_user_context(user_id="123")
"""

import os
from typing import List, Optional

from ..utils.logger import get_logger
from .embeddings import EmbeddingProvider, get_embedding_provider
from .supabase_memory import SupabaseMemory, SupabaseMemoryConfig

logger = get_logger("memory_facade")


class MemoryFacade:
    """High-level memory interface combining storage and embeddings.

    This facade provides a unified API that:
    - Stores messages in Supabase PostgreSQL
    - Automatically generates embeddings for semantic search
    - Includes relevant historical context in responses

    Migration path from UserMemoryStore:
        Old: UserMemoryStore(memory_dir="./data", persona_name="x")
        New: await MemoryFacade.create(persona_name="x")

        Old: memory.record_dm(...)
        New: await facade.record_dm(...)
    """

    def __init__(
        self,
        supabase_memory: SupabaseMemory,
        embedding_provider: EmbeddingProvider,
        persona_name: str,
    ):
        """Initialize the facade.

        Args:
            supabase_memory: Configured SupabaseMemory instance
            embedding_provider: EmbeddingProvider for computing vectors
            persona_name: Name of the persona
        """
        self.memory = supabase_memory
        self.embeddings = embedding_provider
        self.persona_name = persona_name

        logger.info(
            f"MemoryFacade created for {persona_name} "
            f"(embeddings cached: {embedding_provider.get_cache_stats()['cache_size']})"
        )

    @classmethod
    async def create(
        cls,
        persona_name: str,
        database_url: Optional[str] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        embedding_model: Optional[str] = None,
        embedding_device: Optional[str] = None,
        embedding_cache_size: int = 512,
        min_similarity: float = 0.6,
    ) -> "MemoryFacade":
        """Factory method to create and initialize a MemoryFacade.

        Args:
            persona_name: Name of the persona
            database_url: PostgreSQL connection string
            embedding_provider: Optional pre-built provider; if omitted, uses the
                global singleton from :func:`get_embedding_provider`.
            embedding_model: Model name for embeddings
            embedding_device: 'cpu' or 'cuda'
            embedding_cache_size: LRU cache size
            min_similarity: Minimum similarity for semantic search

        Returns:
            Initialized MemoryFacade instance
        """
        if embedding_provider is None:
            embedding_provider = get_embedding_provider(
                model_name=embedding_model,
                device=embedding_device,
                cache_size=embedding_cache_size,
            )

        # Create memory config
        config = SupabaseMemoryConfig(
            database_url=database_url or os.getenv("DATABASE_URL"),
            persona_name=persona_name,
            embedding_provider=embedding_provider,
            min_similarity=min_similarity,
        )

        # Create and initialize supabase memory
        supabase_memory = SupabaseMemory(config)
        await supabase_memory.initialize()

        return cls(
            supabase_memory=supabase_memory,
            embedding_provider=embedding_provider,
            persona_name=persona_name,
        )

    async def close(self) -> None:
        """Close database connections."""
        await self.memory.close()
        logger.info(f"MemoryFacade closed for {self.persona_name}")

    # ========================================
    # CORE API: User Context
    # ========================================

    async def get_user_context(self, user_id: str, include_semantic: bool = True) -> str:
        """Get comprehensive user context for prompts.

        Includes:
        - Traditional context (funnel stage, notes, recommendations)
        - Semantic context (relevant historical messages)

        Args:
            user_id: User identifier
            include_semantic: Whether to include semantic search results

        Returns:
            Context string for LLM prompts
        """
        # Get traditional context from Supabase
        base_context = await self.memory.get_user_context(user_id)

        if not include_semantic:
            return base_context

        # Get semantic context (recent interactions similar to expected query)
        # We use a generic query to find relevant history
        semantic_context = await self._get_semantic_context_for_user(user_id)

        if semantic_context:
            # Combine contexts
            combined = f"{base_context}\n\nРелевантный контекст:\n{semantic_context}"
            return combined

        return base_context

    async def _get_semantic_context_for_user(self, user_id: str) -> str:
        """Get semantic context as a formatted string."""
        # Get recent user messages to find relevant history
        recent = await self.memory.get_recent_messages(f"dm:{user_id}", limit=3)

        if not recent:
            return ""

        # Use most recent message as query
        query = recent[-1].get("text", "")
        if not query:
            return ""

        # Search for similar messages
        similar = await self.memory.search_semantic(
            query=query,
            user_id=user_id,
            top_k=3,
            min_similarity=0.7,  # Higher threshold for context relevance
        )

        if not similar:
            return ""

        # Format results
        parts = []
        for msg in similar:
            role = "Пользователь" if msg["role"] == "user" else "Бот"
            parts.append(f"[{role}]: {msg['text']}")

        return "\n".join(parts)

    # ========================================
    # CORE API: Record Interactions
    # ========================================

    async def record_dm(
        self,
        user_id: str,
        username: str,
        display_name: str,
        message: str,
        response: str = "",
        stage: str = "",
    ) -> None:
        """Record a DM interaction with automatic embedding.

        Args:
            user_id: User identifier
            username: Telegram/VK username
            display_name: Full display name
            message: User's message
            response: Bot's response (if any)
            stage: Funnel stage (if determined)
        """
        # Delegate to supabase memory (handles user updates, DM tracking, embeddings)
        await self.memory.record_dm(
            user_id=user_id,
            username=username,
            display_name=display_name,
            message=message,
            response=response,
            stage=stage,
        )

        logger.debug(f"Recorded DM for {user_id}: {message[:50]}...")

    async def record_group_message(
        self,
        user_id: str,
        username: str,
        display_name: str,
        chat_id: str,
        chat_title: str,
        message: str,
    ) -> None:
        """Record a group message with automatic embedding.

        Args:
            user_id: User identifier
            username: Telegram/VK username
            display_name: Full display name
            chat_id: Chat identifier
            chat_title: Chat title
            message: Message text
        """
        await self.memory.record_group_message(
            user_id=user_id,
            username=username,
            display_name=display_name,
            chat_id=chat_id,
            chat_title=chat_title,
            message=message,
        )

        logger.debug(f"Recorded group message in {chat_id}: {message[:50]}...")

    # ========================================
    # CORE API: Deduplication
    # ========================================

    async def is_processed(self, chat_id: str, message_id: int, text: str) -> bool:
        """Check if message was already processed."""
        return await self.memory.is_processed(chat_id, message_id, text)

    async def mark_processed(self, chat_id: str, message_id: int, text: str) -> None:
        """Mark message as processed."""
        await self.memory.mark_processed(chat_id, message_id, text)

    # ========================================
    # CORE API: Anti-repeat
    # ========================================

    async def is_repeating_response(
        self, chat_id: str, response: str, threshold: float = 0.8
    ) -> bool:
        """Check if response is too similar to recent ones."""
        return await self.memory.is_repeating_response(chat_id, response, threshold)

    async def record_bot_response(self, chat_id: str, response: str) -> None:
        """Record bot response for anti-repeat tracking."""
        await self.memory.record_bot_response(chat_id, response)

    # ========================================
    # SEMANTIC SEARCH API
    # ========================================

    async def search_semantic(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        min_similarity: Optional[float] = None,
    ) -> List[dict]:
        """Search for semantically similar messages.

        Args:
            query: Search query text
            user_id: User to search for
            top_k: Maximum results
            min_similarity: Minimum similarity (uses config default if None)

        Returns:
            List of dicts with: text, role, similarity, ts
        """
        return await self.memory.search_semantic(
            query=query,
            user_id=user_id,
            top_k=top_k,
            min_similarity=min_similarity,
        )

    async def search_semantic_group(
        self,
        query: str,
        chat_id: str,
        top_k: int = 5,
        min_similarity: Optional[float] = None,
    ) -> List[dict]:
        """Search for semantically similar messages in a group chat.

        Uses match_group_messages() which filters by chat_id instead of user_id.

        Args:
            query: Search query text
            chat_id: Group chat to search in
            top_k: Maximum results
            min_similarity: Minimum similarity threshold

        Returns:
            List of dicts with: text, role, similarity, ts
        """
        return await self.memory.search_semantic_group(
            query=query,
            chat_id=chat_id,
            top_k=top_k,
            min_similarity=min_similarity,
        )

    async def get_semantic_context(
        self, query: str, user_id: str, top_k: int = 3
    ) -> List[str]:
        """Get semantic context as text strings.

        Convenience method that returns just the text content.

        Args:
            query: Text to find similar messages for
            user_id: User to search within
            top_k: Number of results

        Returns:
            List of message texts
        """
        return await self.memory.get_semantic_context(query, user_id, top_k)

    # ========================================
    # Funnel & Tool Tracking
    # ========================================

    async def set_funnel_stage(self, user_id: str, stage: str) -> None:
        """Set the funnel stage for a user."""
        await self.memory.set_funnel_stage(user_id, stage)

    async def get_funnel_stage(self, user_id: str) -> str:
        """Get the current funnel stage."""
        return await self.memory.get_funnel_stage(user_id)

    async def is_first_response(self, user_id: str, chat_id: str) -> bool:
        """Check if this is the first response to a user."""
        return await self.memory.is_first_response(user_id, chat_id)

    async def set_last_tool(
        self, user_id: str, tool_name: str, tool_args: Optional[dict] = None
    ) -> None:
        """Record the last tool used."""
        await self.memory.set_last_tool(user_id, tool_name, tool_args)

    async def get_last_tool(self, user_id: str) -> str:
        """Get the last tool used for a user."""
        return await self.memory.get_last_tool(user_id)

    async def get_last_tool_args(self, user_id: str) -> dict:
        """Get the last tool arguments."""
        return await self.memory.get_last_tool_args(user_id)

    # ========================================
    # Notes & Recommendations
    # ========================================

    async def add_note(self, user_id: str, note: str) -> None:
        """Add a note for a user."""
        await self.memory.add_note(user_id, note)

    async def get_notes(self, user_id: str, limit: int = 10) -> List[str]:
        """Get notes for a user."""
        return await self.memory.get_notes(user_id, limit)

    async def add_recommendation(self, user_id: str, recommendation: str) -> None:
        """Record a recommendation made."""
        await self.memory.add_recommendation(user_id, recommendation)

    async def get_recommendations(self, user_id: str, limit: int = 10) -> List[str]:
        """Get past recommendations for a user."""
        return await self.memory.get_recommendations(user_id, limit)

    # ========================================
    # Chat Context
    # ========================================

    async def get_recent_messages(
        self, chat_id: str, limit: int = 5
    ) -> List[dict]:
        """Get recent messages from a chat."""
        return await self.memory.get_recent_messages(chat_id, limit)

    async def get_dm_transcript_for_prompt(self, user_id: str, max_chars: int = 1500) -> str:
        """Recent DM thread text for the ``dm_history`` prompt slot (not profile metadata)."""
        return await self.memory.get_dm_transcript_tail(user_id, max_chars=max_chars)

    async def get_dm_inbound_streak(self, user_id: str) -> int:
        """Inbound DM streak without a completed bot reply (persists in ``users.extra``)."""
        return await self.memory.get_dm_inbound_streak(user_id)

    async def increment_dm_inbound_streak(self, user_id: str) -> int:
        """Increment streak when a DM is admitted past antispam."""
        return await self.memory.increment_dm_inbound_streak(user_id)

    async def reset_dm_inbound_streak(self, user_id: str) -> None:
        """Reset streak after the bot successfully sends a DM."""
        await self.memory.reset_dm_inbound_streak(user_id)

    # ========================================
    # Statistics & Maintenance
    # ========================================

    async def get_stats(self) -> dict:
        """Get memory statistics."""
        return await self.memory.get_stats()

    async def cleanup_old_data(self, max_age_hours: int = 48) -> dict:
        """Clean up old processed messages and responses."""
        return await self.memory.cleanup_old_data(max_age_hours)

    def get_embedding_cache_stats(self) -> dict:
        """Get embedding provider cache statistics."""
        return self.embeddings.get_cache_stats()

    def clear_embedding_cache(self) -> None:
        """Clear the embedding cache."""
        self.embeddings.clear_cache()


# Factory cache for persona facades
_facade_cache: dict[str, MemoryFacade] = {}


async def get_facade_for_persona(persona_name: str) -> MemoryFacade:
    """Get or create a MemoryFacade for a persona.

    Args:
        persona_name: Name of the persona

    Returns:
        MemoryFacade instance
    """
    if persona_name not in _facade_cache:
        facade = await MemoryFacade.create(persona_name)
        _facade_cache[persona_name] = facade

    return _facade_cache[persona_name]


def reset_facade_cache():
    """Reset the facade cache (for testing)."""
    global _facade_cache
    _facade_cache = {}
    logger.info("Facade cache reset")
