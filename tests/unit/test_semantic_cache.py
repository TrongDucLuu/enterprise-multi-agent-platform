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
    
    cache.set(query=query, response=response, tier="L1", is_public=True)
    
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
    cache.set(query=q1, response=resp, is_public=True)

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
        response="Truy cập https://account.company.com/reset",
        is_public=True,
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
        ttl_seconds=1,
        is_public=True,
    )

    # Immediately should hit
    assert cache.get("Sự cố VPN Cisco") is not None

    # Wait for TTL to pass
    time.sleep(1.1)
    assert cache.get("Sự cố VPN Cisco") is None


def test_semantic_cache_lru_eviction():
    cache = SemanticCache(max_size=2)
    cache.clear()

    cache.set("Q1", "Resp1", is_public=True)
    cache.set("Q2", "Resp2", is_public=True)

    # Access Q2 so it gets hit count
    cache.get("Q2")

    # Add Q3 -> Should evict Q1
    cache.set("Q3", "Resp3", is_public=True)

    stats = cache.get_stats()
    assert stats["total_entries"] == 2


@pytest.mark.asyncio
async def test_semantic_cache_callbacks_roundtrip_authenticated_isolation():
    from unittest.mock import MagicMock
    from google.genai import types
    from google.adk.models import LlmRequest, LlmResponse
    from it_helpdesk_agent.app_utils.sso_auth import current_sso_user, SSOUser
    from it_helpdesk_agent.agent import (
        semantic_cache_before_model_callback,
        semantic_cache_after_model_callback
    )

    cache = get_semantic_cache()
    cache.clear()

    # 1. Private / Account-sensitive query: "Cách reset mật khẩu Windows của tôi"
    alice = SSOUser(user_id="alice_123", email="alice@corp.com", full_name="Alice Nguyen", roles=["employee"])
    token_alice = current_sso_user.set(alice)

    try:
        mock_ctx = MagicMock()
        mock_ctx._invocation_context.agent.name = "l1_selfservice_agent"

        private_query = "Làm sao để reset mật khẩu Windows của tôi?"
        mock_user_event = MagicMock()
        mock_user_event.author = "user"
        mock_user_event.content.parts = [types.Part.from_text(text=private_query)]
        mock_ctx._invocation_context._get_events.return_value = [mock_user_event]

        req_private = LlmRequest(
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=private_query)])]
        )

        # Before callback should miss initially
        assert await semantic_cache_before_model_callback(mock_ctx, req_private) is None

        # Simulate model response
        sim_resp_private = LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Để reset mật khẩu cá nhân, truy cập portal.company.com/selfservice/reset.")]
            )
        )
        await semantic_cache_after_model_callback(mock_ctx, sim_resp_private)

        # Alice gets cache hit for her private question
        cached_alice = await semantic_cache_before_model_callback(mock_ctx, req_private)
        assert cached_alice is not None
        assert cached_alice.custom_metadata.get("cached") is True

        # Bob asks the exact same private question -> MUST MISS (isolated per-user)
        bob = SSOUser(user_id="bob_456", email="bob@corp.com", full_name="Bob Tran", roles=["employee"])
        token_bob = current_sso_user.set(bob)
        try:
            cached_bob = await semantic_cache_before_model_callback(mock_ctx, req_private)
            assert cached_bob is None, "Private reset password query must NOT be cached publicly!"
        finally:
            current_sso_user.reset(token_bob)

        # 2. Public General FAQ: "Hướng dẫn kết nối mạng Wi-Fi văn phòng"
        public_query = "Hướng dẫn kết nối mạng Wi-Fi văn phòng"
        mock_user_event_pub = MagicMock()
        mock_user_event_pub.author = "user"
        mock_user_event_pub.content.parts = [types.Part.from_text(text=public_query)]
        mock_ctx._invocation_context._get_events.return_value = [mock_user_event_pub]

        req_public = LlmRequest(
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=public_query)])]
        )

        # Before callback misses initially
        assert await semantic_cache_before_model_callback(mock_ctx, req_public) is None

        # Model response
        sim_resp_public = LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Để kết nối Wi-Fi văn phòng: Chọn SSID 'Corp-Guest' hoặc 'Corp-Internal-5G'.")]
            )
        )
        await semantic_cache_after_model_callback(mock_ctx, sim_resp_public)

        # Alice gets cache hit
        assert await semantic_cache_before_model_callback(mock_ctx, req_public) is not None

        # Switch to Bob -> Bob MUST ALSO GET CACHE HIT because Wi-Fi FAQ is safe public knowledge!
        token_bob = current_sso_user.set(bob)
        try:
            cached_bob_pub = await semantic_cache_before_model_callback(mock_ctx, req_public)
            assert cached_bob_pub is not None, "Public Wi-Fi FAQ must be shared across all authenticated employees!"
            assert cached_bob_pub.custom_metadata.get("cached") is True
            assert "Corp-Internal-5G" in cached_bob_pub.content.parts[0].text
        finally:
            current_sso_user.reset(token_bob)

    finally:
        current_sso_user.reset(token_alice)


@pytest.mark.asyncio
async def test_semantic_cache_after_callback_fails_closed_when_unauthenticated():
    from unittest.mock import MagicMock
    from google.genai import types
    from google.adk.models import LlmResponse
    from it_helpdesk_agent.agent import semantic_cache_after_model_callback

    cache = get_semantic_cache()
    cache.clear()

    # Ensure no SSO user in contextvar
    mock_ctx = MagicMock()
    mock_ctx._invocation_context.agent.name = "l1_selfservice_agent"
    mock_user_event = MagicMock()
    mock_user_event.author = "user"
    mock_user_event.content.parts = [types.Part.from_text(text="Lương tháng này khi nào được chuyển?")]
    mock_ctx._invocation_context._get_events.return_value = [mock_user_event]

    simulated_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text="Lương sẽ chuyển vào ngày 25 hàng tháng.")]
        )
    )

    # Calling without authenticated user MUST FAIL CLOSED and NOT insert into cache
    await semantic_cache_after_model_callback(mock_ctx, simulated_resp)
    assert len(cache._entries) == 0


@pytest.mark.asyncio
async def test_contextvar_propagation_across_threadpool():
    """
    Verifies that current_sso_user contextvar correctly propagates across asyncio
    and concurrent.futures threadpools (preventing identity loss during agent execution).
    """
    import asyncio
    import concurrent.futures
    from it_helpdesk_agent.app_utils.sso_auth import current_sso_user, SSOUser

    user = SSOUser(user_id="engineer_007", email="dev@corp.com", full_name="Dev User", roles=["sys_admin"])
    token = current_sso_user.set(user)

    try:
        # 1. Propagation across asyncio tasks
        async def sub_task():
            u = current_sso_user.get()
            return u.user_id if u else None

        task_user_id = await asyncio.create_task(sub_task())
        assert task_user_id == "engineer_007"

        # 2. Propagation across to_thread (asyncio threadpool)
        def thread_task():
            # In Python 3.11+, context is automatically propagated by asyncio.to_thread
            u = current_sso_user.get()
            return u.user_id if u else None

        thread_user_id = await asyncio.to_thread(thread_task)
        assert thread_user_id == "engineer_007"

    finally:
        current_sso_user.reset(token)


