"""Tests for enhanced deduplication and conversation tracking."""
import pytest
import time
import tempfile
import os

from src.utils.dedup import DeduplicationStore


class TestBasicDedup:
    """Test basic message deduplication."""
    
    def test_message_not_processed_initially(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        assert store.is_processed("chat1", 1, "hello") is False
    
    def test_message_processed_after_mark(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.mark_processed("chat1", 1, "hello")
        assert store.is_processed("chat1", 1, "hello") is True
    
    def test_different_message_not_processed(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.mark_processed("chat1", 1, "hello")
        assert store.is_processed("chat1", 2, "world") is False


class TestChatActivityTracking:
    """Test per-chat activity tracking."""
    
    def test_no_activity_initially(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        assert store.last_bot_response_time("chat1") is None
        assert store.seconds_since_last_response("chat1") is None
    
    def test_record_response(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.record_bot_response("chat1", "Hello there!")
        
        last_time = store.last_bot_response_time("chat1")
        assert last_time is not None
        assert last_time <= time.time()
    
    def test_seconds_since_last(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.record_bot_response("chat1")
        
        time.sleep(0.1)
        
        elapsed = store.seconds_since_last_response("chat1")
        assert elapsed is not None
        assert elapsed >= 0.1
    
    def test_activity_survives_reload(self, tmp_path):
        path = str(tmp_path / "dedup.json")
        
        store1 = DeduplicationStore(storage_path=path)
        store1.record_bot_response("chat1", "Test response")
        
        store2 = DeduplicationStore(storage_path=path)
        assert store2.last_bot_response_time("chat1") is not None


class TestResponseRepetition:
    """Test response repetition detection."""
    
    def test_exact_match_detected(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.record_bot_response("chat1", "Привет, чем могу помочь?")
        
        assert store.is_repeating_response("chat1", "Привет, чем могу помочь?") is True
    
    def test_different_response_ok(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.record_bot_response("chat1", "Привет, чем могу помочь?")
        
        assert store.is_repeating_response("chat1", "Расскажи про своего пса") is False
    
    def test_highly_similar_detected(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.record_bot_response("chat1", "Корм для собак с аллергией очень важен для здоровья")
        
        # Very similar wording
        assert store.is_repeating_response(
            "chat1",
            "Корм для собак с аллергией важен для здоровья"
        ) is True
    
    def test_different_chat_ok(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.record_bot_response("chat1", "Привет!")
        
        # Same text but different chat is OK
        assert store.is_repeating_response("chat2", "Привет!") is False
    
    def test_no_responses_is_not_repeat(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        assert store.is_repeating_response("chat1", "Любой текст") is False


class TestDedupStats:
    """Test dedup statistics."""
    
    def test_stats_structure(self, tmp_path):
        store = DeduplicationStore(storage_path=str(tmp_path / "dedup.json"))
        store.mark_processed("chat1", 1, "hello")
        store.record_bot_response("chat1", "hello back")
        
        stats = store.get_stats()
        assert stats["total_tracked"] == 1
        assert stats["chats_active"] == 1
        assert stats["chats_with_responses"] == 1
