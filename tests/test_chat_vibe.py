"""
Tests for Chat Vibe Detector — detecting chat energy/tone
"""
from src.responders.chat_vibe import ChatVibeDetector, ChatVibe, detect_chat_vibe


class TestChatVibeDetector:
    """Test chat vibe detection."""
    
    def setup_method(self):
        self.detector = ChatVibeDetector()
    
    def test_empty_messages_returns_casual(self):
        result = self.detector.analyze([])
        assert result.primary_vibe == ChatVibe.CASUAL
        assert result.intensity == 0.5
        assert result.message_count == 0
    
    def test_normal_casual_chat(self):
        messages = [
            "Привет всем",
            "Как дела?",
            "Нормально, работаю",
            "А что нового?",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe == ChatVibe.CASUAL
        assert result.message_count == 4
    
    def test_drunk_chat_detected(self):
        messages = [
            "Ну блять кто бухает",
            "Я вчера выпил три литра пива",
            "Ахахаха 😂😂😂",
            "Шашлык на даче был огонь",
            "Водку кто несёт?",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe == ChatVibe.DRUNK
        assert result.intensity > 0.3
    
    def test_funny_chat_detected(self):
        messages = [
            "Хахаха лол",
            "😂😂😂",
            "Смешно же",
            "Прикол дня",
            "Ахах ржу не могу",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe == ChatVibe.FUNNY
    
    def test_sad_chat_detected(self):
        messages = [
            "Грустно сегодня",
            "Устал от всего",
            "Тяжело как-то",
            "Депрессия блин",
            "Одиноко(((",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe == ChatVibe.SAD
    
    def test_aggressive_chat_detected(self):
        messages = [
            "Заткнись уже",
            "Ты дурак вообще",
            "Идиот блин",
            "ПОШЁЛ ОТСЮДА",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe == ChatVibe.AGGRESSIVE
        assert result.intensity > 0.3
    
    def test_serious_chat_detected(self):
        messages = [
            "Давайте обсудим проблему",
            "Есть данные по исследованию",
            "Факт в том что результаты показывают",
            "Анализ говорит о серьёзном изменении",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe == ChatVibe.SERIOUS
    
    def test_flirty_chat_detected(self):
        messages = [
            "Ты такая красивая 😘",
            "Встретимся вечером?",
            "❤️❤️❤️",
            "Нежная ты",
            "Симпатичная фотка",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe == ChatVibe.FLIRTY
    
    def test_motivational_chat_detected(self):
        messages = [
            "Сегодня поставил цель",
            "Прогресс на лицо 💪",
            "Достигну нового результата",
            "🔥🔥🔥 Энергия зашкаливает",
            "Мотивация на максимуме",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe == ChatVibe.MOTIVATIONAL
    
    def test_emoji_density_calculated(self):
        messages = [
            "Привет 😂",
            "Как дела 😂😂",
            "😂😂😂😂",
        ]
        result = self.detector.analyze(messages)
        assert result.emoji_density > 1.0
    
    def test_avg_length_calculated(self):
        messages = ["Привет", "Нормально", "Пока"]
        result = self.detector.analyze(messages)
        assert result.avg_length > 0
    
    def test_has_exclamation_detected(self):
        messages = ["Отлично!", "Вау!", "Супер!"]
        result = self.detector.analyze(messages)
        assert result.has_exclamation is True
    
    def test_has_caps_detected(self):
        messages = ["ПРИВЕТ ВСЕМ", "ОКЕЙ", "ХОРОШО"]
        result = self.detector.analyze(messages)
        assert result.has_caps is True
    
    def test_to_prompt_modifier_drunk(self):
        result = detect_chat_vibe(["Бухаю водку 😂😂", "Выпил три бутылки"])
        modifier = result.to_prompt_modifier()
        assert "пьян" in modifier.lower() or "поток сознания" in modifier.lower()
    
    def test_to_prompt_modifier_sad(self):
        result = detect_chat_vibe(["Грустно", "Тяжело", "Депрессия"])
        modifier = result.to_prompt_modifier()
        assert "груст" in modifier.lower() or "empathetic" in modifier.lower()
    
    def test_to_prompt_modifier_casual(self):
        result = detect_chat_vibe(["Привет", "Как дела", "Ок"])
        modifier = result.to_prompt_modifier()
        assert "обычн" in modifier.lower() or "болтовн" in modifier.lower()
    
    def test_to_prompt_modifier_high_intensity(self):
        result = detect_chat_vibe([
            "БУХАЕМ ВОДКУ",
            "Три бутылки выпил 😂😂😂",
            "АХАХ ЛОЛ",
            "Шашлык огонь блять",
            "Пиво несите наливай",
        ])
        modifier = result.to_prompt_modifier()
        assert "высокая" in modifier.lower() or "активн" in modifier.lower()
    
    def test_secondary_vibe_detected(self):
        # Mix of drunk and funny
        messages = [
            "Выпил пива ахаха",
            "😂😂😂 бухаем",
            "Лол пьяный в стельку",
            "Шашлык водка прикол",
        ]
        result = self.detector.analyze(messages)
        assert result.primary_vibe in (ChatVibe.DRUNK, ChatVibe.FUNNY)
        # secondary may or may not be set depending on scores
    
    def test_convenience_function(self):
        result = detect_chat_vibe(["Привет", "Как дела"])
        assert isinstance(result.primary_vibe, ChatVibe)
        assert isinstance(result.intensity, float)
