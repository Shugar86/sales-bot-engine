"""In-memory + SQLite-dedup facade when PostgreSQL is unavailable.

Used for legacy message handling only (no LangGraph). Implements the subset of
:class:`~src.memory.memory_facade.MemoryFacade` required by
``_handle_message_legacy`` and error-path dedup markers.
"""

from __future__ import annotations

import asyncio
from typing import Any, List

from ..utils.dedup import DeduplicationStore
from ..utils.logger import get_logger

logger = get_logger("degraded_memory")


class DegradedMemoryFacade:
    """Async facade backed by per-persona :class:`DeduplicationStore` only."""

    def __init__(self, dedup: DeduplicationStore, persona_name: str = "") -> None:
        self._dedup = dedup
        self.persona_name = persona_name
        self._dm_streak: dict[str, int] = {}

    async def close(self) -> None:
        """No pooled resources."""
        self._dm_streak.clear()

    async def is_processed(self, chat_id: str, message_id: int, text: str) -> bool:
        """Delegate to SQLite dedup (thread offload for sync sqlite)."""
        return await asyncio.to_thread(
            self._dedup.is_processed, chat_id, message_id, text
        )

    async def mark_processed(self, chat_id: str, message_id: int, text: str) -> None:
        await self._dedup.mark_processed(chat_id, message_id, text)

    async def get_user_context(self, user_id: str, include_semantic: bool = True) -> str:
        _ = (user_id, include_semantic)
        return ""

    async def get_funnel_stage(self, user_id: str) -> str:
        _ = user_id
        return "unknown"

    async def get_dm_transcript_for_prompt(self, user_id: str, max_chars: int = 1500) -> str:
        _ = (user_id, max_chars)
        return ""

    async def get_recommendations(self, user_id: str, limit: int = 10) -> List[str]:
        _ = (user_id, limit)
        return []

    async def get_dm_inbound_streak(self, user_id: str) -> int:
        return int(self._dm_streak.get(user_id, 0))

    async def increment_dm_inbound_streak(self, user_id: str) -> int:
        n = int(self._dm_streak.get(user_id, 0)) + 1
        self._dm_streak[user_id] = n
        return n

    async def reset_dm_inbound_streak(self, user_id: str) -> None:
        self._dm_streak[user_id] = 0

    async def record_bot_response(self, chat_id: str, response: str) -> None:
        await self._dedup.record_bot_response(chat_id, response)

    async def is_repeating_response(
        self, chat_id: str, response: str, threshold: float = 0.8
    ) -> bool:
        return await asyncio.to_thread(
            self._dedup.is_repeating_response, chat_id, response, threshold
        )

    async def record_dm(self, *args: Any, **kwargs: Any) -> None:
        """No-op in degraded mode (legacy path does not persist DM rows)."""
        _ = (args, kwargs)
        logger.debug("record_dm skipped (degraded memory)")
