import os
import time
import math
from typing import Optional, Callable, Any
from dataclasses import dataclass, field


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes the cosine similarity between two vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)


@dataclass
class SemanticCacheEntry:
    query: str
    embedding: list[float]
    response: str
    tier: str = "L1"
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    hit_count: int = 0
    metadata: dict = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at <= 0.0:
            return False
        return time.time() > self.expires_at


class SemanticCache:
    """
    In-memory / Firestore-backed Semantic Cache for IT Helpdesk questions.
    Caches semantically similar questions using vector cosine similarity to reduce Gemini token costs & latency (<50ms).
    """

    def __init__(
        self,
        similarity_threshold: float = 0.92,
        default_ttl_seconds: int = 86400,
        max_size: int = 1000,
        embedding_fn: Optional[Callable[[str], list[float]]] = None,
    ):
        self.similarity_threshold = float(
            os.getenv("SEMANTIC_CACHE_THRESHOLD", similarity_threshold)
        )
        self.default_ttl_seconds = int(
            os.getenv("SEMANTIC_CACHE_TTL_SECONDS", default_ttl_seconds)
        )
        self.max_size = max_size
        self._embedding_fn = embedding_fn
        self._entries: list[SemanticCacheEntry] = []
        self._total_lookups = 0
        self._total_hits = 0

    def _generate_embedding(self, text: str) -> list[float]:
        """Generates embedding for a query text."""
        if self._embedding_fn:
            return self._embedding_fn(text)

        # Use Vertex AI embedding only if explicitly enabled in production
        if os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1"):
            try:
                from vertexai.language_models import TextEmbeddingModel
                model = TextEmbeddingModel.from_pretrained("text-embedding-005")
                embeddings = model.get_embeddings([text])
                return embeddings[0].values
            except Exception as e:
                # Log and fallback to deterministic local embedding
                print(f"Notice: Vertex AI embedding unavailable ({e}), using local embedding.")

        # High-speed deterministic character n-gram & word frequency vector (dimension 128)
        # Guarantees zero network latency and consistent cosine similarity for similar phrases
        vec = [0.0] * 128
        cleaned = text.lower().strip()
        words = cleaned.split()
        for i, char in enumerate(cleaned):
            idx = (ord(char) * (i + 1) * 31) % 128
            vec[idx] += 1.0
        for w in words:
            idx = (sum(ord(c) for c in w) * 17) % 128
            vec[idx] += 2.0

        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def get(
        self, query: str, similarity_threshold: Optional[float] = None
    ) -> Optional[dict]:
        """
        Retrieves a cached response if a semantically similar query exists within the similarity threshold.
        Returns None if cache miss or expired.
        """
        if not os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes"):
            return None

        self._total_lookups += 1
        threshold = similarity_threshold or self.similarity_threshold
        query_emb = self._generate_embedding(query)

        # Evict expired entries
        self._entries = [e for e in self._entries if not e.is_expired()]

        best_match: Optional[SemanticCacheEntry] = None
        highest_sim = -1.0

        for entry in self._entries:
            sim = cosine_similarity(query_emb, entry.embedding)
            if sim > highest_sim:
                highest_sim = sim
                best_match = entry

        if best_match and highest_sim >= threshold:
            best_match.hit_count += 1
            self._total_hits += 1
            return {
                "status": "cache_hit",
                "cached_query": best_match.query,
                "response": best_match.response,
                "similarity": round(highest_sim, 4),
                "tier": best_match.tier,
                "hits": best_match.hit_count,
                "metadata": best_match.metadata,
            }

        return None

    def set(
        self,
        query: str,
        response: str,
        ttl_seconds: Optional[int] = None,
        tier: str = "L1",
        metadata: Optional[dict] = None,
    ) -> SemanticCacheEntry:
        """Stores a new query-response pair in the semantic cache."""
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        expires_at = time.time() + ttl if ttl > 0 else 0.0

        query_emb = self._generate_embedding(query)

        # Evict if max_size reached (remove entry with lowest hit count)
        if len(self._entries) >= self.max_size:
            self._entries.sort(key=lambda x: x.hit_count)
            self._entries.pop(0)

        entry = SemanticCacheEntry(
            query=query,
            embedding=query_emb,
            response=response,
            tier=tier,
            created_at=time.time(),
            expires_at=expires_at,
            hit_count=0,
            metadata=metadata or {},
        )
        self._entries.append(entry)
        return entry

    def clear(self) -> None:
        """Clears all cached entries."""
        self._entries.clear()
        self._total_lookups = 0
        self._total_hits = 0

    def get_stats(self) -> dict:
        """Returns statistics on semantic cache usage."""
        active_entries = [e for e in self._entries if not e.is_expired()]
        hit_rate = (
            round(self._total_hits / self._total_lookups * 100, 2)
            if self._total_lookups > 0
            else 0.0
        )
        return {
            "total_entries": len(active_entries),
            "total_lookups": self._total_lookups,
            "total_hits": self._total_hits,
            "hit_rate_percent": hit_rate,
            "similarity_threshold": self.similarity_threshold,
        }


# Global Singleton Semantic Cache
_GLOBAL_SEMANTIC_CACHE = SemanticCache()


def get_semantic_cache() -> SemanticCache:
    """Returns the global Semantic Cache singleton instance."""
    return _GLOBAL_SEMANTIC_CACHE
