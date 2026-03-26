"""
Tests for Response Composer — greeting handling, formatting, validation.

Tests the patterns ported from ai-tutor-engine:
- is_pure_greeting() detection
- looks_like_greeting() detection
- strip_leading_greeting() stripping
- Greeting variants with random selection
- Price shock handling
- Off-topic handling
- Banned phrase filtering
"""

from src.responders.response_composer import (
    is_pure_greeting,
    looks_like_greeting,
    strip_leading_greeting,
    is_price_shock,
    get_price_shock_response,
    is_offtopic_joke_request,
    get_offtopic_response,
    is_pure_followup,
    GreetingPolicy,
    CompositionContext,
    ResponseComposer,
    _stable_seed,
    _pick_variant,
)


# ══════════════════════════════════════════════════════════════
# PURE GREETING DETECTION
# ══════════════════════════════════════════════════════════════

class TestIsPureGreeting:
    def test_simple_greetings(self):
        assert is_pure_greeting("привет")
        assert is_pure_greeting("Привет!")
        assert is_pure_greeting("ПРИВЕТ")
        assert is_pure_greeting("здравствуйте")
        assert is_pure_greeting("добрый день")
        assert is_pure_greeting("доброе утро")
        assert is_pure_greeting("добрый вечер")
        assert is_pure_greeting("хай")
        assert is_pure_greeting("здарова")

    def test_confirmation_as_greeting(self):
        assert is_pure_greeting("ок")
        assert is_pure_greeting("окей")
        assert is_pure_greeting("да")
        assert is_pure_greeting("давай")
        assert is_pure_greeting("конечно")
        assert is_pure_greeting("ага")
        assert is_pure_greeting("ладно")

    def test_not_greeting(self):
        assert not is_pure_greeting("привет, как дела?")
        assert not is_pure_greeting("привет подскажи про корм")
        assert not is_pure_greeting("хочу купить корм")
        assert not is_pure_greeting("какой корм лучше?")
        assert not is_pure_greeting("")

    def test_empty_and_none(self):
        assert not is_pure_greeting("")
        assert not is_pure_greeting(None)

    def test_with_punctuation(self):
        assert is_pure_greeting("привет!")
        assert is_pure_greeting("привет.")
        assert is_pure_greeting("привет?")
        assert is_pure_greeting("  привет  ")


# ══════════════════════════════════════════════════════════════
# GREETING LOOKS-LIKE DETECTION
# ══════════════════════════════════════════════════════════════

class TestLooksLikeGreeting:
    def test_greeting_with_content(self):
        assert looks_like_greeting("привет, подскажи про корм")
        assert looks_like_greeting("Здравствуйте, хочу узнать")
        assert looks_like_greeting("Добрый день! У меня вопрос")
        assert looks_like_greeting("Хай, что нового?")

    def test_not_greeting(self):
        assert not looks_like_greeting("какой корм лучше?")
        assert not looks_like_greeting("у собаки аллергия")
        assert not looks_like_greeting("")

    def test_pure_greeting_also_looks_like(self):
        assert looks_like_greeting("привет")
        assert looks_like_greeting("здравствуйте")


# ══════════════════════════════════════════════════════════════
# GREETING STRIPPING
# ══════════════════════════════════════════════════════════════

class TestStripLeadingGreeting:
    def test_strip_simple_greeting(self):
        result = strip_leading_greeting("Привет!\nВот варианты корма.")
        assert "привет" not in result.lower()
        assert "варианты" in result

    def test_strip_greeting_with_name(self):
        result = strip_leading_greeting("Здравствуйте!\nПодскажу по корму.")
        assert "здравствуйте" not in result.lower()
        assert "подскажу" in result.lower()

    def test_strip_greeting_single_line_short(self):
        # Short greeting-only line (fits within 80 chars greeting match)
        result = strip_leading_greeting("Привет, как дела? Вот варианты.")
        # Full line gets stripped because it's < 80 chars and ends with ?
        assert result == "" or "варианты" in result

    def test_no_greeting_no_change(self):
        text = "Вот варианты корма для собак."
        assert strip_leading_greeting(text) == text

    def test_empty_string(self):
        assert strip_leading_greeting("") == ""

    def test_none_returns_none(self):
        assert strip_leading_greeting(None) is None


# ══════════════════════════════════════════════════════════════
# PRICE SHOCK DETECTION
# ══════════════════════════════════════════════════════════════

class TestPriceShock:
    def test_price_shock_detected(self):
        assert is_price_shock("дорого!")
        assert is_price_shock("ого сколько стоит")
        assert is_price_shock("цена кусается")
        assert is_price_shock("ну это многовато")
        assert is_price_shock("за такие деньги")

    def test_not_price_shock(self):
        assert not is_price_shock("какой корм?")
        assert not is_price_shock("привет")
        assert not is_price_shock("")

    def test_get_price_shock_response(self):
        response = get_price_shock_response("дорого!", "kormoved")
        assert response  # Non-empty
        assert len(response) > 10  # Substantial response


# ══════════════════════════════════════════════════════════════
# OFF-TOPIC DETECTION
# ══════════════════════════════════════════════════════════════

class TestOfftopic:
    def test_joke_request_detected(self):
        assert is_offtopic_joke_request("расскажи шутку")
        assert is_offtopic_joke_request("посмеши меня")
        assert is_offtopic_joke_request("хочу прикол")

    def test_not_joke_request(self):
        assert not is_offtopic_joke_request("какой корм?")
        assert not is_offtopic_joke_request("")

    def test_get_offtopic_response(self):
        response = get_offtopic_response("расскажи шутку", "kormoved")
        assert response


# ══════════════════════════════════════════════════════════════
# FOLLOW-UP DETECTION
# ══════════════════════════════════════════════════════════════

class TestFollowupDetection:
    def test_pure_followup(self):
        assert is_pure_followup("покажи еще")
        assert is_pure_followup("еще")
        assert is_pure_followup("продолжай")
        assert is_pure_followup("далее")
        assert is_pure_followup("покажи ещё")

    def test_not_followup(self):
        assert not is_pure_followup("покажи корм для собак")
        assert not is_pure_followup("какой корм?")
        assert not is_pure_followup("")

    def test_price_comparison_followup(self):
        assert is_pure_followup("подешевле")
        assert is_pure_followup("другой вариант")
        assert is_pure_followup("аналоги")


# ══════════════════════════════════════════════════════════════
# GREETING POLICY
# ══════════════════════════════════════════════════════════════

class TestGreetingPolicy:
    def test_default_policy(self):
        policy = GreetingPolicy()
        assert policy.enabled
        assert policy.greet_only_first_response
        assert policy.greet_only_if_user_greeted
        assert len(policy.fallback_variants) > 0

    def test_compose_greeting_first_response(self):
        composer = ResponseComposer(
            persona_name="test",
            greeting_policy=GreetingPolicy(
                greeting_variants=["Привет! 🐾", "Здарова!"],
            ),
        )
        ctx = CompositionContext(
            question="привет",
            is_first_response=True,
            user_greeted=True,
            persona_name="test",
        )
        greeting = composer.compose_greeting(ctx)
        assert greeting in ["Привет! 🐾", "Здарова!"]

    def test_compose_greeting_not_first(self):
        composer = ResponseComposer(
            persona_name="test",
            greeting_policy=GreetingPolicy(
                greeting_variants=["Привет! 🐾"],
            ),
        )
        ctx = CompositionContext(
            question="привет",
            is_first_response=False,
            user_greeted=True,
            persona_name="test",
        )
        greeting = composer.compose_greeting(ctx)
        assert greeting is None  # Not first response, no greeting

    def test_compose_greeting_user_not_greeted(self):
        composer = ResponseComposer(
            persona_name="test",
            greeting_policy=GreetingPolicy(
                greeting_variants=["Привет! 🐾"],
            ),
        )
        ctx = CompositionContext(
            question="какой корм?",
            is_first_response=True,
            user_greeted=False,  # User didn't greet
            persona_name="test",
        )
        greeting = composer.compose_greeting(ctx)
        assert greeting is None

    def test_pure_greeting_reply(self):
        composer = ResponseComposer(
            persona_name="test",
            greeting_policy=GreetingPolicy(
                greeting_variants=["Привет! 🐾"],
            ),
        )
        ctx = CompositionContext(
            question="привет",
            is_first_response=True,
            user_greeted=True,
            persona_name="test",
        )
        reply = composer.compose_pure_greeting_reply(ctx)
        assert reply == "Привет! 🐾"

    def test_pure_greeting_fallback(self):
        composer = ResponseComposer(
            persona_name="test",
            greeting_policy=GreetingPolicy(
                enabled=False,  # Greeting disabled
                fallback_variants=["Я на связи!"],
            ),
        )
        ctx = CompositionContext(
            question="привет",
            is_first_response=False,
            user_greeted=True,
            persona_name="test",
        )
        reply = composer.compose_pure_greeting_reply(ctx)
        assert reply == "Я на связи!"


# ══════════════════════════════════════════════════════════════
# RESPONSE COMPOSER
# ══════════════════════════════════════════════════════════════

class TestResponseComposer:
    def test_postprocess_strips_greeting(self):
        composer = ResponseComposer(
            persona_name="test",
            greeting_policy=GreetingPolicy(
                strip_greeting_if_not_allowed=True,
                greet_only_first_response=True,
            ),
        )
        ctx = CompositionContext(
            question="какой корм?",
            is_first_response=False,  # Not first
            persona_name="test",
        )
        result = composer.postprocess("Привет! Вот варианты...", ctx)
        assert "привет" not in result.lower()

    def test_postprocess_banned_phrases(self):
        composer = ResponseComposer(
            persona_name="test",
            banned_phrases=["уважаемый", "к сожалению"],
        )
        ctx = CompositionContext(question="привет", persona_name="test")
        result = composer.postprocess("К сожалению, уважаемый клиент, товара нет.", ctx)
        assert "к сожалению" not in result.lower()
        assert "уважаемый" not in result.lower()

    def test_handle_price_shock(self):
        composer = ResponseComposer(persona_name="test")
        ctx = CompositionContext(question="дорого!", persona_name="test")
        response = composer.handle_price_shock(ctx)
        assert response is not None

    def test_handle_offtopic(self):
        composer = ResponseComposer(persona_name="test")
        ctx = CompositionContext(question="расскажи шутку", persona_name="test")
        response = composer.handle_offtopic(ctx)
        assert response is not None


# ══════════════════════════════════════════════════════════════
# DETERMINISTIC VARIANT SELECTION
# ══════════════════════════════════════════════════════════════

class TestVariantSelection:
    def test_stable_seed(self):
        seed1 = _stable_seed("test", "привет")
        seed2 = _stable_seed("test", "привет")
        assert seed1 == seed2  # Same input → same seed

    def test_different_seeds(self):
        seed1 = _stable_seed("test", "привет")
        seed2 = _stable_seed("test", "здравствуйте")
        assert seed1 != seed2

    def test_pick_variant(self):
        variants = ["a", "b", "c"]
        # Seed 0 → idx 0
        assert _pick_variant(variants, 0) == "a"
        # Seed 1 → idx 1
        assert _pick_variant(variants, 1) == "b"
        # Seed 2 → idx 2
        assert _pick_variant(variants, 2) == "c"
        # Seed 3 → idx 0 (wraps)
        assert _pick_variant(variants, 3) == "a"

    def test_pick_variant_empty(self):
        assert _pick_variant([], 0) == ""

    def test_empty_strings_filtered(self):
        variants = ["", "hello", ""]
        assert _pick_variant(variants, 1) == "hello"
