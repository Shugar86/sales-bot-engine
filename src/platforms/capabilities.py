"""Platform capability flags and optional rate-limit hints for adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RateLimitHint:
    """Soft hint for RateLimiter; does not replace HTTP/API 429 handling."""

    min_interval_sec: float = 0.0
    burst: int = 0


@dataclass(frozen=True)
class PlatformCapabilities:
    """What this platform adapter can do — used to avoid dead-end graph branches."""

    supports_dm: bool = True
    supports_group_reply: bool = True
    supports_reactions: bool = False
    supports_edit: bool = False
    supports_fetch_thread_context: bool = False
    supports_typing_indicator: bool = False
    rate_limit_hint: Optional[RateLimitHint] = None
