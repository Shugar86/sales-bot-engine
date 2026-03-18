"""Tests for Context Manager — namespace-based context with TTL."""
import pytest
from src.core.context_manager import ContextManager
from src.core.vibe_schema import ContextPolicy


@pytest.fixture
def manager():
    return ContextManager(namespace="test_persona", keep_keys=["name", "stage"], ttl_turns=3)


class TestContextManagerBasic:
    def test_set_and_get(self, manager):
        manager.set("name", "Alice")
        assert manager.get("name") == "Alice"
    
    def test_get_default(self, manager):
        assert manager.get("unknown", "default") == "default"
    
    def test_delete(self, manager):
        manager.set("name", "Alice")
        manager.delete("name")
        assert manager.get("name") is None
    
    def test_update_batch(self, manager):
        manager.update({"name": "Alice", "stage": "interested"})
        assert manager.get("name") == "Alice"
        assert manager.get("stage") == "interested"
    
    def test_clear(self, manager):
        manager.set("name", "Alice")
        manager.clear()
        assert manager.get("name") is None


class TestContextManagerWhitelist:
    def test_allowed_key(self, manager):
        manager.set("name", "Alice")
        assert manager.get("name") == "Alice"
    
    def test_blocked_key(self, manager):
        manager.set("secret", "blocked")
        assert manager.get("secret") is None
    
    def test_no_whitelist(self):
        manager = ContextManager(namespace="test")
        manager.set("anything", "works")
        assert manager.get("anything") == "works"


class TestContextManagerTTL:
    def test_before_expiry(self, manager):
        manager.set("name", "Alice")
        for _ in range(3):
            manager.increment_turn()
        assert manager.get("name") == "Alice"  # ttl=3, turn diff = 3
    
    def test_after_expiry(self, manager):
        manager.set("name", "Alice")
        for _ in range(5):
            manager.increment_turn()
        assert manager.get("name") is None  # expired
    
    def test_cleanup_on_increment(self, manager):
        manager.set("name", "Alice")
        for _ in range(5):
            manager.increment_turn()
        assert manager.get_all() == {}


class TestContextManagerNamespace:
    def test_isolation(self):
        m1 = ContextManager(namespace="persona1")
        m2 = ContextManager(namespace="persona2")
        
        m1.set("key", "value1")
        # Different managers share no state
        assert m2.get("key") is None
    
    def test_get_all(self, manager):
        manager.set("name", "Alice")
        manager.set("stage", "interested")
        all_data = manager.get_all()
        assert all_data["name"] == "Alice"
        assert all_data["stage"] == "interested"


class TestContextManagerState:
    def test_state_context(self, manager):
        manager.set("name", "Alice")
        state = manager.get_state_context()
        assert "ns" in state
        assert "test_persona" in state["ns"]
    
    def test_load_state(self):
        manager = ContextManager(namespace="test")
        state = {"ns": {"test": {"name": {"value": "Bob", "timestamp": 0, "turn": 0}}}}
        manager.load_state_context(state)
        assert manager.get("name") == "Bob"
    
    def test_from_policy(self):
        policy = ContextPolicy(namespace="custom", keep_keys=["x"], ttl_turns=5)
        manager = ContextManager.from_policy("fallback", policy)
        assert manager.namespace == "custom"
        assert manager.ttl_turns == 5


class TestContextManagerEdgeCases:
    def test_empty_namespace(self):
        manager = ContextManager(namespace="")
        manager.set("key", "val")
        assert manager.get("key") == "val"
    
    def test_none_value(self, manager):
        manager.set("name", None)
        assert manager.get("name") is None
    
    def test_overwrite(self, manager):
        manager.set("name", "Alice")
        manager.set("name", "Bob")
        assert manager.get("name") == "Bob"
