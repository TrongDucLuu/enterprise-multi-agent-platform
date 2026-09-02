import math
import os
import pytest
from unittest.mock import MagicMock

def _compute_deterministic_embedding(text_or_obj) -> list[float]:
    if hasattr(text_or_obj, "text"):
        text = str(text_or_obj.text)
    else:
        text = str(text_or_obj)
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


class MockEmbeddingItem:
    def __init__(self, values: list[float]):
        self.values = values


class MockTextEmbeddingModel:
    def get_embeddings(self, texts: list) -> list[MockEmbeddingItem]:
        return [MockEmbeddingItem(_compute_deterministic_embedding(t)) for t in texts]


# NOTE: In unit test environments, we use a lightweight 128-dimensional deterministic mock vector
# for performance and hermetic testing without GCP network calls.
# In live production environments, Vertex AI TextEmbeddingModel (e.g. text-multilingual-embedding-002)
# produces 768-dimensional embeddings.
# USE_VERTEX_EMBEDDING=true is set as default so all code paths utilizing embeddings are exercised in tests.
os.environ.setdefault("USE_VERTEX_EMBEDDING", "true")


@pytest.fixture(autouse=True)
def mock_vertex_embeddings_for_tests(monkeypatch):
    """
    Autouse fixture that provides deterministic local embeddings for TextEmbeddingModel.from_pretrained,
    allowing unit tests under ENVIRONMENT=production to test semantic cache and Redis without network calls.
    Specific fail-closed tests can override this via monkeypatch/patch.
    """
    if not os.getenv("USE_VERTEX_EMBEDDING"):
        monkeypatch.setenv("USE_VERTEX_EMBEDDING", "true")

    mock_model_instance = MockTextEmbeddingModel()

    def _mock_from_pretrained(model_name: str):
        return mock_model_instance

    try:
        import vertexai.language_models
        monkeypatch.setattr(
            vertexai.language_models.TextEmbeddingModel,
            "from_pretrained",
            _mock_from_pretrained,
        )
    except (ImportError, AttributeError):
        pass


@pytest.fixture
def admin_sec_ctx():
    from agent_core.knowledge.base import SecurityContext
    return SecurityContext.from_user(
        user_id="test-admin",
        roles=["admin", "it_admin", "support_agent"],
        clearance_level=3,
    )


@pytest.fixture
def employee_sec_ctx():
    from agent_core.knowledge.base import SecurityContext
    return SecurityContext.from_user(
        user_id="test-employee",
        roles=["employee"],
        clearance_level=1,
    )
