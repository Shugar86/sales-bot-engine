"""Tests for DeduplicationStore."""
import pytest
import json
import os
import time

from src.utils.dedup import DeduplicationStore


class TestDeduplication:
    """Test message deduplication."""
    
    def test_new_message_not_processed(self, tmp_path):
        store = DeduplicationStore(
            storage_path=str(tmp_path / "dedup.json")
        )
        
        assert store.is_processed("chat1", 1, "Hello") is False
    
    def test_mark_then_processed(self, tmp_path):
        store = DeduplicationStore(
            storage_path=str(tmp_path / "dedup.json")
        )
        
        store.mark_processed("chat1", 1, "Hello")
        
        assert store.is_processed("chat1", 1, "Hello") is True
    
    def test_different_messages_not_duplicates(self, tmp_path):
        store = DeduplicationStore(
            storage_path=str(tmp_path / "dedup.json")
        )
        
        store.mark_processed("chat1", 1, "Hello")
        
        assert store.is_processed("chat1", 2, "World") is False
        assert store.is_processed("chat2", 1, "Hello") is False
    
    def test_same_text_different_chat_not_duplicate(self, tmp_path):
        store = DeduplicationStore(
            storage_path=str(tmp_path / "dedup.json")
        )
        
        store.mark_processed("chat1", 1, "Hello")
        
        assert store.is_processed("chat2", 1, "Hello") is False
    
    def test_persistence(self, tmp_path):
        path = str(tmp_path / "dedup.json")
        
        store1 = DeduplicationStore(storage_path=path)
        store1.mark_processed("chat1", 1, "Hello")
        
        # New instance should load from file
        store2 = DeduplicationStore(storage_path=path)
        assert store2.is_processed("chat1", 1, "Hello") is True
    
    def test_stats(self, tmp_path):
        store = DeduplicationStore(
            storage_path=str(tmp_path / "dedup.json")
        )
        
        store.mark_processed("chat1", 1, "A")
        store.mark_processed("chat1", 2, "B")
        
        stats = store.get_stats()
        assert stats["total_tracked"] == 2
    
    def test_creates_directory(self, tmp_path):
        path = str(tmp_path / "subdir" / "dedup.json")
        store = DeduplicationStore(storage_path=path)
        store.mark_processed("chat1", 1, "Test")
        
        assert os.path.exists(path)
