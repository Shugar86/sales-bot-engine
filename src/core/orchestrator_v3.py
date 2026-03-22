"""
EXPERIMENTAL: Orchestrator v3 — "Живой человек" Pipeline

⚠️  STATUS: EXPERIMENTAL — not wired into main.py
   This is a research branch for future architecture ideas.
   DO NOT use in production. See orchestrator_v2.py for canonical runtime.

Pipeline:
  1. RECEIVE    — New message from monitor
  2. CONTEXT    — Read last 10 messages in chat (context_reader)
  3. VIBE CHECK — Is this in my vibe? (vibe_checker)
  4. MEMORY     — Do I know this person? What did we talk about? (memory_writer)
  5. DECIDE     — respond / react / leave_read / disengage / wait (decision_gate)
  6. GENERATE   — Response = personal story + chat context + personality (generator)
  7. HUMANIZE   — Typos, casual, typing delay (existing humanizer)
  8. SEND       — Send as reply if directed at me
  9. REMEMBER   — Write to memory (memory_writer)

To activate: wire this class into src/main.py
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from ..core.context_reader import ContextReader, ChatContext
from ..core.vibe_checker import VibeChecker, VibeCheck
from ..core.decision_gate import DecisionGate, GateDecision
from ..core.memory_writer import MemoryWriter, UserMemoryProfile
from ..utils.logger import get_logger

logger = get_logger("orchestrator-v3")


@dataclass
class V3PipelineResult:
    """Результат работы v3 пайплайна."""
    action: str  # "respond" | "react" | "leave_read" | "disengage" | "wait"
    response_text: str = ""
    emoji: str = ""
    reply_to: int = 0
    delay_seconds: int = 0
    topic: str = ""
    vibe: str = ""
    vibe_check_confidence: float = 0.0
    decision_reason: str = ""
    user_memory_profile: Optional[UserMemoryProfile] = None


class OrchestratorV3:
    """
    Orchestrator v3 — живой человек пайплайн.
    
    Использует:
    - ContextReader для понимания контекста чата
    - VibeChecker для проверки "вайба"
    - DecisionGate для финального решения
    - MemoryWriter для памяти о пользователях
    """
    
    def __init__(
        self,
        persona_config: dict,
        memory_dir: str = "data/memory",
        my_user_id: str = "",
    ):
        """
        Args:
            persona_config: Конфигурация персонажа (из persona.yaml)
            memory_dir: Директория для памяти
            my_user_id: ID персоны в чате
        """
        self.config = persona_config
        self.my_user_id = my_user_id
        
        # Инициализация v3 компонентов
        self.context_reader = ContextReader(my_user_id=my_user_id)
        self.vibe_checker = VibeChecker(persona_config)
        self.decision_gate = DecisionGate(
            anti_spam_config=persona_config.get("anti_spam", {})
        )
        self.memory_writer = MemoryWriter(memory_dir=memory_dir)
        
        # Anti-spam трекинг
        self._message_counts: dict[str, list[int]] = {}  # chat_id -> [timestamps]
    
    async def process(
        self,
        message_text: str,
        chat_id: str,
        chat_title: str = "",
        user_id: str = "",
        username: str = "",
        display_name: str = "",
        message_id: int = 0,
        is_dm: bool = False,
        chat_messages: list[dict] = None,
        my_last_message_time: int = 0,
        reply_to_message_id: int = 0,
        is_reply_to_me: bool = False,
    ) -> V3PipelineResult:
        """
        Обработать сообщение через v3 пайплайн.
        
        Args:
            message_text: Текст сообщения
            chat_id: ID чата
            chat_title: Название чата
            user_id: ID отправителя
            username: Username отправителя
            display_name: Имя отправителя
            message_id: ID сообщения
            is_dm: Это ЛС?
            chat_messages: Последние сообщения чата для контекста
            my_last_message_time: Когда я последний раз писал
            reply_to_message_id: На какое сообщение ответ
            is_reply_to_me: Это ответ на моё сообщение?
        
        Returns:
            V3PipelineResult с решением.
        """
        # === STEP 1: CONTEXT ===
        messages_for_context = chat_messages or []
        
        # Добавляем текущее сообщение в контекст
        messages_for_context.append({
            "message_id": message_id,
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "text": message_text,
            "timestamp": self._now(),
            "reply_to_message_id": reply_to_message_id,
            "is_reply_to_me": is_reply_to_me,
        })
        
        context = self.context_reader.read_context(
            messages=messages_for_context,
            my_last_message_time=my_last_message_time,
        )
        
        # === STEP 2: VIBE CHECK ===
        vibe_check = self.vibe_checker.check(
            context=context,
            last_message_text=message_text,
            persona_name=self.config.get("name", ""),
        )
        
        logger.info(
            f"[V3] Vibe check: should_respond={vibe_check.should_respond}, "
            f"confidence={vibe_check.confidence:.1f}, "
            f"reason={vibe_check.reason}, "
            f"angle={vibe_check.suggested_angle}"
        )
        
        # === STEP 3: MEMORY — проверяем знаем ли пользователя ===
        user_profile = self.memory_writer.get_user_profile(user_id)
        user_context = self.memory_writer.get_user_context_for_prompt(user_id)
        
        # === STEP 4: DECIDE ===
        messages_in_last_hour = self._count_messages_in_hour(chat_id)
        max_per_hour = self.config.get("conversation_flow", {}).get(
            "group_mode", {}
        ).get("max_messages_per_hour", 3)
        
        decision = self.decision_gate.decide(
            vibe_check=vibe_check,
            context=context,
            is_dm=is_dm,
            messages_in_last_hour=messages_in_last_hour,
            max_messages_per_hour=max_per_hour,
        )
        
        logger.info(
            f"[V3] Decision: {decision.action}, "
            f"delay={decision.delay_seconds}s, "
            f"reason={decision.reason}"
        )
        
        # === STEP 5-9: обрабатываем решение ===
        result = V3PipelineResult(
            action=decision.action,
            emoji=decision.emoji or "",
            reply_to=decision.reply_to or 0,
            delay_seconds=decision.delay_seconds,
            topic=context.topic,
            vibe=context.vibe,
            vibe_check_confidence=vibe_check.confidence,
            decision_reason=decision.reason,
            user_memory_profile=user_profile,
        )
        
        return result
    
    def record_my_message(self, chat_id: str, user_id: str = ""):
        """Записать что я отправил сообщение (для anti-spam)."""
        import time
        now = int(time.time())
        self._message_counts.setdefault(chat_id, []).append(now)
        
        if user_id:
            self.memory_writer.record_my_message(user_id, chat_id)
    
    def record_interaction(
        self,
        user_id: str,
        username: str,
        display_name: str,
        chat_id: str,
        chat_title: str,
        user_message: str,
        bot_response: str,
        is_dm: bool = False,
        source_chat_id: str = "",
        source_chat_title: str = "",
        topic: str = "",
        funnel_stage: str = "",
    ):
        """Записать взаимодействие в память."""
        if is_dm:
            self.memory_writer.write_dm_interaction(
                user_id=user_id,
                username=username,
                display_name=display_name,
                user_message=user_message,
                bot_response=bot_response,
                source_chat_id=source_chat_id,
                source_chat_title=source_chat_title,
                funnel_stage=funnel_stage,
                topic=topic,
            )
        else:
            self.memory_writer.write_group_interaction(
                user_id=user_id,
                username=username,
                display_name=display_name,
                chat_id=chat_id,
                chat_title=chat_title,
                user_message=user_message,
                bot_response=bot_response,
                topic=topic,
            )
    
    def remember_detail(self, user_id: str, key: str, value: str):
        """Запомнить деталь о пользователе."""
        self.memory_writer.remember_detail(user_id, key, value)
    
    def update_funnel(self, user_id: str, stage: str):
        """Обновить стадию воронки."""
        self.memory_writer.update_funnel(user_id, stage)
    
    def _count_messages_in_hour(self, chat_id: str) -> int:
        """Посчитать сколько сообщений отправлено за последний час."""
        import time
        now = int(time.time())
        hour_ago = now - 3600
        
        timestamps = self._message_counts.get(chat_id, [])
        # Очищаем старые записи
        timestamps = [t for t in timestamps if t > hour_ago]
        self._message_counts[chat_id] = timestamps
        
        return len(timestamps)
    
    @staticmethod
    def _now() -> int:
        import time
        return int(time.time())
