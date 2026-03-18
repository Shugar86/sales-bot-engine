"""
Response Composer — greeting handling, formatting, validation.

Ported from ai-tutor-engine patterns with sales-bot-engine adaptations:
- is_pure_greeting() — detect greeting-only messages (skip LLM)
- looks_like_greeting() — detect messages that start with a greeting
- strip_leading_greeting() — remove duplicate greetings from LLM responses
- Multiple greeting variants with random selection
- Price shock handling
- Off-topic handling
- Banned phrase filtering
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# GREETING DETECTION
# ══════════════════════════════════════════════════════════════

_GREETING_PREFIX_RE = re.compile(
    r"(?is)^\s*(?:"
    r"доброго\s+дня|"
    r"добрый\s+день|"
    r"здравствуйте|"
    r"здравствуй|"
    r"привет(?:ствую)?|"
    r"доброе\s+утро|"
    r"добрый\s+вечер|"
    r"хай|"
    r"хей|"
    r"салют"
    r")(?:[^\n]{0,80})(?:[!\.\?]|[\r\n])\s*"
)

_PURE_GREETINGS = {
    "привет",
    "приветик",
    "здравствуй",
    "здравствуйте",
    "добрый день",
    "доброго дня",
    "доброе утро",
    "добрый вечер",
    "хай",
    "хей",
    "салют",
    "здарова",
    "здорово",
    "здрасте",
    "приветствую",
    "да",
    "давай",
    "давайте",
    "конечно",
    "хочу",
    "ок",
    "окей",
    "ага",
    "угу",
    "ладно",
    "понял",
    "ясно",
    "го",
    "погнали",
}

_FOLLOWUP_COMMAND_TOKENS = {
    "покажи", "показать", "еще", "ещё", "дальше", "далее",
    "следующие", "продолжай", "продолжить", "все", "всё",
    "пожалуйста", "пж", "плиз", "да", "ок", "окей", "хорошо",
    "еще", "более", "подешевле", "подороже", "другой", "другие",
    "похожие", "аналоги", "альтернативы", "вариант", "варианты",
}


def is_pure_greeting(text: str) -> bool:
    """Return True if message is a greeting-only utterance.
    
    Examples: "привет", "ок", "давай", "здравствуйте"
    These should skip LLM and return a greeting variant directly.
    """
    if not text:
        return False
    normalized = text.strip().lower().replace("ё", "е")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.strip("!?. \n\t")
    return normalized in _PURE_GREETINGS


def looks_like_greeting(text: str) -> bool:
    """Return True if user text starts like a greeting.
    
    Examples: "привет, как дела?", "здравствуйте, подскажите..."
    These should have greeting stripped from LLM response.
    """
    if not text:
        return False
    t = text.strip().lower().replace("ё", "е")
    return bool(
        re.match(
            r"^(?:"
            r"привет(?:ствую)?|"
            r"здравствуй|"
            r"здравствуйте|"
            r"добрый\s+день|"
            r"доброго\s+дня|"
            r"доброе\s+утро|"
            r"добрый\s+вечер|"
            r"хай|"
            r"хей|"
            r"салют|"
            r"здарова|"
            r"здорово"
            r")\b",
            t,
        )
    )


def strip_leading_greeting(text: str) -> str:
    """Strip a leading greeting line from LLM response.
    
    Prevents duplicate greetings when user already greeted.
    """
    if not text:
        return text
    stripped = _GREETING_PREFIX_RE.sub("", text, count=1)
    return stripped.lstrip("\n ").lstrip()


# ══════════════════════════════════════════════════════════════
# GREETING POLICY
# ══════════════════════════════════════════════════════════════

@dataclass
class GreetingPolicy:
    """Configuration for greeting behavior."""
    enabled: bool = True
    greet_only_first_response: bool = True
    greet_only_if_user_greeted: bool = True
    strip_greeting_if_not_allowed: bool = True
    greeting_variants: List[str] = field(default_factory=list)
    fallback_variants: List[str] = field(default_factory=lambda: [
        "Я на связи. Чем помочь?",
        "Слушаю тебя!",
        "Что нужно?",
    ])


def _stable_seed(*parts: str) -> int:
    """Generate stable seed from string parts for deterministic variant selection."""
    joined = "|".join(p for p in parts if p)
    if not joined:
        return 0
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _pick_variant(variants: List[str], seed: int) -> str:
    """Pick a variant deterministically based on seed."""
    if not variants:
        return ""
    idx = seed % len(variants)
    return (variants[idx] or "").strip()


# ══════════════════════════════════════════════════════════════
# PRICE SHOCK HANDLING
# ══════════════════════════════════════════════════════════════

PRICE_SHOCK_PHRASES = {
    "expensive": [
        "дорого", "дороговато", "многовато", "кусается", "недёшево",
        "цена кусается", "за такие деньги", "это дорого",
    ],
    "shock": [
        "ого", "ого сколько", "чё", "ого", "ух ты", "вау дорого",
        "ну это", "ну и цена", "ого аж",
    ],
}

PRICE_SHOCK_RESPONSES = [
    "Понимаю, цена может удивить. Но смотри: тут натуральный состав, без химии и консервантов. Здоровье питомца — это инвестиция, не трата.",
    "Да, не из дешёвых. Но 100% натуральный состав — это профилактика аллергии и проблем с ЖКТ. Сколько ты тратишь на ветеринара в год?",
    "Смотри сколько тратишь на ветеринара от аллергии на дешёвый корм — и станет понятно что выгоднее. Но есть вариант подешевле / маленькая фасовка для пробы.",
    "Натуралка всегда дороже «химии». Но состав как для людей — без сои, без красителей. Есть маленькая фасовка если хочешь попробовать сначала.",
]


def is_price_shock(text: str) -> bool:
    """Detect if user is reacting to price with shock/objection."""
    if not text:
        return False
    t = text.lower()
    return any(phrase in t for phrases in PRICE_SHOCK_PHRASES.values() for phrase in phrases)


def get_price_shock_response(text: str, persona_name: str = "") -> str:
    """Get a price shock response (deterministic, no LLM needed)."""
    seed = _stable_seed(persona_name, "price_shock", text)
    return _pick_variant(PRICE_SHOCK_RESPONSES, seed)


# ══════════════════════════════════════════════════════════════
# OFF-TOPIC HANDLING
# ══════════════════════════════════════════════════════════════

OFFTOPIC_JOKE_PHRASES = [
    "шутк", "анекдот", "посмеши", "рассмеши", "юмор",
    "прикол", "смешно", "шути", "расскажи шутку",
]

OFFTOPIC_REDIRECTS = [
    "А теперь давайте подберём что-то для вашего питомца! 🐾",
    "Ладно, вернёмся к делу — чем могу помочь?",
    "Ну вот, а теперь серьёзно — что ищете?",
    "Ха, а теперь к нашим баранам... точнее, кормам 😄 Чем помочь?",
]


def is_offtopic_joke_request(text: str) -> bool:
    """Detect if user is asking for a joke or off-topic content."""
    if not text:
        return False
    t = text.lower()
    return any(phrase in t for phrase in OFFTOPIC_JOKE_PHRASES)


def get_offtopic_response(text: str, persona_name: str = "") -> str:
    """Get off-topic handling response with joke + redirect."""
    seed = _stable_seed(persona_name, "offtopic", text)
    return _pick_variant(OFFTOPIC_REDIRECTS, seed)


# ══════════════════════════════════════════════════════════════
# FOLLOW-UP DETECTION
# ══════════════════════════════════════════════════════════════

def is_pure_followup(text: str) -> bool:
    """Detect if message is a pure follow-up command.
    
    Examples: "покажи ещё", "еще", "далее", "продолжай"
    Should reuse last tool call with same args.
    """
    if not text:
        return False
    q_tokens = re.findall(r"[a-zа-яё0-9]+", text.lower())
    if not q_tokens:
        return False
    return all(t in _FOLLOWUP_COMMAND_TOKENS for t in q_tokens)


# ══════════════════════════════════════════════════════════════
# RESPONSE COMPOSER CLASS
# ══════════════════════════════════════════════════════════════

@dataclass
class CompositionContext:
    """Inputs for response composition."""
    question: str
    is_first_response: bool = False
    user_greeted: bool = False
    persona_name: str = ""
    tool_name: Optional[str] = None
    tool_status: str = "none"  # "success", "no_results", "error", "none"
    has_previous_context: bool = False


class ResponseComposer:
    """Config-driven response composer for a persona."""

    def __init__(
        self,
        persona_name: str,
        greeting_policy: Optional[GreetingPolicy] = None,
        banned_phrases: Optional[List[str]] = None,
    ):
        self.persona_name = persona_name
        self.greeting_policy = greeting_policy or GreetingPolicy()
        self.banned_phrases = banned_phrases or []

    def compose_greeting(self, ctx: CompositionContext) -> Optional[str]:
        """Return a deterministic greeting if allowed by policy."""
        policy = self.greeting_policy
        if not policy.enabled or not policy.greeting_variants:
            return None
        if policy.greet_only_first_response and not ctx.is_first_response:
            return None
        if policy.greet_only_if_user_greeted and not ctx.user_greeted:
            return None
        seed = _stable_seed(self.persona_name, ctx.question)
        return _pick_variant(policy.greeting_variants, seed) or None

    def compose_pure_greeting_reply(self, ctx: CompositionContext) -> Optional[str]:
        """Return a reply for a pure greeting (no LLM needed)."""
        if not is_pure_greeting(ctx.question):
            return None
        greeting = self.compose_greeting(ctx)
        if greeting:
            return greeting
        seed = _stable_seed(self.persona_name, "fallback", ctx.question)
        return _pick_variant(self.greeting_policy.fallback_variants, seed) or None

    def should_strip_greeting(self, ctx: CompositionContext) -> bool:
        """Check if greeting should be stripped from LLM response."""
        policy = self.greeting_policy
        if not policy.enabled or not policy.strip_greeting_if_not_allowed:
            return False
        allowed = self.compose_greeting(ctx) is not None
        return not allowed

    def postprocess(self, text: str, ctx: CompositionContext) -> str:
        """Apply deterministic post-processing."""
        out = text or ""

        # Banned phrase filtering
        if self.banned_phrases:
            out = self._strip_banned_phrases(out)

        # Greeting stripping
        if self.should_strip_greeting(ctx):
            out = strip_leading_greeting(out)

        return out.strip()

    def _strip_banned_phrases(self, text: str) -> str:
        """Remove banned phrases from response."""
        if not text or not self.banned_phrases:
            return text
        out = text
        for phrase in self.banned_phrases:
            if not phrase:
                continue
            out = re.sub(re.escape(phrase), "", out, flags=re.IGNORECASE)
        # Clean up artifacts
        out = re.sub(r"—\s*!", "", out)
        out = re.sub(r",\s*!", "", out)
        out = re.sub(r"\?\s*!", "?", out)
        out = re.sub(r"(?m)^\s*[!?.]+\s*", "", out)
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()

    def handle_price_shock(self, ctx: CompositionContext) -> Optional[str]:
        """Handle price shock if detected."""
        if is_price_shock(ctx.question):
            return get_price_shock_response(ctx.question, self.persona_name)
        return None

    def handle_offtopic(self, ctx: CompositionContext) -> Optional[str]:
        """Handle off-topic joke requests."""
        if is_offtopic_joke_request(ctx.question):
            return get_offtopic_response(ctx.question, self.persona_name)
        return None
