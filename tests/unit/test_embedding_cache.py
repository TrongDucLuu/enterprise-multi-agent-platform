import pytest
from unittest.mock import MagicMock, patch
from it_helpdesk_agent.app_utils.embedding_utils import (
    generate_text_embedding,
    clear_embedding_cache,
    _EMBEDDING_CACHE,
)


def setup_function():
    clear_embedding_cache()


def test_embedding_ttl_cache_avoids_recomputation():
    mock_model = MagicMock()
    mock_emb = MagicMock()
    mock_emb.values = [0.1] * 768
    mock_model.get_embeddings.return_value = [mock_emb]

    with patch("vertexai.language_models.TextEmbeddingModel.from_pretrained", return_value=mock_model):
        # 1st call: Should call Vertex AI
        vec1 = generate_text_embedding("query text 1", use_vertex=True, use_cache=True)
        assert len(vec1) == 768
        assert mock_model.get_embeddings.call_count == 1

        # 2nd call with same query: Should hit cache without calling Vertex AI
        vec2 = generate_text_embedding("query text 1", use_vertex=True, use_cache=True)
        assert vec2 == vec1
        assert mock_model.get_embeddings.call_count == 1

        # 3rd call with different query: Should call Vertex AI
        vec3 = generate_text_embedding("query text 2", use_vertex=True, use_cache=True)
        assert mock_model.get_embeddings.call_count == 2

        # 4th call with use_cache=False: Should call Vertex AI
        vec4 = generate_text_embedding("query text 1", use_vertex=True, use_cache=False)
        assert mock_model.get_embeddings.call_count == 3


def test_clear_embedding_cache():
    generate_text_embedding("query text ABC", use_vertex=False, use_cache=True)
    assert len(_EMBEDDING_CACHE) > 0

    clear_embedding_cache()
    assert len(_EMBEDDING_CACHE) == 0
