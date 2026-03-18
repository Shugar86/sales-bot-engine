"""
Tests for Edge Cases — bot detection, trivial messages, voice, de-escalation
"""
import pytest
from src.core.router import MessageRouter, Decision
from src.responders.generator import ResponseGenerator
from src.utils.llm_client import LLMClient


class TestTrivialMessages:
    """Test handling of trivial/minimal messages."""
    
    def setup_method(self):
        self.llm = LLMClient(api_key="test-key")
        self.contract = {
            "persona": {
                "name": "Андрей",
                "backstory": "Кинолог",
                "speaking_style": {"tone": "expert", "patterns": [], "forbidden": []},
            },
            "product": {"products": []},
            "triggers": {"respond_to": [], "ignore": []},
            "conversation_flow": {"group_chat": {"steps": []}, "never": []},
        }
        self.router = MessageRouter(
            llm_client=self.llm,
            model="test",
            contract=self.contract,
        )
    
    def test_dot_ignored(self):
        """Single dot should be ignored."""
        result = self.router._prefilter(".")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_plus_plus_ignored(self):
        """'++' should be ignored."""
        result = self.router._prefilter("++")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_question_marks_ignored(self):
        """'???' should be ignored."""
        result = self.router._prefilter("???")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_dash_dash_ignored(self):
        """'---' should be ignored."""
        result = self.router._prefilter("---")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_emoji_only_ignored(self):
        """Just emojis should be ignored."""
        result = self.router._prefilter("😂😂😂")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_number_only_ignored(self):
        """'123' should be ignored."""
        result = self.router._prefilter("123")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_meaningful_question_passes(self):
        """Real questions should pass prefilter."""
        result = self.router._prefilter("Какой корм лучше для щенка лабрадора?")
        # Should not be pre-filtered out (needs LLM)
        assert result is None  # None = needs LLM routing


class TestBotToBotDetection:
    """Test detection of bot-like behavior patterns."""
    
    def test_perfect_formatting_suspicious(self):
        """Perfectly formatted text with lists might be from a bot."""
        from src.responders.text_humanizer import TextHumanizer
        humanizer = TextHumanizer()
        
        bot_text = (
            "1. Первый пункт.\n"
            "2. Второй пункт.\n"
            "3. Третий пункт."
        )
        
        # Humanizer should make it less perfect
        result = humanizer.humanize(bot_text, is_casual=True)
        assert isinstance(result, str)
    
    def test_repeated_response_pattern(self):
        """Bot repeating similar responses is detectable."""
        from src.utils.dedup import DeduplicationStore
        import tempfile
        import os
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DeduplicationStore(
                storage_path=os.path.join(tmpdir, "dedup.json")
            )
            
            # Bot sends same response
            store.record_bot_response("c1", "Рекомендую корм на ягнёнке")
            store.record_bot_response("c1", "Рекомендую корм на ягнёнке для аллергиков")
            store.record_bot_response("c1", "Рекомендую гипоаллергенный корм на ягнёнке")
            
            # New similar response should be detected
            assert store.is_repeating_response(
                "c1",
                "Рекомендую корм на ягнёнке всем",
                similarity_threshold=0.5
            ) is True


class TestVoiceMessageHandling:
    """Test graceful handling of voice messages."""
    
    def setup_method(self):
        self.llm = LLMClient(api_key="test-key")
        self.contract = {
            "persona": {
                "name": "Андрей",
                "backstory": "Кинолог",
                "speaking_style": {"tone": "expert", "patterns": [], "forbidden": []},
            },
            "product": {"products": []},
            "triggers": {"respond_to": [], "ignore": []},
            "conversation_flow": {"group_chat": {"steps": []}, "never": []},
        }
        self.router = MessageRouter(
            llm_client=self.llm,
            model="test",
            contract=self.contract,
        )
    
    def test_voice_message_metadata_ignored(self):
        """Voice message metadata (empty text) should be handled gracefully."""
        # Voice messages come as empty text or special marker
        result = self.router._prefilter("")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_voice_transcript_routed(self):
        """Voice transcript should be routed normally."""
        result = self.router._prefilter("Привет, какой корм посоветуешь?")
        # Should need LLM (None = needs routing)
        assert result is None


class TestDeEscalation:
    """Test argument de-escalation patterns."""
    
    def setup_method(self):
        self.llm = LLMClient(api_key="test-key")
        self.contract = {
            "persona": {
                "name": "Андрей",
                "backstory": "Кинолог",
                "speaking_style": {"tone": "expert", "patterns": [], "forbidden": []},
            },
            "product": {"products": []},
            "triggers": {"respond_to": [], "ignore": []},
            "conversation_flow": {"group_chat": {"steps": []}, "never": []},
        }
        self.generator = ResponseGenerator(
            llm_client=self.llm,
            model="test",
            contract=self.contract,
        )
    
    def test_go_away_is_deescalation(self):
        """'Отстань' should trigger de-escalation, not argument."""
        response = self.generator.generate_go_away_response("Андрей")
        assert response.stage == "disengage"
        assert "извини" in response.text.lower() or "понял" in response.text.lower() or "хорошо" in response.text.lower() or "ладно" in response.text.lower()
    
    def test_go_away_not_aggressive(self):
        """De-escalation should not be aggressive."""
        response = self.generator.generate_go_away_response("Андрей")
        aggressive_words = ["дурак", "идиот", "заткнись", "отвали"]
        text_lower = response.text.lower()
        for word in aggressive_words:
            assert word not in text_lower


class TestAdminWarning:
    """Test handling of admin warnings."""
    
    def setup_method(self):
        self.llm = LLMClient(api_key="test-key")
        self.contract = {
            "persona": {
                "name": "Андрей",
                "backstory": "Кинолог",
                "speaking_style": {"tone": "expert", "patterns": [], "forbidden": []},
            },
            "product": {"products": []},
            "triggers": {"respond_to": [], "ignore": []},
            "conversation_flow": {"group_chat": {"steps": []}, "never": []},
        }
        self.router = MessageRouter(
            llm_client=self.llm,
            model="test",
            contract=self.contract,
        )
    
    def test_stop_command_triggers_disengage(self):
        """'Стоп' should trigger disengage."""
        result = self.router._prefilter("стоп")
        assert result is not None
        assert result.decision == Decision.DISENGAGE
    
    def test_harsh_warning_triggers_disengage(self):
        """Harsh warnings should trigger disengage."""
        result = self.router._prefilter("Заткнись уже, хватит тут рекламировать")
        assert result is not None
        assert result.decision == Decision.DISENGAGE


class TestLinkHandling:
    """Test handling of links and media."""
    
    def setup_method(self):
        self.llm = LLMClient(api_key="test-key")
        self.contract = {
            "persona": {
                "name": "Андрей",
                "backstory": "Кинолог",
                "speaking_style": {"tone": "expert", "patterns": [], "forbidden": []},
            },
            "product": {"products": []},
            "triggers": {"respond_to": [], "ignore": []},
            "conversation_flow": {"group_chat": {"steps": []}, "never": []},
        }
        self.router = MessageRouter(
            llm_client=self.llm,
            model="test",
            contract=self.contract,
        )
    
    def test_bare_link_ignored(self):
        """Just a link without text should be ignored."""
        result = self.router._prefilter("https://example.com")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_telegram_channel_link_ignored(self):
        """Telegram channel links should be ignored (spam)."""
        result = self.router._prefilter("https://t.me/somechannel")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_link_with_context_passes(self):
        """Link with context should pass prefilter."""
        result = self.router._prefilter("Посмотри вот этот корм, https://example.com он реально помогает")
        # Should need LLM for routing
        assert result is None


class TestSpamDetection:
    """Test advanced spam patterns."""
    
    def setup_method(self):
        self.llm = LLMClient(api_key="test-key")
        self.contract = {
            "persona": {"name": "Андрей", "backstory": "Кинолог", "speaking_style": {"tone": "expert", "patterns": [], "forbidden": []}},
            "product": {"products": []},
            "triggers": {"respond_to": [], "ignore": []},
            "conversation_flow": {"group_chat": {"steps": []}, "never": []},
        }
        self.router = MessageRouter(
            llm_client=self.llm,
            model="test",
            contract=self.contract,
        )
    
    def test_crypto_spam_ignored(self):
        """Crypto spam should be ignored."""
        result = self.router._prefilter("Заработай на крипто трейдинге, переходи по ссылке")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_subscribe_spam_ignored(self):
        """Subscribe spam should be ignored."""
        result = self.router._prefilter("Подпишись на канал и получи бонус")
        assert result is not None
        assert result.decision == Decision.IGNORE
    
    def test_phone_spam_ignored(self):
        """Phone number spam should be ignored."""
        result = self.router._prefilter("Звони +79161234567 для заказа")
        assert result is not None
        assert result.decision == Decision.IGNORE
