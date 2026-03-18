"""
Message Deduplication — не отвечать на одно сообщение дважды
+ Conversation tracking per chat
"""

import hashlib
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .logger import get_logger

logger = get_logger("dedup")


@dataclass
class DeduplicationStore:
    """
    Хранилище для дедупликации сообщений + отслеживание разговоров.
    
    Отслеживает какие сообщения уже обработаны,
    чтобы не отвечать на одно и то же дважды.
    Также отслеживает активность бота в чатах.
    """
    
    storage_path: str = "data/memory/processed_messages.json"
    max_age_hours: int = 48  # Хранить записи 48 часов
    
    # Внутреннее: message_hash -> timestamp
    _processed: dict = None
    
    # Chat activity tracking: chat_id -> last_response_timestamp
    _chat_activity: Dict[str, float] = field(default_factory=dict)
    
    # Response text tracking: chat_id -> list of recent response texts (for avoiding repetition)
    _recent_responses: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    
    def __post_init__(self):
        if self._processed is None:
            self._processed = {}
            self._load()
    
    def _load(self):
        """Загрузить из файла"""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                self._processed = data.get("processed", {})
                self._chat_activity = data.get("chat_activity", {})
                self._recent_responses = defaultdict(list, data.get("recent_responses", {}))
                self._cleanup_old()
                logger.debug(f"Loaded {len(self._processed)} processed message hashes")
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to load dedup store: {e}")
                self._processed = {}
    
    def _save(self):
        """Сохранить в файл"""
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        try:
            with open(self.storage_path, "w") as f:
                json.dump({
                    "processed": self._processed,
                    "chat_activity": self._chat_activity,
                    "recent_responses": dict(self._recent_responses),
                }, f)
        except Exception as e:
            logger.error(f"Failed to save dedup store: {e}")
    
    def _cleanup_old(self):
        """Удалить старые записи"""
        cutoff = time.time() - (self.max_age_hours * 3600)
        old_keys = [k for k, v in self._processed.items() if v < cutoff]
        for k in old_keys:
            del self._processed[k]
        if old_keys:
            logger.debug(f"Cleaned up {len(old_keys)} old dedup entries")
    
    def _hash_message(self, chat_id: str, message_id: int, text: str) -> str:
        """Вычислить хеш сообщения"""
        content = f"{chat_id}:{message_id}:{text[:100]}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def is_processed(self, chat_id: str, message_id: int, text: str) -> bool:
        """Проверить обработано ли сообщение"""
        h = self._hash_message(chat_id, message_id, text)
        return h in self._processed
    
    def mark_processed(self, chat_id: str, message_id: int, text: str):
        """Отметить сообщение как обработанное"""
        h = self._hash_message(chat_id, message_id, text)
        self._processed[h] = time.time()
        self._cleanup_old()
        self._save()
    
    # === NEW: Chat activity tracking ===
    
    def record_bot_response(self, chat_id: str, response_text: str = ""):
        """Record that the bot responded in a chat."""
        now = time.time()
        self._chat_activity[chat_id] = now
        
        # Track recent responses (keep last 10)
        if response_text:
            self._recent_responses[chat_id].append(response_text[:200])
            self._recent_responses[chat_id] = self._recent_responses[chat_id][-10:]
        
        self._save()
    
    def last_bot_response_time(self, chat_id: str) -> Optional[float]:
        """When did the bot last respond in this chat?"""
        return self._chat_activity.get(chat_id)
    
    def seconds_since_last_response(self, chat_id: str) -> Optional[float]:
        """How many seconds since bot's last response in this chat."""
        last = self._chat_activity.get(chat_id)
        if last is None:
            return None
        return time.time() - last
    
    def is_repeating_response(self, chat_id: str, new_response: str, similarity_threshold: float = 0.8) -> bool:
        """
        Check if this response is too similar to recent ones in this chat.
        Prevents the bot from saying the same thing twice.
        """
        recent = self._recent_responses.get(chat_id, [])
        if not recent:
            return False
        
        new_lower = new_response.lower().strip()
        new_words = set(new_lower.split())
        
        for old_response in recent:
            old_lower = old_response.lower().strip()
            if new_lower == old_lower:
                return True
            
            # Word overlap similarity
            old_words = set(old_lower.split())
            if new_words and old_words:
                overlap = len(new_words & old_words) / max(len(new_words | old_words), 1)
                if overlap > similarity_threshold:
                    return True
        
        return False
    
    def get_recent_texts(self, chat_id: str, limit: int = 10) -> list[str]:
        """Get recent response texts for a chat (for vibe analysis)."""
        responses = self._recent_responses.get(chat_id, [])
        return responses[-limit:] if responses else []
    
    def get_stats(self) -> dict:
        """Статистика"""
        return {
            "total_tracked": len(self._processed),
            "chats_active": len(self._chat_activity),
            "chats_with_responses": len(self._recent_responses),
            "storage": self.storage_path,
        }
