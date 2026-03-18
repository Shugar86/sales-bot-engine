"""
Orchestrator v2 — Multi-Persona Sales Bot

Manages N personas running in parallel.
Each persona = 1 YAML contract + 1 platform account + 1 set of components.

Pipeline per message:
  Monitor → IncomingMessage → Dedup → Router → Generator → AntiSpam → Send → Memory
"""

import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from ..core.persona_manager import PersonaConfig, PersonaManager, discover_personas
from ..core.router import MessageRouter, Decision, RouteResult
from ..responders.generator import ResponseGenerator, GeneratedResponse
from ..monitors.telegram_userbot import TelegramUserbot, UserbotMessage
from ..monitors.telegram_monitor import TelegramMonitor, TelegramMessage
from ..monitors.vk_monitor import VKMonitorAsync, VKMessage
from ..monitors.anti_spam import RateLimiter, TypingSpeedCalculator
from ..memory.user_memory import UserMemoryStore
from ..responders.text_humanizer import humanize_text
from ..responders.chat_vibe import detect_chat_vibe, VibeAnalysis
from ..responders.response_composer import (
    ResponseComposer,
    CompositionContext,
    GreetingPolicy,
    is_pure_greeting,
    looks_like_greeting,
)
from ..responders.preprocess import PreprocessNode, PreprocessResult
from ..responders.anaphora_resolver import AnaphoraResolver
from ..utils.dedup import DeduplicationStore
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..models.message import IncomingMessage, Platform

logger = get_logger("orchestrator")


class BotState(Enum):
    """Orchestrator state."""
    IDLE = "idle"
    FETCHING = "fetching"
    ROUTING = "routing"
    GENERATING = "generating"
    SENDING = "sending"
    MEMORY_UPDATE = "memory_update"
    ERROR = "error"


@dataclass
class PersonaRuntime:
    """
    Runtime state for a single persona.
    Each persona gets its own set of components for isolation.
    """
    config: PersonaConfig
    llm: LLMClient
    router: MessageRouter
    generator: ResponseGenerator
    antispam: RateLimiter
    memory: UserMemoryStore
    dedup: DeduplicationStore
    composer: ResponseComposer = None  # Response composer (greeting, formatting)
    preprocessor: PreprocessNode = None  # Deterministic shortcuts
    anaphora: AnaphoraResolver = None  # Context anaphora resolution
    monitor: object = None  # TelegramUserbot | TelegramMonitor | VKMonitorAsync
    state: BotState = BotState.IDLE
    stats: dict = field(default_factory=lambda: {
        "messages_processed": 0,
        "responses_sent": 0,
        "ignored": 0,
        "errors": 0,
        "preprocess_shortcuts": 0,
        "greeting_skips": 0,
    })


class SalesBotOrchestratorV2:
    """
    Multi-persona orchestrator.
    
    Loads all personas from personas/ directory,
    starts each on its platform, routes messages through
    router → generator pipeline, stores in memory.
    """
    
    def __init__(
        self,
        personas_dir: str = "./personas",
        memory_dir: str = "./data/memory",
        openrouter_api_key: str = "",
    ):
        self.personas_dir = personas_dir
        self.memory_dir = memory_dir
        self.api_key = openrouter_api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.runtimes: dict[str, PersonaRuntime] = {}
        self._tasks: list[asyncio.Task] = []
        self._running = False
    
    def load_personas(self) -> list[PersonaConfig]:
        """Load all persona configs from directory."""
        configs = discover_personas(self.personas_dir)
        logger.info(f"Discovered {len(configs)} personas in {self.personas_dir}")
        return configs
    
    def _build_runtime(self, config: PersonaConfig) -> PersonaRuntime:
        """Build runtime components for a persona."""
        # LLM client (shared key, per-persona instance)
        llm = LLMClient(
            api_key=self.api_key,
            timeout=30,
        )
        
        # Build contract dict compatible with v1 router/generator
        contract = self._persona_to_contract(config)
        
        # Router (fast model — decide respond/ignore)
        router = MessageRouter(
            llm_client=llm,
            model=config.router_model,
            contract=contract,
        )
        
        # Generator (slow model — generate response, with response examples)
        generator = ResponseGenerator(
            llm_client=llm,
            model=config.generator_model,
            contract=contract,
            response_examples=[
                {"trigger": ex.trigger, "bad": ex.bad_response, "good": ex.good_response}
                for ex in config.response_examples
            ] if config.response_examples else None,
        )
        
        # Anti-spam (per-persona limits)
        antispam = RateLimiter(
            min_delay_sec=config.anti_spam.min_delay_between_messages,
            max_delay_sec=config.anti_spam.max_delay_between_messages,
            max_global_per_hour=config.group_mode.max_messages_per_hour * 2,
            max_per_chat_per_hour=config.group_mode.max_messages_per_hour,
            cooldown_sec=config.anti_spam.min_delay_between_messages,
        )
        
        # Memory (per-persona directory, persona-aware entity extraction)
        persona_memory_dir = os.path.join(self.memory_dir, config.name.lower().replace(" ", "_"))
        memory = UserMemoryStore(
            memory_dir=persona_memory_dir,
            persona_name=config.name,
        )
        
        # Dedup
        dedup = DeduplicationStore(
            storage_path=os.path.join(persona_memory_dir, "processed_messages.json")
        )
        
        # Response Composer (greeting handling, formatting — from ai-tutor-engine pattern)
        greeting_policy = GreetingPolicy(
            enabled=config.greeting_policy.enabled,
            greet_only_first_response=config.greeting_policy.greet_only_first_response,
            greet_only_if_user_greeted=config.greeting_policy.greet_only_if_user_greeted,
            strip_greeting_if_not_allowed=config.greeting_policy.strip_greeting_if_not_allowed,
            greeting_variants=config.greeting_policy.greeting_variants,
            fallback_variants=config.greeting_policy.fallback_variants,
        )
        composer = ResponseComposer(
            persona_name=config.name,
            greeting_policy=greeting_policy,
            banned_phrases=config.vibe.taboos if config.vibe else [],
        )
        
        # Preprocess Node (deterministic shortcuts — from ai-tutor-engine pattern)
        preprocessor = PreprocessNode(
            composer=composer,
            followup_reuse_tools=["product_search"],
        )
        
        # Anaphora Resolver (context memory — from ai-tutor-engine pattern)
        anaphora = AnaphoraResolver(max_contexts=500)
        
        return PersonaRuntime(
            config=config,
            llm=llm,
            router=router,
            generator=generator,
            antispam=antispam,
            memory=memory,
            dedup=dedup,
            composer=composer,
            preprocessor=preprocessor,
            anaphora=anaphora,
        )
    
    def _persona_to_contract(self, config: PersonaConfig) -> dict:
        """
        Convert PersonaConfig (v2 YAML format) to contract dict
        compatible with v1 router/generator expectations.
        """
        # Build triggers in v1 format
        respond_to = []
        for trigger in config.respond_triggers:
            respond_to.append({
                "context": ", ".join(trigger.keywords[:3]) if trigger.keywords else "general",
                "keywords": trigger.keywords,
                "topics": trigger.topics,
            })
        
        ignore = []
        for ign in config.ignore_triggers:
            if ign.contains:
                ignore.extend(ign.contains)
            if ign.from_bot:
                ignore.append("from_bot")
        
        # Build products
        products = []
        if config.product_name:
            products.append({
                "name": config.product_name,
                "description": config.product_description,
                "price": config.product_price,
                "link": config.product_link,
            })
        
        # Build conversation flow
        never = []
        if config.group_mode.max_messages_per_hour:
            never.append(f"More than {config.group_mode.max_messages_per_hour} messages per hour")
        
        return {
            "persona": {
                "name": config.name,
                "backstory": config.personality,
                "speaking_style": {
                    "tone": config.group_mode.style,
                    "patterns": [],
                    "forbidden": [],
                },
                "competitor_knowledge": config.competitor_knowledge,
                "group_context_examples": [
                    {"trigger": ex.trigger, "bad": ex.bad_response, "good": ex.good_response}
                    for ex in config.group_context_examples
                ] if config.group_context_examples else [],
            },
            "product": {
                "products": products,
            },
            "triggers": {
                "respond_to": respond_to,
                "ignore": ignore,
            },
            "conversation_flow": {
                "group_chat": {
                    "strategy": config.group_mode.style,
                    "steps": [config.group_mode.style],
                },
                "direct_message": {
                    "strategy": config.dm_mode.greeting,
                    "steps": [s.step for s in config.dm_mode.funnel],
                },
                "never": never,
            },
        }
    
    async def _handle_message(self, msg: IncomingMessage, runtime: PersonaRuntime):
        """
        Process one message through the full pipeline.
        
        Pipeline: Dedup → Preprocess → Anaphora → Route → Generate → Compose → AntiSpam → Send → Memory
        """
        runtime.state = BotState.FETCHING
        runtime.stats["messages_processed"] += 1
        
        # === DEDUP ===
        if runtime.dedup.is_processed(msg.chat_id, msg.message_id, msg.text):
            logger.debug(f"[{runtime.config.name}] Skipping duplicate: {msg.message_id}")
            return
        
        # === PREPROCESS (deterministic shortcuts — from ai-tutor-engine pattern) ===
        # Pure greetings, follow-ups, price shock — skip LLM entirely
        last_context = {
            "last_tool_name": runtime.memory.get_last_tool(msg.user_id),
            "last_tool_args": runtime.memory.get_last_tool_args(msg.user_id),
        }
        
        is_first = runtime.memory.is_first_response(msg.user_id, msg.chat_id)
        user_greeted = looks_like_greeting(msg.text) if msg.text else False
        
        preprocess_result = runtime.preprocessor.process(
            question=msg.text or "",
            last_context=last_context,
            is_first_response=is_first,
            user_greeted=user_greeted,
            is_dm=msg.is_dm,
        )
        
        if preprocess_result.has_shortcut:
            runtime.stats["preprocess_shortcuts"] += 1
            
            if preprocess_result.skip_generation:
                logger.debug(f"[{runtime.config.name}] Preprocess: skip (trivial)")
                runtime.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
                runtime.state = BotState.IDLE
                return
            
            if preprocess_result.shortcut_response:
                logger.info(f"[{runtime.config.name}] Preprocess: shortcut → {preprocess_result.pipeline_step}")
                runtime.stats["greeting_skips"] += 1
                sent = await self._send_response(runtime, msg, preprocess_result.shortcut_response)
                if sent:
                    runtime.stats["responses_sent"] += 1
                    runtime.antispam.record_send(msg.chat_id)
                    runtime.dedup.record_bot_response(msg.chat_id, preprocess_result.shortcut_response)
                runtime.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
                runtime.state = BotState.IDLE
                return
        
        # === ANAPHORA RESOLUTION (context memory — from ai-tutor-engine pattern) ===
        anaphora_result = runtime.anaphora.resolve(
            user_id=str(msg.user_id),
            chat_id=str(msg.chat_id),
            question=msg.text or "",
        )
        
        # === ROUTING ===
        runtime.state = BotState.ROUTING
        
        chat_context = runtime.memory.get_recent_messages(msg.chat_id, limit=3)
        
        route_result = await runtime.router.route(
            message_text=msg.text,
            chat_context=chat_context,
            is_dm=msg.is_dm,
        )
        
        logger.info(
            f"[{runtime.config.name}] Route: {route_result.decision.value} "
            f"(conf={route_result.confidence:.1f}, reason={route_result.reason})"
        )
        
        if route_result.decision == Decision.IGNORE:
            runtime.stats["ignored"] += 1
            runtime.state = BotState.IDLE
            runtime.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
            return
        
        # === LEAVE ON READ (human-like behavior) ===
        # Real humans don't respond to ~35% of messages they read
        if not msg.is_dm and runtime.antispam.should_leave_on_read():
            logger.debug(f"[{runtime.config.name}] Leave on read: {msg.message_id}")
            runtime.stats["ignored"] += 1
            runtime.state = BotState.IDLE
            runtime.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
            return
        
        # === EMOJI REACTION (instead of text) ===
        # Sometimes 👍 is more natural than a paragraph
        if not msg.is_dm and runtime.antispam.should_use_emoji_reaction():
            emoji = runtime.antispam.get_emoji_reaction(msg.text)
            if emoji:
                logger.info(f"[{runtime.config.name}] Emoji reaction: {emoji} to {msg.message_id}")
                await self._send_emoji_reaction(runtime, msg, emoji)
                runtime.stats["responses_sent"] += 1
                runtime.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
                runtime.state = BotState.IDLE
                return
        
        # === GENERATION ===
        runtime.state = BotState.GENERATING
        
        response: Optional[GeneratedResponse] = None
        
        try:
            if msg.is_dm:
                user_memory = runtime.memory.get_user_context(msg.user_id)
                group_context = runtime.memory.get_group_context_for_user(msg.user_id)
                
                # Auto-analyze funnel signals
                funnel_stage = runtime.memory.analyze_funnel_signals(msg.user_id, msg.text)
                # Use the more advanced stage if funnel analysis detected something
                current_stage = runtime.memory.get_funnel_stage(msg.user_id)
                effective_stage = funnel_stage if funnel_stage != current_stage else current_stage
                
                # Include previous recommendations to avoid repetition
                prev_recs = runtime.memory.get_recommendations(msg.user_id)
                if prev_recs:
                    user_memory += f"\nУже рекомендовал: {'; '.join(prev_recs[-3:])}"
                
                response = await runtime.generator.generate_dm_response(
                    message_text=msg.text,
                    user_memory=user_memory,
                    dm_history="",
                    group_context=group_context,
                    funnel_stage=effective_stage,
                )
            else:
                # Detect chat vibe from recent context for vibe matching
                recent_msgs = runtime.dedup.get_recent_texts(msg.chat_id, limit=10)
                chat_vibe = detect_chat_vibe(recent_msgs) if recent_msgs else None
                
                response = await runtime.generator.generate_group_response(
                    message_text=msg.text,
                    chat_context=chat_context,
                    chat_vibe=chat_vibe,
                )
        except Exception as e:
            logger.error(f"[{runtime.config.name}] Generation error: {e}")
            runtime.stats["errors"] += 1
            runtime.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
            runtime.state = BotState.IDLE
            return
        
        if not response or not response.text:
            logger.info(f"[{runtime.config.name}] No response for {msg.message_id}")
            runtime.state = BotState.IDLE
            runtime.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
            return
        
        # === SENDING ===
        runtime.state = BotState.SENDING
        
        can_send, reason = runtime.antispam.can_send(msg.chat_id)
        
        sent = False
        if can_send:
            delay = runtime.antispam.get_random_delay()
            logger.debug(f"[{runtime.config.name}] Anti-spam delay: {delay:.1f}s")
            await asyncio.sleep(delay)
            
            # Re-check after delay
            can_send, reason = runtime.antispam.can_send(msg.chat_id)
            if can_send:
                # Apply text humanization (typos, lowercase starts, etc.)
                send_text = response.text
                if runtime.config.anti_spam.random_typos:
                    is_casual = response.tone in ("casual", "humor")
                    send_text = humanize_text(send_text, is_casual=is_casual)
                
                # Check if we're repeating ourselves
                if runtime.dedup.is_repeating_response(msg.chat_id, send_text):
                    logger.info(f"[{runtime.config.name}] Skipping repeat response in {msg.chat_id}")
                    runtime.stats["ignored"] += 1
                else:
                    # === TYPING INDICATOR ===
                    # Simulate human typing before sending
                    if runtime.config.anti_spam.typing_simulation:
                        typing_calc = TypingSpeedCalculator()
                        typing_time = typing_calc.estimate_typing_time(send_text)
                        # Send typing indicator if monitor supports it
                        try:
                            if hasattr(runtime.monitor, 'send_typing'):
                                await runtime.monitor.send_typing(msg.chat_id)
                        except Exception:
                            pass
                        # Wait "typing" time (clamped to reasonable range)
                        await asyncio.sleep(min(typing_time, 15.0))
                    
                    sent = await self._send_response(runtime, msg, send_text)
                    if sent:
                        runtime.antispam.record_send(msg.chat_id)
                        runtime.dedup.record_bot_response(msg.chat_id, send_text)
            else:
                logger.warning(f"[{runtime.config.name}] Blocked after delay: {reason}")
        else:
            logger.warning(f"[{runtime.config.name}] Anti-spam blocked: {reason}")
        
        if sent:
            runtime.stats["responses_sent"] += 1
            
            # === MEMORY ===
            runtime.state = BotState.MEMORY_UPDATE
            
            if msg.is_dm:
                runtime.memory.record_dm(
                    user_id=msg.user_id,
                    username=msg.username,
                    display_name=msg.display_name,
                    message=msg.text,
                    response=response.text,
                    stage=response.stage,
                )
            else:
                runtime.memory.record_group_message(
                    user_id=msg.user_id,
                    username=msg.username,
                    display_name=msg.display_name,
                    chat_id=msg.chat_id,
                    chat_title=msg.chat_title,
                    message=msg.text,
                )
            
            for note in response.remember:
                runtime.memory.add_note(msg.user_id, note)
        
        runtime.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
        runtime.state = BotState.IDLE
    
    async def _send_response(
        self,
        runtime: PersonaRuntime,
        msg: IncomingMessage,
        text: str,
    ) -> bool:
        """Send response using the appropriate platform monitor."""
        try:
            config = runtime.config
            monitor = runtime.monitor
            
            if config.platform == "telegram" and config.account_type == "userbot":
                if isinstance(monitor, TelegramUserbot):
                    return await monitor.send_message(
                        chat_id=msg.chat_id,
                        text=text,
                        reply_to=msg.message_id if not msg.is_dm else None,
                        typing_delay=config.anti_spam.typing_simulation,
                    )
            
            elif config.platform == "telegram" and config.account_type == "bot":
                if isinstance(monitor, TelegramMonitor):
                    return await monitor.send_message(
                        chat_id=msg.chat_id,
                        text=text,
                        reply_to=msg.message_id if not msg.is_dm else None,
                    )
            
            elif config.platform == "vk":
                if isinstance(monitor, VKMonitorAsync):
                    return await monitor.send_message(
                        peer_id=msg.chat_id,
                        text=text,
                    )
            
            logger.error(f"[{config.name}] No send handler for platform={config.platform}")
            return False
            
        except Exception as e:
            logger.error(f"[{runtime.config.name}] Send error: {e}")
            return False
    
    async def _send_emoji_reaction(
        self,
        runtime: PersonaRuntime,
        msg: IncomingMessage,
        emoji: str,
    ) -> bool:
        """Send emoji reaction instead of text response."""
        try:
            monitor = runtime.monitor
            config = runtime.config
            
            if config.platform == "telegram" and isinstance(monitor, TelegramUserbot):
                # Telethon: use SendReactionRequest
                try:
                    from telethon.tl.functions.messages import SendReactionRequest
                    from telethon.tl.types import ReactionEmoji
                    await monitor.client(SendReactionRequest(
                        peer=int(msg.chat_id),
                        msg_id=msg.message_id,
                        reaction=[ReactionEmoji(emoticon=emoji)],
                    ))
                    return True
                except ImportError:
                    # Telethon not available — skip reaction
                    logger.debug("Telethon not available for reactions")
                    return False
                except Exception as e:
                    logger.warning(f"Reaction failed: {e}")
                    return False
            
            # For other platforms — no reaction support, silently skip
            return False
            
        except Exception as e:
            logger.error(f"[{runtime.config.name}] Emoji reaction error: {e}")
            return False
    
    async def _run_telegram_userbot(
        self,
        runtime: PersonaRuntime,
    ):
        """Run a Telegram userbot persona."""
        config = runtime.config
        
        try:
            bot = TelegramUserbot(
                session_name=config.session_name or config.name.lower(),
                api_id=config.api_id or None,
                api_hash=config.api_hash or None,
                phone=config.phone or None,
            )
            runtime.monitor = bot
            
            async def callback(userbot_msg: UserbotMessage):
                msg = IncomingMessage.from_userbot_message(userbot_msg, persona_name=config.name)
                await self._handle_message(msg, runtime)
            
            await bot.run(callback=callback, allowed_chats=config.groups_to_monitor)
            
        except Exception as e:
            logger.error(f"[{config.name}] Telegram userbot crashed: {e}")
            runtime.stats["errors"] += 1
    
    async def _run_telegram_bot(
        self,
        runtime: PersonaRuntime,
    ):
        """Run a Telegram Bot API persona."""
        config = runtime.config
        
        try:
            bot = TelegramMonitor(bot_token=config.bot_token)
            runtime.monitor = bot
            
            async def callback(tg_msg: TelegramMessage):
                msg = IncomingMessage.from_telegram_message(tg_msg, persona_name=config.name)
                await self._handle_message(msg, runtime)
            
            await bot.poll_loop(callback=callback, allowed_chats=config.groups_to_monitor)
            
        except Exception as e:
            logger.error(f"[{config.name}] Telegram bot crashed: {e}")
            runtime.stats["errors"] += 1
    
    async def _run_vk_persona(
        self,
        runtime: PersonaRuntime,
    ):
        """Run a VK persona."""
        config = runtime.config
        
        try:
            monitor = VKMonitorAsync(access_token=config.vk_token)
            await monitor.start()
            runtime.monitor = monitor
            
            async def callback(vk_msg: VKMessage):
                msg = IncomingMessage.from_vk_message(vk_msg, persona_name=config.name)
                await self._handle_message(msg, runtime)
            
            await monitor.run(callback=callback, allowed_chats=config.groups_to_monitor)
            
        except Exception as e:
            logger.error(f"[{config.name}] VK persona crashed: {e}")
            runtime.stats["errors"] += 1
    
    async def _run_persona(self, runtime: PersonaRuntime):
        """Run a single persona based on its platform config."""
        config = runtime.config
        logger.info(f"Starting persona: {config.name} on {config.platform}/{config.account_type}")
        
        try:
            if config.platform == "telegram" and config.account_type == "userbot":
                await self._run_telegram_userbot(runtime)
            elif config.platform == "telegram" and config.account_type == "bot":
                await self._run_telegram_bot(runtime)
            elif config.platform == "vk":
                await self._run_vk_persona(runtime)
            else:
                logger.error(f"[{config.name}] Unknown platform: {config.platform}/{config.account_type}")
        except Exception as e:
            logger.error(f"[{config.name}] Fatal error: {e}")
            runtime.stats["errors"] += 1
    
    async def start(self):
        """Load all personas and start them in parallel."""
        logger.info("=" * 50)
        logger.info("Sales Bot Orchestrator v2 starting...")
        logger.info(f"Personas dir: {self.personas_dir}")
        logger.info(f"Memory dir: {self.memory_dir}")
        logger.info("=" * 50)
        
        # Load persona configs
        configs = self.load_personas()
        
        if not configs:
            logger.warning("No personas found! Nothing to run.")
            return
        
        # Build runtimes
        for config in configs:
            try:
                runtime = self._build_runtime(config)
                self.runtimes[config.name] = runtime
                logger.info(f"Built runtime for: {config.name}")
            except Exception as e:
                logger.error(f"Failed to build runtime for {config.name}: {e}")
        
        if not self.runtimes:
            logger.error("No valid runtimes built. Exiting.")
            return
        
        # Start all personas in parallel
        self._running = True
        tasks = []
        
        for name, runtime in self.runtimes.items():
            task = asyncio.create_task(
                self._run_persona(runtime),
                name=f"persona-{name}",
            )
            tasks.append(task)
        
        self._tasks = tasks
        logger.info(f"Running {len(tasks)} personas in parallel")
        
        # Wait for all (or first crash)
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        # Log crashes
        for task in done:
            if task.exception():
                logger.error(f"Persona task {task.get_name()} crashed: {task.exception()}")
        
        # Cancel remaining on crash
        for task in pending:
            task.cancel()
            logger.warning(f"Cancelled: {task.get_name()}")
    
    async def stop(self):
        """Stop all running personas."""
        self._running = False
        
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        # Close LLM clients
        for name, runtime in self.runtimes.items():
            try:
                await runtime.llm.close()
            except Exception:
                pass
        
        self._tasks.clear()
        logger.info("All personas stopped")
    
    def get_status(self) -> dict:
        """Get status of all personas."""
        return {
            "running": self._running,
            "personas": {
                name: {
                    "state": runtime.state.value,
                    "platform": runtime.config.platform,
                    "account_type": runtime.config.account_type,
                    "stats": runtime.stats,
                    "antispam": runtime.antispam.get_stats(),
                }
                for name, runtime in self.runtimes.items()
            },
        }


# === Entry point for standalone run ===

async def run_multi_persona(
    personas_dir: str = "./personas",
    memory_dir: str = "./data/memory",
    api_key: str = "",
):
    """Run the multi-persona orchestrator."""
    orchestrator = SalesBotOrchestratorV2(
        personas_dir=personas_dir,
        memory_dir=memory_dir,
        openrouter_api_key=api_key,
    )
    
    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    from ..utils.logger import setup_logging
    setup_logging(level="INFO")
    asyncio.run(run_multi_persona())
