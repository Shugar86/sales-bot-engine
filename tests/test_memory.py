"""Tests for UserMemoryStore"""
import pytest
import os
from src.memory.user_memory import UserMemoryStore


@pytest.fixture
def store(tmp_path):
    """Store с временной директорией — kormoved persona"""
    return UserMemoryStore(memory_dir=str(tmp_path / "memory"), persona_name="kormoved")


class TestUserMemoryCRUD:
    """Тесты создания, чтения, записи"""
    
    def test_new_user_creates_record(self, store):
        store.record_group_message(
            user_id="123",
            username="testuser",
            display_name="Test User",
            chat_id="456",
            chat_title="Test Chat",
            message="Привет",
        )
        
        context = store.get_user_context("123")
        assert "Test User" in context
    
    def test_group_message_recorded(self, store):
        store.record_group_message(
            user_id="123",
            username="testuser",
            display_name="Test",
            chat_id="456",
            chat_title="Chat",
            message="У меня овчарка 3 года",
        )
        
        data = store._load("123")
        assert len(data["group_messages"]) == 1
        assert data["group_messages"][0]["text"] == "У меня овчарка 3 года"
    
    def test_dm_recorded(self, store):
        store.record_dm(
            user_id="123",
            username="testuser",
            display_name="Test",
            message="А про корм подробнее?",
            response="Конечно, вот...",
            stage="help",
        )
        
        data = store._load("123")
        assert data["has_dm"] is True
        assert data["funnel_stage"] == "helping"
    
    def test_messages_capped_at_10(self, store):
        for i in range(15):
            store.record_group_message(
                user_id="123",
                username="test",
                display_name="Test",
                chat_id="456",
                chat_title="Chat",
                message=f"Message {i}",
            )
        
        data = store._load("123")
        assert len(data["group_messages"]) == 10
    
    def test_notes_capped_at_20(self, store):
        for i in range(25):
            store.add_note("123", f"Note {i}")
        
        data = store._load("123")
        assert len(data["notes"]) == 20


class TestDogInfoExtraction:
    """Тесты авто-извлечения информации о собаке"""
    
    def test_breed_extraction(self, store):
        store.record_group_message("123", "u", "U", "456", "Chat", "У меня немецкая овчарка")
        
        data = store._load("123")
        assert data["dog_breed"] == "Немецкая овчарка"
    
    def test_problem_extraction(self, store):
        store.record_group_message("123", "u", "U", "456", "Chat", "Собака чешется постоянно")
        
        data = store._load("123")
        assert "зуд/чесотка" in data["dog_problems"]
    
    def test_age_extraction(self, store):
        store.record_group_message("123", "u", "U", "456", "Chat", "Овчарке 3 года")
        
        data = store._load("123")
        assert data["dog_age"] == "3 года"
    
    def test_name_extraction(self, store):
        store.record_group_message("123", "u", "U", "456", "Chat", "Собаку зовут Рекс")
        
        data = store._load("123")
        assert data["dog_name"] == "Рекс"
    
    def test_multiple_problems_accumulated(self, store):
        store.record_group_message("123", "u", "U", "456", "Chat", "Аллергия на курицу")
        store.record_group_message("123", "u", "U", "456", "Chat", "Ещё чешется и хромает")
        
        data = store._load("123")
        assert "аллергия" in data["dog_problems"]
        assert "зуд/чесотка" in data["dog_problems"]
        assert "хромота" in data["dog_problems"]


class TestFunnelStages:
    """Тесты воронки продаж"""
    
    def test_funnel_progression(self, store):
        # Начинаем с unknown
        assert store.get_funnel_stage("123") == "unknown"
        
        # После engage → engaged
        store.record_dm("123", "u", "U", "Привет", "Привет!", "engage")
        assert store.get_funnel_stage("123") == "engaged"
        
        # После help → helping
        store.record_dm("123", "u", "U", "Что посоветуете?", "Вот...", "help")
        assert store.get_funnel_stage("123") == "helping"
        
        # После soft_sell → soft_sold
        store.record_dm("123", "u", "U", "Интересно", "Могу отправить пробник", "soft_sell")
        assert store.get_funnel_stage("123") == "soft_sold"


class TestContextRetrieval:
    """Тесты получения контекста"""
    
    def test_group_context_for_user(self, store):
        store.record_group_message("123", "u", "U", "456", "Kinology", "Какой корм для щенка?")
        store.record_group_message("123", "u", "U", "456", "Kinology", "Щенок 4 месяца")
        
        context = store.get_group_context_for_user("123")
        
        assert "корм" in context.lower() or "щенок" in context.lower()
    
    def test_recent_messages_across_users(self, store):
        store.record_group_message("111", "u1", "U1", "456", "Chat", "Корм для овчарки")
        store.record_group_message("222", "u2", "U2", "456", "Chat", "Аллергия у лабрадора")
        store.record_group_message("111", "u1", "U1", "456", "Chat", "Попробуйте гипоаллергенный")
        
        context = store.get_recent_messages("456", limit=3)
        
        assert "овчарки" in context or "лабрадора" in context
    
    def test_different_chats_isolated(self, store):
        store.record_group_message("111", "u", "U", "AAA", "ChatA", "Message A")
        store.record_group_message("111", "u", "U", "BBB", "ChatB", "Message B")
        
        context_a = store.get_recent_messages("AAA")
        context_b = store.get_recent_messages("BBB")
        
        assert "Message A" in context_a
        assert "Message B" in context_b
        assert "Message A" not in context_b


class TestPersistence:
    """Тесты персистентности"""
    
    def test_data_survives_reload(self, tmp_path):
        mem_dir = str(tmp_path / "memory")
        
        store1 = UserMemoryStore(memory_dir=mem_dir, persona_name="kormoved")
        store1.record_group_message("123", "u", "U", "456", "Chat", "Овчарка")
        
        # Новый экземпляр — должен подхватить данные
        store2 = UserMemoryStore(memory_dir=mem_dir, persona_name="kormoved")
        data = store2._load("123")
        
        assert data["dog_breed"] == "Немецкая овчарка"
    
    def test_corrupted_file_handled(self, tmp_path):
        mem_dir = str(tmp_path / "memory")
        os.makedirs(mem_dir)
        
        # Пишем битый JSON
        with open(os.path.join(mem_dir, "999.json"), "w") as f:
            f.write("{broken json")
        
        store = UserMemoryStore(memory_dir=mem_dir, persona_name="kormoved")
        data = store._load("999")  # Не должен упасть
        
        # Должен создать нового юзера
        assert "user_id" in data
