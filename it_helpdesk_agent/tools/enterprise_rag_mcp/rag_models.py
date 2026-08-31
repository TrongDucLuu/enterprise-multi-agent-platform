from typing import Optional
from pydantic import BaseModel, Field, field_validator

try:
    from it_helpdesk_agent.app_utils.system_config import get_valid_system_filters
except ImportError:
    try:
        from app_utils.system_config import get_valid_system_filters
    except ImportError:
        def get_valid_system_filters() -> set[str]:
            return {"ERP", "HRM", "CRM", "ALL"}


class SectionHierarchy(BaseModel):
    """Hierarchical document section path (H1/H2/H3)."""
    h1: Optional[str] = None
    h2: Optional[str] = None
    h3: Optional[str] = None

    def format_path(self) -> str:
        parts = [p.strip() for p in (self.h1, self.h2, self.h3) if p and p.strip()]
        return " > ".join(parts) if parts else ""


class KnowledgeArticle(BaseModel):
    id: str
    system: str
    title: str
    category: str
    content: str
    keywords: list[str] = Field(default_factory=list)
    section_hierarchy: Optional[SectionHierarchy] = None

    @field_validator("system")
    @classmethod
    def validate_system(cls, v: str) -> str:
        clean_v = v.strip().upper() if v else ""
        valid_set = get_valid_system_filters()
        if clean_v not in valid_set:
            raise ValueError(f"Hệ thống '{v}' không hợp lệ. Các hệ thống được hỗ trợ: {sorted(list(valid_set))}")
        return clean_v


class SearchResult(BaseModel):
    article_id: str
    system: str
    title: str
    snippet: str
    relevance_score: float
    section_hierarchy: Optional[SectionHierarchy] = None
    context_path: Optional[str] = None

    @field_validator("system")
    @classmethod
    def validate_system(cls, v: str) -> str:
        clean_v = v.strip().upper() if v else ""
        valid_set = get_valid_system_filters()
        if clean_v not in valid_set:
            raise ValueError(f"Hệ thống '{v}' không hợp lệ. Các hệ thống được hỗ trợ: {sorted(list(valid_set))}")
        return clean_v


class DocumentSummary(BaseModel):
    system: str
    title: str
    key_points: list[str]
    action_items: list[str]
    suggested_email_draft: Optional[str] = None
