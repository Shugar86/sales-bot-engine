"""VK (Long Poll) platform adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional

if TYPE_CHECKING:
    from ...core.persona_manager import PersonaConfig
from ...models.message import IncomingMessage
from ...monitors.vk_monitor import VKMessage, VKMonitorAsync
from ..base_mixin import DefaultAdapterMethods
from ..capabilities import PlatformCapabilities, RateLimitHint
from ..send_options import SendOptions


class VKAdapter(DefaultAdapterMethods):
    """Facade over VKMonitorAsync; maps chat_id to peer_id for sends."""

    def __init__(self, monitor: VKMonitorAsync, persona_name: str) -> None:
        self._monitor = monitor
        self._persona_name = persona_name

    @classmethod
    async def create(cls, config: "PersonaConfig") -> "VKAdapter":
        monitor = VKMonitorAsync(access_token=config.vk_token)
        await monitor.start()
        return cls(monitor, config.name)

    def platform_key(self) -> str:
        return "vk"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            supports_dm=True,
            supports_group_reply=True,
            supports_reactions=False,
            supports_edit=False,
            supports_fetch_thread_context=False,
            supports_typing_indicator=False,
            rate_limit_hint=RateLimitHint(min_interval_sec=0.0, burst=0),
        )

    async def run(
        self,
        callback: Callable[..., Awaitable[None]],
        allowed_chats: Optional[List[str]] = None,
    ) -> None:
        async def _wrapped(vk_msg: VKMessage) -> None:
            msg = IncomingMessage.from_vk_message(
                vk_msg, persona_name=self._persona_name
            )
            await callback(msg)

        await self._monitor.run(callback=_wrapped, allowed_chats=allowed_chats)

    async def send_reply(
        self,
        msg: IncomingMessage,
        text: str,
        options: SendOptions,
    ) -> bool:
        peer_id = msg.chat_id
        reply_to = options.reply_to_message_id
        if reply_to is None and not msg.is_dm:
            reply_to = msg.message_id
        return await self._monitor.send_message(
            peer_id,
            text,
            reply_to=reply_to,
        )

    async def send_reaction(self, msg: IncomingMessage, emoji: str) -> bool:
        return False

    async def send_typing(self, msg: IncomingMessage) -> None:
        return None

    async def stop(self) -> None:
        return None
