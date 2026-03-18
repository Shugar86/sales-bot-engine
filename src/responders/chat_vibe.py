"""
Chat Vibe Detector — определяет энергию/тон чата на основе последних сообщений.

Позволяет боту "подстраиваться" под тон разговора:
- Пьяный чат → пьяная энергия
- Серьёзный чат → серьёзные ответы
- Шуточный → юмор
- Агрессивный → деэскалация
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ChatVibe(Enum):
    """Detected chat energy/tone."""
    CASUAL = "casual"          # Обычная болтовня
    SERIOUS = "serious"        # Серьёзное обсуждение
    FUNNY = "funny"            # Шутки, смех, мемы
    DRUNK = "drunk"            # Алкогольный поток сознания
    AGGRESSIVE = "aggressive"  # Негатив, срач, троллинг
    SAD = "sad"                # Грусть, жалобы, проблемы
    FLIRTY = "flirty"         # Флирт, подкаты
    MOTIVATIONAL = "motivational"  # Мотивация, успех, цели


# Паттерны для определения вайба
VIBE_PATTERNS = {
    ChatVibe.DRUNK: {
        "keywords": [
            "бух", "пьян", "выпил", "выпива", "водка", "пиво", "коньяк",
            "луна", "самогон", "бухло", "алкоголь", "похмелье", "опохмел",
            "бутылк", "рюмк", "стакан", "напит", "градус", "выпьем",
            "наливай", "допил", "опьянел", "шашлык", "закус",
        ],
        "patterns": [r"[😂🤣😅]{2,}", r"[.]{3,}", r"[!]{3,}"],
        "weight": 2.0,
    },
    ChatVibe.FUNNY: {
        "keywords": [
            "хаха", "ахах", "лол", "кек", "ржу", "ору", "смешно",
            "прикол", "шуточ", "юмор", "мем", "шутк", "funny",
        ],
        "patterns": [r"(?:хаха|ахах|лол|кек){2,}", r"😂+", r"🤣+"],
        "weight": 1.5,
    },
    ChatVibe.SAD: {
        "keywords": [
            "грустн", "печаль", "жаль", "жаль", "обидн", "жаль",
            "плак", "слёз", "депресс", "тоск", "одиноч", "устал",
            "больно", "трудн", "тяжел", "горьк", "хренов", "хуёв",
            "паршив", "отстой",
        ],
        "patterns": [r"\({2,}"],
        "weight": 1.5,
    },
    ChatVibe.AGGRESSIVE: {
        "keywords": [
            "дурак", "идиот", "тупой", "дебил", "придурок", "урод",
            "заткнись", "отвали", "пошёл", "блядь", "сука", "хуй",
            "пизд", "мудак", "говно", "дерьм",
        ],
        "patterns": [r"[А-ЯЁ]{5,}"],
        "weight": 2.0,
    },
    ChatVibe.SERIOUS: {
        "keywords": [
            "проблема", "вопрос", "обсужда", "анализ", "данные",
            "результат", "исследован", "наук", "факт", "доказательств",
            "важно", "серьёзно", "серьезно",
        ],
        "patterns": [],
        "weight": 1.0,
    },
    ChatVibe.FLIRTY: {
        "keywords": [
            "красив", "мил", "симпатич", "нравишься", "встретим",
            "погуляем", "поцелу", "обним", "нежн", "романти",
            "подкат", "флирт",
        ],
        "patterns": [r"😘+", r"💋+", r"❤️+"],
        "weight": 1.5,
    },
    ChatVibe.MOTIVATIONAL: {
        "keywords": [
            "цель", "достич", "успех", "мотивац", "продуктив",
            "результат", "достижен", "план", "прогресс", "развит",
            "вдохнов", "энерги", "сила",
        ],
        "patterns": [r"💪+", r"🔥+"],
        "weight": 1.0,
    },
}


@dataclass
class VibeAnalysis:
    """Result of chat vibe analysis."""
    primary_vibe: ChatVibe
    intensity: float        # 0.0 - 1.0, how strong the vibe is
    secondary_vibe: Optional[ChatVibe] = None
    message_count: int = 0
    emoji_density: float = 0.0  # emojis per message
    avg_length: float = 0.0     # average message length
    has_exclamation: bool = False
    has_caps: bool = False
    
    def to_prompt_modifier(self) -> str:
        """Generate a prompt modifier for the generator."""
        parts = []
        
        if self.primary_vibe == ChatVibe.DRUNK:
            parts.append("Тон чата: пьяный поток сознания. Отвечай в том же духе — абсурдно, с юмором, как свой парень.")
        elif self.primary_vibe == ChatVibe.FUNNY:
            parts.append("Тон чата: шуточный. Поддержи юмор, будь смешным.")
        elif self.primary_vibe == ChatVibe.SAD:
            parts.append("Тон чата: грустный. Будь empathetic, но не ментор. Просто будь рядом.")
        elif self.primary_vibe == ChatVibe.AGGRESSIVE:
            parts.append("Тон чата: напряжённый. Не вступай в конфликт, деэскалируй.")
        elif self.primary_vibe == ChatVibe.SERIOUS:
            parts.append("Тон чата: серьёзный. Отвечай по делу, без лишнего юмора.")
        elif self.primary_vibe == ChatVibe.FLIRTY:
            parts.append("Тон чата: игривый. Можно подыграть, но не перегибать.")
        elif self.primary_vibe == ChatVibe.MOTIVATIONAL:
            parts.append("Тон чата: мотивационный. Поддержи, но без пафоса.")
        else:
            parts.append("Тон чата: обычный, болтовня. Отвечай легко.")
        
        if self.intensity > 0.7:
            parts.append("Энергия высокая — чат активный.")
        elif self.intensity < 0.3:
            parts.append("Энергия низкая — чат тихий.")
        
        if self.emoji_density > 3:
            parts.append("В чате много эмодзи — используй тоже.")
        
        return " ".join(parts)


class ChatVibeDetector:
    """Detects the current vibe/energy of a chat from recent messages."""
    
    def analyze(self, messages: list[str]) -> VibeAnalysis:
        """
        Analyze recent messages to determine chat vibe.
        
        Args:
            messages: List of recent message texts (newest last)
        
        Returns:
            VibeAnalysis with detected vibe
        """
        if not messages:
            return VibeAnalysis(
                primary_vibe=ChatVibe.CASUAL,
                intensity=0.5,
                message_count=0,
            )
        
        # Score each vibe
        vibe_scores: dict[ChatVibe, float] = {v: 0.0 for v in ChatVibe}
        
        total_emojis = 0
        total_length = 0
        has_exclamation = False
        has_caps = False
        
        for msg in messages:
            text = msg.lower().strip()
            total_length += len(text)
            
            # Count emojis
            emoji_count = len(re.findall(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]', msg))
            total_emojis += emoji_count
            
            if "!" in msg:
                has_exclamation = True
            if any(c.isupper() and len(c) > 1 for c in msg.split()):
                has_caps = True
            
            # Check each vibe
            for vibe, config in VIBE_PATTERNS.items():
                weight = config["weight"]
                
                # Keyword matching
                for keyword in config["keywords"]:
                    if keyword in text:
                        vibe_scores[vibe] += weight
                
                # Pattern matching
                for pattern in config["patterns"]:
                    if re.search(pattern, msg):
                        vibe_scores[vibe] += weight * 0.5
        
        # Normalize by message count
        msg_count = len(messages)
        for vibe in vibe_scores:
            vibe_scores[vibe] /= max(msg_count, 1)
        
        # Find top vibes
        sorted_vibes = sorted(vibe_scores.items(), key=lambda x: x[1], reverse=True)
        
        primary_vibe = sorted_vibes[0][0] if sorted_vibes[0][1] > 0 else ChatVibe.CASUAL
        secondary_vibe = sorted_vibes[1][0] if len(sorted_vibes) > 1 and sorted_vibes[1][1] > 0 else None
        
        # Calculate intensity (0-1)
        max_score = sorted_vibes[0][1] if sorted_vibes[0][1] > 0 else 0
        intensity = min(max_score / 2.0, 1.0)  # Normalize: score of 2+ = max intensity
        
        return VibeAnalysis(
            primary_vibe=primary_vibe,
            intensity=intensity,
            secondary_vibe=secondary_vibe,
            message_count=msg_count,
            emoji_density=total_emojis / max(msg_count, 1),
            avg_length=total_length / max(msg_count, 1),
            has_exclamation=has_exclamation,
            has_caps=has_caps,
        )


# Singleton
_detector: Optional[ChatVibeDetector] = None


def get_vibe_detector() -> ChatVibeDetector:
    """Get singleton vibe detector."""
    global _detector
    if _detector is None:
        _detector = ChatVibeDetector()
    return _detector


def detect_chat_vibe(messages: list[str]) -> VibeAnalysis:
    """Convenience function to detect chat vibe."""
    return get_vibe_detector().analyze(messages)
