"""
Persona Manager — loads YAML contracts, manages multiple personas.
Each persona = 1 product + 1 account + 1 set of groups.

v3: Rebuilt with Pydantic models (VibePersona, VibeBehavior, GreetingPolicy,
OutputValidators, ContextPolicy) from vibe_schema.py
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Callable, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .vibe_schema import (
    VibePersona, VibeBehavior, GreetingPolicy, AntiSpamConfig, MemoryConfig, ResponseExample,
)
from ..utils.logger import get_logger
from ..models.message import IncomingMessage

logger = get_logger("persona-manager")


# ─── Pydantic Models ────────────────────────────────────────────────

class TriggerConfig(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="ignore")


class IgnoreConfig(BaseModel):
    contains: list[str] = Field(default_factory=list)
    from_bot: bool = True
    min_message_length: int = 3
    model_config = ConfigDict(extra="ignore")


class GroupModeConfig(BaseModel):
    max_messages_per_hour: int = 3
    style: str = "экспертный, ненавязчивый"
    model_config = ConfigDict(extra="ignore")


class DMFunnelStep(BaseModel):
    step: str = ""
    trigger: str = ""
    model_config = ConfigDict(extra="ignore")


class DMModeConfig(BaseModel):
    """DM copy and optional funnel *hints* for prompts (not a state machine in runtime)."""

    greeting: str = ""
    funnel: list[DMFunnelStep] = Field(default_factory=list)
    model_config = ConfigDict(extra="ignore")


class ProductConfig(BaseModel):
    name: str = ""
    price: str = ""
    link: str = ""
    description: str = ""
    model_config = ConfigDict(extra="ignore")


class PersonaConfig(BaseModel):
    """
    Full persona configuration — Pydantic validated.
    Integrates VibePersona, VibeBehavior, GreetingPolicy from vibe_schema.
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
    product: ProductConfig = Field(default_factory=ProductConfig)

    # Triggers
    respond_triggers: list[TriggerConfig] = Field(default_factory=list)
    ignore_triggers: list[IgnoreConfig] = Field(default_factory=list)

    # Conversation
    group_mode: GroupModeConfig = Field(default_factory=GroupModeConfig)
    dm_mode: DMModeConfig = Field(default_factory=DMModeConfig)

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

    @property
    def greeting_policy(self) -> Optional[GreetingPolicy]:
        """Convenience access to greeting policy from behavior."""
        if self.behavior and self.behavior.greeting_policy:
            return self.behavior.greeting_policy
        return None

    # Backward-compatible product accessors
    @property
    def product_name(self) -> str:
        return self.product.name

    @property
    def product_price(self) -> str:
        return self.product.price

    @property
    def product_link(self) -> str:
        return self.product.link

    @property
    def product_description(self) -> str:
        return self.product.description

    @field_validator("name", mode="after")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Persona name cannot be empty")
        return value.strip()


# ─── Env overlays (secrets must not live in git-tracked YAML) ───────


def _persona_env_prefix(config: PersonaConfig) -> str:
    """Build env var prefix from ``session_name`` or ``name`` (e.g. ``kormoved`` → ``KORMOVED``)."""
    raw = (config.session_name or config.name or "persona").strip()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", raw).upper().strip("_")
    return slug or "PERSONA"


def apply_env_overrides_to_persona(config: PersonaConfig) -> None:
    """Overlay phone/tokens/API credentials from environment.

    Per-persona variables use ``{PREFIX}_*`` where PREFIX is derived from
    ``session_name`` in uppercase (e.g. ``KORMOVED_PHONE``). Shared Telegram
    app credentials can be set via ``TELEGRAM_API_ID`` / ``TELEGRAM_API_HASH``.
    """
    prefix = _persona_env_prefix(config)

    def prefixed(suffix: str) -> str | None:
        val = os.getenv(f"{prefix}_{suffix}")
        if val is not None and str(val).strip() != "":
            return str(val).strip()
        return None

    phone = prefixed("PHONE")
    if phone:
        config.phone = phone

    bot_token = prefixed("BOT_TOKEN")
    if bot_token:
        config.bot_token = bot_token

    vk_token = prefixed("VK_TOKEN")
    if vk_token:
        config.vk_token = vk_token

    api_id_raw = prefixed("API_ID") or os.getenv("TELEGRAM_API_ID")
    if api_id_raw and str(api_id_raw).strip().isdigit():
        config.api_id = int(str(api_id_raw).strip())

    api_hash = prefixed("API_HASH") or os.getenv("TELEGRAM_API_HASH")
    if api_hash:
        config.api_hash = api_hash


# ─── Loader ─────────────────────────────────────────────────────────

def load_persona(yaml_path: str) -> PersonaConfig:
    """Load persona from YAML file — Pydantic validated."""
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

    # Parse behavior with nested models
    behavior_data = persona_data.get("behavior")
    behavior = None
    if behavior_data:
        bd = dict(behavior_data)
        # greeting_policy can be nested inside behavior OR at persona level
        gp = bd.pop("greeting_policy", None) or persona_data.get("greeting_policy")
        if gp:
            bd["greeting_policy"] = GreetingPolicy(**gp)
        behavior = VibeBehavior(**bd)

    # Parse triggers
    respond_triggers = [
        TriggerConfig(**t) for t in persona_data.get("triggers", {}).get("respond_when", [])
    ]
    ignore_triggers = [
        IgnoreConfig(**t) for t in persona_data.get("triggers", {}).get("ignore_when", [])
    ]

    # Parse conversation flow
    cf = persona_data.get("conversation_flow", {})
    gm = cf.get("group_mode", {})
    group_mode = GroupModeConfig(
        max_messages_per_hour=gm.get("max_messages_per_hour", 3),
        style=gm.get("style", "экспертный, ненавязчивый"),
    )
    dm = cf.get("dm_mode", {})
    dm_mode = DMModeConfig(
        greeting=dm.get("greeting", ""),
        funnel=[DMFunnelStep(**s) for s in dm.get("funnel", [])],
    )

    # Parse anti-spam
    sp = persona_data.get("anti_spam", {})
    anti_spam = AntiSpamConfig(**sp) if sp else AntiSpamConfig()

    # Parse memory
    mem = persona_data.get("memory", {})
    memory = MemoryConfig(**mem) if mem else MemoryConfig()

    # Parse product
    prod = persona_data.get("product", {})
    product = ProductConfig(**prod) if prod else ProductConfig()

    # Parse examples
    response_examples = [
        ResponseExample(**ex) for ex in persona_data.get("response_examples", [])
    ]
    group_context_examples = [
        ResponseExample(**ex) for ex in persona_data.get("group_context_examples", [])
    ]

    config = PersonaConfig(
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

    apply_env_overrides_to_persona(config)

    logger.info(f"Loaded persona: {config.name} ({config.platform}/{config.account_type})")
    return config


def discover_personas(personas_dir: str) -> list[PersonaConfig]:
    """Discover all persona YAML files in directory."""
    personas = []
    personas_path = Path(personas_dir)

    if not personas_path.exists():
        logger.warning(f"Personas directory not found: {personas_dir}")
        return personas

    for subdir in personas_path.iterdir():
        if not subdir.is_dir():
            continue
        yaml_file = subdir / "persona.yaml"
        if yaml_file.exists():
            try:
                persona = load_persona(str(yaml_file))
                personas.append(persona)
            except Exception as e:
                logger.error(f"Failed to load {yaml_file}: {e}")

    return personas


# ─── Manager ────────────────────────────────────────────────────────

class PersonaManager:
    """Manages multiple personas running in parallel."""

    def __init__(self, personas_dir: str = "./personas"):
        self.personas_dir = personas_dir
        self.personas: list[PersonaConfig] = []
        self._tasks: dict[str, asyncio.Task] = {}

    def load_all(self) -> list[PersonaConfig]:
        """Load all personas from directory."""
        self.personas = discover_personas(self.personas_dir)
        logger.info(f"Loaded {len(self.personas)} personas")
        return self.personas

    def get_persona(self, name: str) -> Optional[PersonaConfig]:
        """Get persona by name."""
        for p in self.personas:
            if p.name == name:
                return p
        return None

    async def run_persona(self, persona: PersonaConfig, handler: Callable):
        """Run a single persona via the platform registry (same as orchestrator)."""
        from ..platforms import UnknownPlatformError, create_adapter

        logger.info(f"Starting persona: {persona.name} on {persona.platform}")
        try:
            adapter = await create_adapter(persona)

            async def callback(msg: IncomingMessage) -> None:
                await handler(msg, persona)

            await adapter.run(callback=callback, allowed_chats=persona.groups_to_monitor)
        except UnknownPlatformError as e:
            logger.error(str(e))
        except Exception as e:
            logger.error(f"Persona {persona.name} crashed: {e}")

    async def run_all(self, handler: Callable):
        """Run all personas in parallel."""
        self.load_all()
        tasks = []
        for persona in self.personas:
            task = asyncio.create_task(
                self.run_persona(persona, handler),
                name=f"persona-{persona.name}"
            )
            tasks.append(task)
            self._tasks[persona.name] = task

        logger.info(f"Running {len(tasks)} personas in parallel")
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception():
                logger.error(f"Persona task {task.get_name()} crashed: {task.exception()}")
        for task in pending:
            task.cancel()

    async def stop_all(self):
        """Stop all running personas."""
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()
                logger.info(f"Stopped persona: {name}")
        self._tasks.clear()
