"""Tests for Context Reader — v3 core module."""
import time
from src.core.context_reader import ContextReader, ChatContext, Message, VibeType


class TestMessage:
    """Tests for Message dataclass."""
    
    def test_message_creation(self):
        msg = Message(
            message_id=1,
            user_id="123",
            username="testuser",
            display_name="Test",
            text="Hello",
            timestamp=1700000000,
        )
        assert msg.message_id == 1
        assert msg.text == "Hello"
        assert msg.is_from_me is False
    
    def test_message_from_me(self):
        msg = Message(
            message_id=1, user_id="123", username="me", display_name="Me",
            text="My message", timestamp=1700000000, is_from_me=True,
        )
        assert msg.is_from_me is True
    
    def test_message_reply_to_me(self):
        msg = Message(
            message_id=2, user_id="456", username="other", display_name="Other",
            text="Reply", timestamp=1700000001, is_reply_to_me=True,
        )
        assert msg.is_reply_to_me is True


class TestChatContext:
    """Tests for ChatContext dataclass."""
    
    def test_empty_context(self):
        ctx = ChatContext()
        assert len(ctx.messages) == 0
        assert ctx.last_message is None
        assert ctx.is_active_chat is False
    
    def test_last_message(self):
        ctx = ChatContext(messages=[
            Message(1, "1", "a", "A", "first", 100),
            Message(2, "2", "b", "B", "last", 200),
        ])
        assert ctx.last_message.text == "last"
    
    def test_get_messages_from_user(self):
        ctx = ChatContext(messages=[
            Message(1, "123", "a", "A", "msg1", 100),
            Message(2, "456", "b", "B", "msg2", 200),
            Message(3, "123", "a", "A", "msg3", 300),
        ])
        msgs = ctx.get_messages_from_user("123")
        assert len(msgs) == 2
    
    def test_get_recent_messages(self):
        ctx = ChatContext(messages=[
            Message(i, "1", "a", "A", f"msg{i}", i) for i in range(10)
        ])
        recent = ctx.get_recent_messages(3)
        assert len(recent) == 3
        assert recent[-1].text == "msg9"
    
    def test_is_active_chat(self):
        ctx = ChatContext(messages=[Message(1, "1", "a", "A", "x", 100)])
        assert ctx.is_active_chat is True
    
    def test_seconds_since_my_last_message_no_message(self):
        ctx = ChatContext(my_last_message_time=0)
        assert ctx.seconds_since_my_last_message > 99999
    
    def test_seconds_since_my_last_message_with_time(self):
        now = int(time.time())
        ctx = ChatContext(my_last_message_time=now - 60)
        assert 55 <= ctx.seconds_since_my_last_message <= 65


class TestContextReader:
    """Tests for ContextReader."""
    
    def setup_method(self):
        self.reader = ContextReader(my_user_id="bot123")
    
    def test_empty_messages(self):
        ctx = self.reader.read_context([])
        assert len(ctx.messages) == 0
        assert ctx.vibe == "casual"
        assert ctx.topic == ""
    
    def test_basic_context(self):
        messages = [
            {"message_id": 1, "user_id": "user1", "username": "alice",
             "display_name": "Alice", "text": "Привет всем!", "timestamp": 100},
            {"message_id": 2, "user_id": "user2", "username": "bob",
             "display_name": "Bob", "text": "Привет!", "timestamp": 200},
        ]
        ctx = self.reader.read_context(messages)
        assert len(ctx.messages) == 2
        assert ctx.unique_participants == 2
        assert "alice" in ctx.participants
        assert "bob" in ctx.participants
    
    def test_topic_detection_dogs(self):
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "Собака не ест корм уже 3 дня", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.topic == "dogs_food"
    
    def test_topic_detection_fitness(self):
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "Как накачать мышцы в зале?", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.topic == "fitness"
    
    def test_vibe_detection_funny(self):
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "😂😂😂 хахаха прикол", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.vibe == VibeType.FUNNY.value
    
    def test_vibe_detection_sad(self):
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "Мне грустно и печально, очень плохо", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.vibe == VibeType.SAD.value
    
    def test_vibe_detection_aggressive(self):
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "Заткнись уже, идиот!", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.vibe == VibeType.AGGRESSIVE.value
    
    def test_directed_at_me_by_reply(self):
        messages = [
            {"message_id": 1, "user_id": "bot123", "username": "bot", "display_name": "Bot",
             "text": "Я бот", "timestamp": 100},
            {"message_id": 2, "user_id": "user1", "username": "a", "display_name": "A",
             "text": "А ты что думаешь?", "timestamp": 200,
             "reply_to_message_id": 1},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.is_directed_at_me is True
    
    def test_directed_at_me_flag(self):
        messages = [
            {"message_id": 2, "user_id": "user1", "username": "a", "display_name": "A",
             "text": "Что думаешь?", "timestamp": 200, "is_reply_to_me": True},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.is_directed_at_me is True
    
    def test_not_directed_at_me(self):
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "Привет!", "timestamp": 100},
            {"message_id": 2, "user_id": "u2", "username": "b", "display_name": "B",
             "text": "Привет!", "timestamp": 200},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.is_directed_at_me is False
    
    def test_my_last_message_not_directed(self):
        """Моё собственное сообщение не направлено на меня."""
        messages = [
            {"message_id": 1, "user_id": "bot123", "username": "bot", "display_name": "Bot",
             "text": "Я написал", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.is_directed_at_me is False
    
    def test_deduplication_by_message_id(self):
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "msg1", "timestamp": 100},
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "msg1 duplicate", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages)
        assert len(ctx.messages) == 1
    
    def test_messages_sorted_by_time(self):
        messages = [
            {"message_id": 2, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "second", "timestamp": 200},
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "first", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages)
        assert ctx.messages[0].text == "first"
        assert ctx.messages[1].text == "second"
    
    def test_summarize_empty_context(self):
        ctx = ChatContext()
        summary = self.reader.summarize_context(ctx)
        assert "контекста нет" in summary
    
    def test_summarize_with_context(self):
        ctx = ChatContext(
            messages=[
                Message(1, "1", "alice", "Alice", "Привет", 100),
                Message(2, "2", "bob", "Bob", "Как дела?", 200),
            ],
            participants=["alice", "bob"],
            topic="general",
            vibe="casual",
        )
        summary = self.reader.summarize_context(ctx)
        assert "alice" in summary
        assert "bob" in summary
        assert "casual" in summary
    
    def test_summarize_directed(self):
        ctx = ChatContext(
            messages=[Message(1, "1", "u", "U", "Вопрос", 100)],
            is_directed_at_me=True,
        )
        summary = self.reader.summarize_context(ctx)
        assert "направлено" in summary
    
    def test_read_context_with_my_last_message_time(self):
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "msg", "timestamp": 100},
        ]
        ctx = self.reader.read_context(messages, my_last_message_time=99)
        assert ctx.my_last_message_time == 99
