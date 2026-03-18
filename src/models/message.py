"""
Unified message type — platform-agnostic abstraction for incoming messages.
All monitors produce this; orchestrator, router, generator consume it.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Platform(Enum):
    """Supported platforms."""
    TELEGRAM_BOT = "telegram_bot"
    TELEGRAM_USERBOT = "telegram_userbot"
    VK = "vk"


@dataclass
class IncomingMessage:
    """
    Platform-agnostic incoming message.
    
    All monitors (Telegram bot, Telegram userbot, VK) produce this.
    The orchestrator, router, and generator work with this type only.
    """
    message_id: int
    chat_id: str
    chat_title: str
    user_id: str
    username: str
    display_name: str
    text: str
    is_dm: bool
    date: int
    platform: Platform = Platform.TELEGRAM_BOT
    reply_to_message_id: Optional[int] = None
    is_reply_to_me: bool = False
    persona_name: str = ""  # which persona this message belongs to
    raw: object = field(default=None, repr=False)  # original platform message
    
    @classmethod
    def from_telegram_message(cls, msg, persona_name: str = "") -> "IncomingMessage":
        """Create from TelegramMessage (Bot API)."""
        return cls(
            message_id=msg.message_id,
            chat_id=msg.chat_id,
            chat_title=msg.chat_title,
            user_id=msg.user_id,
            username=msg.username,
            display_name=msg.display_name,
            text=msg.text,
            is_dm=msg.is_dm,
            date=msg.date,
            platform=Platform.TELEGRAM_BOT,
            reply_to_message_id=msg.reply_to_message_id,
            persona_name=persona_name,
            raw=msg,
        )
    
    @classmethod
    def from_userbot_message(cls, msg, persona_name: str = "") -> "IncomingMessage":
        """Create from UserbotMessage (Telethon)."""
        return cls(
            message_id=msg.message_id,
            chat_id=msg.chat_id,
            chat_title=msg.chat_title,
            user_id=msg.user_id,
            username=msg.username,
            display_name=msg.display_name,
            text=msg.text,
            is_dm=msg.is_dm,
            date=msg.date,
            platform=Platform.TELEGRAM_USERBOT,
            reply_to_message_id=msg.reply_to_message_id,
            is_reply_to_me=msg.is_reply_to_me,
            persona_name=persona_name,
            raw=msg.raw,
        )
    
    @classmethod
    def from_vk_message(cls, msg, persona_name: str = "") -> "IncomingMessage":
        """Create from VKMessage."""
        return cls(
            message_id=msg.message_id,
            chat_id=msg.chat_id,
            chat_title=msg.chat_title,
            user_id=msg.user_id,
            username=msg.username,
            display_name=msg.display_name,
            text=msg.text,
            is_dm=msg.is_dm,
            date=msg.date,
            platform=Platform.VK,
            is_reply_to_me=msg.is_reply_to_me,
            persona_name=persona_name,
        )
