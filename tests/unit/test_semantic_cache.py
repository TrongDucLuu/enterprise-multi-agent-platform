import time
import pytest
from it_helpdesk_agent.app_utils.semantic_cache import (
    SemanticCache,
    cosine_similarity,
    get_semantic_cache,
)


def test_cosine_similarity_identical_and_orthogonal():
    vec1 = [1.0, 2.0, 3.0]
    vec2 = [1.0, 2.0, 3.0]
    assert pytest.approx(cosine_similarity(vec1, vec2), 0.0001) == 1.0

    vec_ortho = [-2.0, 1.0, 0.0]
    # dot product = -2 + 2 + 0 = 0
    assert pytest.approx(cosine_similarity(vec1, vec_ortho), 0.0001) == 0.0

    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


def test_semantic_cache_exact_hit():
    cache = SemanticCache(similarity_threshold=0.90)
    cache.clear()

    query = "Hướng dẫn kết nối mạng Wi-Fi doanh nghiệp tầng 3"
    response = "Để kết nối Wi-Fi tầng 3, vui lòng chọn SSID 'Corp-Internal-5G' và nhập mật khẩu WPA2-Enterprise."
    
    cache.set(query=query, response=response, tier="L1")
    
    match = cache.get(query)
    assert match is not None
    assert match["status"] == "cache_hit"
    assert match["response"] == response
    assert match["similarity"] >= 0.99
    assert match["hits"] == 1


def test_semantic_cache_similar_query_hit():
    cache = SemanticCache(similarity_threshold=0.85)
    cache.clear()

    q1 = "Làm sao để đổi mật khẩu Wi-Fi nội bộ"
    resp = "Truy cập cổng portal.company.com/wifi và đăng nhập tài khoản Okta."
    cache.set(query=q1, response=resp)

    q2 = "Làm sao để đổi mật khẩu Wi-Fi nội bộ công ty"
    match = cache.get(q2)
    assert match is not None
    assert match["status"] == "cache_hit"
    assert match["response"] == resp
    assert match["similarity"] >= 0.85


def test_semantic_cache_miss_on_different_query():
    cache = SemanticCache(similarity_threshold=0.90)
    cache.clear()

    cache.set(
        query="Cách reset mật khẩu Windows Active Directory",
        response="Truy cập https://account.company.com/reset"
    )

    unrelated_query = "Phân tích log Out of memory trên cụm Kubernetes"
    match = cache.get(unrelated_query)
    assert match is None


def test_semantic_cache_ttl_expiration():
    cache = SemanticCache(similarity_threshold=0.90, default_ttl_seconds=1)
    cache.clear()

    cache.set(
        query="Sự cố VPN Cisco",
        response="Khởi động lại VPN client",
        ttl_seconds=1
    )

    # Immediately should hit
    assert cache.get("Sự cố VPN Cisco") is not None

    # Wait for TTL to pass
    time.sleep(1.1)
    assert cache.get("Sự cố VPN Cisco") is None


def test_semantic_cache_lru_eviction():
    cache = SemanticCache(max_size=2)
    cache.clear()

    cache.set("Q1", "Resp1")
    cache.set("Q2", "Resp2")

    # Access Q2 so it gets hit count
    cache.get("Q2")

    # Add Q3 -> Should evict Q1
    cache.set("Q3", "Resp3")

    stats = cache.get_stats()
    assert stats["total_entries"] == 2
