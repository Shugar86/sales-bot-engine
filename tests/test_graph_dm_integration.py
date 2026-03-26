"""
Integration: DM path through compiled LangGraph (production checkpointer).

Uses the same compile_persona_graph + AsyncPostgresSaver path as production when
``DATABASE_URL`` is set. The real :class:`~src.core.router.MessageRouter` is used;
for ``is_dm=True`` it returns ``Decision.SALES_DM`` without calling the LLM — we do
not mock the router to ``RESPOND``. Generator and platform adapter are stubs;
memory is AsyncMock (no Supabase required for this test).
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.orchestrator import PersonaRuntime
from src.core.persona_manager import PersonaConfig
from src.core.router import MessageRouter
from src.graph.builder import build_config, compile_persona_graph
from src.graph.state import build_initial_state
from src.models.message import IncomingMessage, Platform
from src.monitors.anti_spam import RateLimiter
from src.platforms.capabilities import PlatformCapabilities
from src.responders.generator import GeneratedResponse
from src.utils.dedup import DeduplicationStore
from src.utils.llm_client import LLMClient


pytestmark = pytest.mark.integration

requires_database = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set — need Postgres for LangGraph AsyncPostgresSaver",
)


def _minimal_contract() -> dict:
    """Contract dict compatible with MessageRouter._build_persona_summary."""
    return {
        "persona": {
            "name": "IntegrationDM",
            "backstory": "Test persona",
            "speaking_style": {"tone": "casual", "patterns": [], "forbidden": []},
            "group_context_examples": [],
        },
        "product": {"products": []},
        "triggers": {"respond_to": [], "ignore": []},
        "conversation_flow": {
            "group_chat": {"strategy": "test", "steps": ["test"]},
            "direct_message": {"strategy": "", "steps": []},
            "never": [],
        },
    }


def _dm_message() -> IncomingMessage:
    return IncomingMessage(
        message_id=42,
        chat_id="dm_chat_1",
        chat_title="DM",
        user_id="user_dm_1",
        username="@buyer",
        display_name="Buyer",
        text="Привет, хочу узнать про продукт подробнее",
        is_dm=True,
        date=1700000000,
        platform=Platform.TELEGRAM_USERBOT,
    )


@requires_database
@pytest.mark.asyncio
async def test_dm_graph_path_uses_real_router_sales_dm() -> None:
    """DM message: route (SALES_DM) -> antispam -> generate -> validate -> send -> memory."""
    tmp = tempfile.mkdtemp()
    dedup = DeduplicationStore(
        storage_path=os.path.join(tmp, "processed_messages.json"),
    )

    config = PersonaConfig(name="integration_dm", yaml_path="")

    llm = LLMClient(api_key="test-key-unused-for-dm-route", timeout=5)
    router = MessageRouter(
        llm_client=llm,
        model="openrouter/google/gemini-2.0-flash-lite",
        contract=_minimal_contract(),
    )

    memory = AsyncMock()
    memory.is_processed = AsyncMock(return_value=False)
    memory.mark_processed = AsyncMock()
    memory.get_last_tool = AsyncMock(return_value="")
    memory.get_last_tool_args = AsyncMock(return_value={})
    memory.is_first_response = AsyncMock(return_value=True)
    memory.get_recent_messages = AsyncMock(return_value=[])
    memory.get_user_context = AsyncMock(return_value="")
    memory.get_recommendations = AsyncMock(return_value=[])
    memory.get_funnel_stage = AsyncMock(return_value="unknown")
    memory.get_dm_transcript_for_prompt = AsyncMock(return_value="")
    memory.set_funnel_stage = AsyncMock()
    memory.is_repeating_response = AsyncMock(return_value=False)
    memory.record_bot_response = AsyncMock()
    memory.record_dm = AsyncMock()
    memory.record_group_message = AsyncMock()
    memory.search_semantic = AsyncMock(return_value=[])
    memory.get_dm_inbound_streak = AsyncMock(return_value=0)
    memory.increment_dm_inbound_streak = AsyncMock(return_value=1)
    memory.reset_dm_inbound_streak = AsyncMock()

    generator = AsyncMock()
    generator.generate_dm_response = AsyncMock(
        return_value=GeneratedResponse(
            text="Вот что могу рассказать по делу.",
            stage="interested",
            remember=[],
            tone="friendly",
        )
    )

    output_validator = MagicMock()
    output_validator.validate = MagicMock(
        return_value=MagicMock(
            violations=[],
            cleaned_text="Вот что могу рассказать по делу.",
        )
    )

    caps = PlatformCapabilities(
        supports_dm=True,
        supports_group_reply=True,
        supports_reactions=True,
        supports_edit=False,
        supports_fetch_thread_context=False,
        supports_typing_indicator=True,
    )
    adapter = MagicMock()
    adapter.capabilities = MagicMock(return_value=caps)
    adapter.send_reply = AsyncMock(return_value=True)
    adapter.send_typing = AsyncMock(return_value=None)

    preprocessor = MagicMock()
    preprocessor.process = MagicMock(
        return_value=MagicMock(
            has_shortcut=False,
            skip_generation=False,
            shortcut_response=None,
            pipeline_step="",
        )
    )
    anaphora = MagicMock()
    anaphora.resolve = MagicMock(
        return_value=MagicMock(
            is_resolved=False,
            resolved_question="Привет, хочу узнать про продукт подробнее",
        )
    )

    antispam = RateLimiter(
        min_delay_sec=0.0,
        max_delay_sec=0.0,
        max_global_per_hour=1000,
        max_per_chat_per_hour=1000,
        cooldown_sec=0.0,
    )

    runtime = PersonaRuntime(
        config=config,
        llm=llm,
        router=router,
        generator=generator,
        antispam=antispam,
        memory=memory,
        dedup=dedup,
        composer=None,
        preprocessor=preprocessor,
        anaphora=anaphora,
        output_validator=output_validator,
        adapter=adapter,
    )

    graph = await compile_persona_graph(
        runtime, database_url=os.environ["DATABASE_URL"]
    )
    runtime.graph = graph

    msg = _dm_message()
    initial = build_initial_state(msg)
    thread_id = f"{config.name}:{msg.user_id}:{msg.chat_id}"
    runnable_config = build_config(runtime, thread_id)

    final_state = await graph.ainvoke(initial, config=runnable_config)

    assert final_state.get("route_decision") == "respond"
    assert final_state.get("sent") is True
    history = final_state.get("node_history") or []
    for step in ("dedup", "preprocess", "parallel_retrieval", "route", "antispam", "generate", "validate", "send", "memory"):
        assert step in history, f"missing {step} in {history}"

    adapter.send_reply.assert_awaited()
    memory.mark_processed.assert_awaited()
    memory.record_dm.assert_awaited()
    generator.generate_dm_response.assert_awaited()

    await llm.close()
