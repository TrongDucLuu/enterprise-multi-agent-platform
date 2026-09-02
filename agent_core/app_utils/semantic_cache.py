import os
import re
import time
import math
import json
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Optional, Callable, Any, Dict, List
from dataclasses import dataclass, field

logger = logging.getLogger("agent_core")


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


DEFAULT_TIER_THRESHOLDS = {
    "L1": 0.90,
    "L2": 0.92,
    "L3": 0.98,
}


class BaseSemanticCache(ABC):
    """Abstract interface for Semantic Cache implementations."""

    def __init__(self, similarity_threshold: float = 0.92):
        self.similarity_threshold = float(
            os.getenv("SEMANTIC_CACHE_THRESHOLD", similarity_threshold)
        )

    def get_tier_threshold(self, tier: Optional[str] = None) -> float:
        """
        Returns the risk-weighted similarity threshold for a specific operational tier.
        - L1 (Self-service FAQs / common issues): threshold ~0.90 (Low risk)
        - L2 (Enterprise RAG SOPs / Manuals): threshold ~0.92 (Medium risk)
        - L3 (Deep Diagnostics / RCA / Legal / SLA): threshold ~0.98 (High risk - requires near-exact text match)
        """
        if not tier:
            return self.similarity_threshold
        tier_upper = tier.upper().strip()
        if "L3" in tier_upper:
            env_val = os.getenv("SEMANTIC_CACHE_THRESHOLD_L3")
            return float(env_val) if env_val else DEFAULT_TIER_THRESHOLDS["L3"]
        elif "L2" in tier_upper:
            env_val = os.getenv("SEMANTIC_CACHE_THRESHOLD_L2")
            return float(env_val) if env_val else DEFAULT_TIER_THRESHOLDS["L2"]
        elif "L1" in tier_upper:
            env_val = os.getenv("SEMANTIC_CACHE_THRESHOLD_L1")
            return float(env_val) if env_val else DEFAULT_TIER_THRESHOLDS["L1"]
        return self.similarity_threshold

    @abstractmethod
    def get(
        self,
        query: str,
        user_id: Optional[str] = None,
        similarity_threshold: Optional[float] = None,
        tier: Optional[str] = None,
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
    def invalidate(
        self,
        article_id: Optional[str] = None,
        system: Optional[str] = None
    ) -> int:
        pass

    @abstractmethod
    def get_stats(self) -> dict:
        pass


from agent_core.app_utils.env import is_production_mode


class InMemorySemanticCache(BaseSemanticCache):
    """
    In-memory Semantic Cache for local development and unit tests.
    Uses vector cosine similarity to reduce Gemini token costs & latency (<50ms).
    """

    def __init__(
        self,
        similarity_threshold: float = 0.92,
        default_ttl_seconds: int = 86400,
        default_public_ttl_seconds: int = 14400,
        max_size: int = 1000,
        embedding_fn: Optional[Callable[[str], list[float]]] = None,
    ):
        super().__init__(similarity_threshold=similarity_threshold)
        self.default_ttl_seconds = int(
            os.getenv("SEMANTIC_CACHE_TTL_SECONDS", default_ttl_seconds)
        )
        self.default_public_ttl_seconds = int(
            os.getenv("SEMANTIC_CACHE_PUBLIC_TTL_SECONDS", default_public_ttl_seconds)
        )
        self.max_size = max_size
        self._embedding_fn = embedding_fn
        self._entries: list[SemanticCacheEntry] = []
        self._total_lookups = 0
        self._total_hits = 0

    def _generate_embedding(self, text: str) -> Optional[list[float]]:
        """
        Generates embedding for a query text.
        
        Fail-Closed in Production:
        If running in production (ENVIRONMENT=production, K_SERVICE set), real Vertex AI
        embeddings are strictly required. If USE_VERTEX_EMBEDDING is not enabled or Vertex AI
        fails, returns None so that the semantic cache is safely bypassed rather than producing
        inaccurate pseudo-vector matches.
        
        In local dev/test environments, falls back to ASCII character-hash vectors.
        """
        if self._embedding_fn:
            return self._embedding_fn(text)

        use_vertex = os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1", "yes")
        in_prod = is_production_mode()

        if use_vertex:
            try:
                from vertexai.language_models import TextEmbeddingModel
                model = TextEmbeddingModel.from_pretrained("text-embedding-005")
                embeddings = model.get_embeddings([text])
                return embeddings[0].values
            except Exception as e:
                if in_prod:
                    logger.error("Fail-Closed: Vertex AI embedding error in production (%s). Bypassing semantic cache.", e)
                    return None
                logger.debug("Vertex AI embedding unavailable (%s), using local embedding.", e)

        if in_prod:
            logger.warning("Fail-Closed: Semantic cache requires real Vertex AI embeddings in production (USE_VERTEX_EMBEDDING=true). Bypassing cache.")
            return None

        # Local development / test ASCII pseudo-vector fallback
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
        similarity_threshold: Optional[float] = None,
        tier: Optional[str] = None,
    ) -> Optional[dict]:
        if not os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes"):
            return None

        query_emb = self._generate_embedding(query)
        if query_emb is None:
            return None

        self._total_lookups += 1
        threshold = similarity_threshold or self.get_tier_threshold(tier)

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
    ) -> Optional[SemanticCacheEntry]:
        query_emb = self._generate_embedding(query)
        if query_emb is None:
            logger.debug("Skipping semantic cache set: embedding is None (Fail-Closed mode).")
            return None

        if ttl_seconds is not None:
            ttl = ttl_seconds
        elif is_public:
            ttl = self.default_public_ttl_seconds
        else:
            ttl = self.default_ttl_seconds
        expires_at = time.time() + ttl if ttl > 0 else 0.0

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

    def invalidate(
        self,
        article_id: Optional[str] = None,
        system: Optional[str] = None
    ) -> int:
        """
        Scans and invalidates cached entries matching article_id or system.
        """
        initial_len = len(self._entries)
        aid_upper = article_id.strip().upper() if article_id else None
        sys_upper = system.strip().upper() if system else None

        def should_retain(entry: SemanticCacheEntry) -> bool:
            meta = entry.metadata or {}
            meta_aid = (meta.get("article_id") or "").upper()
            meta_sys = (meta.get("system") or "").upper()
            match_aid = aid_upper and (aid_upper in meta_aid or aid_upper in entry.response.upper())
            match_sys = sys_upper and (sys_upper == meta_sys)
            if match_aid or match_sys:
                return False
            return True

        self._entries = [e for e in self._entries if should_retain(e)]
        invalidated_count = initial_len - len(self._entries)
        if invalidated_count > 0:
            logger.info("InMemorySemanticCache invalidated %d entries for (article_id=%s, system=%s)", invalidated_count, article_id, system)
        return invalidated_count

    def get_stats(self) -> dict:
        hit_rate = (
            round(self._total_hits / self._total_lookups * 100, 2)
            if self._total_lookups > 0
            else 0.0
        )
        return {
            "total_entries": len(self._entries),
            "total_lookups": self._total_lookups,
            "total_hits": self._total_hits,
            "hit_rate_percent": hit_rate,
            "similarity_threshold": self.similarity_threshold,
            "backend": "memory",
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
        default_public_ttl_seconds: int = 14400,
        socket_timeout: float = 2.0,
        cooldown_seconds: float = 30.0,
        embedding_fn: Optional[Callable[[str], list[float]]] = None,
        kb_version: Optional[str] = None,
    ):
        super().__init__(similarity_threshold=similarity_threshold)
        self.default_ttl_seconds = int(
            os.getenv("SEMANTIC_CACHE_TTL_SECONDS", default_ttl_seconds)
        )
        self.default_public_ttl_seconds = int(
            os.getenv("SEMANTIC_CACHE_PUBLIC_TTL_SECONDS", default_public_ttl_seconds)
        )
        self.kb_version = kb_version or os.getenv("KB_VERSION", "1")
        self._embedding_fn = embedding_fn
        self._redis = redis_client
        self._host = host or os.getenv("REDIS_HOST", "localhost")
        self._port = int(port or os.getenv("REDIS_PORT", "6379"))
        self._db = int(os.getenv("REDIS_DB", str(db)))
        self._socket_timeout = socket_timeout

        self._local_lookups = 0
        self._local_hits = 0

        # Circuit Breaker, State Machine & Cooldown Recovery Tracker
        self._consecutive_redis_failures = 0
        self._circuit_breaker_tripped = False
        self._failure_threshold = 10
        self._cooldown_seconds = float(
            os.getenv("REDIS_CIRCUIT_COOLDOWN_SECONDS", str(cooldown_seconds))
        )
        self._last_failure_time = 0.0

        if self._redis is None:
            self._init_redis()

    @property
    def public_keys_set(self) -> str:
        return f"sem_cache:v{self.kb_version}:keys:public"

    def user_keys_set(self, user_id: str) -> str:
        return f"sem_cache:v{self.kb_version}:keys:user:{user_id}"

    def entry_key(self, entry_id: str) -> str:
        return f"sem_cache:v{self.kb_version}:entry:{entry_id}"

    def user_keys_pattern(self) -> str:
        return f"sem_cache:v{self.kb_version}:keys:user:*"

    def _allow_request(self) -> bool:
        """
        Determines whether a Redis operation should be attempted.
        Implements Circuit Breaker State Machine:
        - CLOSED: Normal operation.
        - OPEN: Cooldown period active, bypass Redis immediately.
        - HALF_OPEN: Cooldown elapsed, permits a single probe request to test Redis recovery.
        """
        if not self._circuit_breaker_tripped:
            return True

        now = time.time()
        if (now - self._last_failure_time) >= self._cooldown_seconds:
            logger.info(
                "RedisSemanticCache cooldown (%.1fs) elapsed. Transitioning circuit breaker to HALF_OPEN to probe Redis.",
                self._cooldown_seconds
            )
            return True
        return False

    def _record_redis_failure(self, error: Exception, context: str) -> None:
        """Records a Redis failure, trips circuit breaker, and updates last failure timestamp."""
        self._consecutive_redis_failures += 1
        self._last_failure_time = time.time()
        if self._consecutive_redis_failures >= self._failure_threshold:
            if not self._circuit_breaker_tripped:
                self._circuit_breaker_tripped = True
                logger.critical(
                    "REDIS_CIRCUIT_BREAKER_ALERT: Redis semantic cache failed %d consecutive times! "
                    "Last error in %s: %s. Circuit breaker TRIPPED (OPEN). Bypassing Redis cache for %.1fs.",
                    self._consecutive_redis_failures,
                    context,
                    error,
                    self._cooldown_seconds,
                )
            else:
                logger.warning("RedisSemanticCache circuit breaker remains OPEN. Error in %s: %s", context, error)
        else:
            logger.warning("RedisSemanticCache %s operation failed (consecutive: %d): %s. Soft Fail-Closed active.", context, self._consecutive_redis_failures, error)

    def _record_redis_success(self) -> None:
        """Resets consecutive failures upon any successful Redis communication."""
        if self._consecutive_redis_failures > 0 or self._circuit_breaker_tripped:
            logger.info("RedisSemanticCache connection healthy. Resetting circuit breaker to CLOSED.")
        self._consecutive_redis_failures = 0
        self._circuit_breaker_tripped = False

    def _init_redis(self) -> None:
        """Initializes redis connection pool safely."""
        if not self._allow_request():
            return
        try:
            import redis
            self._redis = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                socket_timeout=self._socket_timeout,
                decode_responses=True,
            )
            self._redis.ping()
            self._record_redis_success()
            logger.info("RedisSemanticCache connected to %s:%d/%d", self._host, self._port, self._db)
        except Exception as e:
            self._record_redis_failure(e, "init")
            self._redis = None

    def _generate_embedding(self, text: str) -> Optional[list[float]]:
        """
        Generates embedding vector for caching and similarity search.
        Fail-Closed in Production: Returns None if Vertex AI is not configured or fails.
        """
        if self._embedding_fn:
            return self._embedding_fn(text)

        use_vertex = os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1", "yes")
        in_prod = is_production_mode()

        if use_vertex:
            try:
                from vertexai.language_models import TextEmbeddingModel
                model = TextEmbeddingModel.from_pretrained("text-embedding-005")
                embeddings = model.get_embeddings([text])
                return embeddings[0].values
            except Exception as e:
                if in_prod:
                    logger.error("Fail-Closed: Vertex AI embedding error in production (%s). Bypassing semantic cache.", e)
                    return None
                logger.debug("Vertex AI embedding unavailable (%s), using local embedding.", e)

        if in_prod:
            logger.warning("Fail-Closed: Redis Semantic cache requires real Vertex AI embeddings in production (USE_VERTEX_EMBEDDING=true). Bypassing cache.")
            return None

        # Local development / test ASCII pseudo-vector fallback
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

    def _get_entry_id(self, query: str, user_id: Optional[str] = None, is_public: bool = False) -> str:
        """Deterministic ID for key lookup in Redis."""
        scope = "public" if is_public else f"user_{user_id or 'anon'}"
        cleaned = re.sub(r"[^\w\s]", "", query.lower()).strip()
        h = hashlib.sha256(f"{scope}:{cleaned}".encode("utf-8")).hexdigest()[:16]
        return f"{scope}_{h}"

    def get(
        self,
        query: str,
        user_id: Optional[str] = None,
        similarity_threshold: Optional[float] = None,
        tier: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Fetches best semantic match from Redis.
        Soft Fail-Closed: Returns None on any Redis error or circuit break.
        """
        if not os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes"):
            return None

        if not self._allow_request():
            logger.debug("RedisSemanticCache circuit breaker is OPEN. Fast bypassing get().")
            return None

        query_emb = self._generate_embedding(query)
        if query_emb is None:
            return None

        self._local_lookups += 1
        threshold = similarity_threshold or self.get_tier_threshold(tier)

        if self._redis is None:
            self._init_redis()
            if self._redis is None:
                return None

        # Multi-tenant Candidate-Set Scan with Vector Cosine Similarity
        try:
            candidate_entry_ids = set()
            public_ids = self._redis.smembers(self.public_keys_set) or set()
            candidate_entry_ids.update(public_ids)

            if user_id:
                user_ids = self._redis.smembers(self.user_keys_set(user_id)) or set()
                candidate_entry_ids.update(user_ids)

            if not candidate_entry_ids:
                self._record_redis_success()
                return None

            # Fetch candidate entries in one batch
            entry_keys = [self.entry_key(eid) for eid in candidate_entry_ids]
            raw_entries = self._redis.mget(entry_keys)

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
                except Exception as entry_err:
                    logger.warning("RedisSemanticCache error deserializing cache entry %s: %s. Skipping entry.", eid, entry_err)
                    expired_ids.append(eid)

            # Lazy cleanup of expired keys in sets
            if expired_ids:
                try:
                    pipe = self._redis.pipeline()
                    pipe.srem(self.public_keys_set, *expired_ids)
                    if user_id:
                        pipe.srem(self.user_keys_set(user_id), *expired_ids)
                    pipe.execute()
                except Exception as cleanup_err:
                    logger.warning("RedisSemanticCache lazy cleanup error: %s", cleanup_err)

            self._record_redis_success()

            if best_match and highest_sim >= threshold and best_entry_id:
                best_match.hit_count += 1
                self._local_hits += 1

                # Update hit count asynchronously in Redis
                try:
                    pipe = self._redis.pipeline()
                    pipe.set(
                        self.entry_key(best_entry_id),
                        json.dumps(best_match.to_dict()),
                        keepttl=True
                    )
                    pipe.execute()
                except Exception as hit_err:
                    logger.warning("RedisSemanticCache error updating hit count: %s", hit_err)

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
            self._record_redis_failure(e, "get")
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
        if not self._allow_request():
            logger.debug("RedisSemanticCache circuit breaker is OPEN. Fast bypassing set().")
            return None

        query_emb = self._generate_embedding(query)
        if query_emb is None:
            logger.debug("Skipping RedisSemanticCache set: embedding is None (Fail-Closed mode).")
            return None

        if self._redis is None:
            self._init_redis()
            if self._redis is None:
                return None

        if ttl_seconds is not None:
            ttl = ttl_seconds
        elif is_public:
            ttl = self.default_public_ttl_seconds
        else:
            ttl = self.default_ttl_seconds
        expires_at = time.time() + ttl if ttl > 0 else 0.0

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

            # Store standard JSON entry & set indexing
            entry_k = self.entry_key(entry_id)
            serialized = json.dumps(entry.to_dict())
            if ttl > 0:
                pipe.set(entry_k, serialized, ex=ttl)
            else:
                pipe.set(entry_k, serialized)

            if is_public:
                pipe.sadd(self.public_keys_set, entry_id)
            elif user_id:
                pipe.sadd(self.user_keys_set(user_id), entry_id)

            pipe.execute()
            self._record_redis_success()
            return entry
        except Exception as e:
            self._record_redis_failure(e, "set")
            return None

    def clear(self) -> None:
        """Clears all semantic cache entries from Redis."""
        if not self._allow_request():
            return
        if self._redis is None:
            return
        try:
            public_keys = list(self._redis.smembers(self.public_keys_set) or [])
            all_entry_keys = [self.entry_key(eid) for eid in public_keys]

            # Find user sets
            user_sets = list(self._redis.keys(self.user_keys_pattern()) or [])
            for u_set in user_sets:
                u_keys = list(self._redis.smembers(u_set) or [])
                all_entry_keys.extend([self.entry_key(eid) for eid in u_keys])

            if all_entry_keys or user_sets or public_keys:
                pipe = self._redis.pipeline()
                if all_entry_keys:
                    pipe.delete(*all_entry_keys)
                if user_sets:
                    pipe.delete(*user_sets)
                pipe.delete(self.public_keys_set)
                pipe.execute()

            self._local_lookups = 0
            self._local_hits = 0
            self._record_redis_success()
        except Exception as e:
            self._record_redis_failure(e, "clear")

    def invalidate(
        self,
        article_id: Optional[str] = None,
        system: Optional[str] = None
    ) -> int:
        """
        Scans and invalidates cached entries in Redis matching article_id or system.
        """
        if not self._allow_request() or self._redis is None:
            return 0
        try:
            public_keys = list(self._redis.smembers(self.public_keys_set) or [])
            candidate_ids = set(public_keys)
            user_sets = list(self._redis.keys(self.user_keys_pattern()) or [])
            for u_set in user_sets:
                candidate_ids.update(list(self._redis.smembers(u_set) or []))

            if not candidate_ids:
                return 0

            entry_keys = [self.entry_key(eid) for eid in candidate_ids]
            raw_entries = self._redis.mget(entry_keys)

            ids_to_delete = []
            aid_upper = article_id.strip().upper() if article_id else None
            sys_upper = system.strip().upper() if system else None

            for eid, raw_json in zip(candidate_ids, raw_entries):
                if not raw_json:
                    ids_to_delete.append(eid)
                    continue
                try:
                    data = json.loads(raw_json)
                    meta = data.get("metadata", {})
                    resp = data.get("response", "")
                    meta_aid = (meta.get("article_id") or "").upper()
                    meta_sys = (meta.get("system") or "").upper()

                    match_aid = aid_upper and (aid_upper in meta_aid or aid_upper in resp.upper())
                    match_sys = sys_upper and (sys_upper == meta_sys)

                    if match_aid or match_sys:
                        ids_to_delete.append(eid)
                except Exception:
                    ids_to_delete.append(eid)

            if ids_to_delete:
                pipe = self._redis.pipeline()
                pipe.delete(*[self.entry_key(eid) for eid in ids_to_delete])
                pipe.srem(self.public_keys_set, *ids_to_delete)
                for u_set in user_sets:
                    pipe.srem(u_set, *ids_to_delete)
                pipe.execute()
                self._record_redis_success()
                return len(ids_to_delete)
            return 0
        except Exception as e:
            self._record_redis_failure(e, "invalidate")
            return 0

    def get_stats(self) -> dict:
        """Returns statistics on semantic cache usage."""
        total_entries = 0
        if self._redis is not None:
            try:
                public_count = self._redis.scard(self.public_keys_set) or 0
                total_entries += public_count
                for u_set in self._redis.keys(self.user_keys_pattern()) or []:
                    total_entries += (self._redis.scard(u_set) or 0)
                self._record_redis_success()
            except Exception as e:
                self._record_redis_failure(e, "get_stats")

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
            "backend": "redis",
            "circuit_breaker_tripped": self._circuit_breaker_tripped,
            "consecutive_failures": self._consecutive_redis_failures,
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
