import pytest
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

from agent_core.tools.enterprise_rag_mcp.rag_models import Fact
from agent_core.tools.enterprise_rag_mcp.knowledge_store import (
    BaseFactsStore,
    InMemoryFactsStore,
    BigQueryFactsStore,
    KnowledgeStoreUnavailableError,
    get_facts_store,
)
from agent_core.tools.enterprise_rag_mcp.main import lookup_fact


def test_fact_pydantic_model_types():
    fact_int = Fact(
        fact_id="FACT-001",
        domain="ERP",
        key="erp.po.sla_hours",
        value="2",
        value_type="int",
        unit="hours",
        date_updated="2025-01-01",
        updated_by="human",
        status="active",
    )
    assert fact_int.typed_value() == 2
    assert isinstance(fact_int.typed_value(), int)

    fact_float = Fact(
        fact_id="FACT-002",
        domain="SYSTEM",
        key="pipeline.chunking.well_structured_max_section_ratio",
        value="0.65",
        value_type="float",
        unit="ratio",
        date_updated="2025-01-01",
        updated_by="human",
        status="active",
    )
    assert fact_float.typed_value() == 0.65
    assert isinstance(fact_float.typed_value(), float)

    fact_bool = Fact(
        fact_id="FACT-003",
        domain="SYSTEM",
        key="feature.flag.enabled",
        value="true",
        value_type="bool",
        date_updated="2025-01-01",
        updated_by="human",
        status="active",
    )
    assert fact_bool.typed_value() is True

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        Fact(
            fact_id="FACT-004",
            domain="ERP",
            key="erp.po.test",
            value="123",
            value_type="int",
            date_updated="2025-01-01",
            updated_by="human",
            status="active",
            invalid_extra_field="boom",
        )


def test_in_memory_facts_store_lookup_success():
    store = InMemoryFactsStore()
    fact = store.get_fact("erp.po.sla_hours")
    assert fact is not None
    assert fact.key == "erp.po.sla_hours"
    assert fact.value == "2"
    assert fact.typed_value() == 2
    assert fact.domain == "ERP"
    assert fact.unit == "hours"

    # Case-insensitive lookup
    fact_upper = store.get_fact("ERP.PO.SLA_HOURS")
    assert fact_upper is not None
    assert fact_upper.fact_id == fact.fact_id

    # Stripped whitespace lookup
    fact_space = store.get_fact("  hrm.timesheet.payroll_lock_day  ")
    assert fact_space is not None
    assert fact_space.typed_value() == 25


def test_in_memory_facts_store_lookup_not_found():
    store = InMemoryFactsStore()
    assert store.get_fact("non_existent_key") is None
    assert store.get_fact("") is None


def test_in_memory_facts_store_lifecycle_status_filter():
    custom_facts = [
        Fact(
            fact_id="F-ACT-1",
            domain="ERP",
            key="erp.active.test",
            value="100",
            value_type="int",
            date_updated="2025-01-01",
            status="active",
        ),
        Fact(
            fact_id="F-DEP-1",
            domain="ERP",
            key="erp.deprecated.test",
            value="200",
            value_type="int",
            date_updated="2025-01-01",
            status="deprecated",
        ),
        Fact(
            fact_id="F-SUP-1",
            domain="HRM",
            key="hrm.superseded.test",
            value="300",
            value_type="int",
            date_updated="2025-01-01",
            status="superseded",
            superseded_by="F-ACT-1",
        ),
    ]
    store = InMemoryFactsStore(facts=custom_facts)

    # get_fact only returns active
    assert store.get_fact("erp.active.test") is not None
    assert store.get_fact("erp.deprecated.test") is None
    assert store.get_fact("hrm.superseded.test") is None

    # list_facts filtering
    active_facts = store.list_facts(status="active")
    assert len(active_facts) == 1
    assert active_facts[0].fact_id == "F-ACT-1"

    deprecated_facts = store.list_facts(status="deprecated")
    assert len(deprecated_facts) == 1
    assert deprecated_facts[0].fact_id == "F-DEP-1"

    erp_facts = store.list_facts(domain="ERP", status="active")
    assert len(erp_facts) == 1


def test_bigquery_facts_store_query():
    store = BigQueryFactsStore(project_id="test-proj", dataset_id="test_kb")

    # Mock BigQuery Client
    mock_client = MagicMock()
    store.bq_client = mock_client

    mock_row = MagicMock()
    mock_row.fact_id = "FACT-BQ-01"
    mock_row.domain = "ERP"
    mock_row.key = "erp.po.sla_hours"
    mock_row.value = "2"
    mock_row.value_type = "int"
    mock_row.unit = "hours"
    mock_row.source_document = "docs/erp.md"
    mock_row.date_updated = "2025-01-01"
    mock_row.updated_by = "human"
    mock_row.status = "active"
    mock_row.superseded_by = None
    mock_row.notes = "Test notes"

    mock_query_job = MagicMock()
    mock_query_job.result.return_value = [mock_row]
    mock_client.query.return_value = mock_query_job

    fact = store.get_fact("erp.po.sla_hours")
    assert fact is not None
    assert fact.fact_id == "FACT-BQ-01"
    assert fact.typed_value() == 2

    # Verify query job timeout configuration
    assert mock_client.query.called
    called_sql = mock_client.query.call_args[0][0]
    assert "WHERE LOWER(key) = LOWER(@key) AND status = 'active'" in called_sql


def test_bigquery_facts_store_timeout_and_cancel():
    store = BigQueryFactsStore(project_id="test-proj", dataset_id="test_kb")
    mock_client = MagicMock()
    store.bq_client = mock_client

    mock_query_job = MagicMock()
    mock_query_job.result.side_effect = TimeoutError("Query timed out")
    mock_client.query.return_value = mock_query_job

    with pytest.raises(KnowledgeStoreUnavailableError):
        store.get_fact("erp.po.sla_hours")

    assert mock_query_job.cancel.called


def test_lookup_fact_tool():
    # Success lookup
    res = lookup_fact("erp.po.sla_hours")
    assert res["status"] == "success"
    assert res["key"] == "erp.po.sla_hours"
    assert res["value"] == "2"
    assert res["typed_value"] == 2
    assert res["unit"] == "hours"
    assert res["domain"] == "ERP"

    # Not found lookup
    res_nf = lookup_fact("unknown.nonexistent.key")
    assert res_nf["status"] == "not_found"
    assert "không tồn tại" in res_nf["message"]

    # Empty key
    res_empty = lookup_fact("   ")
    assert res_empty["status"] == "error"
    assert "không được để trống" in res_empty["message"]
