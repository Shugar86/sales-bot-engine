"""Tests for Output Validators — banned phrases and greeting policy."""
import pytest
from src.core.output_validators import OutputValidator
from src.core.vibe_schema import OutputValidators as ValidatorsConfig, GreetingPolicy


@pytest.fixture
def strict_validator():
    return OutputValidator(
        validators_config=ValidatorsConfig(banned_phrases=[
            "я AI", "language model", "я искусственный интеллект",
            "к сожалению, я не могу",
        ]),
        greeting_policy=GreetingPolicy(
            enabled=True,
            greet_only_first_response=True,
            greet_only_if_user_greeted=True,
        ),
    )


@pytest.fixture
def relaxed_validator():
    return OutputValidator(
        validators_config=ValidatorsConfig(banned_phrases=[]),
        greeting_policy=GreetingPolicy(enabled=False),
    )


class TestBannedPhrases:
    def test_clean_response(self, strict_validator):
        result = strict_validator.validate("У меня собака так делала, попробуй убрать курицу")
        assert result.is_valid is True
        assert len(result.violations) == 0
    
    def test_banned_phrase_detected(self, strict_validator):
        result = strict_validator.validate("Я AI и я не могу помочь")
        assert result.is_valid is False
        assert any("Banned phrase" in v for v in result.violations)
    
    def test_case_insensitive(self, strict_validator):
        result = strict_validator.validate("Я Language Model")
        assert result.is_valid is False
    
    def test_partial_match(self, strict_validator):
        result = strict_validator.validate("К сожалению, я не могу это сделать")
        assert result.is_valid is False
    
    def test_no_banned_phrases(self, relaxed_validator):
        result = relaxed_validator.validate("Я AI бот, language model!")
        assert result.is_valid is True


class TestGreetingPolicy:
    def test_strip_greeting_not_first(self, strict_validator):
        # Not first response, user didn't greet → strip
        result = strict_validator.validate("Привет! Как дела?", is_first_response=False)
        assert result.greeting_stripped is True
        assert "Привет" not in result.cleaned_text
    
    def test_keep_greeting_first_greeted(self, strict_validator):
        # First response AND user greeted → keep
        result = strict_validator.validate("Привет! Чем помочь?", is_first_response=True, user_greeted=True)
        assert result.greeting_stripped is False
    
    def test_strip_greeting_variants(self, strict_validator):
        greetings = ["Привет", "Здравствуйте", "Здарова", "Йо", "Хай", "Добрый день"]
        for g in greetings:
            result = strict_validator.validate(f"{g}! Как дела?", is_first_response=False)
            assert result.greeting_stripped is True, f"Failed to strip: {g}"
    
    def test_no_greeting_policy(self, relaxed_validator):
        result = relaxed_validator.validate("Привет!", is_first_response=False)
        assert result.greeting_stripped is False
    
    def test_greeting_only_if_user_greeted(self, strict_validator):
        # First response but user didn't greet → strip
        result = strict_validator.validate("Привет!", is_first_response=True, user_greeted=False)
        assert result.greeting_stripped is True


class TestFormatValidation:
    def test_empty_response(self, strict_validator):
        result = strict_validator.validate("")
        assert result.is_valid is False
        assert "Empty" in result.violations[0]
    
    def test_emoji_only(self, strict_validator):
        result = strict_validator.validate("😂🤣👍")
        assert result.is_valid is False
        assert "Emoji-only" in result.violations[0]
    
    def test_too_long(self, strict_validator):
        long_text = "x" * 2001
        result = strict_validator.validate(long_text)
        assert result.is_valid is False
        assert "too long" in result.violations[0]
    
    def test_normal_text(self, strict_validator):
        result = strict_validator.validate("У меня собака так делала")
        assert result.is_valid is True


class TestFallbackGreeting:
    def test_fallback_exists(self, strict_validator):
        fallback = strict_validator.get_fallback_greeting()
        assert len(fallback) > 0
    
    def test_no_fallback(self):
        v = OutputValidator(greeting_policy=GreetingPolicy(fallback_variants=[]))
        assert v.get_fallback_greeting() == ""


class TestValidationResult:
    def test_clean_result(self, strict_validator):
        result = strict_validator.validate("Нормальный ответ")
        assert result.is_valid is True
        assert result.cleaned_text == "Нормальный ответ"
        assert result.violations == []
    
    def test_multiple_violations(self, strict_validator):
        result = strict_validator.validate("Я AI бот", is_first_response=False)
        # Should have banned phrase violation
        assert result.is_valid is False
