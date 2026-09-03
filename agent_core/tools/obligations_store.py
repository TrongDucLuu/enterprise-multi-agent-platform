from pathlib import Path
import yaml
import os
import logging
from abc import ABC, abstractmethod
from typing import Optional

from agent_core.tools.enterprise_rag_mcp.rag_models import Obligation
from agent_core.tools.enterprise_rag_mcp.knowledge_store import KnowledgeStoreUnavailableError

logger = logging.getLogger(__name__)

def load_sample_obligations(pack_name: Optional[str] = None) -> list[Obligation]:
    """Lazy-load sample L3 obligations from active domain pack."""
    if pack_name is None:
        pack_name = os.getenv("DOMAIN_PACK", "it-helpdesk")

    candidate_paths = [
        Path(__file__).resolve().parent.parent.parent / "domain_packs" / pack_name / "sample_data" / "obligations.yaml",
        Path("domain_packs") / pack_name / "sample_data" / "obligations.yaml",
    ]
    for path in candidate_paths:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and isinstance(data, list):
                    return [Obligation(**item) for item in data]
            except Exception as e:
                logger.warning("Failed to load sample obligations from %s: %s", path, e)
                return []
    return []



class BaseObligationsStore(ABC):
    """Abstract interface for L3 Obligations Registry (SLA, contracts, regulatory requirements)."""

    @abstractmethod
    def get_obligation(self, obligation_id: str) -> Optional[Obligation]:
        """Lookup an active obligation by its unique obligation_id."""
        pass

    @abstractmethod
    def list_obligations(
        self,
        source_id: Optional[str] = None,
        applies_to: Optional[str] = None,
        severity: Optional[str] = None,
        status: str = "active",
    ) -> list[Obligation]:
        """List obligations matching filters."""
        pass


class InMemoryObligationsStore(BaseObligationsStore):
    """In-memory implementation of L3 Obligations Store."""

    def __init__(self, obligations: Optional[list[Obligation]] = None):
        self._obligations: dict[str, Obligation] = {}
        items = obligations if obligations is not None else load_sample_obligations()
        for item in items:
            self._obligations[item.obligation_id.strip().lower()] = item

    def get_obligation(self, obligation_id: str) -> Optional[Obligation]:
        if not obligation_id:
            return None
        clean_id = obligation_id.strip().lower()
        ob = self._obligations.get(clean_id)
        if ob and ob.status == "active":
            return ob
        return None

    def list_obligations(
        self,
        source_id: Optional[str] = None,
        applies_to: Optional[str] = None,
        severity: Optional[str] = None,
        status: str = "active",
    ) -> list[Obligation]:
        results = []
        for ob in self._obligations.values():
            if status and ob.status.lower() != status.lower():
                continue
            if source_id and ob.source_id.strip().lower() != source_id.strip().lower():
                continue
            if applies_to and ob.applies_to.strip().lower() != applies_to.strip().lower():
                continue
            if severity and ob.severity.strip().lower() != severity.strip().lower():
                continue
            results.append(ob)
        return results


class BigQueryObligationsStore(BaseObligationsStore):
    """Production BigQuery adapter for L3 Obligations Registry (`it_helpdesk_kb.obligations`)."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        table_name: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ):
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "test-project")
        self.dataset_id = dataset_id or os.getenv("BIGQUERY_DATASET", "it_helpdesk_kb")
        self.table_name = table_name or os.getenv("OBLIGATIONS_TABLE_NAME", "l3_obligations")
        self.timeout_seconds = timeout_seconds
        self._client = None

    @property
    def bq_client(self):
        if self._client is None:
            try:
                from google.cloud import bigquery
                self._client = bigquery.Client(project=self.project_id)
            except Exception as e:
                logger.error("Failed to initialize BigQuery client for ObligationsStore: %s", e)
                raise KnowledgeStoreUnavailableError(f"BigQuery initialization failed: {e}")
        return self._client

    @bq_client.setter
    def bq_client(self, client):
        self._client = client

    def get_obligation(self, obligation_id: str) -> Optional[Obligation]:
        if not obligation_id:
            return None
        clean_id = obligation_id.strip()

        sql = f"""
        SELECT 
            obligation_id, source_id, source_title, authority, article, 
            description, severity, applies_to, 
            CAST(date_added AS STRING) AS date_added,
            CAST(date_effective AS STRING) AS date_effective,
            CAST(date_expires AS STRING) AS date_expires,
            status, source_document_path
        FROM `{self.project_id}.{self.dataset_id}.{self.table_name}`
        WHERE LOWER(obligation_id) = LOWER(@obligation_id) AND status = 'active'
        LIMIT 1
        """
        try:
            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("obligation_id", "STRING", clean_id),
                ]
            )
            job_timeout_ms = int(self.timeout_seconds * 1000)
            query_job = self.bq_client.query(sql, job_config=job_config, timeout=job_timeout_ms / 1000.0)
            rows = list(query_job.result(timeout=self.timeout_seconds))
            if not rows:
                return None
            row = rows[0]
            return Obligation(
                obligation_id=row.obligation_id,
                source_id=row.source_id,
                source_title=row.source_title,
                authority=row.authority,
                article=row.article,
                description=row.description,
                severity=row.severity,
                applies_to=row.applies_to,
                date_added=str(row.date_added),
                date_effective=str(row.date_effective),
                date_expires=str(row.date_expires) if row.date_expires else None,
                status=row.status,
                source_document_path=row.source_document_path,
            )
        except Exception as e:
            if 'query_job' in locals() and query_job is not None:
                try:
                    query_job.cancel()
                except Exception:
                    pass
            logger.error("BigQuery Obligations query failed: %s", e)
            raise KnowledgeStoreUnavailableError(f"BigQuery obligations query failed: {e}")

    def list_obligations(
        self,
        source_id: Optional[str] = None,
        applies_to: Optional[str] = None,
        severity: Optional[str] = None,
        status: str = "active",
    ) -> list[Obligation]:
        where_clauses = ["1=1"]
        params = []

        try:
            from google.cloud import bigquery
            if status:
                where_clauses.append("LOWER(status) = LOWER(@status)")
                params.append(bigquery.ScalarQueryParameter("status", "STRING", status))
            if source_id:
                where_clauses.append("LOWER(source_id) = LOWER(@source_id)")
                params.append(bigquery.ScalarQueryParameter("source_id", "STRING", source_id.strip()))
            if applies_to:
                where_clauses.append("LOWER(applies_to) = LOWER(@applies_to)")
                params.append(bigquery.ScalarQueryParameter("applies_to", "STRING", applies_to.strip()))
            if severity:
                where_clauses.append("LOWER(severity) = LOWER(@severity)")
                params.append(bigquery.ScalarQueryParameter("severity", "STRING", severity.strip()))

            sql = f"""
            SELECT 
                obligation_id, source_id, source_title, authority, article, 
                description, severity, applies_to, 
                CAST(date_added AS STRING) AS date_added,
                CAST(date_effective AS STRING) AS date_effective,
                CAST(date_expires AS STRING) AS date_expires,
                status, source_document_path
            FROM `{self.project_id}.{self.dataset_id}.{self.table_name}`
            WHERE {' AND '.join(where_clauses)}
            ORDER BY severity DESC, obligation_id ASC
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            job_timeout_ms = int(self.timeout_seconds * 1000)
            query_job = self.bq_client.query(sql, job_config=job_config, timeout=job_timeout_ms / 1000.0)
            rows = list(query_job.result(timeout=self.timeout_seconds))
            results = []
            for row in rows:
                results.append(
                    Obligation(
                        obligation_id=row.obligation_id,
                        source_id=row.source_id,
                        source_title=row.source_title,
                        authority=row.authority,
                        article=row.article,
                        description=row.description,
                        severity=row.severity,
                        applies_to=row.applies_to,
                        date_added=str(row.date_added),
                        date_effective=str(row.date_effective),
                        date_expires=str(row.date_expires) if row.date_expires else None,
                        status=row.status,
                        source_document_path=row.source_document_path,
                    )
                )
            return results
        except Exception as e:
            if 'query_job' in locals() and query_job is not None:
                try:
                    query_job.cancel()
                except Exception:
                    pass
            logger.error("BigQuery Obligations list query failed: %s", e)
            raise KnowledgeStoreUnavailableError(f"BigQuery obligations query failed: {e}")


_GLOBAL_OBLIGATIONS_STORE: Optional[BaseObligationsStore] = None


def get_obligations_store() -> BaseObligationsStore:
    """Factory function for L3 Obligations Store singleton."""
    global _GLOBAL_OBLIGATIONS_STORE
    if _GLOBAL_OBLIGATIONS_STORE is not None:
        return _GLOBAL_OBLIGATIONS_STORE

    backend = os.getenv("KNOWLEDGE_STORE_BACKEND", "inmemory").lower().strip()
    if backend == "bigquery":
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError(
                "BigQuery vector/obligations store requested via KNOWLEDGE_STORE_BACKEND='bigquery' "
                "but 'google-cloud-bigquery' is not installed. "
                "Add 'google-cloud-bigquery>=3.25.0' to your pyproject.toml / uv dependencies."
            ) from exc
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "test-project")
        dataset_id = os.getenv("BIGQUERY_DATASET_ID", "it_helpdesk_kb")
        _GLOBAL_OBLIGATIONS_STORE = BigQueryObligationsStore(project_id=project_id, dataset_id=dataset_id)
    else:
        _GLOBAL_OBLIGATIONS_STORE = InMemoryObligationsStore()
    return _GLOBAL_OBLIGATIONS_STORE


def reset_obligations_store() -> None:
    """Resets global obligations store singleton (used in test fixtures/pack reloading)."""
    global _GLOBAL_OBLIGATIONS_STORE
    _GLOBAL_OBLIGATIONS_STORE = None

