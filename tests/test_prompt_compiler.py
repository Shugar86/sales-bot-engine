"""Tests for Prompt Compiler — dynamic prompt assembly."""
import pytest
from src.core.prompt_compiler import PromptCompiler
from src.core.vibe_schema import VibePersona, VibeBehavior, ResponseExample


@pytest.fixture
def full_vibe():
    return VibePersona(
        role="Консультант по кормлению собак",
        personality="35 лет, овчарка Барон",
        backstory="Бывший кинолог МВД",
        voice="Бывалый кинолог",
        core_emotions=["caring", "experienced"],
        values=["Здоровье собак"],
        taboos=["политика", "религия"],
    )


@pytest.fixture
def full_behavior():
    return VibeBehavior(
        on_greeting="Здравствуйте. Какая собака?",
        on_tool_success="Дай полезный ответ",
        on_tool_error="Что-то глючит",
        on_offtopic="Расскажи про собак",
        on_dm="Болтай как в группе",
        on_food_question="Делись опытом",
        on_bot_question="Нет, я Андрей",
        on_taboo="Не моя тема",
        on_disengage="Молчу",
        always="Ты живой человек в чате",
        routing_style="бывалый, спокойный",
    )


@pytest.fixture
def examples():
    return [
        ResponseExample(trigger="Дорого", bad_response="bad1", good_response="good1"),
        ResponseExample(trigger="Не ест", bad_response="bad2", good_response="good2"),
    ]


class TestPromptCompilerIdentity:
    """Tests for identity block."""
    
    def test_identity_block_present(self, full_vibe, full_behavior):
        compiler = PromptCompiler(vibe=full_vibe, behavior=full_behavior)
        prompt = compiler.compile_system_prompt()
        assert "=== IDENTITY ===" in prompt
        assert "Консультант по кормлению собак" in prompt
        assert "Бывший кинолог МВД" in prompt
        assert "caring" in prompt
    
    def test_no_vibe(self):
        compiler = PromptCompiler()
        prompt = compiler.compile_system_prompt()
        assert "=== IDENTITY ===" not in prompt
        assert "=== FACTUALITY ===" in prompt  # always present
    
    def test_taboos_in_identity(self, full_vibe):
        compiler = PromptCompiler(vibe=full_vibe)
        prompt = compiler.compile_system_prompt()
        assert "политика" in prompt
        assert "религия" in prompt


class TestPromptCompilerBehavior:
    """Tests for behavior block."""
    
    def test_behavior_block_present(self, full_behavior):
        compiler = PromptCompiler(behavior=full_behavior)
        prompt = compiler.compile_system_prompt(tool_status="success")
        assert "=== BEHAVIOR ===" in prompt
        assert "SUCCESS" in prompt
    
    def test_always_behavior(self, full_behavior):
        compiler = PromptCompiler(behavior=full_behavior)
        prompt = compiler.compile_system_prompt()
        assert "ALWAYS" in prompt
        assert "живой человек" in prompt
    
    def test_tool_status_selects_handler(self, full_behavior):
        compiler = PromptCompiler(behavior=full_behavior)
        
        success_prompt = compiler.compile_system_prompt(tool_status="success")
        assert "SUCCESS" in success_prompt
        
        error_prompt = compiler.compile_system_prompt(tool_status="error")
        assert "ERROR" in error_prompt
    
    def test_dm_handler(self, full_behavior):
        compiler = PromptCompiler(behavior=full_behavior)
        prompt = compiler.compile_system_prompt(tool_status="dm")
        assert "DM" in prompt
        assert "Болтай" in prompt
    
    def test_taboo_handler(self, full_behavior):
        compiler = PromptCompiler(behavior=full_behavior)
        prompt = compiler.compile_system_prompt(tool_status="taboo")
        assert "TABOO" in prompt
        assert "Не моя тема" in prompt


class TestPromptCompilerExamples:
    """Tests for examples block."""
    
    def test_examples_present(self, examples):
        compiler = PromptCompiler(response_examples=examples)
        prompt = compiler.compile_system_prompt()
        assert "=== RESPONSE EXAMPLES ===" in prompt
        assert "Дорого" in prompt
        assert "BAD:" in prompt
        assert "GOOD:" in prompt
    
    def test_no_examples(self):
        compiler = PromptCompiler()
        prompt = compiler.compile_system_prompt()
        assert "EXAMPLES" not in prompt
    
    def test_examples_limited_to_8(self):
        many = [ResponseExample(trigger=f"t{i}", bad_response=f"b{i}", good_response=f"g{i}") for i in range(20)]
        compiler = PromptCompiler(response_examples=many)
        prompt = compiler.compile_system_prompt()
        # Only first 8
        assert "t0" in prompt
        assert "t7" in prompt
        assert "t8" not in prompt


class TestPromptCompilerContext:
    """Tests for context block."""
    
    def test_user_context(self):
        compiler = PromptCompiler()
        prompt = compiler.compile_system_prompt(user_context="Alice: овчарка, 4 года")
        assert "USER MEMORY" in prompt
        assert "Alice" in prompt
    
    def test_chat_context(self):
        compiler = PromptCompiler()
        prompt = compiler.compile_system_prompt(chat_context="Последние: Привет / Как дела?")
        assert "CHAT CONTEXT" in prompt
    
    def test_no_context(self):
        compiler = PromptCompiler()
        prompt = compiler.compile_system_prompt()
        assert "CONTEXT" not in prompt or "FACTUALITY" in prompt


class TestPromptCompilerRouter:
    """Tests for router prompt."""
    
    def test_router_prompt_technical(self, full_vibe):
        compiler = PromptCompiler(vibe=full_vibe)
        prompt = compiler.compile_router_system_prompt()
        assert "technical routing system" in prompt
        assert "NO personality" in prompt
    
    def test_router_taboos(self, full_vibe):
        compiler = PromptCompiler(vibe=full_vibe)
        prompt = compiler.compile_router_system_prompt()
        assert "TABOO TOPICS" in prompt
        assert "политика" in prompt


class TestPromptCompilerFactuality:
    """Tests for factuality block."""
    
    def test_factuality_always_present(self):
        compiler = PromptCompiler()
        prompt = compiler.compile_system_prompt()
        assert "FACTUALITY" in prompt
        assert "Never invent" in prompt
    
    def test_format_block(self):
        compiler = PromptCompiler()
        prompt = compiler.compile_system_prompt()
        assert "FORMAT" in prompt
        assert "Russian" in prompt


class TestPromptCompilerCompetitor:
    """Tests for competitor knowledge."""
    
    def test_competitor_present(self):
        compiler = PromptCompiler(competitor_knowledge="Royal Canin: норм корм")
        prompt = compiler.compile_system_prompt()
        assert "COMPETITOR KNOWLEDGE" in prompt
        assert "Royal Canin" in prompt
    
    def test_no_competitor(self):
        compiler = PromptCompiler()
        prompt = compiler.compile_system_prompt()
        assert "COMPETITOR" not in prompt


class TestPromptCompilerPersonality:
    """Tests for legacy personality field."""
    
    def test_personality_block(self):
        compiler = PromptCompiler(personality="Ты парень из Ростова")
        prompt = compiler.compile_system_prompt()
        assert "PERSONALITY" in prompt
        assert "Ростова" in prompt
