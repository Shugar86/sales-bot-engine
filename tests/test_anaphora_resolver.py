"""
Tests for Anaphora Resolver — context memory for follow-up questions.

Tests the patterns ported from ai-tutor-engine:
- "что это?" → resolves to previous search results
- "подешевле" → understands context of previous conversation
- Context tracking per user per chat
"""

import pytest
from src.responders.anaphora_resolver import (
    AnaphoraResolver,
    AnaphoraResult,
    ConversationContext,
    ANAPHORA_TRIGGERS,
)


# ══════════════════════════════════════════════════════════════
# CONVERSATION CONTEXT
# ══════════════════════════════════════════════════════════════

class TestConversationContext:
    def test_default_values(self):
        ctx = ConversationContext()
        assert ctx.last_tool_name is None
        assert ctx.last_tool_args == {}
        assert ctx.last_tool_result is None
        assert ctx.last_query is None
        assert ctx.last_products == []
        assert ctx.message_count == 0


# ══════════════════════════════════════════════════════════════
# ANAPHORA RESOLVER
# ══════════════════════════════════════════════════════════════

class TestAnaphoraResolver:
    @pytest.fixture
    def resolver(self):
        return AnaphoraResolver(max_contexts=10)

    def test_resolve_no_anaphora(self, resolver):
        result = resolver.resolve("user1", "chat1", "какой корм?")
        assert not result.has_anaphora

    def test_resolve_what_is_this(self, resolver):
        # Setup: previous search
        resolver.update_context(
            "user1", "chat1",
            tool_name="product_search",
            tool_args={"query": "корм из индейки"},
            query="корм из индейки",
        )

        result = resolver.resolve("user1", "chat1", "что это?")
        assert result.has_anaphora
        assert result.resolved_query == "корм из индейки"

    def test_resolve_detailed_info(self, resolver):
        resolver.update_context(
            "user1", "chat1",
            tool_name="product_search",
            tool_args={"query": "дог-дюк"},
            query="дог-дюк",
        )

        result = resolver.resolve("user1", "chat1", "расскажи подробнее")
        assert result.has_anaphora
        assert result.resolved_query == "дог-дюк"

    def test_resolve_cheaper(self, resolver):
        resolver.update_context(
            "user1", "chat1",
            tool_name="product_search",
            tool_args={"query": "корм"},
            query="корм",
            products=["Дог-Дюк", "Кото-Ролл"],
        )

        result = resolver.resolve("user1", "chat1", "подешевле")
        assert result.has_anaphora
        assert result.comparison_direction == "cheaper"
        assert result.resolved_query == "корм"

    def test_resolve_similar(self, resolver):
        resolver.update_context(
            "user1", "chat1",
            tool_name="product_search",
            tool_args={"query": "корм из курицы"},
            query="корм из курицы",
        )

        result = resolver.resolve("user1", "chat1", "аналоги")
        assert result.has_anaphora
        assert result.comparison_direction == "similar"

    def test_resolve_show_more(self, resolver):
        resolver.update_context(
            "user1", "chat1",
            tool_name="product_search",
            tool_args={"query": "лакомства"},
            query="лакомства",
        )

        result = resolver.resolve("user1", "chat1", "покажи еще")
        assert result.has_anaphora
        assert result.resolved_query == "лакомства"
        assert result.context_tool == "product_search"

    def test_no_context_empty_result(self, resolver):
        # No previous context
        result = resolver.resolve("user1", "chat1", "что это?")
        assert result.has_anaphora
        # No context → resolved_query is empty string (no data to resolve to)
        assert not result.resolved_query  # Empty or None

    def test_context_isolation_between_users(self, resolver):
        resolver.update_context(
            "user1", "chat1",
            tool_name="product_search",
            tool_args={"query": "корм1"},
            query="корм1",
        )
        resolver.update_context(
            "user2", "chat1",
            tool_name="product_search",
            tool_args={"query": "корм2"},
            query="корм2",
        )

        result1 = resolver.resolve("user1", "chat1", "что это?")
        result2 = resolver.resolve("user2", "chat1", "что это?")

        assert result1.resolved_query == "корм1"
        assert result2.resolved_query == "корм2"

    def test_context_isolation_between_chats(self, resolver):
        resolver.update_context(
            "user1", "chat1",
            tool_name="product_search",
            tool_args={"query": "корм_чат1"},
            query="корм_чат1",
        )
        resolver.update_context(
            "user1", "chat2",
            tool_name="product_search",
            tool_args={"query": "корм_чат2"},
            query="корм_чат2",
        )

        result1 = resolver.resolve("user1", "chat1", "что это?")
        result2 = resolver.resolve("user1", "chat2", "что это?")

        assert result1.resolved_query == "корм_чат1"
        assert result2.resolved_query == "корм_чат2"

    def test_context_update_increments_count(self, resolver):
        resolver.update_context("user1", "chat1", query="test1")
        ctx1 = resolver.get_context("user1", "chat1")
        assert ctx1.message_count == 1

        resolver.update_context("user1", "chat1", query="test2")
        ctx2 = resolver.get_context("user1", "chat1")
        assert ctx2.message_count == 2

    def test_clear_context(self, resolver):
        resolver.update_context("user1", "chat1", query="test")
        resolver.clear_context("user1", "chat1")
        ctx = resolver.get_context("user1", "chat1")
        assert ctx.message_count == 0

    def test_max_contexts_eviction(self, resolver):
        # Fill up to max
        for i in range(15):
            resolver.update_context(f"user{i}", "chat1", query=f"query{i}")

        # Should have evicted oldest
        stats = resolver.get_stats()
        assert stats["total_contexts"] <= 10


# ══════════════════════════════════════════════════════════════
# TRIGGER PHRASES
# ══════════════════════════════════════════════════════════════

class TestTriggerPhrases:
    @pytest.fixture
    def resolver(self):
        r = AnaphoraResolver()
        r.update_context(
            "u", "c",
            tool_name="product_search",
            tool_args={"query": "тест"},
            query="тест",
        )
        return r

    def test_various_triggers(self, resolver):
        triggers = [
            "что это?",
            "что такое?",
            "расскажи подробнее",
            "про это",
            "об этом",
            "подробнее",
            "а это что?",
        ]
        for trigger in triggers:
            result = resolver.resolve("u", "c", trigger)
            assert result.has_anaphora, f"Failed for trigger: {trigger}"


# ══════════════════════════════════════════════════════════════
# EDGE CASES
# ══════════════════════════════════════════════════════════════

class TestEdgeCases:
    @pytest.fixture
    def resolver(self):
        return AnaphoraResolver()

    def test_empty_question(self, resolver):
        result = resolver.resolve("u", "c", "")
        assert not result.has_anaphora

    def test_none_question(self, resolver):
        result = resolver.resolve("u", "c", None)
        assert not result.has_anaphora

    def test_mixed_context_and_new_question(self, resolver):
        # Previous context
        resolver.update_context("u", "c", tool_name="product_search", tool_args={"query": "старый"})
        # New question (not anaphoric)
        result = resolver.resolve("u", "c", "какой корм для щенка?")
        assert not result.has_anaphora

    def test_category_preserved(self, resolver):
        resolver.update_context(
            "u", "c",
            tool_name="product_search",
            tool_args={"query": "корм", "category": "dog"},
            category="dog",
        )
        result = resolver.resolve("u", "c", "подешевле")
        assert result.context_args.get("category") == "dog"

    def test_animal_type_preserved(self, resolver):
        resolver.update_context(
            "u", "c",
            tool_name="product_search",
            tool_args={"query": "корм"},
            animal_type="dog",
        )
        result = resolver.resolve("u", "c", "что это?")
        assert result.context_args.get("animal_type") == "dog"
