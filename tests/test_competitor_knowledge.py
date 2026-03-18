"""Tests for competitor knowledge and persona loading."""
import os
import pytest

from src.core.persona_manager import load_persona


class TestCompetitorKnowledge:
    """Test competitor knowledge loading and integration."""
    
    def test_competitor_knowledge_loaded(self):
        persona_yaml = os.path.join(
            os.path.dirname(__file__), "..", "personas", "kormoved", "persona.yaml"
        )
        if os.path.exists(persona_yaml):
            persona = load_persona(persona_yaml)
            assert persona.competitor_knowledge != ""
            assert "Royal Canin" in persona.competitor_knowledge
            assert "Hills" in persona.competitor_knowledge
    
    def test_competitor_knowledge_in_contract(self):
        """Competitor knowledge should be passed to the contract dict."""
        from src.core.orchestrator_v2 import SalesBotOrchestratorV2
        from src.core.persona_manager import PersonaConfig, AntiSpamConfig
        
        config = PersonaConfig(
            name="Test",
            personality="Test persona",
            competitor_knowledge="Royal Canin: Норм корм.",
            anti_spam=AntiSpamConfig(),
        )
        
        orchestrator = SalesBotOrchestratorV2(personas_dir="/tmp")
        contract = orchestrator._persona_to_contract(config)
        
        assert contract["persona"]["competitor_knowledge"] == "Royal Canin: Норм корм."
    
    def test_competitor_knowledge_in_style(self):
        """Competitor knowledge should appear in speaking style."""
        from src.responders.generator import ResponseGenerator
        from src.utils.llm_client import LLMClient
        
        contract = {
            "persona": {
                "name": "Test",
                "speaking_style": {"tone": "expert"},
                "competitor_knowledge": "Royal Canin: good but not for working dogs.",
            }
        }
        
        gen = ResponseGenerator(
            llm_client=LLMClient(api_key="fake"),
            model="fake",
            contract=contract,
        )
        
        style = gen._get_speaking_style()
        assert "Royal Canin" in style
