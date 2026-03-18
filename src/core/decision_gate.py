"""
Decision Gate — финальное решение: respond / react / leave-on-read / disengage / wait.

Принимает решение на основе:
- Результата vibe_checker (стоит ли отвечать?)
- Контекста чата (направлено ли на меня?)
- Конфигурации anti_spam (вероятность leave-on-read, emoji reaction)
- Времени суток (night slowdown)
- Сколько недавно писал (anti-spam)

Философия:
- Реальный человек не отвечает на каждое сообщение
- 35% сообщений — просто прочитать и промолчать
- 15% — ответить эмодзи вместо текста
- Ночью — в 3 раза медленнее
"""

import random
import time
from dataclasses import dataclass
from typing import Optional

from .context_reader import ChatContext
from .vibe_checker import VibeCheck


@dataclass
class Decision:
    """
    Финальное решение о том, как реагировать.
    """
    action: str            # "respond" | "react" | "leave_read" | "disengage" | "wait"
    delay_seconds: int     # когда действовать
    emoji: Optional[str] = None   # если react, какой эмодзи
    reply_to: Optional[int] = None  # если respond, ответить на какое сообщение
    reason: str = ""       # почему такое решение
    confidence: float = 0.0  # уверенность в решении


# Эмодзи для реакций (когда вместо текста — просто эмодзи)
REACTION_EMOJIS = {
    "agree": ["👍", "✅", "💯"],
    "funny": ["😂", "🤣", "😅"],
    "love": ["❤️", "❤", "🥰"],
    "wow": ["😮", "😲", "🔥"],
    "sad": ["😢", "😔", "💪"],
    "think": ["🤔", "💭", "🧐"],
    "ok": ["👌", "✌️", "🙃"],
}

# Общие триггеры для "отстань"
DISENGAGE_PATTERNS = [
    "отстань", "отвали", "не пиши", "хватит", "прекрати",
    "заткнись", "замолчи", "уйди", "не надо", "стоп",
    "перестань", "хватит уже", "оставь меня", "не пиши мне",
]


class DecisionGate:
    """
    Финальное решение: respond / react / leave_on_read / disengage / wait.
    
    Учитывает:
    - Vibe check: стоит ли вообще отвечать?
    - Anti-spam: может быть просто прочитать (leave on read)?
    - Emoji: может быть ответить эмодзи вместо текста?
    - Время суток: ночью медленнее
    - Anti-spam: лимиты на количество сообщений
    """
    
    def __init__(self, anti_spam_config: dict = None):
        """
        Args:
            anti_spam_config: Конфигурация anti_spam из persona YAML.
        """
        config = anti_spam_config or {}
        
        self.min_delay = config.get("min_delay_between_messages", 30)
        self.max_delay = config.get("max_delay_between_messages", 300)
        self.leave_on_read_probability = config.get("leave_on_read", 0.35)
        self.emoji_reaction_probability = config.get("emoji_reaction", 0.15)
        self.night_slowdown = config.get("night_slowdown", 3.0)
        self.night_start = config.get("night_start", 23)
        self.night_end = config.get("night_end", 8)
    
    def decide(
        self,
        vibe_check: VibeCheck,
        context: ChatContext,
        is_dm: bool = False,
        messages_in_last_hour: int = 0,
        max_messages_per_hour: int = 3,
    ) -> Decision:
        """
        Принять решение о реакции.
        
        Args:
            vibe_check: Результат проверки vibe_checker
            context: Контекст чата
            is_dm: Это ЛС?
            messages_in_last_hour: Сколько сообщений отправлено за последний час
            max_messages_per_hour: Максимум сообщений в час
        
        Returns:
            Decision с действием.
        """
        last_msg = context.last_message
        last_text = last_msg.text.lower() if last_msg and last_msg.text else ""
        
        # === CHECK 1: DISENGAGE — человек просит отстать ===
        if any(p in last_text for p in DISENGAGE_PATTERNS):
            return Decision(
                action="disengage",
                delay_seconds=0,
                reason="Пользователь просит отстать",
                confidence=1.0,
            )
        
        # === CHECK 2: DM — всегда отвечаем (но с задержкой) ===
        if is_dm:
            delay = self._calculate_delay(is_dm=True)
            return Decision(
                action="respond",
                delay_seconds=delay,
                reply_to=last_msg.message_id if last_msg else None,
                reason="Личное сообщение — всегда отвечаем",
                confidence=0.95,
            )
        
        # === CHECK 3: Vibe check говорит НЕ отвечать ===
        if not vibe_check.should_respond:
            # Может быть просто прочитать?
            return Decision(
                action="leave_read",
                delay_seconds=0,
                reason=f"Vibe check: {vibe_check.reason}",
                confidence=vibe_check.confidence,
            )
        
        # === CHECK 4: Anti-spam — лимит сообщений в час ===
        if messages_in_last_hour >= max_messages_per_hour:
            return Decision(
                action="leave_read",
                delay_seconds=0,
                reason=f"Anti-spam: лимит {max_messages_per_hour}/час достигнут",
                confidence=0.9,
            )
        
        # === CHECK 5: Leave on read (35% вероятность) ===
        # Реальные люди не отвечают на каждое сообщение
        if random.random() < self.leave_on_read_probability:
            # Но если направлено на меня — отвечаем с большей вероятностью
            if not context.is_directed_at_me:
                return Decision(
                    action="leave_read",
                    delay_seconds=0,
                    reason="Вероятность leave-on-read (35%)",
                    confidence=0.8,
                )
        
        # === CHECK 6: Emoji reaction вместо текста (15%) ===
        if random.random() < self.emoji_reaction_probability:
            if not context.is_directed_at_me:  # Если на меня — лучше текст
                emoji = self._pick_emoji(last_text, context.vibe)
                return Decision(
                    action="react",
                    delay_seconds=random.randint(5, 30),
                    emoji=emoji,
                    reason="Эмодзи вместо текста (15% вероятность)",
                    confidence=0.7,
                )
        
        # === CHECK 7: RESPOND — с расчётом задержки ===
        delay = self._calculate_delay(is_dm=False)
        
        return Decision(
            action="respond",
            delay_seconds=delay,
            reply_to=last_msg.message_id if context.is_directed_at_me else None,
            reason=f"Vibe check OK: {vibe_check.reason}",
            confidence=vibe_check.confidence,
        )
    
    def _calculate_delay(self, is_dm: bool = False) -> int:
        """
        Рассчитать задержку перед ответом.
        
        Учитывает:
        - min/max delay из конфига
        - Ночное замедление
        - DM чуть быстрее
        """
        base_delay = random.randint(self.min_delay, self.max_delay)
        
        # Ночное замедление
        current_hour = time.localtime().tm_hour
        if current_hour >= self.night_start or current_hour < self.night_end:
            base_delay = int(base_delay * self.night_slowdown)
        
        # DM — чуть быстрее (люди ждут ответа в ЛС)
        if is_dm:
            base_delay = max(int(base_delay * 0.5), 10)
        
        return base_delay
    
    def _pick_emoji(self, text: str, vibe: str) -> str:
        """Выбрать подходящий эмодзи для реакции."""
        text_lower = text.lower()
        
        if any(w in text_lower for w in ["хаха", "😂", "смешно", "лол"]):
            return random.choice(REACTION_EMOJIS["funny"])
        elif any(w in text_lower for w in ["плохо", "грустн", "жаль"]):
            return random.choice(REACTION_EMOJIS["sad"])
        elif any(w in text_lower for w in ["согласен", "точно", "верно"]):
            return random.choice(REACTION_EMOJIS["agree"])
        elif "?" in text:
            return random.choice(REACTION_EMOJIS["think"])
        else:
            return random.choice(REACTION_EMOJIS["ok"])
    
    def should_wait(self, seconds_since_last: int) -> bool:
        """
        Проверить, нужно ли подождать (слишком часто отвечал).
        
        Args:
            seconds_since_last: Сколько секунд прошло с последнего сообщения
        
        Returns:
            True если нужно подождать.
        """
        return seconds_since_last < self.min_delay
