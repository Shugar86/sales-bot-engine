"""Graph module — LangGraph state machine for message processing.

Provides a stateful pipeline for processing incoming messages:
    dedup → preprocess → semantic_retrieval/anaphora → route →
    antispam → generate → validate → send → memory

Builder (compile_persona_graph, etc.) is loaded lazily to avoid importing
PostgreSQL checkpoint drivers when only nodes/state are needed.
"""

from __future__ import annotations

from typing import Any

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
    "PersonaState",
    "build_initial_state",
    "dedup_node",
    "preprocess_node",
    "semantic_retrieval_node",
    "route_node",
    "antispam_node",
    "generate_node",
    "validate_node",
    "send_node",
    "memory_node",
    "after_dedup",
    "after_preprocess",
    "after_route",
    "after_antispam",
    "after_validate",
    "build_persona_graph",
    "compile_persona_graph",
    "build_config",
]


def __getattr__(name: str) -> Any:
    if name == "build_config":
        from .builder import build_config

        return build_config
    if name == "build_persona_graph":
        from .builder import build_persona_graph

        return build_persona_graph
    if name == "compile_persona_graph":
        from .builder import compile_persona_graph

        return compile_persona_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
