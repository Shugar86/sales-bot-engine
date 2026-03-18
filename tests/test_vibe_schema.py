"""Tests for Vibe Schema — Pydantic models."""
import pytest
from src.core.vibe_schema import (
    VibePersona, VibeBehavior, GreetingPolicy, OutputValidators,
    ContextPolicy, PreprocessRules, AntiSpamConfig, MemoryConfig,
    ResponseExample, RouterExample,
)


class TestVibePersona:
    """Tests for VibePersona model."""
    
    def test_minimal(self):
        p = VibePersona(role="Тест")
        assert p.role == "Тест"
        assert p.core_emotions == []
    
    def test_full(self):
        p = VibePersona(
            role="Консультант",
            personality="35 лет, собаковод",
            backstory="Бывший кинолог",
            voice="Бывалый",
            core_emotions=["caring", "honest"],
            values=["Честность"],
            taboos=["политика"],
        )
        assert p.backstory == "Бывший кинолог"
        assert "caring" in p.core_emotions
        assert "политика" in p.taboos
    
    def test_extra_fields_ignored(self):
        p = VibePersona(role="Test", unknown_field="ignored")
        assert p.role == "Test"


class TestGreetingPolicy:
    def test_defaults(self):
        gp = GreetingPolicy()
        assert gp.enabled is True
        assert gp.greet_only_first_response is True
        assert len(gp.fallback_variants) > 0
    
    def test_custom(self):
        gp = GreetingPolicy(
            enabled=False,
            greeting_variants=["Привет!", "Здарова!"],
        )
        assert gp.enabled is False
        assert len(gp.greeting_variants) == 2


class TestOutputValidators:
    def test_defaults(self):
        v = OutputValidators()
        assert v.banned_phrases == []
        assert v.forbid_markdown_links_in_wrapper is True
    
    def test_with_banned(self):
        v = OutputValidators(banned_phrases=["я AI", "language model"])
        assert "я AI" in v.banned_phrases


class TestContextPolicy:
    def test_defaults(self):
        cp = ContextPolicy()
        assert cp.namespace is None
        assert cp.ttl_turns == 10
    
    def test_custom(self):
        cp = ContextPolicy(namespace="bot1", keep_keys=["name", "stage"], ttl_turns=5)
        assert cp.namespace == "bot1"
        assert "name" in cp.keep_keys


class TestVibeBehavior:
    def test_defaults(self):
        b = VibeBehavior()
        assert b.on_greeting == ""
        assert b.greeting_policy is None
    
    def test_full(self):
        b = VibeBehavior(
            on_greeting="Привет!",
            on_tool_success="Дай ответ",
            on_dm="Болтай",
            on_taboo="Не моя тема",
            always="Ты живой человек",
            greeting_policy=GreetingPolicy(enabled=True),
            validators=OutputValidators(banned_phrases=["курс"]),
        )
        assert b.always == "Ты живой человек"
        assert b.greeting_policy.enabled is True
        assert "курс" in b.validators.banned_phrases


class TestAntiSpamConfig:
    def test_defaults(self):
        c = AntiSpamConfig()
        assert c.leave_on_read == 0.35
        assert c.night_slowdown == 3.0
    
    def test_custom(self):
        c = AntiSpamConfig(leave_on_read=0.5, night_start=22)
        assert c.leave_on_read == 0.5


class TestMemoryConfig:
    def test_defaults(self):
        m = MemoryConfig()
        assert m.reference_past is True
        assert m.track_funnel is True


class TestResponseExample:
    def test(self):
        ex = ResponseExample(trigger="Дорого", bad_response="bad", good_response="good")
        assert ex.trigger == "Дорого"


class TestRouterExample:
    def test(self):
        ex = RouterExample(
            context=[{"role": "user", "content": "Привет"}],
            user_query="Что посоветуешь?",
            expected_tool="product_search",
            expected_args={"query": "корм для собак"},
        )
        assert ex.expected_tool == "product_search"
