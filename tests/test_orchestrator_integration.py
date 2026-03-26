"""
Tests for Orchestrator integration with new pipeline.

Tests the preprocess → route → generate → compose pipeline:
- PersonaRuntime has new components
- Preprocess integration
- Anaphora integration
"""

import importlib

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.core.orchestrator import (
    SalesBotOrchestrator,
    PersonaRuntime,
)
from src.core.persona_manager import (
    load_persona,
)
from src.responders.response_composer import ResponseComposer, GreetingPolicy
from src.responders.preprocess import PreprocessNode
from src.responders.anaphora_resolver import AnaphoraResolver


@pytest.fixture
def mock_memory_and_graph(monkeypatch):
    """Avoid real Supabase and Postgres checkpointer when building runtime."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:test@localhost:5432/testdb",
    )
    mem = AsyncMock()
    mem.close = AsyncMock()
    graph_mock = MagicMock()
    graph_mock.ainvoke = AsyncMock(
        return_value={
            "sent": False,
            "route_decision": "ignore",
            "node_history": [],
        }
    )
    graph_builder = importlib.import_module("src.graph.builder")
    with patch(
        "src.core.orchestrator.MemoryFacade.create",
        new_callable=AsyncMock,
        return_value=mem,
    ), patch.object(
        graph_builder,
        "compile_persona_graph",
        new_callable=AsyncMock,
        return_value=graph_mock,
    ):
        yield


# ══════════════════════════════════════════════════════════════
# PERSONA RUNTIME NEW COMPONENTS
# ══════════════════════════════════════════════════════════════

class TestPersonaRuntimeNewComponents:
    def test_runtime_has_composer_field(self):
        """PersonaRuntime should have composer field."""
        runtime = PersonaRuntime.__dataclass_fields__
        assert "composer" in runtime

    def test_runtime_has_preprocessor_field(self):
        """PersonaRuntime should have preprocessor field."""
        runtime = PersonaRuntime.__dataclass_fields__
        assert "preprocessor" in runtime

    def test_runtime_has_anaphora_field(self):
        """PersonaRuntime should have anaphora field."""
        runtime = PersonaRuntime.__dataclass_fields__
        assert "anaphora" in runtime

    def test_runtime_stats_has_new_fields(self):
        """Runtime stats should include preprocess and greeting counters."""
        # Check the default factory
        default_stats = {
            "messages_processed": 0,
            "responses_sent": 0,
            "ignored": 0,
            "errors": 0,
            "preprocess_shortcuts": 0,
            "greeting_skips": 0,
        }
        # The stats default_factory should include these
        field = PersonaRuntime.__dataclass_fields__["stats"]
        result = field.default_factory()
        for key, expected in default_stats.items():
            assert result[key] == expected


# ══════════════════════════════════════════════════════════════
# ORCHESTRATOR BUILD RUNTIME
# ══════════════════════════════════════════════════════════════

class TestOrchestratorBuildRuntime:
    @pytest.mark.asyncio
    async def test_build_runtime_creates_composer(self, mock_memory_and_graph):
        """_build_runtime should create ResponseComposer."""
        config = load_persona("personas/kormoved/persona.yaml")
        orchestrator = SalesBotOrchestrator(
            openrouter_api_key="test-key",
        )
        runtime = await orchestrator._build_runtime(config)

        assert runtime.composer is not None
        assert isinstance(runtime.composer, ResponseComposer)
        assert runtime.composer.persona_name == "Андрей"

    @pytest.mark.asyncio
    async def test_build_runtime_creates_preprocessor(self, mock_memory_and_graph):
        """_build_runtime should create PreprocessNode."""
        config = load_persona("personas/kormoved/persona.yaml")
        orchestrator = SalesBotOrchestrator(
            openrouter_api_key="test-key",
        )
        runtime = await orchestrator._build_runtime(config)

        assert runtime.preprocessor is not None
        assert isinstance(runtime.preprocessor, PreprocessNode)

    @pytest.mark.asyncio
    async def test_build_runtime_creates_anaphora(self, mock_memory_and_graph):
        """_build_runtime should create AnaphoraResolver."""
        config = load_persona("personas/kormoved/persona.yaml")
        orchestrator = SalesBotOrchestrator(
            openrouter_api_key="test-key",
        )
        runtime = await orchestrator._build_runtime(config)

        assert runtime.anaphora is not None
        assert isinstance(runtime.anaphora, AnaphoraResolver)

    @pytest.mark.asyncio
    async def test_build_runtime_greeting_policy_from_config(self, mock_memory_and_graph):
        """Greeting policy should come from persona config."""
        config = load_persona("personas/kormoved/persona.yaml")
        orchestrator = SalesBotOrchestrator(
            openrouter_api_key="test-key",
        )
        runtime = await orchestrator._build_runtime(config)

        policy = runtime.composer.greeting_policy
        assert policy.enabled
        assert policy.greet_only_first_response
        assert len(policy.greeting_variants) > 0


# ══════════════════════════════════════════════════════════════
# ORCHESTRATOR INTEGRATION
# ══════════════════════════════════════════════════════════════

class TestOrchestratorIntegration:
    @pytest.mark.asyncio
    async def test_all_personas_build_successfully(self, mock_memory_and_graph):
        """All 3 personas should build runtimes with new components."""
        orchestrator = SalesBotOrchestrator(
            openrouter_api_key="test-key",
        )
        configs = orchestrator.load_personas()

        for config in configs:
            runtime = await orchestrator._build_runtime(config)
            assert runtime.composer is not None, f"{config.name}: missing composer"
            assert runtime.preprocessor is not None, f"{config.name}: missing preprocessor"
            assert runtime.anaphora is not None, f"{config.name}: missing anaphora"


# ══════════════════════════════════════════════════════════════
# PIPELINE COMPONENTS WORK TOGETHER
# ══════════════════════════════════════════════════════════════

class TestPipelineComponents:
    def test_preprocess_uses_composer_greeting(self):
        """PreprocessNode should use ResponseComposer for greetings."""
        policy = GreetingPolicy(
            greeting_variants=["Привет! 🐾", "Здарова!"],
        )
        composer = ResponseComposer(
            persona_name="test",
            greeting_policy=policy,
        )
        preprocessor = PreprocessNode(
            composer=composer,
            followup_reuse_tools=["product_search"],
        )

        result = preprocessor.process(
            question="привет",
            last_context={},
            is_first_response=True,
            user_greeted=True,
            is_dm=False,
        )
        assert result.has_shortcut
        assert result.shortcut_response in ["Привет! 🐾", "Здарова!"]

    def test_anaphora_tracks_context(self):
        """AnaphoraResolver should track tool context for resolution."""
        resolver = AnaphoraResolver()
        resolver.update_context(
            "user1", "chat1",
            tool_name="product_search",
            tool_args={"query": "корм для хаски"},
            query="корм для хаски",
        )

        result = resolver.resolve("user1", "chat1", "подешевле")
        assert result.has_anaphora
        assert result.resolved_query == "корм для хаски"
        assert result.comparison_direction == "cheaper"

    def test_composer_handles_price_shock(self):
        """ResponseComposer should detect and handle price shock."""
        composer = ResponseComposer(persona_name="test")
        
        from src.responders.response_composer import CompositionContext
        ctx = CompositionContext(question="дорого!", persona_name="test")
        response = composer.handle_price_shock(ctx)
        
        assert response is not None
        assert len(response) > 20  # Substantive response

    def test_preprocess_skips_trivial(self):
        """Preprocess should skip trivial messages."""
        composer = ResponseComposer(persona_name="test")
        preprocessor = PreprocessNode(composer=composer)

        result = preprocessor.process(
            question=".",
            last_context={},
            is_first_response=True,
            user_greeted=False,
            is_dm=False,
        )
        assert result.skip_generation
