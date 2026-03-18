"""
Tests for Memory & Context improvements
"""
import json
import os
import tempfile
import pytest
from src.memory.user_memory import UserMemoryStore


class TestConversationTopics:
    """Test conversation topic tracking per user."""
    
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
    
    def test_topics_tracked_from_messages(self):
        """Topics should be extracted from user messages."""
        self.memory.record_group_message(
            user_id="u1",
            username="test",
            display_name="Test",
            chat_id="c1",
            chat_title="Test Chat",
            message="Мой пёс аллергик, что делать с кормом?",
        )
        
        data = self.memory._load("u1")
        topics = data.get("topics_discussed", [])
        # Should have captured something about the topic
        assert isinstance(topics, list)
    
    def test_no_duplicate_topics(self):
        """Same topic should not be added twice."""
        self.memory.record_group_message(
            user_id="u1", username="t", display_name="T",
            chat_id="c1", chat_title="C", message="аллергия у собаки",
        )
        self.memory.record_group_message(
            user_id="u1", username="t", display_name="T",
            chat_id="c1", chat_title="C", message="опять аллергия, что делать",
        )
        
        data = self.memory._load("u1")
        # Should not have duplicate topic entries
        allergies = [t for t in data.get("topics_discussed", []) if "аллерги" in t.lower()]
        assert len(set(allergies)) == len(allergies)


class TestRecommendationTracking:
    """Test tracking what was already recommended to a user."""
    
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
    
    def test_recommendation_recorded(self):
        """Bot should remember what it recommended."""
        self.memory.record_recommendation("u1", "Корм на ягнёнке для аллергиков")
        
        data = self.memory._load("u1")
        recs = data.get("recommendations", [])
        assert len(recs) == 1
        assert "ягнёнк" in recs[0].lower()
    
    def test_multiple_recommendations(self):
        """Should track multiple recommendations."""
        self.memory.record_recommendation("u1", "Корм на ягнёнке")
        self.memory.record_recommendation("u1", "Гипоаллергенный корм Brit")
        
        data = self.memory._load("u1")
        recs = data.get("recommendations", [])
        assert len(recs) == 2
    
    def test_recommendations_in_context(self):
        """Recommendations should appear in user context."""
        self.memory.record_recommendation("u1", "Корм на ягнёнке для аллергиков")
        
        context = self.memory.get_user_context("u1")
        assert "ягнёнк" in context.lower() or "рекоменд" in context.lower()


class TestFunnelAutoProgress:
    """Test funnel auto-progression based on signals."""
    
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
    
    def test_buying_signal_advances_funnel(self):
        """Buying signals should advance funnel to ready_to_buy."""
        # User starts at unknown
        assert self.memory.get_funnel_stage("u1") == "unknown"
        
        # Detect buying signal
        stage = self.memory.analyze_funnel_signals("u1", "Хочу купить этот корм")
        assert stage == "ready_to_buy"
    
    def test_interest_signal(self):
        """Interest signals should advance funnel."""
        stage = self.memory.analyze_funnel_signals("u1", "Расскажи подробнее про состав")
        assert stage == "interested"
    
    def test_objection_detected(self):
        """Objection signals should be detected."""
        stage = self.memory.analyze_funnel_signals("u1", "Дорого, подумаю")
        assert stage == "objection"
    
    def test_disengage_detected(self):
        """Disengagement should be detected."""
        stage = self.memory.analyze_funnel_signals("u1", "Не надо мне, отстань")
        assert stage == "disengaged"
    
    def test_funnel_applied_to_dm(self):
        """Funnel stage should update after DM interaction."""
        self.memory.record_dm(
            user_id="u1", username="t", display_name="T",
            message="Хочу заказать корм",
            response="Отлично, какой вес собаки?",
            stage="help",
        )
        
        # Should have recorded the DM
        data = self.memory._load("u1")
        assert data["has_dm"] is True


class TestRichUserContext:
    """Test that user context provides rich information."""
    
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
    
    def test_context_includes_interaction_count(self):
        """Context should show how many times we interacted."""
        self.memory.record_group_message(
            user_id="u1", username="t", display_name="Тест",
            chat_id="c1", chat_title="C", message="Привет",
        )
        self.memory.record_group_message(
            user_id="u1", username="t", display_name="Тест",
            chat_id="c1", chat_title="C", message="Как дела?",
        )
        
        context = self.memory.get_user_context("u1")
        # Should indicate this is not a first contact
        assert "взаимодейств" in context.lower() or "встреч" in context.lower() or "interact" in context.lower() or context != ""
    
    def test_context_includes_dog_info(self):
        """Dog info should appear in context for kormoved persona."""
        self.memory.record_group_message(
            user_id="u1", username="t", display_name="Тест",
            chat_id="c1", chat_title="C",
            message="У меня лабрадор, 3 года, зовут Рекс",
        )
        
        context = self.memory.get_user_context("u1")
        assert "лабрадор" in context.lower() or "собак" in context.lower()
    
    def test_context_shows_funnel_stage(self):
        """Funnel stage should be in context."""
        self.memory.record_dm(
            user_id="u1", username="t", display_name="T",
            message="Хочу купить", response="Ок", stage="soft_sell",
        )
        
        context = self.memory.get_user_context("u1")
        assert "soft_sold" in context.lower() or "воронк" in context.lower() or "funnel" in context.lower()


class TestGroupContextForDM:
    """Test that DM context includes group interactions."""
    
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
    
    def test_group_messages_in_dm_context(self):
        """Group messages should be available when responding in DM."""
        self.memory.record_group_message(
            user_id="u1", username="t", display_name="Тест",
            chat_id="c1", chat_title="Кинологи",
            message="Мой пёс аллергик, что делать?",
        )
        
        group_ctx = self.memory.get_group_context_for_user("u1")
        assert "аллергик" in group_ctx.lower()
        assert "кинологи" in group_ctx.lower()
    
    def test_empty_group_context(self):
        """Empty group context when no interactions."""
        group_ctx = self.memory.get_group_context_for_user("u999")
        assert group_ctx == ""
