"""PersonaState — TypedDict for LangGraph state management.

Defines the state structure for the sales bot pipeline.
State is persisted between invocations via PostgresSaver checkpointer.
"""

from typing import Annotated, Optional
from typing_extensions import TypedDict

from ..models.message import IncomingMessage


# Annotation for list append reducer
def _add_to_list(existing: list, new: list) -> list:
    """Reducer that appends to list."""
    return existing + new


class PersonaState(TypedDict):
    """State for a single persona message processing pipeline.

    This state flows through the LangGraph nodes and is persisted
    via the PostgresSaver checkpointer at each checkpoint.

    Thread ID format: "{persona_name}:{user_id}:{chat_id}"
    """

    # ========================================
    # INPUT (immutable for this invocation)
    # ========================================
    message: IncomingMessage
    """The incoming message to process."""

    # ========================================
    # NODE RESULTS
    # ========================================
    is_duplicate: bool
    """True if message was already processed (dedup)."""

    preprocess_shortcut: Optional[str]
    """Response text if preprocessor provided a shortcut."""

    preprocess_skip: bool
    """True if preprocessor determined message is trivial to ignore."""

    resolved_question: str
    """Question text after anaphora resolution."""

    route_decision: str
    """Router decision: 'respond' | 'ignore' | 'emoji'."""

    emoji_to_send: Optional[str]
    """Emoji reaction to send (if route_decision == 'emoji')."""

    semantic_context: Annotated[list[str], _add_to_list]
    """Relevant historical messages from semantic search."""

    generated_text: Optional[str]
    """Raw generated response text."""

    validated_text: Optional[str]
    """Text after output validation."""

    can_send: bool
    """Anti-spam decision on whether sending is allowed."""

    send_delay: float
    """Calculated delay before sending (seconds)."""

    sent: bool
    """True if response was successfully sent."""

    # ========================================
    # PERSISTENT STATE (via checkpointer)
    # ========================================
    funnel_stage: str
    """Current funnel stage for this user conversation."""

    interaction_count: int
    """Number of interactions with this user."""

    last_response_ts: Optional[float]
    """Timestamp of last bot response (for rate limiting)."""

    is_first_interaction: bool
    """True if this is the first interaction with this user."""

    # ========================================
    # DEBUG/ANALYSIS
    # ========================================
    node_history: Annotated[list[str], _add_to_list]
    """History of nodes visited (for debugging)."""

    error_message: Optional[str]
    """Error message if processing failed."""


def build_initial_state(msg: IncomingMessage) -> PersonaState:
    """Build initial state for a new message processing run.

    Args:
        msg: The incoming message

    Returns:
        Initial PersonaState with defaults
    """
    return {
        # Input
        "message": msg,
        # Node results (defaults)
        "is_duplicate": False,
        "preprocess_shortcut": None,
        "preprocess_skip": False,
        "resolved_question": "",
        "route_decision": "",
        "emoji_to_send": None,
        "semantic_context": [],
        "generated_text": None,
        "validated_text": None,
        "can_send": False,
        "send_delay": 0.0,
        "sent": False,
        # Persistent state (will be loaded from checkpointer if exists)
        "funnel_stage": "unknown",
        "interaction_count": 0,
        "last_response_ts": None,
        "is_first_interaction": True,
        # Debug
        "node_history": [],
        "error_message": None,
    }
