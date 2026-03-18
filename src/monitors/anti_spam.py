"""
Anti-Spam — rate limiter + random delays + per-chat throttling + time-aware activity
"""

import asyncio
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("antispam")


@dataclass
class RateLimiter:
    """
    Rate limiter с per-chat и глобальными лимитами.
    Human-like delays: 30-300s (not 180-900s).
    Time-of-day awareness: quieter at night.
    Leave-on-read: sometimes just don't respond.
    """
    
    # Настройки
    min_delay_sec: float = 30.0      # was 5.0 → now human-realistic minimum
    max_delay_sec: float = 300.0     # was 30.0 → now 5min max (human in active chat)
    max_global_per_hour: int = 10
    max_per_chat_per_hour: int = 3
    cooldown_sec: float = 30.0       # was 60.0 → less aggressive cooldown
    
    # Time-of-day settings (GMT, persona can override)
    active_hours_start: int = 8      # 8 AM
    active_hours_end: int = 23       # 11 PM
    night_delay_multiplier: float = 3.0  # 3x slower at night
    
    # Leave-on-read probability (30-40% of messages)
    leave_on_read_probability: float = 0.35
    
    # Emoji reaction probability (instead of text response)
    emoji_reaction_probability: float = 0.15
    
    # Внутреннее состояние
    _send_times: List[float] = field(default_factory=list)
    _chat_send_times: Dict[str, List[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _last_response_per_chat: Dict[str, float] = field(default_factory=dict)
    
    def _is_active_hours(self, hour: Optional[int] = None) -> bool:
        """Check if current time is within active hours."""
        if hour is None:
            hour = datetime.now().hour
        return self.active_hours_start <= hour < self.active_hours_end
    
    def should_leave_on_read(self) -> bool:
        """
        Decide if we should 'read but not respond'.
        Real humans don't answer ~35% of messages they read.
        """
        return random.random() < self.leave_on_read_probability
    
    def should_use_emoji_reaction(self) -> bool:
        """
        Decide if we should react with emoji instead of text.
        Sometimes 👍 is more natural than a paragraph.
        """
        return random.random() < self.emoji_reaction_probability
    
    def get_emoji_reaction(self, message_text: str = "") -> Optional[str]:
        """Pick an appropriate emoji reaction."""
        text_lower = message_text.lower()
        
        # Context-aware reactions
        if any(w in text_lower for w in ["спасибо", "благодарю", "thanks"]):
            return random.choice(["❤️", "👍", "😊"])
        if any(w in text_lower for w in ["согласен", "поддерживаю", "да", "точно"]):
            return random.choice(["👍", "💪", "🤝"])
        if any(w in text_lower for w in ["смешно", "хаха", "😂", "ахах"]):
            return random.choice(["😂", "🤣", "😄"])
        if any(w in text_lower for w in ["вопрос", "подскажите", "помогите"]):
            return random.choice(["🤔", "👀"])
        
        # Generic positive
        return random.choice(["👍", "❤️", "🔥", "💪"])
    
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
        """
        Get a human-like random delay.
        Time-aware: slower at night, faster during active hours.
        Variable: sometimes quick (30s), sometimes slow (5min).
        Result is always clamped to [min_delay_sec, max_delay_sec * night_multiplier].
        """
        base_delay = random.uniform(self.min_delay_sec, self.max_delay_sec)
        
        # Night mode: multiply delay (but clamp to max)
        if not self._is_active_hours():
            night_max = self.max_delay_sec * self.night_delay_multiplier
            base_delay = min(base_delay * self.night_delay_multiplier, night_max)
            logger.debug(f"Night mode: delay={base_delay:.0f}s")
        
        # Add occasional "thinking" pauses (10% chance)
        # But keep within reasonable bounds
        if random.random() < 0.10:
            thinking_pause = random.uniform(
                self.min_delay_sec * 0.5,
                self.min_delay_sec * 2.0,
            )
            base_delay = min(base_delay + thinking_pause, self.max_delay_sec * 3)
            logger.debug(f"Thinking pause: +{thinking_pause:.0f}s")
        
        return base_delay
    
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
            "active_hours": self._is_active_hours(),
            "leave_on_read_pct": self.leave_on_read_probability,
            "emoji_reaction_pct": self.emoji_reaction_probability,
        }
