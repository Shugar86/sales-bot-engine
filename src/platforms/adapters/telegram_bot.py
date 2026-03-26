"""Telegram Bot API (long polling) platform adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional

if TYPE_CHECKING:
    from ...core.persona_manager import PersonaConfig
from ...models.message import IncomingMessage
from ...monitors.telegram_monitor import TelegramMonitor, TelegramMessage
from ..base_mixin import DefaultAdapterMethods
from ..capabilities import PlatformCapabilities, RateLimitHint
from ..send_options import SendOptions


class TelegramBotAdapter(DefaultAdapterMethods):
    """Facade over TelegramMonitor; run() delegates to poll_loop."""

    def __init__(self, monitor: TelegramMonitor, persona_name: str) -> None:
        self._monitor = monitor
        self._persona_name = persona_name

    @classmethod
    async def create(cls, config: "PersonaConfig") -> "TelegramBotAdapter":
        monitor = TelegramMonitor(bot_token=config.bot_token)
        return cls(monitor, config.name)

    def platform_key(self) -> str:
        return "telegram_bot"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            supports_dm=True,
            supports_group_reply=True,
            supports_reactions=False,
            supports_edit=False,
            supports_fetch_thread_context=False,
            supports_typing_indicator=True,
            rate_limit_hint=RateLimitHint(min_interval_sec=0.0, burst=0),
        )

    async def run(
        self,
        callback: Callable[..., Awaitable[None]],
        allowed_chats: Optional[List[str]] = None,
    ) -> None:
        async def _wrapped(tg_msg: TelegramMessage) -> None:
            msg = IncomingMessage.from_telegram_message(
                tg_msg, persona_name=self._persona_name
            )
            await callback(msg)

        await self._monitor.poll_loop(callback=_wrapped, allowed_chats=allowed_chats)

    async def send_reply(
        self,
        msg: IncomingMessage,
        text: str,
        options: SendOptions,
    ) -> bool:
        reply_to = options.reply_to_message_id
        if reply_to is None and not msg.is_dm:
            reply_to = msg.message_id
        return await self._monitor.send_message(
            msg.chat_id,
            text,
            reply_to=reply_to,
        )

    async def send_reaction(self, msg: IncomingMessage, emoji: str) -> bool:
        return False

    async def send_typing(self, msg: IncomingMessage) -> None:
        await self._monitor.send_typing(msg.chat_id)

    async def stop(self) -> None:
        await self._monitor.stop()
