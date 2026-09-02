import math
import os
from typing import Optional, Any
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


# Default ALLOWED_DOMAINS for hermetic unit testing in production mode
os.environ.setdefault("ALLOWED_DOMAINS", "company.com,example.com")


class FakeDocumentSnapshot:
    def __init__(self, doc_id: str, data: Optional[dict]):
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> Optional[dict]:
        if self._data is None:
            return None
        return dict(self._data)


class FakeDocumentReference:
    def __init__(self, collection: "FakeCollectionReference", doc_id: str):
        self.collection = collection
        self.id = doc_id

    def get(self) -> FakeDocumentSnapshot:
        data = self.collection.client._store.get(self.collection.name, {}).get(self.id)
        return FakeDocumentSnapshot(self.id, data)

    def set(self, data: dict, merge: bool = False):
        col_store = self.collection.client._store.setdefault(self.collection.name, {})
        if merge and self.id in col_store:
            col_store[self.id].update(data)
        else:
            col_store[self.id] = dict(data)

    def update(self, data: dict):
        col_store = self.collection.client._store.setdefault(self.collection.name, {})
        if self.id not in col_store:
            col_store[self.id] = {}
        col_store[self.id].update(data)


class FakeQuery:
    def __init__(self, collection: "FakeCollectionReference", filters: list, limit_val: Optional[int] = None):
        self.collection = collection
        self.filters = filters
        self._limit_val = limit_val

    def where(self, field: Optional[str] = None, op: Optional[str] = None, value: Any = None, filter: Any = None) -> "FakeQuery":
        new_filters = list(self.filters)
        if filter is not None:
            f_field = getattr(filter, "field_name", None) or getattr(filter, "field_path", None)
            f_op = getattr(filter, "op_string", "==")
            f_val = getattr(filter, "value", None)
            new_filters.append((f_field, f_op, f_val))
        elif field is not None:
            new_filters.append((field, op or "==", value))
        return FakeQuery(self.collection, new_filters, limit_val=self._limit_val)

    def limit(self, count: int) -> "FakeQuery":
        return FakeQuery(self.collection, self.filters, limit_val=count)

    def order_by(self, *args, **kwargs) -> "FakeQuery":
        return self

    def stream(self):
        col_store = self.collection.client._store.get(self.collection.name, {})
        matched = []
        for doc_id, data in list(col_store.items()):
            match = True
            for field, op, val in self.filters:
                doc_val = data.get(field)
                if op == "==" and doc_val != val:
                    match = False
                    break
                elif op == "in" and doc_val not in val:
                    match = False
                    break
            if match:
                matched.append(FakeDocumentSnapshot(doc_id, dict(data)))
                if self._limit_val is not None and len(matched) >= self._limit_val:
                    break
        for item in matched:
            yield item


class FakeCollectionReference:
    def __init__(self, client: "FakeFirestoreClient", name: str):
        self.client = client
        self.name = name

    def document(self, doc_id: str) -> FakeDocumentReference:
        return FakeDocumentReference(self, str(doc_id))

    def where(self, field: Optional[str] = None, op: Optional[str] = None, value: Any = None, filter: Any = None) -> FakeQuery:
        return FakeQuery(self, []).where(field=field, op=op, value=value, filter=filter)

    def limit(self, count: int) -> FakeQuery:
        return FakeQuery(self, [], limit_val=count)

    def order_by(self, *args, **kwargs) -> FakeQuery:
        return FakeQuery(self, [])

    def stream(self):
        return FakeQuery(self, []).stream()


class FakeFirestoreClient:
    def __init__(self):
        self._store: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self, name)


@pytest.fixture(autouse=True)
def fake_firestore():
    """
    Autouse fixture providing an in-memory FakeFirestoreClient
    injected into case_tool for unit tests running under ENVIRONMENT=production.
    Specific fail-closed tests can override or reset this.
    """
    from agent_core.tools.case_tool import set_firestore_client, reset_firestore_client
    client = FakeFirestoreClient()
    set_firestore_client(client)
    yield client
    reset_firestore_client()


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


@pytest.fixture(autouse=True)
def hermetic_domain_pack_isolation(monkeypatch):
    """
    Ensures that in-process unit tests run with DOMAIN_PACK='it-helpdesk' by default,
    preventing global runner environment variables (e.g. DOMAIN_PACK=_template) from
    leaking into tests specifically asserting IT Helpdesk systems and knowledge base.
    """
    monkeypatch.setenv("DOMAIN_PACK", "it-helpdesk")
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "in_memory")
    from agent_core.app_utils.system_config import reload_system_config
    try:
        reload_system_config()
    except Exception:
        pass
    yield
    try:
        reload_system_config()
    except Exception:
        pass
