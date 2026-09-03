import ast
import os
import glob
import logging
import pytest
from unittest.mock import MagicMock, patch

# Pins it-helpdesk pack because probe tests assert IT Helpdesk systems and fail-closed RAG lookups
pytestmark = pytest.mark.usefixtures("pinned_it_helpdesk_pack")

from agent_core.knowledge.base import SecurityContext
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user
from agent_core.app_utils.artifact_storage import resolve_artifact_content
from agent_core.tools.enterprise_rag_mcp.rag_models import KnowledgeArticle, Fact
from agent_core.tools.enterprise_rag_mcp.knowledge_store import (
    InMemoryKnowledgeStore,
    BigQueryVectorKnowledgeStore,
    VertexAISearchKnowledgeStore,
    InMemoryFactsStore,
    BigQueryFactsStore,
    resolve_security_context,
)
from agent_core.tools.enterprise_rag_mcp.main import (
    search_enterprise_knowledge,
    get_system_manual,
    get_obligation,
    lookup_fact,
)


# =====================================================================
# PROBE 1: AST Duplicate Method & Function Detector across all codebase
# =====================================================================

def test_ast_no_duplicate_methods_in_agent_core():
    """
    Scans every Python file under agent_core/ using the AST module.
    Asserts that NO class defines duplicated method names (which would cause method shadowing/dead code).
    Distinguishes standard property getter/setter/deleter pairs from real duplicates.
    """
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agent_core"))
    py_files = glob.glob(f"{root_dir}/**/*.py", recursive=True)
    assert len(py_files) > 0, f"No Python files found in {root_dir}"

    duplicate_findings = []

    for file_path in py_files:
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()

        try:
            tree = ast.parse(code, filename=file_path)
        except SyntaxError as e:
            pytest.fail(f"Syntax error parsing {file_path}: {e}")

        # Check classes for duplicate methods
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                seen_methods = {}
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_name = item.name
                        # Check decorators: is it a setter or deleter for an existing property?
                        is_setter_or_deleter = any(
                            isinstance(dec, ast.Attribute) and dec.attr in ("setter", "deleter")
                            for dec in item.decorator_list
                        )
                        if is_setter_or_deleter:
                            continue

                        if method_name in seen_methods:
                            first_line = seen_methods[method_name]
                            duplicate_findings.append(
                                f"File: {os.path.relpath(file_path, root_dir)}, "
                                f"Class: {node.name}, Method: {method_name} "
                                f"(defined at line {first_line} and duplicated at line {item.lineno})"
                            )
                        else:
                            seen_methods[method_name] = item.lineno

    assert not duplicate_findings, (
        f"Found duplicate methods in agent_core classes:\n" + "\n".join(duplicate_findings)
    )


# =====================================================================
# PROBE 2: Zero Fabricated Identity & Fail-Closed Search Behavior
# =====================================================================

def test_search_fail_closed_for_anonymous_caller():
    """
    Probe 2: Verify that an unauthenticated caller (current_sso_user is None, no sec_ctx passed)
    is resolved strictly to SecurityContext.anonymous() (clearance 0, roles=[]) and
    never receives internal articles (sensitivity=INTERNAL, clearance 1).
    """
    token = current_sso_user.set(None)
    try:
        # 1. Test InMemoryKnowledgeStore with anonymous context
        store = InMemoryKnowledgeStore()
        results = store.search(query="SAP purchase order", security_context=SecurityContext.anonymous())
        # Should return 0 results because ERP-KB-001 is INTERNAL (clearance 1 > 0)
        assert len(results) == 0, f"Expected 0 results for anonymous caller, got {len(results)}"

        # 2. Test explicit anonymous context
        anon_results = store.search(
            query="SAP purchase order",
            security_context=SecurityContext.anonymous(),
        )
        assert len(anon_results) == 0

        # 3. Test explicit empty roles context
        empty_role_results = store.search(
            query="SAP purchase order",
            security_context=SecurityContext.from_user(roles=[], clearance_level=0),
        )
        assert len(empty_role_results) == 0

        # 4. Authenticated employee receives the article
        emp_ctx = SecurityContext.from_user(user_id="emp-1", roles=["employee"], clearance_level=1)
        emp_results = store.search(query="SAP purchase order", security_context=emp_ctx)
        assert len(emp_results) > 0
        assert emp_results[0].system == "ERP"
    finally:
        current_sso_user.reset(token)


def test_resolve_security_context_never_fabricates_roles():
    """Verify resolve_security_context produces strictly anonymous context when unauthenticated."""
    token = current_sso_user.set(None)
    try:
        ctx = resolve_security_context()
        assert ctx.user_id == "anonymous"
        assert ctx.roles == []
        assert ctx.clearance_level == 0
        assert ctx.authenticated is False

        # When roles given without clearance, clearance is computed deterministically
        ctx_emp = resolve_security_context(user_roles=["employee"])
        assert ctx_emp.clearance_level == 1
        assert "employee" in ctx_emp.roles

        # When security_context explicitly passed, it is preserved exactly
        custom_ctx = SecurityContext(user_id="custom", roles=["special"], clearance_level=2, authenticated=True)
        assert resolve_security_context(security_context=custom_ctx) == custom_ctx
    finally:
        current_sso_user.reset(token)


# =====================================================================
# PROBE 3: Strict Whitelist Artifact Storage & Evasion Resistance
# =====================================================================

@pytest.mark.parametrize("payload", [
    "/proc/self/environ",
    "//proc/self/environ",
    "/./proc/self/environ",
    "/etc/passwd",
    "//etc/passwd",
    "../../../../etc/shadow",
    ".env",
    "/app/.env",
    "file:///etc/hosts",
    "C:\\Windows\\win.ini",
    "/var/log/syslog",
    "gs://unauthorized-attacker-bucket/payload.txt",
])
def test_artifact_storage_evasion_blocked(payload, monkeypatch):
    """
    Probe 3: Verify that artifact_storage rejects all path traversal, LFI,
    and unauthorized GCS buckets via strict Whitelist.
    """
    monkeypatch.setenv("ALLOWED_ARTIFACT_BUCKET", "trusted-company-artifacts")
    content, err = resolve_artifact_content(ref=payload, resource_label="file")
    assert content is None
    assert err is not None
    assert err["status"] == "error"
    assert err["error_code"] in ["INVALID_ARTIFACT_REF", "FORBIDDEN_BUCKET"]


def test_artifact_storage_valid_gcs_and_inline_text(monkeypatch):
    """Verify valid GCS URIs and inline raw text succeed."""
    monkeypatch.setenv("ALLOWED_ARTIFACT_BUCKET", "trusted-company-artifacts")
    
    # 1. Direct raw text
    text, err = resolve_artifact_content(raw_text="Hello world raw content")
    assert err is None
    assert text == "Hello world raw content"

    # 2. Multiline ref rejected as invalid artifact reference (must use raw_text for inline text)
    multi_ref, err = resolve_artifact_content(ref="Line 1\nLine 2\nLine 3")
    assert multi_ref is None
    assert err is not None
    assert err["error_code"] == "INVALID_ARTIFACT_REF"

    # Multiline inline text via raw_text succeeds
    multi_raw, err = resolve_artifact_content(raw_text="Line 1\nLine 2\nLine 3")
    assert err is None
    assert multi_raw == "Line 1\nLine 2\nLine 3"

    # 3. Valid GCS URI with mock
    with patch("agent_core.app_utils.artifact_storage.read_gcs_artifact", return_value="GCS log contents"):
        gcs_text, err = resolve_artifact_content(ref="gs://trusted-company-artifacts/logs/app.log")
        assert err is None
        assert gcs_text == "GCS log contents"


# =====================================================================
# PROBE 4: Fail-Closed MCP Security for Anonymous / Unauthorized Callers
# =====================================================================

def test_mcp_tools_fail_closed_for_anonymous():
    """Verify MCP tools return 403 Forbidden when called by unauthenticated/anonymous caller."""
    token = current_sso_user.set(None)
    try:
        # 1. lookup_fact
        fact_res = lookup_fact("erp.po.sla_hours")
        assert fact_res["status"] == "forbidden"
        assert fact_res["error"] == "Access Denied"

        # 2. get_obligation
        ob_res = get_obligation("OBL-SAP-001")
        assert ob_res["status"] == "forbidden"
        assert ob_res["error"] == "Access Denied"

        # 3. search_enterprise_knowledge
        search_res = search_enterprise_knowledge("purchase order", system="ERP")
        assert len(search_res) == 1
        assert "FORBIDDEN" in search_res[0]["article_id"] or "Access Denied" in search_res[0]["title"]

        # 4. get_system_manual
        manual_res = get_system_manual("ERP-KB-001")
        assert manual_res["status"] == "forbidden"
        assert manual_res["error"] == "Access Denied"
    finally:
        current_sso_user.reset(token)


# =====================================================================
# PROBE 5: 3 Backends x 8 Scenarios Parity Matrix
# =====================================================================

def _get_probe_fixture_articles() -> list[KnowledgeArticle]:
    return [
        KnowledgeArticle(
            id="DOC-PUB-001",
            system="ALL",
            title="Public General Information",
            category="General",
            content="Public policy overview",
            allowed_roles=[],
            sensitivity="PUBLIC",
            clearance_level=0,
            keywords=["public", "general", "policy"],
        ),
        KnowledgeArticle(
            id="DOC-INT-001",
            system="ALL",
            title="Internal Standard Procedure",
            category="General",
            content="Internal procedure details",
            allowed_roles=[],
            sensitivity="INTERNAL",
            clearance_level=1,
            keywords=["internal", "procedure", "policy"],
        ),
        KnowledgeArticle(
            id="DOC-HR-001",
            system="ALL",
            title="HR General Compensation Guide",
            category="HR",
            content="HR compensation rules for specialists",
            allowed_roles=["hr_specialist", "hr_admin", "it_admin"],
            sensitivity="CONFIDENTIAL",
            clearance_level=2,
            keywords=["compensation", "hr", "policy"],
        ),
        KnowledgeArticle(
            id="DOC-HR-EXEC",
            system="ALL",
            title="Executive Salary Bands & Multipliers",
            category="HR",
            content="Executive salary bands and bonus multipliers",
            allowed_roles=["hr_admin", "it_admin"],
            sensitivity="CONFIDENTIAL",
            clearance_level=2,
            keywords=["salary", "executive", "bonus"],
        ),
        KnowledgeArticle(
            id="DOC-RESTRICTED",
            system="ALL",
            title="Domain Root Key Rotation",
            category="Security",
            content="Restricted domain root key rotation procedure",
            allowed_roles=["it_admin"],
            sensitivity="RESTRICTED",
            clearance_level=3,
            keywords=["security", "root", "key"],
        ),
        KnowledgeArticle(
            id="DOC-TOMBSTONE",
            system="ALL",
            title="Deleted Procedure",
            category="General",
            content="This document has been deleted/tombstoned",
            allowed_roles=[],
            sensitivity="INTERNAL",
            clearance_level=1,
            is_deleted=True,
            keywords=["deleted", "procedure"],
        ),
        KnowledgeArticle(
            id="DOC-EXPIRED",
            system="ALL",
            title="Expired Legacy Policy",
            category="General",
            content="Expired legacy policy from 2020",
            allowed_roles=[],
            sensitivity="INTERNAL",
            clearance_level=1,
            expiry_date="2020-01-01",
            keywords=["expired", "legacy"],
        ),
        KnowledgeArticle(
            id="DOC-FUTURE",
            system="ALL",
            title="Future Policy Directive 2099",
            category="General",
            content="Future policy effective in 2099",
            allowed_roles=[],
            sensitivity="INTERNAL",
            clearance_level=1,
            effective_date="2099-01-01",
            keywords=["future", "directive"],
        ),
    ]


def _create_mock_bigquery_store(articles: list[KnowledgeArticle]) -> BigQueryVectorKnowledgeStore:
    """Instantiates a BigQueryVectorKnowledgeStore with a mock BigQuery client returning the fixtures."""
    mock_bq = MagicMock()

    # Convert KnowledgeArticle objects to mock BigQuery row objects
    rows = []
    for a in articles:
        row_mock = MagicMock()
        row_mock.id = a.id
        row_mock.title = a.title
        row_mock.system = a.system
        row_mock.category = a.category
        row_mock.content = a.content
        row_mock.allowed_roles = a.allowed_roles
        row_mock.sensitivity = a.sensitivity
        row_mock.clearance_level = a.clearance_level
        row_mock.keywords = a.keywords
        row_mock.source_uri = a.source_uri
        row_mock.owner = a.owner
        row_mock.effective_date = a.effective_date
        row_mock.expiry_date = a.expiry_date
        row_mock.is_deleted = a.is_deleted
        row_mock.section_h1 = a.section_h1
        row_mock.section_h2 = a.section_h2
        row_mock.section_h3 = a.section_h3
        row_mock.parent_doc_id = a.parent_doc_id
        row_mock.chunk_index = a.chunk_index
        row_mock.distance = 0.05
        rows.append(row_mock)

    def mock_query(sql, job_config=None, timeout=None):
        mock_job = MagicMock()
        if job_config and hasattr(job_config, "query_parameters"):
            art_id_param = next((p.value for p in job_config.query_parameters if p.name == "article_id"), None)
            if art_id_param:
                matching = [r for r in rows if r.id.upper() == art_id_param.upper()]
                mock_job.result.return_value = matching
                return mock_job
        mock_job.result.return_value = rows
        return mock_job

    mock_bq.query.side_effect = mock_query

    return BigQueryVectorKnowledgeStore(
        project_id="test-proj",
        dataset_id="test_ds",
        table_name="kb_table",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64,
    )


def _create_mock_vertex_search_store(articles: list[KnowledgeArticle]) -> VertexAISearchKnowledgeStore:
    """Instantiates a VertexAISearchKnowledgeStore with a mock Discovery Engine client returning the fixtures."""
    mock_client = MagicMock()

    def mock_search(request=None, **kwargs):
        mock_response = MagicMock()
        results = []
        for a in articles:
            item = MagicMock()
            doc = MagicMock()
            doc.id = a.id
            doc.title = a.title
            doc.name = f"projects/p/locations/l/collections/default_collection/dataStores/ds/branches/0/documents/{a.id}"
            doc.struct_data = {
                "id": a.id,
                "title": a.title,
                "system": a.system,
                "category": a.category,
                "content": a.content,
                "allowed_roles": a.allowed_roles,
                "sensitivity": a.sensitivity,
                "clearance_level": a.clearance_level,
                "keywords": a.keywords,
                "source_uri": a.source_uri,
                "owner": a.owner,
                "effective_date": a.effective_date,
                "expiry_date": a.expiry_date,
                "is_deleted": a.is_deleted,
            }
            doc.derived_struct_data = {
                "snippets": [{"snippet": a.content}]
            }
            item.document = doc
            item.relevance_score = 0.95
            results.append(item)
        mock_response.results = results
        return mock_response

    mock_client.search.side_effect = mock_search

    return VertexAISearchKnowledgeStore(
        project_id="test-proj",
        location="global",
        data_store_id="test-datastore",
        search_client=mock_client,
    )


@pytest.mark.parametrize("roles,clearance,expected_doc_ids", [
    # Scenario 1: Anonymous (clearance 0, roles []) -> Only PUBLIC doc
    ([], 0, {"DOC-PUB-001"}),
    # Scenario 2: Employee (clearance 1, roles [employee]) -> PUBLIC + INTERNAL
    (["employee"], 1, {"DOC-PUB-001", "DOC-INT-001"}),
    # Scenario 3: HR Specialist (clearance 2, roles [hr_specialist]) -> PUBLIC + INTERNAL + HR General
    (["hr_specialist"], 2, {"DOC-PUB-001", "DOC-INT-001", "DOC-HR-001"}),
    # Scenario 4: HR Admin (clearance 2, roles [hr_admin]) -> PUBLIC + INTERNAL + HR General + HR Exec
    (["hr_admin"], 2, {"DOC-PUB-001", "DOC-INT-001", "DOC-HR-001", "DOC-HR-EXEC"}),
    # Scenario 5: IT Admin (clearance 3, roles [it_admin]) -> All valid active docs
    (["it_admin"], 3, {"DOC-PUB-001", "DOC-INT-001", "DOC-HR-001", "DOC-HR-EXEC", "DOC-RESTRICTED"}),
])
def test_authorization_matrix_parity_across_all_3_stores(roles, clearance, expected_doc_ids):
    """
    CRITICAL PROBE 5a: 3 Backends x 8 Scenarios Parity Test for search().
    Asserts that InMemoryKnowledgeStore, BigQueryVectorKnowledgeStore, and VertexAISearchKnowledgeStore
    enforce IDENTICAL RBAC, MAC, Tombstone, Expiry, and Effective date access controls.
    """
    sec_ctx = SecurityContext.from_user(user_id="test-user", roles=roles, clearance_level=clearance)

    fixture_articles = _get_probe_fixture_articles()
    in_memory_store = InMemoryKnowledgeStore(articles=fixture_articles)
    bq_store = _create_mock_bigquery_store(fixture_articles)
    vertex_store = _create_mock_vertex_search_store(fixture_articles)

    stores = [
        ("InMemoryKnowledgeStore", in_memory_store),
        ("BigQueryVectorKnowledgeStore", bq_store),
        ("VertexAISearchKnowledgeStore", vertex_store),
    ]

    for store_name, store in stores:
        results = store.search(query="policy procedure salary key", security_context=sec_ctx, limit=20)
        result_ids = {r.article_id for r in results}

        # Assert parity of accessible documents
        assert result_ids == expected_doc_ids, (
            f"Backend '{store_name}' failed authorization parity for roles={roles}, clearance={clearance}.\n"
            f"Expected: {expected_doc_ids}\n"
            f"Got:      {result_ids}\n"
            f"Difference (Missing): {expected_doc_ids - result_ids}\n"
            f"Difference (Unexpected): {result_ids - expected_doc_ids}"
        )

        # Scenarios 6, 7, 8 verification:
        # Assert tombstoned, expired, and future-dated documents are NEVER returned
        assert "DOC-TOMBSTONE" not in result_ids, f"{store_name} returned deleted document DOC-TOMBSTONE"
        assert "DOC-EXPIRED" not in result_ids, f"{store_name} returned expired document DOC-EXPIRED"
        assert "DOC-FUTURE" not in result_ids, f"{store_name} returned not-yet-effective document DOC-FUTURE"


@pytest.mark.parametrize("article_id,roles,clearance,expect_accessible", [
    # Scenario 1: Public doc -> Accessible by Anonymous, Employee, Admin
    ("DOC-PUB-001", [], 0, True),
    ("DOC-PUB-001", ["employee"], 1, True),
    ("DOC-PUB-001", ["it_admin"], 3, True),

    # Scenario 2: Internal doc (clearance 1) -> Denied for Anonymous (clearance 0), Allowed for Employee
    ("DOC-INT-001", [], 0, False),
    ("DOC-INT-001", ["employee"], 1, True),

    # Scenario 3: HR General (clearance 2, allowed_roles: [hr_specialist, hr_admin, it_admin])
    # Employee with clearance 2 lacks required role -> Denied
    ("DOC-HR-001", ["employee"], 2, False),
    # HR Specialist with clearance 1 lacks clearance -> Denied
    ("DOC-HR-001", ["hr_specialist"], 1, False),
    # HR Specialist with clearance 2 -> Allowed
    ("DOC-HR-001", ["hr_specialist"], 2, True),

    # Scenario 4: HR Executive Salary (clearance 2, allowed_roles: [hr_admin, it_admin])
    # HR Specialist has clearance 2 but not in allowed_roles -> Denied
    ("DOC-HR-EXEC", ["hr_specialist"], 2, False),
    # HR Admin has clearance 2 and in allowed_roles -> Allowed
    ("DOC-HR-EXEC", ["hr_admin"], 2, True),

    # Scenario 5: Root Key Rotation (clearance 3, allowed_roles: [it_admin])
    # HR Admin has clearance 2 -> Denied
    ("DOC-RESTRICTED", ["hr_admin"], 2, False),
    # IT Admin has clearance 3 -> Allowed
    ("DOC-RESTRICTED", ["it_admin"], 3, True),

    # Scenario 6: Tombstoned/Deleted Document -> Always Denied across all users including IT Admin
    ("DOC-TOMBSTONE", ["it_admin"], 3, False),
    ("DOC-TOMBSTONE", ["employee"], 1, False),

    # Scenario 7: Expired Document -> Always Denied across all users including IT Admin
    ("DOC-EXPIRED", ["it_admin"], 3, False),
    ("DOC-EXPIRED", ["employee"], 1, False),

    # Scenario 8: Future-dated Document -> Always Denied across all users including IT Admin
    ("DOC-FUTURE", ["it_admin"], 3, False),
    ("DOC-FUTURE", ["employee"], 1, False),

    # Scenario 9: Non-existent Document -> Always None
    ("DOC-NONEXISTENT", ["it_admin"], 3, False),
])
def test_get_article_by_id_parity_matrix_across_all_3_stores(article_id, roles, clearance, expect_accessible):
    """
    CRITICAL PROBE 5b: 3 Backends x get_article_by_id Parity Test.
    Asserts that InMemoryKnowledgeStore, BigQueryVectorKnowledgeStore, and VertexAISearchKnowledgeStore
    enforce IDENTICAL authorization, MAC, RBAC, Tombstone, Expiry, and Effective date access controls
    when retrieving individual articles via get_article_by_id().
    """
    sec_ctx = SecurityContext.from_user(user_id="test-user", roles=roles, clearance_level=clearance)

    fixture_articles = _get_probe_fixture_articles()
    in_memory_store = InMemoryKnowledgeStore(articles=fixture_articles)
    bq_store = _create_mock_bigquery_store(fixture_articles)
    vertex_store = _create_mock_vertex_search_store(fixture_articles)

    stores = [
        ("InMemoryKnowledgeStore", in_memory_store),
        ("BigQueryVectorKnowledgeStore", bq_store),
        ("VertexAISearchKnowledgeStore", vertex_store),
    ]

    for store_name, store in stores:
        article = store.get_article_by_id(article_id=article_id, security_context=sec_ctx)
        if expect_accessible:
            assert article is not None, (
                f"Backend '{store_name}' unexpectedly denied get_article_by_id for article_id={article_id}, "
                f"roles={roles}, clearance={clearance}"
            )
            assert article.id.upper() == article_id.upper()
        else:
            assert article is None, (
                f"Backend '{store_name}' unexpectedly ALLOWED get_article_by_id for article_id={article_id}, "
                f"roles={roles}, clearance={clearance}"
            )


# =====================================================================
# PROBE 6: BigQuery Vector Store Tombstone & Clearance SQL Enforcement
# =====================================================================

def test_bigquery_vector_store_sql_filters_and_clearance():
    """Verify BigQueryVectorKnowledgeStore.search generates SQL with tombstone and clearance filtering."""
    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq.query.return_value = mock_query_job

    store = BigQueryVectorKnowledgeStore(
        project_id="test-proj",
        dataset_id="test_dataset",
        table_name="kb_table",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64,
    )

    sec_ctx = SecurityContext.from_user(user_id="user-1", roles=["employee"], clearance_level=1)
    store.search("SAP purchase order", system="ERP", security_context=sec_ctx)

    assert mock_bq.query.called
    called_sql = mock_bq.query.call_args[0][0]
    job_config = mock_bq.query.call_args[1]["job_config"]
    param_names = [p.name for p in job_config.query_parameters]

    # Verify Tombstone predicate
    assert "is_deleted IS NOT TRUE" in called_sql
    # Verify Effective/Expiry date predicates
    assert "effective_date IS NULL OR effective_date <= @today" in called_sql
    assert "expiry_date IS NULL OR expiry_date >= @today" in called_sql
    # Verify Clearance filter
    assert "clearance_level IS NULL OR clearance_level <= @user_clearance" in called_sql
    assert "user_clearance" in param_names
    clearance_param = next(p for p in job_config.query_parameters if p.name == "user_clearance")
    assert clearance_param.value == 1


# =====================================================================
# PROBE 7: Cloud Identity Startup Self-Check & Bounded Cache
# =====================================================================

def test_cloud_identity_startup_probe_403_error_logging(monkeypatch, caplog):
    """Asserts that check_cloud_identity_startup_access logs an explicit ERROR on 403 Forbidden."""
    from agent_core.app_utils import sso_auth

    monkeypatch.setenv("ENABLE_CLOUD_IDENTITY_GROUP_LOOKUP", "true")

    mock_service = MagicMock()
    mock_request = MagicMock()
    mock_request.execute.side_effect = Exception("403 Forbidden: The caller does not have permission")
    mock_service.groups().memberships().searchTransitiveGroups.return_value = mock_request

    monkeypatch.setattr(sso_auth, "_get_cloud_identity_service", lambda: mock_service)

    with caplog.at_level(logging.ERROR):
        result = sso_auth.check_cloud_identity_startup_access()
        assert result is False
        assert any("Cloud Identity Groups API returned 403 Forbidden" in r.message for r in caplog.records)
        assert any("Groups Reader" in r.message for r in caplog.records)


def test_cloud_identity_cache_bounded_eviction(monkeypatch):
    """Asserts that _store_workspace_groups_cache evicts older items when reaching MAX_SIZE."""
    from agent_core.app_utils import sso_auth

    monkeypatch.setattr(sso_auth, "_WORKSPACE_GROUPS_CACHE_MAX_SIZE", 3)
    sso_auth._WORKSPACE_GROUPS_CACHE.clear()

    sso_auth._store_workspace_groups_cache("user1@example.com", 100.0, ["g1"])
    sso_auth._store_workspace_groups_cache("user2@example.com", 101.0, ["g2"])
    sso_auth._store_workspace_groups_cache("user3@example.com", 102.0, ["g3"])
    assert len(sso_auth._WORKSPACE_GROUPS_CACHE) == 3

    # Adding 4th user must evict the first user
    sso_auth._store_workspace_groups_cache("user4@example.com", 103.0, ["g4"])
    assert len(sso_auth._WORKSPACE_GROUPS_CACHE) == 3
    assert "user1@example.com" not in sso_auth._WORKSPACE_GROUPS_CACHE
    assert "user4@example.com" in sso_auth._WORKSPACE_GROUPS_CACHE


def test_cloud_identity_startup_probe_timeout(monkeypatch, caplog):
    """Asserts that check_cloud_identity_startup_access handles timeouts gracefully."""
    import time
    from agent_core.app_utils import sso_auth

    monkeypatch.setenv("ENABLE_CLOUD_IDENTITY_GROUP_LOOKUP", "true")

    mock_service = MagicMock()
    mock_request = MagicMock()

    def slow_execute(*args, **kwargs):
        time.sleep(0.5)
        return {}

    mock_request.execute.side_effect = slow_execute
    mock_service.groups().memberships().searchTransitiveGroups.return_value = mock_request
    monkeypatch.setattr(sso_auth, "_get_cloud_identity_service", lambda: mock_service)

    with caplog.at_level(logging.WARNING):
        result = sso_auth.check_cloud_identity_startup_access(timeout=0.05)
        assert result is False
        assert any("timed out" in r.message for r in caplog.records)


def test_fastapi_lifespan_invokes_startup_self_check(monkeypatch):
    """Asserts that booting the FastAPI app with Starlette lifespan executes check_cloud_identity_startup_access."""
    from starlette.testclient import TestClient
    from agent_core import fast_api_app

    called = []

    def mock_startup_check():
        called.append(True)
        return True

    monkeypatch.setattr(fast_api_app, "check_cloud_identity_startup_access", mock_startup_check)

    # TestClient as a context manager triggers the lifespan lifecycle (startup and shutdown)
    with TestClient(fast_api_app.app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200

    assert len(called) >= 1, "Expected check_cloud_identity_startup_access to be invoked during app lifespan startup!"
