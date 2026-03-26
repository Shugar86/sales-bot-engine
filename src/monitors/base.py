"""
Legacy alias: the platform contract is ``src.platforms.protocol.PlatformAdapter``.

Monitors under ``src.monitors`` are low-level drivers; the orchestrator and graph
use adapters from ``src.platforms`` (registry + facades).
"""

from ..platforms.protocol import PlatformAdapter

# Backward-compatible name for code that still imports PlatformMonitor.
PlatformMonitor = PlatformAdapter

__all__ = ["PlatformAdapter", "PlatformMonitor"]
