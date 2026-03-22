"""Tests for LangGraph nodes.

Unit tests for each node in isolation, mocking dependencies.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.runnables import RunnableConfig

from src.graph.nodes import (
    dedup_node,
    preprocess_node,
    semantic_retrieval_node,
    anaphora_node,
    route_node,
    antispam_node,
    generate_node,
    validate_node,
    send_node,
    memory_node,
    emoji_node,
    send_shortcut_node,
)
from src.graph.state import PersonaState, build_initial_state
from src.models.message import IncomingMessage, Platform
from src.core.router import Decision, RouteResult


@pytest.fixture
def sample_message():
    """Create a sample incoming message."""
    return IncomingMessage(
        platform=Platform.TELEGRAM,
        user_id="user123",
        username="@testuser",
        display_name="Test User",
        chat_id="chat456",
        chat_title="Test Chat",
        text="Hello, what dog food do you recommend?",
        message_id=789,
        is_dm=True,
        timestamp=datetime.now(),
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
    runtime.memory.record_bot_response = AsyncMock()
    runtime.memory.is_repeating_response = AsyncMock(return_value=False)

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

    # Mock anti-spam
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

    # Mock monitor
    runtime.monitor = AsyncMock()
    runtime.monitor.send_message = AsyncMock(return_value=True)
    runtime.monitor.send_reaction = AsyncMock(return_value=True)

    # Mock config
    runtime.config = MagicMock()
    runtime.config.name = "test_persona"
    runtime.config.platform = "telegram"
    runtime.config.account_type = "userbot"
    runtime.config.anti_spam = MagicMock()
    runtime.config.anti_spam.random_typos = False
    runtime.config.anti_spam.typing_simulation = False

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

        assert result["preprocess_skip"] is False
        assert result["preprocess_shortcut"] is None
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
        mock_runtime.monitor.send_message = AsyncMock(return_value=True)

        result = await send_node(state, mock_config)

        assert result["sent"] is True
        mock_runtime.monitor.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_send(self, sample_message, mock_config, mock_runtime):
        """Should handle send failure."""
        state = build_initial_state(sample_message)
        state["validated_text"] = "Response to send"
        mock_runtime.monitor.send_message = AsyncMock(return_value=False)

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

        result = await memory_node(state, mock_config)

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
        mock_runtime.monitor.send_reaction.assert_called_once()


class TestSendShortcutNode:
    """Test send_shortcut_node."""

    @pytest.mark.asyncio
    async def test_shortcut_send(self, sample_message, mock_config, mock_runtime):
        """Should send shortcut response."""
        state = build_initial_state(sample_message)
        state["preprocess_shortcut"] = "Quick answer"

        result = await send_shortcut_node(state, mock_config)

        assert result["sent"] is True
        mock_runtime.monitor.send_message.assert_called_once()
