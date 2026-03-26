"""PlatformAdapter protocol — single contract for inbound loop and outbound actions."""

from __future__ import annotations

from typing import Awaitable, Callable, List, Protocol, runtime_checkable

from ..models.message import IncomingMessage
from .capabilities import PlatformCapabilities
from .send_options import SendOptions


@runtime_checkable
class PlatformAdapter(Protocol):
    """Structural contract for all platform implementations."""

    def platform_key(self) -> str:
        """Stable id for logs/metrics (e.g. telegram_userbot, telegram_bot, vk)."""
        ...

    def capabilities(self) -> PlatformCapabilities:
        """Feature flags for antispam, emoji path, future thread context."""
        ...

    async def run(
        self,
        callback: Callable[..., Awaitable[None]],
        allowed_chats: List[str] | None = None,
    ) -> None:
        """Inbound loop; invokes callback for each normalized message."""
        ...

    async def send_reply(
        self,
        msg: IncomingMessage,
        text: str,
        options: SendOptions,
    ) -> bool:
        """Send a text reply in the same conversation as msg."""
        ...

    async def send_reaction(
        self,
        msg: IncomingMessage,
        emoji: str,
    ) -> bool:
        """React to msg if supported; otherwise return False."""
        ...

    async def send_typing(self, msg: IncomingMessage) -> None:
        """Typing indicator if supported; no-op otherwise."""
        ...

    async def fetch_thread_context(
        self,
        msg: IncomingMessage,
        *,
        limit: int = 20,
    ) -> List[str]:
        """Thread/post context for forum-style platforms; default empty."""
        ...

    async def edit_message(
        self,
        msg: IncomingMessage,
        new_text: str,
    ) -> bool:
        """Edit bot message if supported."""
        ...

    async def stop(self) -> None:
        """Release connections / stop background tasks."""
        ...
