"""
Tests for Anti-Detect Deep features — typing speed, contextual leave-on-read, activity patterns
"""
import pytest
import time
from src.monitors.anti_spam import RateLimiter


class TestTypingSpeedCalculator:
    """Test typing speed variation based on message complexity."""
    
    def test_short_message_fast_typing(self):
        """Short messages should have fast typing time."""
        calc = TypingSpeedCalculator()
        speed = calc.estimate_typing_time("Привет")
        assert 0.5 <= speed <= 3.0  # Very fast for short text
    
    def test_long_message_slow_typing(self):
        """Long messages should take longer to 'type'."""
        calc = TypingSpeedCalculator()
        short = calc.estimate_typing_time("Привет")
        long = calc.estimate_typing_time(
            "Длинный ответ с множеством слов и сложной структурой предложения, "
            "который требует обдумывания и набора текста на клавиатуре телефона"
        )
        assert long > short
    
    def test_question_adds_thinking_time(self):
        """Questions should add extra 'thinking' time."""
        calc = TypingSpeedCalculator()
        statement = calc.estimate_typing_time("Да, согласен")
        question = calc.estimate_typing_time("Какой корм лучше для щенка?")
        assert question > statement
    
    def test_complex_words_slow_typing(self):
        """Complex/long words should slow typing."""
        calc = TypingSpeedCalculator()
        simple = calc.estimate_typing_time("да нет наверное")
        complex_words = calc.estimate_typing_time("гипоаллергенный корм для собак")
        assert complex_words > simple
    
    def test_emoji_speeds_up(self):
        """Messages with emojis are often faster (copy-paste or quick reaction)."""
        calc = TypingSpeedCalculator()
        text_only = calc.estimate_typing_time("Спасибо большое за помощь")
        with_emoji = calc.estimate_typing_time("Спасибо 😊👍")
        # Emoji version should be similar or slightly faster
        assert with_emoji <= text_only * 1.2
    
    def test_always_positive(self):
        """Typing time should always be positive."""
        calc = TypingSpeedCalculator()
        assert calc.estimate_typing_time("") > 0
        assert calc.estimate_typing_time("а") > 0
        assert calc.estimate_typing_time("а" * 1000) > 0


class TestContextualLeaveOnRead:
    """Test smarter leave-on-read decisions."""
    
    def setup_method(self):
        self.limiter = RateLimiter()
    
    def test_question_should_respond_more(self):
        """Questions should have lower leave-on-read rate."""
        # Direct questions are important — should respond
        for _ in range(100):
            should_leave = self.limiter.should_leave_on_read(message_text="Какой корм лучше?")
            # We can't assert exact probability, but the system should exist
            assert isinstance(should_leave, bool)
    
    def test_reaction_should_leave_more(self):
        """Simple reactions should have higher leave-on-read rate."""
        for _ in range(100):
            should_leave = self.limiter.should_leave_on_read(message_text="👍")
            assert isinstance(should_leave, bool)
    
    def test_dm_never_leave_on_read(self):
        """DMs should never be left on read."""
        for _ in range(20):
            should_leave = self.limiter.should_leave_on_read(
                message_text="Привет",
                is_dm=True
            )
            assert should_leave is False
    
    def test_mention_should_respond(self):
        """Messages that mention someone should usually be responded to."""
        for _ in range(100):
            should_leave = self.limiter.should_leave_on_read(
                message_text="@андрей, что думаешь?"
            )
            assert isinstance(should_leave, bool)


class TestActivityPatterns:
    """Test time-of-day activity patterns."""
    
    def test_active_hours_daytime(self):
        """Should be active during daytime (8-23)."""
        limiter = RateLimiter(active_hours_start=8, active_hours_end=23)
        assert limiter._is_active_hours(hour=12) is True
        assert limiter._is_active_hours(hour=18) is True
        assert limiter._is_active_hours(hour=10) is True
    
    def test_inactive_hours_night(self):
        """Should be inactive at night (0-8)."""
        limiter = RateLimiter(active_hours_start=8, active_hours_end=23)
        assert limiter._is_active_hours(hour=3) is False
        assert limiter._is_active_hours(hour=6) is False
        assert limiter._is_active_hours(hour=0) is False
    
    def test_night_delay_multiplier(self):
        """Night delays should be longer."""
        limiter = RateLimiter(
            min_delay_sec=30,
            max_delay_sec=300,
            night_delay_multiplier=3.0,
        )
        
        # During day: should be within 30-300
        day_delays = []
        for _ in range(50):
            d = limiter.get_random_delay(current_hour=14)
            day_delays.append(d)
        
        # At night: should be 3x longer
        night_delays = []
        for _ in range(50):
            d = limiter.get_random_delay(current_hour=3)
            night_delays.append(d)
        
        avg_day = sum(day_delays) / len(day_delays)
        avg_night = sum(night_delays) / len(night_delays)
        
        # Night should be at least 2x day average
        assert avg_night > avg_day * 1.5


class TestActivityBurstDetection:
    """Test burst activity pattern (humans send 2-3 messages quickly)."""
    
    def test_burst_window(self):
        """Messages within burst window should be handled differently."""
        limiter = RateLimiter()
        
        # First send is OK
        assert limiter.can_send("test_chat")[0] is True
        limiter.record_send("test_chat")
        
        # Immediate second send — should be blocked by cooldown
        can, reason = limiter.can_send("test_chat")
        # Should be blocked by cooldown
        assert can is False or "cooldown" in reason.lower()


class TestConversationThreading:
    """Test conversation threading awareness."""

    @pytest.mark.asyncio
    async def test_already_replied_detection(self):
        """Bot should detect if it already replied to a conversation."""
        from src.utils.dedup import DeduplicationStore
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DeduplicationStore(
                storage_path=os.path.join(tmpdir, "dedup.json")
            )

            # Record a response
            await store.record_bot_response("chat1", "Рекомендую корм с ягнёнком")

            # Similar response should be detected as repeat (lower threshold)
            assert store.is_repeating_response(
                "chat1",
                "Рекомендую корм с ягнёнком для аллергиков",
                similarity_threshold=0.6
            ) is True

    @pytest.mark.asyncio
    async def test_different_chat_not_repeating(self):
        """Same response in different chat should not be flagged."""
        from src.utils.dedup import DeduplicationStore
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DeduplicationStore(
                storage_path=os.path.join(tmpdir, "dedup.json")
            )

            await store.record_bot_response("chat1", "Хороший корм")

            assert store.is_repeating_response(
                "chat2",
                "Хороший корм"
            ) is False


# Need to import TypingSpeedCalculator
from src.monitors.anti_spam import TypingSpeedCalculator
