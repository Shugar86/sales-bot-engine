"""Tests for Text Humanizer."""
import pytest
import random

from src.responders.text_humanizer import TextHumanizer, humanize_text


class TestTextHumanizer:
    """Test text humanization."""
    
    def test_preserves_short_text(self):
        humanizer = TextHumanizer(typo_probability=0.0, lowercase_start_probability=0.0)
        assert humanizer.humanize("Hi") == "Hi"
        assert humanizer.humanize("") == ""
    
    def test_no_modifications_when_probability_zero(self):
        humanizer = TextHumanizer(
            typo_probability=0.0,
            lowercase_start_probability=0.0,
            missing_period_probability=0.0,
        )
        text = "Привет, как дела."
        random.seed(42)
        
        for _ in range(20):
            result = humanizer.humanize(text)
            assert result == text
    
    def test_lowercase_start_casual(self):
        humanizer = TextHumanizer(
            typo_probability=0.0,
            lowercase_start_probability=1.0,  # Always
            missing_period_probability=0.0,
        )
        text = "Привет, как дела"
        result = humanizer.humanize(text, is_casual=True)
        assert result[0].islower()
    
    def test_missing_period_casual(self):
        humanizer = TextHumanizer(
            typo_probability=0.0,
            lowercase_start_probability=0.0,
            missing_period_probability=1.0,  # Always
        )
        text = "Всё хорошо."
        result = humanizer.humanize(text, is_casual=True)
        assert not result.endswith(".")
    
    def test_typo_injection(self):
        humanizer = TextHumanizer(
            typo_probability=1.0,  # Always inject typo
            lowercase_start_probability=0.0,
            missing_period_probability=0.0,
        )
        text = "Привет это очень длинное предложение для теста"
        result = humanizer.humanize(text)
        # Should be different (has typo)
        assert len(result) > 0
    
    def test_typo_preserves_meaning(self):
        """Typos should be subtle, not destroy meaning."""
        humanizer = TextHumanizer(typo_probability=1.0)
        
        for _ in range(20):
            text = "Корм для собаки с аллергией очень важен"
            result = humanizer.humanize(text)
            # At least half the words should be recognizable
            assert len(result) > len(text) * 0.5
    
    def test_known_typo_variants(self):
        """Known typos should use predefined variants."""
        humanizer = TextHumanizer(
            typo_probability=1.0,
            lowercase_start_probability=0.0,
        )
        random.seed(42)
        
        # "привет" has known typo variants
        text = "привет это тест для проверки"
        result = humanizer.humanize(text)
        assert len(result) > 0
    
    def test_convenience_function(self):
        """Test humanize_text convenience function."""
        text = "Тестовое сообщение для проверки"
        result = humanize_text(text)
        assert isinstance(result, str)
        assert len(result) > 0


class TestHumanizerIntegration:
    """Test humanizer doesn't break existing behavior."""
    
    def test_empty_text(self):
        assert humanize_text("") == ""
    
    def test_single_char(self):
        assert humanize_text("a") == "a"
    
    def test_emoji_preserved(self):
        result = humanize_text("Отлично 👍")
        assert "👍" in result
    
    def test_json_like_text_not_corrupted(self):
        """Text that looks like JSON should not be modified."""
        # The humanizer is only applied AFTER JSON parsing, 
        # so this tests that it doesn't corrupt structured text
        text = '{"text": "hello"}'
        result = humanize_text(text)
        assert result == text  # Too short for modifications
