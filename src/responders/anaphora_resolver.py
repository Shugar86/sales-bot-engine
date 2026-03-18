"""
Anaphora Resolver — context memory for follow-up questions.

Ported from ai-tutor-engine ProductAnaphoraPlugin pattern:
- "что это?" → resolves "это" to previous search results
- "подешевле" → understands context of previous conversation
- "а он?" → resolves pronoun to previous product/person

Tracks conversation context per user per chat.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# ANAPHORA TRIGGERS
# ══════════════════════════════════════════════════════════════

# Triggers that indicate user is referring to previous context
ANAPHORA_TRIGGERS = [
    # Direct anaphora
    "что это", "что такое", "расскажи подробнее", "как это",
    "опиши", "подробнее", "а это", "про это", "об этом",
    "что он", "что она", "а он", "а она", "он", "она", "это",
    # Price/quality comparison
    "подешевле", "подороже", "дешевле", "дороже",
    "лучше", "хуже", "похожее", "аналоги", "альтернативы",
    # Quantity/availability
    "еще", "ещё", "покажи еще", "есть еще", "другие",
    "все", "всё", "остальные",
    # Confirmation
    "давай его", "беру", "хочу это", "подходит",
    "подойдет", "заказать", "купить",
]

ANAPHORA_RE = re.compile(
    "|".join(re.escape(t) for t in sorted(ANAPHORA_TRIGGERS, key=len, reverse=True)),
    re.IGNORECASE,
)

# Price comparison patterns
PRICE_COMPARISON_PATTERNS = [
    (r"подешевле|дешевле|по дешевле", "cheaper"),
    (r"подороже|дороже", "more_expensive"),
    (r"лучше|получше", "better"),
    (r"похоже|аналог|альтернатива", "similar"),
]

PRICE_COMPARISON_RE = [
    (re.compile(p, re.IGNORECASE), direction) for p, direction in PRICE_COMPARISON_PATTERNS
]


# ══════════════════════════════════════════════════════════════
# CONVERSATION CONTEXT
# ══════════════════════════════════════════════════════════════

@dataclass
class ConversationContext:
    """Stores context for anaphora resolution per user per chat."""
    last_tool_name: Optional[str] = None
    last_tool_args: Dict[str, Any] = field(default_factory=dict)
    last_tool_result: Optional[str] = None
    last_query: Optional[str] = None
    last_products: List[str] = field(default_factory=list)  # Product names from last search
    last_animal_type: Optional[str] = None  # "dog" or "cat"
    last_category: Optional[str] = None
    message_count: int = 0


@dataclass
class AnaphoraResult:
    """Result of anaphora resolution."""
    has_anaphora: bool = False
    resolved_query: Optional[str] = None  # Resolved query with context
    comparison_direction: Optional[str] = None  # "cheaper", "better", "similar"
    context_tool: Optional[str] = None  # Tool to use based on context
    context_args: Dict[str, Any] = field(default_factory=dict)


class AnaphoraResolver:
    """Resolves anaphoric references in user messages.

    Tracks per-user per-chat context and resolves:
    - "что это?" → uses last search query
    - "подешевле" → reuses last search with price filter
    - "а он?" → uses last product/person context
    """

    def __init__(self, max_contexts: int = 1000):
        """Initialize resolver with context storage."""
        self._contexts: Dict[str, ConversationContext] = {}
        self._max_contexts = max_contexts

    def _context_key(self, user_id: str, chat_id: str) -> str:
        """Generate context key from user and chat IDs."""
        return f"{user_id}:{chat_id}"

    def get_context(self, user_id: str, chat_id: str) -> ConversationContext:
        """Get or create context for user in chat."""
        key = self._context_key(user_id, chat_id)
        if key not in self._contexts:
            self._contexts[key] = ConversationContext()
            # Evict old contexts if over limit
            if len(self._contexts) > self._max_contexts:
                oldest_key = next(iter(self._contexts))
                del self._contexts[oldest_key]
        return self._contexts[key]

    def update_context(
        self,
        user_id: str,
        chat_id: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[str] = None,
        query: Optional[str] = None,
        products: Optional[List[str]] = None,
        animal_type: Optional[str] = None,
        category: Optional[str] = None,
    ):
        """Update context after a tool execution or user message."""
        ctx = self.get_context(user_id, chat_id)
        ctx.message_count += 1

        if tool_name:
            ctx.last_tool_name = tool_name
        if tool_args:
            ctx.last_tool_args = tool_args
        if tool_result:
            ctx.last_tool_result = tool_result
        if query:
            ctx.last_query = query
        if products:
            ctx.last_products = products
        if animal_type:
            ctx.last_animal_type = animal_type
        if category:
            ctx.last_category = category

    def resolve(self, user_id: str, chat_id: str, question: str) -> AnaphoraResult:
        """Resolve anaphoric references in user message.

        Args:
            user_id: User identifier
            chat_id: Chat identifier
            question: User's message text

        Returns:
            AnaphoraResult with resolved query and context info
        """
        if not question:
            return AnaphoraResult()

        ctx = self.get_context(user_id, chat_id)
        q_lower = question.lower().strip()

        # Check if message contains anaphora triggers
        has_anaphora = bool(ANAPHORA_RE.search(q_lower))
        if not has_anaphora:
            return AnaphoraResult()

        result = AnaphoraResult(has_anaphora=True)

        # Detect comparison direction
        for pattern, direction in PRICE_COMPARISON_RE:
            if pattern.search(q_lower):
                result.comparison_direction = direction
                break

        # If we have previous context, resolve
        if ctx.last_tool_name:
            result.context_tool = ctx.last_tool_name
            result.context_args = dict(ctx.last_tool_args) if ctx.last_tool_args else {}

            # Resolve query based on context
            if ctx.last_query:
                result.resolved_query = ctx.last_query
            elif ctx.last_tool_args and "query" in ctx.last_tool_args:
                result.resolved_query = ctx.last_tool_args["query"]

            # Add category if we know it
            if ctx.last_category and "category" not in result.context_args:
                result.context_args["category"] = ctx.last_category

            # Add animal type if we know it
            if ctx.last_animal_type and "animal_type" not in result.context_args:
                result.context_args["animal_type"] = ctx.last_animal_type

        # Specific handling for pure follow-ups
        if q_lower in ("покажи еще", "еще", "ещё", "дальше", "продолжай"):
            result.resolved_query = ctx.last_query or ctx.last_tool_args.get("query", "")

        # Handle "подешевле" with context
        if result.comparison_direction == "cheaper" and ctx.last_products:
            result.resolved_query = ctx.last_query or ctx.last_tool_args.get("query", "")

        # Handle "что это?" / "что такое?" — use last query
        if any(trigger in q_lower for trigger in ("что это", "что такое", "про это", "об этом")):
            result.resolved_query = ctx.last_query or ctx.last_tool_args.get("query", "")

        logger.debug(
            f"Anaphora resolved: query={result.resolved_query}, "
            f"tool={result.context_tool}, direction={result.comparison_direction}"
        )

        return result

    def clear_context(self, user_id: str, chat_id: str):
        """Clear context for user in chat."""
        key = self._context_key(user_id, chat_id)
        self._contexts.pop(key, None)

    def get_stats(self) -> Dict[str, Any]:
        """Get resolver statistics."""
        return {
            "total_contexts": len(self._contexts),
            "max_contexts": self._max_contexts,
        }
