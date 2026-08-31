import os
import time
import math
import json
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Optional, Callable, Any, Dict, List
from dataclasses import dataclass, field

logger = logging.getLogger("it_helpdesk_agent")


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
    user_id: Optional[str] = None
    is_public: bool = False
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    hit_count: int = 0
    metadata: dict = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at <= 0.0:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "embedding": self.embedding,
            "response": self.response,
            "tier": self.tier,
            "user_id": self.user_id,
            "is_public": self.is_public,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "hit_count": self.hit_count,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticCacheEntry":
        return cls(
            query=d.get("query", ""),
            embedding=d.get("embedding", []),
            response=d.get("response", ""),
            tier=d.get("tier", "L1"),
            user_id=d.get("user_id"),
            is_public=bool(d.get("is_public", False)),
            created_at=float(d.get("created_at", time.time())),
            expires_at=float(d.get("expires_at", 0.0)),
            hit_count=int(d.get("hit_count", 0)),
            metadata=d.get("metadata", {}),
        )


class BaseSemanticCache(ABC):
    """Abstract interface for Semantic Cache implementations."""

    @abstractmethod
    def get(
        self,
        query: str,
        user_id: Optional[str] = None,
        similarity_threshold: Optional[float] = None
    ) -> Optional[dict]:
        pass

    @abstractmethod
    def set(
        self,
        query: str,
        response: str,
        user_id: Optional[str] = None,
        is_public: bool = False,
        ttl_seconds: Optional[int] = None,
        tier: str = "L1",
        metadata: Optional[dict] = None,
    ) -> Any:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass

    @abstractmethod
    def get_stats(self) -> dict:
        pass


class InMemorySemanticCache(BaseSemanticCache):
    """
    In-memory Semantic Cache for local development and unit tests.
    Uses vector cosine similarity to reduce Gemini token costs & latency (<50ms).
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

        if os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1"):
            try:
                from vertexai.language_models import TextEmbeddingModel
                model = TextEmbeddingModel.from_pretrained("text-embedding-005")
                embeddings = model.get_embeddings([text])
                return embeddings[0].values
            except Exception as e:
                logger.debug("Vertex AI embedding unavailable (%s), using local embedding.", e)

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
        self,
        query: str,
        user_id: Optional[str] = None,
        similarity_threshold: Optional[float] = None
    ) -> Optional[dict]:
        if not os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes"):
            return None

        self._total_lookups += 1
        threshold = similarity_threshold or self.similarity_threshold
        query_emb = self._generate_embedding(query)

        self._entries = [e for e in self._entries if not e.is_expired()]

        best_match: Optional[SemanticCacheEntry] = None
        highest_sim = -1.0

        for entry in self._entries:
            can_access = entry.is_public or (user_id is not None and entry.user_id == user_id)
            if not can_access:
                continue

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
                "is_public": best_match.is_public,
                "metadata": best_match.metadata,
            }

        return None

    def set(
        self,
        query: str,
        response: str,
        user_id: Optional[str] = None,
        is_public: bool = False,
        ttl_seconds: Optional[int] = None,
        tier: str = "L1",
        metadata: Optional[dict] = None,
    ) -> SemanticCacheEntry:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        expires_at = time.time() + ttl if ttl > 0 else 0.0

        query_emb = self._generate_embedding(query)

        if len(self._entries) >= self.max_size:
            self._entries.sort(key=lambda x: x.hit_count)
            self._entries.pop(0)

        entry = SemanticCacheEntry(
            query=query,
            embedding=query_emb,
            response=response,
            tier=tier,
            user_id=user_id,
            is_public=is_public,
            created_at=time.time(),
            expires_at=expires_at,
            hit_count=0,
            metadata=metadata or {},
        )
        self._entries.append(entry)
        return entry

    def clear(self) -> None:
        self._entries.clear()
        self._total_lookups = 0
        self._total_hits = 0

    def get_stats(self) -> dict:
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
            "backend": "in_memory"
        }


# Alias for backward compatibility
SemanticCache = InMemorySemanticCache


class RedisSemanticCache(BaseSemanticCache):
    """
    Cluster-wide Semantic Cache using Redis / Memorystore.
    Shares cached queries & responses across all Cloud Run instances.
    
    Soft Fail-Closed Architecture: On Redis failure or timeout, logs WARNING
    and returns None (Cache Miss) so downstream agent calls continue normally.
    """

    def __init__(
        self,
        redis_client=None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: int = 0,
        similarity_threshold: float = 0.92,
        default_ttl_seconds: int = 86400,
        socket_timeout: float = 2.0,
        embedding_fn: Optional[Callable[[str], list[float]]] = None,
    ):
        self.similarity_threshold = float(
            os.getenv("SEMANTIC_CACHE_THRESHOLD", similarity_threshold)
        )
        self.default_ttl_seconds = int(
            os.getenv("SEMANTIC_CACHE_TTL_SECONDS", default_ttl_seconds)
        )
        self._embedding_fn = embedding_fn
        self._redis = redis_client
        self._host = host or os.getenv("REDIS_HOST", "localhost")
        self._port = int(port or os.getenv("REDIS_PORT", "6379"))
        self._db = int(os.getenv("REDIS_DB", str(db)))
        self._socket_timeout = socket_timeout

        self._local_lookups = 0
        self._local_hits = 0

        if self._redis is None:
            self._init_redis()

    def _init_redis(self) -> None:
        try:
            import redis
            self._redis = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                socket_connect_timeout=self._socket_timeout,
                socket_timeout=self._socket_timeout,
                decode_responses=True,
            )
            self._redis.ping()
            logger.info("Connected to Redis Semantic Cache at %s:%s (db=%d)", self._host, self._port, self._db)
        except Exception as e:
            logger.warning("Failed to connect to Redis Semantic Cache (%s:%s): %s. Operating in Soft Fail-Closed mode.", self._host, self._port, e)
            self._redis = None

    def _generate_embedding(self, text: str) -> list[float]:
        if self._embedding_fn:
            return self._embedding_fn(text)

        if os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1"):
            try:
                from vertexai.language_models import TextEmbeddingModel
                model = TextEmbeddingModel.from_pretrained("text-embedding-005")
                embeddings = model.get_embeddings([text])
                return embeddings[0].values
            except Exception as e:
                logger.debug("Vertex AI embedding unavailable (%s), using local embedding.", e)

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

    def _get_entry_id(self, query: str, user_id: Optional[str], is_public: bool) -> str:
        scope = "public" if is_public else f"user_{user_id or 'anon'}"
        raw = f"{scope}:{query.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def get(
        self,
        query: str,
        user_id: Optional[str] = None,
        similarity_threshold: Optional[float] = None
    ) -> Optional[dict]:
        """
        Retrieves cached response from Redis with vector cosine similarity.
        Soft Fail-Closed: Returns None (Cache Miss) on Redis errors.
        """
        if not os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes"):
            return None

        self._local_lookups += 1
        threshold = similarity_threshold or self.similarity_threshold

        if self._redis is None:
            self._init_redis()
            if self._redis is None:
                return None

        try:
            # Multi-tenant candidate keys: read public candidates and user-specific candidates
            candidate_entry_ids = set()
            public_ids = self._redis.smembers("sem_cache:keys:public") or set()
            candidate_entry_ids.update(public_ids)

            if user_id:
                user_ids = self._redis.smembers(f"sem_cache:keys:user:{user_id}") or set()
                candidate_entry_ids.update(user_ids)

            if not candidate_entry_ids:
                return None

            # Fetch candidate entries in one batch
            entry_keys = [f"sem_cache:entry:{eid}" for eid in candidate_entry_ids]
            raw_entries = self._redis.mget(entry_keys)

            query_emb = self._generate_embedding(query)
            best_match: Optional[SemanticCacheEntry] = None
            best_entry_id: Optional[str] = None
            highest_sim = -1.0
            expired_ids = []

            for eid, raw_json in zip(candidate_entry_ids, raw_entries):
                if not raw_json:
                    expired_ids.append(eid)
                    continue
                try:
                    data = json.loads(raw_json)
                    entry = SemanticCacheEntry.from_dict(data)
                    if entry.is_expired():
                        expired_ids.append(eid)
                        continue

                    can_access = entry.is_public or (user_id is not None and entry.user_id == user_id)
                    if not can_access:
                        continue

                    sim = cosine_similarity(query_emb, entry.embedding)
                    if sim > highest_sim:
                        highest_sim = sim
                        best_match = entry
                        best_entry_id = eid
                except Exception:
                    expired_ids.append(eid)

            # Lazy cleanup of expired keys in sets
            if expired_ids:
                try:
                    pipe = self._redis.pipeline()
                    pipe.srem("sem_cache:keys:public", *expired_ids)
                    if user_id:
                        pipe.srem(f"sem_cache:keys:user:{user_id}", *expired_ids)
                    pipe.execute()
                except Exception:
                    pass

            if best_match and highest_sim >= threshold and best_entry_id:
                best_match.hit_count += 1
                self._local_hits += 1

                # Update hit count asynchronously in Redis
                try:
                    pipe = self._redis.pipeline()
                    pipe.set(
                        f"sem_cache:entry:{best_entry_id}",
                        json.dumps(best_match.to_dict()),
                        keepttl=True
                    )
                    pipe.execute()
                except Exception:
                    pass

                return {
                    "status": "cache_hit",
                    "cached_query": best_match.query,
                    "response": best_match.response,
                    "similarity": round(highest_sim, 4),
                    "tier": best_match.tier,
                    "hits": best_match.hit_count,
                    "is_public": best_match.is_public,
                    "metadata": best_match.metadata,
                }

            return None

        except Exception as e:
            logger.warning("RedisSemanticCache get error: %s. Soft Fail-Closed (Cache Miss).", e)
            return None

    def set(
        self,
        query: str,
        response: str,
        user_id: Optional[str] = None,
        is_public: bool = False,
        ttl_seconds: Optional[int] = None,
        tier: str = "L1",
        metadata: Optional[dict] = None,
    ) -> Optional[SemanticCacheEntry]:
        """
        Persists query, embedding, and response into Redis with TTL.
        Soft Fail-Closed: Silently logs warning on error without interrupting flow.
        """
        if self._redis is None:
            self._init_redis()
            if self._redis is None:
                return None

        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        expires_at = time.time() + ttl if ttl > 0 else 0.0
        query_emb = self._generate_embedding(query)

        entry = SemanticCacheEntry(
            query=query,
            embedding=query_emb,
            response=response,
            tier=tier,
            user_id=user_id,
            is_public=is_public,
            created_at=time.time(),
            expires_at=expires_at,
            hit_count=0,
            metadata=metadata or {},
        )

        entry_id = self._get_entry_id(query, user_id, is_public)

        try:
            pipe = self._redis.pipeline()
            entry_key = f"sem_cache:entry:{entry_id}"
            serialized = json.dumps(entry.to_dict())
            if ttl > 0:
                pipe.set(entry_key, serialized, ex=ttl)
            else:
                pipe.set(entry_key, serialized)

            if is_public:
                pipe.sadd("sem_cache:keys:public", entry_id)
            elif user_id:
                pipe.sadd(f"sem_cache:keys:user:{user_id}", entry_id)

            pipe.execute()
            return entry
        except Exception as e:
            logger.warning("RedisSemanticCache set error for entry %s: %s (Soft Fail-Closed).", entry_id, e)
            return None

    def clear(self) -> None:
        """Clears all semantic cache entries from Redis."""
        if self._redis is None:
            return
        try:
            public_keys = list(self._redis.smembers("sem_cache:keys:public") or [])
            all_entry_keys = [f"sem_cache:entry:{eid}" for eid in public_keys]
            
            # Find user sets
            user_sets = list(self._redis.keys("sem_cache:keys:user:*") or [])
            for u_set in user_sets:
                u_keys = list(self._redis.smembers(u_set) or [])
                all_entry_keys.extend([f"sem_cache:entry:{eid}" for eid in u_keys])

            if all_entry_keys or user_sets or public_keys:
                pipe = self._redis.pipeline()
                if all_entry_keys:
                    pipe.delete(*all_entry_keys)
                if user_sets:
                    pipe.delete(*user_sets)
                pipe.delete("sem_cache:keys:public")
                pipe.execute()

            self._local_lookups = 0
            self._local_hits = 0
        except Exception as e:
            logger.warning("Failed to clear RedisSemanticCache: %s", e)

    def get_stats(self) -> dict:
        """Returns statistics on semantic cache usage."""
        total_entries = 0
        if self._redis is not None:
            try:
                public_count = self._redis.scard("sem_cache:keys:public") or 0
                total_entries += public_count
                for u_set in self._redis.keys("sem_cache:keys:user:*") or []:
                    total_entries += (self._redis.scard(u_set) or 0)
            except Exception:
                pass

        hit_rate = (
            round(self._local_hits / self._local_lookups * 100, 2)
            if self._local_lookups > 0
            else 0.0
        )
        return {
            "total_entries": total_entries,
            "total_lookups": self._local_lookups,
            "total_hits": self._local_hits,
            "hit_rate_percent": hit_rate,
            "similarity_threshold": self.similarity_threshold,
            "backend": "redis"
        }


# Global Singleton Semantic Cache
_GLOBAL_SEMANTIC_CACHE: Optional[BaseSemanticCache] = None


def get_semantic_cache() -> BaseSemanticCache:
    """Returns the global Semantic Cache singleton instance."""
    global _GLOBAL_SEMANTIC_CACHE
    if _GLOBAL_SEMANTIC_CACHE is None:
        backend = os.getenv("SEMANTIC_CACHE_BACKEND", "memory").lower()
        if backend == "redis" or (backend == "auto" and os.getenv("REDIS_HOST")):
            _GLOBAL_SEMANTIC_CACHE = RedisSemanticCache()
        else:
            _GLOBAL_SEMANTIC_CACHE = InMemorySemanticCache()
    return _GLOBAL_SEMANTIC_CACHE


def reset_semantic_cache() -> None:
    """Reset singleton instance for unit tests."""
    global _GLOBAL_SEMANTIC_CACHE
    _GLOBAL_SEMANTIC_CACHE = None
