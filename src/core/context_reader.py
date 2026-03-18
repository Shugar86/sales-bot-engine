"""
Context Reader — читает последние N сообщений из чата для понимания контекста разговора.

Формирует ChatContext: участники, тема, вайб, направлено ли на меня.
Это основа для "живого" общения — бот должен понимать контекст чата,
а не просто реагировать на одно сообщение.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class VibeType(Enum):
    """Тип вайба чата."""
    CASUAL = "casual"          # обычная болтовня
    SERIOUS = "serious"        # серьёзное обсуждение
    FUNNY = "funny"            # шутки, смех
    SAD = "sad"                # грусть, проблемы
    AGGRESSIVE = "aggressive"  # негатив, срач
    NEUTRAL = "neutral"        # без определённого тона


@dataclass
class Message:
    """Сообщение в чате."""
    message_id: int
    user_id: str
    username: str
    display_name: str
    text: str
    timestamp: int  # unix timestamp
    is_from_me: bool = False
    reply_to_message_id: Optional[int] = None
    is_reply_to_me: bool = False


@dataclass
class ChatContext:
    """
    Контекст чата на основе последних N сообщений.
    
    Используется vibe_checker и decision_gate для понимания
    что происходит в чате и надо ли вмешиваться.
    """
    messages: list[Message] = field(default_factory=list)  # last N messages
    participants: list[str] = field(default_factory=list)  # who's talking (usernames)
    topic: str = ""              # current topic detected
    vibe: str = "casual"         # casual/serious/funny/sad/aggressive
    is_directed_at_me: bool = False  # did someone mention/reply to me?
    my_last_message_time: int = 0    # when did I last speak here (unix)
    message_count: int = 0           # total messages in context
    unique_participants: int = 0     # how many different people
    
    @property
    def last_message(self) -> Optional[Message]:
        """Последнее сообщение в контексте."""
        return self.messages[-1] if self.messages else None
    
    @property
    def is_active_chat(self) -> True:
        """Чат активен (есть сообщения)."""
        return len(self.messages) > 0
    
    @property
    def seconds_since_my_last_message(self) -> int:
        """Сколько секунд прошло с моего последнего сообщения."""
        if not self.my_last_message_time:
            return 999999  # never spoke
        import time
        return int(time.time()) - self.my_last_message_time
    
    def get_messages_from_user(self, user_id: str) -> list[Message]:
        """Получить сообщения конкретного пользователя."""
        return [m for m in self.messages if m.user_id == user_id]
    
    def get_recent_messages(self, count: int = 3) -> list[Message]:
        """Получить последние N сообщений."""
        return self.messages[-count:] if self.messages else []


class ContextReader:
    """
    Читает сообщения чата и формирует ChatContext.
    
    Принимает список сообщений, определяет:
    - Кто участвует в разговоре
    - Какая тема обсуждается
    - Какой вайб (тон) в чате
    - Направлено ли последнее сообщение на персону
    - Когда персона последний раз писала
    """
    
    # Ключевые слова для определения темы
    TOPIC_KEYWORDS = {
        "dogs_food": ["корм", "кормить", "ест", "еда", "рацион", "собака", "пёс", "щенок"],
        "dogs_health": ["болеет", "ветеринар", "врач", "лечение", "симптом", "аллергия"],
        "fitness": ["тренировка", "зал", "мышцы", "похудеть", "накачать", "вес"],
        "business": ["бизнес", "клиент", "деньги", "продажи", "маркетинг"],
        "food_general": ["еда", "готовить", "рецепт", "кафе", "ресторан"],
        "tech": ["компьютер", "программа", "код", "разработка", "айти"],
        "life": ["работа", "дом", "семья", "отдых", "планы", "выходные"],
    }
    
    # Ключевые слова для определения вайба
    VIBE_KEYWORDS = {
        VibeType.FUNNY: ["хаха", "ахах", "лол", "😂", "🤣", "ржу", "смешно", "прикол"],
        VibeType.SAD: ["грустн", "печаль", "жаль", "плохо", "устал", "депрессия"],
        VibeType.AGGRESSIVE: ["дурак", "идиот", "блядь", "сука", "заткнись", "отвали"],
        VibeType.SERIOUS: ["проблема", "вопрос", "важно", "серьёзно", "обсужда"],
    }
    
    def __init__(self, my_user_id: str = ""):
        """
        Args:
            my_user_id: ID персоны в чате (для определения "направлено на меня")
        """
        self.my_user_id = my_user_id
    
    def read_context(
        self,
        messages: list[dict],
        my_last_message_time: int = 0,
    ) -> ChatContext:
        """
        Прочитать контекст чата из списка сообщений.
        
        Args:
            messages: Список сообщений (dict с полями message_id, user_id, 
                      username, display_name, text, timestamp)
            my_last_message_time: Unix timestamp моего последнего сообщения
        
        Returns:
            ChatContext с полной информацией о контексте чата
        """
        if not messages:
            return ChatContext(my_last_message_time=my_last_message_time)
        
        # Конвертируем в Message объекты
        parsed_messages = []
        for msg in messages:
            parsed_messages.append(Message(
                message_id=msg.get("message_id", 0),
                user_id=str(msg.get("user_id", "")),
                username=msg.get("username", ""),
                display_name=msg.get("display_name", ""),
                text=msg.get("text", ""),
                timestamp=msg.get("timestamp", 0),
                is_from_me=str(msg.get("user_id", "")) == self.my_user_id,
                reply_to_message_id=msg.get("reply_to_message_id"),
                is_reply_to_me=msg.get("is_reply_to_me", False),
            ))
        
        # Убираем дубли по message_id
        seen_ids = set()
        unique_messages = []
        for m in parsed_messages:
            if m.message_id not in seen_ids:
                seen_ids.add(m.message_id)
                unique_messages.append(m)
        parsed_messages = unique_messages
        
        # Сортируем по времени
        parsed_messages.sort(key=lambda m: m.timestamp)
        
        # Собираем участников
        participants_set = set()
        for msg in parsed_messages:
            if msg.username:
                participants_set.add(msg.username)
            elif msg.display_name:
                participants_set.add(msg.display_name)
        
        # Определяем тему
        topic = self._detect_topic(parsed_messages)
        
        # Определяем вайб
        vibe = self._detect_vibe(parsed_messages)
        
        # Проверяем, направлено ли на меня
        is_directed = self._check_if_directed_at_me(parsed_messages)
        
        return ChatContext(
            messages=parsed_messages,
            participants=list(participants_set),
            topic=topic,
            vibe=vibe,
            is_directed_at_me=is_directed,
            my_last_message_time=my_last_message_time,
            message_count=len(parsed_messages),
            unique_participants=len(participants_set),
        )
    
    def _detect_topic(self, messages: list[Message]) -> str:
        """Определить текущую тему обсуждения."""
        # Считаем совпадения по последним сообщениям
        recent = messages[-5:] if len(messages) >= 5 else messages
        all_text = " ".join(m.text.lower() for m in recent)
        
        topic_scores = {}
        for topic, keywords in self.TOPIC_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in all_text)
            if score > 0:
                topic_scores[topic] = score
        
        if topic_scores:
            return max(topic_scores, key=topic_scores.get)
        return "general"
    
    def _detect_vibe(self, messages: list[Message]) -> str:
        """Определить вайб (тон) чата."""
        recent = messages[-5:] if len(messages) >= 5 else messages
        all_text = " ".join(m.text.lower() for m in recent)
        
        vibe_scores = {}
        for vibe_type, keywords in self.VIBE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in all_text)
            if score > 0:
                vibe_scores[vibe_type] = score
        
        if vibe_scores:
            top_vibe = max(vibe_scores, key=vibe_scores.get)
            return top_vibe.value
        return VibeType.CASUAL.value
    
    def _check_if_directed_at_me(self, messages: list[Message]) -> bool:
        """Проверить, направлено ли последнее сообщение на персону."""
        if not messages:
            return False
        
        last = messages[-1]
        
        # Если это моё сообщение — не направлено на меня
        if last.is_from_me:
            return False
        
        # Если это ответ на моё сообщение
        if last.is_reply_to_me:
            return True
        
        # Если кто-то ответил на моё сообщение
        if last.reply_to_message_id:
            for msg in messages:
                if msg.message_id == last.reply_to_message_id and msg.is_from_me:
                    return True
        
        return False
    
    def summarize_context(self, context: ChatContext) -> str:
        """
        Создать текстовое описание контекста для промпта LLM.
        
        Returns:
            Строку с кратким описанием контекста чата.
        """
        if not context.messages:
            return "(контекста нет)"
        
        parts = []
        
        # Участники
        if context.participants:
            parts.append(f"Участники: {', '.join(context.participants[:5])}")
        
        # Тема
        if context.topic and context.topic != "general":
            parts.append(f"Тема: {context.topic}")
        
        # Вайб
        parts.append(f"Вайб: {context.vibe}")
        
        # Последние сообщения
        parts.append("Последние сообщения:")
        for msg in context.get_recent_messages(5):
            name = msg.display_name or msg.username or msg.user_id
            text_preview = msg.text[:100] if msg.text else "(пусто)"
            parts.append(f"  {name}: {text_preview}")
        
        # Направлено ли на меня
        if context.is_directed_at_me:
            parts.append("⚡ Последнее сообщение направлено на тебя!")
        
        return "\n".join(parts)
