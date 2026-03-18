"""
Orchestrator Legacy — v1 Single-Persona Sales Bot (Bot API)

State machine: IDLE → FETCHING → ROUTING → GENERATING → SENDING → MEMORY_UPDATE

Connects: Router + Generator + Memory + AntiSpam + Dedup
"""

import asyncio
import os
from enum import Enum
from typing import Optional

from .router import MessageRouter, Decision, RouteResult
from ..responders.generator import ResponseGenerator, GeneratedResponse
from ..monitors.telegram_monitor import TelegramMonitor, TelegramMessage
from ..monitors.anti_spam import RateLimiter
from ..memory.user_memory import UserMemoryStore
from ..contracts.loader import Contract, load_contract, reload_if_changed
from ..utils.dedup import DeduplicationStore
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from config.settings import AppConfig

logger = get_logger("orchestrator")


class BotState(Enum):
    IDLE = "idle"
    FETCHING = "fetching"
    ROUTING = "routing"
    GENERATING = "generating"
    SENDING = "sending"
    MEMORY_UPDATE = "memory_update"
    ERROR = "error"


class SalesBotOrchestrator:
    """
    Orchestrator v1 — single persona, Bot API.

    State machine:
    1. IDLE — waiting for messages
    2. FETCHING — message received
    3. ROUTING — fast model decides respond/ignore
    4. GENERATING — slow model generates response
    5. SENDING — anti-spam + send
    6. MEMORY_UPDATE — update user memory
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.state = BotState.IDLE

        # LLM clients
        self.llm = LLMClient(
            api_key=config.llm.api_key,
            api_base=config.llm.api_base,
            timeout=config.llm.timeout,
        )

        # Contract
        self.contract: Optional[Contract] = None

        # Components (initialized after contract loads)
        self.router: Optional[MessageRouter] = None
        self.generator: Optional[ResponseGenerator] = None
        self.telegram: Optional[TelegramMonitor] = None
        self.antispam = RateLimiter(
            min_delay_sec=config.antispam.min_delay_sec,
            max_delay_sec=config.antispam.max_delay_sec,
            max_global_per_hour=config.antispam.max_responses_per_hour,
            max_per_chat_per_hour=config.antispam.max_responses_per_chat_per_hour,
            cooldown_sec=config.antispam.cooldown_after_response_sec,
        )
        self.memory = UserMemoryStore(memory_dir=config.memory.memory_dir)
        self.dedup = DeduplicationStore(
            storage_path=os.path.join(config.memory.memory_dir, "processed_messages.json")
        )

        # Stats
        self.stats = {
            "messages_processed": 0,
            "responses_sent": 0,
            "ignored": 0,
            "errors": 0,
            "started_at": None,
        }

    async def initialize(self):
        """Initialize all components."""
        logger.info("Initializing Sales Bot Orchestrator (v1)...")

        # 1. Load contract
        self.contract = load_contract(self.config.contract_path)
        if not self.contract.valid:
            logger.error(f"Contract invalid: {self.contract.errors}")
            raise ValueError(f"Invalid contract: {self.contract.errors}")

        logger.info(f"Contract loaded: {self.contract.persona_name}")

        # 2. Initialize router and generator
        self.router = MessageRouter(
            llm_client=self.llm,
            model=self.config.llm.fast_model,
            contract=self.contract.data,
        )

        self.generator = ResponseGenerator(
            llm_client=self.llm,
            model=self.config.llm.slow_model,
            contract=self.contract.data,
        )

        # 3. Telegram monitor
        if self.config.telegram.bot_token:
            self.telegram = TelegramMonitor(
                bot_token=self.config.telegram.bot_token,
                poll_timeout=self.config.telegram.long_poll_timeout,
            )
            logger.info("Telegram monitor ready")
        else:
            logger.warning("No Telegram bot token — monitor mode only")

        logger.info("Orchestrator initialized successfully")

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        await self.llm.close()
        if self.telegram:
            await self.telegram.close()

    async def handle_message(self, msg: TelegramMessage):
        """
        Process one message. State machine pipeline.

        Args:
            msg: Incoming message
        """
        self.state = BotState.FETCHING
        self.stats["messages_processed"] += 1

        # === DEDUP ===
        if self.dedup.is_processed(msg.chat_id, msg.message_id, msg.text):
            logger.debug(f"Skipping duplicate: {msg.message_id}")
            return

        # === ROUTING (Fast Model) ===
        self.state = BotState.ROUTING

        chat_context = self.memory.get_recent_messages(msg.chat_id, limit=3)

        route_result = await self.router.route(
            message_text=msg.text,
            chat_context=chat_context,
            is_dm=msg.is_dm,
        )

        logger.info(
            f"Route: {route_result.decision.value} "
            f"(conf={route_result.confidence:.1f}, reason={route_result.reason})"
        )

        if route_result.decision == Decision.IGNORE:
            self.stats["ignored"] += 1
            self.state = BotState.IDLE
            self.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
            return
        
        # DISENGAGE — человек просит отстать
        if route_result.decision == Decision.DISENGAGE:
            logger.info(f"User asked to stop in {msg.chat_id}")
            self.stats["ignored"] += 1
            self.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
            self.state = BotState.IDLE
            return

        # === GENERATION (Slow Model) ===
        self.state = BotState.GENERATING

        response: Optional[GeneratedResponse] = None

        if msg.is_dm:
            user_memory = self.memory.get_user_context(msg.user_id)
            group_context = self.memory.get_group_context_for_user(msg.user_id)
            funnel_stage = self.memory.get_funnel_stage(msg.user_id)

            response = await self.generator.generate_dm_response(
                message_text=msg.text,
                user_memory=user_memory,
                dm_history="",
                group_context=group_context,
                funnel_stage=funnel_stage,
                persona_name=self.contract.persona_name if self.contract else "",
            )
        else:
            response = await self.generator.generate_group_response(
                message_text=msg.text,
                chat_context=chat_context,
                persona_name=self.contract.persona_name if self.contract else "",
            )

        if not response or not response.text:
            logger.info(f"No response generated for {msg.message_id}")
            self.state = BotState.IDLE
            self.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
            return

        # === SEND (Anti-Spam) ===
        self.state = BotState.SENDING

        can_send, reason = self.antispam.can_send(msg.chat_id)

        sent = False
        if can_send:
            delay = self.antispam.get_random_delay()
            await asyncio.sleep(delay)

            sent = await self.telegram.send_message(
                chat_id=msg.chat_id,
                text=response.text,
                reply_to=msg.message_id if not msg.is_dm else None,
            )

            if sent:
                self.antispam.record_send(msg.chat_id)
        else:
            logger.warning(f"Anti-spam blocked to {msg.chat_id}: {reason}")

        if sent:
            self.stats["responses_sent"] += 1

            # === MEMORY ===
            self.state = BotState.MEMORY_UPDATE

            if msg.is_dm:
                self.memory.record_dm(
                    user_id=msg.user_id,
                    username=msg.username,
                    display_name=msg.display_name,
                    message=msg.text,
                    response=response.text,
                    stage=response.stage,
                )
            else:
                self.memory.record_group_message(
                    user_id=msg.user_id,
                    username=msg.username,
                    display_name=msg.display_name,
                    chat_id=msg.chat_id,
                    chat_title=msg.chat_title,
                    message=msg.text,
                )

            for note in response.remember:
                self.memory.add_note(msg.user_id, note)

        self.dedup.mark_processed(msg.chat_id, msg.message_id, msg.text)
        self.state = BotState.IDLE

    async def run(self):
        """Main loop."""
        await self.initialize()

        if not self.telegram:
            logger.error("No Telegram monitor — cannot run")
            return

        from datetime import datetime
        self.stats["started_at"] = datetime.utcnow().isoformat()

        logger.info(
            f"Sales Bot running. "
            f"Chats: {self.config.telegram.monitor_chats or 'ALL'}. "
            f"Contract: {self.contract.persona_name}"
        )

        await self.telegram.poll_loop(
            callback=self.handle_message,
            allowed_chats=self.config.telegram.monitor_chats,
        )

    def get_status(self) -> dict:
        """Bot status for monitoring."""
        return {
            "state": self.state.value,
            "contract": self.contract.persona_name if self.contract else None,
            "stats": self.stats,
            "antispam": self.antispam.get_stats(),
            "dedup": self.dedup.get_stats(),
        }
