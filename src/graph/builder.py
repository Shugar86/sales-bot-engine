"""LangGraph Builder — Assemble and compile the persona processing graph.

Builds a StateGraph with:
- All processing nodes
- Conditional edges
- PostgresSaver for persistence
- Checkpointer for state between invocations

Thread ID format: "{persona_name}:{user_id}:{chat_id}"
"""

import os
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ..utils.logger import get_logger
from .edges import (
    after_antispam,
    after_dedup,
    after_emoji,
    after_memory,
    after_preprocess,
    after_retrieval,
    after_route,
    after_send,
    after_send_shortcut,
    after_validate,
)
from .nodes import (
    anaphora_node,
    antispam_node,
    dedup_node,
    emoji_node,
    generate_node,
    memory_node,
    preprocess_node,
    route_node,
    semantic_retrieval_node,
    send_node,
    send_shortcut_node,
    validate_node,
)
from .state import PersonaState

logger = get_logger("graph_builder")


async def create_checkpointer(database_url: str) -> AsyncPostgresSaver:
    """Create and initialize the PostgresSaver checkpointer.

    Args:
        database_url: PostgreSQL connection string

    Returns:
        Configured AsyncPostgresSaver
    """
    saver = AsyncPostgresSaver.from_conn_string(database_url)

    # Ensure tables exist
    try:
        await saver.setup()
        logger.info("PostgresSaver checkpointer initialized")
    except Exception as e:
        logger.warning(f"PostgresSaver setup error (may already exist): {e}")

    return saver


def build_persona_graph(runtime: Any) -> StateGraph:
    """Build the processing graph for a persona.

    Pipeline:
        dedup -> preprocess -> [semantic_retrieval, anaphora] -> route ->
        antispam -> generate -> validate -> send -> memory

    With shortcuts:
        - preprocess can go to send_shortcut -> memory
        - route can go to emoji -> memory or end
        - antispam can go to end (rate limited)

    Args:
        runtime: PersonaRuntime with all components configured

    Returns:
        Compiled StateGraph
    """
    # Create the graph
    workflow = StateGraph(PersonaState)

    # ========================================
    # ADD NODES
    # ========================================

    # Main pipeline nodes
    workflow.add_node("dedup", dedup_node)
    workflow.add_node("preprocess", preprocess_node)
    workflow.add_node("semantic_retrieval", semantic_retrieval_node)
    workflow.add_node("anaphora", anaphora_node)
    workflow.add_node("route", route_node)
    workflow.add_node("antispam", antispam_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("send", send_node)
    workflow.add_node("memory", memory_node)

    # Shortcut nodes
    workflow.add_node("send_shortcut", send_shortcut_node)
    workflow.add_node("emoji", emoji_node)

    # ========================================
    # ADD EDGES
    # ========================================

    # Entry point
    workflow.set_entry_point("dedup")

    # Deduplication -> Preprocess or END
    workflow.add_conditional_edges(
        "dedup",
        after_dedup,
        {
            "preprocess": "preprocess",
            "end": END,
        },
    )

    # Preprocessing -> Parallel retrieval, shortcut, or END
    workflow.add_conditional_edges(
        "preprocess",
        after_preprocess,
        {
            "send_shortcut": "send_shortcut",
            "parallel_retrieval": "semantic_retrieval",  # Start parallel branch
            "end": END,
        },
    )

    # Parallel: semantic_retrieval and anaphora (both feed into route)
    # semantic_retrieval -> route
    workflow.add_edge("semantic_retrieval", "anaphora")  # Sequential for simplicity
    # anaphora -> route
    workflow.add_conditional_edges(
        "anaphora",
        after_retrieval,
        {
            "route": "route",
        },
    )

    # Routing -> Anti-spam, Emoji, or END
    workflow.add_conditional_edges(
        "route",
        after_route,
        {
            "antispam": "antispam",
            "emoji": "emoji",
            "end": END,
        },
    )

    # Anti-spam -> Generate, Emoji, or END
    workflow.add_conditional_edges(
        "antispam",
        after_antispam,
        {
            "generate": "generate",
            "emoji": "emoji",
            "end": END,
        },
    )

    # Generation -> Validation
    workflow.add_edge("generate", "validate")

    # Validation -> Send or END
    workflow.add_conditional_edges(
        "validate",
        after_validate,
        {
            "send": "send",
            "end": END,
        },
    )

    # Send -> Memory
    workflow.add_conditional_edges(
        "send",
        after_send,
        {
            "memory": "memory",
        },
    )

    # Shortcut send -> Memory
    workflow.add_conditional_edges(
        "send_shortcut",
        after_send_shortcut,
        {
            "memory": "memory",
        },
    )

    # Emoji -> Memory
    workflow.add_conditional_edges(
        "emoji",
        after_emoji,
        {
            "memory": "memory",
        },
    )

    # Memory -> END
    workflow.add_conditional_edges(
        "memory",
        after_memory,
        {
            "end": END,
        },
    )

    logger.info(f"Built persona graph for {runtime.config.name}")

    return workflow


async def compile_persona_graph(
    runtime: Any,
    database_url: Optional[str] = None,
) -> Any:
    """Build and compile the graph with checkpointer.

    Args:
        runtime: PersonaRuntime with all components
        database_url: PostgreSQL connection string (uses env if not set)

    Returns:
        Compiled graph with checkpointer
    """
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")

    # Build graph
    workflow = build_persona_graph(runtime)

    # Create checkpointer
    checkpointer = await create_checkpointer(database_url)

    # Compile with checkpointer
    compiled = workflow.compile(checkpointer=checkpointer)

    logger.info(f"Compiled persona graph for {runtime.config.name} with PostgresSaver")

    return compiled


def build_config(
    runtime: Any,
    thread_id: str,
    recursion_limit: int = 50,
) -> RunnableConfig:
    """Build the config for graph invocation.

    Args:
        runtime: PersonaRuntime with components
        thread_id: Unique thread identifier
        recursion_limit: Max recursion depth for safety

    Returns:
        RunnableConfig for graph.ainvoke()
    """
    return {
        "configurable": {
            "thread_id": thread_id,
            "runtime": runtime,
        },
        "recursion_limit": recursion_limit,
    }


# Convenience re-exports
__all__ = [
    "build_persona_graph",
    "compile_persona_graph",
    "build_config",
    "create_checkpointer",
]
