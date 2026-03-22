"""
Monitor Protocol — defines the interface every platform monitor must implement.

Using typing.Protocol (structural subtyping) so existing monitors don't need
to explicitly inherit from this — they just need to match the interface.
"""

from typing import Callable, Awaitable, Protocol, runtime_checkable


@runtime_checkable
class PlatformMonitor(Protocol):
    """
    Common interface for all platform monitors.

    Concrete implementations: TelegramUserbot, TelegramMonitor, VKMonitorAsync.
    Using Protocol instead of ABC gives structural subtyping — existing code
    does not need to change (duck-typing that is statically checked).
    """

    async def send_message(self, chat_id: str, text: str, **kwargs) -> bool:
        """
        Send a text message to a chat.

        Args:
            chat_id: Platform-specific chat identifier.
            text: Message text.
            **kwargs: Platform-specific extras (reply_to, typing_delay, peer_id, …).

        Returns:
            True if sent successfully, False otherwise.
        """
        ...

    async def run(
        self,
        callback: Callable[..., Awaitable[None]],
        allowed_chats: list[str] = None,
    ) -> None:
        """
        Start the monitoring loop and call *callback* for every incoming message.

        Args:
            callback: Async function invoked with each new message.
            allowed_chats: If set, only monitor these chat IDs.
        """
        ...
