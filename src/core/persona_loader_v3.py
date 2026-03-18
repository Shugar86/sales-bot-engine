"""
Persona Loader v3 — Pydantic-based persona configuration with validation.

Адаптировано из ai-tutor-engine/src/agents/personas/loader.py

Загружает persona YAML и валидирует через Pydantic модели.
Совместим с существующим persona_manager.py (legacy dataclass формат).
"""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .vibe_schema import (
    VibePersona,
    VibeBehavior,
    AntiSpamConfig,
    MemoryConfig,
    ResponseExample,
    RouterExample,
    GreetingPolicy,
    OutputValidators,
    ContextPolicy,
)

logger = logging.getLogger("persona-loader")

try:
    from ..utils.logger import get_logger
    logger = get_logger("persona-loader")
except Exception:
    pass


class TriggerConfigV3(BaseModel):
    """Триггер для ответа."""
    keywords: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    probability: float = 0.3

    model_config = ConfigDict(extra="ignore")


class IgnoreConfigV3(BaseModel):
    """Паттерн для игнорирования."""
    contains: list[str] = Field(default_factory=list)
    from_bot: bool = True

    model_config = ConfigDict(extra="ignore")


class GroupModeConfigV3(BaseModel):
    """Настройки группового режима."""
    max_messages_per_hour: int = 3
    probability_to_respond: float = 0.3
    style: str = "экспертный, ненавязчивый"

    model_config = ConfigDict(extra="ignore")


class DMFunnelStepV3(BaseModel):
    """Шаг воронки DM."""
    step: str = ""
    trigger: str = ""

    model_config = ConfigDict(extra="ignore")


class DMModeConfigV3(BaseModel):
    """Настройки DM режима."""
    greeting: str = ""
    funnel: list[DMFunnelStepV3] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class ProductConfigV3(BaseModel):
    """Конфигурация продукта."""
    name: str = ""
    price: str = ""
    link: str = ""
    description: str = ""

    model_config = ConfigDict(extra="ignore")


class PersonaConfigV3(BaseModel):
    """
    Полная конфигурация персонажа (v3 Pydantic).
    
    Загружается из persona.yaml и валидируется.
    Совместим с существующим persona_manager.PersonaConfig (dataclass).
    """
    name: str
    platform: str = "telegram"
    account_type: str = "userbot"
    session_name: str = ""
    personality: str = ""
    
    # Auth
    phone: str = ""
    bot_token: str = ""
    api_id: int = 0
    api_hash: str = ""
    vk_token: str = ""
    
    # Groups
    groups_to_monitor: list[str] = Field(default_factory=list)
    
    # VibeCraft models
    vibe: Optional[VibePersona] = None
    behavior: Optional[VibeBehavior] = None
    
    # Product
    product: ProductConfigV3 = Field(default_factory=ProductConfigV3)
    
    # Triggers
    respond_triggers: list[TriggerConfigV3] = Field(default_factory=list)
    ignore_triggers: list[IgnoreConfigV3] = Field(default_factory=list)
    
    # Conversation
    group_mode: GroupModeConfigV3 = Field(default_factory=GroupModeConfigV3)
    dm_mode: DMModeConfigV3 = Field(default_factory=DMModeConfigV3)
    
    # Anti-spam
    anti_spam: AntiSpamConfig = Field(default_factory=AntiSpamConfig)
    
    # Memory
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    
    # Response examples
    response_examples: list[ResponseExample] = Field(default_factory=list)
    competitor_knowledge: str = ""
    group_context_examples: list[ResponseExample] = Field(default_factory=list)
    
    # Knowledge base
    knowledge_file: str = ""
    examples_file: str = ""
    
    # Models
    router_model: str = "openrouter/google/gemini-2.0-flash-lite"
    generator_model: str = "openrouter/hunter-alpha"
    
    # Path
    yaml_path: str = ""

    model_config = ConfigDict(extra="ignore")
    
    @field_validator("name", mode="after")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Persona name cannot be empty")
        return value.strip()


class PersonaLoaderV3:
    """Загрузчик persona конфигурации из YAML (Pydantic-based)."""
    
    @staticmethod
    def load(yaml_path: str) -> PersonaConfigV3:
        """
        Загрузить persona из YAML файла.
        
        Args:
            yaml_path: Путь к persona.yaml
        
        Returns:
            PersonaConfigV3
        
        Raises:
            ValueError: Если YAML невалидный
        """
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Persona file not found: {yaml_path}")
        
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        
        if not isinstance(raw, dict):
            raise ValueError("Persona YAML must be a dict")
        
        persona_data = raw.get("persona", raw)
        
        # Parse vibe
        vibe_data = persona_data.get("vibe")
        vibe = VibePersona(**vibe_data) if vibe_data else None
        
        # Parse behavior
        behavior_data = persona_data.get("behavior")
        if behavior_data:
            # Parse nested models
            if "greeting_policy" in behavior_data:
                behavior_data["greeting_policy"] = GreetingPolicy(**behavior_data["greeting_policy"])
            if "validators" in behavior_data:
                behavior_data["validators"] = OutputValidators(**behavior_data["validators"])
            if "context_policy" in behavior_data:
                behavior_data["context_policy"] = ContextPolicy(**behavior_data["context_policy"])
            behavior = VibeBehavior(**behavior_data)
        else:
            behavior = None
        
        # Parse triggers
        triggers = persona_data.get("triggers", {})
        respond_triggers = [
            TriggerConfigV3(**t) for t in triggers.get("respond_when", [])
        ]
        ignore_triggers = [
            IgnoreConfigV3(**t) for t in triggers.get("ignore_when", [])
        ]
        
        # Parse conversation flow
        cf = persona_data.get("conversation_flow", {})
        gm = cf.get("group_mode", {})
        group_mode = GroupModeConfigV3(
            max_messages_per_hour=gm.get("max_messages_per_hour", 3),
            probability_to_respond=gm.get("probability_to_respond", 0.3),
            style=gm.get("style", "экспертный, ненавязчивый"),
        )
        dm = cf.get("dm_mode", {})
        dm_mode = DMModeConfigV3(
            greeting=dm.get("greeting", ""),
            funnel=[DMFunnelStepV3(**s) for s in dm.get("funnel", [])],
        )
        
        # Parse anti-spam
        sp = persona_data.get("anti_spam", {})
        anti_spam = AntiSpamConfig(**sp) if sp else AntiSpamConfig()
        
        # Parse memory
        mem = persona_data.get("memory", {})
        memory = MemoryConfig(**mem) if mem else MemoryConfig()
        
        # Parse product
        prod = persona_data.get("product", {})
        product = ProductConfigV3(**prod) if prod else ProductConfigV3()
        
        # Parse response examples
        response_examples = [
            ResponseExample(**ex) for ex in persona_data.get("response_examples", [])
        ]
        
        # Parse group context examples
        group_context_examples = [
            ResponseExample(**ex) for ex in persona_data.get("group_context_examples", [])
        ]
        
        config = PersonaConfigV3(
            name=persona_data.get("name", "Unknown"),
            platform=persona_data.get("platform", "telegram"),
            account_type=persona_data.get("account_type", "userbot"),
            session_name=persona_data.get("session_name", ""),
            personality=persona_data.get("personality", ""),
            phone=persona_data.get("phone", ""),
            bot_token=persona_data.get("bot_token", ""),
            api_id=persona_data.get("api_id", 0),
            api_hash=persona_data.get("api_hash", ""),
            vk_token=persona_data.get("vk_token", ""),
            groups_to_monitor=persona_data.get("groups_to_monitor", []),
            vibe=vibe,
            behavior=behavior,
            product=product,
            respond_triggers=respond_triggers,
            ignore_triggers=ignore_triggers,
            group_mode=group_mode,
            dm_mode=dm_mode,
            anti_spam=anti_spam,
            memory=memory,
            response_examples=response_examples,
            competitor_knowledge=persona_data.get("competitor_knowledge", ""),
            group_context_examples=group_context_examples,
            knowledge_file=persona_data.get("knowledge_file", ""),
            examples_file=persona_data.get("examples_file", ""),
            router_model=persona_data.get("router_model", "openrouter/google/gemini-2.0-flash-lite"),
            generator_model=persona_data.get("generator_model", "openrouter/hunter-alpha"),
            yaml_path=yaml_path,
        )
        
        logger.info(f"Loaded persona v3: {config.name} ({config.platform}/{config.account_type})")
        return config
    
    @staticmethod
    def discover(personas_dir: str) -> list[PersonaConfigV3]:
        """Найти и загрузить все persona из директории."""
        personas = []
        pdir = Path(personas_dir)
        
        if not pdir.exists():
            return personas
        
        for subdir in pdir.iterdir():
            if not subdir.is_dir():
                continue
            yaml_file = subdir / "persona.yaml"
            if yaml_file.exists():
                try:
                    persona = PersonaLoaderV3.load(str(yaml_file))
                    personas.append(persona)
                except Exception as e:
                    logger.error(f"Failed to load {yaml_file}: {e}")
        
        return personas
