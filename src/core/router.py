"""
Fast Model Router — решает: отвечать / не отвечать / продажный триггер
Использует дешёвую быструю модель (Gemini Flash)
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..utils.llm_client import LLMClient, LLMResponse
from ..utils.logger import get_logger

logger = get_logger("router")


class Decision(Enum):
    IGNORE = "ignore"           # Не отвечать
    RESPOND = "respond"         # Ответить в группе
    ENGAGE = "engage"           # Завязать диалог (мягко)
    SALES_DM = "sales_dm"      # Если написал в ЛС — продавать
    WAIT = "wait"              # Подождать, пока контекст неясен


@dataclass
class RouteResult:
    decision: Decision
    confidence: float           # 0.0 - 1.0
    reason: str                 # Почему так решил
    topic: Optional[str] = None # Тема сообщения
    keywords_matched: list = field(default_factory=list)


SYSTEM_PROMPT = """Ты — фильтр сообщений в чате. Решаешь, как реагировать.

Правила:
1. RESPOND — если релевантно контракту (продукт/темы персонажа)
2. ENGAGE — если можно мягко вступить в разговор
3. IGNORE — если не по теме, спам, или уже ответили
4. WAIT — если контекст неясен

Отвечай ТОЛЬКО JSON:
{"decision": "...", "confidence": 0.0-1.0, "reason": "...", "topic": "...", "keywords": [...]}"""


class MessageRouter:
    """Fast model роутер для принятия решений"""
    
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
        # DM всегда обрабатываем
        if is_dm:
            return RouteResult(
                decision=Decision.SALES_DM,
                confidence=1.0,
                reason="Direct message — always engage",
                topic="dm",
            )
        
        # Пустые сообщения игнорируем
        if not message_text or not message_text.strip():
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=1.0,
                reason="Empty message",
            )
        
        # Формируем промпт
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
            temperature=0.3,  # Низкая температура для стабильных решений
            max_tokens=256,
        )
        
        if not response.success:
            logger.error(f"Router call failed: {response.error}")
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason=f"LLM error: {response.error}",
            )
        
        return self._parse_response(response.text)
    
    def _parse_response(self, text: str) -> RouteResult:
        """Парсинг ответа модели"""
        try:
            # Вырезаем JSON из markdown блоков
            text = text.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:])
            if text.endswith("```"):
                text = "\n".join(text.split("\n")[:-1])
            text = text.strip()
            
            data = json.loads(text)
            
            decision_str = data.get("decision", "IGNORE").upper()
            try:
                decision = Decision(decision_str.lower())
            except ValueError:
                decision = Decision.IGNORE
            
            return RouteResult(
                decision=decision,
                confidence=float(data.get("confidence", 0.5)),
                reason=data.get("reason", ""),
                topic=data.get("topic"),
                keywords_matched=data.get("keywords", []),
            )
            
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse router response: {e} | text: {text[:200]}")
            return RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason=f"Parse error: {e}",
            )
