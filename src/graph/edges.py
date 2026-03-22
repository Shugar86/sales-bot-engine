"""LangGraph Conditional Edges — Routing logic between nodes.

Each edge function receives the current state and returns the next node name.
Edge conditions determine the flow through the pipeline.
"""

from .state import PersonaState


# ========================================
# EDGE 1: After Deduplication
# ========================================

def after_dedup(state: PersonaState) -> str:
    """Route after deduplication check.

    - If duplicate -> END (skip processing)
    - If new message -> preprocess
    """
    if state.get("is_duplicate", False):
        return "end"
    return "preprocess"


# ========================================
# EDGE 2: After Preprocessing
# ========================================

def after_preprocess(state: PersonaState) -> str:
    """Route after preprocessing.

    - If shortcut response -> send_shortcut (then memory)
    - If trivial/skip -> END (just mark processed)
    - Otherwise -> parallel semantic + anaphora
    """
    if state.get("preprocess_shortcut"):
        return "send_shortcut"

    if state.get("preprocess_skip", False):
        return "end"

    # Continue to parallel retrieval
    return "parallel_retrieval"


# ========================================
# EDGE 3: After Routing
# ========================================

def after_route(state: PersonaState) -> str:
    """Route after message routing decision.

    - "ignore" -> END (message not relevant)
    - "respond" -> antispam checks
    - "emoji" -> emoji reaction
    """
    decision = state.get("route_decision", "")

    if decision == "ignore":
        return "end"
    elif decision == "emoji":
        return "emoji"
    else:  # "respond"
        return "antispam"


# ========================================
# EDGE 5: After Anti-spam
# ========================================

def after_antispam(state: PersonaState) -> str:
    """Route after anti-spam checks.

    - can_send=False and no emoji -> END (leave on read or rate limited)
    - emoji_to_send set -> emoji node
    - can_send=True -> generate response
    """
    if state.get("emoji_to_send"):
        return "emoji"

    if not state.get("can_send", False):
        return "end"

    return "generate"


# ========================================
# EDGE 6: After Validation
# ========================================

def after_validate(state: PersonaState) -> str:
    """Route after output validation.

    - validated_text is None/empty -> END (nothing to send)
    - Otherwise -> send
    """
    validated = state.get("validated_text")

    if not validated:
        return "end"

    return "send"


# ========================================
# EDGE 7: After Send
# ========================================

def after_send(state: PersonaState) -> str:
    """Route after sending.

    Always goes to memory update to persist the interaction.
    """
    return "memory"


# ========================================
# EDGE 8: After Shortcut Send
# ========================================

def after_send_shortcut(state: PersonaState) -> str:
    """Route after sending shortcut response.

    Goes to memory update to persist.
    """
    return "memory"


# ========================================
# EDGE 9: After Emoji
# ========================================

def after_emoji(state: PersonaState) -> str:
    """Route after emoji reaction.

    Goes to memory to mark message processed.
    """
    return "memory"


# ========================================
# EDGE 10: Final
# ========================================

def after_memory(state: PersonaState) -> str:
    """Final node, always ends."""
    return "end"
