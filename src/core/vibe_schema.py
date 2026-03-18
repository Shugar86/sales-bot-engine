"""
Vibe Schema — Pydantic модели для persona configuration (v3.1).

Скопировано и адаптировано из ai-tutor-engine/src/agents/personas/vibe_schema.py
"""

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class VibePersona(BaseModel):
    """Статический эмоциональный/идентификационный профиль персоны."""
    role: str
    personality: str = ""           # v3: краткое описание character
    backstory: str = ""             # v3: предыстория
    voice: str = ""
    core_emotions: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    taboos: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class GreetingPolicy(BaseModel):
    """Правила приветствия."""
    enabled: bool = True
    greet_only_first_response: bool = True
    greet_only_if_user_greeted: bool = True
    strip_greeting_if_not_allowed: bool = True
    greeting_variants: list[str] = Field(default_factory=list)
    fallback_variants: list[str] = Field(
        default_factory=lambda: [
            "Я на связи. Чем помочь?",
            "Слушаю тебя!",
            "Что нужно?",
        ]
    )

    model_config = ConfigDict(extra="ignore")


class OutputValidators(BaseModel):
    """Валидаторы для предотвращения нежелательных фраз."""
    banned_phrases: list[str] = Field(default_factory=list)
    forbid_markdown_links_in_wrapper: bool = True

    model_config = ConfigDict(extra="ignore")


class ContextPolicy(BaseModel):
    """Правила namespace-based контекста."""
    namespace: Optional[str] = None
    keep_keys: list[str] = Field(default_factory=list)
    ttl_turns: int = 10

    model_config = ConfigDict(extra="ignore")


class PreprocessRules(BaseModel):
    """Детерминированные правила до роутинга."""
    greeting_enabled: bool = True
    followup_reuse_tools: list[str] = Field(default_factory=list)
    slot_fill_rules: dict[str, list[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class VibeBehavior(BaseModel):
    """Поведенческие паттерны для разных ситуаций."""
    on_greeting: str = ""
    on_tool_success: str = ""
    on_tool_no_results: str = ""
    on_tool_error: str = ""
    on_offtopic: str = ""
    on_price_query: str = ""
    on_price_shock: str = ""
    on_dm: str = ""                     # v3: поведение в ЛС
    on_food_question: str = ""          # v3: вопрос про еду
    on_bot_question: str = ""           # v3: вопрос "ты бот?"
    on_taboo: str = ""                  # v3: табуированная тема
    on_disengage: str = ""              # v3: отступить
    always: str = ""                    # v3: общее поведение
    routing_style: Optional[str] = None

    greeting_policy: Optional[GreetingPolicy] = None
    validators: Optional[OutputValidators] = None
    context_policy: Optional[ContextPolicy] = None
    preprocess_rules: Optional[PreprocessRules] = None

    model_config = ConfigDict(extra="ignore")


class AntiSpamConfig(BaseModel):
    """Конфигурация anti-spam."""
    min_delay_between_messages: int = 30
    max_delay_between_messages: int = 300
    leave_on_read: float = 0.35
    emoji_reaction: float = 0.15
    night_slowdown: float = 3.0
    night_start: int = 23
    night_end: int = 8
    typing_simulation: bool = True
    random_typos: bool = False

    model_config = ConfigDict(extra="ignore")


class MemoryConfig(BaseModel):
    """Конфигурация памяти."""
    remember: list[str] = Field(default_factory=list)
    reference_past: bool = True
    track_funnel: bool = True

    model_config = ConfigDict(extra="ignore")


class ResponseExample(BaseModel):
    """Пара хороший/плохой ответ для обучения персоны."""
    trigger: str = ""
    bad_response: str = ""
    good_response: str = ""

    model_config = ConfigDict(extra="ignore")


class RouterExample(BaseModel):
    """Few-shot пример для роутера."""
    context: list[dict[str, str]] = Field(default_factory=list)
    user_query: str = ""
    expected_tool: str = ""
    expected_args: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")
