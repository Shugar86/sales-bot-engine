"""Tests for IncomingMessage — unified message type."""
from dataclasses import asdict

from src.models.message import IncomingMessage, Platform


class TestIncomingMessage:
    """Test IncomingMessage dataclass."""
    
    def test_create_basic(self):
        msg = IncomingMessage(
            message_id=1,
            chat_id="100",
            chat_title="Test",
            user_id="500",
            username="user",
            display_name="User",
            text="Hello",
            is_dm=False,
            date=1700000000,
        )
        
        assert msg.message_id == 1
        assert msg.platform == Platform.TELEGRAM_BOT  # default
        assert msg.persona_name == ""
    
    def test_from_telegram_message(self):
        """Test factory from TelegramMessage (Bot API)."""
        # Mock TelegramMessage
        class MockTGMsg:
            message_id = 42
            chat_id = "-100123"
            chat_title = "Кинологи"
            user_id = "777"
            username = "doglover"
            display_name = "Dog Lover"
            text = "Корм для овчарки"
            is_dm = False
            date = 1700000000
            reply_to_message_id = None
        
        msg = IncomingMessage.from_telegram_message(
            MockTGMsg(), persona_name="Кормовед"
        )
        
        assert msg.platform == Platform.TELEGRAM_BOT
        assert msg.persona_name == "Кормовед"
        assert msg.text == "Корм для овчарки"
        assert msg.chat_id == "-100123"
    
    def test_from_userbot_message(self):
        """Test factory from UserbotMessage (Telethon)."""
        class MockUBMsg:
            message_id = 10
            chat_id = "200"
            chat_title = "Фитнес"
            user_id = "888"
            username = "gymbro"
            display_name = "Gym Bro"
            text = "Как накачаться?"
            is_dm = False
            date = 1700000100
            reply_to_message_id = 5
            is_reply_to_me = True
            raw = "raw_object"
        
        msg = IncomingMessage.from_userbot_message(
            MockUBMsg(), persona_name="FitBro"
        )
        
        assert msg.platform == Platform.TELEGRAM_USERBOT
        assert msg.persona_name == "FitBro"
        assert msg.is_reply_to_me is True
        assert msg.raw == "raw_object"
    
    def test_from_vk_message(self):
        """Test factory from VKMessage."""
        class MockVKMsg:
            message_id = 20
            chat_id = "300"
            chat_title = "VK Group"
            user_id = "999"
            username = "vkuser"
            display_name = "VK User"
            text = "Привет из VK"
            is_dm = True
            date = 1700000200
            is_reply_to_me = False
        
        msg = IncomingMessage.from_vk_message(
            MockVKMsg(), persona_name="SMM Bot"
        )
        
        assert msg.platform == Platform.VK
        assert msg.persona_name == "SMM Bot"
        assert msg.is_dm is True
    
    def test_fields_present(self):
        """All expected fields should be present."""
        msg = IncomingMessage(
            message_id=1,
            chat_id="100",
            chat_title="T",
            user_id="1",
            username="u",
            display_name="U",
            text="t",
            is_dm=False,
            date=0,
        )
        
        d = asdict(msg)
        assert "message_id" in d
        assert "chat_id" in d
        assert "text" in d
        assert "platform" in d
        assert "persona_name" in d
        assert "reply_to_message_id" in d
        assert "is_reply_to_me" in d
        assert "raw" in d


class TestPlatform:
    """Test Platform enum."""
    
    def test_values(self):
        assert Platform.TELEGRAM_BOT.value == "telegram_bot"
        assert Platform.TELEGRAM_USERBOT.value == "telegram_userbot"
        assert Platform.VK.value == "vk"
