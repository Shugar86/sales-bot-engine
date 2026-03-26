"""Tests for new anti-detect features: leave-on-read, emoji reactions, time-awareness."""
import random
from unittest.mock import patch

from src.monitors.anti_spam import RateLimiter


class TestLeaveOnRead:
    """Test leave-on-read behavior."""
    
    def test_leave_on_read_probability(self):
        """should_leave_on_read respects probability setting."""
        limiter = RateLimiter(leave_on_read_probability=1.0)
        assert limiter.should_leave_on_read() is True
        
        limiter2 = RateLimiter(leave_on_read_probability=0.0)
        assert limiter2.should_leave_on_read() is False
    
    def test_leave_on_read_default_probability(self):
        """Default probability is 0.35 (35% of messages)."""
        limiter = RateLimiter()
        assert limiter.leave_on_read_probability == 0.35
    
    def test_leave_on_read_statistical(self):
        """Over many trials, should be ~35% with default."""
        random.seed(42)
        limiter = RateLimiter(leave_on_read_probability=0.35)
        
        results = [limiter.should_leave_on_read() for _ in range(1000)]
        true_count = sum(results)
        
        # Should be roughly 35% ± 10%
        assert 250 <= true_count <= 450


class TestEmojiReaction:
    """Test emoji reaction selection."""
    
    def test_should_use_emoji_probability(self):
        """should_use_emoji_reaction respects probability."""
        limiter = RateLimiter(emoji_reaction_probability=1.0)
        assert limiter.should_use_emoji_reaction() is True
        
        limiter2 = RateLimiter(emoji_reaction_probability=0.0)
        assert limiter2.should_use_emoji_reaction() is False
    
    def test_emoji_for_thanks(self):
        """Thank-you messages get appropriate emoji."""
        limiter = RateLimiter()
        emoji = limiter.get_emoji_reaction("Спасибо за совет!")
        assert emoji in ["❤️", "👍", "😊"]
    
    def test_emoji_for_agreement(self):
        """Agreement messages get appropriate emoji."""
        limiter = RateLimiter()
        emoji = limiter.get_emoji_reaction("Согласен, точно так же думаю")
        assert emoji in ["👍", "💪", "🤝"]
    
    def test_emoji_for_funny(self):
        """Funny messages get appropriate emoji."""
        limiter = RateLimiter()
        emoji = limiter.get_emoji_reaction("Ахахах это смешно 😂")
        assert emoji in ["😂", "🤣", "😄"]
    
    def test_emoji_for_generic(self):
        """Generic messages still get an emoji."""
        limiter = RateLimiter()
        emoji = limiter.get_emoji_reaction("какая погода сегодня")
        assert emoji in ["👍", "❤️", "🔥", "💪"]


class TestTimeAwareness:
    """Test time-of-day aware behavior."""
    
    def test_active_hours_detection(self):
        """Check active hours detection."""
        limiter = RateLimiter(active_hours_start=8, active_hours_end=23)
        
        assert limiter._is_active_hours(hour=10) is True
        assert limiter._is_active_hours(hour=22) is True
        assert limiter._is_active_hours(hour=3) is False
        assert limiter._is_active_hours(hour=7) is False
    
    def test_night_delay_multiplier(self):
        """At night, delays should be multiplied."""
        random.seed(42)
        limiter = RateLimiter(
            min_delay_sec=30.0,
            max_delay_sec=60.0,
            night_delay_multiplier=3.0,
        )
        
        # During active hours — should be in 30-60 range
        with patch.object(limiter, '_is_active_hours', return_value=True):
            delays_day = [limiter.get_random_delay() for _ in range(20)]
        
        # At night — should be 3x higher
        with patch.object(limiter, '_is_active_hours', return_value=False):
            delays_night = [limiter.get_random_delay() for _ in range(20)]
        
        avg_day = sum(delays_day) / len(delays_day)
        avg_night = sum(delays_night) / len(delays_night)
        
        # Night delays should be significantly higher
        assert avg_night > avg_day
    
    def test_stats_include_active_hours(self):
        """Stats should include active hours status."""
        limiter = RateLimiter()
        stats = limiter.get_stats()
        
        assert "active_hours" in stats
        assert "leave_on_read_pct" in stats
        assert "emoji_reaction_pct" in stats


class TestDelayRange:
    """Test delay ranges are human-realistic."""
    
    def test_default_delays_are_human_like(self):
        """Default delays should be 30-300s (not 180-900s)."""
        limiter = RateLimiter()
        assert limiter.min_delay_sec == 30.0
        assert limiter.max_delay_sec == 300.0
    
    def test_delays_respect_bounds(self):
        """Delays should stay within configured bounds (with modifiers)."""
        limiter = RateLimiter(min_delay_sec=30.0, max_delay_sec=120.0)
        
        for _ in range(100):
            delay = limiter.get_random_delay()
            # At minimum should be >= min
            assert delay >= 30.0
            # At maximum with thinking pause: max + 2*min = 120 + 60 = 180
            # At night with thinking: max*3 + 2*min = 360 + 60 = 420
            assert delay <= 420.0
