"""Normalized outbound send options (no platform-specific **kwargs in the engine)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SendOptions:
    """Options for send_reply — built by graph nodes from message + runtime state."""

    reply_to_message_id: Optional[int] = None
    typing_already_simulated: bool = False
    thread_id: Optional[str] = None
