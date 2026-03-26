"""Concrete platform adapters (thin facades over src/monitors)."""

from .telegram_bot import TelegramBotAdapter
from .telegram_userbot import TelegramUserbotAdapter
from .vk import VKAdapter

__all__ = [
    "TelegramBotAdapter",
    "TelegramUserbotAdapter",
    "VKAdapter",
]
