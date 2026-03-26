"""Tests for TelegramUserbot — message parsing, mock Telethon."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

from src.monitors.telegram_userbot import (
    UserbotMessage,
    TelegramUserbot,
)


# Create mock entity classes with correct __name__ for type() checks
def _make_entity_class(class_name, fields):
    def __init__(self, **kwargs):
        for f, default in fields.items():
            setattr(self, f, kwargs.get(f, default))
    
    return type(class_name, (), {"__init__": __init__})


MockUser = _make_entity_class("User", {
    "id": 123, "first_name": "Test", "last_name": "User",
    "username": "testuser", "bot": False,
})

MockChannel = _make_entity_class("Channel", {
    "id": 100123, "title": "Test Channel",
})

MockChat = _make_entity_class("Chat", {
    "id": 456, "title": "Test Group",
})


class MockReplyTo:
    """Mock reply_to."""
    def __init__(self, reply_to_msg_id=None):
        self.reply_to_msg_id = reply_to_msg_id


class MockMessage:
    """Mock Telethon Message."""
    def __init__(
        self,
        id=1,
        text="Hello",
        sender=None,
        chat=None,
        date=None,
        reply_to=None,
    ):
        self.id = id
        self.text = text
        self.sender = sender
        self.chat = chat
        self.date = date or datetime(2024, 1, 1, 12, 0, 0)
        self.reply_to = reply_to


class MockEvent:
    """Mock Telethon NewMessage event."""
    def __init__(self, message):
        self.message = message


@pytest.fixture
def mock_userbot():
    """Create a TelegramUserbot with mocked Telethon."""
    with patch("src.monitors.telegram_userbot.TELETHON_AVAILABLE", True):
        with patch("src.monitors.telegram_userbot.TelegramClient", create=True) as MockClient:
            client_instance = MagicMock()
            MockClient.return_value = client_instance
            
            bot = TelegramUserbot(
                session_name="test",
                api_id=12345,
                api_hash="testhash",
                phone="+79001234567",
            )
            bot.client = client_instance
            bot._my_id = 999
            
            yield bot


class TestParseMessage:
    """Test message parsing from Telethon events."""
    
    def test_parse_channel_message(self, mock_userbot):
        """Parse message from a channel/group."""
        msg = MockMessage(
            id=42,
            text="Какой корм для овчарки?",
            sender=MockUser(id=100, first_name="Иван", last_name="", username="ivan"),
            chat=MockChannel(id=100123, title="Кинологи"),
        )
        event = MockEvent(msg)
        
        result = mock_userbot._parse_message(event)
        
        assert result is not None
        assert result.message_id == 42
        assert result.chat_id == "100123"
        assert result.chat_title == "Кинологи"
        assert result.user_id == "100"
        assert result.username == "ivan"
        assert result.display_name == "Иван"
        assert result.text == "Какой корм для овчарки?"
        assert result.is_dm is False
    
    def test_parse_dm_message(self, mock_userbot):
        """Parse DM message."""
        user = MockUser(id=200, first_name="Петр", last_name="Сидоров", username="petr")
        msg = MockMessage(
            id=10,
            text="Привет, расскажи про корм",
            sender=user,
            chat=user,  # DM — chat is a User
        )
        event = MockEvent(msg)
        
        result = mock_userbot._parse_message(event)
        
        assert result is not None
        assert result.is_dm is True
        assert result.chat_title == "DM:Петр"
        assert result.display_name == "Петр Сидоров"
    
    def test_parse_basic_group(self, mock_userbot):
        """Parse message from basic group (Chat, not Channel)."""
        msg = MockMessage(
            id=5,
            text="У меня щенок",
            sender=MockUser(id=300, first_name="Оля"),
            chat=MockChat(id=789, title="Щенячий клуб"),
        )
        event = MockEvent(msg)
        
        result = mock_userbot._parse_message(event)
        
        assert result is not None
        assert result.chat_id == "789"
        assert result.chat_title == "Щенячий клуб"
    
    def test_parse_empty_text_returns_none(self, mock_userbot):
        """Empty text messages should return None."""
        msg = MockMessage(text=None)
        event = MockEvent(msg)
        
        result = mock_userbot._parse_message(event)
        assert result is None
    
    def test_parse_no_sender(self, mock_userbot):
        """Message without sender (e.g., channel post)."""
        msg = MockMessage(
            id=1,
            text="Channel post",
            sender=None,
            chat=MockChannel(id=100, title="Channel"),
        )
        event = MockEvent(msg)
        
        result = mock_userbot._parse_message(event)
        
        assert result is not None
        assert result.user_id == "unknown"
        assert result.display_name == "Unknown"
    
    def test_parse_reply_to(self, mock_userbot):
        """Parse message with reply_to."""
        msg = MockMessage(
            id=20,
            text="Ответ",
            sender=MockUser(id=100, first_name="Test"),
            chat=MockChannel(id=100, title="Chat"),
            reply_to=MockReplyTo(reply_to_msg_id=15),
        )
        event = MockEvent(msg)
        
        result = mock_userbot._parse_message(event)
        
        assert result is not None
        assert result.reply_to_message_id == 15


class TestSendMessage:
    """Test send_message functionality."""
    
    @pytest.mark.asyncio
    async def test_send_message_success(self, mock_userbot):
        """Successful send."""
        mock_entity = MagicMock()
        mock_userbot.client.get_entity = AsyncMock(return_value=mock_entity)
        mock_userbot.client.send_message = AsyncMock()
        
        # Mock action context manager
        mock_action = MagicMock()
        mock_action.__aenter__ = AsyncMock(return_value=None)
        mock_action.__aexit__ = AsyncMock(return_value=None)
        mock_userbot.client.action.return_value = mock_action
        
        result = await mock_userbot.send_message(
            chat_id="-100123",
            text="Test response",
            reply_to=42,
            typing_delay=False,
        )
        
        assert result is True
        mock_userbot.client.send_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_send_message_error_returns_false(self, mock_userbot):
        """Send error should return False."""
        mock_userbot.client.get_entity = AsyncMock(side_effect=Exception("Not found"))
        
        result = await mock_userbot.send_message(
            chat_id="invalid",
            text="Test",
            typing_delay=False,
        )
        
        assert result is False


class TestHandleNewMessage:
    """Test _handle_new_message callback flow."""
    
    @pytest.mark.asyncio
    async def test_skips_own_messages(self, mock_userbot):
        """Own messages should be skipped."""
        mock_userbot._callback = AsyncMock()
        
        msg = MockMessage(
            sender=MockUser(id=999),  # Same as _my_id
            chat=MockChannel(id=100, title="Chat"),
            text="My own message",
        )
        event = MockEvent(msg)
        
        await mock_userbot._handle_new_message(event)
        
        mock_userbot._callback.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_filters_by_allowed_chats(self, mock_userbot):
        """Messages from non-allowed chats should be skipped."""
        mock_userbot._callback = AsyncMock()
        mock_userbot._allowed_chats = ["-100123"]  # Only this chat
        
        msg = MockMessage(
            sender=MockUser(id=100),
            chat=MockChannel(id=-99999, title="Other Chat"),  # Not in allowed
            text="Hello",
        )
        event = MockEvent(msg)
        
        await mock_userbot._handle_new_message(event)
        
        mock_userbot._callback.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_dm_always_passes_filter(self, mock_userbot):
        """DMs should always pass the chat filter."""
        mock_userbot._callback = AsyncMock()
        mock_userbot._allowed_chats = ["-100123"]  # DM not in list
        
        user = MockUser(id=100, first_name="Test")
        msg = MockMessage(
            sender=user,
            chat=user,  # DM
            text="Private message",
        )
        event = MockEvent(msg)
        
        await mock_userbot._handle_new_message(event)
        
        mock_userbot._callback.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_callback_called_with_parsed_message(self, mock_userbot):
        """Callback should receive parsed UserbotMessage."""
        received = []
        
        async def capture(msg):
            received.append(msg)
        
        mock_userbot._callback = capture
        mock_userbot._allowed_chats = []
        
        msg = MockMessage(
            id=42,
            text="Test message",
            sender=MockUser(id=100, first_name="Alice"),
            chat=MockChannel(id=500, title="Chat"),
        )
        event = MockEvent(msg)
        
        await mock_userbot._handle_new_message(event)
        
        assert len(received) == 1
        assert isinstance(received[0], UserbotMessage)
        assert received[0].text == "Test message"
        assert received[0].user_id == "100"
