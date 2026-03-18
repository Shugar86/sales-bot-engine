"""
Message Deduplication — не отвечать на одно сообщение дважды
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Set

from .logger import get_logger

logger = get_logger("dedup")


@dataclass
class DeduplicationStore:
    """
    Хранилище для дедупликации сообщений.
    
    Отслеживает какие сообщения уже обработаны,
    чтобы не отвечать на одно и то же дважды.
    """
    
    storage_path: str = "data/memory/processed_messages.json"
    max_age_hours: int = 48  # Хранить записи 48 часов
    
    # Внутреннее: message_hash -> timestamp
    _processed: dict = None
    
    def __post_init__(self):
        if self._processed is None:
            self._processed = {}
            self._load()
    
    def _load(self):
        """Загрузить из файла"""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r") as f:
                    self._processed = json.load(f)
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
                json.dump(self._processed, f)
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
    
    def get_stats(self) -> dict:
        """Статистика"""
        return {
            "total_tracked": len(self._processed),
            "storage": self.storage_path,
        }
