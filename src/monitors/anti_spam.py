"""
Anti-Spam — rate limiter + random delays + per-chat throttling
"""

import asyncio
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

from ..utils.logger import get_logger

logger = get_logger("antispam")


@dataclass
class RateLimiter:
    """
    Rate limiter с per-chat и глобальными лимитами.
    
    Отслеживает когда и кому отправляли сообщения,
    блокирует если превышен лимит.
    """
    
    # Настройки
    min_delay_sec: float = 5.0
    max_delay_sec: float = 30.0
    max_global_per_hour: int = 10
    max_per_chat_per_hour: int = 3
    cooldown_sec: float = 60.0
    
    # Внутреннее состояние
    _send_times: List[float] = field(default_factory=list)
    _chat_send_times: Dict[str, List[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _last_response_per_chat: Dict[str, float] = field(default_factory=dict)
    
    def can_send(self, chat_id: str) -> tuple[bool, str]:
        """
        Проверить можно ли отправить сообщение.
        
        Returns:
            (can_send: bool, reason: str)
        """
        now = time.monotonic()
        hour_ago = now - 3600
        
        # Очистка старых записей
        self._send_times = [t for t in self._send_times if t > hour_ago]
        self._chat_send_times[chat_id] = [
            t for t in self._chat_send_times.get(chat_id, []) if t > hour_ago
        ]
        
        # Глобальный лимит
        if len(self._send_times) >= self.max_global_per_hour:
            return False, f"Global rate limit: {len(self._send_times)}/{self.max_global_per_hour} per hour"
        
        # Per-chat лимит
        chat_times = self._chat_send_times.get(chat_id, [])
        if len(chat_times) >= self.max_per_chat_per_hour:
            return False, f"Chat rate limit: {len(chat_times)}/{self.max_per_chat_per_hour} per hour for {chat_id}"
        
        # Cooldown после последнего ответа в этом чате
        last = self._last_response_per_chat.get(chat_id, 0)
        elapsed = now - last
        if last > 0 and elapsed < self.cooldown_sec:
            return False, f"Cooldown: {self.cooldown_sec - elapsed:.0f}s remaining"
        
        return True, "OK"
    
    def record_send(self, chat_id: str):
        """Записать факт отправки"""
        now = time.monotonic()
        self._send_times.append(now)
        self._chat_send_times[chat_id].append(now)
        self._last_response_per_chat[chat_id] = now
    
    def get_random_delay(self) -> float:
        """Получить случайную задержку перед отправкой"""
        return random.uniform(self.min_delay_sec, self.max_delay_sec)
    
    async def wait_and_send(self, chat_id: str, send_func, *args, **kwargs):
        """
        Подождать случайную задержку, проверить лимиты, отправить.
        
        Args:
            chat_id: ID чата
            send_func: Функция отправки (async)
            *args, **kwargs: Аргументы для send_func
        
        Returns:
            True если отправлено, False если заблокировано
        """
        # Проверяем лимиты
        can_send, reason = self.can_send(chat_id)
        if not can_send:
            logger.warning(f"Blocked send to {chat_id}: {reason}")
            return False
        
        # Случайная задержка
        delay = self.get_random_delay()
        logger.debug(f"Anti-spam delay: {delay:.1f}s before sending to {chat_id}")
        await asyncio.sleep(delay)
        
        # Повторная проверка после задержки
        can_send, reason = self.can_send(chat_id)
        if not can_send:
            logger.warning(f"Blocked after delay to {chat_id}: {reason}")
            return False
        
        # Отправляем
        try:
            await send_func(*args, **kwargs)
            self.record_send(chat_id)
            logger.info(f"Sent to {chat_id} (global: {len(self._send_times)}/hr, chat: {len(self._chat_send_times.get(chat_id, []))}/hr)")
            return True
        except Exception as e:
            logger.error(f"Send failed to {chat_id}: {e}")
            return False
    
    def get_stats(self) -> dict:
        """Получить статистику"""
        now = time.monotonic()
        hour_ago = now - 3600
        
        global_count = len([t for t in self._send_times if t > hour_ago])
        chat_counts = {
            chat_id: len([t for t in times if t > hour_ago])
            for chat_id, times in self._chat_send_times.items()
        }
        
        return {
            "global_per_hour": global_count,
            "max_global_per_hour": self.max_global_per_hour,
            "chats": chat_counts,
        }
