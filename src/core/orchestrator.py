"""
Orchestrator — Multi-Persona Sales Bot (Production)

Manages N personas running in parallel.
Each persona = 1 YAML contract + 1 platform account + 1 set of components.

Pipeline per message (LangGraph state machine):
  dedup → preprocess → [semantic_retrieval, anaphora] → route →
  antispam → generate → validate → send → memory

This is the unified production orchestrator, consolidating v1/v2/v3 into a single
maintainable architecture. Telegram-first, multi-persona, with full reliability features.

New: LangGraph-based state machine with Supabase PostgreSQL persistence.
"""

import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from ..core.lifecycle import LifecycleManager, PersonaSupervisor, SupervisorConfig
from ..core.persona_manager import PersonaConfig, PersonaManager, discover_personas
from ..core.router import MessageRouter, Decision, RouteResult
from ..core.output_validators import OutputValidator, ValidationResult
from ..core.prompt_compiler import PromptCompiler
from ..core.vibe_schema import VibePersona, VibeBehavior, ResponseExample, GreetingPolicy as SchemaGreetingPolicy
from ..graph.state import build_initial_state
from ..memory.memory_facade import MemoryFacade
from ..responders.generator import ResponseGenerator, GeneratedResponse
from ..monitors.anti_spam import RateLimiter, TypingSpeedCalculator
from ..platforms import PlatformAdapter, SendOptions, UnknownPlatformError, create_adapter
from ..responders.text_humanizer import humanize_text
from ..responders.chat_vibe import detect_chat_vibe
from ..responders.response_composer import (
    ResponseComposer,
    GreetingPolicy,
    looks_like_greeting,
)
from ..responders.preprocess import PreprocessNode
from ..responders.anaphora_resolver import AnaphoraResolver
from ..utils.dedup import DeduplicationStore
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..models.message import IncomingMessage

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
    memory: MemoryFacade  # NEW: Unified Supabase+embeddings facade
    dedup: DeduplicationStore  # Kept for backward compatibility during migration
    composer: ResponseComposer = None  # Response composer (greeting, formatting)
    preprocessor: PreprocessNode = None  # Deterministic shortcuts
    anaphora: AnaphoraResolver = None  # Context anaphora resolution
    output_validator: OutputValidator = None  # Post-generation output validation
    adapter: Optional[PlatformAdapter] = None  # Telegram* / VK / future platforms
    graph: Optional[Any] = None  # NEW: Compiled LangGraph
    state: BotState = BotState.IDLE
    stats: dict = field(default_factory=lambda: {
        "messages_processed": 0,
        "responses_sent": 0,
        "ignored": 0,
        "errors": 0,
        "preprocess_shortcuts": 0,
        "greeting_skips": 0,
        "validator_fixes": 0,
        "started_at": None,
        "last_message_at": None,
    })


class SalesBotOrchestrator:
    """
    Multi-persona orchestrator — Production Unified Architecture.

    Loads all personas from personas/ directory,
    starts each on its platform, routes messages through
    full pipeline, stores in memory.

    Features:
    - Per-persona isolation (runtime, memory, rate limits)
    - Automatic restart with exponential backoff
    - Graceful shutdown with resource cleanup
    - Health status reporting
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
        self._lifecycle = LifecycleManager()
        self._running = False

    def load_personas(self) -> list[PersonaConfig]:
        """Load all persona configs from directory."""
        configs = discover_personas(self.personas_dir)
        logger.info(f"Discovered {len(configs)} personas in {self.personas_dir}")
        return configs

    async def _build_runtime(self, config: PersonaConfig) -> PersonaRuntime:
        """Build runtime components for a persona."""
        import time

        # LLM client (shared key, per-persona instance)
        llm = LLMClient(
            api_key=self.api_key,
            timeout=30,
        )

        # Build contract dict compatible with router/generator
        contract = self._build_router_contract(config)

        # PromptCompiler — builds rich behavior block from persona vibe/behavior/examples
        prompt_compiler = PromptCompiler(
            vibe=config.vibe,
            behavior=config.behavior,
            response_examples=[
                ResponseExample(
                    trigger=ex.trigger,
                    bad_response=ex.bad_response,
                    good_response=ex.good_response,
                )
                for ex in config.response_examples
            ] if config.response_examples else None,
            competitor_knowledge=config.competitor_knowledge or "",
            personality=config.personality or "",
        )
        behavior_block = prompt_compiler.compile_system_prompt()

        # Router (fast model — decide respond/ignore)
        router = MessageRouter(
            llm_client=llm,
            model=config.router_model,
            contract=contract,
        )

        # Generator (slow model — generate response, with response examples + behavior block)
        generator = ResponseGenerator(
            llm_client=llm,
            model=config.generator_model,
            contract=contract,
            response_examples=[
                {"trigger": ex.trigger, "bad": ex.bad_response, "good": ex.good_response}
                for ex in config.response_examples
            ] if config.response_examples else None,
            behavior_block=behavior_block,
        )

        # Anti-spam (per-persona limits)
        antispam = RateLimiter(
            min_delay_sec=config.anti_spam.min_delay_between_messages,
            max_delay_sec=config.anti_spam.max_delay_between_messages,
            max_global_per_hour=config.group_mode.max_messages_per_hour * 2,
            max_per_chat_per_hour=config.group_mode.max_messages_per_hour,
            cooldown_sec=config.anti_spam.min_delay_between_messages,
        )

        # NEW: Memory Facade (Supabase + embeddings)
        memory = await MemoryFacade.create(persona_name=config.name)

        # Dedup (kept during migration period)
        persona_memory_dir = os.path.join(self.memory_dir, config.name.lower().replace(" ", "_"))
        dedup = DeduplicationStore(
            storage_path=os.path.join(persona_memory_dir, "processed_messages.json")
        )

        # Response Composer (greeting handling, formatting)
        gp = config.greeting_policy
        if gp:
            greeting_policy = GreetingPolicy(
                enabled=gp.enabled,
                greet_only_first_response=gp.greet_only_first_response,
                greet_only_if_user_greeted=gp.greet_only_if_user_greeted,
                strip_greeting_if_not_allowed=gp.strip_greeting_if_not_allowed,
                greeting_variants=gp.greeting_variants,
                fallback_variants=gp.fallback_variants,
            )
            schema_greeting_policy = SchemaGreetingPolicy(
                enabled=gp.enabled,
                greet_only_first_response=gp.greet_only_first_response,
                greet_only_if_user_greeted=gp.greet_only_if_user_greeted,
                strip_greeting_if_not_allowed=gp.strip_greeting_if_not_allowed,
                greeting_variants=gp.greeting_variants,
            )
        else:
            greeting_policy = GreetingPolicy()
            schema_greeting_policy = None

        composer = ResponseComposer(
            persona_name=config.name,
            greeting_policy=greeting_policy,
            banned_phrases=config.vibe.taboos if config.vibe else [],
        )

        # Output Validator — post-generation validation
        taboos = config.vibe.taboos if config.vibe else []
        validators_config = None
        if taboos:
            from ..core.vibe_schema import OutputValidators as OutputValidatorsConfig
            validators_config = OutputValidatorsConfig(banned_phrases=taboos)
        output_validator = OutputValidator(
            validators_config=validators_config,
            greeting_policy=schema_greeting_policy,
        )

        # Preprocess Node (deterministic shortcuts)
        preprocessor = PreprocessNode(
            composer=composer,
            followup_reuse_tools=["product_search"],
        )

        # Anaphora Resolver (context memory)
        anaphora = AnaphoraResolver(max_contexts=500)

        # Build runtime
        runtime = PersonaRuntime(
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
            output_validator=output_validator,
        )
        runtime.stats["started_at"] = time.time()

        # Compile LangGraph for this persona (lazy import — pulls Postgres checkpoint driver)
        try:
            from ..graph.builder import compile_persona_graph

            runtime.graph = await compile_persona_graph(runtime)
            logger.info(f"Compiled LangGraph for {config.name}")
        except Exception as e:
            logger.error(f"Failed to compile graph for {config.name}: {e}")
            # Continue without graph - will use fallback _handle_message_legacy

        return runtime

    def _build_router_contract(self, config: PersonaConfig) -> dict:
        """
        Build the internal contract dict expected by Router and Generator.
        """
        # Build triggers
        respond_to = []
        for trigger in config.respond_triggers:
            respond_to.append({
                "context": ", ".join(trigger.keywords) if trigger.keywords else "general",
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
        Process one message through the LangGraph pipeline.

        Uses compiled graph with PostgresSaver for state persistence.
        Thread ID: "{persona_name}:{user_id}:{chat_id}"
        """
        import time

        runtime.state = BotState.FETCHING
        runtime.stats["messages_processed"] += 1
        runtime.stats["last_message_at"] = time.time()

        # Build thread ID for state isolation
        thread_id = f"{runtime.config.name}:{msg.user_id}:{msg.chat_id}"

        # Build initial state
        initial_state = build_initial_state(msg)

        from ..graph.builder import build_config

        # Build config with runtime dependencies
        config = build_config(runtime, thread_id)

        try:
            # Run the graph
            if runtime.graph:
                final_state = await runtime.graph.ainvoke(initial_state, config=config)

                # Update stats based on results
                if final_state.get("sent"):
                    runtime.stats["responses_sent"] += 1
                elif final_state.get("route_decision") == "ignore":
                    runtime.stats["ignored"] += 1

                # Log any errors
                if final_state.get("error_message"):
                    runtime.stats["errors"] += 1
                    logger.error(f"[{runtime.config.name}] Graph error: {final_state['error_message']}")

                # Log node history for debugging
                node_history = final_state.get("node_history", [])
                logger.debug(f"[{runtime.config.name}] Path: {' -> '.join(node_history)}")
            else:
                # Fallback: use legacy handler if graph not compiled
                logger.warning(f"[{runtime.config.name}] No graph available, using legacy handler")
                await self._handle_message_legacy(msg, runtime)

        except Exception as e:
            logger.error(f"[{runtime.config.name}] Graph invocation error: {e}")
            runtime.stats["errors"] += 1
            # Try to at least mark as processed
            try:
                await runtime.memory.mark_processed(msg.chat_id, msg.message_id, msg.text)
            except Exception:
                pass

        finally:
            runtime.state = BotState.IDLE

    async def _handle_message_legacy(self, msg: IncomingMessage, runtime: PersonaRuntime):
        """Legacy handler for fallback when graph is not available.

        Simplified version of the original linear pipeline.
        """
        # Quick dedup check
        is_dup = await runtime.memory.is_processed(msg.chat_id, msg.message_id, msg.text)
        if is_dup:
            return

        # Mark processed
        await runtime.memory.mark_processed(msg.chat_id, msg.message_id, msg.text)

        # Simple routing
        route_result = await runtime.router.route(
            message_text=msg.text or "",
            chat_context=[],
            is_dm=msg.is_dm,
        )

        if route_result.decision == Decision.IGNORE:
            runtime.stats["ignored"] += 1
            return

        # Generate response
        try:
            if msg.is_dm:
                user_context = await runtime.memory.get_user_context(msg.user_id)
                response = await runtime.generator.generate_dm_response(
                    message_text=msg.text or "",
                    user_memory=user_context,
                    dm_history="",
                    group_context="",
                    funnel_stage="unknown",
                )
            else:
                response = await runtime.generator.generate_group_response(
                    message_text=msg.text or "",
                    chat_context=[],
                    chat_vibe=None,
                )

            if response and response.text and runtime.adapter:
                reply_to = None if msg.is_dm else msg.message_id
                success = await runtime.adapter.send_reply(
                    msg,
                    response.text,
                    SendOptions(
                        reply_to_message_id=reply_to,
                        typing_already_simulated=True,
                    ),
                )

                if success:
                    runtime.stats["responses_sent"] += 1
                    # Record to memory
                    await runtime.memory.record_dm(
                        user_id=msg.user_id,
                        username=msg.username,
                        display_name=msg.display_name,
                        message=msg.text or "",
                        response=response.text,
                        stage="unknown",
                    )

        except Exception as e:
            logger.error(f"[{runtime.config.name}] Legacy handler error: {e}")

    async def _run_persona(self, runtime: PersonaRuntime):
        """Run a single persona: registry-built adapter + inbound loop."""
        config = runtime.config
        logger.info(f"Starting persona: {config.name} on {config.platform}/{config.account_type}")

        try:
            adapter = await create_adapter(config)
            runtime.adapter = adapter

            async def callback(msg: IncomingMessage) -> None:
                await self._handle_message(msg, runtime)

            await adapter.run(callback=callback, allowed_chats=config.groups_to_monitor)
        except UnknownPlatformError as e:
            logger.error(f"[{config.name}] {e}")
            runtime.stats["errors"] += 1
        except Exception as e:
            logger.error(f"[{config.name}] Fatal error: {e}")
            runtime.stats["errors"] += 1
            raise

    def _create_supervisor(self, runtime: PersonaRuntime) -> PersonaSupervisor:
        """Create a supervisor for a persona runtime."""
        config = SupervisorConfig(
            max_restarts=5,
            backoff_base_sec=10.0,
            backoff_max_sec=300.0,
            graceful_shutdown_timeout_sec=10.0,
        )

        async def task_factory():
            await self._run_persona(runtime)

        return PersonaSupervisor(
            persona_name=runtime.config.name,
            task_factory=task_factory,
            config=config,
        )

    async def start(self):
        """Load all personas and start them in parallel."""
        logger.info("=" * 50)
        logger.info("Sales Bot Orchestrator starting (production)...")
        logger.info(f"Personas dir: {self.personas_dir}")
        logger.info(f"Memory dir: {self.memory_dir}")
        logger.info("=" * 50)

        # Load persona configs
        configs = self.load_personas()

        if not configs:
            logger.warning("No personas found! Nothing to run.")
            return

        # Build runtimes (async)
        for config in configs:
            try:
                runtime = await self._build_runtime(config)
                self.runtimes[config.name] = runtime
                logger.info(f"Built runtime for: {config.name}")
            except Exception as e:
                logger.error(f"Failed to build runtime for {config.name}: {e}")

        if not self.runtimes:
            logger.error("No valid runtimes built. Exiting.")
            return

        # Start all personas via lifecycle manager
        self._running = True

        for name, runtime in self.runtimes.items():
            supervisor = self._create_supervisor(runtime)
            self._lifecycle.register(name, supervisor)

        await self._lifecycle.start_all()
        logger.info(f"Running {len(self.runtimes)} personas in parallel")

        # Wait for shutdown or any supervisor to complete
        await self._lifecycle.wait_for_shutdown()

    async def stop(self):
        """Stop all running personas gracefully."""
        logger.info("Stopping orchestrator...")
        self._running = False

        # Stop all supervisors (handles task cancellation)
        await self._lifecycle.stop_all(timeout=10.0)

        # Cleanup resources for each runtime
        for name, runtime in self.runtimes.items():
            if runtime.adapter:
                try:
                    await runtime.adapter.stop()
                except Exception as e:
                    logger.debug(f"[{name}] Adapter stop error: {e}")

            # Close LLM client
            try:
                await runtime.llm.close()
            except Exception as e:
                logger.debug(f"[{name}] LLM close error: {e}")

            # Close memory facade (Supabase connection pool)
            try:
                await runtime.memory.close()
            except Exception as e:
                logger.debug(f"[{name}] Memory close error: {e}")

        logger.info("All personas stopped")

    def get_status(self) -> dict:
        """Get status of all personas."""
        import time

        # Get lifecycle health for all supervisors
        lifecycle_health = self._lifecycle.get_health()

        return {
            "running": self._running,
            "personas": {
                name: {
                    "state": runtime.state.value,
                    "platform": runtime.config.platform,
                    "platform_key": runtime.adapter.platform_key() if runtime.adapter else None,
                    "account_type": runtime.config.account_type,
                    "stats": runtime.stats,
                    "antispam": runtime.antispam.get_stats(),
                    "uptime_sec": time.time() - runtime.stats["started_at"] if runtime.stats["started_at"] else 0,
                    "last_message_sec_ago": time.time() - runtime.stats["last_message_at"] if runtime.stats["last_message_at"] else None,
                    "supervisor": lifecycle_health.get(name, {}),
                }
                for name, runtime in self.runtimes.items()
            },
        }


# === Entry point for standalone run ===

async def run_orchestrator(
    personas_dir: str = "./personas",
    memory_dir: str = "./data/memory",
    api_key: str = "",
):
    """Run the multi-persona orchestrator."""
    orchestrator = SalesBotOrchestrator(
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
    asyncio.run(run_orchestrator())
