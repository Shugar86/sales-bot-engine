"""Platform adapter layer — engine talks only to PlatformAdapter + registry."""

from .capabilities import PlatformCapabilities, RateLimitHint
from .protocol import PlatformAdapter
from .registry import UnknownPlatformError, create_adapter, register_platform
from .send_options import SendOptions

__all__ = [
    "PlatformAdapter",
    "PlatformCapabilities",
    "RateLimitHint",
    "SendOptions",
    "UnknownPlatformError",
    "create_adapter",
    "register_platform",
]
