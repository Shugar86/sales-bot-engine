"""
Tests for DM Conversation Flow improvements
"""
import tempfile
from src.memory.user_memory import UserMemoryStore


class TestDMConversationFlow:
    """Test DM conversation flow with memory integration."""
    
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
    
    def test_dm_records_funnel_progression(self):
        """DM should progress funnel based on signals."""
        # First DM — initial contact
        self.memory.record_dm(
            user_id="u1", username="t", display_name="Тест",
            message="Привет", response="Здарова!", stage="engage",
        )
        assert self.memory.get_funnel_stage("u1") == "engaged"
        
        # Second DM — shows interest
        self.memory.record_dm(
            user_id="u1", username="t", display_name="Тест",
            message="Расскажи про корм", response="Конечно!", stage="help",
        )
        assert self.memory.get_funnel_stage("u1") == "helping"
        
        # Third DM — wants to buy
        self.memory.record_dm(
            user_id="u1", username="t", display_name="Тест",
            message="Хочу заказать", response="Отлично!", stage="soft_sell",
        )
        assert self.memory.get_funnel_stage("u1") == "soft_sold"
    
    def test_dm_context_includes_group_history(self):
        """DM context should include what user said in groups."""
        self.memory.record_group_message(
            user_id="u1", username="t", display_name="Тест",
            chat_id="c1", chat_title="Кинологи",
            message="Мой пёс аллергик, что делать?",
        )
        
        group_ctx = self.memory.get_group_context_for_user("u1")
        assert "аллергик" in group_ctx.lower()
    
    def test_dm_history_summary_built(self):
        """DM history summary should be built from interactions."""
        self.memory.record_dm(
            user_id="u1", username="t", display_name="Тест",
            message="Какой корм для лабрадора?",
            response="Для лабрадора бери с ягнёнком",
            stage="help",
        )
        
        data = self.memory._load("u1")
        summary = data.get("dm_history_summary", "")
        assert "лабрадор" in summary.lower()
        assert "ягнёнк" in summary.lower()
    
    def test_recommendations_avoid_repetition(self):
        """Bot should remember what it already recommended."""
        self.memory.record_recommendation("u1", "Корм на ягнёнке для аллергиков")
        self.memory.record_recommendation("u1", "Brit Care для чувствительного ЖКТ")
        
        recs = self.memory.get_recommendations("u1")
        assert len(recs) == 2
        assert "ягнёнк" in recs[0].lower()
        assert "brit" in recs[1].lower()
    
    def test_recommendations_in_context(self):
        """Previous recommendations should appear in user context."""
        self.memory.record_recommendation("u1", "Корм на ягнёнке")
        
        context = self.memory.get_user_context("u1")
        assert "ягнёнк" in context.lower() or "рекоменд" in context.lower()
    
    def test_entity_extraction_for_kormoved(self):
        """Dog info should be extracted from messages."""
        self.memory.record_group_message(
            user_id="u1", username="t", display_name="Тест",
            chat_id="c1", chat_title="Chat",
            message="У меня хаски, 2 года, зовут Барон, аллергия на курицу",
        )
        
        context = self.memory.get_user_context("u1")
        assert "хаски" in context.lower()
        assert "барон" in context.lower()
        assert "аллерги" in context.lower()
    
    def test_multiple_users_isolated(self):
        """Different users should have isolated memory."""
        self.memory.record_dm(
            user_id="u1", username="t1", display_name="Тест1",
            message="У меня лабрадор", response="Ок", stage="help",
        )
        self.memory.record_dm(
            user_id="u2", username="t2", display_name="Тест2",
            message="У меня хаски", response="Ок", stage="help",
        )
        
        ctx1 = self.memory.get_user_context("u1")
        ctx2 = self.memory.get_user_context("u2")
        
        assert "лабрадор" in ctx1.lower()
        assert "хаски" in ctx2.lower()
        assert "хаски" not in ctx1.lower()
        assert "лабрадор" not in ctx2.lower()


class TestDMFunnelSignals:
    """Test funnel signal detection in DMs."""
    
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
    
    def test_buying_signals(self):
        buying_messages = [
            "Хочу купить",
            "Сколько стоит?",
            "Где заказать?",
            "Скинь ссылку",
            "Когда доставка?",
        ]
        for msg in buying_messages:
            stage = self.memory.analyze_funnel_signals("u1", msg)
            assert stage == "ready_to_buy", f"'{msg}' should trigger ready_to_buy"
    
    def test_interest_signals(self):
        interest_messages = [
            "Расскажи подробнее",
            "А как это работает?",
            "А почему так?",
            "Интересно, а что насчёт...",
        ]
        for msg in interest_messages:
            stage = self.memory.analyze_funnel_signals("u1", msg)
            assert stage in ("interested", "asking_questions"), f"'{msg}' should trigger interest"
    
    def test_objection_signals(self):
        objection_messages = [
            "Дорого",
            "Подумаю",
            "Не уверен",
            "Может позже",
        ]
        for msg in objection_messages:
            stage = self.memory.analyze_funnel_signals("u1", msg)
            assert stage == "objection", f"'{msg}' should trigger objection"
    
    def test_disengage_signals(self):
        disengage_messages = [
            "Не надо",
            "Отстань",
        ]
        for msg in disengage_messages:
            stage = self.memory.analyze_funnel_signals("u1", msg)
            assert stage == "disengaged", f"'{msg}' should trigger disengage"
