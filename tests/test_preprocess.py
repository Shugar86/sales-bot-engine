"""
Tests for Preprocess Node — deterministic routing shortcuts.

Tests the patterns ported from ai-tutor-engine:
- Pure greeting → skip LLM
- Follow-up reuse → last tool call
- Trivial message detection
- Price shock detection
- Off-topic handling
"""

import pytest
from src.responders.preprocess import (
    PreprocessNode,
    PreprocessResult,
    is_trivial_message,
)
from src.responders.response_composer import (
    ResponseComposer,
    GreetingPolicy,
)


# ══════════════════════════════════════════════════════════════
# TRIVIAL MESSAGE DETECTION
# ══════════════════════════════════════════════════════════════

class TestTrivialMessages:
    def test_single_dot(self):
        assert is_trivial_message(".")

    def test_multiple_dots(self):
        assert is_trivial_message("...")

    def test_plus_minus(self):
        assert is_trivial_message("+")
        assert is_trivial_message("++")
        assert is_trivial_message("-")

    def test_question_marks(self):
        assert is_trivial_message("???")
        assert is_trivial_message("!!!")

    def test_single_letters(self):
        assert is_trivial_message("ок")
        assert is_trivial_message("да")
        assert is_trivial_message("нет")
        assert is_trivial_message("хм")

    def test_not_trivial(self):
        assert not is_trivial_message("какой корм?")
        assert not is_trivial_message("привет подскажи")
        assert not is_trivial_message("у собаки аллергия")

    def test_empty_string(self):
        assert is_trivial_message("")

    def test_none(self):
        assert is_trivial_message(None)

    def test_short_text(self):
        assert is_trivial_message("a")  # Very short


# ══════════════════════════════════════════════════════════════
# PREPROCESS RESULT
# ══════════════════════════════════════════════════════════════

class TestPreprocessResult:
    def test_has_shortcut_with_response(self):
        result = PreprocessResult(shortcut_response="Привет!")
        assert result.has_shortcut

    def test_has_shortcut_with_reuse(self):
        result = PreprocessResult(reuse_last_tool=True)
        assert result.has_shortcut

    def test_has_shortcut_with_skip(self):
        result = PreprocessResult(skip_generation=True)
        assert result.has_shortcut

    def test_no_shortcut(self):
        result = PreprocessResult()
        assert not result.has_shortcut


# ══════════════════════════════════════════════════════════════
# PREPROCESS NODE
# ══════════════════════════════════════════════════════════════

class TestPreprocessNode:
    @pytest.fixture
    def composer(self):
        return ResponseComposer(
            persona_name="test",
            greeting_policy=GreetingPolicy(
                greeting_variants=["Привет! 🐾", "Здарова!"],
            ),
        )

    @pytest.fixture
    def preprocessor(self, composer):
        return PreprocessNode(
            composer=composer,
            followup_reuse_tools=["product_search"],
        )

    def test_trivial_skip(self, preprocessor):
        result = preprocessor.process(
            question=".",
            last_context={},
            is_first_response=True,
            user_greeted=False,
            is_dm=False,
        )
        assert result.skip_generation
        assert result.pipeline_step == "preprocess (trivial_skip)"

    def test_pure_greeting_shortcut(self, preprocessor):
        result = preprocessor.process(
            question="привет",
            last_context={},
            is_first_response=True,
            user_greeted=True,
            is_dm=False,
        )
        assert result.has_shortcut
        assert result.shortcut_response in ["Привет! 🐾", "Здарова!"]
        assert "greeting_skip" in result.pipeline_step

    def test_greeting_not_first_response(self, preprocessor):
        # Second response — no greeting
        result = preprocessor.process(
            question="привет",
            last_context={},
            is_first_response=False,  # Not first
            user_greeted=True,
            is_dm=False,
        )
        # Should get fallback greeting
        assert result.has_shortcut or result.shortcut_response is not None

    def test_followup_reuse(self, preprocessor):
        result = preprocessor.process(
            question="покажи еще",
            last_context={
                "last_tool_name": "product_search",
                "last_tool_args": {"query": "корм", "category": "dog"},
            },
            is_first_response=False,
            user_greeted=False,
            is_dm=False,
        )
        assert result.reuse_last_tool
        assert "reuse_last_tool" in result.pipeline_step

    def test_followup_wrong_tool(self, preprocessor):
        # Follow-up but tool not in reuse list
        result = preprocessor.process(
            question="покажи еще",
            last_context={
                "last_tool_name": "shop_info",  # Not in reuse list
                "last_tool_args": {},
            },
            is_first_response=False,
            user_greeted=False,
            is_dm=False,
        )
        assert not result.reuse_last_tool

    def test_price_shock(self, preprocessor):
        result = preprocessor.process(
            question="дорого!",
            last_context={},
            is_first_response=False,
            user_greeted=False,
            is_dm=False,
        )
        assert result.has_shortcut
        assert result.shortcut_response is not None
        assert "price_shock" in result.pipeline_step

    def test_no_shortcut_normal_question(self, preprocessor):
        result = preprocessor.process(
            question="какой корм для хаски?",
            last_context={},
            is_first_response=False,
            user_greeted=False,
            is_dm=False,
        )
        assert not result.has_shortcut
        assert "no_shortcut" in result.pipeline_step

    def test_dm_pure_greeting(self, preprocessor):
        result = preprocessor.process(
            question="привет",
            last_context={},
            is_first_response=True,
            user_greeted=True,
            is_dm=True,
        )
        assert result.has_shortcut
        assert result.shortcut_response is not None


# ══════════════════════════════════════════════════════════════
# EDGE CASES
# ══════════════════════════════════════════════════════════════

class TestPreprocessEdgeCases:
    @pytest.fixture
    def preprocessor(self):
        composer = ResponseComposer(
            persona_name="test",
            greeting_policy=GreetingPolicy(greeting_variants=["Привет!"]),
        )
        return PreprocessNode(composer=composer, followup_reuse_tools=["product_search"])

    def test_empty_question(self, preprocessor):
        result = preprocessor.process(
            question="",
            last_context={},
            is_first_response=True,
            user_greeted=False,
            is_dm=False,
        )
        assert result.skip_generation  # Empty is trivial

    def test_none_question(self, preprocessor):
        result = preprocessor.process(
            question=None,
            last_context={},
            is_first_response=True,
            user_greeted=False,
            is_dm=False,
        )
        assert result.skip_generation

    def test_offtopic_joke(self, preprocessor):
        result = preprocessor.process(
            question="расскажи шутку",
            last_context={},
            is_first_response=False,
            user_greeted=False,
            is_dm=False,
        )
        assert result.has_shortcut
        assert result.shortcut_response is not None
