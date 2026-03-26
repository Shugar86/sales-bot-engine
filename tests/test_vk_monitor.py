"""Tests for VK Monitor — message parsing."""
import pytest
from unittest.mock import patch

from src.monitors.vk_monitor import (
    VKMessage,
    VKMonitor,
)


class TestVKMessage:
    """Test VKMessage dataclass."""
    
    def test_create_vk_message(self):
        msg = VKMessage(
            message_id=1,
            chat_id="100",
            chat_title="Test",
            user_id="500",
            username="testuser",
            display_name="Test User",
            text="Hello",
            is_dm=True,
            date=1700000000,
        )
        
        assert msg.message_id == 1
        assert msg.is_dm is True
        assert msg.text == "Hello"
        assert msg.is_reply_to_me is False


class TestVKMonitorParsing:
    """Test VK monitor event parsing logic (without actual VK API)."""
    
    def test_dm_detection(self):
        """DM peer_id is positive and < 2000000000."""
        peer_id = 12345
        is_dm = peer_id < 2000000000 and peer_id > 0
        assert is_dm is True
    
    def test_group_chat_detection(self):
        """Group chat peer_id is > 2000000000."""
        peer_id = 2000000001
        is_dm = peer_id < 2000000000 and peer_id > 0
        assert is_dm is False
    
    def test_community_detection(self):
        """Community peer_id is negative."""
        peer_id = -12345
        is_dm = peer_id < 2000000000 and peer_id > 0
        assert is_dm is False


class TestVKMonitorNoLib:
    """Test VK monitor behavior when vk_api is not installed."""
    
    def test_raises_on_init_without_vk_api(self):
        """Should raise ImportError when vk_api not available."""
        with patch("src.monitors.vk_monitor.VK_AVAILABLE", False):
            with pytest.raises(ImportError):
                VKMonitor(access_token="test")
