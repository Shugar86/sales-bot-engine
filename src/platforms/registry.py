"""Registry of platform factories keyed by (platform, account_type)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Tuple

from .adapters.telegram_bot import TelegramBotAdapter
from .adapters.telegram_userbot import TelegramUserbotAdapter
from .adapters.vk import VKAdapter
from .protocol import PlatformAdapter

PlatformFactory = Callable[[Any], Awaitable[PlatformAdapter]]

PLATFORM_REGISTRY: Dict[Tuple[str, str], PlatformFactory] = {}


def register_platform(platform: str, account_type: str, factory: PlatformFactory) -> None:
    """Register an async factory that builds a PlatformAdapter from PersonaConfig."""
    PLATFORM_REGISTRY[(platform.lower(), account_type.lower())] = factory


class UnknownPlatformError(ValueError):
    """No adapter registered for this persona platform/account_type."""


async def create_adapter(config: Any) -> PlatformAdapter:
    """
    Instantiate the adapter for a persona.

    Tries (platform, account_type); for VK falls back to (vk, userbot).
    """
    p, a = config.platform.lower(), config.account_type.lower()
    factory = PLATFORM_REGISTRY.get((p, a))
    if factory is None and p == "vk":
        factory = PLATFORM_REGISTRY.get(("vk", "userbot"))
    if factory is None:
        raise UnknownPlatformError(
            f"No platform adapter for platform={config.platform!r} "
            f"account_type={config.account_type!r}"
        )
    return await factory(config)


def _register_builtin_platforms() -> None:
    register_platform("telegram", "userbot", TelegramUserbotAdapter.create)
    register_platform("telegram", "bot", TelegramBotAdapter.create)
    register_platform("vk", "userbot", VKAdapter.create)
    register_platform("vk", "group", VKAdapter.create)
    register_platform("vk", "bot", VKAdapter.create)


_register_builtin_platforms()
