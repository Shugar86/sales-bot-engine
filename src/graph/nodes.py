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
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from ..core.router import Decision
from ..models.message import IncomingMessage
from ..monitors.anti_spam import TypingSpeedCalculator
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
# NODE 3: Semantic Retrieval
# ========================================

async def semantic_retrieval_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Fetch semantically relevant messages from vector DB.

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
# NODE 4: Anaphora Resolution
# ========================================

async def anaphora_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Resolve anaphoric references (it, this, that, etc.).

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
            results = await runtime.memory.search_semantic(
                query=msg.text,
                user_id=msg.user_id,
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

async def route_node(state: PersonaState, config: RunnableConfig) -> dict:
    """Decide whether to respond, ignore, or use emoji reaction.

    Returns:
        {"route_decision": "respond" | "ignore" | "emoji"}
    """
    runtime = _get_runtime(config)
    msg: IncomingMessage = state["message"]

    # Get recent chat context (single call, cached in state if needed)
    # For now, fetch fresh - can be optimized to use state["semantic_context"]
    recent_msgs = await runtime.memory.get_recent_messages(msg.chat_id, limit=3)
    chat_context = [m["text"] for m in recent_msgs]

    # Route
    route_result = await runtime.router.route(
        message_text=state["resolved_question"] or msg.text,
        chat_context=chat_context,
        is_dm=msg.is_dm,
    )

    decision_str = route_result.decision.value
    logger.info(
        f"[{runtime.config.name}] Route: {decision_str} "
        f"(conf={route_result.confidence:.1f}, reason={route_result.reason})"
    )

    # Map to simplified decisions
    if route_result.decision == Decision.IGNORE:
        return {"route_decision": "ignore", "node_history": ["route"]}
    elif route_result.decision == Decision.RESPOND:
        return {"route_decision": "respond", "node_history": ["route"]}
    else:
        return {"route_decision": "emoji", "node_history": ["route"]}


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

    # Emoji reaction check (group chats only)
    if not msg.is_dm and runtime.antispam.should_use_emoji_reaction():
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

            # Funnel stage
            funnel_stage = await runtime.memory.get_funnel_stage(msg.user_id)

            response = await runtime.generator.generate_dm_response(
                message_text=state["resolved_question"] or msg.text,
                user_memory=user_context,
                dm_history="",
                group_context="",
                funnel_stage=funnel_stage,
            )
        else:
            # Group chat: Use chat vibe + recent context
            recent_texts = await runtime.memory.get_recent_messages(
                msg.chat_id, limit=10
            )
            from ..responders.chat_vibe import detect_chat_vibe

            chat_vibe = detect_chat_vibe([t["text"] for t in recent_texts])

            response = await runtime.generator.generate_group_response(
                message_text=state["resolved_question"] or msg.text,
                chat_context=[t["text"] for t in recent_texts[:3]],
                chat_vibe=chat_vibe,
            )

        if response and response.text:
            logger.debug(
                f"[{runtime.config.name}] Generated: {response.text[:80]}..."
            )
            return {"generated_text": response.text, "node_history": ["generate"]}
        else:
            logger.info(f"[{runtime.config.name}] No response generated")
            return {"generated_text": None, "node_history": ["generate"]}

    except Exception as e:
        logger.error(f"[{runtime.config.name}] Generation error: {e}")
        return {
            "generated_text": None,
            "error_message": str(e),
            "node_history": ["generate"],
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
    if runtime.config.anti_spam.typing_simulation:
        calc = TypingSpeedCalculator()
        typing_time = calc.estimate_typing_time(send_text)

        try:
            if hasattr(runtime.monitor, "send_typing"):
                await runtime.monitor.send_typing(msg.chat_id)
        except Exception:
            pass

        await asyncio.sleep(min(typing_time, 15.0))

    # Send
    try:
        kwargs: dict = {}
        if runtime.config.platform == "vk":
            kwargs["peer_id"] = msg.chat_id
        else:
            kwargs["chat_id"] = msg.chat_id
            if not msg.is_dm:
                kwargs["reply_to"] = msg.message_id
            if runtime.config.account_type == "userbot":
                kwargs["typing_delay"] = False  # Already simulated above

        success = await runtime.monitor.send_message(text=send_text, **kwargs)

        if success:
            # Record for anti-repeat
            await runtime.memory.record_bot_response(msg.chat_id, send_text)
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

            await runtime.memory.record_dm(
                user_id=msg.user_id,
                username=msg.username,
                display_name=msg.display_name,
                message=msg.text or "",
                response=response_text,
                stage=state.get("funnel_stage", "unknown"),
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
        kwargs: dict = {}
        if runtime.config.platform == "vk":
            kwargs["peer_id"] = msg.chat_id
        else:
            kwargs["chat_id"] = msg.chat_id
            if not msg.is_dm:
                kwargs["reply_to"] = msg.message_id

        success = await runtime.monitor.send_message(text=shortcut_text, **kwargs)

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
        success = await runtime.monitor.send_reaction(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            emoji=emoji,
        )

        if success:
            logger.info(f"[{runtime.config.name}] Sent emoji: {emoji}")
            return {"sent": True, "node_history": ["emoji"]}
        else:
            return {"sent": False, "node_history": ["emoji"]}

    except Exception as e:
        logger.error(f"[{runtime.config.name}] Emoji send error: {e}")
        return {"sent": False, "error_message": str(e), "node_history": ["emoji"]}
