"""Heuristic funnel stage updates from user message text (shared across memory backends)."""


def suggest_funnel_stage(current: str, user_message: str) -> str:
    """Infer the next funnel stage from the latest user message.

    Args:
        current: Current stage string from persistence (e.g. ``unknown``, ``engaged``).
        user_message: Latest inbound user text.

    Returns:
        Updated stage label; returns ``current`` when no signal matches.
    """
    text_lower = (user_message or "").lower()

    buy_signals = [
        "купить",
        "заказать",
        "сколько стоит",
        "как оплатить",
        "доставка",
        "когда приедет",
        "хочу заказать",
        "беру",
        "скинь ссылку",
        "где купить",
    ]
    if any(s in text_lower for s in buy_signals):
        return "ready_to_buy"

    interest_signals = [
        "подробнее",
        "расскажи",
        "а как",
        "а почему",
        "а сколько",
        "а если",
        "подходит ли",
        "а что насчёт",
        "а в чём разница",
        "интересно",
        "а правда что",
    ]
    if any(s in text_lower for s in interest_signals):
        return "asking_questions" if current not in ("unknown", "noticed") else "interested"

    objection_signals = ["дорого", "мало денег", "не уверен", "подумаю", "не сейчас", "может позже"]
    if any(s in text_lower for s in objection_signals):
        return "objection"

    disengage_signals = ["не надо", "отстань", "не интересно", "хватит"]
    if any(s in text_lower for s in disengage_signals):
        return "disengaged"

    if current in ("", "unknown") and len(text_lower.strip()) > 2:
        return "in_dialogue"

    return current or "unknown"
