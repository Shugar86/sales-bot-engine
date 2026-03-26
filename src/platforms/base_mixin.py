"""Shared default implementations for optional PlatformAdapter methods."""

from __future__ import annotations

from typing import List

from ..models.message import IncomingMessage


class DefaultAdapterMethods:
    """Mixin-style defaults; adapters inherit and override as needed."""

    async def fetch_thread_context(
        self,
        msg: IncomingMessage,
        *,
        limit: int = 20,
    ) -> List[str]:
        return []

    async def edit_message(self, msg: IncomingMessage, new_text: str) -> bool:
        return False
