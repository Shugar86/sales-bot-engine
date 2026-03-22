"""SupabaseMemory — Async PostgreSQL backend for user memory and deduplication.

Replaces UserMemoryStore + DeduplicationStore with unified Supabase backend.
Features:
- Async PostgreSQL via asyncpg
- Connection pooling
- Semantic search via pgvector
- Full backward compatibility with UserMemoryStore API

Environment:
    DATABASE_URL=postgresql://postgres:...@db.xxxx.supabase.co:5432/postgres
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional

import asyncpg

from ..utils.logger import get_logger
from .embeddings import EmbeddingProvider

logger = get_logger("supabase_memory")


@dataclass
class SupabaseMemoryConfig:
    """Configuration for SupabaseMemory."""

    database_url: str = ""
    persona_name: str = "default"
    embedding_provider: Optional[EmbeddingProvider] = None
    min_similarity: float = 0.6  # Minimum similarity for semantic search
    max_semantic_results: int = 5

    def __post_init__(self):
        if not self.database_url:
            self.database_url = os.getenv(
                "DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5432/postgres"
            )


class SupabaseMemory:
    """Unified memory backend using Supabase PostgreSQL.

    Combines user memory tracking, deduplication, and semantic search
    into a single async PostgreSQL backend.

    Tables used:
        - users: User profiles and funnel tracking
        - user_notes: Notes per user
        - recommendations: What was recommended
        - group_messages: Group chat history
        - dm_summaries: DM conversation summaries
        - processed_messages: Deduplication
        - bot_responses: Anti-repeat tracking
        - message_embeddings: Vector storage for semantic search

    Example:
        memory = SupabaseMemory(config=SupabaseMemoryConfig(
            database_url="postgresql://...",
            persona_name="kormoved"
        ))
        await memory.initialize()

        # Record interaction
        await memory.record_dm(
            user_id="123",
            username="@user",
            display_name="User Name",
            message="Привет!",
            response="Здравствуйте! Чем могу помочь?",
            stage="greeting"
        )
    """

    def __init__(self, config: Optional[SupabaseMemoryConfig] = None):
        """Initialize SupabaseMemory.

        Args:
            config: Configuration object. Uses defaults if not provided.
        """
        self.config = config or SupabaseMemoryConfig()
        self.persona_name = self.config.persona_name

        self._pool: Optional[asyncpg.Pool] = None
        self._embedding_provider = self.config.embedding_provider

        logger.info(f"SupabaseMemory initialized for persona: {self.persona_name}")

    async def initialize(self) -> None:
        """Initialize the database connection pool.

        Must be called before using any other methods.
        """
        if self._pool is not None:
            return

        try:
            self._pool = await asyncpg.create_pool(
                self.config.database_url,
                min_size=2,
                max_size=10,
                command_timeout=30,
            )
            logger.info(f"Database pool created for {self.persona_name}")
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}")
            raise

    async def close(self) -> None:
        """Close the database connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info(f"Database pool closed for {self.persona_name}")

    async def _execute(self, query: str, *args) -> str:
        """Execute a query and return command status."""
        if not self._pool:
            await self.initialize()

        async with self._pool.acquire() as conn:
            try:
                result = await conn.execute(query, *args)
                return result
            except asyncpg.PostgresError as e:
                logger.error(f"Database error: {e}, query: {query[:100]}")
                raise

    async def _fetchrow(self, query: str, *args) -> Optional[asyncpg.Record]:
        """Fetch a single row."""
        if not self._pool:
            await self.initialize()

        async with self._pool.acquire() as conn:
            try:
                return await conn.fetchrow(query, *args)
            except asyncpg.PostgresError as e:
                logger.error(f"Database error: {e}, query: {query[:100]}")
                raise

    async def _fetch(self, query: str, *args) -> List[asyncpg.Record]:
        """Fetch multiple rows."""
        if not self._pool:
            await self.initialize()

        async with self._pool.acquire() as conn:
            try:
                return await conn.fetch(query, *args)
            except asyncpg.PostgresError as e:
                logger.error(f"Database error: {e}, query: {query[:100]}")
                raise

    # ========================================
    # USER MANAGEMENT
    # ========================================

    async def update_user(
        self,
        user_id: str,
        username: str = "",
        display_name: str = "",
        extra: Optional[dict] = None,
    ) -> None:
        """Update or insert user record.

        Uses the update_user_interaction stored procedure for atomic upsert.
        """
        extra_json = json.dumps(extra) if extra else "{}"

        await self._execute(
            """
            SELECT update_user_interaction($1, $2, $3, $4)
            """,
            user_id,
            self.persona_name,
            username,
            display_name,
        )

        # Update extra fields if provided
        if extra:
            await self._execute(
                """
                UPDATE users SET extra = extra || $3::jsonb
                WHERE user_id = $1 AND persona_name = $2
                """,
                user_id,
                self.persona_name,
                extra_json,
            )

    async def get_user(self, user_id: str) -> Optional[dict]:
        """Get user record by ID."""
        row = await self._fetchrow(
            """
            SELECT * FROM users
            WHERE user_id = $1 AND persona_name = $2
            """,
            user_id,
            self.persona_name,
        )
        return dict(row) if row else None

    async def set_funnel_stage(self, user_id: str, stage: str) -> None:
        """Set the funnel stage for a user."""
        await self._execute(
            """
            UPDATE users SET funnel_stage = $3
            WHERE user_id = $1 AND persona_name = $2
            """,
            user_id,
            self.persona_name,
            stage,
        )

    async def get_funnel_stage(self, user_id: str) -> str:
        """Get the current funnel stage for a user."""
        row = await self._fetchrow(
            """
            SELECT funnel_stage FROM users
            WHERE user_id = $1 AND persona_name = $2
            """,
            user_id,
            self.persona_name,
        )
        return row["funnel_stage"] if row else "unknown"

    async def is_first_response(self, user_id: str, chat_id: str) -> bool:
        """Check if this is the first response to a user in a chat."""
        row = await self._fetchrow(
            """
            SELECT total_interactions, has_dm FROM users
            WHERE user_id = $1 AND persona_name = $2
            """,
            user_id,
            self.persona_name,
        )
        if not row:
            return True
        # First response only if no previous DM interactions
        return row["total_interactions"] == 0 or not row["has_dm"]

    # ========================================
    # NOTES
    # ========================================

    async def add_note(self, user_id: str, note: str) -> None:
        """Add a note for a user."""
        await self._execute(
            """
            INSERT INTO user_notes (user_id, persona_name, note)
            VALUES ($1, $2, $3)
            """,
            user_id,
            self.persona_name,
            note,
        )

    async def get_notes(self, user_id: str, limit: int = 10) -> List[str]:
        """Get notes for a user."""
        rows = await self._fetch(
            """
            SELECT note FROM user_notes
            WHERE user_id = $1 AND persona_name = $2
            ORDER BY ts DESC
            LIMIT $3
            """,
            user_id,
            self.persona_name,
            limit,
        )
        return [row["note"] for row in rows]

    # ========================================
    # RECOMMENDATIONS
    # ========================================

    async def add_recommendation(self, user_id: str, recommendation: str) -> None:
        """Record a recommendation made to a user."""
        await self._execute(
            """
            INSERT INTO recommendations (user_id, persona_name, recommendation)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, persona_name, recommendation) DO NOTHING
            """,
            user_id,
            self.persona_name,
            recommendation,
        )

    async def get_recommendations(self, user_id: str, limit: int = 10) -> List[str]:
        """Get past recommendations for a user."""
        rows = await self._fetch(
            """
            SELECT recommendation FROM recommendations
            WHERE user_id = $1 AND persona_name = $2
            ORDER BY ts DESC
            LIMIT $3
            """,
            user_id,
            self.persona_name,
            limit,
        )
        return [row["recommendation"] for row in rows]

    # ========================================
    # DM TRACKING
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
        """Record a DM interaction.

        Also updates user stats and stores embeddings if provider available.
        """
        # Update user
        await self.update_user(user_id, username, display_name, {"has_dm": True})

        # Update funnel stage if provided
        if stage:
            await self.set_funnel_stage(user_id, stage)

        # Update DM summary
        summary_addition = f"\nUser: {message}"
        if response:
            summary_addition += f"\nBot: {response}"

        await self._execute(
            """
            INSERT INTO dm_summaries (user_id, persona_name, summary)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, persona_name) DO UPDATE SET
                summary = dm_summaries.summary || $3
            """,
            user_id,
            self.persona_name,
            summary_addition,
        )

        # Store embeddings if available
        if self._embedding_provider:
            # Store user message embedding
            await self.add_embedding(
                user_id=user_id,
                chat_id=f"dm:{user_id}",
                text=message,
                role="user",
            )

            # Store bot response embedding if present
            if response:
                await self.add_embedding(
                    user_id=user_id,
                    chat_id=f"dm:{user_id}",
                    text=response,
                    role="bot",
                )

    async def get_dm_summary(self, user_id: str) -> dict:
        """Get DM summary for a user."""
        row = await self._fetchrow(
            """
            SELECT * FROM dm_summaries
            WHERE user_id = $1 AND persona_name = $2
            """,
            user_id,
            self.persona_name,
        )
        return dict(row) if row else {"summary": "", "last_tool": None, "last_tool_args": {}}

    # ========================================
    # GROUP MESSAGES
    # ========================================

    async def record_group_message(
        self,
        user_id: str,
        username: str,
        display_name: str,
        chat_id: str,
        chat_title: str,
        message: str,
    ) -> None:
        """Record a group message."""
        # Update user
        await self.update_user(user_id, username, display_name)

        # Store message
        await self._execute(
            """
            INSERT INTO group_messages
            (user_id, persona_name, chat_id, chat_title, text)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id,
            self.persona_name,
            chat_id,
            chat_title,
            message,
        )

        # Store embedding if available
        if self._embedding_provider:
            await self.add_embedding(
                user_id=user_id,  # Pass user_id for proper filtering in search_semantic
                chat_id=chat_id,
                text=message,
                role="user",
            )

    async def get_group_messages(
        self, chat_id: str, limit: int = 10
    ) -> List[dict]:
        """Get recent messages from a group chat."""
        rows = await self._fetch(
            """
            SELECT * FROM group_messages
            WHERE persona_name = $1 AND chat_id = $2
            ORDER BY ts DESC
            LIMIT $3
            """,
            self.persona_name,
            chat_id,
            limit,
        )
        return [dict(row) for row in rows]

    # ========================================
    # TOOL TRACKING
    # ========================================

    async def set_last_tool(
        self, user_id: str, tool_name: str, tool_args: Optional[dict] = None
    ) -> None:
        """Record the last tool used for a user."""
        args_json = json.dumps(tool_args) if tool_args else "{}"
        await self._execute(
            """
            INSERT INTO dm_summaries (user_id, persona_name, last_tool, last_tool_args)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (user_id, persona_name) DO UPDATE SET
                last_tool = $3,
                last_tool_args = $4::jsonb
            """,
            user_id,
            self.persona_name,
            tool_name,
            args_json,
        )

    async def get_last_tool(self, user_id: str) -> str:
        """Get the last tool used for a user."""
        row = await self._fetchrow(
            """
            SELECT last_tool FROM dm_summaries
            WHERE user_id = $1 AND persona_name = $2
            """,
            user_id,
            self.persona_name,
        )
        return row["last_tool"] if row else ""

    async def get_last_tool_args(self, user_id: str) -> dict:
        """Get the last tool arguments for a user."""
        row = await self._fetchrow(
            """
            SELECT last_tool_args FROM dm_summaries
            WHERE user_id = $1 AND persona_name = $2
            """,
            user_id,
            self.persona_name,
        )
        if row and row["last_tool_args"]:
            return dict(row["last_tool_args"])
        return {}

    # ========================================
    # CONTEXT BUILDING
    # ========================================

    async def get_user_context(self, user_id: str) -> str:
        """Build a user context string for prompts.

        Includes: funnel stage, notes, recommendations, DM summary.
        """
        parts = []

        # User info
        user = await self.get_user(user_id)
        if user:
            parts.append(f"Стадия воронки: {user.get('funnel_stage', 'unknown')}")
            parts.append(f"Всего взаимодействий: {user.get('total_interactions', 0)}")

        # Notes
        notes = await self.get_notes(user_id, limit=5)
        if notes:
            parts.append(f"Заметки: {'; '.join(notes)}")

        # Recommendations
        recs = await self.get_recommendations(user_id, limit=3)
        if recs:
            parts.append(f"Уже рекомендовал: {'; '.join(recs)}")

        # DM summary (last portion)
        dm = await self.get_dm_summary(user_id)
        if dm.get("summary"):
            # Take last 500 chars
            summary = dm["summary"][-500:]
            parts.append(f"История диалога: {summary}")

        return "\n".join(parts) if parts else "Новый пользователь"

    async def get_recent_messages(self, chat_id: str, limit: int = 5) -> List[dict]:
        """Get recent messages from a chat for context."""
        rows = await self._fetch(
            """
            SELECT user_id, text, ts FROM group_messages
            WHERE persona_name = $1 AND chat_id = $2
            ORDER BY ts DESC
            LIMIT $3
            """,
            self.persona_name,
            chat_id,
            limit,
        )
        return [dict(row) for row in reversed(rows)]  # Oldest first

    # ========================================
    # DEDUPLICATION
    # ========================================

    def _hash_message(self, chat_id: str, message_id: int, text: str) -> str:
        """Compute message hash for deduplication."""
        content = f"{self.persona_name}:{chat_id}:{message_id}:{text[:100]}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    async def is_processed(self, chat_id: str, message_id: int, text: str) -> bool:
        """Check if message has been processed."""
        message_hash = self._hash_message(chat_id, message_id, text)

        row = await self._fetchrow(
            """
            SELECT 1 FROM processed_messages
            WHERE message_hash = $1
            """,
            message_hash,
        )
        return row is not None

    async def mark_processed(
        self, chat_id: str, message_id: int, text: str
    ) -> None:
        """Mark message as processed."""
        message_hash = self._hash_message(chat_id, message_id, text)

        await self._execute(
            """
            INSERT INTO processed_messages
            (message_hash, persona_name, chat_id, message_id, text_preview)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (message_hash) DO NOTHING
            """,
            message_hash,
            self.persona_name,
            chat_id,
            message_id,
            text[:100],
        )

    # ========================================
    # ANTI-REPEAT
    # ========================================

    async def record_bot_response(
        self, chat_id: str, response_text: str
    ) -> None:
        """Record bot response for anti-repeat tracking."""
        if not response_text:
            return

        response_hash = hashlib.sha256(
            response_text.encode()
        ).hexdigest()[:16]

        await self._execute(
            """
            INSERT INTO bot_responses
            (persona_name, chat_id, response_hash, response_preview)
            VALUES ($1, $2, $3, $4)
            """,
            self.persona_name,
            chat_id,
            response_hash,
            response_text[:200],
        )

    async def is_repeating_response(
        self, chat_id: str, new_response: str, similarity_threshold: float = 0.8
    ) -> bool:
        """Check if response is too similar to recent ones.

        Uses simple word overlap similarity.
        """
        # Get recent responses
        rows = await self._fetch(
            """
            SELECT response_preview FROM bot_responses
            WHERE persona_name = $1 AND chat_id = $2
            ORDER BY responded_at DESC
            LIMIT 10
            """,
            self.persona_name,
            chat_id,
        )

        if not rows:
            return False

        new_lower = new_response.lower().strip()
        new_words = set(new_lower.split())

        for row in rows:
            old_response = row["response_preview"].lower().strip()

            # Exact match
            if new_lower == old_response:
                return True

            # Word overlap
            old_words = set(old_response.split())
            if new_words and old_words:
                overlap = len(new_words & old_words) / max(
                    len(new_words | old_words), 1
                )
                if overlap > similarity_threshold:
                    return True

        return False

    # ========================================
    # SEMANTIC SEARCH / EMBEDDINGS
    # ========================================

    async def add_embedding(
        self,
        user_id: Optional[str],
        chat_id: str,
        text: str,
        role: str,
        embedding: Optional[List[float]] = None,
    ) -> None:
        """Add a message embedding to the vector store.

        If embedding is not provided, will compute using the provider.
        """
        if not self._embedding_provider:
            logger.warning("No embedding provider configured")
            return

        # Compute embedding if not provided
        if embedding is None:
            embedding = self._embedding_provider.encode(text)

        # Convert to PostgreSQL vector format
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        await self._execute(
            """
            INSERT INTO message_embeddings
            (persona_name, user_id, chat_id, role, text, embedding)
            VALUES ($1, $2, $3, $4, $5, $6::vector(1024))
            """,
            self.persona_name,
            user_id,
            chat_id,
            role,
            text,
            embedding_str,
        )

    async def search_semantic(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        min_similarity: Optional[float] = None,
    ) -> List[dict]:
        """Search for semantically similar messages.

        Returns messages from message_embeddings ordered by similarity.

        Args:
            query: Text to search for
            user_id: User to filter by
            top_k: Maximum results
            min_similarity: Minimum similarity threshold (uses config default if None)

        Returns:
            List of dicts with: text, role, similarity, ts
        """
        if not self._embedding_provider:
            logger.warning("No embedding provider configured")
            return []

        # Compute query embedding
        query_embedding = self._embedding_provider.encode(query)
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        min_sim = min_similarity or self.config.min_similarity

        rows = await self._fetch(
            """
            SELECT * FROM match_messages(
                $1::vector(1024),
                $2,
                $3,
                $4,
                $5
            )
            """,
            embedding_str,
            self.persona_name,
            user_id,
            top_k,
            min_sim,
        )

        return [dict(row) for row in rows]

    async def get_semantic_context(
        self, query: str, user_id: str, top_k: int = 3
    ) -> List[str]:
        """Get semantic context as text strings.

        Convenience method that returns just the text content.
        """
        results = await self.search_semantic(query, user_id, top_k=top_k)
        return [r["text"] for r in results]

    async def search_semantic_group(
        self,
        query: str,
        chat_id: str,
        top_k: int = 5,
        min_similarity: Optional[float] = None,
    ) -> List[dict]:
        """Search for semantically similar messages in a group chat.

        Uses match_group_messages() SQL function which filters by chat_id
        instead of user_id, allowing retrieval of group-wide context.

        Args:
            query: Text to search for
            chat_id: Chat to filter by (group chat ID)
            top_k: Maximum results
            min_similarity: Minimum similarity threshold

        Returns:
            List of dicts with: text, role, similarity, ts
        """
        if not self._embedding_provider:
            logger.warning("No embedding provider configured")
            return []

        # Compute query embedding
        query_embedding = self._embedding_provider.encode(query)
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        min_sim = min_similarity or self.config.min_similarity

        rows = await self._fetch(
            """
            SELECT * FROM match_group_messages(
                $1::vector(1024),
                $2,
                $3,
                $4,
                $5
            )
            """,
            embedding_str,
            self.persona_name,
            chat_id,
            top_k,
            min_sim,
        )

        return [dict(row) for row in rows]

    # ========================================
    # STATS
    # ========================================

    async def get_stats(self) -> dict:
        """Get memory statistics."""
        # Count users
        row = await self._fetchrow(
            """
            SELECT COUNT(*) as cnt FROM users
            WHERE persona_name = $1
            """,
            self.persona_name,
        )
        user_count = row["cnt"] if row else 0

        # Count processed messages
        row = await self._fetchrow(
            """
            SELECT COUNT(*) as cnt FROM processed_messages
            WHERE persona_name = $1
            """,
            self.persona_name,
        )
        processed_count = row["cnt"] if row else 0

        # Count embeddings
        row = await self._fetchrow(
            """
            SELECT COUNT(*) as cnt FROM message_embeddings
            WHERE persona_name = $1
            """,
            self.persona_name,
        )
        embedding_count = row["cnt"] if row else 0

        return {
            "persona": self.persona_name,
            "users": user_count,
            "processed_messages": processed_count,
            "embeddings": embedding_count,
            "cache_stats": self._embedding_provider.get_cache_stats()
            if self._embedding_provider
            else None,
        }

    # ========================================
    # CLEANUP
    # ========================================

    async def cleanup_old_data(self, max_age_hours: int = 48) -> dict:
        """Clean up old processed messages and bot responses."""
        # Cleanup processed messages
        row = await self._fetchrow(
            "SELECT cleanup_old_processed_messages($1)", max_age_hours
        )
        processed_deleted = row[0] if row else 0

        # Cleanup bot responses
        row = await self._fetchrow("SELECT cleanup_old_bot_responses()")
        responses_deleted = row[0] if row else 0

        logger.info(
            f"Cleanup: deleted {processed_deleted} processed messages, "
            f"{responses_deleted} bot responses"
        )

        return {
            "processed_deleted": processed_deleted,
            "responses_deleted": responses_deleted,
        }


# Factory for creating persona-specific memory instances
_memory_instances: dict[str, SupabaseMemory] = {}


async def get_memory_for_persona(
    persona_name: str,
    database_url: Optional[str] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
) -> SupabaseMemory:
    """Get or create a memory instance for a persona.

    Args:
        persona_name: Name of the persona
        database_url: Database URL (uses env if not provided)
        embedding_provider: Shared embedding provider

    Returns:
        Initialized SupabaseMemory instance
    """
    if persona_name not in _memory_instances:
        config = SupabaseMemoryConfig(
            database_url=database_url or os.getenv("DATABASE_URL"),
            persona_name=persona_name,
            embedding_provider=embedding_provider,
        )
        memory = SupabaseMemory(config)
        await memory.initialize()
        _memory_instances[persona_name] = memory

    return _memory_instances[persona_name]


def reset_memory_instances():
    """Reset all cached memory instances (for testing)."""
    global _memory_instances
    _memory_instances = {}
    logger.info("Memory instances reset")
