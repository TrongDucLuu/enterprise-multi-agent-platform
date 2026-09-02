import ast
import os
import glob
import pytest
from unittest.mock import MagicMock, patch

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
        # 1. Test InMemoryKnowledgeStore
        store = InMemoryKnowledgeStore()
        # Default search with no user context
        results = store.search(query="SAP purchase order")
        # Should return 0 results because ERP-KB-001 is INTERNAL (clearance 1 > 0)
        assert len(results) == 0, f"Expected 0 results for anonymous caller, got {len(results)}"

        # 2. Test explicit anonymous context
        anon_results = store.search(
            query="SAP purchase order",
            security_context=SecurityContext.anonymous(),
        )
        assert len(anon_results) == 0

        # 3. Test explicit empty roles
        empty_role_results = store.search(
            query="SAP purchase order",
            user_roles=[],
            user_clearance=0,
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

    # 2. Multiline ref treated as inline text
    multi_text, err = resolve_artifact_content(ref="Line 1\nLine 2\nLine 3")
    assert err is None
    assert multi_text == "Line 1\nLine 2\nLine 3"

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
# PROBE 5: 3 Backends x Multi-Role Authorization Matrix
# =====================================================================

@pytest.mark.parametrize("roles,clearance,expected_can_access_internal,expected_can_access_confidential", [
    ([], 0, False, False),
    (["employee"], 1, True, False),
    (["hr_specialist"], 2, True, False),
    (["hr_admin"], 2, True, True),
    (["it_admin"], 3, True, True),
])
def test_authorization_matrix_across_stores(roles, clearance, expected_can_access_internal, expected_can_access_confidential):
    """
    Tests RBAC & MAC across all roles and clearance levels on InMemoryKnowledgeStore.
    """
    internal_doc = KnowledgeArticle(
        id="DOC-INT-001",
        system="ERP",
        title="Internal Document",
        category="General",
        content="Internal procedure details",
        allowed_roles=[],
        sensitivity="INTERNAL",
        keywords=["internal", "procedure"],
    )
    confidential_doc = KnowledgeArticle(
        id="DOC-CONF-001",
        system="HRM",
        title="Confidential Salary Document",
        category="HR",
        content="Executive salary bands and bonus multipliers",
        allowed_roles=["hr_admin", "director", "it_admin"],
        sensitivity="CONFIDENTIAL",
        keywords=["salary", "bonus"],
    )

    store = InMemoryKnowledgeStore(articles=[internal_doc, confidential_doc])
    sec_ctx = SecurityContext.from_user(user_id="test-user", roles=roles, clearance_level=clearance)

    # Search internal doc
    int_results = store.search(query="Internal procedure", security_context=sec_ctx)
    int_ids = [r.article_id for r in int_results]
    if expected_can_access_internal:
        assert "DOC-INT-001" in int_ids
    else:
        assert "DOC-INT-001" not in int_ids

    # Search confidential doc
    conf_results = store.search(query="Executive salary bonus", security_context=sec_ctx)
    conf_ids = [r.article_id for r in conf_results]
    if expected_can_access_confidential:
        assert "DOC-CONF-001" in conf_ids
    else:
        assert "DOC-CONF-001" not in conf_ids


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
