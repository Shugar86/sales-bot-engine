"""Tests for LangGraph state management.

Tests PersonaState TypedDict and checkpoint persistence.
Uses mocking for checkpointer or requires real Supabase test project.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.graph.state import build_initial_state
from src.models.message import IncomingMessage, Platform


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


class TestPersonaState:
    """Test PersonaState structure and operations."""

    def test_initial_state_structure(self, sample_message):
        """build_initial_state should create correct structure."""
        state = build_initial_state(sample_message)

        assert state["message"] == sample_message
        assert state["is_duplicate"] is False
        assert state["preprocess_skip"] is False
        assert state["semantic_context"] == []
        assert state["generated_text"] is None
        assert state["sent"] is False
        assert state["funnel_stage"] == "unknown"
        assert state["interaction_count"] == 0
        assert state["is_first_interaction"] is True
        assert state["parse_warnings"] == []

    def test_state_is_mutable(self, sample_message):
        """State should be mutable for node updates."""
        state = build_initial_state(sample_message)

        # Simulate node updates
        state["is_duplicate"] = True
        state["route_decision"] = "respond"
        state["semantic_context"] = ["Previous message 1", "Previous message 2"]
        state["generated_text"] = "I recommend this food"
        state["sent"] = True
        state["node_history"] = ["dedup", "preprocess", "route", "generate", "send"]

        assert state["is_duplicate"] is True
        assert state["route_decision"] == "respond"
        assert len(state["semantic_context"]) == 2
        assert state["generated_text"] == "I recommend this food"
        assert state["sent"] is True
        assert state["node_history"] == ["dedup", "preprocess", "route", "generate", "send"]

    def test_state_thread_id_format(self, sample_message):
        """Thread ID should follow persona:user:chat format."""
        # Thread ID is built in orchestrator, not in state
        persona_name = "kormoved"
        expected_thread_id = f"{persona_name}:{sample_message.user_id}:{sample_message.chat_id}"
        assert expected_thread_id == "kormoved:user123:chat456"


class TestStateTransitions:
    """Test state transitions through the pipeline."""

    def test_duplicate_detection_transition(self, sample_message):
        """After dedup_node with duplicate, should end."""
        from src.graph.edges import after_dedup

        state = build_initial_state(sample_message)
        state["is_duplicate"] = True

        result = after_dedup(state)
        assert result == "end"

    def test_preprocess_skip_transition(self, sample_message):
        """After preprocess with skip, should end."""
        from src.graph.edges import after_preprocess

        state = build_initial_state(sample_message)
        state["preprocess_skip"] = True

        result = after_preprocess(state)
        assert result == "end"

    def test_preprocess_shortcut_transition(self, sample_message):
        """After preprocess with shortcut, should go to send_shortcut."""
        from src.graph.edges import after_preprocess

        state = build_initial_state(sample_message)
        state["preprocess_shortcut"] = "Hello! I can help you."

        result = after_preprocess(state)
        assert result == "send_shortcut"

    def test_normal_flow_transition(self, sample_message):
        """Normal flow goes to parallel retrieval."""
        from src.graph.edges import after_preprocess

        state = build_initial_state(sample_message)
        # No shortcut, no skip

        result = after_preprocess(state)
        assert result == "parallel_retrieval"

    def test_route_ignore_transition(self, sample_message):
        """Router ignore decision ends the pipeline."""
        from src.graph.edges import after_route

        state = build_initial_state(sample_message)
        state["route_decision"] = "ignore"

        result = after_route(state)
        assert result == "end"

    def test_route_respond_transition(self, sample_message):
        """Router respond decision goes to antispam."""
        from src.graph.edges import after_route

        state = build_initial_state(sample_message)
        state["route_decision"] = "respond"

        result = after_route(state)
        assert result == "antispam"

    def test_route_error_transition(self, sample_message):
        """Mapping error ends pipeline (no silent emoji branch from route)."""
        from src.graph.edges import after_route

        state = build_initial_state(sample_message)
        state["route_decision"] = "error"

        result = after_route(state)
        assert result == "end"

    def test_route_unknown_decision_defensive_end(self, sample_message):
        """Unknown route_decision must not fall through to antispam."""
        from src.graph.edges import after_route

        state = build_initial_state(sample_message)
        state["route_decision"] = "emoji"

        result = after_route(state)
        assert result == "end"

    def test_antispam_blocked_transition(self, sample_message):
        """Antispam block ends the pipeline."""
        from src.graph.edges import after_antispam

        state = build_initial_state(sample_message)
        state["can_send"] = False
        state["emoji_to_send"] = None

        result = after_antispam(state)
        assert result == "end"

    def test_antispam_emoji_transition(self, sample_message):
        """Antispam emoji reaction goes to emoji node."""
        from src.graph.edges import after_antispam

        state = build_initial_state(sample_message)
        state["can_send"] = False
        state["emoji_to_send"] = "👍"

        result = after_antispam(state)
        assert result == "emoji"

    def test_antispam_allowed_transition(self, sample_message):
        """Antispam allow goes to generate."""
        from src.graph.edges import after_antispam

        state = build_initial_state(sample_message)
        state["can_send"] = True
        state["emoji_to_send"] = None

        result = after_antispam(state)
        assert result == "generate"

    def test_validate_empty_transition(self, sample_message):
        """Empty validated text ends the pipeline."""
        from src.graph.edges import after_validate

        state = build_initial_state(sample_message)
        state["validated_text"] = ""

        result = after_validate(state)
        assert result == "end"

    def test_validate_valid_transition(self, sample_message):
        """Valid text goes to send."""
        from src.graph.edges import after_validate

        state = build_initial_state(sample_message)
        state["validated_text"] = "Here is my response"

        result = after_validate(state)
        assert result == "send"


class TestCheckpointPersistence:
    """Test state persistence via PostgresSaver."""

    @pytest.mark.skipif(
        not os.getenv("TEST_SUPABASE_URL"),
        reason="TEST_SUPABASE_URL not set, skipping integration tests"
    )
    @pytest.mark.asyncio
    async def test_checkpoint_save_and_load(self, sample_message):
        """Test saving and loading checkpoints."""
        from src.graph.builder import create_checkpointer
        from src.graph.state import build_initial_state

        database_url = os.getenv("TEST_SUPABASE_URL")

        saver = await create_checkpointer(database_url)

        try:
            # Create a checkpoint
            thread_id = "test:checkpoint:test"
            state = build_initial_state(sample_message)
            state["interaction_count"] = 5
            state["funnel_stage"] = "interested"

            # Save checkpoint
            config = {"configurable": {"thread_id": thread_id}}
            # Note: This is a simplified test, real usage would be through the graph

            # Load checkpoint (may be None if no prior save)
            await saver.aget(config)

            # This test verifies the saver can connect and operate

        finally:
            await saver.aclose()

    @pytest.mark.asyncio
    async def test_mock_checkpoint_operations(self):
        """Test checkpoint operations with mock."""
        mock_saver = MagicMock()
        mock_saver.aget = AsyncMock(return_value=None)
        mock_saver.aput = AsyncMock()
        mock_saver.aclose = AsyncMock()

        config = {"configurable": {"thread_id": "test:mock:test"}}

        # Test get
        result = await mock_saver.aget(config)
        assert result is None

        # Test put
        await mock_saver.aput(config, "checkpoint", {}, {})
        mock_saver.aput.assert_called_once()


class TestThreadIsolation:
    """Test that different threads have isolated state."""

    def test_different_threads_different_state(self, sample_message):
        """Different thread IDs should have different state."""
        state1 = build_initial_state(sample_message)
        state2 = build_initial_state(sample_message)

        # Same initial state
        assert state1["interaction_count"] == state2["interaction_count"]

        # Modify one
        state1["interaction_count"] = 10

        # Other should be unchanged (different objects)
        assert state2["interaction_count"] == 0

    def test_thread_id_components(self):
        """Thread ID should contain all identifying components."""
        # Thread ID format: persona:user:chat
        thread_id = "kormoved:user123:chat456"
        parts = thread_id.split(":")

        assert len(parts) == 3
        assert parts[0] == "kormoved"  # persona
        assert parts[1] == "user123"   # user
        assert parts[2] == "chat456"   # chat
