import time
import pytest
from agent_core.app_utils.semantic_cache import (
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
    from agent_core.app_utils.sso_auth import current_sso_user, SSOUser
    from agent_core.agent import (
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
    from agent_core.agent import semantic_cache_after_model_callback

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
    from agent_core.app_utils.sso_auth import current_sso_user, SSOUser

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


def test_is_safe_public_faq_word_boundary_matching():
    """
    P2.7: Word Boundary Matching for _is_safe_public_faq.
    Verifies that substring 'po' does not prevent public caching for queries
    like 'wifi support' or 'quy định it về powerpoint', while actual sensitive
    terms like 'purchase order của tôi' and 'kiểm tra PO 123' remain private.
    """
    from agent_core.agent import _is_safe_public_faq

    # 1. Safe queries that contain 'po' as substring inside words like 'support', 'powerpoint', 'portal', 'policy'
    assert _is_safe_public_faq("wifi support", "l1_selfservice_agent", []) is True
    assert _is_safe_public_faq("quy định it về powerpoint", "l1_selfservice_agent", []) is True
    assert _is_safe_public_faq("hướng dẫn cài đặt vpn văn phòng", "l1_selfservice_agent", []) is True

    # 2. Private queries containing standalone PO or purchase order
    assert _is_safe_public_faq("purchase order của tôi", "l1_selfservice_agent", []) is False
    assert _is_safe_public_faq("kiểm tra mã PO 12345", "l1_selfservice_agent", []) is False
    assert _is_safe_public_faq("hướng dẫn reset password", "l1_selfservice_agent", []) is False

    # 3. Non-L1 agents or tool-calling agents must never be public FAQ
    assert _is_safe_public_faq("wifi support", "l2_operator_agent", []) is False
    assert _is_safe_public_faq("wifi support", "l1_selfservice_agent", ["search_knowledge_base"]) is False


def test_redis_semantic_cache_deserialization_and_errors_log_warning(caplog):
    """
    P2.6: Verifies that Redis errors, pipeline errors, and corrupted/invalid cache entry
    deserialization emit log at WARNING level so operators are alerted.
    """
    import logging
    import fakeredis
    from agent_core.app_utils.semantic_cache import RedisSemanticCache

    caplog.set_level(logging.WARNING)

    # 1. Corrupted entry deserialization
    fake_server = fakeredis.FakeServer()
    r = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
    cache = RedisSemanticCache(redis_client=r, similarity_threshold=0.85)

    # Inject corrupted JSON into public cache
    r.sadd(cache.public_keys_set, "corrupted_eid")
    r.set(cache.entry_key("corrupted_eid"), "NOT_A_VALID_JSON{")

    # get() should skip corrupted entry and log a warning
    res = cache.get("Hướng dẫn Wi-Fi")
    assert res is None

    warning_messages = [rec.message for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("deserializing" in msg.lower() or "skipping entry" in msg.lower() for msg in warning_messages)

    # 2. Redis operation failure
    class ErrorRedis:
        def smembers(self, key):
            raise RuntimeError("Simulated Redis socket failure")
        def pipeline(self):
            raise RuntimeError("Simulated Redis pipeline failure")

    err_cache = RedisSemanticCache(redis_client=ErrorRedis())
    err_res = err_cache.get("Lỗi kết nối", user_id="user_1")
    assert err_res is None

    warning_messages = [rec.message for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("error" in msg.lower() or "soft fail-closed" in msg.lower() for msg in warning_messages)


def test_semantic_cache_tier_aware_thresholds():
    """
    P1.2: Verifies that risk-weighted thresholds differ by operational tier:
    - L1: 0.90 (Low risk)
    - L2: 0.92 (Medium risk)
    - L3: 0.98 (High risk - near exact match required)
    """
    from agent_core.app_utils.semantic_cache import InMemorySemanticCache, DEFAULT_TIER_THRESHOLDS

    cache = InMemorySemanticCache(similarity_threshold=0.92)
    assert cache.get_tier_threshold("L1") == 0.90
    assert cache.get_tier_threshold("l1_selfservice_agent") == 0.90
    assert cache.get_tier_threshold("L2") == 0.92
    assert cache.get_tier_threshold("l2_enterprise_rag_agent") == 0.92
    assert cache.get_tier_threshold("L3") == 0.98
    assert cache.get_tier_threshold("l3_deep_diagnostics_agent") == 0.98


def test_l3_queries_false_hit_protection_mutation():
    """
    P1.2 (Mutation): Test 2 L3 queries with similar incident symptoms but distinct contexts/causes.
    Verifies that L3 threshold (0.98) prevents false cache hits on distinct incidents.
    """
    from agent_core.app_utils.semantic_cache import InMemorySemanticCache

    cache = InMemorySemanticCache(similarity_threshold=0.92)
    cache.clear()

    # Incident 1
    q1 = "Sự cố OutOfMemory trên SAP App Server node 1"
    resp1 = "RCA Incident 1: Heap dump phân tích phát hiện rò rỉ bộ nhớ do batch job xuất báo cáo hóa đơn treo luồng."
    cache.set(query=q1, response=resp1, is_public=True, tier="L3")

    # Incident 2 (Similar phrasing but different cause/node)
    q2 = "Sự cố OutOfMemory trên SAP App Server node 2 do spike người dùng"

    # In L1/L2 with threshold 0.85/0.90 it might hit, but in L3 with threshold 0.98 it MUST miss
    match_l3 = cache.get(q2, tier="l3_deep_diagnostics_agent")
    assert match_l3 is None, "L3 query must NOT falsely hit cache for distinct incident context"


@pytest.mark.asyncio
async def test_l3_agent_cache_bypass_in_callbacks():
    """
    P1.2: Verifies that l3_deep_diagnostics_agent completely bypasses semantic cache:
    1. semantic_cache_before_model_callback returns None (never returns cached response).
    2. semantic_cache_after_model_callback never persists L3 outputs to cache.
    """
    from unittest.mock import MagicMock
    from agent_core.agent import (
        semantic_cache_before_model_callback,
        semantic_cache_after_model_callback,
        current_sso_user,
    )
    from agent_core.app_utils.sso_auth import SSOUser
    from google.genai import types
    from google.adk.models import LlmRequest, LlmResponse
    from agent_core.app_utils.semantic_cache import get_semantic_cache

    cache = get_semantic_cache()
    cache.clear()

    test_user = SSOUser(user_id="dev_user", email="dev@corp.internal", hosted_domain="corp.internal")
    token = current_sso_user.set(test_user)

    try:
        mock_ctx = MagicMock()
        mock_ctx._invocation_context.agent.name = "l3_deep_diagnostics_agent"

        query_text = "Phân tích lỗi OutOfMemory trên cụm SAP"
        mock_user_event = MagicMock()
        mock_user_event.author = "user"
        mock_user_event.content.parts = [types.Part.from_text(text=query_text)]
        mock_ctx._invocation_context._get_events.return_value = [mock_user_event]

        req = LlmRequest(
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=query_text)])]
        )

        # 1. Before callback: MUST return None (bypassed) even if query was in cache
        cache.set(
            query=query_text,
            response="Cached fake L3 response",
            user_id="dev_user",
            is_public=True
        )

        before_res = await semantic_cache_before_model_callback(mock_ctx, req)
        assert before_res is None, "L3 agent before_model_callback must return None to bypass cache"

        # 2. After callback: MUST NOT store into cache
        cache.clear()
        sim_resp = LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Live diagnostic RCA result")]
            )
        )

        after_res = await semantic_cache_after_model_callback(mock_ctx, sim_resp)
        # modified_response is None if no soft warning modification was needed
        assert after_res is None or isinstance(after_res, LlmResponse)
        # Verify nothing was added to cache
        assert cache.get(query_text) is None
    finally:
        current_sso_user.reset(token)


def test_semantic_cache_public_ttl_default_4h():
    """
    0.8: Verifies that public cache entries default to 14400s (4h) TTL,
    while private entries default to 86400s (24h) TTL.
    """
    import time
    from agent_core.app_utils.semantic_cache import InMemorySemanticCache

    cache = InMemorySemanticCache()
    cache.clear()

    # Public entry
    pub_entry = cache.set(
        query="Hướng dẫn cài đặt VPN",
        response="Tải VPN Client",
        is_public=True
    )
    assert pub_entry is not None
    # TTL should be approx 14400s
    remaining_pub_ttl = pub_entry.expires_at - time.time()
    assert 14390 <= remaining_pub_ttl <= 14400

    # Private entry
    priv_entry = cache.set(
        query="Reset mật khẩu cá nhân",
        response="Truy cập link reset",
        user_id="user_123",
        is_public=False
    )
    assert priv_entry is not None
    remaining_priv_ttl = priv_entry.expires_at - time.time()
    assert 86390 <= remaining_priv_ttl <= 86400


def test_is_safe_public_faq_first_turn_only():
    """
    0.8: Verifies that _is_safe_public_faq returns False when is_first_turn is False.
    """
    from agent_core.agent import _is_safe_public_faq

    # Turn 1 -> Allowed
    assert _is_safe_public_faq("wifi support", "l1_selfservice_agent", [], is_first_turn=True) is True
    # Turn 2+ -> Rejected (multi-turn context risk)
    assert _is_safe_public_faq("wifi support", "l1_selfservice_agent", [], is_first_turn=False) is False


@pytest.mark.asyncio
async def test_semantic_cache_multiturn_public_caching_restricted_to_turn_1():
    """
    0.8: Verifies that during a multi-turn conversation, only turn 1 is cached as public FAQ.
    Turn 2+ queries are cached with user isolation (is_public=False) even if matching FAQ keywords.
    """
    from unittest.mock import MagicMock
    from agent_core.agent import (
        semantic_cache_after_model_callback,
        current_sso_user,
    )
    from agent_core.app_utils.sso_auth import SSOUser
    from google.genai import types
    from google.adk.models import LlmResponse
    from agent_core.app_utils.semantic_cache import get_semantic_cache

    cache = get_semantic_cache()
    cache.clear()

    user = SSOUser(user_id="alice_turn_test", email="alice@corp.com", full_name="Alice", roles=["employee"])
    token = current_sso_user.set(user)

    try:
        mock_ctx = MagicMock()
        mock_ctx._invocation_context.agent.name = "l1_selfservice_agent"

        # Turn 1: First user message
        ev1 = MagicMock()
        ev1.author = "user"
        ev1.content.parts = [types.Part.from_text(text="Hướng dẫn cài đặt wifi văn phòng")]
        mock_ctx._invocation_context._get_events.return_value = [ev1]
        mock_ctx._invocation_context.session.events = [ev1]

        resp1 = LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Kết nối vào SSID Corp-WiFi")]
            )
        )
        await semantic_cache_after_model_callback(mock_ctx, resp1)

        # Verify entry 1 is cached as public
        hit1 = cache.get("Hướng dẫn cài đặt wifi văn phòng", user_id="bob_other_user")
        assert hit1 is not None, "Turn 1 FAQ must be cached as public!"
        assert hit1["is_public"] is True

        # Turn 2: Follow-up question in the same session
        ev2_model = MagicMock(author="model")
        ev2_user = MagicMock()
        ev2_user.author = "user"
        ev2_user.content.parts = [types.Part.from_text(text="wifi văn phòng có hỗ trợ máy in không?")]
        mock_ctx._invocation_context._get_events.return_value = [ev2_user]
        mock_ctx._invocation_context.session.events = [ev1, ev2_model, ev2_user]

        resp2 = LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Có, máy in kết nối qua SSID Corp-WiFi.")]
            )
        )
        await semantic_cache_after_model_callback(mock_ctx, resp2)

        # Bob should NOT be able to access turn 2 response as public cache
        hit2_bob = cache.get("wifi văn phòng có hỗ trợ máy in không?", user_id="bob_other_user")
        assert hit2_bob is None, "Turn 2 query must NOT be cached as public FAQ!"

        # Alice should be able to access it via private user cache
        hit2_alice = cache.get("wifi văn phòng có hỗ trợ máy in không?", user_id="alice_turn_test")
        assert hit2_alice is not None
        assert hit2_alice["is_public"] is False
    finally:
        current_sso_user.reset(token)


def test_redis_semantic_cache_kb_version_namespace():
    """
    0.8: Verifies that RedisSemanticCache constructs namespace keys incorporating KB_VERSION.
    """
    import fakeredis
    from agent_core.app_utils.semantic_cache import RedisSemanticCache

    fake_server = fakeredis.FakeServer()
    r = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)

    cache_v2 = RedisSemanticCache(redis_client=r, kb_version="2")
    assert cache_v2.public_keys_set == "sem_cache:v2:keys:public"
    assert cache_v2.user_keys_set("usr_1") == "sem_cache:v2:keys:user:usr_1"
    assert cache_v2.entry_key("abc") == "sem_cache:v2:entry:abc"

    cache_v2.set(query="Q1", response="A1", is_public=True)
    assert r.sismember("sem_cache:v2:keys:public", cache_v2._get_entry_id("Q1", is_public=True))


def test_semantic_cache_clearance_filtering_in_memory():
    """Verifies that InMemorySemanticCache enforces document/entry clearance level against caller clearance."""
    from agent_core.app_utils.semantic_cache import InMemorySemanticCache

    cache = InMemorySemanticCache()
    cache.clear()

    # Store confidential executive policy (Clearance = 2)
    cache.set(
        query="Chính sách thưởng ban điều hành năm 2026",
        response="Chi tiết thưởng ban điều hành: Confidential Level 2",
        is_public=True,
        clearance_level=2,
    )

    # 1. Anonymous user / clearance 0 -> Must miss
    assert cache.get("Chính sách thưởng ban điều hành năm 2026", clearance_level=0) is None

    # 2. Regular employee / clearance 1 -> Must miss
    assert cache.get("Chính sách thưởng ban điều hành năm 2026", clearance_level=1) is None

    # 3. HR / Executive with clearance 2 -> Must hit
    hit_c2 = cache.get("Chính sách thưởng ban điều hành năm 2026", clearance_level=2)
    assert hit_c2 is not None
    assert hit_c2["clearance_level"] == 2
    assert "Confidential Level 2" in hit_c2["response"]

    # 4. Super Admin with clearance 3 -> Must hit
    hit_c3 = cache.get("Chính sách thưởng ban điều hành năm 2026", clearance_level=3)
    assert hit_c3 is not None


def test_redis_semantic_cache_clearance_filtering_and_partitioning():
    """Verifies that RedisSemanticCache partitions keys by clearance level and filters entries."""
    import fakeredis
    from agent_core.app_utils.semantic_cache import RedisSemanticCache

    fake_server = fakeredis.FakeServer()
    r = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
    cache = RedisSemanticCache(redis_client=r)

    q = "Kế hoạch M&A quý 4"
    resp = "Kế hoạch sáp nhập đối thủ: Restricted Level 3"

    entry = cache.set(
        query=q,
        response=resp,
        is_public=True,
        clearance_level=3,
    )
    assert entry is not None
    assert entry.clearance_level == 3

    # Check key deterministic partitioning contains clearance level
    entry_id = cache._get_entry_id(q, is_public=True, clearance_level=3)
    assert "_c3_" in entry_id

    # Caller with clearance 1 -> Miss
    assert cache.get(q, clearance_level=1) is None

    # Caller with clearance 2 -> Miss
    assert cache.get(q, clearance_level=2) is None

    # Caller with clearance 3 -> Hit
    hit = cache.get(q, clearance_level=3)
    assert hit is not None
    assert hit["clearance_level"] == 3
    assert "Restricted Level 3" in hit["response"]


def test_redis_semantic_cache_auth_and_tls_config():
    """Verifies RedisSemanticCache reads and stores password and ssl options."""
    from agent_core.app_utils.semantic_cache import RedisSemanticCache
    import fakeredis

    r = fakeredis.FakeStrictRedis(decode_responses=True)
    cache = RedisSemanticCache(
        redis_client=r,
        password="test_secret_redis_pass",
        ssl=True,
    )
    assert cache._password == "test_secret_redis_pass"
    assert cache._ssl is True


def test_redis_rate_limiter_auth_and_tls_config():
    """Verifies RedisRateLimiter reads and stores password and ssl options."""
    from agent_core.app_utils.rate_limiter import RedisRateLimiter
    import fakeredis

    r = fakeredis.FakeStrictRedis(decode_responses=True)
    limiter = RedisRateLimiter(
        redis_client=r,
        password="test_secret_redis_pass",
        ssl=True,
    )
    assert limiter._password == "test_secret_redis_pass"
    assert limiter._ssl is True


def test_redis_semantic_cache_production_missing_auth_raises(monkeypatch):
    """
    Tier R2 (Phần D3): Validates that in production mode, missing REDIS_AUTH_STRING
    strictly raises a RuntimeError rather than connecting unauthenticated.
    """
    from agent_core.app_utils.semantic_cache import RedisSemanticCache

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("REDIS_AUTH_STRING", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="REDIS_AUTH_STRING is required in production mode"):
        RedisSemanticCache(host="redis.internal", password=None)


def test_redis_semantic_cache_production_tls_missing_ca_raises(monkeypatch):
    """
    Tier R2 (Phần D1): Validates that in production mode with TLS enabled,
    missing CA certificate strictly raises a RuntimeError.
    """
    from agent_core.app_utils.semantic_cache import RedisSemanticCache

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("REDIS_CA_CERT", raising=False)
    monkeypatch.delenv("REDIS_CA_CERT_PATH", raising=False)
    monkeypatch.delenv("REDIS_TLS_CA_CERTS", raising=False)

    with pytest.raises(RuntimeError, match="Redis TLS CA certificate verification is required in production mode"):
        RedisSemanticCache(host="redis.internal", password="prod-password-123", ssl=True)


def test_redis_semantic_cache_production_tls_with_ca_cert(monkeypatch):
    """
    Tier R2 (Phần D1): Validates that in production mode with REDIS_CA_CERT provided,
    ssl_cert_reqs is set to 'required' and ssl_ca_certs points to the certificate file.
    """
    from unittest.mock import MagicMock, patch
    from agent_core.app_utils.semantic_cache import RedisSemanticCache

    monkeypatch.setenv("ENVIRONMENT", "production")
    sample_ca_pem = "-----BEGIN CERTIFICATE-----\nMIIB...fake...ca...cert\n-----END CERTIFICATE-----"
    monkeypatch.setenv("REDIS_CA_CERT", sample_ca_pem)

    captured_kwargs = {}
    mock_redis_instance = MagicMock()
    mock_redis_instance.ping.return_value = True

    def fake_redis_init(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_redis_instance

    with patch("redis.Redis", side_effect=fake_redis_init):
        cache = RedisSemanticCache(host="redis.internal", password="prod-password-123", ssl=True)

    assert captured_kwargs.get("ssl") is True
    assert captured_kwargs.get("ssl_cert_reqs") == "required"
    assert captured_kwargs.get("ssl_ca_certs") is not None
    assert captured_kwargs.get("password") == "prod-password-123"


def test_redis_semantic_cache_dev_tls_allows_unverified(monkeypatch):
    """
    Tier R2 (Phần D1): Validates that in development mode, TLS allows ssl_cert_reqs=None for local testing.
    """
    from unittest.mock import MagicMock, patch
    from agent_core.app_utils.semantic_cache import RedisSemanticCache

    monkeypatch.setenv("ENVIRONMENT", "development")
    captured_kwargs = {}
    mock_redis_instance = MagicMock()
    mock_redis_instance.ping.return_value = True

    def fake_redis_init(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_redis_instance

    with patch("redis.Redis", side_effect=fake_redis_init):
        cache = RedisSemanticCache(host="localhost", password=None, ssl=True)

    assert captured_kwargs.get("ssl") is True
    assert captured_kwargs.get("ssl_cert_reqs") is None


def test_runtime_semantic_cache_callback_propagates_clearance_and_blocks_public(monkeypatch):
    """
    Tier R2 (Phần E): Ensures that semantic_cache_after_model_callback determines the caller's
    clearance level, sets is_public=False for user-specific queries, and passes clearance_level to cache.set.
    """
    import pytest
    from unittest.mock import MagicMock, patch
    from google.adk.models import LlmResponse
    from google.genai import types
    from agent_core.runtime import semantic_cache_after_model_callback
    from agent_core.app_utils.sso_auth import SSOUser, current_sso_user

    exec_user = SSOUser(
        user_id="compliance_vp",
        email="compliance@company.com",
        display_name="Compliance Officer",
        roles=["compliance_officer"],
    )
    current_sso_user.set(exec_user)

    mock_cache = MagicMock()
    with patch("agent_core.runtime.get_semantic_cache", return_value=mock_cache):
        llm_resp = LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Đây là kế hoạch kiểm toán bảo mật cấp 2.")]
            )
        )

        mock_inv_ctx = MagicMock()
        mock_inv_ctx.agent.name = "l1_selfservice_agent"
        mock_event = MagicMock()
        mock_event.author = "user"
        mock_event.content.parts = [types.Part.from_text(text="Kiểm tra logs audit của tài khoản tôi")]
        mock_inv_ctx._get_events.return_value = [mock_event]
        mock_inv_ctx.session.events = [mock_event]

        mock_cb_ctx = MagicMock()
        mock_cb_ctx._invocation_context = mock_inv_ctx

        import asyncio
        asyncio.run(semantic_cache_after_model_callback(mock_cb_ctx, llm_resp))

    assert mock_cache.set.called
    kwargs = mock_cache.set.call_args.kwargs
    assert kwargs.get("clearance_level") == 2
    assert kwargs.get("is_public") is False
    assert kwargs.get("user_id") == "compliance_vp"








