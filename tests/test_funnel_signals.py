"""Tests for funnel auto-progression signals."""
import pytest
import tempfile

from src.memory.user_memory import UserMemoryStore


class TestFunnelSignals:
    """Test funnel signal analysis."""
    
    def test_buying_signal(self, tmp_path):
        store = UserMemoryStore(memory_dir=str(tmp_path / "mem"), persona_name="kormoved")
        stage = store.analyze_funnel_signals("u1", "Хочу заказать, скинь ссылку")
        assert stage == "ready_to_buy"
    
    def test_interest_signal(self, tmp_path):
        store = UserMemoryStore(memory_dir=str(tmp_path / "mem"), persona_name="kormoved")
        stage = store.analyze_funnel_signals("u1", "Расскажи подробнее про состав")
        assert stage == "interested"
    
    def test_asking_questions_from_interested(self, tmp_path):
        store = UserMemoryStore(memory_dir=str(tmp_path / "mem"), persona_name="kormoved")
        # Set up: user is already helping (engaged)
        store.record_dm("u1", "user1", "User1", "Расскажи подробнее", "Конечно, вот...", "help")
        assert store.get_funnel_stage("u1") == "helping"
        # Now they ask more detailed questions
        stage = store.analyze_funnel_signals("u1", "А в чём разница с Хиллс?")
        assert stage == "asking_questions"
    
    def test_objection_signal(self, tmp_path):
        store = UserMemoryStore(memory_dir=str(tmp_path / "mem"), persona_name="kormoved")
        stage = store.analyze_funnel_signals("u1", "Дорого, не могу позволить")
        assert stage == "objection"
    
    def test_disengagement_signal(self, tmp_path):
        store = UserMemoryStore(memory_dir=str(tmp_path / "mem"), persona_name="kormoved")
        stage = store.analyze_funnel_signals("u1", "Не надо, отстань")
        assert stage == "disengaged"
    
    def test_no_signal_keeps_current(self, tmp_path):
        store = UserMemoryStore(memory_dir=str(tmp_path / "mem"), persona_name="kormoved")
        store.record_dm("u1", "user1", "User1", "Привет", "Здарова!", "engage")
        stage = store.analyze_funnel_signals("u1", "Какая сегодня погода")
        assert stage == "engaged"
    
    def test_buying_signal_overrides_any_stage(self, tmp_path):
        store = UserMemoryStore(memory_dir=str(tmp_path / "mem"), persona_name="kormoved")
        store.record_dm("u1", "user1", "User1", "Не надо", "Ок", "disengage")
        stage = store.analyze_funnel_signals("u1", "Ладно, хочу купить")
        assert stage == "ready_to_buy"
