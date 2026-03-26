"""LangGraph Nodes — Pure async functions for state processing.

Each node receives (state, config) and returns updates as a dict.
Dependencies are accessed via config["configurable"]["runtime"].

Node order:
1. dedup_node — Check if message is duplicate
2. preprocess_node — Deterministic shortcuts
3. semantic_retrieval_node — Fetch relevant context from vector DB
4. anaphora_node — Resolve anaphoras (it, this, etc.)
5. route_node — Decide respond/ignore/emoji
6. antispam_node — Check rate limits, calculate delays
7. generate_node — Generate response text
8. validate_node — Validate output
9. send_node — Send message with delays
10. memory_node — Persist to database
"""

import asyncio
import time
from typing import Any, Optional, Tuple

from langchain_core.runnables import RunnableConfig

from ..core.router import Decision
from ..models.message import IncomingMessage
from ..monitors.anti_spam import TypingSpeedCalculator
from ..platforms import SendOptions
from ..responders.preprocess import PreprocessResult
from ..responders.response_composer import looks_like_greeting
from ..responders.text_humanizer import humanize_text
from ..utils.logger import get_logger
from .state import PersonaState

logger = get_logger("graph_nodes")


def _get_runtime(config: RunnableConfig) -> Any:
    """Extract runtime from config."""
    return config.get("configurable", {}).get("runtime")


def _get_memory(config: RunnableConfig) -> Any:
    """Extract memory facade from runtime."""
    runtime = _get_runtime(config)
    return runtime.memory if runtime else None


# ========================================
# NODE 1: Deduplication
# ========================================

async def dedup_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Check if message has already been processed.

    Returns:
        {"is_duplicate": bool}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    is_dup = await runtime.memory.is_processed(
        msg.chat_id, msg.message_id, msg.text
    )

    if is_dup:
        logger.debug(f"[{runtime.config.name}] Duplicate message: {msg.message_id}")

    return {"is_duplicate": is_dup, "node_history": ["dedup"]}


# ========================================
# NODE 2: Preprocessing
# ========================================

async def preprocess_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Apply deterministic preprocessing rules.

    Returns:
        {
            "preprocess_shortcut": str | None,
            "preprocess_skip": bool,
            "resolved_question": str  # same as input if no changes
        }
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    # Build last context from memory
    last_tool = await runtime.memory.get_last_tool(msg.user_id)
    last_tool_args = await runtime.memory.get_last_tool_args(msg.user_id)

    last_context = {
        "last_tool_name": last_tool,
        "last_tool_args": last_tool_args,
    }

    # Check if first interaction
    is_first = await runtime.memory.is_first_response(msg.user_id, msg.chat_id)
    user_greeted = looks_like_greeting(msg.text) if msg.text else False

    # Process
    result: PreprocessResult = runtime.preprocessor.process(
        question=msg.text or "",
        last_context=last_context,
        is_first_response=is_first,
        user_greeted=user_greeted,
        is_dm=msg.is_dm,
    )

    updates: dict = {"node_history": ["preprocess"]}

    if result.has_shortcut:
        if result.skip_generation:
            updates["preprocess_skip"] = True
            logger.debug(f"[{runtime.config.name}] Preprocess skip (trivial)")
        elif result.shortcut_response:
            updates["preprocess_shortcut"] = result.shortcut_response
            logger.info(
                f"[{runtime.config.name}] Preprocess shortcut: {result.pipeline_step}"
            )

    # Anaphora resolution happens in parallel, but we store the question
    updates["resolved_question"] = msg.text or ""

    return updates


# ========================================
# NODE 3: Semantic Retrieval (DEPRECATED - use parallel_retrieval_node)
# ========================================
# LEGACY: This node is kept for backward compatibility during migration.
# The graph now uses parallel_retrieval_node which runs semantic retrieval
# and anaphora resolution concurrently via asyncio.gather.
# This standalone node may be removed in a future version.

async def semantic_retrieval_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Fetch semantically relevant messages from vector DB.

    DEPRECATED: Use parallel_retrieval_node instead for concurrent execution.

    Returns:
        {"semantic_context": [str]}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    # Skip for empty messages
    if not msg.text:
        return {"semantic_context": [], "node_history": ["semantic"]}

    try:
        # Search for similar messages
        results = await runtime.memory.search_semantic(
            query=msg.text,
            user_id=msg.user_id,
            top_k=3,
            min_similarity=0.6,
        )

        context = [r["text"] for r in results]
        logger.debug(f"[{runtime.config.name}] Semantic context: {len(context)} items")

        return {"semantic_context": context, "node_history": ["semantic"]}

    except Exception as e:
        logger.warning(f"[{runtime.config.name}] Semantic search error: {e}")
        return {"semantic_context": [], "node_history": ["semantic"]}


# ========================================
# NODE 4: Anaphora Resolution (DEPRECATED - use parallel_retrieval_node)
# ========================================
# LEGACY: This node is kept for backward compatibility during migration.
# The graph now uses parallel_retrieval_node which runs semantic retrieval
# and anaphora resolution concurrently via asyncio.gather.
# This standalone node may be removed in a future version.

async def anaphora_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Resolve anaphoric references (it, this, that, etc.).

    DEPRECATED: Use parallel_retrieval_node instead for concurrent execution.

    Returns:
        {"resolved_question": str}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    if not msg.text:
        return {"resolved_question": "", "node_history": ["anaphora"]}

    result = runtime.anaphora.resolve(
        user_id=str(msg.user_id),
        chat_id=str(msg.chat_id),
        question=msg.text,
    )

    resolved = result.resolved_question if result.is_resolved else msg.text

    logger.debug(f"[{runtime.config.name}] Anaphora resolved: {msg.text[:50]}... -> {resolved[:50]}...")

    return {"resolved_question": resolved, "node_history": ["anaphora"]}


# ========================================
# NODE 3b: Parallel Retrieval (semantic + anaphora)
# ========================================

async def parallel_retrieval_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Run semantic retrieval and anaphora resolution in PARALLEL.

    This node replaces sequential semantic_retrieval_node -> anaphora_node
    to reduce latency (~200-400ms each, parallel = ~400ms vs sequential = ~600-800ms).

    Uses asyncio.gather for concurrent execution.

    Returns:
        {
            "semantic_context": list[str],
            "resolved_question": str
        }
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    async def _do_semantic_retrieval() -> list[str]:
        """Fetch semantically relevant messages."""
        if not msg.text:
            return []
        try:
            # DM: search by user_id for personal context
            # Group: search by chat_id for group-wide context
            if msg.is_dm:
                results = await runtime.memory.search_semantic(
                    query=msg.text,
                    user_id=msg.user_id,
                    top_k=3,
                    min_similarity=0.6,
                )
            else:
                results = await runtime.memory.search_semantic_group(
                    query=msg.text,
                    chat_id=msg.chat_id,
                    top_k=3,
                    min_similarity=0.6,
                )
            return [r["text"] for r in results]
        except Exception as e:
            logger.warning(f"[{runtime.config.name}] Semantic search error: {e}")
            return []

    async def _do_anaphora_resolution() -> str:
        """Resolve anaphoric references."""
        if not msg.text:
            return ""
        try:
            result = runtime.anaphora.resolve(
                user_id=str(msg.user_id),
                chat_id=str(msg.chat_id),
                question=msg.text,
            )
            resolved = result.resolved_question if result.is_resolved else msg.text
            logger.debug(f"[{runtime.config.name}] Anaphora: {msg.text[:50]}... -> {resolved[:50]}...")
            return resolved
        except Exception as e:
            logger.warning(f"[{runtime.config.name}] Anaphora error: {e}")
            return msg.text or ""

    # Execute BOTH operations concurrently
    start_time = asyncio.get_event_loop().time()
    semantic_context, resolved_question = await asyncio.gather(
        _do_semantic_retrieval(),
        _do_anaphora_resolution(),
    )
    elapsed = asyncio.get_event_loop().time() - start_time

    logger.debug(f"[{runtime.config.name}] Parallel retrieval completed in {elapsed:.3f}s")

    return {
        "semantic_context": semantic_context,
        "resolved_question": resolved_question,
        "node_history": ["parallel_retrieval"],
    }


# ========================================
# NODE 5: Routing
# ========================================


def map_router_decision_for_graph(decision: Any) -> Tuple[str, Optional[str]]:
    """Map router :class:`~src.core.router.Decision` to graph branch labels.

    Emoji reactions are not chosen here; ``antispam_node`` sets ``emoji_to_send``.

    Args:
        decision: Value from :class:`~src.core.router.RouteResult` (expected ``Decision``).

    Returns:
        ``(route_decision, error_message)``. ``error_message`` is set when the value is
        invalid or a new ``Decision`` member was not wired into the graph (fail-safe).
    """
    if decision == Decision.IGNORE:
        return "ignore", None
    if decision == Decision.DISENGAGE:
        return "ignore", None
    if decision in (
        Decision.RESPOND,
        Decision.SALES_DM,
        Decision.ENGAGE,
        Decision.WAIT,
    ):
        return "respond", None
    if isinstance(decision, Decision):
        msg = (
            f"Unhandled router Decision in graph mapping: {decision!r}. "
            "Add an explicit branch in map_router_decision_for_graph."
        )
        logger.error(msg)
        return "error", msg
    msg = (
        f"Invalid router decision type for graph: {type(decision).__name__} ({decision!r})."
    )
    logger.error(msg)
    return "error", msg


async def route_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Map router output to graph branches (respond / ignore / error).

    Returns:
        State updates including ``route_decision`` and cached ``chat_context``.
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    # Get recent chat context (cached in state to avoid duplicate DB queries in generate_node)
    recent_msgs = await runtime.memory.get_recent_messages(msg.chat_id, limit=10)
    chat_context = [m["text"] for m in recent_msgs]

    top_lines = chat_context[:3]
    chat_context_str = "\n".join(top_lines) if top_lines else ""

    route_result = await runtime.router.route(
        message_text=state["resolved_question"] or msg.text,
        chat_context=chat_context_str,
        is_dm=msg.is_dm,
    )

    decision_str = route_result.decision.value
    logger.info(
        f"[{runtime.config.name}] Route: {decision_str} "
        f"(conf={route_result.confidence:.1f}, reason={route_result.reason})"
    )

    route_decision, map_error = map_router_decision_for_graph(route_result.decision)

    result: dict = {
        "route_decision": route_decision,
        "chat_context": chat_context,
        "node_history": ["route"],
    }
    if map_error:
        result["error_message"] = map_error
    if route_result.parse_failed:
        reason = (route_result.reason or "").strip() or "router_parse_failed"
        result["parse_warnings"] = [reason]
    return result


# ========================================
# NODE 6: Anti-spam
# ========================================

async def antispam_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Check rate limits and calculate send parameters.

    Returns:
        {
            "can_send": bool,
            "send_delay": float,
            "emoji_to_send": str | None,
            "is_first_interaction": bool (updated)
        }
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    updates: dict = {"node_history": ["antispam"]}

    # Check if first interaction
    is_first = await runtime.memory.is_first_response(msg.user_id, msg.chat_id)
    updates["is_first_interaction"] = is_first

    # Leave on read check (group chats only)
    if not msg.is_dm and runtime.antispam.should_leave_on_read():
        logger.debug(f"[{runtime.config.name}] Leave on read")
        return {"can_send": False, "send_delay": 0.0, **updates}

    # Emoji reaction check (group chats only; skip if adapter cannot react)
    caps = (
        runtime.adapter.capabilities()
        if runtime.adapter
        else None
    )
    if (
        not msg.is_dm
        and caps
        and caps.supports_reactions
        and runtime.antispam.should_use_emoji_reaction()
    ):
        emoji = runtime.antispam.get_emoji_reaction(msg.text)
        if emoji:
            logger.info(f"[{runtime.config.name}] Emoji reaction: {emoji}")
            return {
                "can_send": False,
                "send_delay": 0.0,
                "emoji_to_send": emoji,
                **updates,
            }

    # Rate limit check
    can_send, reason = runtime.antispam.can_send(msg.chat_id)

    if not can_send:
        logger.warning(f"[{runtime.config.name}] Anti-spam blocked: {reason}")
        return {"can_send": False, "send_delay": 0.0, **updates}

    # Per-user DM flood: too many consecutive inbound DMs without a bot reply
    if msg.is_dm:
        burst_limit = runtime.config.anti_spam.dm_max_inbound_burst_without_bot_reply
        streak = await runtime.memory.get_dm_inbound_streak(msg.user_id)
        if streak >= burst_limit:
            logger.warning(
                f"[{runtime.config.name}] DM inbound flood: user={msg.user_id} "
                f"streak={streak} >= limit={burst_limit}"
            )
            return {"can_send": False, "send_delay": 0.0, **updates}
        await runtime.memory.increment_dm_inbound_streak(msg.user_id)

    # Calculate delay
    delay = runtime.antispam.get_random_delay()
    logger.debug(f"[{runtime.config.name}] Anti-spam delay: {delay:.1f}s")

    return {"can_send": True, "send_delay": delay, **updates}


# ========================================
# NODE 7: Generation
# ========================================

async def generate_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Generate response text using LLM.

    Returns:
        {"generated_text": str | None}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    try:
        if msg.is_dm:
            # DM: Use full user context + semantic context
            user_context = await runtime.memory.get_user_context(msg.user_id)

            # Add semantic context if available
            if state.get("semantic_context"):
                user_context += f"\n\nРелевантный контекст: {'; '.join(state['semantic_context'])}"

            # Get recommendations to avoid repetition
            recs = await runtime.memory.get_recommendations(msg.user_id, limit=3)
            if recs:
                user_context += f"\nУже рекомендовал: {'; '.join(recs)}"

            funnel_stage = await runtime.memory.get_funnel_stage(msg.user_id)

            dm_history = await runtime.memory.get_dm_transcript_for_prompt(msg.user_id)
            if not (dm_history or "").strip():
                dm_history = "(ещё нет переписки в этой сессии)"

            response = await runtime.generator.generate_dm_response(
                message_text=state["resolved_question"] or msg.text,
                user_memory=user_context,
                dm_history=dm_history,
                group_context="",
                funnel_stage=funnel_stage,
            )
        else:
            # Group chat: Use chat vibe + recent context (reuse from route_node if available)
            # Use cached chat_context from state to avoid duplicate DB query
            chat_context = state.get("chat_context", [])
            if not chat_context:
                # Fallback: fetch fresh if not cached (shouldn't happen in normal flow)
                recent_msgs = await runtime.memory.get_recent_messages(msg.chat_id, limit=10)
                chat_context = [m["text"] for m in recent_msgs]

            from ..responders.chat_vibe import detect_chat_vibe

            chat_vibe = detect_chat_vibe(chat_context)

            response = await runtime.generator.generate_group_response(
                message_text=state["resolved_question"] or msg.text,
                chat_context=chat_context[:3],
                chat_vibe=chat_vibe,
            )

        if response and response.text:
            logger.debug(
                f"[{runtime.config.name}] Generated: {response.text[:80]}..."
            )
            gen_updates: dict = {
                "generated_text": response.text,
                "node_history": ["generate"],
                "llm_failed": False,
            }
            return gen_updates
        else:
            logger.info(f"[{runtime.config.name}] No response generated")
            return {
                "generated_text": None,
                "node_history": ["generate"],
                "llm_failed": response is None,
            }

    except Exception as e:
        logger.error(f"[{runtime.config.name}] Generation error: {e}")
        return {
            "generated_text": None,
            "error_message": str(e),
            "node_history": ["generate"],
            "llm_failed": True,
        }


# ========================================
# NODE 8: Validation
# ========================================

async def validate_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Validate and clean generated output.

    Returns:
        {"validated_text": str | None}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]
    generated = state.get("generated_text")

    if not generated:
        return {"validated_text": None, "node_history": ["validate"]}

    # Run validation
    is_first = state.get("is_first_interaction", True)
    user_greeted = looks_like_greeting(msg.text) if msg.text else False

    validation = runtime.output_validator.validate(
        text=generated,
        is_first_response=is_first,
        user_greeted=user_greeted,
    )

    if validation.violations:
        logger.warning(
            f"[{runtime.config.name}] Output violations: {validation.violations}"
        )

    cleaned = validation.cleaned_text
    if not cleaned:
        logger.info(f"[{runtime.config.name}] Validator cleared response")
        cleaned = None

    return {"validated_text": cleaned, "node_history": ["validate"]}


# ========================================
# NODE 9: Send
# ========================================

async def send_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Send response with delays and humanization.

    Returns:
        {"sent": bool}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]
    text = state.get("validated_text")

    if not text:
        return {"sent": False, "node_history": ["send"]}

    # Check for repeat
    is_repeat = await runtime.memory.is_repeating_response(msg.chat_id, text)
    if is_repeat:
        logger.info(f"[{runtime.config.name}] Skipping repeat response")
        return {"sent": False, "node_history": ["send"]}

    # Humanize text if configured
    send_text = text
    if runtime.config.anti_spam.random_typos:
        # Try to get tone from state or default to casual
        is_casual = True  # Default
        send_text = humanize_text(send_text, is_casual=is_casual)

    # Anti-spam delay
    delay = state.get("send_delay", 0)
    if delay > 0:
        logger.debug(f"[{runtime.config.name}] Waiting {delay:.1f}s before send")
        await asyncio.sleep(delay)

    # Typing simulation
    typing_already_simulated = False
    if runtime.config.anti_spam.typing_simulation:
        calc = TypingSpeedCalculator()
        typing_time = calc.estimate_typing_time(send_text)

        try:
            if runtime.adapter:
                await runtime.adapter.send_typing(msg)
        except Exception:
            pass

        await asyncio.sleep(min(typing_time, 15.0))
        if runtime.config.account_type == "userbot":
            typing_already_simulated = True

    # Send
    try:
        if not runtime.adapter:
            logger.error(f"[{runtime.config.name}] No platform adapter")
            return {"sent": False, "node_history": ["send"]}

        reply_to = None if msg.is_dm else msg.message_id
        options = SendOptions(
            reply_to_message_id=reply_to,
            typing_already_simulated=typing_already_simulated,
            thread_id=msg.thread_id,
        )
        success = await runtime.adapter.send_reply(msg, send_text, options)

        if success:
            # Record for anti-repeat
            await runtime.memory.record_bot_response(msg.chat_id, send_text)
            if msg.is_dm:
                await runtime.memory.reset_dm_inbound_streak(msg.user_id)
            # Update anti-spam stats
            runtime.antispam.record_send(msg.chat_id)
            # Update last response timestamp
            updates = {
                "sent": True,
                "last_response_ts": time.time(),
                "node_history": ["send"],
            }
            logger.info(f"[{runtime.config.name}] Sent: {send_text[:80]}...")
            return updates
        else:
            logger.error(f"[{runtime.config.name}] Send failed")
            return {"sent": False, "node_history": ["send"]}

    except Exception as e:
        logger.error(f"[{runtime.config.name}] Send error: {e}")
        return {"sent": False, "error_message": str(e), "node_history": ["send"]}


# ========================================
# NODE 10: Memory Update
# ========================================

async def memory_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Persist interaction to database.

    This node ALWAYS runs to record the incoming message, regardless of
    whether a response was sent.

    Returns:
        {"interaction_count": int (incremented)}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    try:
        # Mark as processed (always do this)
        await runtime.memory.mark_processed(msg.chat_id, msg.message_id, msg.text)

        # Record the interaction
        if msg.is_dm:
            # Record DM with or without response
            response_text = ""
            if state.get("sent") and state.get("validated_text"):
                response_text = state["validated_text"]

            from ..core.funnel_heuristic import suggest_funnel_stage

            cur = await runtime.memory.get_funnel_stage(msg.user_id)
            new_stage = suggest_funnel_stage(cur, msg.text or "")

            await runtime.memory.record_dm(
                user_id=msg.user_id,
                username=msg.username,
                display_name=msg.display_name,
                message=msg.text or "",
                response=response_text,
                stage=new_stage,
            )
        else:
            # Record group message
            await runtime.memory.record_group_message(
                user_id=msg.user_id,
                username=msg.username,
                display_name=msg.display_name,
                chat_id=msg.chat_id,
                chat_title=msg.chat_title,
                message=msg.text or "",
            )

        # Increment interaction count
        current_count = state.get("interaction_count", 0)
        updates = {
            "interaction_count": current_count + 1,
            "node_history": ["memory"],
        }

        logger.debug(f"[{runtime.config.name}] Memory updated for {msg.user_id}")
        return updates

    except Exception as e:
        logger.error(f"[{runtime.config.name}] Memory update error: {e}")
        return {
            "interaction_count": state.get("interaction_count", 0),
            "error_message": str(e),
            "node_history": ["memory"],
        }


# ========================================
# SHORTCUT SEND NODE (for preprocess shortcuts)
# ========================================

async def send_shortcut_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Send a preprocess shortcut response.

    Returns:
        {"sent": bool}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]
    shortcut_text = state.get("preprocess_shortcut")

    if not shortcut_text:
        return {"sent": False, "node_history": ["send_shortcut"]}

    try:
        if not runtime.adapter:
            return {"sent": False, "node_history": ["send_shortcut"]}

        reply_to = None if msg.is_dm else msg.message_id
        options = SendOptions(
            reply_to_message_id=reply_to,
            typing_already_simulated=False,
            thread_id=msg.thread_id,
        )
        success = await runtime.adapter.send_reply(msg, shortcut_text, options)

        if success:
            await runtime.memory.record_bot_response(msg.chat_id, shortcut_text)
            runtime.antispam.record_send(msg.chat_id)
            logger.info(f"[{runtime.config.name}] Sent shortcut: {shortcut_text[:80]}...")
            return {"sent": True, "last_response_ts": time.time(), "node_history": ["send_shortcut"]}
        else:
            return {"sent": False, "node_history": ["send_shortcut"]}

    except Exception as e:
        logger.error(f"[{runtime.config.name}] Shortcut send error: {e}")
        return {"sent": False, "error_message": str(e), "node_history": ["send_shortcut"]}


# ========================================
# EMOJI REACTION NODE
# ========================================

async def emoji_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Send emoji reaction.

    Returns:
        {"sent": bool}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]
    emoji = state.get("emoji_to_send")

    if not emoji:
        return {"sent": False, "node_history": ["emoji"]}

    try:
        if not runtime.adapter or not runtime.adapter.capabilities().supports_reactions:
            return {"sent": False, "node_history": ["emoji"]}

        success = await runtime.adapter.send_reaction(msg, emoji)

        if success:
            logger.info(f"[{runtime.config.name}] Sent emoji: {emoji}")
            return {"sent": True, "node_history": ["emoji"]}
        else:
            return {"sent": False, "node_history": ["emoji"]}

    except Exception as e:
        logger.error(f"[{runtime.config.name}] Emoji send error: {e}")
        return {"sent": False, "error_message": str(e), "node_history": ["emoji"]}
