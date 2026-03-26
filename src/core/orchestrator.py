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
import json
import os
import time
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Union

from ..core.lifecycle import LifecycleManager, PersonaSupervisor, SupervisorConfig
from ..core.persona_manager import PersonaConfig, discover_personas
from ..core.router import MessageRouter, Decision
from ..core.output_validators import OutputValidator
from ..core.prompt_compiler import PromptCompiler
from ..core.vibe_schema import ResponseExample, GreetingPolicy as SchemaGreetingPolicy
from ..graph.state import build_initial_state
from ..memory.degraded_memory import DegradedMemoryFacade
from ..memory.embeddings import create_embedding_provider
from ..memory.memory_facade import MemoryFacade
from ..responders.generator import ResponseGenerator
from ..monitors.anti_spam import RateLimiter
from ..platforms import PlatformAdapter, SendOptions, UnknownPlatformError, create_adapter
from ..responders.response_composer import (
    ResponseComposer,
    GreetingPolicy,
)
from ..responders.preprocess import PreprocessNode
from ..responders.anaphora_resolver import AnaphoraResolver
from ..utils.dedup import DeduplicationStore
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..models.message import IncomingMessage

logger = get_logger("orchestrator")


async def _postgres_reachable(database_url: str, timeout_sec: float = 3.0) -> bool:
    """Return True if Postgres accepts a connection and ``SELECT 1``."""
    try:
        import asyncpg

        conn = await asyncio.wait_for(
            asyncpg.connect(database_url), timeout=timeout_sec
        )
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return True
    except Exception as e:
        logger.debug("Postgres ping failed: %s", e)
        return False


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
    memory: Union[MemoryFacade, DegradedMemoryFacade]
    dedup: DeduplicationStore  # Kept for backward compatibility during migration
    composer: ResponseComposer = None  # Response composer (greeting, formatting)
    preprocessor: PreprocessNode = None  # Deterministic shortcuts
    anaphora: AnaphoraResolver = None  # Context anaphora resolution
    output_validator: OutputValidator = None  # Post-generation output validation
    adapter: Optional[PlatformAdapter] = None  # Telegram* / VK / future platforms
    graph: Optional[Any] = None  # Compiled LangGraph (None if no DB URL or compile failed)
    # True only when DATABASE_URL was unset at build — enables legacy path in _handle_message.
    legacy_message_path: bool = False
    # Error message when DATABASE_URL was set but compile_persona_graph failed.
    graph_compile_error: Optional[str] = None
    #: True when DATABASE_URL was set but Postgres was unreachable at startup (degraded legacy).
    postgres_degraded: bool = False
    #: Why legacy path is active (logging / health).
    legacy_reason: str = ""
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
        self._health_snapshot_task: Optional[asyncio.Task] = None

    def load_personas(self) -> list[PersonaConfig]:
        """Load all persona configs from directory."""
        configs = discover_personas(self.personas_dir)
        logger.info(f"Discovered {len(configs)} personas in {self.personas_dir}")
        return configs

    async def _build_runtime(self, config: PersonaConfig) -> PersonaRuntime:
        """Build runtime components for a persona."""
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

        # Dedup first (per-persona SQLite); also backs DegradedMemoryFacade when DB is down.
        persona_memory_dir = os.path.join(self.memory_dir, config.name.lower().replace(" ", "_"))
        dedup = DeduplicationStore(
            storage_path=os.path.join(persona_memory_dir, "processed_messages.json")
        )

        database_url = (os.getenv("DATABASE_URL") or "").strip()
        use_degraded_memory = False
        postgres_degraded = False
        legacy_reason_mem = ""

        if not database_url:
            use_degraded_memory = True
            legacy_reason_mem = "DATABASE_URL not set"
        elif not await _postgres_reachable(database_url):
            logger.warning(
                f"[{config.name}] Postgres unreachable at startup — using legacy message path "
                f"(dedup-only memory); process continues without LangGraph"
            )
            use_degraded_memory = True
            postgres_degraded = True
            legacy_reason_mem = "postgres_unreachable"

        if use_degraded_memory:
            memory: Any = DegradedMemoryFacade(dedup, persona_name=config.name)
        else:
            emb = create_embedding_provider(cache_size=512)
            memory = await MemoryFacade.create(
                persona_name=config.name,
                database_url=database_url,
                embedding_provider=emb,
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
        runtime.postgres_degraded = postgres_degraded

        if use_degraded_memory:
            runtime.legacy_message_path = True
            runtime.legacy_reason = legacy_reason_mem
            runtime.graph = None
            runtime.graph_compile_error = None
            if legacy_reason_mem == "DATABASE_URL not set":
                logger.warning(
                    f"[{config.name}] LangGraph skipped: {legacy_reason_mem} (legacy message path only)"
                )
        else:
            runtime.legacy_message_path = False
            runtime.legacy_reason = ""
            runtime.graph_compile_error = None
            try:
                from ..graph.builder import compile_persona_graph

                runtime.graph = await compile_persona_graph(runtime)
                logger.info(f"Compiled LangGraph for {config.name}")
            except Exception as e:
                err_msg = str(e)
                logger.error(f"Failed to compile graph for {config.name}: {err_msg}")
                runtime.graph = None
                runtime.graph_compile_error = err_msg

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
        t0 = time.perf_counter()
        trace_path = "error"
        trace_decision = "n/a"
        trace_nodes = ""
        trace_llm_error = False

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
            if runtime.graph is not None:
                trace_path = "graph"
                logger.debug(
                    f"[{runtime.config.name}] execution_path=graph thread_id={thread_id}"
                )
                final_state = await runtime.graph.ainvoke(initial_state, config=config)

                if final_state.get("sent"):
                    runtime.stats["responses_sent"] += 1
                elif final_state.get("route_decision") == "ignore":
                    runtime.stats["ignored"] += 1

                if final_state.get("error_message"):
                    runtime.stats["errors"] += 1
                    logger.error(
                        f"[{runtime.config.name}] Graph error: {final_state['error_message']}"
                    )

                node_history = final_state.get("node_history", [])
                logger.debug(f"[{runtime.config.name}] Path: {' -> '.join(node_history)}")
                trace_decision = str(final_state.get("route_decision") or "n/a")
                trace_nodes = "->".join(node_history)
                trace_llm_error = bool(final_state.get("llm_failed"))
            elif runtime.legacy_message_path:
                trace_path = "legacy"
                logger.warning(
                    f"[{runtime.config.name}] running legacy path, reason: {runtime.legacy_reason or 'legacy'}"
                )
                leg_dec, leg_llm_err = await self._handle_message_legacy(msg, runtime)
                trace_decision = leg_dec or "n/a"
                trace_nodes = "legacy"
                trace_llm_error = leg_llm_err
            else:
                reason = runtime.graph_compile_error or "graph_unavailable"
                logger.error(
                    f"[{runtime.config.name}] graph unavailable (compile failed), "
                    f"not using legacy path: {reason}"
                )
                runtime.stats["errors"] += 1
                trace_path = "error"
                trace_nodes = "none"
                try:
                    await runtime.memory.mark_processed(msg.chat_id, msg.message_id, msg.text)
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"[{runtime.config.name}] Graph invocation error: {e}")
            runtime.stats["errors"] += 1
            trace_path = "error"
            trace_decision = "n/a"
            if not trace_nodes:
                trace_nodes = "exception"
            try:
                await runtime.memory.mark_processed(msg.chat_id, msg.message_id, msg.text)
            except Exception:
                pass

        finally:
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            logger.info(
                json.dumps(
                    {
                        "event": "message_trace",
                        "persona": runtime.config.name,
                        "path": trace_path,
                        "decision": trace_decision,
                        "nodes": trace_nodes,
                        "latency_ms": latency_ms,
                        "user_id": str(msg.user_id),
                        "llm_error": trace_llm_error,
                    },
                    ensure_ascii=False,
                )
            )
            runtime.state = BotState.IDLE

    async def _handle_message_legacy(
        self, msg: IncomingMessage, runtime: PersonaRuntime
    ) -> tuple[str, bool]:
        """Linear route → generate → send when LangGraph is unavailable (no DATABASE_URL).

        Returns:
            Tuple of trace decision string and whether an LLM/generation failure occurred.
        """
        if await runtime.memory.is_processed(msg.chat_id, msg.message_id, msg.text):
            return ("skipped_duplicate", False)

        route_result = await runtime.router.route(
            message_text=msg.text or "",
            chat_context=[],
            is_dm=msg.is_dm,
        )

        if route_result.decision in (Decision.IGNORE, Decision.DISENGAGE):
            runtime.stats["ignored"] += 1
            try:
                await runtime.memory.mark_processed(msg.chat_id, msg.message_id, msg.text)
            except Exception as mark_err:
                logger.warning(
                    f"[{runtime.config.name}] legacy mark_processed after ignore: {mark_err}"
                )
            return (route_result.decision.value, False)

        try:
            if msg.is_dm:
                user_context = await runtime.memory.get_user_context(msg.user_id)
                get_hist = getattr(runtime.memory, "get_dm_transcript_for_prompt", None)
                dm_hist = (
                    await get_hist(msg.user_id)
                    if callable(get_hist)
                    else ""
                )
                if not (dm_hist or "").strip():
                    dm_hist = "(ещё нет переписки в этой сессии)"
                get_stage = getattr(runtime.memory, "get_funnel_stage", None)
                funnel_st = (
                    await get_stage(msg.user_id) if callable(get_stage) else "unknown"
                )
                response = await runtime.generator.generate_dm_response(
                    message_text=msg.text or "",
                    user_memory=user_context,
                    dm_history=dm_hist,
                    group_context="",
                    funnel_stage=funnel_st or "unknown",
                )
            else:
                response = await runtime.generator.generate_group_response(
                    message_text=msg.text or "",
                    chat_context=[],
                    chat_vibe=None,
                )

            if response is None:
                logger.error(f"[{runtime.config.name}] legacy: generator returned no response (LLM error)")
                return ("error", True)

            if not response.text:
                return ("error", False)

            if not runtime.adapter:
                return ("error", False)

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
                try:
                    await runtime.memory.mark_processed(msg.chat_id, msg.message_id, msg.text)
                except Exception as mark_err:
                    logger.warning(
                        f"[{runtime.config.name}] legacy mark_processed after send: {mark_err}"
                    )
                return (route_result.decision.value, False)

            return ("error", False)

        except Exception as e:
            logger.error(f"[{runtime.config.name}] Legacy handler error: {e}")

        return ("error", True)

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
            max_restarts=2,
            backoff_schedule_sec=(5.0, 30.0, 120.0),
            jitter=False,
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

        self._health_snapshot_task = asyncio.create_task(
            self._health_snapshot_loop(),
            name="orchestrator-health-snapshot",
        )

        # Wait for shutdown or any supervisor to complete
        await self._lifecycle.wait_for_shutdown()

    async def stop(self):
        """Stop all running personas gracefully."""
        logger.info("Stopping orchestrator...")
        self._running = False

        if self._health_snapshot_task and not self._health_snapshot_task.done():
            self._health_snapshot_task.cancel()
            try:
                await self._health_snapshot_task
            except asyncio.CancelledError:
                pass
            self._health_snapshot_task = None

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

            emb = getattr(runtime.memory, "embeddings", None)
            if emb is not None and hasattr(emb, "clear_cache"):
                try:
                    emb.clear_cache()
                except Exception as e:
                    logger.debug(f"[{name}] Embedding cache clear error: {e}")

            # Close memory facade (Supabase connection pool)
            try:
                await runtime.memory.close()
            except Exception as e:
                logger.debug(f"[{name}] Memory close error: {e}")

        logger.info("All personas stopped")

    @staticmethod
    def _message_path_mode(runtime: PersonaRuntime) -> str:
        """graph | legacy | error — how inbound messages are handled."""
        if runtime.graph is not None:
            return "graph"
        if runtime.legacy_message_path:
            return "legacy"
        return "error"

    async def _health_snapshot_loop(self) -> None:
        """Write ``get_status()``-derived JSON for ``scripts/health_check.py``."""
        path = Path(os.getenv("SALES_BOT_HEALTH_FILE", "/tmp/sales-bot-health.json"))
        try:
            interval = float(os.getenv("SALES_BOT_HEALTH_INTERVAL_SEC", "30"))
        except ValueError:
            interval = 30.0
        interval = max(5.0, interval)

        while self._running:
            try:
                payload = {
                    "source": "sales-bot-orchestrator",
                    "timestamp": time.time(),
                    "running": self._running,
                    "personas": self.get_status()["personas"],
                }
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(path)
            except Exception as e:
                logger.debug("health snapshot write skipped: %s", e)
            await asyncio.sleep(interval)

    def get_status(self) -> dict:
        """Get status of all personas."""
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
                    "uptime_sec": time.time() - runtime.stats["started_at"]
                    if runtime.stats["started_at"]
                    else 0,
                    "last_message_sec_ago": time.time() - runtime.stats["last_message_at"]
                    if runtime.stats["last_message_at"]
                    else None,
                    "supervisor": lifecycle_health.get(name, {}),
                    "message_path_mode": self._message_path_mode(runtime),
                    "postgres_degraded": runtime.postgres_degraded,
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
