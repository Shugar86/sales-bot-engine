"""Embeddings Provider — Sentence Transformers with LRU Cache.

Provides text-to-vector encoding using deepvk/USER-bge-m3 model.
Features:
- LRU cache for frequently encoded texts
- Batch encoding support
- Configurable device (CPU/CUDA)
- Async-compatible API (sync operations, but safe for async context)
"""

import hashlib
import os
from typing import List, Optional


from ..utils.logger import get_logger

logger = get_logger("embeddings")


class EmbeddingProvider:
    """Provider for text embeddings using sentence-transformers.

    Model: deepvk/USER-bge-m3 (1024-dimensional vectors)
    Cache: LRU cache with configurable size

    Example:
        provider = EmbeddingProvider(cache_size=512)
        embedding = provider.encode("Какой корм выбрать для собаки?")
        # Returns: list of 1024 floats
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        cache_size: int = 512,
    ):
        """Initialize embedding provider.

        Args:
            model_name: HuggingFace model name (default: deepvk/USER-bge-m3)
            device: 'cpu', 'cuda', or None for auto
            cache_size: LRU cache size for embeddings
        """
        self.model_name = model_name or os.getenv(
            "EMBEDDING_MODEL", "deepvk/USER-bge-m3"
        )
        self.device = device or os.getenv("EMBEDDING_DEVICE", "cpu")
        self.cache_size = cache_size

        self._model = None
        self._cache: dict[str, list[float]] = {}
        self._cache_order: list[str] = []
        self._cache_lock = None  # Initialized on first use in async context

        logger.info(f"EmbeddingProvider: model={self.model_name}, device={self.device}, cache={cache_size}")

    def _load_model(self):
        """Lazy-load the sentence-transformers model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info(f"Loading embedding model: {self.model_name}")
                self._model = SentenceTransformer(self.model_name, device=self.device)
                logger.info(f"Model loaded successfully on {self.device}")
            except ImportError:
                logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
                raise
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                raise

    def _get_cache_key(self, text: str) -> str:
        """Generate cache key for text."""
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def _get_from_cache(self, key: str) -> Optional[list[float]]:
        """Get embedding from cache with LRU update."""
        if key in self._cache:
            # Move to end (most recently used)
            self._cache_order.remove(key)
            self._cache_order.append(key)
            return self._cache[key]
        return None

    def _add_to_cache(self, key: str, embedding: list[float]):
        """Add embedding to cache with LRU eviction."""
        if key in self._cache:
            # Update existing
            self._cache_order.remove(key)
        elif len(self._cache) >= self.cache_size:
            # Evict least recently used
            oldest = self._cache_order.pop(0)
            del self._cache[oldest]

        self._cache[key] = embedding
        self._cache_order.append(key)

    def encode(self, text: str) -> list[float]:
        """Encode single text to embedding vector.

        Args:
            text: Input text to encode

        Returns:
            List of 1024 floats
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for encoding")
            return [0.0] * 1024

        # Check cache
        cache_key = self._get_cache_key(text)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            logger.debug(f"Embedding cache hit: {text[:50]}...")
            return cached

        # Load model if needed
        self._load_model()

        # Encode
        try:
            embedding = self._model.encode(text, normalize_embeddings=True)
            result = embedding.tolist()

            # Cache result
            self._add_to_cache(cache_key, result)

            logger.debug(f"Encoded: {text[:50]}... -> {len(result)} dims")
            return result
        except Exception as e:
            logger.error(f"Encoding failed: {e}")
            # Return zero vector on error
            return [0.0] * 1024

    def encode_batch(self, texts: List[str]) -> List[list[float]]:
        """Encode multiple texts in batch (more efficient).

        Args:
            texts: List of input texts

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        # Filter out empty texts
        valid_texts = [(i, t) for i, t in enumerate(texts) if t and t.strip()]

        if not valid_texts:
            return [[0.0] * 1024 for _ in texts]

        # Check cache for each
        results = [None] * len(texts)
        to_encode = []
        to_encode_indices = []

        for orig_idx, text in valid_texts:
            cache_key = self._get_cache_key(text)
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                results[orig_idx] = cached
            else:
                to_encode.append(text)
                to_encode_indices.append((orig_idx, cache_key))

        # Encode missing
        if to_encode:
            self._load_model()
            try:
                embeddings = self._model.encode(
                    to_encode, normalize_embeddings=True, show_progress_bar=False
                )

                for (orig_idx, cache_key), embedding in zip(to_encode_indices, embeddings):
                    result = embedding.tolist()
                    results[orig_idx] = result
                    self._add_to_cache(cache_key, result)

                logger.debug(f"Batch encoded {len(to_encode)} texts")
            except Exception as e:
                logger.error(f"Batch encoding failed: {e}")
                for orig_idx, _ in to_encode_indices:
                    results[orig_idx] = [0.0] * 1024

        # Fill any remaining None with zero vectors
        for i, r in enumerate(results):
            if r is None:
                results[i] = [0.0] * 1024

        return results

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return {
            "cache_size": len(self._cache),
            "max_cache_size": self.cache_size,
            "model_name": self.model_name,
            "device": self.device,
        }

    def clear_cache(self):
        """Clear the embedding cache."""
        self._cache.clear()
        self._cache_order.clear()
        logger.info("Embedding cache cleared")


# Singleton instance for reuse across the application
_global_provider: Optional[EmbeddingProvider] = None


def create_embedding_provider(
    model_name: Optional[str] = None,
    device: Optional[str] = None,
    cache_size: int = 512,
) -> EmbeddingProvider:
    """Build a new :class:`EmbeddingProvider` for one persona (no global singleton).

    Each call returns a separate instance with its own LRU cache. The orchestrator
    should use this when constructing :class:`~src.memory.memory_facade.MemoryFacade`
    so personas do not share embedding caches.

    Args:
        model_name: HuggingFace model id; if omitted, uses ``EMBEDDING_MODEL`` or the
            provider default.
        device: ``cpu``, ``cuda``, or similar; if omitted, uses ``EMBEDDING_DEVICE``
            or ``cpu``.
        cache_size: Maximum number of encoded strings to keep in the LRU cache.

    Returns:
        A new ``EmbeddingProvider`` instance.
    """
    return EmbeddingProvider(
        model_name=model_name,
        device=device,
        cache_size=cache_size,
    )


def get_embedding_provider(
    model_name: Optional[str] = None,
    device: Optional[str] = None,
    cache_size: int = 512,
) -> EmbeddingProvider:
    """Get or create the global embedding provider instance.

    Args:
        model_name: Model name (uses env var or default if not set)
        device: Device to use (uses env var or 'cpu' if not set)
        cache_size: LRU cache size

    Returns:
        EmbeddingProvider instance
    """
    global _global_provider

    if _global_provider is None:
        _global_provider = create_embedding_provider(
            model_name=model_name,
            device=device,
            cache_size=cache_size,
        )

    return _global_provider


def reset_embedding_provider():
    """Reset the global embedding provider (for testing)."""
    global _global_provider
    _global_provider = None
    logger.info("Global embedding provider reset")
