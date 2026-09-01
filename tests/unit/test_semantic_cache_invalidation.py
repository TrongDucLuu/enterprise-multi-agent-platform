import pytest
from it_helpdesk_agent.app_utils.semantic_cache import InMemorySemanticCache


def test_inmemory_semantic_cache_invalidation():
    cache = InMemorySemanticCache(embedding_fn=lambda q: [1.0, 0.0, 0.0] * 42 + [0.0] * 2)
    cache.set(
        query="lỗi ME21N tạo đơn hàng",
        response="Hướng dẫn chi tiết xử lý lỗi ME21N: [Mã: ERP-001]",
        is_public=True,
        tier="L2",
        metadata={"article_id": "ERP-001", "system": "ERP"},
    )
    cache.set(
        query="hướng dẫn chấm công",
        response="Quy trình chấm công Workday: [Mã: HRM-001]",
        is_public=True,
        tier="L2",
        metadata={"article_id": "HRM-001", "system": "HRM"},
    )

    assert len(cache._entries) == 2

    # Invalidate article ERP-001
    removed = cache.invalidate(article_id="ERP-001")
    assert removed == 1
    assert len(cache._entries) == 1
    assert cache._entries[0].metadata.get("article_id") == "HRM-001"

    # Invalidate system HRM
    removed_sys = cache.invalidate(system="HRM")
    assert removed_sys == 1
    assert len(cache._entries) == 0
