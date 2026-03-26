"""Telegram userbot (Telethon) platform adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional

if TYPE_CHECKING:
    from ...core.persona_manager import PersonaConfig
from ...models.message import IncomingMessage
from ...monitors.telegram_userbot import TelegramUserbot, UserbotMessage
from ..capabilities import PlatformCapabilities, RateLimitHint
from ..send_options import SendOptions
from ..base_mixin import DefaultAdapterMethods


class TelegramUserbotAdapter(DefaultAdapterMethods):
    """Facade over TelegramUserbot implementing PlatformAdapter."""

    def __init__(self, bot: TelegramUserbot, persona_name: str) -> None:
        self._bot = bot
        self._persona_name = persona_name

    @classmethod
    async def create(cls, config: "PersonaConfig") -> "TelegramUserbotAdapter":
        """Build userbot from persona config (start happens in run())."""
        bot = TelegramUserbot(
            session_name=config.session_name or config.name.lower(),
            api_id=config.api_id or None,
            api_hash=config.api_hash or None,
            phone=config.phone or None,
        )
        return cls(bot, config.name)

    def platform_key(self) -> str:
        return "telegram_userbot"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            supports_dm=True,
            supports_group_reply=True,
            supports_reactions=True,
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
        async def _wrapped(userbot_msg: UserbotMessage) -> None:
            msg = IncomingMessage.from_userbot_message(
                userbot_msg, persona_name=self._persona_name
            )
            await callback(msg)

        await self._bot.run(callback=_wrapped, allowed_chats=allowed_chats)

    async def send_reply(
        self,
        msg: IncomingMessage,
        text: str,
        options: SendOptions,
    ) -> bool:
        reply_to = options.reply_to_message_id
        if reply_to is None and not msg.is_dm:
            reply_to = msg.message_id
        typing_delay = not options.typing_already_simulated
        return await self._bot.send_message(
            msg.chat_id,
            text,
            reply_to=reply_to,
            typing_delay=typing_delay,
        )

    async def send_reaction(self, msg: IncomingMessage, emoji: str) -> bool:
        if not self.capabilities().supports_reactions:
            return False
        return await self._bot.send_reaction(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            emoji=emoji,
        )

    async def send_typing(self, msg: IncomingMessage) -> None:
        if not self.capabilities().supports_typing_indicator:
            return
        await self._bot.send_typing(msg.chat_id)

    async def stop(self) -> None:
        await self._bot.stop()
