"""
Preprocess Node — deterministic routing before LLM.

Ported from ai-tutor-engine PreprocessNode pattern:
- Pure greeting → skip LLM, return greeting variant
- Follow-up reuse → "покажи ещё" reuses last tool call
- Regex shortcuts → common patterns without LLM
- Price shock detection
- Off-topic joke requests
- Trivial/empty messages

This saves LLM tokens and reduces latency for common patterns.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .response_composer import (
    CompositionContext,
    ResponseComposer,
    GreetingPolicy,
    is_pure_greeting,
    is_pure_followup,
    is_price_shock,
    is_offtopic_joke_request,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# TRIVIAL MESSAGE DETECTION
# ══════════════════════════════════════════════════════════════

TRIVIAL_PATTERNS = [
    r"^[\.\+\-\!\?]{1,5}$",         # ".", "!!!", "???", "+++", "---"
    r"^[\U0001F600-\U0001F64F]{1,3}$",  # emoji-only (1-3 emojis)
    r"^(ок|окей|окей|ага|угу|да|нет|嗯|hmm|хм)$",
    r"^(👍|👎|❤️|😂|🔥|💯|🙏|👏){1,3}$",
]

TRIVIAL_RE = re.compile("|".join(f"(?:{p})" for p in TRIVIAL_PATTERNS), re.IGNORECASE)


def is_trivial_message(text: str) -> bool:
    """Detect messages too trivial to warrant LLM processing."""
    if not text:
        return True
    cleaned = text.strip()
    if len(cleaned) < 2:
        return True
    return bool(TRIVIAL_RE.match(cleaned))


# ══════════════════════════════════════════════════════════════
# PREPROCESS RESULT
# ══════════════════════════════════════════════════════════════

@dataclass
class PreprocessResult:
    """Result of preprocessing — either a shortcut or None to continue to LLM."""
    shortcut_response: Optional[str] = None  # If set, skip LLM and use this
    reuse_last_tool: bool = False            # If True, reuse last tool call
    modified_query: Optional[str] = None     # If set, use this query instead
    pipeline_step: str = ""                  # Logging label
    skip_generation: bool = False            # If True, don't generate at all

    @property
    def has_shortcut(self) -> bool:
        return self.shortcut_response is not None or self.reuse_last_tool or self.skip_generation


# ══════════════════════════════════════════════════════════════
# PREPROCESS NODE
# ══════════════════════════════════════════════════════════════

class PreprocessNode:
    """Deterministic routing before LLM router/generator.

    Handles:
    1. Pure greetings → greeting variant (no LLM)
    2. Trivial messages → skip
    3. Follow-up reuse → reuse last tool
    4. Price shock → price shock response
    5. Off-topic joke → joke + redirect
    """

    def __init__(
        self,
        composer: ResponseComposer,
        followup_reuse_tools: Optional[List[str]] = None,
    ):
        self.composer = composer
        self.followup_reuse_tools = followup_reuse_tools or []

    def process(
        self,
        question: str,
        last_context: Dict[str, Any],
        is_first_response: bool = False,
        user_greeted: bool = False,
        is_dm: bool = False,
    ) -> PreprocessResult:
        """Run preprocessing rules.

        Args:
            question: User's message text
            last_context: Previous context (last_tool_name, last_tool_args, etc.)
            is_first_response: Whether this is the first response in conversation
            user_greeted: Whether the user's message contains a greeting
            is_dm: Whether this is a DM

        Returns:
            PreprocessResult with shortcut or empty result to continue to LLM
        """
        ctx = CompositionContext(
            question=question,
            is_first_response=is_first_response,
            user_greeted=user_greeted,
            persona_name=self.composer.persona_name,
        )

        # 1. Trivial messages — skip entirely
        if is_trivial_message(question):
            logger.debug("Preprocess: Trivial message, skipping")
            return PreprocessResult(
                skip_generation=True,
                pipeline_step="preprocess (trivial_skip)",
            )

        # 2. Pure greeting — return greeting variant
        if is_pure_greeting(question):
            greeting = self.composer.compose_pure_greeting_reply(ctx)
            if greeting:
                logger.info("Preprocess: Pure greeting detected, returning greeting variant")
                return PreprocessResult(
                    shortcut_response=greeting,
                    pipeline_step="preprocess (greeting_skip)",
                )

        # 3. Follow-up reuse — reuse last tool call
        if is_pure_followup(question):
            last_tool_name = last_context.get("last_tool_name")
            last_tool_args = last_context.get("last_tool_args", {})
            if last_tool_name in self.followup_reuse_tools and last_tool_args:
                logger.info(f"Preprocess: Follow-up detected, reusing tool {last_tool_name}")
                return PreprocessResult(
                    reuse_last_tool=True,
                    pipeline_step="preprocess (reuse_last_tool)",
                )

        # 4. Price shock — deterministic response
        if is_price_shock(question):
            response = self.composer.handle_price_shock(ctx)
            if response:
                logger.info("Preprocess: Price shock detected")
                return PreprocessResult(
                    shortcut_response=response,
                    pipeline_step="preprocess (price_shock)",
                )

        # 5. Off-topic joke request — handle without LLM
        if is_offtopic_joke_request(question):
            response = self.composer.handle_offtopic(ctx)
            if response:
                logger.info("Preprocess: Off-topic joke request")
                return PreprocessResult(
                    shortcut_response=response,
                    pipeline_step="preprocess (offtopic_joke)",
                )

        # No shortcut found — continue to LLM
        return PreprocessResult(pipeline_step="preprocess (no_shortcut)")
