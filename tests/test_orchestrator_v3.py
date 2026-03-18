"""Tests for Orchestrator v3 Pipeline — integration tests."""
import pytest
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.context_reader import ContextReader, ChatContext
from src.core.vibe_checker import VibeChecker, VibeCheck
from src.core.decision_gate import DecisionGate, Decision
from src.core.memory_writer import MemoryWriter


# Persona config for testing
TEST_PERSONA = {
    "name": "TestBot",
    "vibe": {
        "role": "Тестовый бот",
        "taboos": ["политика"],
    },
    "triggers": {
        "respond_when": [
            {"keywords": ["корм", "собака", "тест"],
             "topics": ["кормление собак"]},
        ],
        "ignore_when": [
            {"contains": ["спам"]},
        ],
    },
    "behavior": {
        "always": "Болтай как человек",
    },
    "anti_spam": {
        "min_delay_between_messages": 5,
        "max_delay_between_messages": 30,
        "leave_on_read": 0.0,
        "emoji_reaction": 0.0,
    },
}


class TestV3PipelineContextToDecision:
    """Test: message → context → vibe → decision pipeline."""
    
    def test_full_pipeline_respond(self, tmp_path):
        """Dog question → should respond."""
        reader = ContextReader(my_user_id="bot123")
        checker = VibeChecker(TEST_PERSONA)
        gate = DecisionGate(TEST_PERSONA["anti_spam"])
        
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "alice",
             "display_name": "Alice", "text": "Собака не ест корм",
             "timestamp": 100},
        ]
        
        context = reader.read_context(messages)
        vibe = checker.check(context, "Собака не ест корм")
        decision = gate.decide(vibe, context, is_dm=False)
        
        assert context.topic == "dogs_food"
        assert vibe.should_respond is True
        assert decision.action in ["respond", "react"]
    
    def test_full_pipeline_ignore(self, tmp_path):
        """Random topic → should not respond."""
        reader = ContextReader()
        checker = VibeChecker(TEST_PERSONA)
        gate = DecisionGate(TEST_PERSONA["anti_spam"])
        
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a",
             "display_name": "A", "text": "Погода сегодня отличная",
             "timestamp": 100},
        ]
        
        context = reader.read_context(messages)
        vibe = checker.check(context, "Погода сегодня отличная")
        decision = gate.decide(vibe, context, is_dm=False)
        
        assert vibe.should_respond is False
        assert decision.action == "leave_read"
    
    def test_full_pipeline_dm(self, tmp_path):
        """DM → always respond."""
        reader = ContextReader()
        checker = VibeChecker(TEST_PERSONA)
        gate = DecisionGate(TEST_PERSONA["anti_spam"])
        
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a",
             "display_name": "A", "text": "Что посоветуешь?",
             "timestamp": 100},
        ]
        
        context = reader.read_context(messages)
        vibe = checker.check(context, "Что посоветуешь?")
        decision = gate.decide(vibe, context, is_dm=True)
        
        assert decision.action == "respond"
    
    def test_full_pipeline_taboo(self, tmp_path):
        """Politics → taboo deflection."""
        reader = ContextReader()
        checker = VibeChecker(TEST_PERSONA)
        gate = DecisionGate(TEST_PERSONA["anti_spam"])
        
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a",
             "display_name": "A", "text": "Что думаешь про политику?",
             "timestamp": 100},
        ]
        
        context = reader.read_context(messages)
        vibe = checker.check(context, "Что думаешь про политику?")
        decision = gate.decide(vibe, context, is_dm=False)
        
        assert vibe.match_type == "taboo"
        assert decision.action in ["respond", "react"]
    
    def test_full_pipeline_disengage(self, tmp_path):
        """Go away → disengage."""
        reader = ContextReader()
        checker = VibeChecker(TEST_PERSONA)
        gate = DecisionGate(TEST_PERSONA["anti_spam"])
        
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a",
             "display_name": "A", "text": "Отстань от меня!",
             "timestamp": 100},
        ]
        
        context = reader.read_context(messages)
        vibe = checker.check(context, "Отстань от меня!")
        decision = gate.decide(vibe, context, is_dm=False)
        
        assert decision.action == "disengage"


class TestV3PipelineWithMemory:
    """Test pipeline with memory integration."""
    
    def test_memory_persists_interaction(self, tmp_path):
        """Interaction should be saved to memory."""
        writer = MemoryWriter(memory_dir=str(tmp_path))
        
        writer.write_group_interaction(
            user_id="u1",
            username="alice",
            display_name="Alice",
            chat_id="-1001",
            chat_title="Dog Chat",
            user_message="Собака не ест",
            bot_response="Попробуй убрать курицу",
            topic="dogs_food",
        )
        
        profile = writer.get_user_profile("u1")
        assert profile.interaction_count == 1
        assert profile.display_name == "Alice"
    
    def test_dm_remembers_source_chat(self, tmp_path):
        """DM should remember which chat the user came from."""
        writer = MemoryWriter(memory_dir=str(tmp_path))
        
        # First interaction in group
        writer.write_group_interaction(
            user_id="u1", username="alice", display_name="Alice",
            chat_id="-1001", chat_title="Rostov Dog Chat",
            user_message="Собака не ест", bot_response="Попробуй...",
        )
        
        # Then DM
        writer.write_dm_interaction(
            user_id="u1", username="alice", display_name="Alice",
            user_message="Привет, напиши подробнее",
            bot_response="Здарова! Так, собака не ест...",
            source_chat_id="-1001",
            source_chat_title="Rostov Dog Chat",
        )
        
        profile = writer.get_user_profile("u1")
        assert profile.source_chat_title == "Rostov Dog Chat"
        ctx = writer.get_user_context_for_prompt("u1")
        assert "Rostov Dog Chat" in ctx
    
    def test_funnel_progression(self, tmp_path):
        """Funnel should progress through stages."""
        writer = MemoryWriter(memory_dir=str(tmp_path))
        
        stages = ["unknown", "interested", "asking", "ready_to_buy"]
        for stage in stages:
            writer.update_funnel("u1", stage)
        
        profile = writer.get_user_profile("u1")
        assert profile.funnel_stage == "ready_to_buy"
    
    def test_personal_details_remembered(self, tmp_path):
        """Personal details should persist."""
        writer = MemoryWriter(memory_dir=str(tmp_path))
        
        # Need at least one interaction for context to show
        writer.write_group_interaction(
            user_id="u1", username="alice", display_name="Alice",
            chat_id="-1001", chat_title="Chat",
            user_message="msg", bot_response="resp",
        )
        
        writer.remember_detail("u1", "dog_breed", "Овчарка")
        writer.remember_detail("u1", "dog_name", "Барон")
        writer.remember_detail("u1", "dog_age", "3 года")
        
        ctx = writer.get_user_context_for_prompt("u1")
        assert "Овчарка" in ctx
        assert "Барон" in ctx
    
    def test_context_for_prompt_first_contact(self, tmp_path):
        """First contact should say 'первый контакт'."""
        writer = MemoryWriter(memory_dir=str(tmp_path))
        ctx = writer.get_user_context_for_prompt("unknown_user")
        assert "первый контакт" in ctx


class TestV3PipelineChatContext:
    """Test chat context building."""
    
    def test_topic_from_messages(self):
        reader = ContextReader()
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "Собака не ест корм", "timestamp": 100},
            {"message_id": 2, "user_id": "u2", "username": "b", "display_name": "B",
             "text": "У меня тоже проблема с кормом", "timestamp": 200},
        ]
        ctx = reader.read_context(messages)
        assert ctx.topic == "dogs_food"
    
    def test_vibe_from_messages(self):
        reader = ContextReader()
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "😂😂😂 хаха прикол", "timestamp": 100},
        ]
        ctx = reader.read_context(messages)
        assert ctx.vibe == "funny"
    
    def test_directed_at_me_detection(self):
        reader = ContextReader(my_user_id="bot123")
        messages = [
            {"message_id": 1, "user_id": "bot123", "username": "bot", "display_name": "Bot",
             "text": "Мой ответ", "timestamp": 100},
            {"message_id": 2, "user_id": "u1", "username": "a", "display_name": "A",
             "text": "А ты что думаешь?", "timestamp": 200,
             "reply_to_message_id": 1},
        ]
        ctx = reader.read_context(messages)
        assert ctx.is_directed_at_me is True
    
    def test_participants_tracking(self):
        reader = ContextReader()
        messages = [
            {"message_id": i, "user_id": f"u{i}", "username": f"user{i}",
             "display_name": f"User {i}", "text": f"msg {i}", "timestamp": i * 100}
            for i in range(5)
        ]
        ctx = reader.read_context(messages)
        assert ctx.unique_participants == 5


class TestV3PipelineEndToEnd:
    """End-to-end pipeline tests."""
    
    def test_dog_food_question_end_to_end(self, tmp_path):
        """Complete flow: dog food question → respond with personal experience angle."""
        reader = ContextReader(my_user_id="bot")
        checker = VibeChecker(TEST_PERSONA)
        gate = DecisionGate({"leave_on_read": 0.0, "emoji_reaction": 0.0,
                              "min_delay_between_messages": 0, "max_delay_between_messages": 0})
        writer = MemoryWriter(memory_dir=str(tmp_path))
        
        # Incoming message
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "alice",
             "display_name": "Alice", "text": "Собака не ест уже 3 дня, что делать?",
             "timestamp": 100},
        ]
        
        # Pipeline
        context = reader.read_context(messages)
        vibe = checker.check(context, "Собака не ест уже 3 дня, что делать?")
        decision = gate.decide(vibe, context, is_dm=False)
        
        # Assert
        assert context.topic == "dogs_food"
        assert vibe.should_respond is True
        assert vibe.suggested_angle in ["personal_experience", "advice"]
        assert decision.action == "respond"
        
        # Save to memory
        writer.write_group_interaction(
            user_id="u1", username="alice", display_name="Alice",
            chat_id="-1001", chat_title="Dog Chat",
            user_message="Собака не ест уже 3 дня",
            bot_response="Попробуй убрать курицу",
            topic="dogs_food",
        )
        
        profile = writer.get_user_profile("u1")
        assert profile.interaction_count == 1
    
    def test_dm_from_group_member_end_to_end(self, tmp_path):
        """DM from group member → remember source chat."""
        writer = MemoryWriter(memory_dir=str(tmp_path))
        reader = ContextReader()
        checker = VibeChecker(TEST_PERSONA)
        gate = DecisionGate({"leave_on_read": 0.0, "emoji_reaction": 0.0,
                              "min_delay_between_messages": 0, "max_delay_between_messages": 0})
        
        # First: group interaction
        writer.write_group_interaction(
            user_id="u1", username="alice", display_name="Alice",
            chat_id="-1001", chat_title="Rostov Dog Chat",
            user_message="Собака не ест", bot_response="Попробуй...",
        )
        
        # Then: DM
        messages = [
            {"message_id": 1, "user_id": "u1", "username": "alice",
             "display_name": "Alice", "text": "Привет, ты из чата собаководов?",
             "timestamp": 200},
        ]
        
        context = reader.read_context(messages)
        vibe = checker.check(context, "Привет, ты из чата собаководов?")
        decision = gate.decide(vibe, context, is_dm=True)
        
        assert decision.action == "respond"
        
        writer.write_dm_interaction(
            user_id="u1", username="alice", display_name="Alice",
            user_message="Привет, ты из чата собаководов?",
            bot_response="Ага, из Ростовского!",
            source_chat_id="-1001",
            source_chat_title="Rostov Dog Chat",
        )
        
        profile = writer.get_user_profile("u1")
        assert profile.source_chat_title == "Rostov Dog Chat"
        ctx = writer.get_user_context_for_prompt("u1")
        assert "Rostov" in ctx
