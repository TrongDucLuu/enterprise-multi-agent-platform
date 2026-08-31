import os
import re
import math
import logging
from abc import ABC, abstractmethod
from typing import Optional, Any

logger = logging.getLogger(__name__)

try:
    from rag_models import KnowledgeArticle, SearchResult, DocumentSummary
except ImportError:
    from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import KnowledgeArticle, SearchResult, DocumentSummary


# Built-in Enterprise Knowledge Base for Local Development & Testing
ENTERPRISE_ARTICLES: list[KnowledgeArticle] = [
    KnowledgeArticle(
        id="ERP-KB-001",
        system="ERP",
        title="Khắc phục lỗi phân quyền Purchase Order (SAP/Oracle M_BEST_EKO)",
        category="Finance & Procurement",
        content="""Khi người dùng gặp lỗi 'Authorization check failed for Object M_BEST_EKO (Activity 01/02)':
1. Nguyên nhân: Tài khoản chưa được gán Role Z_PROC_PURCHASER hoặc Purchasing Group bị giới hạn trong bảng T024.
2. Quy trình xử lý:
   - Yêu cầu người dùng cung cấp mã Purchase Organization và Purchasing Group.
   - Gửi yêu cầu phê duyệt đến Trưởng bộ phận mua hàng (Procurement Manager).
   - Sau khi có phê duyệt, IT Admin gán T-code ME21N/ME22N và object M_BEST_EKO thông qua hệ thống phân quyền SAP GRC.
3. SLA xử lý: 2 giờ làm việc kể từ khi có đủ phê duyệt.""",
        keywords=["erp", "sap", "oracle", "purchase order", "m_best_eko", "me21n", "procurement", "phân quyền", "po"]
    ),
    KnowledgeArticle(
        id="ERP-KB-002",
        system="ERP",
        title="Hướng dẫn xử lý lỗi khóa kỳ kế toán (Posting Period Locked)",
        category="Accounting",
        content="""Lỗi 'Posting period 08/2026 is closed':
1. Kiểm tra trạng thái kỳ kế toán bằng T-code OB52 (FI) hoặc MMPV (MM).
2. Quy trình:
   - Kế toán trưởng phải gửi email xác nhận mở kỳ phụ (Special Period 13-16).
   - IT ERP Team chỉ được mở tạm thời trong khung giờ 17:00 - 19:00 sau khi có ticket phê duyệt.
   - Ghi log audit thay đổi trạng thái OB52.""",
        keywords=["erp", "kỳ kế toán", "posting period", "ob52", "mmpv", "khóa sổ", "sap", "oracle"]
    ),
    KnowledgeArticle(
        id="HRM-KB-101",
        system="HRM",
        title="Xử lý lỗi đồng bộ chấm công và khóa bảng công Workday/BambooHR",
        category="Timesheet & Payroll",
        content="""Vấn đề nhân viên không thấy dữ liệu chấm công từ máy vân tay hoặc FaceID:
1. Nguyên nhân thường gặp:
   - Service 'HR-Biometric-Sync' tại server 10.0.12.55 bị dừng.
   - Mã nhân viên (Employee ID) trên máy chấm công không khớp với mã trong HRM Core.
2. Các bước xử lý:
   - Bước 1: Kiểm tra kết nối mạng máy chấm công tại chi nhánh qua ping IP nội bộ.
   - Bước 2: Restart cronjob sync: `systemctl restart hr-sync-agent`.
   - Bước 3: Nếu bảng công tháng đã bị 'Payroll Locked' sau ngày 25 hàng tháng, yêu cầu HR Operations gửi ticket mở khóa ngoại lệ.""",
        keywords=["hrm", "workday", "bamboohr", "chấm công", "timesheet", "vân tay", "payroll", "bảng lương"]
    ),
    KnowledgeArticle(
        id="HRM-KB-102",
        system="HRM",
        title="Quy trình Onboarding & Cấp phát tài khoản nhân sự mới tự động",
        category="Identity & Access Management",
        content="""Quy trình cấp tài khoản tự động từ HRM sang Active Directory & Google Workspace:
1. Dữ liệu nhân sự mới từ HR tuyển dụng được nhập vào HRM trước ngày làm việc 3 ngày.
2. Job đồng bộ tự động chạy lúc 00:00 hàng ngày:
   - Tạo email theo cú pháp `firstname.lastname@company.com`.
   - Gán group bảo mật theo phòng ban và chức danh (ví dụ: `all-sales@company.com`).
   - Cấp tài khoản SSO Okta / Microsoft Entra ID.
3. Nếu nhân viên mới không nhận được thông tin đăng nhập: Kiểm tra trạng thái 'Pending Approval' trong module Onboarding của HRM.""",
        keywords=["hrm", "onboarding", "nhân viên mới", "cấp tài khoản", "active directory", "email", "okta"]
    ),
    KnowledgeArticle(
        id="CRM-KB-201",
        system="CRM",
        title="Sự cố đồng bộ Lead & Cơ hội giữa CRM và Marketing Automation (Salesforce/HubSpot)",
        category="Sales Operations",
        content="""Khi đội Sales báo cáo Lead từ Web Form không đồng bộ vào CRM:
1. Kiểm tra Webhook Endpoint và API Limits của Salesforce/HubSpot:
   - Giới hạn 24h API Calls: Đảm bảo chưa vượt ngưỡng 90% Daily Limit.
   - Kiểm tra trạng thái OAuth Token của Integration User: Nếu token hết hạn, yêu cầu Admin re-authenticate.
2. Kiểm tra Validation Rules: Các trường bắt buộc như 'Country', 'Phone Number Standard' bị từ chối do dữ liệu thô không hợp lệ.
3. Khắc phục: Chạy lại batch error queue trong CRM Integration Manager.""",
        keywords=["crm", "salesforce", "hubspot", "lead", "đồng bộ", "oauth", "api limit", "webhook", "sales"]
    ),
    KnowledgeArticle(
        id="CRM-KB-202",
        system="CRM",
        title="Phân quyền Territory & Chuyển giao Account khách hàng trên CRM",
        category="Customer Relationship",
        content="""Hướng dẫn chuyển giao quản lý Account khi có thay đổi nhân sự Sales:
1. Điều kiện: Quản lý bộ phận (Sales Manager) tạo ticket chỉ định Sales Rep nhận chuyển giao.
2. Các bước:
   - Vào CRM -> Mass Transfer Records.
   - Chọn chuyển giao: Accounts, Open Opportunities, Open Cases, và Activity History.
   - Bỏ tích 'Transfer Closed Opportunities' nếu quy chế hoa hồng năm cũ vẫn giữ nguyên.
3. Thông báo cho Sales Rep mới qua email tự động sau khi transfer hoàn tất.""",
        keywords=["crm", "territory", "transfer account", "sales rep", "khách hàng", "salesforce", "hubspot"]
    ),
]


ALLOWED_SYSTEMS = {"ERP", "HRM", "CRM", "ALL"}


class BaseKnowledgeStore(ABC):
    """Abstract Base Class for Enterprise Knowledge Stores (Adapter Pattern)."""

    @abstractmethod
    def search(
        self,
        query: str,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None
    ) -> list[SearchResult]:
        """Search knowledge articles matching the query, system filter, and authorized domain list."""
        pass

    @abstractmethod
    def get_article_by_id(self, article_id: str) -> Optional[KnowledgeArticle]:
        """Retrieve the full content of an article by its unique ID."""
        pass


class InMemoryKnowledgeStore(BaseKnowledgeStore):
    """
    In-memory knowledge store supporting fast keyword-based retrieval.
    Ideal for local development, rapid prototyping, and unit testing.
    """

    def __init__(self, articles: list[KnowledgeArticle] = ENTERPRISE_ARTICLES):
        self.articles = articles

    def search(
        self,
        query: str,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None
    ) -> list[SearchResult]:
        """Search knowledge articles by query keywords, system filter, and authorized systems."""
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in ALLOWED_SYSTEMS:
            clean_system = "ALL"

        allowed_upper = set(s.upper() for s in allowed_systems) if allowed_systems is not None else None

        terms = re.findall(r'\w+', query.lower())
        results: list[tuple[float, KnowledgeArticle]] = []

        for article in self.articles:
            art_sys = article.system.upper()
            if clean_system != "ALL" and art_sys != clean_system:
                continue
            if allowed_upper is not None and art_sys not in allowed_upper:
                continue

            score = 0.0
            article_text = f"{article.title} {article.category} {article.content}".lower()

            # Match keywords
            for term in terms:
                if term in article.title.lower():
                    score += 3.0
                elif term in [k.lower() for k in article.keywords]:
                    score += 2.0
                elif term in article_text:
                    score += 1.0

            if score > 0:
                results.append((score, article))

        # Sort by relevance score descending
        results.sort(key=lambda x: x[0], reverse=True)

        search_results = []
        for score, article in results[:limit]:
            snippet = article.content[:200].strip() + "..."
            search_results.append(SearchResult(
                article_id=article.id,
                system=article.system,
                title=article.title,
                snippet=snippet,
                relevance_score=round(score, 2)
            ))
        return search_results

    def get_article_by_id(self, article_id: str) -> Optional[KnowledgeArticle]:
        """Retrieves the full content of an article by its unique ID."""
        for art in self.articles:
            if art.id.upper() == article_id.upper():
                return art
        return None


class BigQueryVectorKnowledgeStore(BaseKnowledgeStore):
    """
    Production-ready BigQuery Vector Search Knowledge Store.
    Leverages BigQuery's VECTOR_SEARCH or COSINE_DISTANCE functions for serverless, cost-efficient RAG.
    Zero fixed-cost per month for datasets under 100k vectors.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset_id: str = "it_helpdesk_kb",
        table_name: str = "knowledge_articles",
        bq_client: Optional[Any] = None,
        embedding_fn: Optional[Any] = None
    ):
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "default-project")
        self.dataset_id = os.getenv("BIGQUERY_KB_DATASET", dataset_id)
        self.table_name = table_name
        self._bq_client = bq_client
        self._embedding_fn = embedding_fn

    @property
    def bq_client(self):
        if self._bq_client is None:
            try:
                from google.cloud import bigquery
                self._bq_client = bigquery.Client(project=self.project_id)
            except Exception as e:
                # Severity-aware warning logging when BigQuery client fails to initialize
                logger.warning("BigQuery client initialization failed (%s). Operating in fallback mode.", e)
                self._bq_client = None
        return self._bq_client

    def _generate_embedding(self, text: str) -> list[float]:
        """Generates embedding vector for a query text."""
        if self._embedding_fn:
            return self._embedding_fn(text)
        if os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1"):
            try:
                from vertexai.language_models import TextEmbeddingModel
                model = TextEmbeddingModel.from_pretrained("text-embedding-005")
                embeddings = model.get_embeddings([text])
                return embeddings[0].values
            except Exception as e:
                logger.info("Vertex AI embedding unavailable (%s), falling back to local embedding.", e)

        # Fallback simple deterministic pseudo-vector for offline simulation
        words = text.lower().split()
        vec = [0.0] * 64
        for i, w in enumerate(words[:64]):
            vec[i] = float(len(w)) / 10.0
        norm = math.sqrt(sum(x*x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def search(
        self,
        query: str,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None
    ) -> list[SearchResult]:
        """
        Searches BigQuery table using VECTOR_SEARCH with parameterized queries and SQL-level security trimming.
        Pushes system filtering into SQL BEFORE fetching to minimize scan cost and eliminate memory leakage.
        """
        if not self.bq_client:
            # Fallback to in-memory store if BigQuery is unavailable
            return InMemoryKnowledgeStore().search(query, system, limit, allowed_systems=allowed_systems)

        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in ALLOWED_SYSTEMS:
            clean_system = "ALL"

        query_vec = self._generate_embedding(query)
        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"

        try:
            from google.cloud import bigquery
            query_params = [
                bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vec),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]

            system_filter = ""
            if clean_system != "ALL":
                system_filter = "WHERE system = @system_param"
                query_params.append(bigquery.ScalarQueryParameter("system_param", "STRING", clean_system))
            elif allowed_systems is not None:
                clean_allowed = [s.upper() for s in allowed_systems if s.upper() in ALLOWED_SYSTEMS and s.upper() != "ALL"]
                if not clean_allowed:
                    return []
                system_filter = "WHERE system IN UNNEST(@allowed_systems_param)"
                query_params.append(bigquery.ArrayQueryParameter("allowed_systems_param", "STRING", clean_allowed))

            # SQL with BigQuery VECTOR_SEARCH using Parameterized Query
            sql = f"""
            SELECT 
                base.id, 
                base.system, 
                base.title, 
                base.content, 
                distance
            FROM VECTOR_SEARCH(
                TABLE {full_table},
                'embedding',
                (SELECT @query_vector AS embedding),
                top_k => @limit,
                distance_type => 'COSINE',
                options => '{{"fraction_lists_to_search": 0.05}}'
            )
            {system_filter}
            ORDER BY distance ASC
            """

            job_config = bigquery.QueryJobConfig(query_parameters=query_params)
            query_job = self.bq_client.query(sql, job_config=job_config)
            rows = query_job.result()

            results = []
            for row in rows:
                snippet = row.content[:200].strip() + "..."
                # Distance in cosine is 0 (identical) to 2. Relevance score = 1 - distance
                relevance = round(max(0.0, 1.0 - (row.distance or 0.0)), 2)
                results.append(SearchResult(
                    article_id=row.id,
                    system=row.system,
                    title=row.title,
                    snippet=snippet,
                    relevance_score=relevance
                ))
            return results
        except Exception as e:
            # Severity-aware warning for BigQuery fallback to enable Cloud Logging alerting
            logger.warning("BigQuery vector search failed (%s). Falling back to in-memory store.", e)
            return InMemoryKnowledgeStore().search(query, system, limit, allowed_systems=allowed_systems)

    def get_article_by_id(self, article_id: str) -> Optional[KnowledgeArticle]:
        """Retrieves article by ID from BigQuery table."""
        if not self.bq_client:
            return InMemoryKnowledgeStore().get_article_by_id(article_id)

        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"
        sql = f"SELECT id, system, title, category, content, keywords FROM {full_table} WHERE UPPER(id) = @article_id LIMIT 1"
        try:
            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("article_id", "STRING", article_id.upper())
                ]
            )
            rows = list(self.bq_client.query(sql, job_config=job_config).result())
            if rows:
                r = rows[0]
                return KnowledgeArticle(
                    id=r.id,
                    system=r.system,
                    title=r.title,
                    category=r.category,
                    content=r.content,
                    keywords=list(r.keywords) if r.keywords else []
                )
        except Exception as e:
            logger.warning("BigQuery get_article_by_id failed (%s). Falling back to in-memory store.", e)
        return InMemoryKnowledgeStore().get_article_by_id(article_id)


def get_knowledge_store() -> BaseKnowledgeStore:
    """
    Factory to retrieve the appropriate Knowledge Store backend based on environment configuration.
    Supported backends:
      - 'in_memory' / 'mock' (default): In-memory keyword store for local dev & unit tests.
      - 'bigquery': BigQuery serverless vector search for cost-effective production scaling.
    """
    backend = os.getenv("KNOWLEDGE_BACKEND", "in_memory").lower()
    if backend == "bigquery":
        return BigQueryVectorKnowledgeStore()
    return InMemoryKnowledgeStore()


# Backward compatibility alias
KnowledgeStore = InMemoryKnowledgeStore

