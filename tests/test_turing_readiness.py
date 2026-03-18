"""
Comprehensive Turing Test Readiness Test

This test validates that the sales bot system is ready to pass the Turing test
in real group chats. It checks all the critical components working together.

Tests organized by "Turing test dimension":
1. Human-like response quality
2. Anti-detection features
3. Memory and context
4. Edge case handling
5. Persona realism
"""
import pytest
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock

from src.core.router import MessageRouter, Decision
from src.responders.generator import ResponseGenerator
from src.responders.text_humanizer import TextHumanizer
from src.responders.chat_vibe import ChatVibeDetector, ChatVibe, detect_chat_vibe
from src.monitors.anti_spam import RateLimiter, TypingSpeedCalculator
from src.memory.user_memory import UserMemoryStore
from src.utils.dedup import DeduplicationStore
from src.utils.llm_client import LLMClient
from src.core.persona_manager import load_persona, discover_personas


# ========================================
# DIMENSION 1: Human-Like Response Quality
# ========================================

class TestHumanLikeResponses:
    """Bot responses should be indistinguishable from real humans."""
    
    def setup_method(self):
        self.llm = LLMClient(api_key="test-key")
        self.contract = {
            "persona": {
                "name": "Андрей",
                "backstory": "Бывший кинолог-инструктор, 12 лет стажа",
                "speaking_style": {
                    "tone": "бывалый, спокойный",
                    "patterns": ["Короткие предложения", "Личные истории"],
                    "forbidden": ["к сожалению", "извините"],
                },
                "competitor_knowledge": "Royal Canin: норм корм",
            },
            "product": {"products": [{"name": "Корм для собак", "price": "2500₽"}]},
            "triggers": {"respond_to": [], "ignore": []},
            "conversation_flow": {"group_chat": {"steps": []}, "never": []},
        }
    
    def test_response_examples_exist_in_all_personas(self):
        """Every persona should have response_examples for training."""
        personas = discover_personas("./personas")
        assert len(personas) > 0, "No personas found"
        
        for persona in personas:
            assert len(persona.response_examples) >= 5, (
                f"Persona '{persona.name}' has only {len(persona.response_examples)} "
                f"response_examples (need at least 5)"
            )
    
    def test_response_examples_have_good_and_bad(self):
        """Each response example should have both good and bad responses."""
        personas = discover_personas("./personas")
        
        for persona in personas:
            for i, ex in enumerate(persona.response_examples):
                assert ex.trigger, f"Persona '{persona.name}' example {i} missing trigger"
                assert ex.good_response, f"Persona '{persona.name}' example {i} missing good_response"
                assert ex.bad_response, f"Persona '{persona.name}' example {i} missing bad_response"
    
    def test_competitor_knowledge_exists(self):
        """Personas should have competitor knowledge for natural conversation."""
        personas = discover_personas("./personas")
        
        for persona in personas:
            assert persona.competitor_knowledge, (
                f"Persona '{persona.name}' missing competitor_knowledge"
            )
    
    def test_group_context_examples_exist(self):
        """Personas should have group context examples for non-product topics."""
        personas = discover_personas("./personas")
        
        for persona in personas:
            assert len(persona.group_context_examples) >= 3, (
                f"Persona '{persona.name}' has only {len(persona.group_context_examples)} "
                f"group_context_examples (need at least 3)"
            )
    
    def test_generator_accepts_vibe(self):
        """Generator should accept and use chat vibe for matching."""
        generator = ResponseGenerator(
            llm_client=self.llm,
            model="test",
            contract=self.contract,
        )
        # Should accept chat_vibe parameter without error
        import inspect
        sig = inspect.signature(generator.generate_group_response)
        assert "chat_vibe" in sig.parameters
    
    def test_text_humanizer_exists(self):
        """Text humanizer should exist for making text look human."""
        humanizer = TextHumanizer()
        text = "Привет, как дела?"
        result = humanizer.humanize(text, is_casual=True)
        assert isinstance(result, str)
        assert len(result) > 0
    
    def test_response_length_varies(self):
        """Humanizer should sometimes produce shorter/casual text."""
        humanizer = TextHumanizer(
            typo_probability=0.5,
            lowercase_start_probability=0.9,
            missing_period_probability=0.9,
        )
        
        results = set()
        for _ in range(50):
            result = humanizer.humanize("Спасибо за помощь.", is_casual=True)
            results.add(result)
        
        # Should have some variation (not always identical)
        assert len(results) > 1, "Humanizer produces identical output every time"


# ========================================
# DIMENSION 2: Anti-Detection Features
# ========================================

class TestAntiDetection:
    """Bot should be undetectable as a bot."""
    
    def test_leave_on_read_exists(self):
        """Bot should sometimes read but not respond (35% rate)."""
        limiter = RateLimiter(leave_on_read_probability=0.35)
        
        leave_count = 0
        total = 1000
        for _ in range(total):
            if limiter.should_leave_on_read():
                leave_count += 1
        
        rate = leave_count / total
        assert 0.25 <= rate <= 0.45, f"Leave-on-read rate {rate:.2f} outside 25-45% range"
    
    def test_emoji_reactions_exist(self):
        """Bot should sometimes react with emoji instead of text."""
        limiter = RateLimiter(emoji_reaction_probability=0.15)
        
        react_count = 0
        total = 1000
        for _ in range(total):
            if limiter.should_use_emoji_reaction():
                react_count += 1
        
        rate = react_count / total
        assert 0.08 <= rate <= 0.25, f"Emoji reaction rate {rate:.2f} outside 8-25% range"
    
    def test_contextual_emoji_reactions(self):
        """Emoji reactions should match message context."""
        limiter = RateLimiter()
        
        # "Спасибо" should give positive emoji
        emoji = limiter.get_emoji_reaction("Спасибо за помощь")
        assert emoji in ["❤️", "👍", "😊"]
        
        # Funny message should give laughing emoji
        emoji = limiter.get_emoji_reaction("Хахаха это смешно 😂")
        assert emoji in ["😂", "🤣", "😄"]
    
    def test_time_of_day_delays(self):
        """Night delays should be longer than day delays."""
        limiter = RateLimiter(
            min_delay_sec=30,
            max_delay_sec=300,
            night_delay_multiplier=3.0,
            active_hours_start=8,
            active_hours_end=23,
        )
        
        day_avg = sum(limiter.get_random_delay(current_hour=14) for _ in range(100)) / 100
        night_avg = sum(limiter.get_random_delay(current_hour=3) for _ in range(100)) / 100
        
        assert night_avg > day_avg * 1.5, (
            f"Night avg ({night_avg:.0f}s) should be >1.5x day avg ({day_avg:.0f}s)"
        )
    
    def test_typing_speed_varies(self):
        """Typing speed should vary with message complexity."""
        calc = TypingSpeedCalculator()
        
        short = calc.estimate_typing_time("Да")
        long = calc.estimate_typing_time("Длинный ответ с множеством слов и сложной структурой")
        question = calc.estimate_typing_time("Какой корм лучше для щенка?")
        
        assert short < long, "Short text should type faster than long"
        assert question > short, "Questions should take longer (thinking time)"
    
    def test_humanizer_produces_typos(self):
        """Text humanizer should occasionally produce subtle typos."""
        humanizer = TextHumanizer(typo_probability=1.0)  # Always typo
        
        results = set()
        for _ in range(100):
            result = humanizer.humanize("Конечно проблема собака", is_casual=False)
            results.add(result)
        
        # At least some should be different (typos applied)
        assert len(results) > 1, "No typos produced even at 100% probability"
    
    def test_delays_are_human_realistic(self):
        """Anti-spam delays should be human-realistic (30s-5min)."""
        limiter = RateLimiter(min_delay_sec=30, max_delay_sec=300)
        
        delays = [limiter.get_random_delay(current_hour=14) for _ in range(100)]
        
        assert min(delays) >= 30, f"Min delay {min(delays)}s is too fast"
        assert max(delays) <= 900, f"Max delay {max(delays)}s is too slow"  # 3x night cap


# ========================================
# DIMENSION 3: Memory & Context
# ========================================

class TestMemoryAndContext:
    """Bot should remember users and context across interactions."""
    
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
    
    def test_user_memory_persists(self):
        """User memory should persist across sessions."""
        memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
        
        memory.record_group_message(
            user_id="u1", username="test", display_name="Тест",
            chat_id="c1", chat_title="Chat", message="У меня лабрадор зовут Рекс",
        )
        
        # New memory store instance should load saved data
        memory2 = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
        context = memory2.get_user_context("u1")
        assert "лабрадор" in context.lower()
    
    def test_funnel_progression(self):
        """Funnel should progress based on signals."""
        memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
        
        # Unknown → interested
        stage = memory.analyze_funnel_signals("u1", "Расскажи подробнее про корм")
        assert stage == "interested"
        
        # Interested → ready_to_buy
        stage = memory.analyze_funnel_signals("u1", "Хочу заказать")
        assert stage == "ready_to_buy"
    
    def test_recommendations_tracked(self):
        """Bot should track what it already recommended."""
        memory = UserMemoryStore(memory_dir=self.tmpdir, persona_name="kormoved")
        
        memory.record_recommendation("u1", "Корм на ягнёнке")
        recs = memory.get_recommendations("u1")
        assert len(recs) == 1
        assert "ягнёнк" in recs[0].lower()
    
    def test_chat_vibe_detection(self):
        """Chat vibe should be detectable from messages."""
        # Drunk chat
        vibe = detect_chat_vibe(["Бухаю водку 😂😂", "Выпил три бутылки", "Шашлык огонь"])
        assert vibe.primary_vibe == ChatVibe.DRUNK
        
        # Sad chat
        vibe = detect_chat_vibe(["Грустно", "Тяжело", "Депрессия"])
        assert vibe.primary_vibe == ChatVibe.SAD
    
    def test_response_deduplication(self):
        """Bot should not repeat the same response."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DeduplicationStore(
                storage_path=os.path.join(tmpdir, "dedup.json")
            )
            store.record_bot_response("c1", "Рекомендую корм на ягнёнке")
            
            assert store.is_repeating_response(
                "c1", "Рекомендую корм на ягнёнке для аллергиков",
                similarity_threshold=0.5
            ) is True


# ========================================
# DIMENSION 4: Edge Case Handling
# ========================================

class TestEdgeCaseHandling:
    """Bot should handle all edge cases gracefully."""
    
    def setup_method(self):
        self.llm = LLMClient(api_key="test-key")
        self.contract = {
            "persona": {"name": "Андрей", "backstory": "Кинолог", "speaking_style": {"tone": "expert", "patterns": [], "forbidden": []}},
            "product": {"products": []},
            "triggers": {"respond_to": [], "ignore": []},
            "conversation_flow": {"group_chat": {"steps": []}, "never": []},
        }
        self.router = MessageRouter(llm_client=self.llm, model="test", contract=self.contract)
        self.generator = ResponseGenerator(llm_client=self.llm, model="test", contract=self.contract)
    
    def test_trivial_messages_ignored(self):
        """Trivial messages should be silently ignored."""
        trivial = [".", "👍", "😂😂😂", "++", "---", "???", "123", "ок", "ага"]
        for msg in trivial:
            result = self.router._prefilter(msg)
            assert result is not None, f"'{msg}' should be prefiltered"
            assert result.decision == Decision.IGNORE, f"'{msg}' should be IGNORED"
    
    def test_go_away_handled(self):
        """'Отстань' should trigger disengage, not argument."""
        result = self.router._prefilter("Отстань, не пиши мне")
        assert result.decision == Decision.DISENGAGE
        
        response = self.generator.generate_go_away_response("Андрей")
        assert response.stage == "disengage"
        assert "извини" in response.text.lower() or "понял" in response.text.lower() or "хорошо" in response.text.lower() or "ладно" in response.text.lower()
    
    def test_bot_question_handled(self):
        """'Ты бот?' should trigger denial, not confession."""
        assert self.generator.detect_bot_question("Ты бот?") is True
        assert self.generator.detect_bot_question("Ты AI?") is True
        assert self.generator.detect_bot_question("Какой корм лучше?") is False
        
        response = self.generator.generate_bot_denial_response("Андрей")
        assert response.tone == "humor"
        # Response should deny being a bot, not confess
        assert "не бот" in response.text.lower() or "какой бот" in response.text.lower() or "нет" in response.text.lower()
    
    def test_spam_ignored(self):
        """Spam patterns should be silently ignored."""
        spam = [
            "https://t.me/spamchannel",
            "Заработай на крипто трейдинге",
            "Подпишись на канал получи бонус",
        ]
        for msg in spam:
            result = self.router._prefilter(msg)
            assert result is not None, f"'{msg}' should be prefiltered"
            assert result.decision == Decision.IGNORE
    
    def test_bare_links_ignored(self):
        """Bare links without context should be ignored."""
        result = self.router._prefilter("https://example.com")
        assert result.decision == Decision.IGNORE
    
    def test_dm_always_engages(self):
        """DMs should always be engaged (never ignored)."""
        import asyncio
        result = asyncio.run(self.router.route("Привет", is_dm=True))
        assert result.decision == Decision.SALES_DM


# ========================================
# DIMENSION 5: Persona Realism
# ========================================

class TestPersonaRealism:
    """Personas should feel like real people."""
    
    def test_all_personas_loadable(self):
        """All persona YAML files should load without errors."""
        personas = discover_personas("./personas")
        assert len(personas) >= 3, f"Expected at least 3 personas, found {len(personas)}"
    
    def test_persona_names_are_real(self):
        """Persona names should be real human names."""
        personas = discover_personas("./personas")
        for persona in personas:
            # Name should not be "Bot", "AI", "Assistant" etc
            forbidden_names = ["bot", "ai", "assistant", "chatgpt", "gpt", "нейросеть"]
            assert persona.name.lower() not in forbidden_names, (
                f"Persona name '{persona.name}' sounds like a bot"
            )
    
    def test_persona_backstories_exist(self):
        """Every persona should have a backstory."""
        personas = discover_personas("./personas")
        for persona in personas:
            assert len(persona.personality) >= 50, (
                f"Persona '{persona.name}' backstory too short ({len(persona.personality)} chars)"
            )
    
    def test_persona_has_products(self):
        """Every persona should have product information."""
        personas = discover_personas("./personas")
        for persona in personas:
            assert persona.product_name, f"Persona '{persona.name}' missing product_name"
    
    def test_persona_delays_human_realistic(self):
        """Persona delays should be human-realistic (30-300s)."""
        personas = discover_personas("./personas")
        for persona in personas:
            assert persona.anti_spam.min_delay_between_messages >= 20, (
                f"Persona '{persona.name}' min delay too fast"
            )
            assert persona.anti_spam.max_delay_between_messages <= 600, (
                f"Persona '{persona.name}' max delay too slow"
            )
    
    def test_persona_has_dm_funnel(self):
        """Every persona should have a DM sales funnel."""
        personas = discover_personas("./personas")
        for persona in personas:
            assert len(persona.dm_mode.funnel) >= 2, (
                f"Persona '{persona.name}' DM funnel too short"
            )
