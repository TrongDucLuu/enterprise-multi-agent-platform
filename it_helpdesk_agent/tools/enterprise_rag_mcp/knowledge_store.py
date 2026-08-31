import os
import re
import math
import logging
from abc import ABC, abstractmethod
from typing import Optional, Any

logger = logging.getLogger(__name__)

try:
    from rag_models import KnowledgeArticle, SearchResult, DocumentSummary, SectionHierarchy
except ImportError:
    from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import KnowledgeArticle, SearchResult, DocumentSummary, SectionHierarchy

try:
    from it_helpdesk_agent.app_utils.system_config import get_valid_system_filters, get_retrieval_config
    from it_helpdesk_agent.app_utils.embedding_utils import DEFAULT_EMBEDDING_MODEL, generate_text_embedding
except ImportError:
    try:
        from app_utils.system_config import get_valid_system_filters, get_retrieval_config
        from app_utils.embedding_utils import DEFAULT_EMBEDDING_MODEL, generate_text_embedding
    except ImportError:
        def get_valid_system_filters() -> set[str]:
            return {"ERP", "HRM", "CRM", "ALL"}
        def get_retrieval_config() -> dict[str, Any]:
            return {"fraction_lists_to_search": 0.05, "hybrid_search_enabled": False}
        DEFAULT_EMBEDDING_MODEL = "text-embedding-005"
        def generate_text_embedding(text: str, **kwargs) -> list[float]:
            return [0.0] * 64


class KnowledgeStoreUnavailableError(Exception):
    """Raised when the primary enterprise knowledge store backend (e.g. BigQuery) fails or is unreachable."""
    pass


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
        keywords=["erp", "sap", "oracle", "purchase order", "m_best_eko", "me21n", "procurement", "phân quyền", "po"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Phân quyền & Mua hàng", h3="Lỗi M_BEST_EKO")
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
        keywords=["erp", "kỳ kế toán", "posting period", "ob52", "mmpv", "khóa sổ", "sap", "oracle"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Kế toán tài chính", h3="Khóa kỳ OB52")
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
        keywords=["hrm", "workday", "bamboohr", "chấm công", "timesheet", "vân tay", "payroll", "bảng lương"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Chấm công & Bảng lương", h3="Đồng bộ Biometric")
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
        keywords=["hrm", "onboarding", "nhân viên mới", "cấp tài khoản", "active directory", "email", "okta"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Onboarding & Tuyển dụng", h3="Cấp tài khoản tự động")
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
        keywords=["crm", "salesforce", "hubspot", "lead", "đồng bộ", "oauth", "api limit", "webhook", "sales"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Sales & Marketing Sync", h3="Webhook & API Limit")
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
        keywords=["crm", "territory", "transfer account", "sales rep", "khách hàng", "salesforce", "hubspot"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Territory Management", h3="Chuyển giao Account")
    ),
]


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
        valid_systems = get_valid_system_filters()
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in valid_systems:
            clean_system = "ALL"

        allowed_upper = set(s.upper() for s in allowed_systems) if allowed_systems is not None else None

        # Common Vietnamese and English stop words to prevent false positive keyword collisions
        STOP_WORDS = {
            "và", "các", "cho", "của", "là", "ở", "trong", "trên", "được", "với", "tại",
            "để", "khi", "có", "này", "đó", "ra", "vào", "lại", "nào", "gì", "sao",
            "làm", "như", "thế", "theo", "từ", "bị", "đã", "sẽ", "phải", "về", "hãy",
            "giúp", "tôi", "bạn", "cách", "hướng", "dẫn", "quy", "định", "bao", "nhiêu",
            "mục", "nằm", "sau", "đến", "hoặc", "một", "hai", "ba", "bốn", "năm",
            "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "is", "are"
        }

        raw_terms = re.findall(r'\w+', query.lower())
        terms = [t for t in raw_terms if t not in STOP_WORDS and len(t) > 1]
        if not terms:
            terms = raw_terms

        results: list[tuple[float, KnowledgeArticle]] = []

        for article in self.articles:
            art_sys = article.system.upper()
            if clean_system != "ALL" and art_sys != clean_system:
                continue
            if allowed_upper is not None and art_sys not in allowed_upper:
                continue

            score = 0.0
            article_text = f"{article.title} {article.category} {article.content}".lower()
            article_keywords = [k.lower() for k in article.keywords]

            # Match keywords
            for term in terms:
                if term in article.title.lower():
                    score += 3.0
                elif term in article_keywords:
                    score += 2.0
                elif term in article_text:
                    score += 0.5

            # Minimum relevance threshold: require at least a meaningful keyword match
            if score >= 2.0:
                results.append((score, article))

        # Sort by relevance score descending
        results.sort(key=lambda x: x[0], reverse=True)

        search_results = []
        for score, article in results[:limit]:
            snippet = article.content[:200].strip() + "..."
            relevance = min(1.0, score / 6.0)
            sec_hier = article.section_hierarchy
            context_path = sec_hier.format_path() if sec_hier else f"{article.system} > {article.category} > {article.title}"
            search_results.append(SearchResult(
                article_id=article.id,
                system=article.system,
                title=article.title,
                snippet=snippet,
                relevance_score=round(relevance, 2),
                section_hierarchy=sec_hier,
                context_path=context_path,
            ))
        return search_results

    def get_article_by_id(self, article_id: str) -> Optional[KnowledgeArticle]:
        """Retrieves an article by its unique ID."""
        for article in self.articles:
            if article.id.upper() == article_id.upper():
                return article
        return None


class BigQueryVectorKnowledgeStore(BaseKnowledgeStore):
    """
    Production-grade Knowledge Store using BigQuery Vector Search and Vertex AI Embeddings.
    Fails closed when BigQuery is unreachable rather than serving mismatched mock data.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        table_name: str = "knowledge_articles",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        bq_client: Optional[Any] = None,
        embedding_fn: Optional[Any] = None,
    ):
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "it-helpdesk-prod")
        self.dataset_id = dataset_id or os.getenv("BIGQUERY_KB_DATASET", "it_helpdesk_kb")
        self.table_name = table_name
        self.embedding_model = embedding_model
        self.embedding_fn = embedding_fn

        if bq_client is not None:
            self.bq_client = bq_client
        else:
            try:
                from google.cloud import bigquery
                self.bq_client = bigquery.Client(project=self.project_id)
            except Exception as e:
                logger.error("Failed to initialize BigQuery Client for Vector Search (%s).", e)
                self.bq_client = None

    def _generate_embedding(self, text: str) -> list[float]:
        """Generates embedding using the shared enterprise embedding model or injected function."""
        if self.embedding_fn is not None:
            return self.embedding_fn(text)
        return generate_text_embedding(text, model_name=self.embedding_model)

    def search(
        self,
        query: str,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None
    ) -> list[SearchResult]:
        """
        Searches BigQuery table using VECTOR_SEARCH with Pre-filtering subquery and SQL-level security trimming.
        Fails closed by raising KnowledgeStoreUnavailableError on backend failure.
        """
        if not self.bq_client:
            logger.error("BigQuery client is not initialized. Raising KnowledgeStoreUnavailableError.")
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Knowledge Store chưa được khởi tạo.")

        valid_systems = get_valid_system_filters()
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in valid_systems:
            clean_system = "ALL"

        try:
            query_vec = self._generate_embedding(query)
            full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"

            from google.cloud import bigquery
            query_params = [
                bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vec),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]

            # 1. Construct Pre-Filter Subquery for VECTOR_SEARCH (Essential for accuracy)
            if clean_system != "ALL":
                base_table_expr = f"(SELECT * FROM {full_table} WHERE system = @system_param)"
                query_params.append(bigquery.ScalarQueryParameter("system_param", "STRING", clean_system))
            elif allowed_systems is not None:
                clean_allowed = [s.upper() for s in allowed_systems if s.upper() in valid_systems and s.upper() != "ALL"]
                if not clean_allowed:
                    return []
                base_table_expr = f"(SELECT * FROM {full_table} WHERE system IN UNNEST(@allowed_systems_param))"
                query_params.append(bigquery.ArrayQueryParameter("allowed_systems_param", "STRING", clean_allowed))
            else:
                base_table_expr = f"(SELECT * FROM {full_table})"

            # 2. Get retrieval configuration (fraction_lists_to_search)
            retrieval_cfg = get_retrieval_config()
            fraction_lists_to_search = retrieval_cfg.get("fraction_lists_to_search", 0.05)

            # 3. SQL with BigQuery VECTOR_SEARCH Pre-Filtering & Stored Fields
            sql = f"""
            SELECT 
                base.id, 
                base.system, 
                base.title, 
                base.content, 
                base.section_hierarchy,
                distance
            FROM VECTOR_SEARCH(
                {base_table_expr},
                'embedding',
                (SELECT @query_vector AS embedding),
                top_k => @limit,
                distance_type => 'COSINE',
                options => '{{"fraction_lists_to_search": {fraction_lists_to_search}}}'
            )
            ORDER BY distance ASC
            """

            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "15.0"))
            job_config = bigquery.QueryJobConfig(query_parameters=query_params)
            query_job = self.bq_client.query(sql, job_config=job_config)
            rows = query_job.result(timeout=bq_timeout)

            results = []
            for row in rows:
                snippet = row.content[:200].strip() + "..."
                relevance = round(max(0.0, 1.0 - (row.distance or 0.0)), 2)
                
                sec_hier = None
                context_path = None
                raw_hier = getattr(row, "section_hierarchy", None)
                if raw_hier:
                    hier_dict = dict(raw_hier) if hasattr(raw_hier, "items") else raw_hier
                    if isinstance(hier_dict, dict):
                        sec_hier = SectionHierarchy(
                            h1=hier_dict.get("h1"),
                            h2=hier_dict.get("h2"),
                            h3=hier_dict.get("h3"),
                        )
                        context_path = sec_hier.format_path()

                results.append(SearchResult(
                    article_id=row.id,
                    system=row.system,
                    title=row.title,
                    snippet=snippet,
                    relevance_score=relevance,
                    section_hierarchy=sec_hier,
                    context_path=context_path,
                ))
            return results
        except Exception as e:
            logger.error("BigQuery vector search failed (%s). Raising KnowledgeStoreUnavailableError.", e)
            raise KnowledgeStoreUnavailableError(f"Truy vấn BigQuery Vector Search thất bại hoặc quá thời gian chờ: {e}") from e

    def get_article_by_id(self, article_id: str) -> Optional[KnowledgeArticle]:
        """Retrieves article by ID from BigQuery table. Fails closed on failure."""
        if not self.bq_client:
            logger.error("BigQuery client is not initialized for get_article_by_id.")
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Knowledge Store chưa được khởi tạo.")

        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"
        sql = f"SELECT id, system, title, category, content, keywords, section_hierarchy FROM {full_table} WHERE UPPER(id) = @article_id LIMIT 1"
        try:
            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "15.0"))
            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("article_id", "STRING", article_id.upper())
                ]
            )
            rows = list(self.bq_client.query(sql, job_config=job_config).result(timeout=bq_timeout))
            if rows:
                r = rows[0]
                sec_hier = None
                raw_hier = getattr(r, "section_hierarchy", None)
                if raw_hier:
                    hier_dict = dict(raw_hier) if hasattr(raw_hier, "items") else raw_hier
                    if isinstance(hier_dict, dict):
                        sec_hier = SectionHierarchy(
                            h1=hier_dict.get("h1"),
                            h2=hier_dict.get("h2"),
                            h3=hier_dict.get("h3"),
                        )
                return KnowledgeArticle(
                    id=r.id,
                    system=r.system,
                    title=r.title,
                    category=r.category,
                    content=r.content,
                    keywords=list(r.keywords) if r.keywords else [],
                    section_hierarchy=sec_hier,
                )
            return None
        except Exception as e:
            logger.error("BigQuery get_article_by_id failed (%s). Raising KnowledgeStoreUnavailableError.", e)
            raise KnowledgeStoreUnavailableError(f"Truy xuất bài viết BigQuery thất bại: {e}") from e


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
