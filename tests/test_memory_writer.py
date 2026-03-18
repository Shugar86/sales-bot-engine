"""Tests for Memory Writer — v3 core module."""
import pytest
import os
import json
import time
import tempfile
from src.core.memory_writer import MemoryWriter, UserMemoryProfile


@pytest.fixture
def memory_dir(tmp_path):
    return str(tmp_path / "memory")


@pytest.fixture
def writer(memory_dir):
    return MemoryWriter(memory_dir=memory_dir)


class TestMemoryWriterBasic:
    """Basic memory writer tests."""
    
    def test_creates_directory(self, memory_dir):
        MemoryWriter(memory_dir=memory_dir)
        assert os.path.exists(memory_dir)
    
    def test_write_group_interaction(self, writer):
        writer.write_group_interaction(
            user_id="user1",
            username="alice",
            display_name="Alice",
            chat_id="-1001",
            chat_title="Dog Chat",
            user_message="Собака не ест",
            bot_response="Попробуй убрать курицу",
            topic="dogs_food",
        )
        
        profile = writer.get_user_profile("user1")
        assert profile.username == "alice"
        assert profile.display_name == "Alice"
        assert profile.interaction_count == 1
        assert "-1001" in profile.group_chats
    
    def test_write_dm_interaction(self, writer):
        writer.write_dm_interaction(
            user_id="user1",
            username="alice",
            display_name="Alice",
            user_message="Привет!",
            bot_response="Здарова!",
            source_chat_id="-1001",
            source_chat_title="Dog Chat",
            funnel_stage="engaged",
        )
        
        profile = writer.get_user_profile("user1")
        assert profile.has_dm is True
        assert profile.source_chat_id == "-1001"
        assert profile.source_chat_title == "Dog Chat"
        assert profile.funnel_stage == "engaged"
    
    def test_write_reaction(self, writer):
        writer.write_reaction("user1", "-1001", "👍")
        
        profile = writer.get_user_profile("user1")
        assert profile.interaction_count >= 1
    
    def test_multiple_interactions_count(self, writer):
        writer.write_group_interaction(
            user_id="user1", username="a", display_name="A",
            chat_id="-1001", chat_title="Chat",
            user_message="msg1", bot_response="resp1",
        )
        writer.write_group_interaction(
            user_id="user1", username="a", display_name="A",
            chat_id="-1001", chat_title="Chat",
            user_message="msg2", bot_response="resp2",
        )
        
        profile = writer.get_user_profile("user1")
        assert profile.interaction_count == 2


class TestMemoryWriterDetails:
    """Tests for personal details and notes."""
    
    def test_remember_detail(self, writer):
        writer.remember_detail("user1", "dog_breed", "Овчарка")
        writer.remember_detail("user1", "dog_name", "Барон")
        
        profile = writer.get_user_profile("user1")
        assert profile.personal_details.get("dog_breed") == "Овчарка"
        assert profile.personal_details.get("dog_name") == "Барон"
    
    def test_add_note(self, writer):
        writer.add_note("user1", "Интересуется кормом для щенков")
        writer.add_note("user1", "Упомянул аллергию на курицу")
        
        profile = writer.get_user_profile("user1")
        assert len(profile.notes) == 2
        assert "курицу" in profile.notes[1]
    
    def test_notes_max_limit(self, writer):
        for i in range(25):
            writer.add_note("user1", f"Note {i}")
        
        profile = writer.get_user_profile("user1")
        assert len(profile.notes) <= 20


class TestMemoryWriterFunnel:
    """Tests for funnel tracking."""
    
    def test_update_funnel(self, writer):
        writer.update_funnel("user1", "interested")
        profile = writer.get_user_profile("user1")
        assert profile.funnel_stage == "interested"
    
    def test_funnel_progression(self, writer):
        writer.update_funnel("user1", "unknown")
        writer.update_funnel("user1", "interested")
        writer.update_funnel("user1", "asking")
        writer.update_funnel("user1", "ready_to_buy")
        
        profile = writer.get_user_profile("user1")
        assert profile.funnel_stage == "ready_to_buy"
    
    def test_dm_sets_funnel(self, writer):
        writer.write_dm_interaction(
            user_id="user1", username="a", display_name="A",
            user_message="Хочу заказать", bot_response="Отлично!",
            funnel_stage="ready_to_buy",
        )
        
        profile = writer.get_user_profile("user1")
        assert profile.funnel_stage == "ready_to_buy"


class TestMemoryWriterSourceChat:
    """Tests for source chat tracking (DM origin)."""
    
    def test_source_chat_set_once(self, writer):
        writer.write_dm_interaction(
            user_id="user1", username="a", display_name="A",
            user_message="Привет", bot_response="Здарова!",
            source_chat_id="-1001", source_chat_title="Dog Chat Rostov",
        )
        
        profile = writer.get_user_profile("user1")
        assert profile.source_chat_id == "-1001"
        assert profile.source_chat_title == "Dog Chat Rostov"
    
    def test_source_chat_not_overwritten(self, writer):
        writer.write_dm_interaction(
            user_id="user1", username="a", display_name="A",
            user_message="msg1", bot_response="resp1",
            source_chat_id="-1001", source_chat_title="First Chat",
        )
        writer.write_dm_interaction(
            user_id="user1", username="a", display_name="A",
            user_message="msg2", bot_response="resp2",
            source_chat_id="-1002", source_chat_title="Second Chat",
        )
        
        profile = writer.get_user_profile("user1")
        assert profile.source_chat_id == "-1001"  # Not overwritten


class TestMemoryWriterProfile:
    """Tests for profile retrieval."""
    
    def test_new_user_profile(self, writer):
        profile = writer.get_user_profile("unknown_user")
        assert profile.user_id == "unknown_user"
        assert profile.interaction_count == 0
        assert profile.funnel_stage == "unknown"
    
    def test_context_for_prompt_empty(self, writer):
        ctx = writer.get_user_context_for_prompt("unknown")
        assert "первый контакт" in ctx
    
    def test_context_for_prompt_with_data(self, writer):
        writer.write_group_interaction(
            user_id="user1", username="alice", display_name="Alice",
            chat_id="-1001", chat_title="Dog Chat",
            user_message="Собака не ест", bot_response="Попробуй...",
            topic="dogs_food",
        )
        writer.remember_detail("user1", "dog_breed", "Лабрадор")
        
        ctx = writer.get_user_context_for_prompt("user1")
        assert "Alice" in ctx
        assert "Лабрадор" in ctx
    
    def test_last_interaction_summary(self, writer):
        writer.write_group_interaction(
            user_id="user1", username="a", display_name="A",
            chat_id="-1001", chat_title="Chat",
            user_message="Корм для собаки", bot_response="Попробуй...",
        )
        
        summary = writer.get_last_interaction_summary("user1")
        assert "Корм" in summary


class TestMemoryWriterPersistence:
    """Tests for data persistence."""
    
    def test_data_persists_across_instances(self, memory_dir):
        writer1 = MemoryWriter(memory_dir=memory_dir)
        writer1.remember_detail("user1", "dog_breed", "Хаски")
        
        # New instance — should load from disk
        writer2 = MemoryWriter(memory_dir=memory_dir)
        profile = writer2.get_user_profile("user1")
        assert profile.personal_details.get("dog_breed") == "Хаски"
    
    def test_json_file_created(self, writer):
        writer.write_group_interaction(
            user_id="user1", username="a", display_name="A",
            chat_id="-1001", chat_title="Chat",
            user_message="msg", bot_response="resp",
        )
        
        path = os.path.join(writer.memory_dir, "user1.json")
        assert os.path.exists(path)
        
        with open(path, "r") as f:
            data = json.load(f)
        assert data["username"] == "a"


class TestMemoryWriterGetAllUsers:
    """Tests for get_all_users."""
    
    def test_empty_directory(self, writer):
        users = writer.get_all_users()
        assert users == []
    
    def test_get_all_users(self, writer):
        writer.write_group_interaction(
            user_id="u1", username="a", display_name="A",
            chat_id="-1001", chat_title="Chat",
            user_message="msg", bot_response="resp",
        )
        writer.write_group_interaction(
            user_id="u2", username="b", display_name="B",
            chat_id="-1001", chat_title="Chat",
            user_message="msg", bot_response="resp",
        )
        
        users = writer.get_all_users()
        assert len(users) == 2
    
    def test_filter_by_funnel_stage(self, writer):
        writer.update_funnel("u1", "interested")
        writer.update_funnel("u2", "ready_to_buy")
        
        interested = writer.get_all_users(funnel_stage="interested")
        assert len(interested) == 1
        assert interested[0]["user_id"] == "u1"
