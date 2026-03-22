"""
Tests for Persona YAML Upgrade (vibe, behavior, greeting_policy).

Tests the patterns ported from ai-tutor-engine:
- Vibe config parsing
- Behavior config parsing
- Greeting policy parsing
- All 3 personas have new format
"""

import pytest
import os
from src.core.persona_manager import (
    load_persona,
    discover_personas,
    PersonaConfig,
)
from src.core.vibe_schema import (
    VibePersona,
    VibeBehavior,
    GreetingPolicy,
)


# ══════════════════════════════════════════════════════════════
# VIBE CONFIG
# ══════════════════════════════════════════════════════════════

class TestVibePersona:
    def test_default_vibe(self):
        vibe = VibePersona(role="")
        assert vibe.role == ""
        assert vibe.voice == ""
        assert vibe.core_emotions == []
        assert vibe.values == []
        assert vibe.taboos == []

    def test_kormoved_vibe(self):
        persona = load_persona("personas/kormoved/persona.yaml")
        assert persona.vibe.role != ""
        assert persona.vibe.voice != ""
        assert len(persona.vibe.core_emotions) > 0
        assert len(persona.vibe.values) > 0
        assert len(persona.vibe.taboos) > 0

    def test_fitness_vibe(self):
        persona = load_persona("personas/fitness/persona.yaml")
        assert persona.vibe.role != ""
        assert persona.vibe.voice != ""
        assert len(persona.vibe.core_emotions) > 0

    def test_smm_vibe(self):
        persona = load_persona("personas/smm_blogger/persona.yaml")
        assert persona.vibe.role != ""
        assert persona.vibe.voice != ""
        assert len(persona.vibe.core_emotions) > 0


# ══════════════════════════════════════════════════════════════
# BEHAVIOR CONFIG
# ══════════════════════════════════════════════════════════════

class TestVibeBehavior:
    def test_default_behavior(self):
        behavior = VibeBehavior()
        assert behavior.on_greeting == ""
        assert behavior.on_tool_success == ""

    def test_kormoved_behavior(self):
        persona = load_persona("personas/kormoved/persona.yaml")
        assert persona.behavior.on_greeting != ""
        assert persona.behavior.on_tool_success != ""
        assert persona.behavior.on_price_query != ""
        assert persona.behavior.on_price_shock != ""
        assert persona.behavior.on_tool_no_results != ""
        assert persona.behavior.on_tool_error != ""
        assert persona.behavior.on_offtopic != ""

    def test_all_personas_have_behavior(self):
        for persona_file in [
            "personas/kormoved/persona.yaml",
            "personas/fitness/persona.yaml",
            "personas/smm_blogger/persona.yaml",
        ]:
            persona = load_persona(persona_file)
            assert persona.behavior.on_greeting != "", f"{persona.name} missing on_greeting"
            assert persona.behavior.on_tool_success != "", f"{persona.name} missing on_tool_success"
            assert persona.behavior.on_price_shock != "", f"{persona.name} missing on_price_shock"
            assert persona.behavior.on_offtopic != "", f"{persona.name} missing on_offtopic"


# ══════════════════════════════════════════════════════════════
# GREETING POLICY
# ══════════════════════════════════════════════════════════════

class TestGreetingPolicy:
    def test_default_policy(self):
        policy = GreetingPolicy()
        assert policy.enabled
        assert policy.greet_only_first_response
        assert policy.greet_only_if_user_greeted
        assert policy.strip_greeting_if_not_allowed
        assert len(policy.fallback_variants) > 0

    def test_kormoved_greeting_policy(self):
        persona = load_persona("personas/kormoved/persona.yaml")
        assert persona.greeting_policy.enabled
        assert persona.greeting_policy.greet_only_first_response
        assert persona.greeting_policy.greet_only_if_user_greeted
        assert len(persona.greeting_policy.greeting_variants) > 0

    def test_fitness_greeting_policy(self):
        persona = load_persona("personas/fitness/persona.yaml")
        assert persona.greeting_policy.enabled
        assert len(persona.greeting_policy.greeting_variants) > 0

    def test_smm_greeting_policy(self):
        persona = load_persona("personas/smm_blogger/persona.yaml")
        assert persona.greeting_policy.enabled
        assert len(persona.greeting_policy.greeting_variants) > 0


# ══════════════════════════════════════════════════════════════
# FULL PERSONA LOAD
# ══════════════════════════════════════════════════════════════

class TestFullPersonaLoad:
    def test_all_personas_discoverable(self):
        personas = discover_personas("personas")
        assert len(personas) == 3
        names = {p.name for p in personas}
        assert "Андрей" in names
        assert "Дима" in names
        assert "Лера" in names

    def test_kormoved_complete(self):
        persona = load_persona("personas/kormoved/persona.yaml")
        assert persona.name == "Андрей"
        assert persona.vibe.role != ""
        assert persona.behavior.on_greeting != ""
        assert persona.greeting_policy.enabled
        assert len(persona.response_examples) >= 10
        assert persona.competitor_knowledge != ""

    def test_fitness_complete(self):
        persona = load_persona("personas/fitness/persona.yaml")
        assert persona.name == "Дима"
        assert persona.vibe.role != ""
        assert persona.behavior.on_greeting != ""
        assert persona.greeting_policy.enabled
        assert len(persona.response_examples) >= 10

    def test_smm_complete(self):
        persona = load_persona("personas/smm_blogger/persona.yaml")
        assert persona.name == "Лера"
        assert persona.vibe.role != ""
        assert persona.behavior.on_greeting != ""
        assert persona.greeting_policy.enabled
        assert len(persona.response_examples) >= 10

    def test_taboos_not_empty(self):
        """Every persona should have taboos defined."""
        for persona_file in [
            "personas/kormoved/persona.yaml",
            "personas/fitness/persona.yaml",
            "personas/smm_blogger/persona.yaml",
        ]:
            persona = load_persona(persona_file)
            assert len(persona.vibe.taboos) > 0, f"{persona.name} has no taboos"
            # Common taboos should be present
            taboos_lower = [t.lower() for t in persona.vibe.taboos]
            assert any("токсич" in t for t in taboos_lower), f"{persona.name} missing toxicity taboo"
            assert any("выдум" in t or "факт" in t for t in taboos_lower), f"{persona.name} missing facts taboo"

    def test_greeting_variants_are_different(self):
        """Each persona should have multiple greeting variants."""
        for persona_file in [
            "personas/kormoved/persona.yaml",
            "personas/fitness/persona.yaml",
            "personas/smm_blogger/persona.yaml",
        ]:
            persona = load_persona(persona_file)
            variants = persona.greeting_policy.greeting_variants
            assert len(variants) >= 2, f"{persona.name} has only {len(variants)} greeting variants"
            # Variants should be different from each other
            assert len(set(variants)) == len(variants), f"{persona.name} has duplicate greeting variants"
