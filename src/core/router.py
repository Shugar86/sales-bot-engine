"""
Fast Model Router — решает: отвечать / не отвечать / продажный триггер
Использует дешёвую быструю модель (Gemini Flash)

Дополнительно: пред-фильтрация без LLM (отстань, бот, спам, ссылки).
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger("router")


class Decision(Enum):
    IGNORE = "ignore"           # Не отвечать
    RESPOND = "respond"         # Ответить в группе
    ENGAGE = "engage"           # Завязать диалог (мягко)
    SALES_DM = "sales_dm"      # Если написал в ЛС — продавать
    WAIT = "wait"              # Подождать, пока контекст неясен
    DISENGAGE = "disengage"     # Отступить (человек просит отстать)


@dataclass
class RouteResult:
    decision: Decision
    confidence: float           # 0.0 - 1.0
    reason: str                 # Почему так решил
    topic: Optional[str] = None # Тема сообщения
    keywords_matched: list = field(default_factory=list)
    parse_failed: bool = False  # True if LLM output was not valid routable JSON/decision


# === ПРЕД-ФИЛЬТРЫ (без LLM, мгновенно) ===

# Просит отстать — сразу DISENGAGE
GO_AWAY_PATTERNS = [
    "отстань", "отвали", "не пиши", "хватит", "прекрати",
    "заткнись", "замолчи", "уйди", "не надо", "стоп",
    "я не просил", "не интересно", "не хочу общаться",
    "перестань", "хватит уже", "оставь меня", "не пиши мне",
]

# Спрашивает про бота — RESPOND (генератор ответит)
BOT_QUESTION_PATTERNS = [
    "ты бот", "ты ai", "ты искусственный", "ты программа",
    "ты нейросеть", "ты chatgpt", "ты gpt", "ты робот",
    "бот ты", "аи ты", "это бот", "автоматический ответ",
    "машина ты", "нейросетка",
]

# Явный спам / реклама — IGNORE
SPAM_PATTERNS = [
    r"https?://t\.me/",      # Ссылки на каналы
    r"https?://bit\.ly/",
    r"заработ[окай]",         # "заработок", "заработай"
    r"подпишись.*канал",
    r"переходи.*ссылк",
    r"бесплатн.*курс",
    r"крипт[оа].*трейд",
    r"\+\d{10,}",             # Длинные номера телефонов (спам)
]

# Сообщения-реакции (не несут смысла) — IGNORE
TRIVIAL_PATTERNS = [
    "лол", "кек", "😂", "👍", "😄", "🔥", "❤️",
    "+1", "согласен", "поддерживаю", "да", "нет",
    "ок", "окей", "окк", "ага", "угу", "ну да",
    "хаха", "ахах", "))))", ")))",
    "++", "---", "???", "...",
]

# Паттерны для проверки "только тривиальные символы"
TRIVIAL_ONLY_PATTERNS = [
    r'^[😂🤣😄😆😅❤️🔥👍💪🤝😘💋🤔👀\s]+$',  # Only emojis
    r'^[.?!\-+]+$',                                  # Only punctuation
    r'^\d+$',                                         # Only numbers
    r'^[хХхХhH]{2,}$',                               # Only laughter "хах", "хахах"
]


SYSTEM_PROMPT = """Ты — фильтр сообщений в чате. Решаешь, как реагировать на сообщение от имени персонажа-эксперта.

Персонаж — РЕАЛЬНЫЙ ЧЕЛОВЕК в чате. Он эксперт своей ниши, общается как живой.

Правила принятия решений:
1. RESPOND — если сообщение релевантно темам персонажа (продукты, ниша, смежные вопросы)
2. ENGAGE — если можно вступить в разговор естественно (близкая тема, общий интерес)
3. IGNORE — если: не по теме, кто-то уже ответил, спам, эмоциональные сообщения без смысла
4. WAIT — если контекст неясен, нужно больше информации
5. DISENGAGE — если человек явно не хочет общаться

ВАЖНО:
- Если кто-то уже дал хороший ответ по теме — IGNORE
- Если сообщение "👍" или "лол" — IGNORE
- Если обсуждают политику/спорт и это не ниша персонажа — IGNORE
- Если вопрос на грани ниши — ENGAGE (можно помочь смежно)
- Если вопрос прямо по продукту — RESPOND с высокой уверенностью

Отвечай ТОЛЬКО JSON:
{"decision": "...", "confidence": 0.0-1.0, "reason": "...", "topic": "...", "keywords": [...]}"""


class MessageRouter:
    """Fast model роутер + пред-фильтры без LLM"""
    
    def __init__(self, llm_client: LLMClient, model: str, contract: dict):
        self.llm = llm_client
        self.model = model
        self.contract = contract
        self._persona_summary = self._build_persona_summary()
    
    def _build_persona_summary(self) -> str:
        """Краткая выжимка персонажа"""
        persona = self.contract.get("persona", {})
        products = self.contract.get("product", {}).get("products", [])
        triggers = self.contract.get("triggers", {})
        
        parts = [
            f"Имя: {persona.get('name', '?')}",
            f"Роль: {persona.get('backstory', '')[:200]}",
        ]
        
        if products:
            parts.append(f"Продукты: {', '.join(p.get('name', '') for p in products)}")
        
        respond_triggers = triggers.get("respond_to", [])
        if respond_triggers:
            parts.append("Отвечать когда:")
            for t in respond_triggers:
                context = t.get("context", "")
                keywords = ", ".join(t.get("keywords", [])[:5])
                parts.append(f"  - {context} ({keywords})")
        
        ignore_triggers = triggers.get("ignore", [])
        if ignore_triggers:
            parts.append("Игнорировать:")
            for i in ignore_triggers:
                parts.append(f"  - {i}")
        
        return "\n".join(parts)
    
    def _prefilter(self, message_text: str) -> Optional[RouteResult]:
        """
        Пред-фильтрация без LLM. Возвращает решение или None (нужен LLM).
        Работает мгновенно, не тратит токены.
        """
        text = message_text.lower().strip()
        
        # Пустые сообщения
        if not text:
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=1.0,
                reason="Пустое сообщение",
            )
        
        # Слишком короткие тривиальные реакции
        if len(text) <= 5 and text in [t.lower() for t in TRIVIAL_PATTERNS]:
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.95,
                reason="Тривиальная реакция",
            )
        
        # Проверка "только тривиальные символы" (эмодзи, пунктуация, числа)
        for pattern in TRIVIAL_ONLY_PATTERNS:
            if re.match(pattern, text):
                return RouteResult(
                    decision=Decision.IGNORE,
                    confidence=0.95,
                    reason="Только тривиальные символы",
                )
        
        # "Отстань" — сразу DISENGAGE
        for pattern in GO_AWAY_PATTERNS:
            if pattern in text:
                return RouteResult(
                    decision=Decision.DISENGAGE,
                    confidence=1.0,
                    reason="Пользователь просит отстать",
                    topic="disengage",
                )
        
        # Спам-паттерны
        for pattern in SPAM_PATTERNS:
            if re.search(pattern, text):
                return RouteResult(
                    decision=Decision.IGNORE,
                    confidence=0.95,
                    reason=f"Спам-паттерн: {pattern}",
                    topic="spam",
                )
        
        # Только ссылки без текста
        if re.match(r'^https?://\S+$', text):
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.9,
                reason="Голая ссылка",
            )
        
        # "Ты бот?" — RESPOND (генератор обработает)
        for pattern in BOT_QUESTION_PATTERNS:
            if pattern in text:
                return RouteResult(
                    decision=Decision.RESPOND,
                    confidence=1.0,
                    reason="Вопрос 'ты бот?' — отвечаем как человек",
                    topic="bot_question",
                )
        
        # Всё остальное — нужен LLM
        return None
    
    async def route(
        self,
        message_text: str,
        chat_context: str = "",
        is_dm: bool = False,
    ) -> RouteResult:
        """
        Решить как реагировать на сообщение.
        
        Args:
            message_text: Текст сообщения
            chat_context: Контекст чата (последние сообщения)
            is_dm: Это ЛС?
        
        Returns:
            RouteResult с решением
        """
        # DM: сначала пред-фильтры (отстань / спам / пусто), иначе — продажный DM
        if is_dm:
            prefilter_dm = self._prefilter(message_text)
            if prefilter_dm is not None:
                return prefilter_dm
            return RouteResult(
                decision=Decision.SALES_DM,
                confidence=1.0,
                reason="Direct message — engage",
                topic="dm",
            )
        
        # Пред-фильтр (без LLM)
        prefilter_result = self._prefilter(message_text)
        if prefilter_result is not None:
            return prefilter_result
        
        # LLM-роутинг
        user_prompt = f"""Персонаж:
{self._persona_summary}

Сообщение из чата:
"{message_text}"

Контекст (последние сообщения):
{chat_context or "(контекста нет)"}

Решай:"""
        
        response = await self.llm.call(
            model=self.model,
            prompt=user_prompt,
            system=SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=256,
        )
        
        if not response.success:
            logger.error(f"Router call failed: {response.error}")
            # При ошибке LLM — IGNORE (безопасный fallback)
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason=f"LLM error: {response.error}",
            )
        
        return self._parse_response(response.text)
    
    def _parse_response(self, text: str) -> RouteResult:
        """Парсинг ответа модели.

        Валидный маршрут: JSON с полем ``decision``, значение мапится на :class:`Decision`.
        Иначе — ``parse_failed=True`` и ``IGNORE`` (не смешиваем с осознанным ignore из JSON).
        """
        raw_preview = (text or "")[:200]
        try:
            text = (text or "").strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:])
            if text.endswith("```"):
                text = "\n".join(text.split("\n")[:-1])
            text = text.strip()

            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(
                f"Router JSON parse failed: {e} | text (truncated): {raw_preview!r}"
            )
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason=f"JSON decode error: {e}",
                parse_failed=True,
            )

        if "decision" not in data:
            logger.warning(
                f"Router response missing 'decision' | text (truncated): {raw_preview!r}"
            )
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason="missing decision field",
                parse_failed=True,
            )

        decision_raw = data["decision"]
        decision_str = str(decision_raw).strip().upper()
        try:
            decision = Decision(decision_str.lower())
        except ValueError:
            logger.warning(
                f"Invalid router decision {decision_str!r} | text (truncated): {raw_preview!r}"
            )
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason=f"invalid decision: {decision_str}",
                parse_failed=True,
            )

        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError) as e:
            logger.warning(
                f"Invalid router confidence {data.get('confidence')!r} | text: {raw_preview!r}"
            )
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason=f"invalid confidence: {e}",
                parse_failed=True,
            )

        keywords = data.get("keywords", [])
        if keywords is None:
            keywords = []
        if not isinstance(keywords, list):
            logger.warning(
                f"Router keywords not a list: {type(keywords).__name__} | text: {raw_preview!r}"
            )
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason="invalid keywords field",
                parse_failed=True,
            )

        return RouteResult(
            decision=decision,
            confidence=confidence,
            reason=str(data.get("reason", "")),
            topic=data.get("topic"),
            keywords_matched=keywords,
            parse_failed=False,
        )
