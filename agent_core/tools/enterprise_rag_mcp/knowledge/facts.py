"""
Enterprise Facts Knowledge Store (L1 Deterministic Facts Registry) with In-Memory and BigQuery backends.
"""
import os
import logging
from pathlib import Path
from typing import Optional, Any
import yaml

from .base import (
    BaseFactsStore,
    KnowledgeStoreUnavailableError,
    _extract_str,
    _extract_list,
)

try:
    from rag_models import Fact
except ImportError:
    from agent_core.tools.enterprise_rag_mcp.rag_models import Fact

logger = logging.getLogger(__name__)


def load_sample_facts(pack_name: Optional[str] = None) -> list[Fact]:
    """Lazy-load sample L1 facts for local development/testing from active domain pack."""
    if pack_name is None:
        pack_name = os.getenv("DOMAIN_PACK", "it-helpdesk")

    candidate_paths = [
        Path(__file__).resolve().parent.parent.parent.parent.parent / "domain_packs" / pack_name / "sample_data" / "facts.yaml",
        Path(__file__).resolve().parent.parent.parent.parent / "domain_packs" / pack_name / "sample_data" / "facts.yaml",
        Path("domain_packs") / pack_name / "sample_data" / "facts.yaml",
    ]
    for path in candidate_paths:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and isinstance(data, list):
                    return [Fact(**item) for item in data]
            except Exception as e:
                logger.warning("Failed to load sample facts from %s: %s", path, e)
                return []
    return []


class InMemoryFactsStore(BaseFactsStore):
    """In-memory facts store for fast deterministic point-lookups (local dev & testing)."""

    def __init__(self, facts: Optional[list[Fact]] = None):
        self.facts = list(facts) if facts is not None else load_sample_facts()

    def get_fact(self, key: str) -> Optional[Fact]:
        if not key:
            return None
        clean_k = key.strip().lower()
        for f in self.facts:
            if f.key.lower() == clean_k and f.status == "active":
                return f
        return None

    def list_facts(self, domain: Optional[str] = None, status: str = "active") -> list[Fact]:
        clean_dom = domain.strip().upper() if domain else None
        clean_status = status.strip().lower() if status else None
        res = []
        for f in self.facts:
            if clean_dom and f.domain.upper() != clean_dom:
                continue
            if clean_status and f.status.lower() != clean_status:
                continue
            res.append(f)
        return res


class BigQueryFactsStore(BaseFactsStore):
    """BigQuery backend for L1 deterministic facts point-lookup."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        table_name: Optional[str] = None,
    ):
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "test-project")
        self.dataset_id = dataset_id or os.getenv("BIGQUERY_DATASET", "it_helpdesk_kb")
        self.table_name = table_name or os.getenv("FACTS_TABLE_NAME", "l1_facts")
        self.bq_client = None

        if os.getenv("KNOWLEDGE_BACKEND", "").lower() == "bigquery" or os.getenv("FACTS_BACKEND", "").lower() == "bigquery":
            try:
                from google.cloud import bigquery
                self.bq_client = bigquery.Client(project=self.project_id)
            except Exception as e:
                logger.error("Failed to initialize BigQuery client for facts store: %s", e)
                raise KnowledgeStoreUnavailableError(f"Không thể khởi tạo kết nối BigQuery Facts Store: {e}") from e

    def get_fact(self, key: str) -> Optional[Fact]:
        if not self.bq_client:
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Facts Store chưa được khởi tạo.")
        if not key:
            return None

        clean_key = key.strip()
        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"
        sql = f"""
        SELECT 
            fact_id, domain, key, value, value_type, unit, source_document,
            date_updated, updated_by, status, superseded_by, notes,
            clearance_level, allowed_roles
        FROM {full_table}
        WHERE LOWER(key) = LOWER(@key) AND status = 'active'
        LIMIT 1
        """
        try:
            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "3.0"))
            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("key", "STRING", clean_key)
                ],
                job_timeout_ms=int(bq_timeout * 1000),
            )
            query_job = self.bq_client.query(sql, job_config=job_config)
            try:
                rows = list(query_job.result(timeout=bq_timeout))
            except Exception as q_err:
                try:
                    query_job.cancel()
                except Exception as c_err:
                    logger.debug("Failed to cancel BigQuery get_fact query job: %s", c_err)
                raise q_err

            if not rows:
                return None

            r = rows[0]
            return Fact(
                fact_id=_extract_str(getattr(r, "fact_id", "")),
                domain=_extract_str(getattr(r, "domain", "")),
                key=_extract_str(getattr(r, "key", "")),
                value=_extract_str(getattr(r, "value", "")),
                value_type=_extract_str(getattr(r, "value_type", "string")),
                unit=_extract_str(getattr(r, "unit", None)),
                source_document=_extract_str(getattr(r, "source_document", None)),
                date_updated=_extract_str(getattr(r, "date_updated", "")),
                updated_by=_extract_str(getattr(r, "updated_by", "human")),
                status=_extract_str(getattr(r, "status", "active")),
                superseded_by=_extract_str(getattr(r, "superseded_by", None)),
                notes=_extract_str(getattr(r, "notes", None)),
                clearance_level=getattr(r, "clearance_level", 1) if getattr(r, "clearance_level", None) is not None else 1,
                allowed_roles=_extract_list(getattr(r, "allowed_roles", None)),
            )
        except Exception as e:
            logger.error("BigQuery get_fact failed: %s", e)
            raise KnowledgeStoreUnavailableError(f"Truy vấn BigQuery Facts Store thất bại: {e}") from e

    def list_facts(self, domain: Optional[str] = None, status: str = "active") -> list[Fact]:
        if not self.bq_client:
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Facts Store chưa được khởi tạo.")
        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"
        where_clauses = ["status = @status"]
        from google.cloud import bigquery
        params = [bigquery.ScalarQueryParameter("status", "STRING", status)]
        if domain:
            where_clauses.append("UPPER(domain) = UPPER(@domain)")
            params.append(bigquery.ScalarQueryParameter("domain", "STRING", domain))

        sql = f"""
        SELECT 
            fact_id, domain, key, value, value_type, unit, source_document,
            date_updated, updated_by, status, superseded_by, notes,
            clearance_level, allowed_roles
        FROM {full_table}
        WHERE {" AND ".join(where_clauses)}
        """
        try:
            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "3.0"))
            job_config = bigquery.QueryJobConfig(
                query_parameters=params,
                job_timeout_ms=int(bq_timeout * 1000),
            )
            query_job = self.bq_client.query(sql, job_config=job_config)
            rows = list(query_job.result(timeout=bq_timeout))
            return [
                Fact(
                    fact_id=_extract_str(getattr(r, "fact_id", "")),
                    domain=_extract_str(getattr(r, "domain", "")),
                    key=_extract_str(getattr(r, "key", "")),
                    value=_extract_str(getattr(r, "value", "")),
                    value_type=_extract_str(getattr(r, "value_type", "string")),
                    unit=_extract_str(getattr(r, "unit", None)),
                    source_document=_extract_str(getattr(r, "source_document", None)),
                    date_updated=_extract_str(getattr(r, "date_updated", "")),
                    updated_by=_extract_str(getattr(r, "updated_by", "human")),
                    status=_extract_str(getattr(r, "status", "active")),
                    superseded_by=_extract_str(getattr(r, "superseded_by", None)),
                    notes=_extract_str(getattr(r, "notes", None)),
                    clearance_level=getattr(r, "clearance_level", 1) if getattr(r, "clearance_level", None) is not None else 1,
                    allowed_roles=_extract_list(getattr(r, "allowed_roles", None)),
                )
                for r in rows
            ]
        except Exception as e:
            logger.error("BigQuery list_facts failed: %s", e)
            raise KnowledgeStoreUnavailableError(f"Truy vấn BigQuery list_facts thất bại: {e}") from e
