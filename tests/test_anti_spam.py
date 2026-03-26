"""Tests for Anti-Spam RateLimiter."""
import pytest
from unittest.mock import AsyncMock

from src.monitors.anti_spam import RateLimiter


class TestRateLimiter:
    """Test rate limiting logic."""
    
    def test_initial_state_allows_send(self):
        limiter = RateLimiter()
        can_send, reason = limiter.can_send("chat1")
        assert can_send is True
        assert reason == "OK"
    
    def test_global_rate_limit(self):
        """After max_global sends, should block."""
        limiter = RateLimiter(max_global_per_hour=2)
        
        limiter.record_send("chat1")
        limiter.record_send("chat2")
        
        can_send, reason = limiter.can_send("chat3")
        assert can_send is False
        assert "Global" in reason
    
    def test_per_chat_rate_limit(self):
        """After max per-chat sends, should block for that chat."""
        limiter = RateLimiter(max_per_chat_per_hour=1, max_global_per_hour=100)
        
        limiter.record_send("chat1")
        
        can_send, reason = limiter.can_send("chat1")
        assert can_send is False
        assert "Chat rate limit" in reason
        
        # Different chat should still work
        can_send2, _ = limiter.can_send("chat2")
        assert can_send2 is True
    
    def test_cooldown(self):
        """After sending, cooldown period should block."""
        limiter = RateLimiter(cooldown_sec=60, max_global_per_hour=100)
        
        limiter.record_send("chat1")
        
        can_send, reason = limiter.can_send("chat1")
        assert can_send is False
        assert "Cooldown" in reason
    
    def test_random_delay_range(self):
        """Random delay should be within configured range (with thinking pause tolerance)."""
        limiter = RateLimiter(min_delay_sec=30.0, max_delay_sec=300.0)
        
        for _ in range(50):
            delay = limiter.get_random_delay()
            # Base range: 30-300s. Thinking pause can add up to 2x min = 60s.
            # Night mode (if active) can triple. We just check it's reasonable.
            assert delay >= 30.0
            assert delay <= 900.0  # max with all modifiers
    
    def test_random_delay_respects_min(self):
        """Random delay should respect min_delay_sec."""
        limiter = RateLimiter(min_delay_sec=5.0, max_delay_sec=10.0)
        
        for _ in range(20):
            delay = limiter.get_random_delay()
            assert delay >= 5.0
    
    def test_record_send_increments(self):
        """record_send should track sends."""
        limiter = RateLimiter(max_global_per_hour=100)
        
        limiter.record_send("chat1")
        limiter.record_send("chat2")
        limiter.record_send("chat1")
        
        stats = limiter.get_stats()
        assert stats["global_per_hour"] == 3
        assert stats["chats"]["chat1"] == 2
        assert stats["chats"]["chat2"] == 1
    
    @pytest.mark.asyncio
    async def test_wait_and_send_success(self):
        """wait_and_send should call send_func after delay."""
        limiter = RateLimiter(min_delay_sec=0.01, max_delay_sec=0.02)
        send_func = AsyncMock()
        
        result = await limiter.wait_and_send("chat1", send_func, "arg1", key="val")
        
        assert result is True
        send_func.assert_called_once_with("arg1", key="val")
    
    @pytest.mark.asyncio
    async def test_wait_and_send_blocked(self):
        """wait_and_send should return False when blocked."""
        limiter = RateLimiter(max_global_per_hour=0)  # Block everything
        send_func = AsyncMock()
        
        result = await limiter.wait_and_send("chat1", send_func)
        
        assert result is False
        send_func.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_wait_and_send_error_returns_false(self):
        """Send error should return False."""
        limiter = RateLimiter(min_delay_sec=0.01, max_delay_sec=0.02)
        send_func = AsyncMock(side_effect=Exception("Network error"))
        
        result = await limiter.wait_and_send("chat1", send_func)
        
        assert result is False
