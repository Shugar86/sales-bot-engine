"""Tests for LangGraph nodes.

Unit tests for each node in isolation, mocking dependencies.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.graph.nodes import (
    dedup_node,
    preprocess_node,
    semantic_retrieval_node,
    anaphora_node,
    map_router_decision_for_graph,
    route_node,
    antispam_node,
    generate_node,
    validate_node,
    send_node,
    memory_node,
    emoji_node,
    send_shortcut_node,
)
from src.graph.state import build_initial_state
from src.models.message import IncomingMessage, Platform
from src.core.router import Decision, RouteResult
from src.core.vibe_schema import AntiSpamConfig
from src.platforms.capabilities import PlatformCapabilities


@pytest.fixture
def sample_message():
    """Create a sample incoming message."""
    return IncomingMessage(
        message_id=789,
        chat_id="chat456",
        chat_title="Test Chat",
        user_id="user123",
        username="@testuser",
        display_name="Test User",
        text="Hello, what dog food do you recommend?",
        is_dm=True,
        date=1700000000,
        platform=Platform.TELEGRAM_USERBOT,
    )


@pytest.fixture
def mock_runtime():
    """Create a mock runtime with all necessary components."""
    runtime = MagicMock()

    # Mock memory
    runtime.memory = AsyncMock()
    runtime.memory.is_processed = AsyncMock(return_value=False)
    runtime.memory.mark_processed = AsyncMock()
    runtime.memory.get_user_context = AsyncMock(return_value="User context")
    runtime.memory.get_last_tool = AsyncMock(return_value="")
    runtime.memory.get_last_tool_args = AsyncMock(return_value={})
    runtime.memory.is_first_response = AsyncMock(return_value=True)
    runtime.memory.record_dm = AsyncMock()
    runtime.memory.record_group_message = AsyncMock()
    runtime.memory.search_semantic = AsyncMock(return_value=[])
    runtime.memory.get_recent_messages = AsyncMock(return_value=[])
    runtime.memory.get_dm_transcript_for_prompt = AsyncMock(return_value="User: hi\nBot: hello")
    runtime.memory.get_funnel_stage = AsyncMock(return_value="unknown")
    runtime.memory.set_funnel_stage = AsyncMock()
    runtime.memory.record_bot_response = AsyncMock()
    runtime.memory.is_repeating_response = AsyncMock(return_value=False)
    runtime.memory.get_dm_inbound_streak = AsyncMock(return_value=0)
    runtime.memory.increment_dm_inbound_streak = AsyncMock(return_value=1)
    runtime.memory.reset_dm_inbound_streak = AsyncMock()

    # Mock preprocessor
    runtime.preprocessor = MagicMock()
    runtime.preprocessor.process = MagicMock(return_value=MagicMock(
        has_shortcut=False,
        skip_generation=False,
        shortcut_response=None,
        pipeline_step=""
    ))

    # Mock anaphora resolver
    runtime.anaphora = MagicMock()
    runtime.anaphora.resolve = MagicMock(return_value=MagicMock(
        is_resolved=False,
        resolved_question="Hello, what dog food do you recommend?"
    ))

    # Mock router
    runtime.router = AsyncMock()
    runtime.router.route = AsyncMock(return_value=RouteResult(
        decision=Decision.RESPOND,
        confidence=0.9,
        reason="User is asking a question"
    ))

    # Mock anti-spam (RateLimiter side; YAML limits come from config.anti_spam)
    runtime.antispam = MagicMock()
    runtime.antispam.should_leave_on_read = MagicMock(return_value=False)
    runtime.antispam.should_use_emoji_reaction = MagicMock(return_value=False)
    runtime.antispam.can_send = MagicMock(return_value=(True, ""))
    runtime.antispam.get_random_delay = MagicMock(return_value=1.0)
    runtime.antispam.record_send = MagicMock()

    # Mock generator
    runtime.generator = AsyncMock()
    runtime.generator.generate_dm_response = AsyncMock(return_value=MagicMock(
        text="I recommend this food",
        stage="interested",
        remember=["User has a dog"],
        tone="friendly"
    ))
    runtime.generator.generate_group_response = AsyncMock(return_value=MagicMock(
        text="Group response",
        stage="",
        remember=[],
        tone="casual"
    ))

    # Mock output validator
    runtime.output_validator = MagicMock()
    runtime.output_validator.validate = MagicMock(return_value=MagicMock(
        violations=[],
        cleaned_text="Cleaned response text"
    ))

    # Mock platform adapter
    caps = PlatformCapabilities(
        supports_dm=True,
        supports_group_reply=True,
        supports_reactions=True,
        supports_edit=False,
        supports_fetch_thread_context=False,
        supports_typing_indicator=True,
    )
    runtime.adapter = MagicMock()
    runtime.adapter.capabilities = MagicMock(return_value=caps)
    runtime.adapter.send_reply = AsyncMock(return_value=True)
    runtime.adapter.send_reaction = AsyncMock(return_value=True)
    runtime.adapter.send_typing = AsyncMock(return_value=None)

    # Mock config
    runtime.config = MagicMock()
    runtime.config.name = "test_persona"
    runtime.config.platform = "telegram"
    runtime.config.account_type = "userbot"
    runtime.config.anti_spam = AntiSpamConfig(
        typing_simulation=False,
        random_typos=False,
        min_delay_between_messages=1,
        max_delay_between_messages=2,
    )

    return runtime


@pytest.fixture
def mock_config(mock_runtime):
    """Create a mock RunnableConfig."""
    return {
        "configurable": {
            "runtime": mock_runtime,
        }
    }


class TestDedupNode:
    """Test dedup_node."""

    @pytest.mark.asyncio
    async def test_not_duplicate(self, sample_message, mock_config, mock_runtime):
        """Should mark is_duplicate=False for new message."""
        state = build_initial_state(sample_message)
        mock_runtime.memory.is_processed = AsyncMock(return_value=False)

        result = await dedup_node(state, mock_config)

        assert result["is_duplicate"] is False
        assert "dedup" in result["node_history"]

    @pytest.mark.asyncio
    async def test_is_duplicate(self, sample_message, mock_config, mock_runtime):
        """Should mark is_duplicate=True for processed message."""
        state = build_initial_state(sample_message)
        mock_runtime.memory.is_processed = AsyncMock(return_value=True)

        result = await dedup_node(state, mock_config)

        assert result["is_duplicate"] is True


class TestPreprocessNode:
    """Test preprocess_node."""

    @pytest.mark.asyncio
    async def test_normal_processing(self, sample_message, mock_config, mock_runtime):
        """Normal message should continue processing."""
        state = build_initial_state(sample_message)
        mock_runtime.preprocessor.process = MagicMock(return_value=MagicMock(
            has_shortcut=False,
            skip_generation=False,
            shortcut_response=None,
        ))

        result = await preprocess_node(state, mock_config)

        assert result.get("preprocess_skip") is not True
        assert result.get("preprocess_shortcut") is None
        assert "preprocess" in result["node_history"]

    @pytest.mark.asyncio
    async def test_skip_generation(self, sample_message, mock_config, mock_runtime):
        """Trivial message should skip generation."""
        state = build_initial_state(sample_message)
        mock_runtime.preprocessor.process = MagicMock(return_value=MagicMock(
            has_shortcut=True,
            skip_generation=True,
            shortcut_response=None,
        ))

        result = await preprocess_node(state, mock_config)

        assert result["preprocess_skip"] is True

    @pytest.mark.asyncio
    async def test_shortcut_response(self, sample_message, mock_config, mock_runtime):
        """Shortcut response should be captured."""
        state = build_initial_state(sample_message)
        mock_runtime.preprocessor.process = MagicMock(return_value=MagicMock(
            has_shortcut=True,
            skip_generation=False,
            shortcut_response="Quick answer",
        ))

        result = await preprocess_node(state, mock_config)

        assert result["preprocess_shortcut"] == "Quick answer"


class TestSemanticRetrievalNode:
    """Test semantic_retrieval_node."""

    @pytest.mark.asyncio
    async def test_empty_message(self, sample_message, mock_config, mock_runtime):
        """Empty message should return empty context."""
        state = build_initial_state(sample_message)
        state["message"].text = ""

        result = await semantic_retrieval_node(state, mock_config)

        assert result["semantic_context"] == []

    @pytest.mark.asyncio
    async def test_with_results(self, sample_message, mock_config, mock_runtime):
        """Should return semantic context when found."""
        state = build_initial_state(sample_message)
        mock_runtime.memory.search_semantic = AsyncMock(return_value=[
            {"text": "Previous message 1", "role": "user", "similarity": 0.85},
            {"text": "Previous message 2", "role": "bot", "similarity": 0.75},
        ])

        result = await semantic_retrieval_node(state, mock_config)

        assert len(result["semantic_context"]) == 2
        assert "Previous message 1" in result["semantic_context"]


class TestAnaphoraNode:
    """Test anaphora_node."""

    @pytest.mark.asyncio
    async def test_resolution(self, sample_message, mock_config, mock_runtime):
        """Should resolve anaphoric references."""
        state = build_initial_state(sample_message)
        state["message"].text = "How much does it cost?"
        mock_runtime.anaphora.resolve = MagicMock(return_value=MagicMock(
            is_resolved=True,
            resolved_question="How much does the dog food cost?"
        ))

        result = await anaphora_node(state, mock_config)

        assert result["resolved_question"] == "How much does the dog food cost?"


class TestRouteNode:
    """Test route_node."""

    @pytest.mark.asyncio
    async def test_respond_decision(self, sample_message, mock_config, mock_runtime):
        """Should return respond decision."""
        state = build_initial_state(sample_message)
        state["resolved_question"] = sample_message.text
        mock_runtime.router.route = AsyncMock(return_value=RouteResult(
            decision=Decision.RESPOND,
            confidence=0.9,
            reason="Valid question"
        ))

        result = await route_node(state, mock_config)

        assert result["route_decision"] == "respond"

    @pytest.mark.asyncio
    async def test_ignore_decision(self, sample_message, mock_config, mock_runtime):
        """Should return ignore decision."""
        state = build_initial_state(sample_message)
        mock_runtime.router.route = AsyncMock(return_value=RouteResult(
            decision=Decision.IGNORE,
            confidence=0.8,
            reason="Not relevant"
        ))

        result = await route_node(state, mock_config)

        assert result["route_decision"] == "ignore"

    @pytest.mark.asyncio
    async def test_sales_dm_maps_to_respond(self, sample_message, mock_config, mock_runtime):
        """DM router returns SALES_DM — must map to respond, not emoji."""
        state = build_initial_state(sample_message)
        state["resolved_question"] = sample_message.text
        mock_runtime.router.route = AsyncMock(return_value=RouteResult(
            decision=Decision.SALES_DM,
            confidence=1.0,
            reason="Direct message",
        ))

        result = await route_node(state, mock_config)

        assert result["route_decision"] == "respond"
        assert "error_message" not in result

    @pytest.mark.asyncio
    async def test_disengage_maps_to_ignore(self, sample_message, mock_config, mock_runtime):
        """DISENGAGE should end reply path like ignore."""
        state = build_initial_state(sample_message)
        state["resolved_question"] = sample_message.text
        mock_runtime.router.route = AsyncMock(return_value=RouteResult(
            decision=Decision.DISENGAGE,
            confidence=1.0,
            reason="User asked to stop",
        ))

        result = await route_node(state, mock_config)

        assert result["route_decision"] == "ignore"

    @pytest.mark.asyncio
    async def test_parse_failed_adds_parse_warnings(self, sample_message, mock_config, mock_runtime):
        """Router parse failure should surface in state for observability."""
        state = build_initial_state(sample_message)
        state["resolved_question"] = sample_message.text
        mock_runtime.router.route = AsyncMock(
            return_value=RouteResult(
                decision=Decision.IGNORE,
                confidence=0.0,
                reason="invalid decision: FOO",
                parse_failed=True,
            )
        )

        result = await route_node(state, mock_config)

        assert result["parse_warnings"] == ["invalid decision: FOO"]


class TestMapRouterDecisionForGraph:
    """Unit tests for router Decision → graph route_decision mapping."""

    def test_all_known_decisions_mapped(self) -> None:
        """Every Decision enum member must have explicit graph semantics."""
        for d in Decision:
            branch, err = map_router_decision_for_graph(d)
            assert branch in ("ignore", "respond", "error")
            assert err is None or branch == "error"

    def test_invalid_type_returns_error(self) -> None:
        """Non-Decision values are configuration errors, not silent emoji."""
        branch, err = map_router_decision_for_graph(object())
        assert branch == "error"
        assert err is not None
        assert "Invalid" in err or "invalid" in err.lower()


class TestAntispamNode:
    """Test antispam_node."""

    @pytest.mark.asyncio
    async def test_can_send(self, sample_message, mock_config, mock_runtime):
        """Should allow sending when under limits."""
        state = build_initial_state(sample_message)
        mock_runtime.antispam.can_send = MagicMock(return_value=(True, ""))

        result = await antispam_node(state, mock_config)

        assert result["can_send"] is True
        assert result["send_delay"] == 1.0  # from mock

    @pytest.mark.asyncio
    async def test_blocked(self, sample_message, mock_config, mock_runtime):
        """Should block when over limits."""
        state = build_initial_state(sample_message)
        mock_runtime.antispam.can_send = MagicMock(return_value=(False, "rate limited"))

        result = await antispam_node(state, mock_config)

        assert result["can_send"] is False

    @pytest.mark.asyncio
    async def test_dm_fourth_inbound_blocked_without_bot_reply(
        self, sample_message, mock_config, mock_runtime
    ):
        """After N=3 admitted DMs, streak 3 blocks the 4th (no increment on block)."""
        state = build_initial_state(sample_message)
        state["message"].is_dm = True

        class _Streak:
            def __init__(self) -> None:
                self.n = 0

            async def get(self, _uid: str) -> int:
                return self.n

            async def inc(self, _uid: str) -> int:
                self.n += 1
                return self.n

        s = _Streak()
        mock_runtime.memory.get_dm_inbound_streak = AsyncMock(side_effect=s.get)
        mock_runtime.memory.increment_dm_inbound_streak = AsyncMock(side_effect=s.inc)

        for i in range(3):
            r = await antispam_node(state, mock_config)
            assert r["can_send"] is True, f"call {i + 1}"
        r4 = await antispam_node(state, mock_config)
        assert r4["can_send"] is False
        assert mock_runtime.memory.increment_dm_inbound_streak.await_count == 3

    @pytest.mark.asyncio
    async def test_group_chat_skips_dm_burst(self, sample_message, mock_config, mock_runtime):
        """Group messages must not touch DM inbound streak."""
        state = build_initial_state(sample_message)
        state["message"].is_dm = False

        await antispam_node(state, mock_config)

        mock_runtime.memory.get_dm_inbound_streak.assert_not_awaited()
        mock_runtime.memory.increment_dm_inbound_streak.assert_not_awaited()


class TestGenerateNode:
    """Test generate_node."""

    @pytest.mark.asyncio
    async def test_dm_generation(self, sample_message, mock_config, mock_runtime):
        """Should generate response for DM."""
        state = build_initial_state(sample_message)
        state["message"].is_dm = True
        state["resolved_question"] = sample_message.text
        state["semantic_context"] = ["Previous context"]

        result = await generate_node(state, mock_config)

        assert result["generated_text"] == "I recommend this food"
        mock_runtime.generator.generate_dm_response.assert_awaited()
        dm_call = mock_runtime.generator.generate_dm_response.call_args
        assert "User: hi" in (dm_call.kwargs.get("dm_history") or "")

    @pytest.mark.asyncio
    async def test_dm_generation_llm_failed_when_none(self, sample_message, mock_config, mock_runtime):
        """Generator returned None (LLM error) → llm_failed True."""
        state = build_initial_state(sample_message)
        state["message"].is_dm = True
        mock_runtime.generator.generate_dm_response = AsyncMock(return_value=None)

        result = await generate_node(state, mock_config)

        assert result.get("generated_text") is None
        assert result.get("llm_failed") is True

    @pytest.mark.asyncio
    async def test_group_generation(self, sample_message, mock_config, mock_runtime):
        """Should generate response for group chat."""
        state = build_initial_state(sample_message)
        state["message"].is_dm = False

        result = await generate_node(state, mock_config)

        assert result["generated_text"] == "Group response"


class TestValidateNode:
    """Test validate_node."""

    @pytest.mark.asyncio
    async def test_valid_output(self, sample_message, mock_config, mock_runtime):
        """Should return validated text."""
        state = build_initial_state(sample_message)
        state["generated_text"] = "Some generated text"
        mock_runtime.output_validator.validate = MagicMock(return_value=MagicMock(
            violations=[],
            cleaned_text="Cleaned text"
        ))

        result = await validate_node(state, mock_config)

        assert result["validated_text"] == "Cleaned text"

    @pytest.mark.asyncio
    async def test_empty_after_validation(self, sample_message, mock_config, mock_runtime):
        """Should return None if validation clears text."""
        state = build_initial_state(sample_message)
        state["generated_text"] = "Bad text"
        mock_runtime.output_validator.validate = MagicMock(return_value=MagicMock(
            violations=["banned_phrase"],
            cleaned_text=""
        ))

        result = await validate_node(state, mock_config)

        assert result["validated_text"] is None


class TestSendNode:
    """Test send_node."""

    @pytest.mark.asyncio
    async def test_successful_send(self, sample_message, mock_config, mock_runtime):
        """Should send message successfully."""
        state = build_initial_state(sample_message)
        state["validated_text"] = "Response to send"
        state["send_delay"] = 0.0
        mock_runtime.adapter.send_reply = AsyncMock(return_value=True)

        result = await send_node(state, mock_config)

        assert result["sent"] is True
        mock_runtime.adapter.send_reply.assert_called_once()
        mock_runtime.memory.reset_dm_inbound_streak.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dm_send_failure_does_not_reset_streak(
        self, sample_message, mock_config, mock_runtime
    ):
        state = build_initial_state(sample_message)
        state["validated_text"] = "Response to send"
        state["send_delay"] = 0.0
        mock_runtime.adapter.send_reply = AsyncMock(return_value=False)

        result = await send_node(state, mock_config)

        assert result["sent"] is False
        mock_runtime.memory.reset_dm_inbound_streak.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_send_does_not_reset_dm_streak(
        self, sample_message, mock_config, mock_runtime
    ):
        state = build_initial_state(sample_message)
        state["message"].is_dm = False
        state["validated_text"] = "Group reply"
        state["send_delay"] = 0.0
        mock_runtime.adapter.send_reply = AsyncMock(return_value=True)

        result = await send_node(state, mock_config)

        assert result["sent"] is True
        mock_runtime.memory.reset_dm_inbound_streak.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_send(self, sample_message, mock_config, mock_runtime):
        """Should handle send failure."""
        state = build_initial_state(sample_message)
        state["validated_text"] = "Response to send"
        mock_runtime.adapter.send_reply = AsyncMock(return_value=False)

        result = await send_node(state, mock_config)

        assert result["sent"] is False


class TestMemoryNode:
    """Test memory_node."""

    @pytest.mark.asyncio
    async def test_dm_memory(self, sample_message, mock_config, mock_runtime):
        """Should record DM interaction."""
        state = build_initial_state(sample_message)
        state["message"].is_dm = True
        state["validated_text"] = "Bot response"
        state["sent"] = True

        result = await memory_node(state, mock_config)

        mock_runtime.memory.mark_processed.assert_called_once()
        mock_runtime.memory.record_dm.assert_called_once()
        assert result["interaction_count"] == 1

    @pytest.mark.asyncio
    async def test_group_memory(self, sample_message, mock_config, mock_runtime):
        """Should record group interaction."""
        state = build_initial_state(sample_message)
        state["message"].is_dm = False

        await memory_node(state, mock_config)

        mock_runtime.memory.record_group_message.assert_called_once()


class TestEmojiNode:
    """Test emoji_node."""

    @pytest.mark.asyncio
    async def test_emoji_send(self, sample_message, mock_config, mock_runtime):
        """Should send emoji reaction."""
        state = build_initial_state(sample_message)
        state["emoji_to_send"] = "👍"

        result = await emoji_node(state, mock_config)

        assert result["sent"] is True
        mock_runtime.adapter.send_reaction.assert_called_once()


class TestSendShortcutNode:
    """Test send_shortcut_node."""

    @pytest.mark.asyncio
    async def test_shortcut_send(self, sample_message, mock_config, mock_runtime):
        """Should send shortcut response."""
        state = build_initial_state(sample_message)
        state["preprocess_shortcut"] = "Quick answer"

        result = await send_shortcut_node(state, mock_config)

        assert result["sent"] is True
        mock_runtime.adapter.send_reply.assert_called_once()
