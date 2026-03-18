"""
Response Generator — генерит ответ на основе YAML-контракта + контекста юзера
Группа: экспертные реплики, без продажи
ЛС: консультация + мягкая продажа
"""

import json
from dataclasses import dataclass
from typing import Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger("generator")


@dataclass
class GeneratedResponse:
    text: str
    tone: str              # "expert", "casual", "concerned", "selling"
    stage: str             # "engage", "help", "soft_sell", "direct_sell"
    remember: list         # Что запомнить


# === SYSTEM PROMPTS ===

GROUP_SYSTEM = """Ты — {persona_name}, {persona_backstory}

Твой стиль общения:
{speaking_style}

Ты в групповом чате. Ты эксперт, НЕ продавец.

ПРАВИЛА:
- 1-3 предложения максимум
- Как коллега, не как продавец  
- Никакого маркетинга в группе
- Можно поделиться личным опытом
- Если нечего сказать — лучше молчать

Если не должен отвечать — верни {{"text": "", "reason": "..."}}

Ответь ТОЛЬКО JSON:
{{"text": "сообщение для чата", "tone": "expert|casual|concerned", "stage": "engage|help", "remember": ["что запомнить"]}}"""


DM_SYSTEM = """Ты — {persona_name}, {persona_backstory}

Твой стиль общения:
{speaking_style}

Пользователь написал тебе в личку.

Что мы о нём помним:
{user_memory}

История диалога:
{dm_history}

Контекст из группы (если был):
{group_context}

Твой продукт:
{product_info}

ПРАВИЛА ДЛЯ ЛС:
- Консультируй прямо
- Можно мягко упомянуть продукт если уместно
- Не давить. Эксперт, не продавец.
- Если отказ — остаёшься полезным
- "Да вот такой корм пробовали? У моих подопечных хорошо пошло"

Стадия воронки: {funnel_stage}

Ответь ТОЛЬКО JSON:
{{"text": "сообщение", "tone": "...", "stage": "engage|help|soft_sell|direct_sell", "remember": ["новое"]}}"""


class ResponseGenerator:
    """Slow model генератор ответов"""
    
    def __init__(self, llm_client: LLMClient, model: str, contract: dict):
        self.llm = llm_client
        self.model = model
        self.contract = contract
    
    def _get_persona(self) -> dict:
        return self.contract.get("persona", {})
    
    def _get_speaking_style(self) -> str:
        style = self._get_persona().get("speaking_style", {})
        parts = []
        if style.get("tone"):
            parts.append(f"Тон: {style['tone']}")
        for p in style.get("patterns", []):
            parts.append(f"- {p}")
        if style.get("forbidden"):
            parts.append("\nЗАПРЕЩЕНО:")
            for f in style["forbidden"]:
                parts.append(f"- {f}")
        return "\n".join(parts)
    
    def _get_flow_rules(self) -> str:
        flow = self.contract.get("conversation_flow", {})
        group = flow.get("group_chat", {})
        never = flow.get("never", [])
        
        parts = []
        if group.get("steps"):
            for step in group["steps"]:
                parts.append(f"- {step}")
        if never:
            parts.append("\nНИКОГДА:")
            for n in never:
                parts.append(f"- {n}")
        return "\n".join(parts)
    
    async def generate_group_response(
        self,
        message_text: str,
        chat_context: str = "",
    ) -> Optional[GeneratedResponse]:
        """
        Сгенерить ответ в группу.
        
        Returns:
            GeneratedResponse или None если нечего сказать
        """
        persona = self._get_persona()
        
        system = GROUP_SYSTEM.format(
            persona_name=persona.get("name", "Андрей"),
            persona_backstory=persona.get("backstory", "")[:300],
            speaking_style=self._get_speaking_style(),
        )
        
        user_prompt = f"""Сообщение в чате:
"{message_text}"

Контекст чата:
{chat_context or "(контекста нет)"}

Правила:
{self._get_flow_rules()}

Твой ответ:"""
        
        response = await self.llm.call(
            model=self.model,
            prompt=user_prompt,
            system=system,
            temperature=0.8,
            max_tokens=512,
        )
        
        if not response.success:
            logger.error(f"Generator call failed: {response.error}")
            return None
        
        result = self._parse_response(response.text)
        
        if result and len(result.text) > 300:
            result.text = result.text[:297] + "..."
        
        return result
    
    async def generate_dm_response(
        self,
        message_text: str,
        user_memory: str = "",
        dm_history: str = "",
        group_context: str = "",
        funnel_stage: str = "engage",
    ) -> Optional[GeneratedResponse]:
        """
        Сгенерить ответ в ЛС.
        
        Returns:
            GeneratedResponse
        """
        persona = self._get_persona()
        products = self.contract.get("product", {}).get("products", [])
        product_info = json.dumps(products, ensure_ascii=False, indent=2) if products else "Нет информации о продуктах"
        
        system = DM_SYSTEM.format(
            persona_name=persona.get("name", "Андрей"),
            persona_backstory=persona.get("backstory", "")[:300],
            speaking_style=self._get_speaking_style(),
            user_memory=user_memory or "(ничего не знаем)",
            dm_history=dm_history or "(первое сообщение)",
            group_context=group_context or "(не общались в группе)",
            product_info=product_info,
            funnel_stage=funnel_stage,
        )
        
        user_prompt = f'Сообщение от пользователя:\n"{message_text}"\n\nТвой ответ:'
        
        response = await self.llm.call(
            model=self.model,
            prompt=user_prompt,
            system=system,
            temperature=0.7,
            max_tokens=1024,
        )
        
        if not response.success:
            logger.error(f"DM generator failed: {response.error}")
            return None
        
        return self._parse_response(response.text)
    
    def _parse_response(self, text: str) -> Optional[GeneratedResponse]:
        """Парсинг ответа генератора"""
        try:
            text = text.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:])
            if text.endswith("```"):
                text = "\n".join(text.split("\n")[:-1])
            text = text.strip()
            
            data = json.loads(text)
            
            resp_text = data.get("text", "").strip()
            if not resp_text:
                return None
            
            return GeneratedResponse(
                text=resp_text,
                tone=data.get("tone", "expert"),
                stage=data.get("stage", "engage"),
                remember=data.get("remember", []),
            )
            
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse generator response: {e} | text: {text[:200]}")
            return None
