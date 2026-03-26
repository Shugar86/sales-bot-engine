"""Per-persona embedding factory returns distinct instances."""

from src.memory.embeddings import create_embedding_provider


def test_create_embedding_provider_distinct_objects() -> None:
    """Two calls with different model names must not share the same instance."""
    a = create_embedding_provider(model_name="model-a/model-x")
    b = create_embedding_provider(model_name="model-b/model-y")
    assert a is not b
    assert id(a) != id(b)
