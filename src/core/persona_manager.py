"""
Persona Manager — loads YAML contracts, manages multiple personas.
Each persona = 1 product + 1 account + 1 set of groups.
"""

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from ..utils.logger import get_logger

logger = get_logger("persona-manager")


@dataclass
class TriggerConfig:
    keywords: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    probability: float = 0.3


@dataclass
class IgnoreConfig:
    contains: list[str] = field(default_factory=list)
    from_bot: bool = True
    min_message_length: int = 3


@dataclass
class GroupModeConfig:
    max_messages_per_hour: int = 3
    probability_to_respond: float = 0.3
    style: str = "экспертный, ненавязчивый"


@dataclass
class DMFunnelStep:
    step: str = ""


@dataclass
class DMModeConfig:
    greeting: str = ""
    funnel: list[DMFunnelStep] = field(default_factory=list)


@dataclass
class ResponseExample:
    """Good/bad response pair for training the persona."""
    trigger: str = ""
    bad_response: str = ""
    good_response: str = ""


@dataclass
class AntiSpamConfig:
    min_delay_between_messages: int = 30    # was 120 → human-realistic
    max_delay_between_messages: int = 300   # was 600 → 5min max
    typing_simulation: bool = True
    random_typos: bool = False


@dataclass
class PersonaConfig:
    """Full persona configuration."""
    name: str
    platform: str = "telegram"  # telegram | vk | pikabu
    account_type: str = "userbot"  # userbot | bot
    
    # Auth
    phone: str = ""
    bot_token: str = ""
    api_id: int = 0
    api_hash: str = ""
    vk_token: str = ""
    session_name: str = ""
    
    # Content
    personality: str = ""
    groups_to_monitor: list[str] = field(default_factory=list)
    
    # Product
    product_name: str = ""
    product_price: str = ""
    product_link: str = ""
    product_description: str = ""
    
    # Triggers
    respond_triggers: list[TriggerConfig] = field(default_factory=list)
    ignore_triggers: list[IgnoreConfig] = field(default_factory=list)
    
    # Conversation
    group_mode: GroupModeConfig = field(default_factory=GroupModeConfig)
    dm_mode: DMModeConfig = field(default_factory=DMModeConfig)
    
    # Anti-spam
    anti_spam: AntiSpamConfig = field(default_factory=AntiSpamConfig)
    
    # Response examples (for Turing test quality)
    response_examples: list[ResponseExample] = field(default_factory=list)
    
    # Competitor knowledge (for natural conversation about competitors)
    competitor_knowledge: str = ""
    
    # Knowledge base
    knowledge_file: str = ""
    examples_file: str = ""
    
    # Model
    router_model: str = "openrouter/google/gemini-2.0-flash-lite"
    generator_model: str = "openrouter/hunter-alpha"
    
    # Path to YAML file
    yaml_path: str = ""


def load_persona(yaml_path: str) -> PersonaConfig:
    """Load persona from YAML file."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    persona_data = data.get("persona", data)
    
    # Parse triggers
    respond_triggers = []
    for t in persona_data.get("triggers", {}).get("respond_when", []):
        respond_triggers.append(TriggerConfig(
            keywords=t.get("keywords", []),
            topics=t.get("topics", []),
            probability=t.get("probability", 0.3),
        ))
    
    ignore_triggers = []
    for t in persona_data.get("triggers", {}).get("ignore_when", []):
        ignore_triggers.append(IgnoreConfig(
            contains=t.get("contains", []),
            from_bot=t.get("from_bot", True),
        ))
    
    # Parse conversation flow
    gm = persona_data.get("conversation_flow", {}).get("group_mode", {})
    group_mode = GroupModeConfig(
        max_messages_per_hour=gm.get("max_messages_per_hour", 3),
        probability_to_respond=gm.get("probability_to_respond", 0.3),
        style=gm.get("style", "экспертный, ненавязчивый"),
    )
    
    dm = persona_data.get("conversation_flow", {}).get("dm_mode", {})
    dm_funnel = [DMFunnelStep(step=s.get("step", "")) for s in dm.get("funnel", [])]
    dm_mode = DMModeConfig(
        greeting=dm.get("greeting", ""),
        funnel=dm_funnel,
    )
    
    # Parse anti-spam
    sp = persona_data.get("anti_spam", {})
    anti_spam = AntiSpamConfig(
        min_delay_between_messages=sp.get("min_delay_between_messages", 30),
        max_delay_between_messages=sp.get("max_delay_between_messages", 300),
        typing_simulation=sp.get("typing_simulation", True),
        random_typos=sp.get("random_typos", False),
    )
    
    # Parse response examples
    response_examples = []
    for ex in persona_data.get("response_examples", []):
        response_examples.append(ResponseExample(
            trigger=ex.get("trigger", ""),
            bad_response=ex.get("bad_response", ""),
            good_response=ex.get("good_response", ""),
        ))
    
    # Parse competitor knowledge
    competitor_knowledge = persona_data.get("competitor_knowledge", "")
    
    # Product
    prod = persona_data.get("product", {})
    
    config = PersonaConfig(
        name=persona_data.get("name", "Unknown"),
        platform=persona_data.get("platform", "telegram"),
        account_type=persona_data.get("account_type", "userbot"),
        phone=persona_data.get("phone", ""),
        bot_token=persona_data.get("bot_token", ""),
        api_id=persona_data.get("api_id", 0),
        api_hash=persona_data.get("api_hash", ""),
        vk_token=persona_data.get("vk_token", ""),
        session_name=persona_data.get("session_name", ""),
        personality=persona_data.get("personality", ""),
        groups_to_monitor=persona_data.get("groups_to_monitor", []),
        product_name=prod.get("name", ""),
        product_price=prod.get("price", ""),
        product_link=prod.get("link", ""),
        product_description=prod.get("description", ""),
        respond_triggers=respond_triggers,
        ignore_triggers=ignore_triggers,
        group_mode=group_mode,
        dm_mode=dm_mode,
        anti_spam=anti_spam,
        knowledge_file=persona_data.get("knowledge_file", ""),
        examples_file=persona_data.get("examples_file", ""),
        router_model=persona_data.get("router_model", "openrouter/google/gemini-2.0-flash-lite"),
        generator_model=persona_data.get("generator_model", "openrouter/hunter-alpha"),
        response_examples=response_examples,
        competitor_knowledge=competitor_knowledge,
        yaml_path=yaml_path,
    )
    
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
                logger.info(f"Discovered persona: {persona.name} in {subdir.name}")
            except Exception as e:
                logger.error(f"Failed to load persona from {yaml_file}: {e}")
    
    return personas


class PersonaManager:
    """
    Manages multiple personas running in parallel.
    Each persona gets its own monitor + responder + memory.
    """
    
    def __init__(self, personas_dir: str = "./personas"):
        self.personas_dir = personas_dir
        self.personas: list[PersonaConfig] = []
        self._tasks: dict[str, asyncio.Task] = {}
    
    def load_all(self):
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
        """
        Run a single persona's monitor.
        
        Args:
            persona: Persona configuration
            handler: async function (message, persona_config) -> response | None
        """
        logger.info(f"Starting persona: {persona.name} on {persona.platform}")
        
        try:
            if persona.platform == "telegram":
                await self._run_telegram_persona(persona, handler)
            elif persona.platform == "vk":
                await self._run_vk_persona(persona, handler)
            else:
                logger.error(f"Unknown platform: {persona.platform}")
        except Exception as e:
            logger.error(f"Persona {persona.name} crashed: {e}")
    
    async def _run_telegram_persona(self, persona: PersonaConfig, handler: Callable):
        """Run Telegram persona."""
        from ..monitors.telegram_userbot import TelegramUserbot
        
        if persona.account_type == "userbot":
            bot = TelegramUserbot(
                session_name=persona.session_name or persona.name.lower(),
                api_id=persona.api_id or None,
                api_hash=persona.api_hash or None,
                phone=persona.phone or None,
            )
            
            async def callback(msg):
                await handler(msg, persona)
            
            await bot.run(callback=callback, allowed_chats=persona.groups_to_monitor)
        
        elif persona.account_type == "bot":
            from ..monitors.telegram_monitor import TelegramMonitor
            bot = TelegramMonitor(bot_token=persona.bot_token)
            
            async def callback(msg):
                await handler(msg, persona)
            
            await bot.poll_loop(callback=callback, allowed_chats=persona.groups_to_monitor)
    
    async def _run_vk_persona(self, persona: PersonaConfig, handler: Callable):
        """Run VK persona."""
        from ..monitors.vk_monitor import VKMonitorAsync
        
        monitor = VKMonitorAsync(access_token=persona.vk_token)
        await monitor.start()
        
        async def callback(msg):
            await handler(msg, persona)
        
        await monitor.run(callback=callback, allowed_chats=persona.groups_to_monitor)
    
    async def run_all(self, handler: Callable):
        """
        Run all personas in parallel.
        
        Args:
            handler: async function (message, persona_config) -> response | None
        """
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
        
        # Wait for all (or first crash)
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        # Log crashes
        for task in done:
            if task.exception():
                logger.error(f"Persona task {task.get_name()} crashed: {task.exception()}")
        
        # Cancel remaining
        for task in pending:
            task.cancel()
    
    async def stop_all(self):
        """Stop all running personas."""
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()
                logger.info(f"Stopped persona: {name}")
        self._tasks.clear()
