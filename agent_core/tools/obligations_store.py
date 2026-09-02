import os
import logging
from abc import ABC, abstractmethod
from typing import Optional

from agent_core.tools.enterprise_rag_mcp.rag_models import Obligation
from agent_core.tools.enterprise_rag_mcp.knowledge_store import KnowledgeStoreUnavailableError

logger = logging.getLogger(__name__)

INITIAL_OBLIGATIONS: list[Obligation] = [
    Obligation(
        obligation_id="OBL-SAP-001",
        source_id="CONTRACT-SAP-ENTERPRISE-2024",
        source_title="SAP Enterprise Support Agreement",
        authority="VP of IT",
        article="Section 3.1",
        description="Cam kết Uptime hệ thống tối thiểu 99.95% mỗi tháng theo lịch 24/7.",
        severity="critical",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/sap-enterprise-sla-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SAP-002",
        source_id="CONTRACT-SAP-ENTERPRISE-2024",
        source_title="SAP Enterprise Support Agreement",
        authority="VP of IT",
        article="Section 4.1",
        description="Thời gian phản hồi sự cố khẩn cấp (P1) trong vòng tối đa 30 phút.",
        severity="high",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/sap-enterprise-sla-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SAP-003",
        source_id="CONTRACT-SAP-ENTERPRISE-2024",
        source_title="SAP Enterprise Support Agreement",
        authority="VP of IT",
        article="Section 4.2",
        description="Thời gian khắc phục sự cố khẩn cấp (P1 MTTR) tối đa trong vòng 4 giờ.",
        severity="critical",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/sap-enterprise-sla-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SAP-004",
        source_id="CONTRACT-SAP-ENTERPRISE-2024",
        source_title="SAP Enterprise Support Agreement",
        authority="Legal Counsel",
        article="Section 5.1",
        description="Khấu trừ 10% Service Credits vào phí dịch vụ tháng tiếp theo nếu Uptime dưới 99.9%.",
        severity="high",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/sap-enterprise-sla-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SAP-005",
        source_id="CONTRACT-SAP-ENTERPRISE-2024",
        source_title="SAP Enterprise Support Agreement",
        authority="CISO",
        article="Section 7.2",
        description="Nhà cung cấp có trách nhiệm thông báo vi phạm dữ liệu (Data Breach) trong vòng 24 giờ kể từ khi phát hiện.",
        severity="critical",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/sap-enterprise-sla-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SAP-006",
        source_id="CONTRACT-SAP-ENTERPRISE-2024",
        source_title="SAP Enterprise Support Agreement",
        authority="Legal Counsel",
        article="Section 8.1",
        description="Khách hàng có quyền thực hiện kiểm toán an toàn thông tin độc lập định kỳ hàng năm.",
        severity="medium",
        applies_to="both",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/sap-enterprise-sla-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SAP-007",
        source_id="CONTRACT-SAP-ENTERPRISE-2024",
        source_title="SAP Enterprise Support Agreement",
        authority="Legal Counsel",
        article="Section 9.1",
        description="Tuân thủ thỏa thuận bảo mật thông tin vô điều kiện (Non-Disclosure Agreement - NDA).",
        severity="high",
        applies_to="both",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/sap-enterprise-sla-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-ORA-001",
        source_id="CONTRACT-ORACLE-CLOUD-2024",
        source_title="Oracle Cloud Infrastructure Agreement",
        authority="VP of IT",
        article="Schedule A",
        description="Cam kết tính sẵn sàng của cơ sở dữ liệu (Database Availability) tối thiểu 99.9% mỗi tháng.",
        severity="high",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/oracle-cloud-agreement.pdf",
    ),
    Obligation(
        obligation_id="OBL-ORA-002",
        source_id="CONTRACT-ORACLE-CLOUD-2024",
        source_title="Oracle Cloud Infrastructure Agreement",
        authority="IT Operations",
        article="Schedule B.1",
        description="Thời gian phản hồi sự cố mức độ nghiêm trọng P2 trong vòng tối đa 2 giờ.",
        severity="medium",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/oracle-cloud-agreement.pdf",
    ),
    Obligation(
        obligation_id="OBL-ORA-003",
        source_id="CONTRACT-ORACLE-CLOUD-2024",
        source_title="Oracle Cloud Infrastructure Agreement",
        authority="IT Operations",
        article="Schedule B.2",
        description="Thời gian giải quyết sự cố P2 (Resolve Time) trong vòng tối đa 12 giờ.",
        severity="medium",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/oracle-cloud-agreement.pdf",
    ),
    Obligation(
        obligation_id="OBL-DPA-001",
        source_id="POLICY-DATA-PROTECTION-DPA-2024",
        source_title="Enterprise Data Protection Addendum",
        authority="DPO",
        article="Clause 6.1",
        description="Nghiêm cấm nhà cung cấp và nhà thầu phụ chuyển giao dữ liệu cá nhân của khách hàng ra ngoài khu vực lưu trữ đã thỏa thuận khi chưa có văn bản chấp thuận trước.",
        severity="critical",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/policies/dpa-addendum-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-DPA-002",
        source_id="POLICY-DATA-PROTECTION-DPA-2024",
        source_title="Enterprise Data Protection Addendum",
        authority="DPO",
        article="Clause 11.3",
        description="Nhà cung cấp phải tiêu hủy an toàn hoặc hoàn trả toàn bộ dữ liệu định danh (PII) trong vòng 30 ngày kể từ khi chấm dứt hợp đồng.",
        severity="high",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/policies/dpa-addendum-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SEC-001",
        source_id="POLICY-INTERNAL-IT-SECURITY-2024",
        source_title="IT Information Security Policy",
        authority="CISO",
        article="SecPolicy 2.1",
        description="Mọi tài khoản quản trị viên truy cập hệ thống doanh nghiệp cốt lõi bắt buộc phải kích hoạt xác thực đa yếu tố chống lừa đảo (FIDO2/Hardware MFA).",
        severity="critical",
        applies_to="customer",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/policies/it-security-policy-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SEC-002",
        source_id="POLICY-INTERNAL-IT-SECURITY-2024",
        source_title="IT Information Security Policy",
        authority="IT Security",
        article="SecPolicy 4.3",
        description="Quản lý bộ phận phải tái xét duyệt (Recertification) quyền truy cập đặc quyền của nhân viên định kỳ mỗi 90 ngày.",
        severity="high",
        applies_to="customer",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/policies/it-security-policy-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-SF-001",
        source_id="CONTRACT-SALESFORCE-CRM-2024",
        source_title="Salesforce Master Subscription Agreement",
        authority="VP of IT",
        article="Section 2.4",
        description="Đảm bảo Uptime hệ thống CRM đạt tối thiểu 99.9% không bao gồm thời gian bảo trì định kỳ có thông báo trước 48h.",
        severity="high",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/salesforce-msa-2024.pdf",
    ),
    Obligation(
        obligation_id="OBL-WD-001",
        source_id="CONTRACT-WORKDAY-HRM-2024",
        source_title="Workday Subscription Agreement",
        authority="Head of HR",
        article="Exhibit C",
        description="Khắc phục sự cố tắc nghẽn đồng bộ bảng lương trong vòng 2 giờ trước hạn chót khóa lương ngày 25 hàng tháng.",
        severity="critical",
        applies_to="vendor",
        date_added="2024-01-01",
        date_effective="2024-01-01",
        status="active",
        source_document_path="docs/contracts/workday-agreement-2024.pdf",
    ),
]


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
        items = obligations if obligations is not None else INITIAL_OBLIGATIONS
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
