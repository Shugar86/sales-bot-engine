"""
Prompt Compiler — динамическая сборка промптов из persona config.

Скопировано и адаптировано из ai-tutor-engine/src/agents/core/prompt_compiler.py

Собирает системные промпты динамически на основе:
- vibe: identity/voice/emotions/values/taboos
- behavior: on_tool_success / on_tool_error / on_offtopic / etc.
- response_examples: few-shot пары для генератора
- competitor_knowledge: знания о конкурентах
"""

import logging
from typing import Optional

from .vibe_schema import VibePersona, VibeBehavior, ResponseExample

try:
    from ..utils.logger import get_logger
    logger = get_logger("prompt-compiler")
except Exception:
    logger = logging.getLogger("prompt-compiler")


class PromptCompiler:
    """
    Компилирует промпты динамически на основе persona config.
    
    Блоки:
    1. IDENTITY BLOCK — роль, голос, эмоции, ценности, табу
    2. CONTEXT BLOCK — стиль общения
    3. BEHAVIOR BLOCK — инструкции для разных ситуаций
    4. EXAMPLES BLOCK — few-shot пары хороший/плохой ответ
    5. COMPETITOR BLOCK — знания о конкурентах
    6. FACTUALITY BLOCK — безопасность вывода
    """
    
    MAX_PROMPT_TOKENS = 6000
    
    def __init__(
        self,
        vibe: Optional[VibePersona] = None,
        behavior: Optional[VibeBehavior] = None,
        response_examples: Optional[list[ResponseExample]] = None,
        competitor_knowledge: str = "",
        personality: str = "",
    ):
        self.vibe = vibe
        self.behavior = behavior
        self.response_examples = response_examples or []
        self.competitor_knowledge = competitor_knowledge
        self.personality = personality
    
    def compile_system_prompt(
        self,
        tool_status: str = "none",
        user_context: str = "",
        chat_context: str = "",
    ) -> str:
        """
        Скомпилировать системный промпт для генератора ответов.
        
        Args:
            tool_status: Статус инструмента ("success", "no_results", "error", "none")
            user_context: Контекст о пользователе из памяти
            chat_context: Контекст чата (последние сообщения)
        
        Returns:
            Готовый системный промпт.
        """
        parts = []
        
        # === 1. IDENTITY BLOCK ===
        identity = self._build_identity_block()
        if identity:
            parts.append(identity)
        
        # === 2. PERSONALITY BLOCK (legacy field) ===
        if self.personality:
            parts.append(f"=== PERSONALITY ===\n{self.personality.strip()}")
        
        # === 3. CONTEXT BLOCK ===
        context = self._build_context_block(user_context, chat_context)
        if context:
            parts.append(context)
        
        # === 4. BEHAVIOR BLOCK ===
        behavior = self._build_behavior_block(tool_status)
        if behavior:
            parts.append(behavior)
        
        # === 5. EXAMPLES BLOCK ===
        examples = self._build_examples_block()
        if examples:
            parts.append(examples)
        
        # === 6. COMPETITOR BLOCK ===
        if self.competitor_knowledge:
            parts.append(f"=== COMPETITOR KNOWLEDGE ===\n{self.competitor_knowledge.strip()}")
        
        # === 7. FACTUALITY BLOCK ===
        parts.append(self._build_factuality_block())
        
        # === 8. FORMAT RULES ===
        parts.append(self._build_format_block())
        
        full_prompt = "\n\n".join(parts)
        
        # Token check
        tokens = self._estimate_tokens(full_prompt)
        if tokens > self.MAX_PROMPT_TOKENS:
            logger.warning(f"System prompt too long: {tokens} tokens (max {self.MAX_PROMPT_TOKENS})")
        
        return full_prompt
    
    def compile_router_system_prompt(self) -> str:
        """
        Скомпилировать системный промпт для роутера (решение: RESPOND / IGNORE).
        
        Технический промпт без personality — только логика.
        """
        prompt = (
            "You are a technical routing system. You have NO personality.\n"
            "Your job is to decide: should this persona RESPOND to the message or IGNORE it.\n\n"
            "RULES:\n"
            "1. RESPOND if: message is directed at the persona, matches triggers, or is a DM.\n"
            "2. IGNORE if: message is off-topic, spam, from a bot, or persona spoke recently.\n"
            "3. Output JSON: {\"decision\": \"RESPOND\"|\"IGNORE\", \"confidence\": 0.0-1.0, \"reason\": \"...\", \"keywords\": [...]}\n\n"
        )
        
        if self.vibe and self.vibe.taboos:
            prompt += f"TABOO TOPICS (always IGNORE): {', '.join(self.vibe.taboos)}\n\n"
        
        if self.behavior and self.behavior.routing_style:
            prompt += f"ROUTING STYLE: {self.behavior.routing_style}\n"
        
        return prompt
    
    def compile_examples_for_prompt(self) -> str:
        """Скомпилировать few-shot примеры для промпта."""
        if not self.response_examples:
            return ""
        
        text = "\n=== EXAMPLES OF CORRECT RESPONSES ===\n"
        for ex in self.response_examples[:10]:  # limit
            text += f'\nUser: "{ex.trigger}"\n'
            text += f"BAD:  {ex.bad_response}\n"
            text += f"GOOD: {ex.good_response}\n"
        
        text += "\nALWAYS follow the GOOD pattern. NEVER use the BAD pattern.\n"
        return text
    
    def _build_identity_block(self) -> str:
        """Блок идентификации."""
        if not self.vibe:
            return ""
        
        parts = ["=== IDENTITY ==="]
        
        if self.vibe.role:
            parts.append(f"Role: {self.vibe.role}")
        if self.vibe.personality:
            parts.append(f"Personality: {self.vibe.personality}")
        if self.vibe.backstory:
            parts.append(f"Backstory: {self.vibe.backstory}")
        if self.vibe.voice:
            parts.append(f"Voice: {self.vibe.voice}")
        if self.vibe.core_emotions:
            parts.append(f"Core Emotions: {', '.join(self.vibe.core_emotions)}")
        if self.vibe.values:
            parts.append(f"Values: {', '.join(self.vibe.values)}")
        if self.vibe.taboos:
            parts.append(f"Taboos: {', '.join(self.vibe.taboos)}")
        
        return "\n".join(parts)
    
    def _build_context_block(self, user_context: str, chat_context: str) -> str:
        """Блок контекста."""
        parts = ["=== CONTEXT ==="]
        
        if self.behavior and self.behavior.routing_style:
            parts.append(f"Communication Style: {self.behavior.routing_style}")
        
        if user_context:
            parts.append(f"USER MEMORY:\n{user_context}")
        
        if chat_context:
            parts.append(f"CHAT CONTEXT:\n{chat_context}")
        
        if len(parts) == 1:
            return ""
        
        return "\n".join(parts)
    
    def _build_behavior_block(self, tool_status: str) -> str:
        """Блок поведения — выбирает нужный handler."""
        if not self.behavior:
            return ""
        
        parts = ["=== BEHAVIOR ==="]
        
        # Always behavior (v3)
        if self.behavior.always:
            parts.append(f"ALWAYS: {self.behavior.always.strip()}")
        
        # Selective behavior based on status
        behavior_map = {
            "success": self.behavior.on_tool_success,
            "no_results": self.behavior.on_tool_no_results,
            "error": self.behavior.on_tool_error,
            "none": self.behavior.on_offtopic,
            "greeting": self.behavior.on_greeting,
            "dm": self.behavior.on_dm,
            "food": self.behavior.on_food_question,
            "bot": self.behavior.on_bot_question,
            "taboo": self.behavior.on_taboo,
            "disengage": self.behavior.on_disengage,
            "price_query": self.behavior.on_price_query,
            "price_shock": self.behavior.on_price_shock,
        }
        
        # Primary instruction based on tool_status
        if tool_status in behavior_map and behavior_map[tool_status]:
            parts.append(f"INSTRUCTION (Status: {tool_status.upper()}): {behavior_map[tool_status].strip()}")
        
        # Also include all other non-empty behaviors for reference
        for status, instruction in behavior_map.items():
            if instruction and status != tool_status:
                parts.append(f"On {status}: {instruction.strip()}")
        
        return "\n".join(parts)
    
    def _build_examples_block(self) -> str:
        """Блок примеров."""
        if not self.response_examples:
            return ""
        
        parts = ["=== RESPONSE EXAMPLES ==="]
        for ex in self.response_examples[:8]:
            parts.append(f'\nTrigger: "{ex.trigger}"')
            parts.append(f"BAD:  {ex.bad_response[:200]}")
            parts.append(f"GOOD: {ex.good_response[:200]}")
        
        parts.append("\nALWAYS follow GOOD pattern. NEVER use BAD pattern.")
        return "\n".join(parts)
    
    def _build_factuality_block(self) -> str:
        """Блок фактичности — предотвращение галлюцинаций."""
        return (
            "=== FACTUALITY ===\n"
            "Never invent or guess product facts: price, ingredients, availability.\n"
            "If you don't know — say so honestly. Better to say 'не знаю' than to lie.\n"
            "Be grounded in reality. This is a real conversation, not a marketing pitch."
        )
    
    def _build_format_block(self) -> str:
        """Блок форматирования."""
        return (
            "=== FORMAT ===\n"
            "Write in Russian. Keep messages short (1-3 sentences).\n"
            "No markdown. No emojis overload. Natural casual style.\n"
            "If directed at you — reply directly. If not — maybe just react."
        )
    
    def _estimate_tokens(self, text: str) -> int:
        """Грубая оценка токенов."""
        return len(text) // 4
