"""
Vibe Checker — определяет, есть ли в чате что-то "в vibe" для персоны.

Это СЕРДЦЕ новой архитектуры. Бот — не продавец, а живой человек.
Vibe Checker решает: стоит ли вступать в разговор, или лучше промолчать.

Философия:
- Прочитал контекст чата → есть ли тут что-то для меня?
- Если да → respond naturally (personal story, context, humor)
- Если нет → wait, maybe react with emoji, maybe leave on read
"""

from dataclasses import dataclass
from typing import Optional

from .context_reader import ChatContext, VibeType


@dataclass
class VibeCheck:
    """
    Результат проверки "вайба" — стоит ли персоне отвечать.
    """
    should_respond: bool      # True если что-то совпадает с personality
    confidence: float         # 0.0 - 1.0
    reason: str               # почему respond/not respond
    suggested_angle: str      # как подойти к ответу (personal story, advice, humor, etc.)
    match_type: str = ""      # what kind of match: topic/personal/mention/humor/general


class VibeChecker:
    """
    Проверяет "вайб" чата и решает, совпадает ли что-то с personality персоны.
    
    Не использует LLM — быстрая эвристическая проверка.
    LLM вызывается позже только если vibe_checker сказал "да".
    """
    
    def __init__(self, persona_config: dict):
        """
        Args:
            persona_config: Конфигурация персонажа из YAML.
                Ожидаемые ключи:
                - vibe.role: роль персонажа
                - vibe.taboos: список табуированных тем
                - triggers.respond_when: триггеры для ответа
                - behavior.always: общее поведение
                - behavior.on_offtopic: поведение на оффтоп
        """
        self.config = persona_config
        self.vibe = persona_config.get("vibe", {})
        self.triggers = persona_config.get("triggers", {})
        self.behavior = persona_config.get("behavior", {})
        
        # Извлекаем ключевые слова из триггеров
        self.respond_keywords = []
        self.respond_topics = []
        for trigger in self.triggers.get("respond_when", []):
            self.respond_keywords.extend(trigger.get("keywords", []))
            self.respond_topics.extend(trigger.get("topics", []))
        
        # Извлекаем игнорируемые слова
        self.ignore_patterns = []
        for ignore in self.triggers.get("ignore_when", []):
            if isinstance(ignore, dict):
                self.ignore_patterns.extend(ignore.get("contains", []))
        
        # Табу
        self.taboos = self.vibe.get("taboos", [])
    
    def check(
        self,
        context: ChatContext,
        last_message_text: str = "",
        persona_name: str = "",
    ) -> VibeCheck:
        """
        Проверить, есть ли в чате что-то "в vibe" для персоны.
        
        Args:
            context: Контекст чата (из context_reader)
            last_message_text: Текст последнего сообщения
            persona_name: Имя персонажа
        
        Returns:
            VibeCheck с решением и обоснованием.
        """
        text = (last_message_text or "").lower().strip()
        
        if not text:
            return VibeCheck(
                should_respond=False,
                confidence=0.9,
                reason="Пустое сообщение",
                suggested_angle="",
                match_type="empty",
            )
        
        # === CHECK 0: Табуированные темы ===
        taboo_result = self._check_taboo(text)
        if taboo_result:
            return taboo_result
        
        # === CHECK 1: Игнорируемые паттерны ===
        ignore_result = self._check_ignore(text)
        if ignore_result:
            return ignore_result
        
        # === CHECK 2: Направлено на меня (mention/reply) ===
        if context.is_directed_at_me:
            return VibeCheck(
                should_respond=True,
                confidence=0.95,
                reason="Сообщение направлено на меня (ответ/упоминание)",
                suggested_angle="reply",
                match_type="mention",
            )
        
        # === CHECK 3: Триггеры по ключевым словам ===
        keyword_result = self._check_keywords(text)
        if keyword_result:
            return keyword_result
        
        # === CHECK 4: Триггеры по теме контекста ===
        topic_result = self._check_topic_match(context)
        if topic_result:
            return topic_result
        
        # === CHECK 5: Личный опыт — могу поделиться ===
        personal_result = self._check_personal_opportunity(text, context)
        if personal_result:
            return personal_result
        
        # === CHECK 6: Юмор / casual — можно вступить ===
        humor_result = self._check_humor_opportunity(text, context)
        if humor_result:
            return humor_result
        
        # === DEFAULT: Не совпадает — лучше промолчать ===
        return VibeCheck(
            should_respond=False,
            confidence=0.7,
            reason="Нет совпадений с personality персонажа",
            suggested_angle="leave_on_read",
            match_type="none",
        )
    
    def _check_taboo(self, text: str) -> Optional[VibeCheck]:
        """Проверка табуированных тем."""
        taboo_keywords = {
            "политика": ["политик", "выборы", "путин", "президент", "парламент", "госдума", "партия"],
            "религия": ["бог", "церковь", "мусульман", "христиан", "религи", "вера", "молитв"],
            "оскорбления": ["ублюдок", "сволочь", "мразь", "тварь"],
        }
        
        for taboo in self.taboos:
            taboo_lower = taboo.lower()
            if taboo_lower in text:
                return VibeCheck(
                    should_respond=True,
                    confidence=0.9,
                    reason=f"Табуированная тема: {taboo}",
                    suggested_angle="taboo_deflect",
                    match_type="taboo",
                )
            
            # Проверяем связанные ключевые слова
            for category, keywords in taboo_keywords.items():
                if taboo_lower in category:
                    if any(kw in text for kw in keywords):
                        return VibeCheck(
                            should_respond=True,
                            confidence=0.9,
                            reason=f"Табуированная тема: {category}",
                            suggested_angle="taboo_deflect",
                            match_type="taboo",
                        )
        
        return None
    
    def _check_ignore(self, text: str) -> Optional[VibeCheck]:
        """Проверка игнорируемых паттернов."""
        for pattern in self.ignore_patterns:
            if isinstance(pattern, str) and pattern.lower() in text:
                return VibeCheck(
                    should_respond=False,
                    confidence=0.95,
                    reason=f"Игнорируемый паттерн: {pattern}",
                    suggested_angle="",
                    match_type="ignore",
                )
        return None
    
    def _check_keywords(self, text: str) -> Optional[VibeCheck]:
        """Проверка совпадений по ключевым словам триггеров."""
        matched_keywords = []
        for kw in self.respond_keywords:
            if kw.lower() in text:
                matched_keywords.append(kw)
        
        if matched_keywords:
            confidence = min(0.5 + len(matched_keywords) * 0.15, 0.95)
            
            # Определяем angle на основе типа ключевых слов
            food_words = ["корм", "ест", "еда", "рацион", "кормить"]
            if any(kw.lower() in " ".join(food_words) for kw in matched_keywords):
                angle = "personal_experience"
            else:
                angle = "advice"
            
            return VibeCheck(
                should_respond=True,
                confidence=confidence,
                reason=f"Совпадение по ключевым словам: {', '.join(matched_keywords[:3])}",
                suggested_angle=angle,
                match_type="keyword",
            )
        
        return None
    
    def _check_topic_match(self, context: ChatContext) -> Optional[VibeCheck]:
        """Проверка совпадения темы контекста с темами персонажа."""
        if not context.topic or context.topic == "general":
            return None
        
        # Маппинг тем контекста на темы персонажа
        topic_mapping = {
            "dogs_food": ["кормление собак", "корм для собак"],
            "dogs_health": ["проблемы со здоровьем", "здоровье собак"],
            "fitness": ["тренировки", "фитнес", "питание"],
            "business": ["бизнес", "маркетинг", "продажи"],
        }
        
        persona_topics = set()
        for trigger in self.triggers.get("respond_when", []):
            persona_topics.update(trigger.get("topics", []))
        
        context_topic = context.topic
        mapped_topics = topic_mapping.get(context_topic, [context_topic])
        
        for mapped in mapped_topics:
            if mapped in persona_topics:
                return VibeCheck(
                    should_respond=True,
                    confidence=0.75,
                    reason=f"Тема чата '{context.topic}' совпадает с темой персонажа",
                    suggested_angle="expert_comment",
                    match_type="topic",
                )
        
        return None
    
    def _check_personal_opportunity(
        self,
        text: str,
        context: ChatContext,
    ) -> Optional[VibeCheck]:
        """
        Проверка возможности поделиться личным опытом.
        
        Даже если тема не прямая — можно вступить как живой человек:
        "У меня собака так делала", "А я вчера..."
        """
        # Проверяем, есть ли в поведении настройка "всегда"
        always_behavior = self.behavior.get("always", "")
        if not always_behavior:
            return None
        
        # Если поведение говорит "делись личным опытом" — можно вступать
        # на более широкий круг тем

        # Если в тексте есть вопрос — больше шансов вступить
        has_question = "?" in text or any(
            w in text for w in ["как", "что", "почему", "где", "когда", "кто"]
        )
        
        # Живые темы — всё, на что можно отреагировать как человек
        life_topics = [
            "собак", "пёс", "щенок", "кот", "животн",  # pets
            "готовить", "еда", "кафе", "ресторан",       # food
            "фильм", "сериал", "музык",                   # entertainment
            "работ", "дом", "отдых", "выходн",           # life
            "погод", "новост",                             # general
        ]
        
        topic_matches = sum(1 for t in life_topics if t in text)
        
        if topic_matches >= 1 and has_question:
            return VibeCheck(
                should_respond=True,
                confidence=0.6,
                reason="Живая тема + вопрос — можно поделиться опытом",
                suggested_angle="personal_story",
                match_type="personal",
            )
        
        # В casual чате — можно вступать просто так
        if context.vibe == VibeType.CASUAL.value and topic_matches >= 2:
            return VibeCheck(
                should_respond=True,
                confidence=0.5,
                reason="Casual чат, есть тема для реплики",
                suggested_angle="casual_comment",
                match_type="personal",
            )
        
        return None
    
    def _check_humor_opportunity(
        self,
        text: str,
        context: ChatContext,
    ) -> Optional[VibeCheck]:
        """Проверка возможности для шутки/юмора."""
        humor_triggers = [
            "анекдот", "шутк", "рассмеши", "смешн", "прикол",
            "хаха", "😂", "🤣",
        ]
        
        if any(t in text for t in humor_triggers):
            return VibeCheck(
                should_respond=True,
                confidence=0.7,
                reason="Триггер юмора — можно шутить",
                suggested_angle="humor",
                match_type="humor",
            )
        
        # Если вайб чата funny — тоже можно
        if context.vibe == VibeType.FUNNY.value:
            return VibeCheck(
                should_respond=True,
                confidence=0.55,
                reason="Вайб чата funny — можно поддержать юмор",
                suggested_angle="humor_support",
                match_type="humor",
            )
        
        return None
