"""Graph module — LangGraph state machine for message processing.

Provides a stateful pipeline for processing incoming messages:
    dedup → preprocess → semantic_retrieval/anaphora → route →
    antispam → generate → validate → send → memory

Features:
- State persistence via PostgreSQL checkpointer
- Parallel processing branches
- Conditional routing
- Thread isolation per user/chat
"""

from .builder import build_config, build_persona_graph, compile_persona_graph
from .edges import (
    after_antispam,
    after_dedup,
    after_preprocess,
    after_route,
    after_validate,
)
from .nodes import (
    antispam_node,
    dedup_node,
    generate_node,
    memory_node,
    preprocess_node,
    route_node,
    semantic_retrieval_node,
    send_node,
    validate_node,
)
from .state import PersonaState, build_initial_state

__all__ = [
    # State
    "PersonaState",
    "build_initial_state",
    # Nodes
    "dedup_node",
    "preprocess_node",
    "semantic_retrieval_node",
    "route_node",
    "antispam_node",
    "generate_node",
    "validate_node",
    "send_node",
    "memory_node",
    # Edges
    "after_dedup",
    "after_preprocess",
    "after_route",
    "after_antispam",
    "after_validate",
    # Builder
    "build_persona_graph",
    "compile_persona_graph",
    "build_config",
]
